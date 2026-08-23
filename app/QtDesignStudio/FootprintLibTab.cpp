// FootprintLibTab.cpp — Inkscape-style footprint editor (Stages 1-2).
//   • vector canvas: infinite zoom/pan, mm grid, datum, live coords
//   • tools: Select, Node editor, Pad, Rect, Ellipse, Polygon, Line, Pen (bezier),
//            Text, Measure — palette uses real Inkscape symbolic icons (recoloured)
//   • persistence: geometry → *.fp.svg (Inkscape-editable, ds: namespace),
//                  properties → *.props.json (datasheet hub). See memory
//                  [[footprint-save-format]].
//   • agent/MCP hook via agentRequested().
#include "FootprintLibTab.h"
#include "ProjectModel.h"
#include "InkscapeHost.h"

#include <QStackedWidget>
#include <QGraphicsScene>
#include <QGraphicsRectItem>
#include <QGraphicsEllipseItem>
#include <QGraphicsPixmapItem>
#include <QGraphicsSimpleTextItem>
#include <QImageReader>
#include <QImage>
#include <QPixmap>
#include <QTransform>
#include <QPainterPath>
#include <QPainterPathStroker>
#include <QStyleOptionGraphicsItem>
#include <QGraphicsSceneHoverEvent>
#include <QFont>
#ifdef HAVE_QTSVGWIDGETS
#include <QGraphicsSvgItem>
#include <QSvgRenderer>
#endif
#include <QToolBar>
#include <QAction>
#include <QActionGroup>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QGroupBox>
#include <QDoubleSpinBox>
#include <QLabel>
#include <QPushButton>
#include <QLineEdit>
#include <QFileDialog>
#include <QMessageBox>
#include <QStandardPaths>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QMap>
#include <QProcess>
#include <QCoreApplication>
#include <QXmlStreamReader>
#include <algorithm>
#include <QRegularExpression>
#include <QWheelEvent>
#include <QMouseEvent>
#include <QKeyEvent>
#include <QNativeGestureEvent>
#include <QPainter>
#include <QCursor>
#include <QPixmap>
#include <QScrollBar>
#include <QIcon>
#include <QtMath>

static QString generatedArtifactDir() {
    QString root = qEnvironmentVariable("DESIGNSTUDIO_ARTIFACT_ROOT");
    if (root.isEmpty())
        root = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation)
               + "/artifacts";
    QDir().mkpath(root);
    return root;
}

// ── FpPathItem ──────────────────────────────────────────────────────────────
FpPathItem::FpPathItem(QGraphicsItem* parent) : QGraphicsPathItem(parent) {
    setPen(QPen(QColor(0x4b, 0x4b, 0x4b), 0));   // cosmetic, thin at all zooms
    setBrush(Qt::NoBrush);
}

void FpPathItem::rebuild() {
    QPainterPath path;
    if (nodes.isEmpty()) { setPath(path); return; }
    path.moveTo(nodes[0].pt);
    for (int i = 1; i < nodes.size(); ++i)
        path.cubicTo(nodes[i - 1].cOut, nodes[i].cIn, nodes[i].pt);
    if (closed && nodes.size() > 2)
        path.cubicTo(nodes.last().cOut, nodes[0].cIn, nodes[0].pt);
    setPath(path);
}

// ── FpHitPathItem ────────────────────────────────────────────────────────────
FpHitPathItem::FpHitPathItem(QGraphicsItem* parent) : QGraphicsPathItem(parent) {
    setAcceptHoverEvents(true);
    setFlags(ItemIsSelectable | ItemIsMovable);
}

QRectF FpHitPathItem::boundingRect() const {
    // Pad for the cosmetic hover/selection overdraw so it isn't clipped.
    return QGraphicsPathItem::boundingRect().adjusted(-0.6, -0.6, 0.6, 0.6);
}

// Hit area = the stroke widened to a constant ~4 px (like Inkscape's click
// tolerance), so thin lines are easy to select at any zoom; plus the fill if any.
QPainterPath FpHitPathItem::shape() const {
    double tolMm = 0.4;
    if (scene() && !scene()->views().isEmpty()) {
        const double pxPerMm = qAbs(scene()->views().first()->transform().m11());
        if (pxPerMm > 0) tolMm = 4.0 / pxPerMm;
    }
    QPainterPathStroker st;
    st.setWidth(qMax(pen().widthF(), tolMm * 2.0));
    st.setCapStyle(Qt::RoundCap);
    st.setJoinStyle(Qt::RoundJoin);
    QPainterPath s = st.createStroke(path());
    if (brush().style() != Qt::NoBrush) s.addPath(path());
    return s;
}

void FpHitPathItem::hoverEnterEvent(QGraphicsSceneHoverEvent*) { m_hover = true;  update(); }
void FpHitPathItem::hoverLeaveEvent(QGraphicsSceneHoverEvent*) { m_hover = false; update(); }

void FpHitPathItem::paint(QPainter* p, const QStyleOptionGraphicsItem*, QWidget*) {
    p->setBrush(brush());
    p->setPen(pen());
    p->drawPath(path());

    if (isSelected()) {                          // bright outline + dashed bbox
        QPen hi(QColor(0xff, 0xa5, 0x00)); hi.setCosmetic(true); hi.setWidthF(1.6);
        p->setBrush(Qt::NoBrush); p->setPen(hi); p->drawPath(path());
        QPen box(QColor(0xff, 0xa5, 0x00)); box.setCosmetic(true); box.setStyle(Qt::DashLine);
        p->setPen(box); p->drawRect(QGraphicsPathItem::boundingRect());
    } else if (m_hover) {                         // Inkscape-style hover highlight
        QPen hp(QColor(0x3d, 0xae, 0xff)); hp.setCosmetic(true); hp.setWidthF(1.6);
        p->setBrush(Qt::NoBrush); p->setPen(hp); p->drawPath(path());
    }
}

// ── FpCanvas ────────────────────────────────────────────────────────────────
FpCanvas::FpCanvas(QWidget* parent) : QGraphicsView(parent) {
    m_scene = new QGraphicsScene(this);
    m_scene->setSceneRect(-5000, -5000, 10000, 10000);   // large, so zoom/pan isn't clamped
    setScene(m_scene);
    setRenderHints(QPainter::Antialiasing | QPainter::SmoothPixmapTransform);
    setTransformationAnchor(QGraphicsView::NoAnchor);   // we anchor zoom manually (zoomAt)
    setResizeAnchor(QGraphicsView::AnchorViewCenter);
    setDragMode(QGraphicsView::RubberBandDrag);
    setMouseTracking(true);
    setFocusPolicy(Qt::StrongFocus);
    viewport()->setContextMenuPolicy(Qt::PreventContextMenu);   // right-drag pans instead
    setBackgroundBrush(QColor(0x20, 0x20, 0x24));
    scale(8.0, -8.0);   // ~8 px/mm, Y up (PCB convention; SVG export flips Y)

    // Remember the latest rubber-band drag (in scene mm) so the Downsizer can
    // crop the imported SVG to exactly the dragged reference + component.
    connect(this, &QGraphicsView::rubberBandChanged, this,
            [this](QRect rect, QPointF from, QPointF to) {
                if (!rect.isNull()) m_rubberScene = QRectF(from, to).normalized();
            });
}

double FpCanvas::pxPerMm() const { return qAbs(transform().m11()); }

// A thin, sharp "+" crosshair (1-px lines, small centre gap) for precise work,
// rather than the chunky system cross cursor.
static QCursor thinCrosshair() {
    static QCursor c = [] {
        const int S = 31, m = S / 2, gap = 3;
        QPixmap pm(S, S);
        pm.fill(Qt::transparent);
        QPainter p(&pm);
        QPen pen(QColor(0xF0, 0xF0, 0xF0));
        pen.setWidth(1);
        p.setPen(pen);
        p.drawLine(0, m, m - gap, m);
        p.drawLine(m + gap, m, S - 1, m);
        p.drawLine(m, 0, m, m - gap);
        p.drawLine(m, m + gap, m, S - 1);
        p.drawPoint(m, m);
        p.end();
        return QCursor(pm, m, m);
    }();
    return c;
}

void FpCanvas::setTool(Tool t) {
    if (m_tool == Pen  && t != Pen)  finishDraft(true);
    if (m_tool == Node && t != Node) { m_editPath = nullptr; viewport()->update(); }
    m_sizing = false;                       // abandon any in-progress tap-spread-tap
    m_tool = t;
    setDragMode(t == Select ? QGraphicsView::RubberBandDrag : QGraphicsView::NoDrag);
    // Thin sharp "+" crosshair for precision placement/drawing; arrow for pick/edit.
    const bool precise = (t != Select && t != Node);
    viewport()->setCursor(precise ? thinCrosshair() : QCursor(Qt::ArrowCursor));
}

void FpCanvas::setRubberSizing(bool on) {
    m_rubberSizing = on;
    m_sizing = false;
    viewport()->update();
}

void FpCanvas::fit() {
    if (m_scene->items().isEmpty()) { resetTransform(); scale(8.0, -8.0); return; }
    fitInView(m_scene->itemsBoundingRect().adjusted(-2, -2, 2, 2), Qt::KeepAspectRatio);
    if (transform().m22() > 0) scale(1, -1);   // keep Y-up
    emit zoomChanged(pxPerMm() / 8.0 * 100.0);
}

// Zoom magnitude-proportionally (smooth for trackpads), clamped, keeping the
// scene point under `anchorViewPos` fixed (zoom toward the cursor).
void FpCanvas::zoomAt(double factor, const QPoint& anchorViewPos) {
    const double cur = pxPerMm();
    double target = qBound(0.2, cur * factor, 5000.0);   // px/mm limits
    factor = target / cur;
    if (qFuzzyCompare(factor, 1.0)) return;
    const QPointF before = mapToScene(anchorViewPos);
    scale(factor, factor);
    const QPointF after = mapToScene(anchorViewPos);
    const QPointF d = after - before;
    translate(d.x(), d.y());                             // pin the cursor's scene point
    emit zoomChanged(pxPerMm() / 8.0 * 100.0);
}

void FpCanvas::zoomBy(double f) { zoomAt(f, viewport()->rect().center()); }

void FpCanvas::wheelEvent(QWheelEvent* e) {
    double delta = e->angleDelta().y();
    if (delta == 0) delta = e->angleDelta().x();
    if (delta == 0) { e->ignore(); return; }
    zoomAt(std::pow(1.0015, delta), e->position().toPoint());   // toward cursor
    e->accept();
}

bool FpCanvas::viewportEvent(QEvent* e) {
    if (e->type() == QEvent::NativeGesture) {
        auto* g = static_cast<QNativeGestureEvent*>(e);
        if (g->gestureType() == Qt::ZoomNativeGesture) {        // trackpad pinch
            zoomAt(1.0 + g->value(), g->position().toPoint());
            return true;
        }
    }
    return QGraphicsView::viewportEvent(e);
}

void FpCanvas::mouseMoveEvent(QMouseEvent* e) {
    if (m_panning && (e->buttons() & Qt::RightButton)) {   // grab-to-pan
        const QPoint d = e->pos() - m_panLast;
        m_panLast = e->pos();
        horizontalScrollBar()->setValue(horizontalScrollBar()->value() - d.x());
        verticalScrollBar()->setValue(verticalScrollBar()->value() - d.y());
        emit cursorMm(mapToScene(e->pos()).x(), mapToScene(e->pos()).y());
        return;
    }

    const QPointF s = mapToScene(e->pos());
    m_cursorScene = s;
    emit cursorMm(s.x(), s.y());

    if (m_sizing) {                              // live "spread" preview
        const QRectF r = QRectF(m_sizeStart, s).normalized();
        emit status(QString("%1 × %2 mm").arg(r.width(), 0, 'f', 3).arg(r.height(), 0, 'f', 3));
        viewport()->update();
        return;
    }

    if (m_tool == Pen && m_drawing && m_dragHandle && (e->buttons() & Qt::LeftButton)) {
        FpNode& n = m_draft->nodes.last();
        n.cOut = s; n.cIn = n.pt * 2.0 - s; n.smooth = true;   // symmetric handle
        m_draft->rebuild(); viewport()->update();
        return;
    }
    if (m_tool == Node && m_grab != Grab::None && m_editPath && (e->buttons() & Qt::LeftButton)) {
        const QPointF ls = m_editPath->mapFromScene(s);
        FpNode& n = m_editPath->nodes[m_grabIdx];
        if (m_grab == Grab::Anchor)      { QPointF d = ls - n.pt; n.pt += d; n.cIn += d; n.cOut += d; }
        else if (m_grab == Grab::CtrlOut){ n.cOut = ls; if (n.smooth) n.cIn = n.pt * 2.0 - ls; }
        else                             { n.cIn  = ls; if (n.smooth) n.cOut = n.pt * 2.0 - ls; }
        m_editPath->rebuild(); viewport()->update();
        return;
    }
    if (m_measuring) {
        const double d = std::hypot(s.x() - m_measureStart.x(), s.y() - m_measureStart.y());
        emit status(QString("Measure: %1 mm").arg(d, 0, 'f', 3));
    }
    QGraphicsView::mouseMoveEvent(e);
    if (m_drawing) viewport()->update();   // live rubber preview
}

