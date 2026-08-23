#pragma once

#include <QPointF>
#include <QMatrix4x4>
#include <QQuaternion>
#include <QVector>
#include <QVector3D>
#include <QWidget>
#include <QOpenGLBuffer>
#include <QOpenGLShaderProgram>
#include <QOpenGLWidget>

#include <memory>

class QPainter;
class QPaintEvent;
class QImage;

namespace designstudio {
class GlbMesh;
class SoftwareCanvas;

class MeshProjectionView final : public QOpenGLWidget {
    Q_OBJECT
public:
    enum class Projection { Front, Right, Top, Bottom, Isometric };
    enum class UpAxis { Y, Z };
    enum class DisplayMode { Shaded, ShadedEdges, Wireframe };
    enum class ScenePalette { Cad, PcbAssembly };

    explicit MeshProjectionView(QWidget* parent = nullptr);

    void setMesh(std::shared_ptr<const GlbMesh> mesh);
    void setProjection(Projection projection);
    void setUpAxis(UpAxis axis);
    void setDisplayMode(DisplayMode mode);
    void setSectionMode(bool enabled);
    void setExploded(bool enabled);
    void setComponentBodiesVisible(bool visible);
    void setClearanceOverlay(bool enabled);
    void setScenePalette(ScenePalette palette, double boardThicknessMm = 1.6);
    void setNavigationEnabled(bool enabled);
    void setScaleToConfirmedMm(double scale);
    void setModelTransform(const QMatrix4x4& transform);
    void setMarkedVertices(const QVector<quint32>& vertices);
    void setSelectedComponent(const QString& semanticId);
    void setSelectedComponentReference(const QString& referenceDesignator);
    bool semanticIdentityAvailable() const noexcept;
    QString selectedComponent() const noexcept { return selectedComponentId_; }
    // Marks orbit/pan/zoom interaction.  The renderer never substitutes an
    // incomplete triangle prefix, so movement cannot show a broken surface.
    void setInteractionPreview(bool enabled);
    void fitView();
    Projection projection() const noexcept { return projection_; }
    UpAxis upAxis() const noexcept { return upAxis_; }
    DisplayMode displayMode() const noexcept { return displayMode_; }
    bool sectionMode() const noexcept { return sectionMode_; }
    bool explodedView() const noexcept { return explodeEnabled_; }
    // Number of connected display groups detected in the current mesh.
    // Disjoint STEP solids (housing, lens stack, interior reservations) each
    // receive their own group so they can be coloured and exploded apart.
    int componentGroupCount() const noexcept { return groupCount_; }
    bool componentBodiesVisible() const noexcept { return componentBodiesVisible_; }
    bool clearanceOverlay() const noexcept { return clearanceOverlay_; }
    ScenePalette scenePalette() const noexcept { return scenePalette_; }
    bool navigationEnabled() const noexcept { return navigationEnabled_; }
    bool hasMesh() const noexcept { return mesh_ != nullptr; }
    bool acceleratedRendererAvailable() const noexcept { return gpuActive(); }
    double zoomFactor() const noexcept { return zoomFactor_; }
    double viewScalePxPerMm() const noexcept { return viewScalePxPerMm_; }
    QPointF panOffsetPx() const noexcept { return panOffsetPx_; }
    QQuaternion cameraOrientation() const noexcept { return cameraOrientation_; }
    QMatrix4x4 modelTransform() const noexcept { return modelTransform_; }
    QString projectionName() const;
    // Deterministic inspection/export path that does not require an OpenGL
    // context. It renders the complete current scene at the widget size.
    QImage renderSoftwareSnapshot();

signals:
    void vertexPicked(quint32 vertexIndex, QVector3D confirmedPositionMm,
                      const QString& projection);
    void componentPicked(const QString& semanticId, const QString& referenceDesignator);
    void viewChanged();

protected:
    void initializeGL() override;
    void paintEvent(QPaintEvent* event) override;
    void paintGL() override;
    void resizeGL(int width, int height) override;
    void mousePressEvent(QMouseEvent* event) override;
    void mouseMoveEvent(QMouseEvent* event) override;
    void mouseReleaseEvent(QMouseEvent* event) override;
    void mouseDoubleClickEvent(QMouseEvent* event) override;
    void wheelEvent(QWheelEvent* event) override;
    void keyPressEvent(QKeyEvent* event) override;
    void resizeEvent(QResizeEvent* event) override;

private:
    enum class DragMode { None, Orbit, Pan, Zoom };
    struct PaintedTriangle {
        quint32 a{}, b{}, c{};
        qsizetype sourceTriangle{-1};
        float depth{};
        float light{};
        quint8 material{};
        quint32 group{};
    };

