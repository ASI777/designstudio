#include "PcbCanvas.h"
#include <QPainter>
#include <QPainterPath>
#include <QPainterPath>
#include <QMouseEvent>
#include <QWheelEvent>
#include <QKeyEvent>
#include <QCursor>
#include <QLineF>
#include <QResizeEvent>
#include <QTimer>
#include <algorithm>
#include <cmath>

static constexpr double NM_PER_MM = 1e6;

PcbCanvas::PcbCanvas(ProjectModel* model, QWidget* parent)
    : QWidget(parent), m_model(model)
{
    setMouseTracking(true);
    setFocusPolicy(Qt::StrongFocus);
    setAttribute(Qt::WA_OpaquePaintEvent);
    initLayers();

    connect(m_model, &ProjectModel::loaded, this, [this]{
        QTimer::singleShot(0, this, [this]{ fitToBoard(); });
    });
    connect(m_model, &ProjectModel::modified, this, [this]{ update(); });
}

void PcbCanvas::initLayers() {
    // Per-layer colour palette — one distinct hue per copper layer so
    // traces on different layers are immediately distinguishable at a glance.
    // Palette mirrors industry viewers (KiCad / Altium defaults).
    static const struct { const char* hex; const char* name; } LAYER_DEFS[8] = {
        { "#FF3333", "Top"     },   // L0  bright red
        { "#FFA500", "L1"      },   // L1  amber
        { "#BB44FF", "L2"      },   // L2  violet
        { "#00D4FF", "L3"      },   // L3  cyan
        { "#00E060", "L4"      },   // L4  spring-green
        { "#FF6688", "L5"      },   // L5  coral-pink
        { "#FFE040", "L6"      },   // L6  yellow
        { "#4488FF", "Bottom"  },   // L7  blue
    };
    for (int i = 0; i < MAX_LAYERS; ++i) {
        if (i < 8) {
            m_layers[i].color = QColor(LAYER_DEFS[i].hex);
            m_layers[i].name  = LAYER_DEFS[i].name;
        } else {
            m_layers[i].color = QColor::fromHsv(i * 37 % 360, 210, 230);
            m_layers[i].name  = QString("L%1").arg(i);
        }
        m_layers[i].visible = true;
    }
}

void PcbCanvas::fitToBoard() {
    if (!m_model) return;
    double bw = m_model->boardWidthMm;
    double bh = m_model->boardHeightMm;
    double sw = width();
    double sh = height();
    if (bw <= 0 || bh <= 0 || sw <= 0 || sh <= 0) return;

    double margin = 0.9;
    m_scale = std::min(sw / bw, sh / bh) * margin;
    m_tx = (sw - bw * m_scale) / 2.0;
    m_ty = (sh - bh * m_scale) / 2.0;
    updateTransform();
    update();
}

void PcbCanvas::selectComponent(const QString& ref)
{
    m_selected.clear();
    if (!ref.isEmpty()) {
        for (const ProjFootprint& footprint : m_model->footprints) {
            if (footprint.ref == ref) {
                m_selected.insert(ref);
                break;
            }
        }
    }
    update();
}

void PcbCanvas::clearComponentSelection()
{
    m_selected.clear();
    update();
}

void PcbCanvas::zoomIn()    { m_scale *= 1.2; updateTransform(); update(); }
void PcbCanvas::zoomOut()   { m_scale /= 1.2; updateTransform(); update(); }
void PcbCanvas::zoomReset() { fitToBoard(); }

void PcbCanvas::setActiveTool(Tool t)          { cancelRoute(); m_tool = t; update(); }
void PcbCanvas::setActiveLayer(int l)           { m_activeLayer = l; update(); }
void PcbCanvas::setLayerVisible(int l, bool v)  { if(l<MAX_LAYERS) { m_layers[l].visible=v; update(); } }

void PcbCanvas::updateTransform() {
    // board coords: (0,0) bottom-left, y up. Screen: y down.
    // We flip Y so PCB renders conventionally.
    m_xform = QTransform();
    m_xform.translate(m_tx, m_ty + m_model->boardHeightMm * m_scale);
    m_xform.scale(m_scale, -m_scale);
    m_inv = m_xform.inverted();
}

QPointF PcbCanvas::toWorld(const QPoint& s) const  { return m_inv.map(QPointF(s)); }
QPointF PcbCanvas::toScreen(const QPointF& w) const { return m_xform.map(w); }