void FpCanvas::mousePressEvent(QMouseEvent* e) {
    if (e->button() == Qt::RightButton) {           // grab-to-pan
        m_panning = true;
        m_panLast = e->pos();
        viewport()->setCursor(Qt::ClosedHandCursor);
        e->accept();
        return;
    }

    const QPointF s = mapToScene(e->pos());
    m_cursorScene = s;

    if (m_tool == Pen) {
        if (e->button() == Qt::LeftButton) {
            if (!m_drawing) {
                m_draft = new FpPathItem();
                m_scene->addItem(m_draft);
                m_drawing = true;
                FpNode n; n.pt = s; n.cIn = s; n.cOut = s; m_draft->nodes.append(n);
            } else {
                // close if clicking near the first node
                const QPointF v0 = mapFromScene(m_draft->nodes.first().pt);
                if (m_draft->nodes.size() > 2 &&
                    (v0 - QPointF(e->pos())).manhattanLength() < 10) {
                    m_draft->closed = true; finishDraft(true); return;
                }
                FpNode n; n.pt = s; n.cIn = s; n.cOut = s; m_draft->nodes.append(n);
            }
            m_dragHandle = true;
            m_draft->rebuild();
        }
        return;
    }

    if (m_tool == Node) {
        Grab k; int idx = hitNode(QPointF(e->pos()), k);
        if (idx >= 0) { m_grab = k; m_grabIdx = idx; return; }
        auto* p = dynamic_cast<FpPathItem*>(itemAt(e->pos()));
        m_editPath = p;            // null clears the editor
        viewport()->update();
        QGraphicsView::mousePressEvent(e);
        return;
    }

    // Tap–spread–tap sizing: 1st tap = corner, move to spread, 2nd tap = commit.
    if (m_rubberSizing && (m_tool == RectShape || m_tool == Ellipse || m_tool == Line)
        && e->button() == Qt::LeftButton) {
        if (!m_sizing) {
            m_sizing = true; m_sizeStart = s;
            emit status("Tap to set the opposite corner (Esc cancels)");
        } else {
            commitSizedShape(m_sizeStart, s);
            m_sizing = false;
        }
        viewport()->update();
        return;
    }

    switch (m_tool) {
    case Pad: {
        auto* r = m_scene->addRect(QRectF(s.x() - 0.15, s.y() - 0.425, 0.30, 0.85),
                                   QPen(QColor(0xC8, 0x96, 0x32), 0), QBrush(QColor(0xC8, 0x96, 0x32)));
        r->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        r->setData(0, "pad");
        emit status(QString("Pad @ (%1, %2) mm").arg(s.x(), 0, 'f', 3).arg(s.y(), 0, 'f', 3));
        break;
    }
    case RectShape: {
        auto* r = m_scene->addRect(QRectF(s.x() - 0.5, s.y() - 0.5, 1.0, 1.0),
                                   QPen(QColor(0x4b, 0x4b, 0x4b), 0));
        r->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        break;
    }
    case Ellipse: {
        auto* el = m_scene->addEllipse(QRectF(s.x() - 0.5, s.y() - 0.5, 1.0, 1.0),
                                       QPen(QColor(0x4b, 0x4b, 0x4b), 0));
        el->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        break;
    }
    case Measure:
        m_measuring = !m_measuring;
        if (m_measuring) m_measureStart = s;
        break;
    default: break;
    }
    QGraphicsView::mousePressEvent(e);
    reportSelection();
}

void FpCanvas::mouseReleaseEvent(QMouseEvent* e) {
    if (e->button() == Qt::RightButton && m_panning) {
        m_panning = false;
        const bool precise = (m_tool != Select && m_tool != Node);
        viewport()->setCursor(precise ? thinCrosshair() : QCursor(Qt::ArrowCursor));
        e->accept();
        return;
    }
    if (m_tool == Pen)  { m_dragHandle = false; }
    if (m_tool == Node) { m_grab = Grab::None; m_grabIdx = -1; }
    QGraphicsView::mouseReleaseEvent(e);
}

void FpCanvas::mouseDoubleClickEvent(QMouseEvent* e) {
    if (m_tool == Pen && m_drawing) {
        if (m_draft->nodes.size() > 1) m_draft->nodes.removeLast();   // drop dbl-click dup
        finishDraft(true);
        return;
    }
    QGraphicsView::mouseDoubleClickEvent(e);
}

void FpCanvas::keyPressEvent(QKeyEvent* e) {
    if (m_sizing && e->key() == Qt::Key_Escape) {
        m_sizing = false; viewport()->update();
        emit status("Cancelled");
        return;
    }
    if (m_drawing) {
        if (e->key() == Qt::Key_Return || e->key() == Qt::Key_Enter) { finishDraft(true);  return; }
        if (e->key() == Qt::Key_Escape)                              { finishDraft(false); return; }
    }
    // Node tool: Delete removes the grabbed node from the path being edited.
    if (m_tool == Node && m_editPath && e->key() == Qt::Key_Delete && m_grabIdx >= 0) {
        if (m_editPath->nodes.size() > 2) {
            m_editPath->nodes.removeAt(m_grabIdx);
            m_editPath->rebuild();
        }
        m_grab = Grab::None; m_grabIdx = -1; viewport()->update();
        return;
    }
    // Otherwise Delete / Backspace removes the selected items.
    if (e->key() == Qt::Key_Delete || e->key() == Qt::Key_Backspace) {
        const auto sel = m_scene->selectedItems();
        if (!sel.isEmpty()) {
            for (auto* it : sel) {
                if (it == m_editPath) { m_editPath = nullptr; m_grab = Grab::None; m_grabIdx = -1; }
                m_scene->removeItem(it);
                delete it;
            }
            emit status(QString("Deleted %1 item%2").arg(sel.size()).arg(sel.size() == 1 ? "" : "s"));
            reportSelection();
            viewport()->update();
            return;
        }
    }
    QGraphicsView::keyPressEvent(e);
}

void FpCanvas::finishDraft(bool keep) {
    if (!m_draft) { m_drawing = false; return; }
    if (keep && m_draft->nodes.size() >= 2) {
        m_draft->rebuild();
        m_draft->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        m_draft->setData(0, "path");
        emit status(QString("Path: %1 nodes").arg(m_draft->nodes.size()));
    } else {
        m_scene->removeItem(m_draft); delete m_draft;
    }
    m_draft = nullptr; m_drawing = false; m_dragHandle = false;
    viewport()->update();
}

void FpCanvas::commitSizedShape(const QPointF& a, const QPointF& b) {
    const QPen pen(QColor(0x4b, 0x4b, 0x4b), 0);
    if (m_tool == RectShape) {
        const QRectF r = QRectF(a, b).normalized();
        if (r.width() < 1e-4 && r.height() < 1e-4) return;          // ignore a stray double-tap
        auto* it = m_scene->addRect(r, pen);
        it->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        emit status(QString("Rect %1 × %2 mm").arg(r.width(), 0, 'f', 3).arg(r.height(), 0, 'f', 3));
    } else if (m_tool == Ellipse) {
        const QRectF r = QRectF(a, b).normalized();
        if (r.width() < 1e-4 && r.height() < 1e-4) return;
        auto* it = m_scene->addEllipse(r, pen);
        it->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        emit status(QString("Ellipse %1 × %2 mm").arg(r.width(), 0, 'f', 3).arg(r.height(), 0, 'f', 3));
    } else if (m_tool == Line) {
        if ((a - b).manhattanLength() < 1e-4) return;
        auto* p = new FpPathItem();
        m_scene->addItem(p);
        FpNode n0; n0.pt = a; n0.cIn = a; n0.cOut = a;
        FpNode n1; n1.pt = b; n1.cIn = b; n1.cOut = b;
        p->nodes.append(n0); p->nodes.append(n1);
        p->rebuild();
        p->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        p->setData(0, "path");
        emit status(QString("Line %1 mm").arg(std::hypot(b.x() - a.x(), b.y() - a.y()), 0, 'f', 3));
    }
}

int FpCanvas::hitNode(const QPointF& vp, Grab& kind) const {
    if (!m_editPath) { kind = Grab::None; return -1; }
    const double tol = 9.0;
    for (int i = 0; i < m_editPath->nodes.size(); ++i) {
        const FpNode& n = m_editPath->nodes[i];
        if (n.cOut != n.pt &&
            (QPointF(mapFromScene(m_editPath->mapToScene(n.cOut))) - vp).manhattanLength() < tol)
            { kind = Grab::CtrlOut; return i; }
        if (n.cIn != n.pt &&
            (QPointF(mapFromScene(m_editPath->mapToScene(n.cIn))) - vp).manhattanLength() < tol)
            { kind = Grab::CtrlIn; return i; }
        if ((QPointF(mapFromScene(m_editPath->mapToScene(n.pt))) - vp).manhattanLength() < tol)
            { kind = Grab::Anchor; return i; }
    }
    kind = Grab::None; return -1;
}

void FpCanvas::reportSelection() {
    auto sel = m_scene->selectedItems();
    if (!sel.isEmpty()) {
        const QRectF b = sel.first()->sceneBoundingRect();
        emit selectionGeom(b.x(), b.y(), b.width(), b.height(), true);
    } else {
        emit selectionGeom(0, 0, 0, 0, false);
    }
}

void FpCanvas::drawBackground(QPainter* p, const QRectF& rect) {
    QGraphicsView::drawBackground(p, rect);
    const double px = pxPerMm();
    double step = m_gridMm;
    while (step * px < 6.0) step *= 2.0;

    QPen fine(QColor(0x2c, 0x2c, 0x32), 0), major(QColor(0x3a, 0x3a, 0x42), 0);
    const double l = std::floor(rect.left()   / step) * step;
    const double t = std::floor(rect.bottom() / step) * step;
    for (double x = l; x <= rect.right(); x += step) {
        p->setPen((std::fmod(std::abs(x), step * 5) < 1e-6) ? major : fine);
        p->drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()));
    }
    for (double y = t; y >= rect.top(); y -= step) {
        p->setPen((std::fmod(std::abs(y), step * 5) < 1e-6) ? major : fine);
        p->drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y));
    }
    p->setPen(QPen(QColor(0x55, 0x88, 0xcc), 0));
    p->drawLine(QPointF(-2, 0), QPointF(2, 0));
    p->drawLine(QPointF(0, -2), QPointF(0, 2));
}

