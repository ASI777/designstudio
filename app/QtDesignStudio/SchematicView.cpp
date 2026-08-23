#include "SchematicView.h"
#include "KicadSymbolLibrary.h"
#include "ProjectModel.h"
#include <QPainter>
#include <QComboBox>
#include <QMouseEvent>
#include <QWheelEvent>
#include <QKeyEvent>
#include <QPainterPath>
#include <QResizeEvent>
#include <QSet>
#include <QTimer>
#include <algorithm>
#include <cmath>

SchematicView::SchematicView(ProjectModel* model, QWidget* parent)
    : QWidget(parent), m_model(model),
      m_kicadSymbols(std::make_unique<designstudio::KicadSymbolLibrary>())
{
    setMouseTracking(true);
    setAttribute(Qt::WA_OpaquePaintEvent);
    setFocusPolicy(Qt::StrongFocus);     // receive R / Del / Esc keys
    m_scopeSelector = new QComboBox(this);
    m_scopeSelector->setObjectName(QStringLiteral("schematic_subsystem_selector"));
    m_scopeSelector->addItem(QStringLiteral("Complete design"));
    m_scopeSelector->addItem(QStringLiteral("Human interface"));
    m_scopeSelector->addItem(QStringLiteral("Control + I/O"));
    m_scopeSelector->addItem(QStringLiteral("Power conversion"));
    m_scopeSelector->addItem(QStringLiteral("Passives"));
    m_scopeSelector->setToolTip(QStringLiteral(
        "Focus a functional subsystem without changing the authoritative netlist."));
    connect(m_scopeSelector, &QComboBox::currentIndexChanged, this, [this](int index) {
        m_scope = static_cast<Scope>(std::clamp(index, 0, 4));
        m_selSym = m_wireSym = m_wirePin = m_hoverSym = m_hoverPin = -1;
        QTimer::singleShot(0, this, [this] { fitToSchematic(); });
    });
    updateTransform();
    connect(model, &ProjectModel::loaded, this, [this]{
        ensureSymbols();
        QTimer::singleShot(0, this, [this]{ fitToSchematic(); });
    });
    connect(model, &ProjectModel::modified, this, [this]{ update(); });
}

SchematicView::~SchematicView() = default;

bool SchematicView::loadKicadSymbolSource(const QString& path, QString* errorMessage) {
    if (!m_kicadSymbols->loadFile(path, errorMessage)) return false;
    update();
    emit statusMessage(QStringLiteral("Loaded %1 KiCad symbol definitions")
                           .arg(m_kicadSymbols->symbolCount()));
    return true;
}

int SchematicView::loadedKicadSymbolCount() const {
    return m_kicadSymbols ? m_kicadSymbols->symbolCount() : 0;
}

// Generate symbols from footprints the first time we have a board but no schematic.
void SchematicView::ensureSymbols() {
    if (m_model->symbols.isEmpty() && !m_model->footprints.isEmpty())
        m_model->generateSymbolsFromFootprints();
}

// ── Symbol geometry ───────────────────────────────────────────────────────────
QSizeF SchematicView::symbolSize(const SchSymbol& s) const {
    int left = 0, right = 0, top = 0, bot = 0;
    for (const auto& p : s.pins) {
        switch (p.side) {
        case PinSide::Left:   ++left;  break;
        case PinSide::Right:  ++right; break;
        case PinSide::Top:    ++top;   break;
        case PinSide::Bottom: ++bot;   break;
        }
    }
    const int rows = std::max(1, std::max(left, right));
    const int cols = std::max(1, std::max(top, bot));
    double h = (rows + 1) * kPitchMm;
    double w = std::max((cols + 1) * kPitchMm, 14.0);   // floor width for the ref label
    return QSizeF(w, h);
}

// Point where the pin line meets the symbol body edge.
QPointF SchematicView::pinRoot(const SchSymbol& s, const SchPin& p) const {
    const QSizeF sz = symbolSize(s);
    const double ox = s.x_mm, oy = s.y_mm;
    const double slot = (p.order + 1) * kPitchMm;
    switch (p.side) {
    case PinSide::Left:   return QPointF(ox,             oy + slot);
    case PinSide::Right:  return QPointF(ox + sz.width(),oy + slot);
    case PinSide::Top:    return QPointF(ox + slot,      oy);
    case PinSide::Bottom: return QPointF(ox + slot,      oy + sz.height());
    }
    return QPointF(ox, oy);
}