// ── Paint ─────────────────────────────────────────────────────────────────────
void PcbCanvas::paintEvent(QPaintEvent*) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);

    // Background
    p.fillRect(rect(), QColor("#0a0a0a"));

    if (!m_model) return;
    p.setTransform(m_xform);

    drawBoard(p);
    drawGrid(p);
    drawRuleAreas(p);
    drawTraces(p);
    drawVias(p);
    drawRoutePreview(p);
    drawFootprints(p);
    // Airwires are editing guidance, not board geometry. At full-board demo
    // scale they obscure the routed copper; reveal them automatically once the
    // user zooms in far enough to act on an individual connection.
    if (m_scale >= 7.5 || m_routing) drawRatsnest(p);

    p.setTransform(QTransform()); // screen space
    drawLayerLegend(p);
    drawCursor(p);
}

void PcbCanvas::drawRuleAreas(QPainter& p) {
    for (const auto& area : m_model->ruleAreas) {
        if (area.points.size() < 3) continue;
        if (area.layer >= 0 && area.layer != m_activeLayer) continue;
        QPainterPath path;
        path.moveTo(area.points[0]);
        for (qsizetype i = 1; i < area.points.size(); ++i) path.lineTo(area.points[i]);
        path.closeSubpath();
        QColor fill = area.forbidPlacement ? QColor(255, 80, 80, 45)
                    : area.maxHeightMm > 0 ? QColor(185, 100, 255, 42)
                    : area.forbidRouting || area.forbidVias ? QColor(255, 170, 40, 40)
                    : QColor(80, 160, 255, 32);
        p.fillPath(path, fill);
        QPen pen(fill.darker(150), 0.12, Qt::DashLine);
        p.setPen(pen); p.setBrush(Qt::NoBrush); p.drawPath(path);
    }
    for (const auto& zone : m_model->copperZones) {
        if (zone.layer != m_activeLayer || zone.points.size() < 3) continue;
        QPainterPath path;
        path.moveTo(zone.points[0]);
        for (qsizetype i = 1; i < zone.points.size(); ++i) path.lineTo(zone.points[i]);
        path.closeSubpath();
        p.fillPath(path, QColor(75, 210, 125, 28));
        p.setPen(QPen(QColor(75, 210, 125, 150), 0.10, Qt::DashDotLine));
        p.setBrush(Qt::NoBrush); p.drawPath(path);
    }
}

void PcbCanvas::drawBoard(QPainter& p) {
    double bw = m_model->boardWidthMm;
    double bh = m_model->boardHeightMm;

    QPainterPath boardPath;
    boardPath.setFillRule(Qt::OddEvenFill);
    if (m_model->boardOutline.size() >= 3) {
        boardPath.moveTo(m_model->boardOutline[0]);
        for (qsizetype i = 1; i < m_model->boardOutline.size(); ++i)
            boardPath.lineTo(m_model->boardOutline[i]);
        boardPath.closeSubpath();
    } else {
        boardPath.addRect(QRectF(0, 0, bw, bh));
    }
    for (const auto& cutout : m_model->boardCutouts) {
        if (cutout.size() < 3) continue;
        boardPath.moveTo(cutout[0]);
        for (qsizetype i = 1; i < cutout.size(); ++i) boardPath.lineTo(cutout[i]);
        boardPath.closeSubpath();
    }

    // FR-4 substrate
    p.fillPath(boardPath, QColor("#0d1a0d"));

    // L2 GND plane subtle fill (if >2 layers)
    if (m_model->copperLayers >= 2 && m_layers[1].visible) {
        QColor gndFill("#112211");
        p.fillPath(boardPath, gndFill);
    }

    // Board outline
    QPen outlinePen(QColor("#ffff00"), 0);
    outlinePen.setCosmetic(false);
    outlinePen.setWidthF(0.15);
    p.setPen(outlinePen);
    p.setBrush(Qt::NoBrush);
    p.drawPath(boardPath);
}

