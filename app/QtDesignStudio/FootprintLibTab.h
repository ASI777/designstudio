// FootprintLibTab.h — an Inkscape-style footprint editor tab for DesignStudio.
// Stage 1: vector canvas (infinite zoom/pan, mm grid, live coordinate readout),
//          left tool palette, right shape/pad properties, agent hook.
// Stage 2: editable bezier paths (FpPathItem), Pen tool, node editor, and
//          JSON footprint library load/save. Geometry ops use lib2geom when
//          available (HAVE_LIB2GEOM), else Qt-native QPainterPath.
#pragma once
#include <QWidget>
#include <QGraphicsView>
#include <QGraphicsPathItem>
#include <QVector>
#include <QPointF>
#include <QRectF>
#include <QPainterPath>
#include <QJsonArray>
#include <QJsonObject>

class QGraphicsScene;
class QPainter;
class QStyleOptionGraphicsItem;
class QGraphicsSceneHoverEvent;
class QDoubleSpinBox;
class QLabel;
class QLineEdit;
class QStackedWidget;
class QWidget;
class InkscapeHost;
class ProjectModel;

// One editable node of a path: an anchor with cubic-bezier control handles.
// A "corner" node has cIn == cOut == pt (zero-length handles → straight edge).
struct FpNode {
    QPointF pt;
    QPointF cIn;
    QPointF cOut;
    bool    smooth = false;
};

// A vector path (silkscreen / courtyard / region outline) with editable nodes.
// Node coordinates are stored in item-local space; rebuild() recomputes the
// QPainterPath. Helpers map to/from scene so editing survives item moves.
class FpPathItem : public QGraphicsPathItem {
public:
    explicit FpPathItem(QGraphicsItem* parent = nullptr);
    QVector<FpNode> nodes;
    bool closed = false;
    void rebuild();
};

// Selectable geometry item with Inkscape-like behaviour: a zoom-constant click
// tolerance (so thin strokes are easy to hit), hover highlight, and a clear
// selection outline. Used for imported SVG paths/rects.
class FpHitPathItem : public QGraphicsPathItem {
public:
    explicit FpHitPathItem(QGraphicsItem* parent = nullptr);
    QRectF       boundingRect() const override;
    QPainterPath shape() const override;
    void         paint(QPainter*, const QStyleOptionGraphicsItem*, QWidget*) override;
protected:
    void hoverEnterEvent(QGraphicsSceneHoverEvent*) override;
    void hoverLeaveEvent(QGraphicsSceneHoverEvent*) override;
private:
    bool m_hover = false;
};

// Vector canvas: QGraphicsView in millimetres (1 scene unit = 1 mm).
class FpCanvas : public QGraphicsView {
    Q_OBJECT
public:
    enum Tool { Select, Node, Pad, RectShape, Ellipse, Polygon, Line, Pen, Text, Measure };
    explicit FpCanvas(QWidget* parent = nullptr);
    void setTool(Tool t);
    void setGridMm(double mm) { m_gridMm = mm; viewport()->update(); }
    void setRubberSizing(bool on);   // tap–spread–tap sizing for Rect/Ellipse/Line
    void zoomBy(double f);
    void fit();

    void       clearFootprint();
    QString    toSvg() const;                                  // geometry → Inkscape-editable SVG
    bool       loadSvg(const QString& svg, QString* err = nullptr);
    bool       importSvg(const QString& path, QString* err = nullptr);         // ANY .svg → flat scaled reference
    bool       importSvgEditable(const QString& path, QString* err = nullptr);  // .svg → one selectable item per element
    QJsonArray padTable() const;                               // pad list for the *.props.json hub

    // Dimension overlay — consumes pad_dimensioner.py output (actual mm).
    void       showPadDimensions(const QJsonObject& root);
    void       clearDimAnnotations();
    int        dimAnnotationCount() const { return m_dimItems.size(); }

    // Downsize support: map the last rubber-band drag (scene mm) back to the
    // imported SVG's own user-units (viewBox coords) so the Python downsizer can
    // crop to exactly the reference + component under the selection. Returns
    // false if nothing is imported or no area has been dragged yet.
    bool       hasImportedSvg() const { return m_hasImport; }
    bool       lastSelectionUserUnits(double& x0, double& y0,
                                      double& x1, double& y1) const;
    void       removeImportedReference();   // drop the datasheet/dimension-graph backdrop