// Outer tip of the pin stub — where net wires connect.
QPointF SchematicView::pinTip(const SchSymbol& s, const SchPin& p) const {
    const QPointF root = pinRoot(s, p);
    switch (p.side) {
    case PinSide::Left:   return root + QPointF(-kStubMm, 0);
    case PinSide::Right:  return root + QPointF( kStubMm, 0);
    case PinSide::Top:    return root + QPointF(0, -kStubMm);
    case PinSide::Bottom: return root + QPointF(0,  kStubMm);
    }
    return root;
}

// ── Hit testing (all in sheet mm) ─────────────────────────────────────────────
QPointF SchematicView::screenToSheet(const QPoint& px) const {
    bool ok = false;
    QTransform inv = m_xform.inverted(&ok);
    return ok ? inv.map(QPointF(px)) : QPointF();
}

int SchematicView::hitSymbol(const QPointF& mm) const {
    // topmost (last drawn) first
    for (int i = m_model->symbols.size() - 1; i >= 0; --i) {
        const auto& s = m_model->symbols[i];
        if (!symbolVisible(s)) continue;
        const QSizeF sz = symbolSize(s);
        QRectF body(s.x_mm, s.y_mm, sz.width(), sz.height());
        if (body.contains(mm)) return i;
    }
    return -1;
}

bool SchematicView::hitPin(const QPointF& mm, int& symIdx, int& pinIdx) const {
    const double r = kPitchMm * 0.6;     // pin-tip catch radius (mm)
    for (int i = m_model->symbols.size() - 1; i >= 0; --i) {
        const auto& s = m_model->symbols[i];
        if (!symbolVisible(s)) continue;
        for (int j = 0; j < s.pins.size(); ++j) {
            const QPointF tip = pinTip(s, s.pins[j]);
            if (QLineF(tip, mm).length() <= r) { symIdx = i; pinIdx = j; return true; }
        }
    }
    return false;
}

// Connect two pins: resolve a shared net (adopt an existing one, or create one),
// then forward-annotate both pins to the PCB. The board view refreshes via
// ProjectModel::modified.
void SchematicView::connectPins(int symA, int pinA, int symB, int pinB) {
    auto& a = m_model->symbols[symA].pins[pinA];
    auto& b = m_model->symbols[symB].pins[pinB];
    const QString refA = m_model->symbols[symA].ref;
    const QString refB = m_model->symbols[symB].ref;

    if (symA == symB && pinA == pinB) return;
    if (a.netId >= 0 && a.netId == b.netId) {
        emit statusMessage("Pins already on the same net");
        return;
    }

    // Resolve the surviving net, and — if BOTH pins already had (different) nets —
    // MERGE: every pin on the losing net is re-annotated to the survivor, so no
    // pins are orphaned off their old net. Snapshot names first (indices/refs are
    // stable; netIds are what change).
    const int netA = a.netId, netB = b.netId;
    int net, loser = -1;
    if      (netA >= 0 && netB >= 0) { net = netA; loser = netB; }   // merge B→A
    else if (netA >= 0)                net = netA;                   // B adopts A
    else if (netB >= 0)                net = netB;                   // A adopts B
    else                               net = m_model->createNet({}); // brand-new

    int merged = 0;
    if (loser >= 0) {
        for (const auto& s : m_model->symbols)
            for (const auto& pin : s.pins)
                if (pin.netId == loser) {
                    m_model->setPinNet(s.ref, pin.number, net);   // reassign whole net
                    ++merged;
                }
    }
    m_model->setPinNet(refA, a.number, net);
    m_model->setPinNet(refB, b.number, net);
    m_model->setModified(true);                      // refreshes PCB canvas too
    emit statusMessage(QString("Connected %1.%2 ↔ %3.%4 on net %5%6")
        .arg(refA, a.name, refB, b.name, m_model->netName(net),
             merged ? QString(" (merged %1 pins)").arg(merged) : QString()));
}