void FpCanvas::drawForeground(QPainter* p, const QRectF&) {
    const double h = 4.0 / pxPerMm();   // marker half-size in mm

    if (m_sizing) {                                     // tap-spread-tap preview
        p->setPen(QPen(QColor(0x88, 0xcc, 0x88), 0, Qt::DashLine));
        p->setBrush(Qt::NoBrush);
        const QRectF r = QRectF(m_sizeStart, m_cursorScene).normalized();
        if (m_tool == RectShape)    p->drawRect(r);
        else if (m_tool == Ellipse) p->drawEllipse(r);
        else if (m_tool == Line)    p->drawLine(m_sizeStart, m_cursorScene);
    }

    if (m_drawing && m_draft && !m_draft->nodes.isEmpty()) {
        p->setPen(QPen(QColor(0x88, 0xcc, 0x88), 0, Qt::DashLine));
        p->drawLine(m_draft->nodes.last().pt, m_cursorScene);
    }

    // ── Dimension overlay (feature-edge grid + annotations) ──────────────────
    // Drawn in VIEWPORT pixel space (reset transform + mapFromScene) so text is
    // never mirrored by the Y-flipped view and font/arrow sizes are zoom-stable
    // and finite (no 1/pxPerMm blow-up).
    if (m_hasDimExtent || !m_dimItems.isEmpty()) {
        p->save();
        p->resetTransform();
        auto V = [this](const QPointF& sc) -> QPointF { return mapFromScene(sc); };

        const QColor confCol(0x2c, 0xc8, 0x44);   // green: confirmed by ≥2 views
        const QColor singCol(0xe6, 0x8c, 0x00);   // orange: single view
        const QColor dimCol (0x00, 0xcc, 0xff);   // cyan
        QFont fnt = p->font(); fnt.setPixelSize(11); p->setFont(fnt);

        double exTopPx = 0, exBotPx = 0, exLeftPx = 0, exRightPx = 0;
        if (m_hasDimExtent) {
            const QRectF& ex = m_dimExtent;
            const QPointF c0 = V(ex.topLeft()), c1 = V(ex.bottomRight());
            exLeftPx  = qMin(c0.x(), c1.x()); exRightPx = qMax(c0.x(), c1.x());
            exTopPx   = qMin(c0.y(), c1.y()); exBotPx   = qMax(c0.y(), c1.y());

            for (const FeatureEdge& fe : m_featureEdges) {
                const QColor c = (fe.views >= 2) ? confCol : singCol;
                p->setPen(QPen(c, 1, fe.views >= 2 ? Qt::SolidLine : Qt::DashLine));
                if (fe.vertical) {
                    const double x = V(QPointF(fe.coord, 0)).x();
                    p->drawLine(QPointF(x, exTopPx), QPointF(x, exBotPx));
                } else {
                    const double y = V(QPointF(0, fe.coord)).y();
                    p->drawLine(QPointF(exLeftPx, y), QPointF(exRightPx, y));
                }
            }
            p->setPen(QPen(QColor(0xff, 0xff, 0xff), 1)); p->setBrush(Qt::NoBrush);
            p->drawRect(QRectF(QPointF(exLeftPx, exTopPx), QPointF(exRightPx, exBotPx)));
        }

        const double arrowW = 5.0, step = 15.0;   // pixels
        auto arrowH = [&](double x, double y, double dir) {
            QPolygonF t; t << QPointF(x, y) << QPointF(x + dir, y - arrowW*0.5)
                           << QPointF(x + dir, y + arrowW*0.5);
            p->setBrush(dimCol); p->drawPolygon(t); p->setBrush(Qt::NoBrush);
        };
        auto arrowV = [&](double x, double y, double dir) {
            QPolygonF t; t << QPointF(x, y) << QPointF(x - arrowW*0.5, y + dir)
                           << QPointF(x + arrowW*0.5, y + dir);
            p->setBrush(dimCol); p->drawPolygon(t); p->setBrush(Qt::NoBrush);
        };

        for (const DimAnnotation& da : m_dimItems) {
            p->setPen(QPen(dimCol, 1));
            if (da.axis == "H") {
                const double y  = exBotPx + step * (da.level + 1);
                const double x1 = V(QPointF(da.tip1Mm.x(), 0)).x();
                const double x2 = V(QPointF(da.tip2Mm.x(), 0)).x();
                p->drawLine(QPointF(x1, y), QPointF(x2, y));
                p->drawLine(QPointF(x1, y), QPointF(x1, exBotPx));
                p->drawLine(QPointF(x2, y), QPointF(x2, exBotPx));
                arrowH(x1, y,  arrowW); arrowH(x2, y, -arrowW);
                p->drawText(QPointF((x1 + x2) / 2 - da.txt.size() * 3.0, y + 11), da.txt);
            } else {
                const double x  = exRightPx + step * (da.level + 1);
                const double y1 = V(QPointF(0, da.tip1Mm.y())).y();
                const double y2 = V(QPointF(0, da.tip2Mm.y())).y();
                p->drawLine(QPointF(x, y1), QPointF(x, y2));
                p->drawLine(QPointF(x, y1), QPointF(exRightPx, y1));
                p->drawLine(QPointF(x, y2), QPointF(exRightPx, y2));
                arrowV(x, y1,  arrowW); arrowV(x, y2, -arrowW);
                p->drawText(QPointF(x + 4, (y1 + y2) / 2 + 4), da.txt);
            }
        }
        p->restore();
    }

    if (!m_editPath) return;
    auto S = [this](QPointF lp) { return m_editPath->mapToScene(lp); };
    p->setPen(QPen(QColor(0x55, 0x88, 0xcc), 0));
    for (const FpNode& n : m_editPath->nodes) {
        if (n.cIn  != n.pt) p->drawLine(S(n.pt), S(n.cIn));
        if (n.cOut != n.pt) p->drawLine(S(n.pt), S(n.cOut));
    }
    for (const FpNode& n : m_editPath->nodes) {
        p->setPen(Qt::NoPen);
        p->setBrush(QColor(0x55, 0x88, 0xcc));
        if (n.cIn  != n.pt) p->drawEllipse(S(n.cIn),  h * 0.8, h * 0.8);
        if (n.cOut != n.pt) p->drawEllipse(S(n.cOut), h * 0.8, h * 0.8);
        p->setBrush(QColor(0xff, 0xcc, 0x33));
        const QPointF a = S(n.pt);
        p->drawRect(QRectF(a.x() - h, a.y() - h, 2 * h, 2 * h));
    }
}

// ── Dimension overlay from pad_dimensioner.py output ─────────────────────────
// Coordinates are already actual mm, datum = connector centre (Y-up). We render
// the merged feature-edge grid, the part extent, and one stacked two-arrow
// annotation per unique dimension (deduplicated across the overlaid views).
void FpCanvas::showPadDimensions(const QJsonObject& root) {
    m_dimItems.clear();
    m_featureEdges.clear();
    m_hasDimExtent = false;

    const QJsonObject ext = root["extent_mm"].toObject();
    const double extW = ext["width"].toDouble();
    const double extH = ext["height"].toDouble();
    if (extW > 0 && extH > 0) {
        m_dimExtent = QRectF(-extW / 2.0, -extH / 2.0, extW, extH);
        m_hasDimExtent = true;
    }

    // merged feature-edge grid
    const QJsonObject ov = root["overlay_merged"].toObject();
    for (const auto& v : ov["vertical_edges"].toArray()) {
        const QJsonObject e = v.toObject();
        m_featureEdges.append({ true, e["x"].toDouble(), e["views"].toInt(1) });
    }
    for (const auto& v : ov["horizontal_edges"].toArray()) {
        const QJsonObject e = v.toObject();
        m_featureEdges.append({ false, e["y"].toDouble(), e["views"].toInt(1) });
    }

    // dimensions: dedupe across views by (axis + rounded edges), stack by span
    QMap<QString, DimAnnotation> uniq;
    const QJsonObject views = root["views"].toObject();
    for (const QString& vn : views.keys()) {
        for (const auto& dv : views[vn].toObject()["dims"].toArray()) {
            const QJsonObject d = dv.toObject();
            const QString axis = d["axis"].toString();
            const QJsonArray edges = d["edges"].toArray();
            if (edges.size() < 2) continue;
            const double e1 = edges[0].toDouble(), e2 = edges[1].toDouble();
            const QString key = axis + QString::number(qRound(e1*100)) + "_" +
                                       QString::number(qRound(e2*100));
            if (uniq.contains(key)) continue;
            DimAnnotation da;
            da.txt   = d["txt"].toString();
            da.valMm = d["val"].toDouble();
            da.axis  = axis;
            // tips at feature edges; the perpendicular coord points back at the part
            if (axis == "H") { da.tip1Mm = QPointF(e1, 0); da.tip2Mm = QPointF(e2, 0); }
            else             { da.tip1Mm = QPointF(0, e1); da.tip2Mm = QPointF(0, e2); }
            uniq.insert(key, da);
        }
    }
    // assign stack levels: smaller spans innermost (level 0), per axis
    QVector<DimAnnotation> hs, vs;
    for (auto& da : uniq) (da.axis == "H" ? hs : vs).append(da);
    auto bySpan = [](const DimAnnotation& a, const DimAnnotation& b) {
        return a.valMm < b.valMm; };
    std::sort(hs.begin(), hs.end(), bySpan);
    std::sort(vs.begin(), vs.end(), bySpan);
    for (int i = 0; i < hs.size(); ++i) { hs[i].level = i; m_dimItems.append(hs[i]); }
    for (int i = 0; i < vs.size(); ++i) { vs[i].level = i; m_dimItems.append(vs[i]); }

    // ── Place reconstructed pads as REAL footprint pads at absolute coords ────
    // Remove any pads from a previous detection run first, then create rect pads
    // centred at the assembled datum-relative coordinates. A "2x …" source dim
    // means the feature is mirrored across the connector centre, so we also place
    // the mirror instance. Tagged data(0)="dimpad" so re-runs are idempotent.
    const auto existing = m_scene->items();
    for (auto* it : existing)
        if (it->data(0).toString() == "dimpad") { m_scene->removeItem(it); delete it; }

    int padN = 0;
    const QJsonArray padArr = root["pads"].toArray();
    auto placePad = [&](double cx, double cy, double w, double h) {
        auto* r = new QGraphicsRectItem(QRectF(cx - w / 2.0, cy - h / 2.0, w, h));
        r->setPen(QPen(QColor(0xc8, 0x96, 0x32), 0));         // copper outline
        r->setBrush(QColor(0xc8, 0x96, 0x32, 90));            // translucent fill
        r->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        r->setData(0, "dimpad");
        r->setData(1, QString("P%1").arg(++padN));
        r->setZValue(5);
        m_scene->addItem(r);
    };
    for (const auto& pv : padArr) {
        const QJsonObject pd = pv.toObject();
        const double cx = pd["cx"].toDouble(), cy = pd["cy"].toDouble();
        const double w  = pd["w"].toDouble(),  h  = pd["h"].toDouble();
        if (w <= 0 || h <= 0) continue;
        placePad(cx, cy, w, h);
        // mirror "2x" features across the connector centre (X axis)
        const bool mirror = pd["w_src"].toString().contains('x') ||
                            pd["h_src"].toString().contains('x');
        if (mirror && qAbs(cx) > 0.05) placePad(-cx, cy, w, h);
    }

    viewport()->update();
}

void FpCanvas::clearDimAnnotations() {
    m_dimItems.clear();
    m_featureEdges.clear();
    m_hasDimExtent = false;
    viewport()->update();
}

// ── Persistence: geometry → SVG (Y flipped to standard SVG Y-down) ────────────
void FpCanvas::clearFootprint() {
    m_editPath = nullptr; m_draft = nullptr; m_drawing = false; m_grab = Grab::None;
    m_scene->clear();
    viewport()->update();
}