    // Scale every SELECTED imported element to real size (all of them, full
    // vector fidelity — no SVG round-trip), recentre on the selection, and drop
    // the unselected datasheet. `realMmPerPageMm` is pad_dimensioner's scale.
    // Returns the number of elements scaled (0 if nothing selectable was picked).
    int        downsizeSelectedItems(double realMmPerPageMm);
    // Merge selected path elements: weld segments that share endpoints into
    // single polylines and drop exact duplicates. Returns elements after merge.
    int        mergeSelectedPaths(double epsMm = 0.02);

signals:
    void cursorMm(double x, double y);
    void zoomChanged(double percent);
    void selectionGeom(double x, double y, double w, double h, bool has);
    void status(const QString& msg);

protected:
    void wheelEvent(QWheelEvent*) override;
    bool viewportEvent(QEvent*) override;          // trackpad pinch-zoom gesture
    void mouseMoveEvent(QMouseEvent*) override;
    void mousePressEvent(QMouseEvent*) override;
    void mouseReleaseEvent(QMouseEvent*) override;
    void mouseDoubleClickEvent(QMouseEvent*) override;
    void keyPressEvent(QKeyEvent*) override;
    void drawBackground(QPainter*, const QRectF&) override;
    void drawForeground(QPainter*, const QRectF&) override;

private:
    enum class Grab { None, Anchor, CtrlIn, CtrlOut };

    QGraphicsScene* m_scene{};
    Tool    m_tool{Select};
    double  m_gridMm{0.5};

    // Measure
    bool    m_measuring{false};
    QPointF m_measureStart;

    // Right-button grab-to-pan
    bool    m_panning{false};
    QPoint  m_panLast;

    // Tap–spread–tap shape sizing (Rect / Ellipse / Line)
    bool    m_rubberSizing{true};
    bool    m_sizing{false};
    QPointF m_sizeStart;

    // Pen drawing
    FpPathItem* m_draft{nullptr};
    bool        m_drawing{false};
    bool        m_dragHandle{false};
    QPointF     m_cursorScene;

    // Node editing
    FpPathItem* m_editPath{nullptr};
    Grab        m_grab{Grab::None};
    int         m_grabIdx{-1};

    double pxPerMm() const;
    void   zoomAt(double factor, const QPoint& anchorViewPos);   // zoom toward a viewport point
    void   finishDraft(bool keep);
    void   commitSizedShape(const QPointF& a, const QPointF& b);   // create the dragged shape
    int    hitNode(const QPointF& viewPos, Grab& kindOut) const;
    void   reportSelection();

    // Dim annotation overlay items (drawn in drawForeground), all in actual-mm
    // canvas space (datum = connector centre). Populated from pad_dimensioner.py.
    struct DimAnnotation {
        QString txt;
        double  valMm = 0;
        QString axis;          // "H" or "V"
        QPointF tip1Mm;        // arrowhead tips at the two feature edges
        QPointF tip2Mm;
        int     level = 0;     // stack order outside the part extent
    };
    // Reconstructed copper feature edge (a pad boundary line).
    struct FeatureEdge {
        bool   vertical = true;
        double coord = 0;      // x (vertical edge) or y (horizontal edge), mm
        int    views = 1;      // # drawing views confirming it (≥2 = high conf)
    };
    QVector<DimAnnotation> m_dimItems;
    QVector<FeatureEdge>   m_featureEdges;
    QRectF                 m_dimExtent;   // overall merged part extent (mm)
    bool                   m_hasDimExtent = false;

    // Imported reference SVG (for rubber-band → user-unit crop mapping).
    bool    m_hasImport = false;
    double  m_impWmm = 0, m_impHmm = 0;   // physical extent placed on the canvas
    QRectF  m_impVB;                       // the SVG's viewBox (user units)
    QRectF  m_rubberScene;                 // last rubber-band rect, scene mm
};

class FootprintLibTab : public QWidget {
    Q_OBJECT
public:
    explicit FootprintLibTab(ProjectModel* model, QWidget* parent = nullptr);

signals:
    void agentRequested(const QString& prompt);   // wired to the chat/agent harness
    void statusMessage(const QString& msg);

private slots:
    void onSave();
    void onLoad();
    void onImportSvg();           // import a plain .svg onto the grid (reference layer)
    void onEditInInkscape();      // save → embed real Inkscape on the .fp.svg
    void onReturnFromInkscape();  // reload Inkscape's edits into the native canvas
    void onDetectDimensions();    // run pad_dimensioner.py and overlay the merged model
    void onExtractSvgDimensionGraph(); // run svg_dimension_graph.py
    void onDownsizeSelection();   // crop to the selection, downsize, drop the dim graph

private:
    FpCanvas*       m_canvas{};
    QDoubleSpinBox* m_x{}; QDoubleSpinBox* m_y{};
    QDoubleSpinBox* m_w{}; QDoubleSpinBox* m_h{};
    QLabel*         m_coord{};
    QLineEdit*      m_name{};
    ProjectModel*   m_model{};

    QStackedWidget* m_stack{};        // [0] native editor, [1] embedded Inkscape
    QWidget*        m_nativePage{};
    InkscapeHost*   m_inkscape{};
    QString         m_inkFile;        // .fp.svg currently open in Inkscape
    QString         m_lastSvgPath;    // last imported SVG (for dim extraction)

    QString libDir() const;
};