// ── Paint ─────────────────────────────────────────────────────────────────────
void SchematicView::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);
    p.fillRect(rect(), QColor("#0a0d13"));

    // Grid
    {
        double gSz = m_scale * kPitchMm;   // one pin-pitch per grid square
        if (gSz >= 6) {
            QPen gp(QColor(30,40,55,120), 1);
            p.setPen(gp);
            for (double x = std::fmod(m_tx, gSz); x < width();  x += gSz) p.drawLine(QPointF(x,0),QPointF(x,height()));
            for (double y = std::fmod(m_ty, gSz); y < height(); y += gSz) p.drawLine(QPointF(0,y),QPointF(width(),y));
        }
    }

    if (m_model->symbols.isEmpty()) {
        p.setPen(QColor("#484f58"));
        p.setFont(QFont("Monospace", 13));
        p.drawText(rect(), Qt::AlignCenter,
            "Open a .dsproj file to view the schematic\n(symbols are generated from the board)");
        return;
    }

    // Connection lines for SIGNAL nets (drawn under the symbols); power/ground
    // stay as net-flag labels so GND/+3V0 don't become spaghetti — the same
    // convention real schematics use. Then the symbols on top.
    drawNets(p);
    for (int i = 0; i < m_model->symbols.size(); ++i)
        if (symbolVisible(m_model->symbols[i]))
            drawSymbol(p, m_model->symbols[i]);

    // Selection highlight
    if (m_selSym >= 0 && m_selSym < m_model->symbols.size()) {
        const auto& s = m_model->symbols[m_selSym];
        const QSizeF sz = symbolSize(s);
        QRectF r(m_xform.map(QPointF(s.x_mm, s.y_mm)),
                 m_xform.map(QPointF(s.x_mm + sz.width(), s.y_mm + sz.height())));
        p.setBrush(Qt::NoBrush);
        p.setPen(QPen(QColor("#58a6ff"), 2, Qt::DashLine));
        p.drawRect(r.adjusted(-3, -3, 3, 3));
    }

    // Hover affordance: ring the pin the cursor is over (connection target).
    if (m_hoverSym >= 0 && m_hoverSym < m_model->symbols.size()
        && m_hoverPin >= 0 && m_hoverPin < m_model->symbols[m_hoverSym].pins.size()) {
        const QPointF tip = m_xform.map(pinTip(m_model->symbols[m_hoverSym],
                                               m_model->symbols[m_hoverSym].pins[m_hoverPin]));
        p.setBrush(Qt::NoBrush);
        p.setPen(QPen(QColor("#f0c000"), 1.6));
        p.drawEllipse(tip, 5.0, 5.0);
    }

    // Pending wire rubber-band
    if (m_wireSym >= 0) {
        const QPointF from = pinTip(m_model->symbols[m_wireSym],
                                    m_model->symbols[m_wireSym].pins[m_wirePin]);
        p.setPen(QPen(QColor("#f0c000"), 1.5, Qt::DashLine));
        p.drawLine(m_xform.map(from), m_xform.map(m_wireCurMm));
    }
}

// Rotate the selected symbol 90° clockwise: remap each pin's side (L→T→R→B→L)
// and re-index the slot order per side so pins stay evenly spaced without
// colliding. Body size is derived from side counts, so it follows automatically.
void SchematicView::rotateSelected() {
    if (m_selSym < 0 || m_selSym >= m_model->symbols.size()) return;
    auto& s = m_model->symbols[m_selSym];
    auto cw = [](PinSide side) {
        switch (side) {
        case PinSide::Left:   return PinSide::Top;
        case PinSide::Top:    return PinSide::Right;
        case PinSide::Right:  return PinSide::Bottom;
        case PinSide::Bottom: return PinSide::Left;
        }
        return side;
    };
    for (auto& pin : s.pins) pin.side = cw(pin.side);
    // re-number order per side, preserving each side's existing relative order
    int next[4] = {0, 0, 0, 0};
    for (auto& pin : s.pins) pin.order = next[int(pin.side)]++;
    s.rotDeg = std::fmod(s.rotDeg + 90.0, 360.0);
    m_model->setModified(true);
    emit statusMessage(QString("Rotated %1 → %2°").arg(s.ref).arg(s.rotDeg));
    update();
}

// Delete the selected symbol and release its pins' nets (annotate them to -1 so
// the PCB pads drop off too). Clears selection.
void SchematicView::deleteSelected() {
    if (m_selSym < 0 || m_selSym >= m_model->symbols.size()) return;
    const auto s = m_model->symbols[m_selSym];         // copy: we mutate the list
    for (const auto& pin : s.pins)
        if (pin.netId >= 0) m_model->setPinNet(s.ref, pin.number, -1);
    m_model->symbols.remove(m_selSym);
    m_selSym = -1; m_hoverSym = m_hoverPin = -1;
    m_model->setModified(true);
    emit statusMessage(QString("Deleted %1").arg(s.ref));
    update();
}

void SchematicView::keyPressEvent(QKeyEvent* e) {
    switch (e->key()) {
    case Qt::Key_R:                                    // rotate selection 90° CW
        rotateSelected(); break;
    case Qt::Key_Delete:
    case Qt::Key_Backspace:
        deleteSelected(); break;
    case Qt::Key_Escape:                               // cancel pending wire / deselect
        if (m_wireSym >= 0) { m_wireSym = m_wirePin = -1; emit statusMessage("Wire cancelled"); }
        else m_selSym = -1;
        update(); break;
    default:
        QWidget::keyPressEvent(e); return;
    }
}