QString FpCanvas::toSvg() const {
    QString pads, silk;
    QRectF bb;
    auto grow = [&](const QRectF& r) { bb = bb.isNull() ? r : bb.united(r); };
    auto fy = [](double y) { return -y; };   // scene Y-up → SVG Y-down

    for (auto* it : m_scene->items()) {
        if (auto* path = dynamic_cast<FpPathItem*>(it)) {
            grow(path->sceneBoundingRect());
            QString d;
            auto P = [&](QPointF lp) {
                const QPointF q = path->mapToScene(lp);
                return QString("%1,%2 ").arg(q.x(), 0, 'f', 4).arg(fy(q.y()), 0, 'f', 4);
            };
            d += "M " + P(path->nodes[0].pt);
            for (int i = 1; i < path->nodes.size(); ++i)
                d += "C " + P(path->nodes[i - 1].cOut) + P(path->nodes[i].cIn) + P(path->nodes[i].pt);
            if (path->closed && path->nodes.size() > 2)
                d += "C " + P(path->nodes.last().cOut) + P(path->nodes[0].cIn) + P(path->nodes[0].pt) + "Z";
            silk += QString("    <path d=\"%1\" ds:type=\"outline\" ds:layer=\"silk-top\" "
                            "style=\"fill:none;stroke:#4b4b4b;stroke-width:0.1\"/>\n").arg(d.trimmed());
        } else if (auto* r = dynamic_cast<QGraphicsRectItem*>(it)) {
            const QRectF b = r->sceneBoundingRect(); grow(b);
            const bool isPad = r->data(0).toString() == "pad";
            QString el = QString("    <rect x=\"%1\" y=\"%2\" width=\"%3\" height=\"%4\" ")
                             .arg(b.x(), 0, 'f', 4).arg(fy(b.y() + b.height()), 0, 'f', 4)
                             .arg(b.width(), 0, 'f', 4).arg(b.height(), 0, 'f', 4);
            if (isPad) {
                el += QString("ds:type=\"pad\" ds:pad=\"%1\" ds:net=\"%2\" ds:shape=\"rect\" "
                              "ds:layer=\"copper-top\" style=\"fill:#c89632;stroke:none\"/>\n")
                          .arg(r->data(1).toString(), r->data(2).toString());
                pads += el;
            } else {
                el += "ds:type=\"shape\" ds:layer=\"silk-top\" style=\"fill:none;stroke:#4b4b4b;stroke-width:0.1\"/>\n";
                silk += el;
            }
        } else if (auto* e2 = dynamic_cast<QGraphicsEllipseItem*>(it)) {
            const QRectF b = e2->sceneBoundingRect(); grow(b);
            silk += QString("    <ellipse cx=\"%1\" cy=\"%2\" rx=\"%3\" ry=\"%4\" "
                            "ds:type=\"shape\" ds:layer=\"silk-top\" "
                            "style=\"fill:none;stroke:#4b4b4b;stroke-width:0.1\"/>\n")
                        .arg(b.center().x(), 0, 'f', 4).arg(fy(b.center().y()), 0, 'f', 4)
                        .arg(b.width() / 2, 0, 'f', 4).arg(b.height() / 2, 0, 'f', 4);
        }
    }
    if (bb.isNull()) bb = QRectF(-1, -1, 2, 2);
    const double vbx = bb.x(), vby = -(bb.y() + bb.height()), vbw = bb.width(), vbh = bb.height();

    QString svg;
    svg += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    svg += "<svg xmlns=\"http://www.w3.org/2000/svg\"\n";
    svg += "     xmlns:inkscape=\"http://www.inkscape.org/namespaces/inkscape\"\n";
    svg += "     xmlns:ds=\"https://designstudio/footprint\"\n";
    svg += "     ds:format=\"designstudio.footprint/1\" ds:component=\"\"\n";
    svg += QString("     width=\"%1mm\" height=\"%2mm\" viewBox=\"%3 %4 %5 %6\">\n")
               .arg(vbw, 0, 'f', 4).arg(vbh, 0, 'f', 4)
               .arg(vbx, 0, 'f', 4).arg(vby, 0, 'f', 4).arg(vbw, 0, 'f', 4).arg(vbh, 0, 'f', 4);
    svg += "  <g inkscape:groupmode=\"layer\" inkscape:label=\"copper-top\" ds:layer=\"copper-top\">\n";
    svg += pads;
    svg += "  </g>\n";
    svg += "  <g inkscape:groupmode=\"layer\" inkscape:label=\"silk-top\" ds:layer=\"silk-top\">\n";
    svg += silk;
    svg += "  </g>\n</svg>\n";
    return svg;
}

bool FpCanvas::loadSvg(const QString& svg, QString* err) {
    clearFootprint();
    QXmlStreamReader xml(svg);
    xml.setNamespaceProcessing(false);   // read qualified ds:/inkscape: names directly

    auto num = [](const QStringView& v) { return v.toString().toDouble(); };

    while (!xml.atEnd()) {
        if (xml.readNext() != QXmlStreamReader::StartElement) continue;
        const QString tag = xml.name().toString();
        const auto a = xml.attributes();

        if (tag == "rect") {
            const double x = num(a.value("x")), y = num(a.value("y"));
            const double w = num(a.value("width")), hh = num(a.value("height"));
            const QRectF scene(x, -y - hh, w, hh);   // invert SVG Y-down → scene Y-up
            const bool isPad = a.value("ds:type").toString() == "pad";
            QColor col = isPad ? QColor(0xC8, 0x96, 0x32) : QColor(0x4b, 0x4b, 0x4b);
            auto* r = m_scene->addRect(scene, QPen(col, 0), isPad ? QBrush(col) : QBrush(Qt::NoBrush));
            r->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
            if (isPad) { r->setData(0, "pad"); r->setData(1, a.value("ds:pad").toString());
                         r->setData(2, a.value("ds:net").toString()); }
        } else if (tag == "ellipse" || tag == "circle") {
            const double cx = num(a.value("cx")), cy = num(a.value("cy"));
            const double rx = tag == "circle" ? num(a.value("r")) : num(a.value("rx"));
            const double ry = tag == "circle" ? num(a.value("r")) : num(a.value("ry"));
            auto* el = m_scene->addEllipse(QRectF(cx - rx, -cy - ry, 2 * rx, 2 * ry),
                                           QPen(QColor(0x4b, 0x4b, 0x4b), 0));
            el->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
        } else if (tag == "path") {
            auto* p = new FpPathItem();
            m_scene->addItem(p);
            QString d = a.value("d").toString();
            d.replace(',', ' ');
            const QStringList t = d.split(QRegularExpression("\\s+"), Qt::SkipEmptyParts);
            QChar cmd;
            for (int i = 0; i < t.size(); ) {
                if (t[i].size() == 1 && t[i][0].isLetter()) {
                    cmd = t[i][0]; ++i;
                    if (cmd == 'Z' || cmd == 'z') p->closed = true;
                    continue;
                }
                if (cmd == 'M' || cmd == 'L') {
                    if (i + 1 >= t.size()) break;
                    FpNode n; n.pt = QPointF(t[i].toDouble(), -t[i + 1].toDouble());
                    n.cIn = n.pt; n.cOut = n.pt; i += 2;
                    if (!p->nodes.isEmpty()) p->nodes.last().cOut = p->nodes.last().pt;
                    p->nodes.append(n);
                } else if (cmd == 'C') {
                    if (i + 5 >= t.size()) break;
                    const QPointF c1(t[i].toDouble(),   -t[i + 1].toDouble());
                    const QPointF c2(t[i + 2].toDouble(), -t[i + 3].toDouble());
                    const QPointF pt(t[i + 4].toDouble(), -t[i + 5].toDouble());
                    i += 6;
                    if (!p->nodes.isEmpty()) p->nodes.last().cOut = c1;
                    FpNode n; n.pt = pt; n.cIn = c2; n.cOut = pt; n.smooth = true;
                    p->nodes.append(n);
                } else { ++i; }
            }
            p->rebuild();
            p->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
            p->setData(0, "path");
        }
    }
    if (xml.hasError()) { if (err) *err = xml.errorString(); return false; }
    viewport()->update();
    return true;
}

// ── Generic SVG → editable-items helpers ─────────────────────────────────────
// Parse an SVG transform list ("matrix(...) translate(...) rotate(...) …") into a
// single QTransform mapping element-local → parent coords (row-vector, so the
// rightmost/innermost transform is applied first: m = s * m).
static QTransform parseSvgTransform(const QString& t) {
    QTransform m;
    if (t.trimmed().isEmpty()) return m;
    static const QRegularExpression re(R"RX(([a-zA-Z]+)\s*\(([^)]*)\))RX");
    auto it = re.globalMatch(t);
    while (it.hasNext()) {
        const auto mm = it.next();
        const QString fn = mm.captured(1);
        const QStringList a = mm.captured(2).split(QRegularExpression("[\\s,]+"), Qt::SkipEmptyParts);
        auto num = [&](int i, double def = 0.0) { return i < a.size() ? a[i].toDouble() : def; };
        QTransform s;
        if      (fn == "matrix" && a.size() == 6) s = QTransform(num(0), num(1), num(2), num(3), num(4), num(5));
        else if (fn == "translate")               s = QTransform::fromTranslate(num(0), num(1));
        else if (fn == "scale")                   s = QTransform::fromScale(num(0, 1), a.size() > 1 ? num(1) : num(0, 1));
        else if (fn == "rotate") {
            if (a.size() >= 3) { s.translate(num(1), num(2)); s.rotate(num(0)); s.translate(-num(1), -num(2)); }
            else s.rotate(num(0));
        }
        else if (fn == "skewX") s.shear(std::tan(qDegreesToRadians(num(0))), 0);
        else if (fn == "skewY") s.shear(0, std::tan(qDegreesToRadians(num(0))));
        m = s * m;
    }
    return m;
}

// Parse SVG path data into a QPainterPath (local coords). Supports M L H V C S Q
// T Z (abs + rel). Arcs (A) are approximated by a line to the endpoint.
static QPainterPath parsePathData(const QString& d) {
    QPainterPath path;
    const int n = d.size();
    int i = 0;
    QChar cmd, lastCmd;
    QPointF cur(0, 0), sub(0, 0), lastCtrl(0, 0);
    auto isWs   = [](QChar c) { return c == ' ' || c == ',' || c == '\t' || c == '\n' || c == '\r'; };
    auto skipWs = [&] { while (i < n && isWs(d[i])) i++; };
    auto isCmd  = [](QChar c) { return QStringLiteral("MmLlHhVvCcSsQqTtAaZz").contains(c); };
    auto rdNum  = [&]() -> double {
        skipWs(); const int s = i;
        if (i < n && (d[i] == '+' || d[i] == '-')) i++;
        while (i < n && (d[i].isDigit() || d[i] == '.')) i++;
        if (i < n && (d[i] == 'e' || d[i] == 'E')) { i++; if (i < n && (d[i] == '+' || d[i] == '-')) i++; while (i < n && d[i].isDigit()) i++; }
        return d.mid(s, i - s).toDouble();
    };
    auto rdPt = [&]() -> QPointF { const double x = rdNum(); const double y = rdNum(); return QPointF(x, y); };

    while (i < n) {
        skipWs();
        if (i >= n) break;
        if (isCmd(d[i])) { cmd = d[i]; i++; }
        else if (cmd == 'M') cmd = 'L';
        else if (cmd == 'm') cmd = 'l';
        const bool rel = cmd.isLower();
        switch (cmd.toUpper().unicode()) {
        case 'M': { QPointF p = rdPt(); if (rel) p += cur; cur = sub = p; path.moveTo(p); break; }
        case 'L': { QPointF p = rdPt(); if (rel) p += cur; cur = p; path.lineTo(p); break; }
        case 'H': { double x = rdNum(); QPointF p(rel ? cur.x() + x : x, cur.y()); cur = p; path.lineTo(p); break; }
        case 'V': { double y = rdNum(); QPointF p(cur.x(), rel ? cur.y() + y : y); cur = p; path.lineTo(p); break; }
        case 'C': { QPointF c1 = rdPt(), c2 = rdPt(), e = rdPt(); if (rel) { c1 += cur; c2 += cur; e += cur; }
                    path.cubicTo(c1, c2, e); lastCtrl = c2; cur = e; break; }
        case 'S': { QPointF c2 = rdPt(), e = rdPt(); if (rel) { c2 += cur; e += cur; }
                    const QChar lu = lastCmd.toUpper();
                    QPointF c1 = (lu == 'C' || lu == 'S') ? (2 * cur - lastCtrl) : cur;
                    path.cubicTo(c1, c2, e); lastCtrl = c2; cur = e; break; }
        case 'Q': { QPointF c = rdPt(), e = rdPt(); if (rel) { c += cur; e += cur; }
                    path.quadTo(c, e); lastCtrl = c; cur = e; break; }
        case 'T': { QPointF e = rdPt(); if (rel) e += cur;
                    const QChar lu = lastCmd.toUpper();
                    QPointF c = (lu == 'Q' || lu == 'T') ? (2 * cur - lastCtrl) : cur;
                    path.quadTo(c, e); lastCtrl = c; cur = e; break; }
        case 'A': { rdNum(); rdNum(); rdNum(); rdNum(); rdNum(); QPointF e = rdPt(); if (rel) e += cur;
                    path.lineTo(e); cur = e; break; }  // arc → line approximation
        case 'Z': { path.closeSubpath(); cur = sub; break; }
        default:  i++; break;
        }
        lastCmd = cmd;
    }
    return path;
}