void PcbCanvas::drawGrid(QPainter& p) {
    double grid = m_model->gridMm;
    // The fine placement grid is an editing aid. Suppress it in a fitted
    // overview where hundreds of scan lines overpower the copper geometry.
    if (grid <= 0 || m_scale < 10.0) return;

    QPen gp(QColor(40,55,40,80), 0);
    gp.setCosmetic(true);
    p.setPen(gp);

    double bw = m_model->boardWidthMm;
    double bh = m_model->boardHeightMm;
    for (double x = 0; x <= bw; x += grid)
        p.drawLine(QPointF(x,0), QPointF(x,bh));
    for (double y = 0; y <= bh; y += grid)
        p.drawLine(QPointF(0,y), QPointF(bw,y));
}

void PcbCanvas::drawTraces(QPainter& p) {
    // Two passes: inactive layers first (semi-transparent), active layer on top.
    for (int pass = 0; pass < 2; ++pass) {
        for (int traceIndex = 0; traceIndex < m_model->traces.size(); ++traceIndex) {
            if (m_routing && std::any_of(m_routePreview.movedTraces.begin(),
                                         m_routePreview.movedTraces.end(),
                    [traceIndex](const InteractiveTraceMove& move) {
                        return move.modelIndex == traceIndex;
                    })) continue;
            auto& t = m_model->traces[traceIndex];
            // Dense hatch segments are the implementation of a copper pour,
            // not individual routes. The zone boundary/fill already conveys
            // them in an overview; reveal hatch detail only while zoomed in.
            if (t.isPour && m_scale < 10.0) continue;
            int l = t.layer;
            if (!m_layers[l % MAX_LAYERS].visible) continue;
            bool isActive = (l == m_activeLayer);
            if ((pass == 0) == isActive) continue;   // wrong pass

            QColor col = m_layers[l % MAX_LAYERS].color;
            if (!isActive) {
                col.setAlpha(32);    // inactive layers: context without routing noise
            } else {
                col.setAlpha(255);   // active layer: fully opaque, full colour
            }

            QPen pen(col, t.width_mm);
            pen.setCapStyle(Qt::RoundCap);
            pen.setJoinStyle(Qt::RoundJoin);
            pen.setCosmetic(false);
            p.setPen(pen);
            p.drawLine(QPointF(t.ax_mm, t.ay_mm), QPointF(t.bx_mm, t.by_mm));
        }
    }
}

void PcbCanvas::drawVias(QPainter& p) {
    for (int viaIndex = 0; viaIndex < m_model->vias.size(); ++viaIndex) {
        if (m_routing && std::any_of(m_routePreview.movedVias.begin(),
                                     m_routePreview.movedVias.end(),
                [viaIndex](const InteractiveViaMove& move) {
                    return move.modelIndex == viaIndex;
                })) continue;
        auto& v = m_model->vias[viaIndex];
        QPointF c(v.x_mm, v.y_mm);
        double  r     = v.diaMm / 2.0;
        double  drill = v.drillMm / 2.0;
        int     fl    = std::max(0, std::min(v.fromLayer, MAX_LAYERS - 1));
        int     tl    = std::max(0, std::min(v.toLayer,   MAX_LAYERS - 1));

        // Annular ring: divided into from-layer colour (top half) and to-layer colour (bottom half)
        // so the via visually shows which layers it bridges.
        QColor fromCol = m_layers[fl].color;
        QColor toCol   = m_layers[tl].color;
        fromCol.setAlpha(220);
        toCol.setAlpha(220);

        // Outer ring — top half in from-layer colour
        p.setBrush(fromCol);
        p.setPen(Qt::NoPen);
        p.drawEllipse(c, r, r);

        // Bottom-half overlay in to-layer colour using a clip rect trick
        p.save();
        p.setClipRect(QRectF(c.x() - r, c.y(), r * 2, r));
        p.setBrush(toCol);
        p.drawEllipse(c, r, r);
        p.restore();

        // Thin outline
        p.setBrush(Qt::NoBrush);
        p.setPen(QPen(QColor(0,0,0,120), 0.04));
        p.drawEllipse(c, r, r);

        // Drill hole
        p.setBrush(QColor("#0a0a0a"));
        p.setPen(Qt::NoPen);
        p.drawEllipse(c, drill, drill);
    }
}