// Draw net "airwires": connect every pin on a net along a chain (sorted by
// position), which reads far cleaner than a star from one pin.
void SchematicView::drawNets(QPainter& p) const {
    // Authored schematic wires are first-class and take precedence over the
    // derived connectivity view. This preserves deliberate bus lanes and
    // orthogonal routes stored in DesignStudio/KiCad-interchanged projects.
    QSet<int> explicitlyDrawn;
    for (const SchWire& wire : m_model->schWires) {
        if (wire.points.size() < 2) continue;

        // Some generated demo documents contain provenance-only horizontal
        // placeholders. They declare a net but touch no pins. Such a line is
        // not electrical schematic geometry and must not suppress the derived
        // connectivity presentation for that net.
        auto endpointTouchesNetPin = [this, &wire](const QPointF& endpoint) {
            constexpr double toleranceMm = kPitchMm * 0.75;
            for (const SchSymbol& symbol : m_model->symbols) {
                if (!symbolVisible(symbol)) continue;
                for (const SchPin& pin : symbol.pins) {
                    if (pin.netId == wire.netId
                        && QLineF(endpoint, pinTip(symbol, pin)).length() <= toleranceMm)
                        return true;
                }
            }
            return false;
        };
        if (!endpointTouchesNetPin(wire.points.front())
            || !endpointTouchesNetPin(wire.points.back()))
            continue;

        explicitlyDrawn.insert(wire.netId);
        QColor color = netColor(wire.netId);
        color.setAlpha(210);
        p.setPen(QPen(color, 1.5));
        QPolygonF polyline;
        for (const QPointF& point : wire.points) polyline.push_back(m_xform.map(point));
        p.drawPolyline(polyline);
        p.setPen(Qt::NoPen);
        p.setBrush(color);
        for (int i = 1; i + 1 < polyline.size(); ++i) p.drawEllipse(polyline.at(i), 2.2, 2.2);
    }

    QMap<int, QVector<QPointF>> netTips;   // netId → tip positions (mm)
    for (const auto& s : m_model->symbols) {
        if (!symbolVisible(s)) continue;
        for (const auto& pin : s.pins)
            if (pin.netId >= 0)
                netTips[pin.netId].append(pinTip(s, pin));
    }

    for (auto it = netTips.begin(); it != netTips.end(); ++it) {
        if (explicitlyDrawn.contains(it.key())) continue;
        auto pts = it.value();
        if (pts.size() < 2) continue;
        // Power/ground are shown as net-flags (pin labels), not wires — drawing
        // them would crisscross the whole sheet (GND alone hits dozens of pins).
        const QString nm = m_model->netName(it.key()).toUpper();
        if (nm.contains("GND") || nm.contains("VCC") || nm.contains("VDD")
            || nm.contains("VBUS") || nm.contains("VBAT") || nm.contains("VLED")
            || nm.contains("VIN") || nm.contains("3V") || nm.startsWith("+"))
            continue;
        // Sort tips left-to-right, top-to-bottom and chain them with an
        // orthogonal (Manhattan) elbow so it reads like routed schematic wire.
        std::sort(pts.begin(), pts.end(), [](const QPointF& a, const QPointF& b){
            return a.x() != b.x() ? a.x() < b.x() : a.y() < b.y();
        });
        QColor wc = netColor(it.key());
        wc.setAlpha(190);
        p.setPen(QPen(wc, 1.4));
        for (int i = 1; i < pts.size(); ++i) {
            // Long sheet-spanning chains obscure functional blocks. Matching
            // net labels carry those distant connections; only local
            // relationships get a drawn Manhattan segment.
            if (QLineF(pts[i - 1], pts[i]).length() > 60.0) continue;
            const QPointF a = m_xform.map(pts[i-1]);
            const QPointF b = m_xform.map(pts[i]);
            const QPointF elbow(b.x(), a.y());          // horizontal then vertical
            p.drawLine(a, elbow);
            p.drawLine(elbow, b);
        }
    }
}