// Import an SVG as INDIVIDUALLY selectable/movable scene items — one per <path>
// and <text>. Each element's transform is applied, then mapped to scene mm
// (Y-up, centred on the datum). This is what makes the elements pickable/editable
// (vs importSvg's single flat reference image).
bool FpCanvas::importSvgEditable(const QString& path, QString* err) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) { if (err) *err = "cannot open file"; return false; }
    const QByteArray data = f.readAll();
    f.close();

    const QString head = QString::fromUtf8(data.left(8192));
    auto attr = [&](const QString& name) -> QString {
        const QRegularExpression re(name + R"RX(\s*=\s*"([^"]*)")RX");
        const auto m = re.match(head);
        return m.hasMatch() ? m.captured(1) : QString();
    };
    auto lenToMm = [](QString s) -> double {
        s = s.trimmed();
        static const QRegularExpression re(R"(^([0-9.+\-eE]+)\s*([a-z%]*)$)");
        const auto m = re.match(s);
        if (!m.hasMatch()) return 0.0;
        const double v = m.captured(1).toDouble();
        const QString u = m.captured(2).toLower();
        if (u.isEmpty() || u == "px") return v * (25.4 / 96.0);
        if (u == "mm") return v;          if (u == "cm") return v * 10.0;
        if (u == "in") return v * 25.4;   if (u == "pt") return v * (25.4 / 72.0);
        if (u == "pc") return v * (25.4 / 6.0);
        return 0.0;
    };

    double vbX = 0, vbY = 0, vbW = 0, vbH = 0;
    const QStringList vb = attr("viewBox").split(QRegularExpression("[\\s,]+"), Qt::SkipEmptyParts);
    if (vb.size() == 4) { vbX = vb[0].toDouble(); vbY = vb[1].toDouble(); vbW = vb[2].toDouble(); vbH = vb[3].toDouble(); }
    double Wmm = lenToMm(attr("width")), Hmm = lenToMm(attr("height"));
    if ((Wmm <= 0 || Hmm <= 0) && vbW > 0 && vbH > 0) { Wmm = vbW * 25.4 / 96.0; Hmm = vbH * 25.4 / 96.0; }
    if (vbW <= 0 || vbH <= 0) { vbW = (Wmm > 0 ? Wmm : 100) * 96.0 / 25.4; vbH = (Hmm > 0 ? Hmm : 100) * 96.0 / 25.4; vbX = vbY = 0; }
    if (Wmm <= 0 || Hmm <= 0) { Wmm = vbW * 25.4 / 96.0; Hmm = vbH * 25.4 / 96.0; }
    const double kx = Wmm / vbW, ky = Hmm / vbH;
    const QTransform Gmat(kx, 0, 0, -ky, -vbX * kx - Wmm / 2.0, vbY * ky + Hmm / 2.0);

    QXmlStreamReader xml(data);
    xml.setNamespaceProcessing(false);
    int nShapes = 0, nText = 0;
    const QPen pen(QColor(0xC8, 0xC8, 0xC8), 0.12);   // visible + clickable on the dark canvas

    while (!xml.atEnd()) {
        if (xml.readNext() != QXmlStreamReader::StartElement) continue;
        const QString tag = xml.name().toString();
        const auto a = xml.attributes();

        if (tag == "path") {
            const QPainterPath lp = parsePathData(a.value("d").toString());
            if (lp.isEmpty()) continue;
            const QTransform Te = parseSvgTransform(a.value("transform").toString());
            auto* it = new FpHitPathItem();
            it->setPath((Te * Gmat).map(lp));
            it->setPen(pen);
            it->setData(0, "import-path");
            m_scene->addItem(it);
            ++nShapes;
        } else if (tag == "rect") {
            const double x = a.value("x").toString().toDouble(), y = a.value("y").toString().toDouble();
            const double w = a.value("width").toString().toDouble(), h = a.value("height").toString().toDouble();
            if (w <= 0 || h <= 0) continue;
            QPainterPath lp; lp.addRect(x, y, w, h);
            const QTransform Te = parseSvgTransform(a.value("transform").toString());
            auto* it = new FpHitPathItem();
            it->setPath((Te * Gmat).map(lp));
            it->setPen(pen);
            it->setData(0, "import-path");
            m_scene->addItem(it);
            ++nShapes;
        } else if (tag == "text") {
            const QTransform Te = parseSvgTransform(a.value("transform").toString());
            double fs = a.value("font-size").toString().toDouble(); if (fs <= 0) fs = 3.0;
            QString txt; double tx = 0, ty = 0; bool got = false;
            while (!xml.atEnd() && !(xml.tokenType() == QXmlStreamReader::EndElement && xml.name() == QStringLiteral("text"))) {
                xml.readNext();
                if (xml.tokenType() == QXmlStreamReader::StartElement && xml.name() == QStringLiteral("tspan")) {
                    const auto ta = xml.attributes();
                    const QString xs = ta.value("x").toString().split(QRegularExpression("[\\s,]+"), Qt::SkipEmptyParts).value(0);
                    const QString ys = ta.value("y").toString().split(QRegularExpression("[\\s,]+"), Qt::SkipEmptyParts).value(0);
                    if (!xs.isEmpty()) tx = xs.toDouble();
                    if (!ys.isEmpty()) ty = ys.toDouble();
                }
                if (xml.tokenType() == QXmlStreamReader::Characters && !xml.isWhitespace()) { txt += xml.text().toString(); got = true; }
            }
            txt = txt.trimmed();
            if (!got || txt.isEmpty()) continue;
            const QTransform M = Te * Gmat;                 // text-local → scene mm
            const QPointF sc = M.map(QPointF(tx, ty));      // mapped anchor
            auto* t = m_scene->addSimpleText(txt);
            t->setBrush(QColor(0xD6, 0xD6, 0xD6));
            QFont fnt = t->font(); fnt.setPointSizeF(10.0); t->setFont(fnt);
            const double hpx = qMax(1.0, t->boundingRect().height());
            const double scale = (fs * kx) / hpx;           // glyph height ≈ fontSize·k mm
            // Honour the element's orientation (rotation/flip) from its transform
            // instead of forcing upright — keeps vertical dim labels vertical so
            // they don't collapse onto one another. Decompose M's linear part into
            // unit axis directions, then re-apply the glyph scale.
            double ax = M.m11(), ay = M.m12(), bx = M.m21(), by = M.m22();
            double alen = std::hypot(ax, ay); if (alen < 1e-9) alen = 1.0;
            double blen = std::hypot(bx, by); if (blen < 1e-9) blen = 1.0;
            const double ux = ax / alen, uy = ay / alen;    // text-x axis → scene
            const double vx = bx / blen, vy = by / blen;    // text-y axis → scene
            t->setTransform(QTransform(scale * ux, scale * uy,
                                       scale * vx, scale * vy,
                                       sc.x(), sc.y()));
            t->setFlags(QGraphicsItem::ItemIsSelectable | QGraphicsItem::ItemIsMovable);
            t->setData(0, "import-text");
            ++nText;
        }
    }
    if (nShapes == 0 && nText == 0) { if (err) *err = "no drawable elements found"; return false; }

    // Record placement + user-unit space so the Downsizer can map a selection
    // (item bbox or rubber-band) back to viewBox coords — same as importSvg.
    m_impWmm = Wmm; m_impHmm = Hmm;
    m_impVB = QRectF(vbX, vbY, vbW, vbH);
    m_hasImport = true;
    m_rubberScene = QRectF();

    emit status(QString("Imported %1 paths + %2 text as editable elements").arg(nShapes).arg(nText));
    return true;
}

// Import ANY .svg/.svgz onto the grid as a scaled reference layer. Qt's qsvg
// image plugin renders it via QImageReader (no QtSvg dependency needed); we
// place it sized to the SVG's physical extent (mm), Y-flipped to the canvas'
// Y-up convention, behind the editable geometry so pads can be traced over it.
bool FpCanvas::importSvg(const QString& path, QString* err) {
    // 1) Read the <svg …> header to recover physical size (width/height/viewBox).
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) { if (err) *err = "cannot open file"; return false; }
    const QString head = QString::fromUtf8(f.read(8192));
    f.close();

    auto lenToMm = [](QString s) -> double {              // CSS length → mm (0 = unknown)
        s = s.trimmed();
        static const QRegularExpression re(R"(^([0-9.+\-eE]+)\s*([a-z%]*)$)");
        const auto m = re.match(s);
        if (!m.hasMatch()) return 0.0;
        const double v = m.captured(1).toDouble();
        const QString u = m.captured(2).toLower();
        if (u.isEmpty() || u == "px") return v * (25.4 / 96.0);   // SVG user unit @96dpi
        if (u == "mm") return v;
        if (u == "cm") return v * 10.0;
        if (u == "in") return v * 25.4;
        if (u == "pt") return v * (25.4 / 72.0);
        if (u == "pc") return v * (25.4 / 6.0);
        return 0.0;                                              // % or unknown
    };
    auto attr = [&](const QString& name) -> QString {
        const QRegularExpression re(name + R"RX(\s*=\s*"([^"]*)")RX");
        const auto m = re.match(head);
        return m.hasMatch() ? m.captured(1) : QString();
    };

    double Wmm = lenToMm(attr("width"));
    double Hmm = lenToMm(attr("height"));
    const QStringList vb = attr("viewBox").split(QRegularExpression("[\\s,]+"), Qt::SkipEmptyParts);
    if ((Wmm <= 0 || Hmm <= 0) && vb.size() == 4) {            // fall back to viewBox extent
        Wmm = vb[2].toDouble() * (25.4 / 96.0);
        Hmm = vb[3].toDouble() * (25.4 / 96.0);
    }
    if (Wmm <= 0 || Hmm <= 0) { Wmm = Hmm = 25.0; }            // last-resort default

    // 2) Build the item: true vector when SvgWidgets is available (crisp at any
    //    zoom), else a crisp raster via the qsvg image plugin. Both end up as a
    //    QGraphicsItem with content spanning local coords [0,lw]×[0,lh].
    QGraphicsItem* item = nullptr;
    double lw = 0, lh = 0;
    const char* kind = "raster";

#ifdef HAVE_QTSVGWIDGETS
    auto* svg = new QGraphicsSvgItem(path);
    if (!svg->renderer() || !svg->renderer()->isValid() || svg->boundingRect().isEmpty()) {
        delete svg;
        if (err) *err = "invalid or empty SVG";
        return false;
    }
    m_scene->addItem(svg);
    lw = svg->boundingRect().width();
    lh = svg->boundingRect().height();
    item = svg;
    kind = "vector";
#else
    const double renderPxPerMm = 40.0;
    const int pw = qBound(16, int(qRound(Wmm * renderPxPerMm)), 4096);
    const int ph = qBound(16, int(qRound(Hmm * renderPxPerMm)), 4096);
    QImageReader reader(path);
    reader.setScaledSize(QSize(pw, ph));
    const QImage img = reader.read();
    if (img.isNull()) { if (err) *err = reader.errorString(); return false; }
    auto* pix = m_scene->addPixmap(QPixmap::fromImage(img));
    pix->setTransformationMode(Qt::SmoothTransformation);
    lw = img.width();
    lh = img.height();
    item = pix;
#endif

    // 3) Scale local→mm, flip to Y-up, centre on the datum, send behind geometry.
    item->setTransform(QTransform(Wmm / lw, 0, 0, -Hmm / lh, 0, 0));
    item->setPos(-Wmm / 2.0, Hmm / 2.0);
    item->setZValue(-100);
    // Non-interactive backdrop: leaving it unselectable/immovable lets a Select
    // rubber-band sweep an AREA across it (for the Downsizer crop) instead of
    // grabbing/moving the image. It is removed programmatically by data(0).
    item->setFlags({});
    item->setData(0, "import");

    // Remember placement + the SVG's user-unit space so a scene-mm selection can
    // be mapped back to viewBox coords for the Python crop.
    m_impWmm = Wmm; m_impHmm = Hmm;
    if (vb.size() == 4)
        m_impVB = QRectF(vb[0].toDouble(), vb[1].toDouble(),
                         vb[2].toDouble(), vb[3].toDouble());
    else
        m_impVB = QRectF(0, 0, attr("width").toDouble(), attr("height").toDouble());
    if (m_impVB.width() <= 0 || m_impVB.height() <= 0)
        m_impVB = QRectF(0, 0, lw, lh);
    m_hasImport = true;
    m_rubberScene = QRectF();

    emit status(QString("Imported SVG (%1)  %2 × %3 mm")
                    .arg(kind).arg(Wmm, 0, 'f', 2).arg(Hmm, 0, 'f', 2));
    return true;
}

// Map the last rubber-band rectangle (scene mm, Y-up, datum-centred) back to the
// imported SVG's own user-unit (viewBox) coordinates — the space the Python
// downsizer crops in. See importSvg for the forward placement.
bool FpCanvas::lastSelectionUserUnits(double& x0, double& y0,
                                      double& x1, double& y1) const {
    if (!m_hasImport || m_impWmm <= 0 || m_impHmm <= 0)
        return false;
    // Crop source: bounding box of the currently-selected import elements
    // (editable mode — you pick the component + its reference dimension), else
    // the last rubber-band drag (flat-reference mode).
    QRectF r;
    const auto sel = m_scene->selectedItems();
    for (auto* it : sel) {
        const QString k = it->data(0).toString();
        if (k != "import-path" && k != "import-text") continue;
        r = r.isNull() ? it->sceneBoundingRect() : r.united(it->sceneBoundingRect());
    }
    if (r.isNull()) r = m_rubberScene;
    if (r.isNull()) return false;
    r = r.normalized();
    auto toUU = [&](const QPointF& s, double& ux, double& uy) {
        const double fx = (s.x() + m_impWmm / 2.0) / m_impWmm;   // 0..1 left→right
        const double fy = (m_impHmm / 2.0 - s.y()) / m_impHmm;   // 0..1 top→bottom (Y-down)
        ux = m_impVB.x() + fx * m_impVB.width();
        uy = m_impVB.y() + fy * m_impVB.height();
    };
    double ax, ay, bx, by;
    toUU(r.topLeft(), ax, ay);
    toUU(r.bottomRight(), bx, by);
    x0 = std::min(ax, bx); x1 = std::max(ax, bx);
    y0 = std::min(ay, by); y1 = std::max(ay, by);
    return true;
}

// Remove the datasheet backdrop (which carries the dimension graph) so only the
// downsized component remains on the grid.
void FpCanvas::removeImportedReference() {
    const auto items = m_scene->items();
    for (auto* it : items) {
        const QString k = it->data(0).toString();
        if (k == "import" || k == "import-path" || k == "import-text") {
            m_scene->removeItem(it);
            delete it;
        }
    }
    m_hasImport = false;
    m_rubberScene = QRectF();
}

// Scale all SELECTED imported elements to real size in place. Each item keeps
// its own vector geometry (QPainterPath / text) — we only adjust its transform —
// so there is no re-parse, no rasterise, no quality loss, and EVERY selected
// element (component copper, reference lines, dimension lines…) is downsized.
int FpCanvas::downsizeSelectedItems(double realMmPerPageMm) {
    if (!m_hasImport || m_impWmm <= 0 || m_impVB.width() <= 0) return 0;
    const double PT2MM = 25.4 / 72.0;                 // page user-unit (pt) → mm
    const double sceneMmPerUU = m_impWmm / m_impVB.width();
    if (sceneMmPerUU <= 0) return 0;
    const double f = realMmPerPageMm * PT2MM / sceneMmPerUU;   // scene-mm → real-mm

    QList<QGraphicsItem*> sel;
    QRectF bb;
    for (auto* it : m_scene->selectedItems()) {
        const QString k = it->data(0).toString();
        if (k != "import-path" && k != "import-text") continue;
        sel << it;
        bb = bb.isNull() ? it->sceneBoundingRect() : bb.united(it->sceneBoundingRect());
    }
    if (sel.isEmpty()) return 0;

    // scene' = f·(scene − centre): true size, recentred on the selection → origin
    const QPointF c = bb.center();
    const QTransform Ttot(f, 0, 0, f, -f * c.x(), -f * c.y());
    for (auto* it : sel) {
        const QTransform st = it->sceneTransform();   // local → scene (now)
        it->setPos(0, 0);
        it->setTransform(st * Ttot);                  // local → scene' (real mm)
        it->setData(0, "footprint");                  // kept geometry, not removable import
    }
    // remove the rest of the datasheet (unselected reference + dimension graph)
    const auto all = m_scene->items();
    for (auto* it : all) {
        const QString k = it->data(0).toString();
        if (k == "import" || k == "import-path" || k == "import-text") {
            m_scene->removeItem(it); delete it;
        }
    }
    m_hasImport = false;
    m_rubberScene = QRectF();
    return sel.size();
}

// Weld selected path elements: chain segments that share endpoints (within
// epsMm) into single polylines, and discard exact duplicates. Reduces a pile of
// individual line segments (component edges, reference lines) into coherent
// single elements. Operates on the current selection; leaves text untouched.
int FpCanvas::mergeSelectedPaths(double epsMm) {
    QList<QGraphicsPathItem*> paths;
    for (auto* it : m_scene->selectedItems())
        if (auto* p = dynamic_cast<QGraphicsPathItem*>(it)) paths << p;
    if (paths.size() < 2) return paths.size();

    // collect every subpath as a polyline in scene coords
    QVector<QVector<QPointF>> polys;
    for (auto* p : paths) {
        const QPainterPath sp = p->mapToScene(p->path());
        QVector<QPointF> cur;
        for (int i = 0; i < sp.elementCount(); ++i) {
            const auto e = sp.elementAt(i);
            if (e.type == QPainterPath::MoveToElement) {
                if (cur.size() > 1) polys << cur;
                cur.clear();
            }
            cur << QPointF(e.x, e.y);
        }
        if (cur.size() > 1) polys << cur;
    }
    auto near = [epsMm](const QPointF& a, const QPointF& b) {
        return std::hypot(a.x() - b.x(), a.y() - b.y()) <= epsMm;
    };
    // drop exact-duplicate polylines (same endpoints, same length)
    QVector<QVector<QPointF>> uniq;
    for (const auto& pl : polys) {
        bool dup = false;
        for (const auto& q : uniq)
            if (q.size() == pl.size() && near(q.front(), pl.front()) && near(q.back(), pl.back()))
                { dup = true; break; }
        if (!dup) uniq << pl;
    }
    // greedily chain polylines whose ends meet
    QVector<QVector<QPointF>> chains;
    QVector<bool> used(uniq.size(), false);
    for (int i = 0; i < uniq.size(); ++i) {
        if (used[i]) continue;
        QVector<QPointF> ch = uniq[i]; used[i] = true;
        bool grew = true;
        while (grew) {
            grew = false;
            for (int j = 0; j < uniq.size(); ++j) {
                if (used[j]) continue;
                const auto& s = uniq[j];
                if (near(ch.back(), s.front()))      { for (int k=1;k<s.size();++k) ch<<s[k]; used[j]=true; grew=true; }
                else if (near(ch.back(), s.back()))  { for (int k=s.size()-2;k>=0;--k) ch<<s[k]; used[j]=true; grew=true; }
                else if (near(ch.front(), s.back())) { QVector<QPointF> n=s; for(int k=1;k<ch.size();++k) n<<ch[k]; ch=n; used[j]=true; grew=true; }
                else if (near(ch.front(), s.front())){ QVector<QPointF> n; for(int k=s.size()-1;k>=0;--k) n<<s[k]; for(int k=1;k<ch.size();++k) n<<ch[k]; ch=n; used[j]=true; grew=true; }
            }
        }
        chains << ch;
    }
    // rebuild: delete the old path items, add one FpHitPathItem per chain
    const QPen pen = paths.first()->pen();
    for (auto* p : paths) { m_scene->removeItem(p); delete p; }
    for (const auto& ch : chains) {
        QPainterPath pp; pp.moveTo(ch.front());
        for (int k = 1; k < ch.size(); ++k) pp.lineTo(ch[k]);
        auto* it = new FpHitPathItem();
        it->setPath(pp);
        it->setPen(pen);
        it->setData(0, "footprint");
        m_scene->addItem(it);
    }
    return chains.size();
}

QJsonArray FpCanvas::padTable() const {
    QJsonArray arr;
    for (auto* it : m_scene->items()) {
        auto* r = dynamic_cast<QGraphicsRectItem*>(it);
        if (!r || r->data(0).toString() != "pad") continue;
        const QRectF b = r->sceneBoundingRect();
        QJsonObject o;
        o["name"]  = r->data(1).toString();
        o["net"]   = r->data(2).toString();
        o["x_mm"]  = b.center().x();
        o["y_mm"]  = b.center().y();
        o["w_mm"]  = b.width();
        o["h_mm"]  = b.height();
        arr.append(o);
    }
    return arr;
}

// ── FootprintLibTab ───────────────────────────────────────────────────────────
FootprintLibTab::FootprintLibTab(ProjectModel* model, QWidget* parent)
    : QWidget(parent), m_model(model) {

    auto* root = new QHBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    m_canvas = new FpCanvas(this);

    // ── Left tool palette (real Inkscape icons) ───────────────────────────────
    auto* tools = new QToolBar(this);
    tools->setOrientation(Qt::Vertical);
    tools->setMovable(false);
    tools->setIconSize(QSize(22, 22));
    auto* group = new QActionGroup(this);
    group->setExclusive(true);

    auto addTool = [&](const QString& icon, const QString& tip, FpCanvas::Tool t, bool on = false) {
        auto* a = tools->addAction(QIcon(":/fp/icons/" + icon), QString());
        a->setToolTip(tip);
        a->setCheckable(true);
        a->setChecked(on);
        group->addAction(a);
        connect(a, &QAction::triggered, this, [this, t] { m_canvas->setTool(t); });
        return a;
    };

    addTool("tool-select.svg",  "Select / transform", FpCanvas::Select, true);
    addTool("tool-node.svg",    "Edit nodes (drag anchors & bezier handles)", FpCanvas::Node);
    tools->addSeparator();
    addTool("tool-pad.svg",     "Place pad (0.30×0.85 mm)", FpCanvas::Pad);
    addTool("tool-rect.svg",    "Rectangle", FpCanvas::RectShape);
    addTool("tool-ellipse.svg", "Ellipse / circle", FpCanvas::Ellipse);
    addTool("tool-polygon.svg", "Polygon / region", FpCanvas::Polygon);
    addTool("tool-line.svg",    "Line", FpCanvas::Line);
    addTool("tool-pen.svg",     "Pen — bezier path (click=corner, drag=smooth, Enter/dbl-click=finish)", FpCanvas::Pen);
    addTool("tool-text.svg",    "Text", FpCanvas::Text);
    tools->addSeparator();
    addTool("tool-measure.svg", "Measure", FpCanvas::Measure);
    auto* fitAct = tools->addAction(QIcon(":/fp/icons/tool-fit.svg"), QString());
    fitAct->setToolTip("Zoom to fit");
    connect(fitAct, &QAction::triggered, this, [this] { m_canvas->fit(); });

    tools->addSeparator();
    auto* sizeAct = tools->addAction(QIcon(":/fp/icons/tool-rect.svg"), QString());
    sizeAct->setToolTip("Tap–spread–tap sizing  (Rect / Ellipse / Line):\n"
                        "tap a corner, move to spread, tap to finish (Esc cancels).\n"
                        "Off = fixed-size placement on a single tap.");
    sizeAct->setCheckable(true);
    sizeAct->setChecked(true);
    connect(sizeAct, &QAction::toggled, this, [this](bool on) { m_canvas->setRubberSizing(on); });

    root->addWidget(tools);

    // ── Centre: a stack of [native editor] and [embedded real Inkscape] ───────
    m_nativePage = new QWidget(this);
    auto* centre = new QVBoxLayout(m_nativePage);
    centre->setContentsMargins(0, 0, 0, 0);
    centre->setSpacing(0);

    auto* xform = new QToolBar(this);
    auto mkSpin = [&](const QString& lbl) {
        xform->addWidget(new QLabel("  " + lbl + " "));
        auto* s = new QDoubleSpinBox();
        s->setRange(-1000, 1000); s->setDecimals(3); s->setSuffix(" mm");
        s->setMaximumWidth(110);
        xform->addWidget(s);
        return s;
    };
    m_x = mkSpin("X"); m_y = mkSpin("Y"); m_w = mkSpin("W"); m_h = mkSpin("H");
    centre->addWidget(xform);
    centre->addWidget(m_canvas, 1);

    auto* statusBar = new QHBoxLayout();
    statusBar->setContentsMargins(6, 2, 6, 2);
    auto* zoomLabel = new QLabel("100%");
    m_coord = new QLabel("X 0.000  Y 0.000 mm");
    statusBar->addWidget(zoomLabel);
    statusBar->addStretch();
    statusBar->addWidget(m_coord);
    centre->addLayout(statusBar);

    m_inkscape = new InkscapeHost(this);
    m_stack = new QStackedWidget(this);
    m_stack->addWidget(m_nativePage);   // index 0
    m_stack->addWidget(m_inkscape);     // index 1
    root->addWidget(m_stack, 1);
    connect(m_inkscape, &InkscapeHost::status, this, &FootprintLibTab::statusMessage);
    connect(m_inkscape, &InkscapeHost::failed, this, [this](const QString& why) {
        emit statusMessage("Inkscape embed failed: " + why);
    });

    // ── Right: file (lib) + properties + agent hook ───────────────────────────
    auto* right = new QVBoxLayout();
    right->setContentsMargins(6, 6, 6, 6);

    auto* fileBox = new QGroupBox("Footprint");
    auto* fileLay = new QVBoxLayout(fileBox);
    m_name = new QLineEdit();
    m_name->setPlaceholderText("name (e.g. USBC-24P-RA-SMT)");
    auto* saveBtn   = new QPushButton("Save  (.fp.svg + .props.json)");
    auto* loadBtn   = new QPushButton("Load .fp.svg…");
    auto* importBtn = new QPushButton("Import SVG…");
    auto* inkBtn    = new QPushButton("Edit in Inkscape  ▸");
    auto* backBtn = new QPushButton("◂  Return to native editor");
    backBtn->setVisible(false);
    fileLay->addWidget(m_name);
    fileLay->addWidget(saveBtn);
    fileLay->addWidget(loadBtn);
    fileLay->addWidget(importBtn);
    fileLay->addWidget(inkBtn);
    fileLay->addWidget(backBtn);
    right->addWidget(fileBox);

    auto* propBox = new QGroupBox("Object");
    auto* propForm = new QFormLayout(propBox);
    auto* wRO = new QLabel("—"); auto* hRO = new QLabel("—");
    propForm->addRow("Width",  wRO);
    propForm->addRow("Height", hRO);
    right->addWidget(propBox);

    auto* dimBox = new QGroupBox("Dimension Recognition");
    auto* dimLay = new QVBoxLayout(dimBox);
    auto* detectBtn = new QPushButton("Detect Dimensions  (auto)");
    auto* extractGraphBtn = new QPushButton("Extract SVG Dimension Graph");
    auto* downsizeBtn = new QPushButton("Downsize selection → real size");
    downsizeBtn->setToolTip("Select the elements (component + a reference "
                            "dimension) with the Select tool, then click: EVERY "
                            "selected element is scaled to true mm at full vector "
                            "quality and the rest of the datasheet is removed.");
    auto* mergeBtn = new QPushButton("Merge selected paths");
    mergeBtn->setToolTip("Weld selected path segments that share endpoints into "
                         "single polylines and drop exact duplicates.");
    auto* clearDimBtn = new QPushButton("Clear dimension overlay");
    dimLay->addWidget(detectBtn);
    dimLay->addWidget(extractGraphBtn);
    dimLay->addWidget(downsizeBtn);
    dimLay->addWidget(mergeBtn);
    dimLay->addWidget(clearDimBtn);
    right->addWidget(dimBox);

    auto* agentBox = new QGroupBox("AI / MCP");
    auto* agentLay = new QVBoxLayout(agentBox);
    auto* prompt = new QLineEdit();
    prompt->setPlaceholderText("Ask the agent (e.g. \"generate 24-pad USB-C array\")…");
    auto* askBtn   = new QPushButton("Ask Agent");
    auto* solveBtn = new QPushButton("Solve from datasheet SVG…");
    agentLay->addWidget(prompt);
    agentLay->addWidget(askBtn);
    agentLay->addWidget(solveBtn);
    right->addWidget(agentBox);
    right->addStretch();

    auto* rightW = new QWidget();
    rightW->setLayout(right);
    rightW->setMinimumWidth(240);
    rightW->setMaximumWidth(300);
    root->addWidget(rightW);

    // ── Wiring ────────────────────────────────────────────────────────────────
    connect(m_canvas, &FpCanvas::cursorMm, this, [this](double x, double y) {
        m_coord->setText(QString("X %1  Y %2 mm").arg(x, 0, 'f', 3).arg(y, 0, 'f', 3));
    });
    connect(m_canvas, &FpCanvas::zoomChanged, this, [zoomLabel](double pc) {
        zoomLabel->setText(QString("%1%").arg(pc, 0, 'f', 0));
    });
    connect(m_canvas, &FpCanvas::status, this, &FootprintLibTab::statusMessage);
    connect(m_canvas, &FpCanvas::selectionGeom, this,
            [this, wRO, hRO](double x, double y, double w, double h, bool has) {
        if (has) {
            m_x->setValue(x); m_y->setValue(y); m_w->setValue(w); m_h->setValue(h);
            wRO->setText(QString::number(w, 'f', 3) + " mm");
            hRO->setText(QString::number(h, 'f', 3) + " mm");
        } else { wRO->setText("—"); hRO->setText("—"); }
    });
    connect(saveBtn,   &QPushButton::clicked, this, &FootprintLibTab::onSave);
    connect(loadBtn,   &QPushButton::clicked, this, &FootprintLibTab::onLoad);
    connect(importBtn, &QPushButton::clicked, this, &FootprintLibTab::onImportSvg);
    connect(detectBtn,  &QPushButton::clicked, this, &FootprintLibTab::onDetectDimensions);
    connect(extractGraphBtn, &QPushButton::clicked, this, &FootprintLibTab::onExtractSvgDimensionGraph);
    connect(downsizeBtn, &QPushButton::clicked, this, &FootprintLibTab::onDownsizeSelection);
    connect(mergeBtn, &QPushButton::clicked, this, [this] {
        const int n = m_canvas->mergeSelectedPaths();
        emit statusMessage(n > 0 ? QString("Merged into %1 path element(s).").arg(n)
                                 : "Select 2+ path elements to merge.");
    });
    connect(clearDimBtn,&QPushButton::clicked, this, [this] { m_canvas->clearDimAnnotations(); });
    connect(inkBtn,  &QPushButton::clicked, this, [this, inkBtn, backBtn] {
        onEditInInkscape();
        if (m_stack->currentWidget() == m_inkscape) {
            inkBtn->setVisible(false);
            backBtn->setVisible(true);
        }
    });
    connect(backBtn, &QPushButton::clicked, this, [this, inkBtn, backBtn] {
        onReturnFromInkscape();
        inkBtn->setVisible(true);
        backBtn->setVisible(false);
    });
    connect(askBtn, &QPushButton::clicked, this, [this, prompt] {
        const QString t = prompt->text().trimmed();
        if (!t.isEmpty()) emit agentRequested(t);
    });
    connect(prompt, &QLineEdit::returnPressed, askBtn, &QPushButton::click);
    connect(solveBtn, &QPushButton::clicked, this, [this] {
        emit agentRequested("extract footprint from datasheet SVG");
    });
}

QString FootprintLibTab::libDir() const {
    QString d = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation) + "/footprints";
    QDir().mkpath(d);
    return d;
}

void FootprintLibTab::onSave() {
    QString base = m_name->text().trimmed();
    if (base.isEmpty()) base = "untitled";
    const QString suggested = libDir() + "/" + base + ".fp.svg";
    QString path = QFileDialog::getSaveFileName(this, "Save footprint geometry",
                                                suggested, "Footprint SVG (*.fp.svg *.svg)");
    if (path.isEmpty()) return;
    if (!path.endsWith(".svg")) path += ".fp.svg";

    QFileInfo fi(path);
    base = fi.fileName();
    base.remove(QRegularExpression("\\.fp\\.svg$|\\.svg$"));
    const QString propsPath = fi.absolutePath() + "/" + base + ".props.json";

    // 1) geometry SVG (with back-ref to the props file)
    QString svg = m_canvas->toSvg();
    svg.replace("ds:component=\"\"", QString("ds:component=\"%1.props.json\"").arg(base));
    QFile sf(path);
    if (!sf.open(QIODevice::WriteOnly | QIODevice::Text)) {
        QMessageBox::warning(this, "Save", "Could not write " + path); return;
    }
    sf.write(svg.toUtf8()); sf.close();

    // 2) properties hub JSON (datasheet info), aligned to component/2
    QJsonObject props;
    props["schema"]        = "design-studio.component/2";
    props["ref"]           = base;
    props["name"]          = base;
    props["mpn"]           = "";
    props["manufacturer"]  = "";
    props["description"]   = "";
    props["package"]       = "";
    props["datasheet"]     = "";
    props["footprint_svg"] = base + ".fp.svg";
    props["pads"]          = m_canvas->padTable();
    props["properties"]    = QJsonObject();   // free-form datasheet hub (ratings, thermal…)
    QFile pf(propsPath);
    if (pf.open(QIODevice::WriteOnly | QIODevice::Text)) {
        pf.write(QJsonDocument(props).toJson(QJsonDocument::Indented)); pf.close();
    }

    m_name->setText(base);
    emit statusMessage(QString("Saved %1.fp.svg + %1.props.json").arg(base));
}

void FootprintLibTab::onLoad() {
    const QString path = QFileDialog::getOpenFileName(this, "Load footprint",
                                                      libDir(), "Footprint SVG (*.fp.svg *.svg)");
    if (path.isEmpty()) return;
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) {
        QMessageBox::warning(this, "Load", "Could not read " + path); return;
    }
    const QString svg = QString::fromUtf8(f.readAll());
    f.close();
    QString err;
    if (!m_canvas->loadSvg(svg, &err)) {
        QMessageBox::warning(this, "Load", "SVG parse error: " + err); return;
    }
    QString base = QFileInfo(path).fileName();
    base.remove(QRegularExpression("\\.fp\\.svg$|\\.svg$"));
    m_name->setText(base);
    m_canvas->fit();
    emit statusMessage("Loaded " + base);
}