void PcbCanvas::drawRoutePreview(QPainter& p) {
    if (!m_routing || !m_routePreview.accepted) return;
    auto drawTrace = [&](const ProjTrace& trace) {
        QColor color = m_layers[trace.layer % MAX_LAYERS].color.lighter(125);
        QPen pen(color, trace.width_mm);
        pen.setCapStyle(Qt::RoundCap);
        pen.setJoinStyle(Qt::RoundJoin);
        p.setPen(pen);
        p.drawLine(QPointF(trace.ax_mm, trace.ay_mm),
                   QPointF(trace.bx_mm, trace.by_mm));
    };
    for (const auto& move : m_routePreview.movedTraces) drawTrace(move.trace);
    for (const auto& trace : m_routePreview.addedTraces) drawTrace(trace);
    auto drawVia = [&](const ProjVia& via) {
        p.setPen(QPen(QColor("#ffffff"), 0.05));
        p.setBrush(m_layers[via.fromLayer % MAX_LAYERS].color.lighter(125));
        p.drawEllipse(QPointF(via.x_mm, via.y_mm), via.diaMm / 2, via.diaMm / 2);
        p.setBrush(QColor("#0a0a0a"));
        p.setPen(Qt::NoPen);
        p.drawEllipse(QPointF(via.x_mm, via.y_mm), via.drillMm / 2, via.drillMm / 2);
    };
    for (const auto& move : m_routePreview.movedVias) drawVia(move.via);
    for (const auto& via : m_routePreview.addedVias) drawVia(via);
}