void SchematicView::drawSymbol(QPainter& p, const SchSymbol& s) const {
    const QSizeF sz = symbolSize(s);
    const QPointF tl = m_xform.map(QPointF(s.x_mm, s.y_mm));
    const QPointF br = m_xform.map(QPointF(s.x_mm + sz.width(), s.y_mm + sz.height()));
    const QRectF body(tl, br);

    // Body. If a user-selected KiCad library contains this part, draw the
    // independently parsed vector primitives. Otherwise use readable native
    // circuit glyphs for common references and a clean functional block.
    const designstudio::KicadSymbolDefinition* definition =
        m_kicadSymbols ? m_kicadSymbols->definition(s.lib) : nullptr;
    if (!definition && m_kicadSymbols) {
        QString fallback;
        if (s.ref.startsWith(QStringLiteral("SW"))) fallback = QStringLiteral("SW_Push");
        else if (s.ref.startsWith(QStringLiteral("LED"))) fallback = QStringLiteral("LED");
        else if (s.ref.startsWith(QLatin1Char('D'))) fallback = QStringLiteral("D");
        else if (s.ref.startsWith(QLatin1Char('R'))) fallback = QStringLiteral("R");
        else if (s.ref.startsWith(QLatin1Char('C'))) fallback = QStringLiteral("C");
        else if (s.ref.startsWith(QLatin1Char('L'))) fallback = QStringLiteral("L");
        if (!fallback.isEmpty()) definition = m_kicadSymbols->definition(fallback);
    }
    p.setBrush(QColor(17, 24, 39, definition ? 90 : 255));
    p.setPen(QPen(definition ? QColor("#303b49") : QColor("#8b949e"), 1.4));
    p.drawRoundedRect(body, 2.0, 2.0);
    if (definition) drawLibraryGraphic(p, body.adjusted(8, 14, -8, -14), *definition);
    else drawFallbackGraphic(p, body.adjusted(8, 16, -8, -16), s);

    // Ref + lib label
    p.setPen(QColor("#58a6ff"));
    p.setFont(QFont("Monospace", std::max(7, int(m_scale*1.2)), QFont::Bold));
    p.drawText(body.adjusted(2, 2, -2, -2), Qt::AlignTop | Qt::AlignHCenter, s.ref);
    if (m_scale > 5) {
        p.setPen(QColor("#6e7681"));
        p.setFont(QFont("Monospace", std::max(6, int(m_scale*0.8))));
        QString label = s.value.trimmed();
        if (label.isEmpty()) label = s.lib.split('/').last();
        if (label.length() > 14) label = label.left(12) + "…";
        p.drawText(body.adjusted(2, 2, -2, -4), Qt::AlignBottom | Qt::AlignHCenter, label);
    }

    // Pins: stub + dot + NET LABEL. Each pin is labelled with the net it
    // connects to (its "net flag") so connectivity is read from labels — pins
    // sharing a net name are connected — instead of from drawn wires.
    // Keep net names visible in the overview.  Hiding them until a deep zoom
    // made a schematic look like disconnected boxes even though the
    // schematic-owned net IDs were correct.  The labels are deliberately
    // compact at overview scale and expand with the normal zoom.
    const bool showLabels = m_scale > 2.25;
    for (const auto& pin : s.pins) {
        const QPointF root = m_xform.map(pinRoot(s, pin));
        const QPointF tip  = m_xform.map(pinTip(s, pin));

        QColor pc = netColor(pin.netId);
        // Pin stub
        p.setPen(QPen(pin.netId >= 0 ? pc : QColor("#484f58"), 1.2));
        p.drawLine(root, tip);
        // Connection dot
        p.setBrush(pin.netId >= 0 ? pc : QColor("#30363d"));
        p.setPen(Qt::NoPen);
        p.drawEllipse(tip, 2.0, 2.0);

        if (!showLabels) continue;
        // Label = the pin's real function name (VCC, GP4, GND…) from the extracted
        // symbol; net-coloured so pins on the same net share a colour (no wires).
        QString lbl = pin.name.isEmpty() ? pin.number : pin.name;
        const QString netName = pin.netId >= 0 ? m_model->netName(pin.netId).trimmed() : QString();
        if (!netName.isEmpty() && netName.compare(lbl, Qt::CaseInsensitive) != 0)
            lbl += QStringLiteral("·") + netName;
        if (lbl.length() > 18) lbl = lbl.left(17) + QStringLiteral("…");
        const QString up = lbl.toUpper();
        const bool power = up.contains("GND") || up.contains("VCC") || up.contains("VDD")
                        || up.contains("VBUS") || up.startsWith("V") || up.contains("3V3");
        p.setPen(pin.netId >= 0 ? pc : QColor("#c9d1d9"));
        p.setFont(QFont("Monospace", std::max(5, int(m_scale*0.7)),
                        power ? QFont::Bold : QFont::Normal));
        QRectF lr;
        switch (pin.side) {
        case PinSide::Left:   lr = QRectF(root.x()+2, root.y()-7, 92, 14); p.drawText(lr, Qt::AlignVCenter|Qt::AlignLeft,  lbl); break;
        case PinSide::Right:  lr = QRectF(root.x()-94,root.y()-7, 92, 14); p.drawText(lr, Qt::AlignVCenter|Qt::AlignRight, lbl); break;
        case PinSide::Top:    lr = QRectF(root.x()-46,root.y()+2, 92, 12); p.drawText(lr, Qt::AlignTop|Qt::AlignHCenter,   lbl); break;
        case PinSide::Bottom: lr = QRectF(root.x()-46,root.y()-14,92, 12); p.drawText(lr, Qt::AlignBottom|Qt::AlignHCenter,lbl); break;
        }
    }
}