// Import a plain .svg/.svgz (datasheet drawing, Inkscape/Illustrator art) onto
// the grid as a scaled reference layer to trace footprint pads over.
void FootprintLibTab::onImportSvg() {
    const QString path = QFileDialog::getOpenFileName(
        this, "Import SVG onto grid", libDir(), "SVG (*.svg *.svgz)");
    if (path.isEmpty()) return;

    QMessageBox box(this);
    box.setWindowTitle("Import SVG");
    box.setText("How should this SVG be imported?");
    box.setInformativeText(
        "Editable elements — each path/text becomes a selectable, movable object on the grid.\n"
        "Flat reference — one fixed image to trace over (best for complex/curved art).");
    auto* editBtn = box.addButton("Editable elements", QMessageBox::AcceptRole);
    auto* flatBtn = box.addButton("Flat reference",    QMessageBox::ActionRole);
    box.addButton(QMessageBox::Cancel);
    box.setDefaultButton(editBtn);
    box.exec();
    if (box.clickedButton() == box.button(QMessageBox::Cancel)) return;

    QString err;
    bool ok = false, editable = (box.clickedButton() == editBtn);
    if (editable) {
        ok = m_canvas->importSvgEditable(path, &err);
        if (!ok) {   // fall back to flat if nothing parsed
            ok = m_canvas->importSvg(path, &err);
            editable = false;
        }
    } else {
        ok = m_canvas->importSvg(path, &err);
    }
    Q_UNUSED(flatBtn);
    if (!ok) { QMessageBox::warning(this, "Import SVG", "Could not import:\n" + err); return; }
    m_lastSvgPath = path;   // remember for "Detect Dimensions"
    m_canvas->fit();
    emit statusMessage(QString("Imported %1 (%2) — use 'Detect Dimensions' to overlay measurements")
                       .arg(QFileInfo(path).fileName(), editable ? "editable" : "reference"));
}