void PcbCanvas::drawFootprints(QPainter& p) {
    for (auto& fp : m_model->footprints) {
        bool selected = m_selected.contains(fp.ref);

        p.save();
        p.translate(fp.x_mm, fp.y_mm);
        p.rotate(fp.rotDeg);    // canonical positive board rotation: CCW
        if (fp.side == 1) p.scale(1.0, -1.0); // bottom assembly mirrors local Y

        // Draw pads
        for (auto& pad : fp.pads) {
            QColor padColor = (pad.netId >= 0)
                ? netColor(pad.netId)
                : QColor("#c8a040");

            if (selected) padColor = padColor.lighter(130);

            QPen padPen(padColor.darker(140), 0.04);
            p.setPen(padPen);
            p.setBrush(padColor);

            if (pad.throughHole) {
                p.drawEllipse(QPointF(pad.x_mm, pad.y_mm), pad.w_mm/2, pad.h_mm/2);
                p.setBrush(QColor("#111"));
                p.setPen(Qt::NoPen);
                p.drawEllipse(QPointF(pad.x_mm, pad.y_mm), pad.drillMm/2, pad.drillMm/2);
            } else {
                QRectF r(pad.x_mm - pad.w_mm/2, pad.y_mm - pad.h_mm/2, pad.w_mm, pad.h_mm);
                if (pad.shape == "circle" || pad.shape == "oval") {
                    p.drawEllipse(r);
                } else if (pad.shape == "roundrect") {
                    double rr = pad.cornerR_mm > 0 ? pad.cornerR_mm
                                                   : std::min(pad.w_mm, pad.h_mm) * 0.25;
                    p.drawRoundedRect(r, rr, rr);
                } else {                                   // "rect"
                    p.drawRect(r);
                }
            }
        }

        // Custom copper regions (curved corner solder areas). When a region has an
        // inner hole it is a HOLLOW ring -- copper only in the band, open centre --
        // drawn with an odd-even QPainterPath so the centre is not filled.
        for (auto& rg : fp.regions) {
            QColor rc = (rg.netId >= 0) ? netColor(rg.netId) : QColor("#c8a040");
            if (selected) rc = rc.lighter(130);
            p.setPen(QPen(rc.darker(140), 0.04));
            p.setBrush(rc);
            QPainterPath path;
            path.setFillRule(Qt::OddEvenFill);
            QPolygonF outer;
            for (auto& pt : rg.points) outer << pt;
            path.addPolygon(outer); path.closeSubpath();
            if (rg.hole.size() >= 3) {
                QPolygonF inner;
                for (auto& pt : rg.hole) inner << pt;
                path.addPolygon(inner); path.closeSubpath();
            }
            p.drawPath(path);
        }

        // Placement evidence: exact courtyard plus engineering halos. These are
        // visual aids only; the legalizer and native DRC use the exact polygons.
        if (fp.courtyard.size() >= 3) {
            QPainterPath courtPath;
            courtPath.moveTo(fp.courtyard[0]);
            for (qsizetype i = 1; i < fp.courtyard.size(); ++i)
                courtPath.lineTo(fp.courtyard[i]);
            courtPath.closeSubpath();
            p.setPen(QPen(QColor(90, 210, 255, 150), 0.06, Qt::DashLine));
            p.setBrush(Qt::NoBrush);
            p.drawPath(courtPath);
        }
        const double halo = std::max(fp.thermalPowerW > 0 ? fp.thermalClearanceMm : 0.0,
                                     fp.testAccessRequired ? fp.testAccessHaloMm : 0.0);
        if (halo > 0 && fp.bodyW_mm > 0 && fp.bodyH_mm > 0) {
            const QColor haloColor = fp.testAccessRequired ? QColor(100, 255, 150, 110)
                                                            : QColor(255, 120, 70, 110);
            p.setPen(QPen(haloColor, 0.06, Qt::DotLine)); p.setBrush(Qt::NoBrush);
            p.drawRoundedRect(QRectF(fp.bodyCx_mm - fp.bodyW_mm / 2 - halo,
                                     fp.bodyCy_mm - fp.bodyH_mm / 2 - halo,
                                     fp.bodyW_mm + 2 * halo, fp.bodyH_mm + 2 * halo),
                              0.2, 0.2);
        }

        // Silkscreen body outline: use the EXACT datasheet body when present
        // (true component shape), else fall back to the pad bounding box.
        if (!fp.pads.isEmpty()) {
            double xmin, ymin, xmax, ymax;
            if (fp.bodyW_mm > 0.0 && fp.bodyH_mm > 0.0) {
                xmin = fp.bodyCx_mm - fp.bodyW_mm/2;  xmax = fp.bodyCx_mm + fp.bodyW_mm/2;
                ymin = fp.bodyCy_mm - fp.bodyH_mm/2;  ymax = fp.bodyCy_mm + fp.bodyH_mm/2;
            } else {
                xmin=1e9; ymin=1e9; xmax=-1e9; ymax=-1e9;
                for (auto& pad : fp.pads) {
                    xmin=std::min(xmin,pad.x_mm-pad.w_mm/2);
                    ymin=std::min(ymin,pad.y_mm-pad.h_mm/2);
                    xmax=std::max(xmax,pad.x_mm+pad.w_mm/2);
                    ymax=std::max(ymax,pad.y_mm+pad.h_mm/2);
                }
                xmin-=0.15; ymin-=0.15; xmax+=0.15; ymax+=0.15;
            }
            QColor silk(selected ? "#aaffaa" : "#dddddd");
            silk.setAlpha(selected ? 200 : 80);
            p.setPen(QPen(silk, 0.06));
            p.setBrush(Qt::NoBrush);
            p.drawRect(QRectF(xmin, ymin, xmax-xmin, ymax-ymin));
            // Pin-1 marker — a small dot just outside the body's top-left corner.
            p.setBrush(silk); p.setPen(Qt::NoPen);
            p.drawEllipse(QPointF(xmin-0.25, ymin-0.25), 0.18, 0.18);
            p.setBrush(Qt::NoBrush);

            // Reference text is useful while editing but becomes an opaque
            // carpet in a fitted full-board overview. Reveal it at a readable
            // zoom (or for the selected part) without hiding any geometry.
            if (m_scale >= 7.5 || selected) {
                p.save();
                p.scale(1.0/m_scale, -1.0/m_scale);  // back to screen coords for font
                QFont f("Monospace", 8);
                p.setFont(f);
                p.setPen(selected ? QColor("#aaffaa") : QColor("#ffffff"));
                p.drawText(QPointF(xmin*m_scale, (ymin-0.2)*(-m_scale)), fp.ref);
                if (fp.placementLocked)
                    p.drawText(QPointF((xmax+0.2)*m_scale, (ymin-0.2)*(-m_scale)),
                               QStringLiteral("L"));
                p.restore();
            }
        }

        p.restore();

        // Selection ring
        if (selected) {
            p.save();
            p.translate(fp.x_mm, fp.y_mm);
            double r = 1.5;
            p.setPen(QPen(QColor("#58a6ff"), 0.12));
            p.setBrush(Qt::NoBrush);
            p.drawEllipse(QPointF(0,0), r, r);
            p.restore();
        }
    }
}