void SchematicView::drawLibraryGraphic(
    QPainter& p, const QRectF& body,
    const designstudio::KicadSymbolDefinition& definition) const {
    const QRectF source = definition.boundsMm;
    if (source.width() <= 0.0 || source.height() <= 0.0 || body.isEmpty()) return;
    auto mapPoint = [&](const QPointF& point) {
        return QPointF(body.left() + (point.x() - source.left()) / source.width() * body.width(),
                       body.bottom() - (point.y() - source.top()) / source.height() * body.height());
    };
    const double sx = body.width() / source.width();
    const double sy = body.height() / source.height();
    for (const auto& graphic : definition.graphics) {
        p.setPen(QPen(QColor("#d0d7de"),
                      std::clamp(graphic.strokeWidthMm * 0.5 * (sx + sy), 1.0, 3.0)));
        p.setBrush(graphic.filled ? QColor("#263445") : Qt::NoBrush);
        switch (graphic.kind) {
        case designstudio::KicadSymbolGraphic::Kind::Rectangle: {
            const QPointF a = mapPoint(graphic.points.at(0));
            const QPointF b = mapPoint(graphic.points.at(1));
            p.drawRect(QRectF(a, b).normalized());
            break;
        }
        case designstudio::KicadSymbolGraphic::Kind::Circle: {
            const QPointF center = mapPoint(graphic.points.front());
            p.drawEllipse(center, graphic.radiusMm * sx, graphic.radiusMm * sy);
            break;
        }
        case designstudio::KicadSymbolGraphic::Kind::Bezier: {
            QPainterPath path(mapPoint(graphic.points.front()));
            if (graphic.points.size() >= 4)
                path.cubicTo(mapPoint(graphic.points.at(1)), mapPoint(graphic.points.at(2)),
                             mapPoint(graphic.points.at(3)));
            else
                for (int i = 1; i < graphic.points.size(); ++i)
                    path.lineTo(mapPoint(graphic.points.at(i)));
            p.drawPath(path);
            break;
        }
        case designstudio::KicadSymbolGraphic::Kind::Arc:
        case designstudio::KicadSymbolGraphic::Kind::Polyline: {
            QPolygonF line;
            for (const QPointF& point : graphic.points) line.push_back(mapPoint(point));
            p.drawPolyline(line);
            break;
        }
        }
    }
}

void SchematicView::drawFallbackGraphic(QPainter& p, const QRectF& body,
                                        const SchSymbol& symbol) const {
    if (body.isEmpty()) return;
    p.setPen(QPen(QColor("#d0d7de"), 1.6));
    p.setBrush(Qt::NoBrush);
    const double cy = body.center().y();
    if (symbol.ref.startsWith(QLatin1Char('R'))) {
        QPolygonF zigzag;
        constexpr int segments = 8;
        for (int i = 0; i <= segments; ++i) {
            const double x = body.left() + body.width() * i / segments;
            const double y = i == 0 || i == segments ? cy
                : cy + (i % 2 ? -1.0 : 1.0) * body.height() * 0.34;
            zigzag << QPointF(x, y);
        }
        p.drawPolyline(zigzag);
    } else if (symbol.ref.startsWith(QLatin1Char('C'))) {
        p.drawLine(QPointF(body.center().x() - 3, body.top()),
                   QPointF(body.center().x() - 3, body.bottom()));
        p.drawLine(QPointF(body.center().x() + 3, body.top()),
                   QPointF(body.center().x() + 3, body.bottom()));
    } else if (symbol.ref.startsWith(QStringLiteral("LED"))
               || symbol.ref.startsWith(QLatin1Char('D'))) {
        QPolygonF diode{QPointF(body.left(), body.top()), QPointF(body.left(), body.bottom()),
                        QPointF(body.right() - 4, cy), QPointF(body.left(), body.top())};
        p.drawPolyline(diode);
        p.drawLine(QPointF(body.right() - 4, body.top()),
                   QPointF(body.right() - 4, body.bottom()));
        if (symbol.ref.startsWith(QStringLiteral("LED"))) {
            p.drawLine(QPointF(body.center().x(), body.top() - 1),
                       QPointF(body.right(), body.top() - 7));
            p.drawLine(QPointF(body.center().x() + 4, cy),
                       QPointF(body.right() + 4, body.top()));
        }
    } else if (symbol.ref.startsWith(QStringLiteral("SW"))) {
        p.drawEllipse(QPointF(body.left() + 2, cy), 2, 2);
        p.drawEllipse(QPointF(body.right() - 2, cy), 2, 2);
        p.drawLine(QPointF(body.left() + 4, cy - 1),
                   QPointF(body.right() - 4, body.top()));
    }
}