    QVector3D canonicalPoint(const QVector3D& point) const;
    QVector3D cameraPoint(const QVector3D& point) const;
    QPointF screenPoint(const QVector3D& camera) const;
    QQuaternion orientationFor(Projection projection) const;
    void zoomAt(const QPointF& cursor, double factor);
    void updateDragCursor();
    void rebuildScreenPoints();
    void rebuildPaintedTriangles();
    void rebuildComponentGroups();
    void applyExplodeOffsets();
    QVector3D displayVertex(int index) const;
    bool groupedColoringActive() const noexcept;
    void paintClearanceOverlay(QPainter& painter);
    void paintSoftware(QPainter& painter);
    qsizetype paintedTriangleLimit() const noexcept;
    void uploadGpuMesh();
    bool gpuActive() const noexcept;
    quint8 triangleMaterial(qsizetype triangleIndex, quint32 a, quint32 b, quint32 c) const;
    QVector3D semanticColor(quint8 material) const;
    QVector3D groupColorVector(quint32 group) const;
    bool triangleSelected(qsizetype triangleIndex) const;
    QMatrix4x4 gpuMvp() const;

    std::shared_ptr<const GlbMesh> mesh_;
    Projection projection_ = Projection::Isometric;
    UpAxis upAxis_ = UpAxis::Y;
    DisplayMode displayMode_ = DisplayMode::Wireframe;
    bool sectionMode_ = false;
    bool explodeEnabled_ = false;
    int groupCount_ = 0;
    QVector<quint32> vertexGroup_;
    QVector<QVector3D> groupOffsets_;
    bool componentBodiesVisible_ = true;
    bool clearanceOverlay_ = false;
    ScenePalette scenePalette_ = ScenePalette::Cad;
    double boardThicknessMm_ = 1.6;
    bool navigationEnabled_ = false;
    bool customView_ = false;
    double scaleToConfirmedMm_ = 1.0;
    double viewScalePxPerMm_ = 1.0;
    double zoomFactor_ = 1.0;
    QPointF panOffsetPx_;
    QQuaternion cameraOrientation_;
    QMatrix4x4 modelTransform_;
    QVector3D meshCenterMm_;
    QVector<quint32> markedVertices_;
    QVector<QPointF> screenPoints_;
    QVector<QVector3D> cameraPoints_;
    QVector<PaintedTriangle> paintedTriangles_;
    DragMode dragMode_ = DragMode::None;
    QPoint dragLast_;
    QPoint dragStart_;
    bool dragMoved_ = false;
    bool orbitGuideVisible_ = false;
    QPointF orbitGuideCenter_;
    bool interactionPreview_ = false;
    QString selectedComponentId_;
    QVector<QVector3D> semanticColors_;
    QVector<QString> semanticReferences_;
    bool gpuReady_ = false;
    bool gpuMeshDirty_ = true;
    QOpenGLBuffer gpuVertexBuffer_{QOpenGLBuffer::VertexBuffer};
    QOpenGLBuffer gpuIndexBuffer_{QOpenGLBuffer::IndexBuffer};
    std::unique_ptr<QOpenGLShaderProgram> gpuProgram_;
    qsizetype gpuIndexCount_ = 0;
    SoftwareCanvas* softwareFallback_{};

    friend class SoftwareCanvas;
};

} // namespace designstudio