void PcbCanvas::drawRatsnest(QPainter& p) {
    // Pad world position + size, keyed by net.
    struct PadPos { QPointF pos; double w, h; };
    QMap<int, QVector<PadPos>> netPads;
    for (auto& fp : m_model->footprints) {
        for (auto& pad : fp.pads) {
            if (pad.netId < 0) continue;
            const QPointF world = footprintLocalToBoard(
                fp, QPointF(pad.x_mm, pad.y_mm));
            netPads[pad.netId].append({world, pad.w_mm, pad.h_mm});
        }
    }

    // A pad is ROUTED if a same-net trace endpoint lands within it. Airwires are
    // only drawn for connections NOT yet routed — once a net is fully routed its
    // dash lines disappear (the previous code drew them for every net).
    auto isRouted = [&](int net, const PadPos& pp) {
        for (const auto& t : m_model->traces) {
            if (t.netId != net || t.isPour) continue;
            for (const QPointF e : { QPointF(t.ax_mm, t.ay_mm), QPointF(t.bx_mm, t.by_mm) })
                if (std::abs(e.x() - pp.pos.x()) <= pp.w/2 + 0.3 &&
                    std::abs(e.y() - pp.pos.y()) <= pp.h/2 + 0.3)
                    return true;
        }
        return false;
    };

    QPen airwire(QColor(255,255,100,90), 0.06);
    airwire.setStyle(Qt::DashLine);
    p.setPen(airwire);

    for (auto it = netPads.begin(); it != netPads.end(); ++it) {
        const auto& pads = it.value();
        if (pads.size() < 2) continue;
        QVector<int> routed, unrouted;
        for (int i = 0; i < pads.size(); ++i)
            (isRouted(it.key(), pads[i]) ? routed : unrouted).append(i);
        if (unrouted.isEmpty()) continue;              // fully routed → no airwires
        // Anchor airwires to a routed pad when one exists (so we show the gap to the
        // routed copper), else star from the first unrouted pad.
        const int anchor = routed.isEmpty() ? unrouted.first() : routed.first();
        for (int u : unrouted)
            if (u != anchor) p.drawLine(pads[anchor].pos, pads[u].pos);
    }
}

void PcbCanvas::drawLayerLegend(QPainter& p) {
    int layers = std::min(m_model->copperLayers, MAX_LAYERS);
    QFont f("Monospace", 8);
    f.setBold(false);
    QFont fb("Monospace", 8);
    fb.setBold(true);
    p.setFont(f);

    const int SW = 16, SH = 7, PAD = 3, ROW = SH + PAD + 2;
    const int x0 = 12, y0 = 12;

    for (int i = 0; i < layers; ++i) {
        int y = y0 + i * ROW;
        bool isActive = (i == m_activeLayer);

        // Colour swatch
        QColor sw = m_layers[i].color;
        if (!m_layers[i].visible) sw.setAlpha(60);
        p.fillRect(x0, y, SW, SH, sw);

        // Active layer gets a bright outline
        if (isActive) {
            p.setPen(QPen(QColor("#ffffff"), 1));
            p.setBrush(Qt::NoBrush);
            p.drawRect(x0, y, SW, SH);
        }

        // Label
        p.setFont(isActive ? fb : f);
        QColor tc = isActive ? QColor("#ffffff") : QColor("#8b949e");
        if (!m_layers[i].visible) tc.setAlpha(80);
        p.setPen(tc);
        QString label = m_layers[i].name.isEmpty()
            ? QString("L%1").arg(i) : m_layers[i].name;
        if (isActive) label += " ◀";
        p.drawText(x0 + SW + 4, y + SH - 1, label);
    }
}

void PcbCanvas::drawCursor(QPainter& p) {
    if (m_tool == Tool::Select) return;
    QPointF s = toScreen(m_cursor);
    QPen cp(QColor("#58a6ff"), 1);
    cp.setStyle(Qt::DashLine);
    p.setPen(cp);
    p.drawLine(QPointF(s.x(), 0), QPointF(s.x(), height()));
    p.drawLine(QPointF(0, s.y()), QPointF(width(), s.y()));
}

// ── Net color by id ───────────────────────────────────────────────────────────
QColor PcbCanvas::netColor(int netId) const {
    if (netId < 0) return QColor("#c8a040");
    QString name = m_model->netName(netId).toUpper();
    if (name.contains("GND"))                           return QColor("#446644");
    if (name.contains("VCC")||name.contains("VDD")||
        name.contains("VBUS")||name.contains("VBAT"))   return QColor("#884400");
    if (name.contains("USB"))                           return QColor("#cc4400");
    if (name.contains("I2C"))                           return QColor("#2244cc");
    if (name.contains("I2S")||name.contains("AUDIO"))   return QColor("#662288");
    if (name.contains("SPI")||name.contains("SCK"))     return QColor("#118866");
    // Deterministic color from id
    return QColor::fromHsv((netId * 67 + 30) % 360, 180, 200);
}