QColor SchematicView::netColor(int netId) const {
    if (netId < 0) return QColor("#484f58");
    QString name = m_model->netName(netId).toUpper();
    if (name.contains("GND"))   return QColor("#3fb950");
    if (name.contains("VCC")||name.contains("VDD")||name.contains("VBUS")||name.contains("3V3")||name.contains("VIN"))
        return QColor("#ff9900");
    if (name.contains("USB"))   return QColor("#ff5555");
    if (name.contains("I2C")||name.contains("SDA")||name.contains("SCL")) return QColor("#58a6ff");
    if (name.contains("SPI")||name.contains("MISO")||name.contains("MOSI")) return QColor("#aa44ff");
    return QColor::fromHsv((netId * 67 + 30) % 360, 180, 220);
}

// ── Navigation ────────────────────────────────────────────────────────────────
void SchematicView::updateTransform() {
    m_xform = QTransform();
    m_xform.translate(m_tx, m_ty);
    m_xform.scale(m_scale, m_scale);
}

void SchematicView::fitToSchematic() {
    ensureSymbols();
    if (m_model->symbols.isEmpty() || width() <= 0 || height() <= 0) return;
    QRectF bounds;
    for (const auto& symbol : m_model->symbols) {
        if (!symbolVisible(symbol)) continue;
        const QSizeF size = symbolSize(symbol);
        const QRectF symbolBounds(symbol.x_mm - kStubMm,
                                  symbol.y_mm - kStubMm,
                                  size.width() + 2.0 * kStubMm,
                                  size.height() + 2.0 * kStubMm);
        bounds = bounds.isNull() ? symbolBounds : bounds.united(symbolBounds);
    }
    if (bounds.isNull() || bounds.width() <= 0.0 || bounds.height() <= 0.0) return;
    constexpr double marginPx = 42.0;
    m_scale = std::clamp(std::min((width() - 2.0 * marginPx) / bounds.width(),
                                  (height() - 2.0 * marginPx) / bounds.height()),
                         1.5, 60.0);
    m_tx = (width() - bounds.width() * m_scale) * 0.5 - bounds.left() * m_scale;
    m_ty = (height() - bounds.height() * m_scale) * 0.5 - bounds.top() * m_scale;
    updateTransform();
    update();
}

void SchematicView::zoomIn() {
    m_scale = std::min(60.0, m_scale * 1.2);
    updateTransform();
    update();
}

void SchematicView::zoomOut() {
    m_scale = std::max(1.5, m_scale / 1.2);
    updateTransform();
    update();
}

void SchematicView::mousePressEvent(QMouseEvent* e) {
    // Pan: middle button or Alt+left (unchanged)
    if (e->button() == Qt::MiddleButton ||
        (e->button() == Qt::LeftButton && e->modifiers() & Qt::AltModifier)) {
        m_panning = true;
        m_panStart = e->pos();
        return;
    }
    if (e->button() != Qt::LeftButton) return;

    const QPointF mm = screenToSheet(e->pos());

    // 1) A pin tip under the cursor → begin wiring from it.
    int si, pi;
    if (hitPin(mm, si, pi)) {
        m_wireSym = si; m_wirePin = pi; m_wireCurMm = mm;
        m_selSym = si;
        update();
        return;
    }
    // 2) A symbol body → select and begin moving it.
    int s = hitSymbol(mm);
    if (s >= 0) {
        m_selSym = s;
        m_movingSym = true;
        m_moveGrabMm = mm - QPointF(m_model->symbols[s].x_mm, m_model->symbols[s].y_mm);
        update();
        return;
    }
    // 3) Empty space → grab-pan the sheet (and clear selection). A plain click
    //    with no drag just deselects; a drag moves around the schematic space.
    m_selSym = -1;
    m_panning = true;
    m_panStart = e->pos();
    setCursor(Qt::ClosedHandCursor);
    update();
}

