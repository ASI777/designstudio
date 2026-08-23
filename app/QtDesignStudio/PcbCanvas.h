#pragma once
#include <QWidget>
#include <QTransform>
#include <QPoint>
#include <QSet>
#include "ProjectModel.h"
#include "CoreBridge.h"

// ── Tool modes ────────────────────────────────────────────────────────────────
enum class Tool { Select, PlaceFootprint, RouteTrace, PlaceVia, Measure };

// ── Layer colors (matches WPF/C# app) ─────────────────────────────────────────
struct LayerStyle {
    QColor  color;
    QString name;
    bool    visible{true};
};

class PcbCanvas : public QWidget {
    Q_OBJECT
public:
    explicit PcbCanvas(ProjectModel* model, QWidget* parent = nullptr);

    void setActiveTool(Tool t);
    void setActiveLayer(int layer);
    void setLayerVisible(int layer, bool visible);
    void fitToBoard();
    void zoomIn();
    void zoomOut();
    void zoomReset();
    // Applies a shared semantic selection without emitting a new user event;
    // MainWindow's selection model prevents feedback loops between views.
    void selectComponent(const QString& ref);
    void clearComponentSelection();
    void setCore(CoreBridge* core) { m_core = core; }

    int  activeLayer() const { return m_activeLayer; }
    Tool activeTool()  const { return m_tool; }

signals:
    void statusMessage(const QString& msg);
    void componentSelected(const QString& ref);
    void traceAdded();
    void viaAdded();

protected:
    void paintEvent(QPaintEvent*) override;
    void mousePressEvent(QMouseEvent*) override;
    void mouseMoveEvent(QMouseEvent*) override;
    void mouseReleaseEvent(QMouseEvent*) override;
    void wheelEvent(QWheelEvent*) override;
    void keyPressEvent(QKeyEvent*) override;
    void resizeEvent(QResizeEvent*) override;

private:
    ProjectModel* m_model;
    QTransform    m_xform;      // world→screen transform
    QTransform    m_inv;        // screen→world

    // Pan state
    bool   m_panning{false};
    QPoint m_panStart;
    double m_tx{0}, m_ty{0};
    double m_scale{6.0};        // px per mm

    // Tool state
    Tool   m_tool{Tool::Select};
    int    m_activeLayer{0};
    QSet<QString> m_selected;
    QPointF m_routeStart;
    bool    m_routing{false};
    QPointF m_cursor;           // world coords of mouse
    CoreBridge* m_core{};
    InteractivePreview m_routePreview;
    int m_routeNet{-1};

    // Layer styles (indexed 0–31)
    static constexpr int MAX_LAYERS = 32;
    LayerStyle m_layers[MAX_LAYERS];

    // Helpers
    void  initLayers();
    void  updateTransform();
    void  drawBoard(QPainter& p);
    void  drawRuleAreas(QPainter& p);
    void  drawTraces(QPainter& p);
    void  drawVias(QPainter& p);
    void  drawFootprints(QPainter& p);
    void  drawRatsnest(QPainter& p);
    void  drawGrid(QPainter& p);
    void  drawCursor(QPainter& p);
    void  drawLayerLegend(QPainter& p);
    void  drawRoutePreview(QPainter& p);

    QPointF toWorld(const QPoint& screen)  const;
    QPointF toScreen(const QPointF& world) const;
    QRectF  fpBounds(const ProjFootprint& fp) const;
    bool    hitTest(const ProjFootprint& fp, const QPointF& world) const;
    bool    selectAt(const QPointF& world);
    int     routeNetAt(const QPointF& world) const;
    void    cancelRoute();

    QColor  netColor(int netId) const;
};