// ── Mouse / keyboard ──────────────────────────────────────────────────────────
void PcbCanvas::mousePressEvent(QMouseEvent* e) {
    QPointF world = toWorld(e->pos());

    if (e->button() == Qt::MiddleButton ||
        (e->button() == Qt::LeftButton && e->modifiers() & Qt::AltModifier)) {
        m_panning = true;
        m_panStart = e->pos();
        setCursor(Qt::ClosedHandCursor);
        return;
    }

    if (e->button() == Qt::LeftButton) {
        if (m_tool == Tool::Select) {
            // The arrow tool doubles as the normal canvas grab tool. Components
            // still win hit-testing, while dragging empty space pans without
            // requiring a modifier or a middle mouse button.
            if (!selectAt(world)) {
                m_panning = true;
                m_panStart = e->pos();
                setCursor(Qt::ClosedHandCursor);
            }
        } else if (m_tool == Tool::RouteTrace) {
            if (!m_routing) {
                if (!m_core) {
                    emit statusMessage("Interactive router unavailable: native engine is not loaded");
                    return;
                }
                m_routeNet = routeNetAt(world);
                if (m_routeNet < 0) {
                    emit statusMessage("Start routing on an assigned pad, trace, or via");
                    return;
                }
                m_routeStart = world;
                m_routePreview = m_core->beginInteractiveRoute(
                    m_model, world, m_activeLayer, m_routeNet, true);
                m_routing = m_routePreview.accepted;
                emit statusMessage(m_routePreview.accepted
                    ? QString("Push-and-shove routing %1 on L%2 — move, click to commit; V changes layer")
                          .arg(m_model->netName(m_routeNet)).arg(m_activeLayer + 1)
                    : m_routePreview.message);
            } else {
                QString error;
                if (m_routePreview.accepted && m_core->commitInteractiveRoute(m_model, error)) {
                    m_routing = false;
                    m_routePreview = {};
                    emit traceAdded();
                    emit statusMessage("Interactive route committed");
                } else {
                    emit statusMessage(error.isEmpty() ? m_routePreview.message : error);
                }
                update();
            }
        }
    }

    if (e->button() == Qt::RightButton) {
        cancelRoute();
        emit statusMessage("Route cancelled");
        update();
    }
}

void PcbCanvas::mouseMoveEvent(QMouseEvent* e) {
    if (m_panning) {
        QPoint delta = e->pos() - m_panStart;
        m_tx += delta.x();
        m_ty += delta.y();
        m_panStart = e->pos();
        updateTransform();
        update();
        return;
    }
    // Snap to grid
    QPointF world = toWorld(e->pos());
    double g = m_model->gridMm;
    m_cursor = (g > 0)
        ? QPointF(std::round(world.x()/g)*g, std::round(world.y()/g)*g)
        : world;
    emit statusMessage(QString("X: %1 mm  Y: %2 mm  Layer: L%3")
        .arg(m_cursor.x(), 0,'f',3)
        .arg(m_cursor.y(), 0,'f',3)
        .arg(m_activeLayer+1));
    if (m_routing && m_core) {
        m_routePreview = m_core->updateInteractiveRoute(m_cursor);
        emit statusMessage(m_routePreview.accepted
            ? QString("Legal preview — %1 new, %2 shoved")
                  .arg(m_routePreview.addedTraces.size())
                  .arg(m_routePreview.movedTraces.size() + m_routePreview.movedVias.size())
            : m_routePreview.message);
    }
    update();
}

void PcbCanvas::mouseReleaseEvent(QMouseEvent* e) {
    if (m_panning && (e->button()==Qt::MiddleButton||e->button()==Qt::LeftButton)) {
        m_panning = false;
        setCursor(Qt::ArrowCursor);
    }
}

void PcbCanvas::wheelEvent(QWheelEvent* e) {
    double factor = (e->angleDelta().y() > 0) ? 1.15 : 1.0/1.15;
    QPointF c = e->position();
    // Zoom towards cursor
    m_tx = c.x() - factor * (c.x() - m_tx);
    m_ty = c.y() - factor * (c.y() - m_ty);
    m_scale *= factor;
    updateTransform();
    update();
}