void SchematicView::mouseMoveEvent(QMouseEvent* e) {
    if (m_panning) {
        QPoint d = e->pos() - m_panStart;
        m_tx += d.x(); m_ty += d.y();
        m_panStart = e->pos();
        updateTransform();
        update();
        return;
    }
    if (m_movingSym && m_selSym >= 0) {
        const QPointF mm = screenToSheet(e->pos());
        // snap the symbol origin to the pin-pitch grid so pins land on grid nodes
        m_model->symbols[m_selSym].x_mm = snap(mm.x() - m_moveGrabMm.x());
        m_model->symbols[m_selSym].y_mm = snap(mm.y() - m_moveGrabMm.y());
        update();
        return;
    }
    if (m_wireSym >= 0) {                 // rubber-band the pending wire
        m_wireCurMm = screenToSheet(e->pos());
        update();
        return;
    }
    // Idle: track the pin under the cursor so it can be highlighted as a
    // connection target, and switch the cursor to signal "click to wire".
    const QPointF mm = screenToSheet(e->pos());
    int hs = -1, hp = -1;
    const bool onPin = hitPin(mm, hs, hp);
    if (hs != m_hoverSym || hp != m_hoverPin) {
        m_hoverSym = hs; m_hoverPin = hp;
        setCursor(onPin ? Qt::CrossCursor
                        : (hitSymbol(mm) >= 0 ? Qt::OpenHandCursor : Qt::ArrowCursor));
        update();
    }
}

void SchematicView::mouseReleaseEvent(QMouseEvent* e) {
    if (m_panning) { m_panning = false; unsetCursor(); return; }

    if (m_movingSym) {
        m_movingSym = false;
        m_model->setModified(true);       // persist the move
        return;
    }
    if (m_wireSym >= 0) {
        const QPointF mm = screenToSheet(e->pos());
        int si, pi;
        if (hitPin(mm, si, pi) && !(si == m_wireSym && pi == m_wirePin))
            connectPins(m_wireSym, m_wirePin, si, pi);
        else
            emit statusMessage("Wire cancelled — release on a pin to connect");
        m_wireSym = m_wirePin = -1;
        update();
    }
    (void)e;
}

void SchematicView::wheelEvent(QWheelEvent* e) {
    double factor = e->angleDelta().y() > 0 ? 1.15 : 1.0/1.15;
    QPointF c = e->position();
    m_tx = c.x() - factor*(c.x()-m_tx);
    m_ty = c.y() - factor*(c.y()-m_ty);
    m_scale *= factor;
    m_scale = std::clamp(m_scale, 1.5, 60.0);
    updateTransform();
    update();
}

void SchematicView::resizeEvent(QResizeEvent* event) {
    if (m_scopeSelector) {
        const QSize hint = m_scopeSelector->sizeHint();
        m_scopeSelector->setGeometry(
            std::max(12, width() - hint.width() - 12), 12,
            hint.width(), hint.height());
        m_scopeSelector->raise();
    }
    if (!event->oldSize().isValid() || event->oldSize().isEmpty())
        QTimer::singleShot(0, this, [this]{ fitToSchematic(); });
    else
        updateTransform();
}

bool SchematicView::symbolVisible(const SchSymbol& symbol) const {
    if (m_scope == Scope::Overview) return true;
    const QString ref = symbol.ref.toUpper();
    const bool switchOrLed = ref.startsWith(QStringLiteral("SW"))
        || ref.startsWith(QStringLiteral("LED"))
        || ref.startsWith(QStringLiteral("ENC"))
        || ref.startsWith(QStringLiteral("JS"));
    const bool diode = ref.startsWith(QLatin1Char('D'))
        && !ref.startsWith(QStringLiteral("DS"));
    const bool integrated = ref.startsWith(QLatin1Char('U'));
    const bool connector = ref.startsWith(QLatin1Char('J'));
    const bool inductor = ref.startsWith(QLatin1Char('L'));
    const bool resistorOrCapacitor = ref.startsWith(QLatin1Char('R'))
        || ref.startsWith(QLatin1Char('C'));
    switch (m_scope) {
    case Scope::Overview:
        return true;
    case Scope::HumanInterface:
        return switchOrLed || diode;
    case Scope::ControlIo:
        return integrated || connector;
    case Scope::Power:
        return integrated || inductor;
    case Scope::Passives:
        return resistorOrCapacitor;
    }
    return true;
}