// Run pad_dimensioner.py on the last imported SVG: associate every dimension's
// extension lines with the copper feature edges, overlay the drawing views, and
// render the merged dimensioned model on the canvas.
void FootprintLibTab::onDetectDimensions() {
    QString svgPath = m_lastSvgPath;
    if (svgPath.isEmpty()) {
        svgPath = QFileDialog::getOpenFileName(
            this, "Choose datasheet SVG for dimension extraction", libDir(),
            "SVG (*.svg *.svgz)");
        if (svgPath.isEmpty()) return;
    }

    // Locate the Python pipeline without relying on mutable repository data.
    const QString toolsRoot = qEnvironmentVariable("DESIGNSTUDIO_TOOLS_ROOT");
    QStringList candidates = {
        QFileInfo(svgPath).absoluteDir().filePath("pad_dimensioner.py"),
        QCoreApplication::applicationDirPath() + "/pad_dimensioner.py",
        QDir::current().filePath("tools/datasheet_geometry/pad_dimensioner.py"),
        toolsRoot.isEmpty() ? QString() : QDir(toolsRoot).filePath("datasheet_geometry/pad_dimensioner.py"),
    };
    QString script;
    for (const auto& c : candidates) { if (QFileInfo::exists(c)) { script = c; break; } }
    if (script.isEmpty()) {
        QMessageBox::warning(this, "Detect Dimensions",
            "Could not find pad_dimensioner.py.\n"
            "Place it next to the SVG, install it with the application, or set "
            "DESIGNSTUDIO_TOOLS_ROOT.");
        return;
    }

    emit statusMessage("Detecting dimensions in " + QFileInfo(svgPath).fileName() + "…");

    const QString jsonPath = QDir(generatedArtifactDir()).filePath(
        QFileInfo(svgPath).completeBaseName() + ".pads.json");

    QProcess proc;
    proc.setProgram("python3");
    proc.setArguments({ script, svgPath, "--output", jsonPath });
    proc.start();
    if (!proc.waitForFinished(30000)) {
        QMessageBox::warning(this, "Detect Dimensions", "pad_dimensioner.py timed out.");
        return;
    }
    QFile jf(jsonPath);
    if (!jf.open(QIODevice::ReadOnly)) {
        const QString err = QString::fromUtf8(proc.readAllStandardError()).trimmed();
        QMessageBox::warning(this, "Detect Dimensions",
            "Could not read " + jsonPath + "\n\nPipeline output:\n" + err);
        return;
    }
    const QJsonObject root = QJsonDocument::fromJson(jf.readAll()).object();
    jf.close();

    m_canvas->showPadDimensions(root);

    const QJsonObject ext = root["extent_mm"].toObject();
    emit statusMessage(QString("Dimensioned: %1 dims, extent %2 × %3 mm  (scale=%4)")
                       .arg(root["n_dims_matched"].toInt())
                       .arg(ext["width"].toDouble(), 0, 'f', 2)
                       .arg(ext["height"].toDouble(), 0, 'f', 2)
                       .arg(root["scale"].toDouble(), 0, 'f', 5));
}