void PcbCanvas::keyPressEvent(QKeyEvent* e) {
    if (e->key() == Qt::Key_Escape)  { cancelRoute(); m_selected.clear(); update(); }
    if (e->key() == Qt::Key_V && m_routing && m_core && m_model->copperLayers > 1) {
        const int targetLayer = (m_activeLayer + 1) % m_model->copperLayers;
        m_routePreview = m_core->placeInteractiveVia(targetLayer);
        if (m_routePreview.accepted) {
            m_activeLayer = targetLayer;
            emit statusMessage(QString("Via staged; continuing on L%1").arg(targetLayer + 1));
        } else {
            emit statusMessage(m_routePreview.message);
        }
        update();
    }
    if (e->key() == Qt::Key_F)       { fitToBoard(); }
    if (e->key() == Qt::Key_Plus ||
        e->key() == Qt::Key_Equal)   { zoomIn(); }
    if (e->key() == Qt::Key_Minus)   { zoomOut(); }
    if (e->key() == Qt::Key_Delete) {
        // Delete selected footprints
        m_model->footprints.erase(
            std::remove_if(m_model->footprints.begin(), m_model->footprints.end(),
                [&](const ProjFootprint& fp){ return m_selected.contains(fp.ref); }),
            m_model->footprints.end());
        m_selected.clear();
        m_model->setModified(true);
        update();
    }
}

void PcbCanvas::resizeEvent(QResizeEvent* event) {
    if (!event->oldSize().isValid() || event->oldSize().isEmpty())
        QTimer::singleShot(0, this, [this]{ fitToBoard(); });
    else
        updateTransform();
}

void PcbCanvas::cancelRoute() {
    if (m_routing && m_core) m_core->cancelInteractiveRoute();
    m_routing = false;
    m_routePreview = {};
    m_routeNet = -1;
}

int PcbCanvas::routeNetAt(const QPointF& world) const {
    for (const auto& fp : m_model->footprints) {
        for (const auto& pad : fp.pads) {
            if (pad.netId < 0) continue;
            const QPointF center = footprintLocalToBoard(fp, QPointF(pad.x_mm, pad.y_mm));
            const double radius = std::max(pad.w_mm, pad.h_mm) / 2 + 0.35;
            if (QLineF(center, world).length() <= radius) return pad.netId;
        }
    }
    auto distanceToSegment = [](const QPointF& point, const QPointF& a, const QPointF& b) {
        const QPointF ab = b - a;
        const double length2 = QPointF::dotProduct(ab, ab);
        const double t = length2 > 0
            ? std::clamp(QPointF::dotProduct(point - a, ab) / length2, 0.0, 1.0) : 0.0;
        return QLineF(point, a + ab * t).length();
    };
    for (const auto& trace : m_model->traces)
        if (trace.netId >= 0 && distanceToSegment(world,
                QPointF(trace.ax_mm, trace.ay_mm), QPointF(trace.bx_mm, trace.by_mm))
                <= trace.width_mm / 2 + 0.35)
            return trace.netId;
    for (const auto& via : m_model->vias)
        if (via.netId >= 0 && QLineF(world, QPointF(via.x_mm, via.y_mm)).length()
                <= via.diaMm / 2 + 0.35)
            return via.netId;
    return -1;
}

bool PcbCanvas::selectAt(const QPointF& world) {
    m_selected.clear();
    for (auto& fp : m_model->footprints) {
        if (hitTest(fp, world)) {
            m_selected.insert(fp.ref);
            emit componentSelected(fp.ref);
            update();
            return true;
        }
    }
    update();
    return false;
}

bool PcbCanvas::hitTest(const ProjFootprint& fp, const QPointF& world) const {
    QTransform t;
    t.translate(fp.x_mm, fp.y_mm);
    t.rotate(fp.rotDeg);
    if (fp.side == 1) t.scale(1.0, -1.0);
    QTransform inv = t.inverted();
    QPointF local = inv.map(world);
    for (auto& pad : fp.pads) {
        QRectF r(pad.x_mm-pad.w_mm/2, pad.y_mm-pad.h_mm/2, pad.w_mm, pad.h_mm);
        r.adjust(-0.5,-0.5,0.5,0.5);
        if (r.contains(local)) return true;
    }
    return false;
}