void FootprintLibTab::onExtractSvgDimensionGraph() {
    QString svgPath = m_lastSvgPath;
    if (svgPath.isEmpty()) {
        svgPath = QFileDialog::getOpenFileName(
            this, "Choose layout SVG for graph extraction", libDir(),
            "SVG (*.svg *.svgz)");
        if (svgPath.isEmpty()) return;
    }

    const QString toolsRoot = qEnvironmentVariable("DESIGNSTUDIO_TOOLS_ROOT");
    QStringList candidates = {
        QFileInfo(svgPath).absoluteDir().filePath("svg_dimension_graph.py"),
        QCoreApplication::applicationDirPath() + "/tools/layout_extractor/svg_dimension_graph.py",
        QDir::current().filePath("tools/layout_extractor/svg_dimension_graph.py"),
        toolsRoot.isEmpty() ? QString() : QDir(toolsRoot).filePath("layout_extractor/svg_dimension_graph.py"),
    };
    QString script;
    for (const auto& c : candidates) { if (QFileInfo::exists(c)) { script = c; break; } }
    if (script.isEmpty()) {
        QMessageBox::warning(this, "Extract Dimension Graph",
            "Could not find svg_dimension_graph.py.");
        return;
    }

    emit statusMessage("Extracting dimension graph from " + QFileInfo(svgPath).fileName() + "…");

    const QString jsonPath = QDir(generatedArtifactDir()).filePath(
        QFileInfo(svgPath).completeBaseName() + ".dimension_graph.json");

    QProcess proc;
    proc.setProgram("python3");
    proc.setArguments({ script, svgPath, "--output", jsonPath });
    proc.start();
    if (!proc.waitForFinished(30000)) {
        QMessageBox::warning(this, "Extract Dimension Graph", "svg_dimension_graph.py timed out.");
        return;
    }
    
    if (proc.exitCode() != 0) {
        const QString err = QString::fromUtf8(proc.readAllStandardError()).trimmed();
        QMessageBox::warning(this, "Extract Dimension Graph", "Execution failed:\n" + err);
        return;
    }

    if (QFileInfo::exists(jsonPath)) {
        QMessageBox::information(this, "Extract Dimension Graph", 
            "Graph extracted successfully to:\n" + jsonPath);
        emit statusMessage("Graph extracted to " + jsonPath);
    } else {
        QMessageBox::warning(this, "Extract Dimension Graph", 
            "Extraction completed but JSON not found at:\n" + jsonPath);
    }
}

// Crop the imported datasheet SVG to the rubber-band selection (one reference
// dimension + the component), run the downsizer on JUST that region, then drop
// the dimension-graph backdrop so only the real-size component remains.
void FootprintLibTab::onDownsizeSelection() {
    if (!m_canvas->hasImportedSvg() || m_lastSvgPath.isEmpty()) {
        QMessageBox::information(this, "Downsize",
            "Import a datasheet SVG first (Import SVG…).");
        return;
    }
    double x0, y0, x1, y1;
    if (!m_canvas->lastSelectionUserUnits(x0, y0, x1, y1)) {
        QMessageBox::information(this, "Downsize",
            "Drag a selection box (Select tool) around one reference dimension "
            "and the component, then click Downsize.");
        return;
    }

    const QString svgPath = m_lastSvgPath;
    const QString toolsRoot = qEnvironmentVariable("DESIGNSTUDIO_TOOLS_ROOT");
    QStringList candidates = {
        QFileInfo(svgPath).absoluteDir().filePath("downsize_component.py"),
        QCoreApplication::applicationDirPath() + "/downsize_component.py",
        QDir::current().filePath("tools/datasheet_geometry/downsize_component.py"),
        toolsRoot.isEmpty() ? QString() : QDir(toolsRoot).filePath("datasheet_geometry/downsize_component.py"),
    };
    QString script;
    for (const auto& c : candidates) { if (QFileInfo::exists(c)) { script = c; break; } }
    if (script.isEmpty()) {
        QMessageBox::warning(this, "Downsize", "Could not find downsize_component.py.");
        return;
    }

    emit statusMessage(QString("Downsizing selection [%1,%2 → %3,%4]…")
                       .arg(x0, 0, 'f', 1).arg(y0, 0, 'f', 1)
                       .arg(x1, 0, 'f', 1).arg(y1, 0, 'f', 1));

    // Ask the pipeline ONLY for the real scale (mm-real per page-mm) of the
    // selection — no regenerated SVG. We then scale the selected canvas elements
    // directly, preserving every element and full vector quality.
    QProcess proc;
    proc.setProgram("python3");
    proc.setArguments({ script, svgPath, "--crop",
                        QString::number(x0, 'f', 3), QString::number(y0, 'f', 3),
                        QString::number(x1, 'f', 3), QString::number(y1, 'f', 3),
                        "--print-scale" });
    proc.start();
    if (!proc.waitForFinished(30000)) {
        QMessageBox::warning(this, "Downsize", "downsize_component.py timed out.");
        return;
    }
    const QString out = QString::fromUtf8(proc.readAllStandardOutput());
    const QRegularExpression re(R"(scale\s+([0-9.eE+\-]+))");
    const auto m = re.match(out);
    if (proc.exitCode() != 0 || !m.hasMatch()) {
        const QString err = QString::fromUtf8(proc.readAllStandardError()).trimmed();
        QMessageBox::warning(this, "Downsize",
            "Could not determine scale — does the selection enclose a dimensioned "
            "arrow + value?\n\nOutput:\n" + err);
        return;
    }
    const double realScale = m.captured(1).toDouble();

    m_canvas->clearDimAnnotations();
    const int n = m_canvas->downsizeSelectedItems(realScale);
    if (n == 0) {
        QMessageBox::information(this, "Downsize",
            "No selectable elements found. Re-import with 'Editable elements' "
            "(Import SVG… → Editable), select the component + its reference "
            "dimension, then Downsize.");
        return;
    }
    m_canvas->fit();
    emit statusMessage(QString("Downsized %1 selected elements to real size "
                               "(scale %2). Use 'Merge selected paths' to weld "
                               "segments.").arg(n).arg(realScale, 0, 'f', 5));
}

// Persist the current geometry to a working .fp.svg, then hand that exact file
// to the embedded real Inkscape and switch the centre stack to it.
void FootprintLibTab::onEditInInkscape() {
    if (!InkscapeHost::available()) {
        QMessageBox::warning(this, "Inkscape",
            "Staged Inkscape not found at:\n" + InkscapeHost::inkscapeRoot() +
            "\n\nSet DESIGNSTUDIO_INKSCAPE_ROOT to the staged tree.");
        return;
    }
    QString base = m_name->text().trimmed();
    if (base.isEmpty()) base = "untitled";
    m_inkFile = libDir() + "/" + base + ".fp.svg";

    QString svg = m_canvas->toSvg();
    svg.replace("ds:component=\"\"", QString("ds:component=\"%1.props.json\"").arg(base));
    QFile f(m_inkFile);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) {
        QMessageBox::warning(this, "Inkscape", "Could not write " + m_inkFile);
        return;
    }
    f.write(svg.toUtf8());
    f.close();

    m_inkscape->openFile(m_inkFile);
    m_stack->setCurrentWidget(m_inkscape);
    emit statusMessage("Editing " + base +
                       " in embedded Inkscape — save there (Ctrl+S), then Return.");
}

// Close the embedded Inkscape, reload whatever it saved back into the native
// canvas. (Best-effort: the canvas parser understands the ds: geometry subset;
// arbitrary Inkscape constructs remain in the .fp.svg on disk regardless.)
void FootprintLibTab::onReturnFromInkscape() {
    m_inkscape->shutdown();
    m_stack->setCurrentWidget(m_nativePage);

    QFile f(m_inkFile);
    if (f.open(QIODevice::ReadOnly | QIODevice::Text)) {
        const QString svg = QString::fromUtf8(f.readAll());
        f.close();
        QString err;
        if (m_canvas->loadSvg(svg, &err)) {
            m_canvas->fit();
            emit statusMessage("Returned — reloaded Inkscape edits.");
        } else {
            emit statusMessage("Returned — could not re-import: " + err);
        }
    }
}
