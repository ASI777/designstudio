#include "MeshProjectionView.h"

#include "GlbMeshReader.h"
#include "SemanticAssembly.h"

#include <QKeyEvent>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QResizeEvent>
#include <QWheelEvent>
#include <QOpenGLContext>
#include <QOpenGLFunctions>
#include <QFont>
#include <QImage>

#include <algorithm>
#include <cmath>
#include <limits>
#include <unordered_map>
#include <vector>

namespace designstudio {
namespace {
constexpr double PickRadiusPx = 13.0;
constexpr double PaddingPx = 42.0;
constexpr double OrbitDegreesPerPixel = 0.38;
constexpr double DragThresholdSquared = 9.0;

QColor shadedCadColor(float light)
{
    const float value = std::clamp(light, 0.0f, 1.0f);
    return QColor::fromRgbF(0.12 + 0.20 * value,
                            0.31 + 0.42 * value,
                            0.38 + 0.42 * value,
                            1.0);
}

// Distinct muted hue per connected display group. Golden-angle stepping keeps
// adjacent groups visually separated without saturating the viewport.
QColor shadedGroupColor(quint32 group, float light)
{
    const float value = std::clamp(light, 0.0f, 1.0f);
    const int hue = int(std::fmod(double(group) * 137.508, 360.0));
    return QColor::fromHsl(hue, 92, 84 + int(58 * value));
}

QColor shadedPcbColor(quint8 material, float light)
{
    const float value = std::clamp(light, 0.0f, 1.0f);
    if (material == 1) {
        // The STEP display mesh carries no AP242 presentation styles. Keep
        // this clearly labelled as a viewer palette, and use the known board
        // thickness only to distinguish FR-4 from assembled component solids.
        return QColor::fromRgbF(0.035 + 0.055 * value,
                                0.20 + 0.30 * value,
                                0.11 + 0.18 * value, 1.0);
    }
    if (material == 2)
        return QColor::fromRgbF(0.48 + 0.42 * value,
                                0.23 + 0.40 * value,
                                0.035 + 0.22 * value, 1.0);
    static const QColor components[] = {
        QColor("#d97706"), QColor("#2563eb"), QColor("#dc2626"),
        QColor("#9333ea"), QColor("#0891b2"), QColor("#65a30d"),
    };
    const QColor base = components[(material - 3) % (sizeof(components) / sizeof(components[0]))];
    return QColor::fromRgbF(base.redF() * (0.45 + 0.55 * value),
                            base.greenF() * (0.45 + 0.55 * value),
                            base.blueF() * (0.45 + 0.55 * value), 1.0);
}

quint8 componentMaterial(const GlbMesh& mesh, quint32 a, quint32 b, quint32 c,
                          double boardThicknessMm)
{
    const QVector3D pa = mesh.verticesMm().at(a);
    const QVector3D pb = mesh.verticesMm().at(b);
    const QVector3D pc = mesh.verticesMm().at(c);
    const float z = (pa.z() + pb.z() + pc.z()) / 3.0f;
    if (z >= -0.05f && z <= float(boardThicknessMm + 0.05)) return 1;
    if (z < -0.05f) return 2;
    const int bucketX = int(std::floor((pa.x() + pb.x() + pc.x()) / 15.0f));
    const int bucketY = int(std::floor((pa.y() + pb.y() + pc.y()) / 15.0f));
    const unsigned hash = unsigned(bucketX * 73856093) ^ unsigned(bucketY * 19349663);
    return quint8(3 + (hash % 6));
}

QVector3D componentColor(quint8 material)
{
    if (material == 1) return QVector3D(0.05f, 0.48f, 0.20f);
    if (material == 2) return QVector3D(0.88f, 0.52f, 0.08f);
    static const QVector3D components[] = {
        {0.85f, 0.39f, 0.02f}, {0.10f, 0.32f, 0.88f}, {0.86f, 0.08f, 0.10f},
        {0.52f, 0.10f, 0.78f}, {0.02f, 0.57f, 0.70f}, {0.34f, 0.62f, 0.03f},
    };
    return components[(material - 3) % (sizeof(components) / sizeof(components[0]))];
}
} // namespace

class SoftwareCanvas final : public QWidget
{
public:
    explicit SoftwareCanvas(MeshProjectionView* owner)
        : QWidget(owner), owner_(owner)
    {
        setAttribute(Qt::WA_TransparentForMouseEvents, true);
        setAttribute(Qt::WA_NoSystemBackground, true);
        setAutoFillBackground(false);
    }

protected:
    void paintEvent(QPaintEvent* event) override
    {
        Q_UNUSED(event);
        QPainter painter(this);
        if (painter.isActive()) owner_->paintSoftware(painter);
    }

private:
    MeshProjectionView* owner_{};
};

MeshProjectionView::MeshProjectionView(QWidget* parent) : QOpenGLWidget(parent)
{
    setMinimumSize(560, 420);
    setMouseTracking(true);
    setFocusPolicy(Qt::StrongFocus);
    setCursor(Qt::CrossCursor);
    cameraOrientation_ = orientationFor(projection_);
    softwareFallback_ = new SoftwareCanvas(this);
    softwareFallback_->setGeometry(rect());
    softwareFallback_->hide();
}

void MeshProjectionView::setMesh(std::shared_ptr<const GlbMesh> mesh)
{
    mesh_ = std::move(mesh);
    markedVertices_.clear();
    selectedComponentId_.clear();
    semanticColors_.clear();
    semanticReferences_.clear();
    if (mesh_ && mesh_->assembly()) {
        for (const SemanticAssemblyComponent& component : mesh_->assembly()->components) {
            if (!component.name.startsWith(QStringLiteral("Component_"))) continue;
            QVector3D color(component.color.redF(), component.color.greenF(),
                            component.color.blueF());
            // STEP member files may not carry a presentation style.  Keep a
            // stable per-component palette in that case instead of letting
            // every body inherit the neutral importer colour.
            if (!component.color.isValid()
                || (std::abs(color.x() - 0.5f) < 0.01f
                    && std::abs(color.y() - 0.5f) < 0.01f
                    && std::abs(color.z() - 0.5f) < 0.01f)) {
                color = componentColor(quint8(3 + semanticColors_.size()));
            }
            semanticColors_.append(color);
            semanticReferences_.append(component.referenceDesignator);
        }
    }
    modelTransform_.setToIdentity();
    if (mesh_) meshCenterMm_ = (mesh_->boundsMinMm() + mesh_->boundsMaxMm()) * 0.5f;
    else meshCenterMm_ = {};
    // Group state is derived from the mesh payload; a new source invalidates it.
    // Build it eagerly so grouped colours are active from the first frame and
    // the explode toggle can reuse the segmentation.
    vertexGroup_.clear();
    groupOffsets_.clear();
    groupCount_ = 0;
    if (mesh_) rebuildComponentGroups();
    gpuMeshDirty_ = true;
    fitView();
}

void MeshProjectionView::setSelectedComponent(const QString& semanticId)
{
    selectedComponentId_ = semanticId;
    gpuMeshDirty_ = true;
    update();
}

void MeshProjectionView::setSelectedComponentReference(const QString& referenceDesignator)
{
    if (!mesh_ || !mesh_->assembly() || !mesh_->assembly()->identityAvailable) return;
    for (const SemanticAssemblyComponent& component : mesh_->assembly()->components)
        if (component.referenceDesignator == referenceDesignator
            || component.semanticId == referenceDesignator) {
            setSelectedComponent(component.semanticId);
            return;
        }
    // A tree selection can race a reload.  Do not leave an unrelated object
    // highlighted when the requested identity is no longer in the cache.
    setSelectedComponent({});
}

bool MeshProjectionView::semanticIdentityAvailable() const noexcept
{
    return mesh_ && mesh_->assembly() && mesh_->assembly()->identityAvailable;
}

void MeshProjectionView::initializeGL()
{
    auto* functions = context() ? context()->functions() : nullptr;
    if (!functions) return;
    functions->initializeOpenGLFunctions();
    gpuProgram_ = std::make_unique<QOpenGLShaderProgram>(this);
    static const char* vertexShader =
        "attribute vec3 position; attribute vec3 vertexColor; uniform mat4 mvp;"
        "varying vec3 vColor; void main(){ vColor = vertexColor;"
        "gl_Position = mvp * vec4(position, 1.0); }";
    static const char* fragmentShader =
        "varying vec3 vColor; void main(){ gl_FragColor = vec4(vColor, 1.0); }";
    if (!gpuProgram_->addShaderFromSourceCode(QOpenGLShader::Vertex, vertexShader)
        || !gpuProgram_->addShaderFromSourceCode(QOpenGLShader::Fragment, fragmentShader)
        || !gpuProgram_->link()) {
        gpuProgram_.reset();
        return;
    }
    gpuVertexBuffer_.create();
    gpuIndexBuffer_.create();
    gpuReady_ = true;
    gpuMeshDirty_ = true;
}

void MeshProjectionView::resizeGL(int width, int height)
{
    Q_UNUSED(width);
    Q_UNUSED(height);
    update();
}

bool MeshProjectionView::gpuActive() const noexcept
{
    // Keep the accelerated path active while a semantic component is
    // selected.  Falling back to a full CPU painter pass for a large STEP
    // assembly would reintroduce the orbit/selection lag this viewport is
    // designed to remove.  The semantic selection remains authoritative in
    // the selection model; software rendering is used for explicit edge or
    // wireframe modes where a highlight can be drawn exactly.
    return gpuReady_ && gpuProgram_ && mesh_ && !sectionMode_
        && componentBodiesVisible_ && !clearanceOverlay_
        && displayMode_ == DisplayMode::Shaded;
}

QVector3D MeshProjectionView::groupColorVector(quint32 group) const
{
    const QColor color = shadedGroupColor(group, 0.82f);
    return QVector3D(float(color.redF()), float(color.greenF()),
                     float(color.blueF()));
}

quint8 MeshProjectionView::triangleMaterial(qsizetype triangleIndex,
                                            quint32 a, quint32 b, quint32 c) const
{
    if (mesh_ && mesh_->assembly() && mesh_->assembly()->identityAvailable) {
        const QString semanticId = mesh_->assembly()->componentForTriangle(triangleIndex);
        const int index = mesh_->assembly()->componentIndex(semanticId);
        if (index >= 0 && index < mesh_->assembly()->components.size()) {
            const QString name = mesh_->assembly()->components.at(index).name;
            if (name == QStringLiteral("PCB_BOARD")
                || name.startsWith(QStringLiteral("SolderMask"))) return 1;
            if (name.startsWith(QStringLiteral("CopperPlane_"))
                || name.startsWith(QStringLiteral("Trace_"))
                || name.startsWith(QStringLiteral("Via_"))
                || name.startsWith(QStringLiteral("Pad_"))
                || name.startsWith(QStringLiteral("Silk_"))) return 2;
            // AP242 component bodies are the only members that receive the
            // semantic colour table and can be hidden as a group.
            if (name.startsWith(QStringLiteral("Component_"))) {
                int componentIndex = 0;
                for (int candidate = 0; candidate < index; ++candidate)
                    if (mesh_->assembly()->components.at(candidate).name
                            .startsWith(QStringLiteral("Component_")))
                        ++componentIndex;
                if (componentIndex < 252) return quint8(3 + componentIndex);
            }
            return 2;
        }
    }
    return componentMaterial(*mesh_, a, b, c, boardThicknessMm_);
}

QVector3D MeshProjectionView::semanticColor(quint8 material) const
{
    if (material >= 3) {
        const int index = int(material) - 3;
        if (index >= 0 && index < semanticColors_.size()) return semanticColors_.at(index);
    }
    return componentColor(material);
}

bool MeshProjectionView::triangleSelected(qsizetype triangleIndex) const
{
    return mesh_ && mesh_->assembly() && !selectedComponentId_.isEmpty()
        && mesh_->assembly()->componentForTriangle(triangleIndex) == selectedComponentId_;
}

void MeshProjectionView::uploadGpuMesh()
{
    if (!gpuActive() || !gpuMeshDirty_) return;
    QVector<float> vertices;
    // Expand the indexed stream so a shared geometric vertex can carry the
    // material of each adjacent face.  A STEP tessellation often shares
    // board/component vertices; one colour per shared vertex causes colours
    // to bleed across copper, mask, and component bodies.
    const QVector<quint32>& indices = mesh_->triangleIndices();
    vertices.reserve(indices.size() * 6);
    QVector<quint32> expandedIndices;
    expandedIndices.reserve(indices.size());
    // Grouped colouring (sidecar-less compounds) and the exploded offset are
    // both baked into this stream so orbiting stays on the accelerated path.
    const bool grouped = groupedColoringActive();
    for (qsizetype triangle = 0; triangle + 2 < indices.size(); triangle += 3) {
        const quint32 a = indices.at(triangle);
        const quint32 b = indices.at(triangle + 1);
        const quint32 c = indices.at(triangle + 2);
        if (a >= quint32(mesh_->verticesMm().size())
            || b >= quint32(mesh_->verticesMm().size())
            || c >= quint32(mesh_->verticesMm().size())) continue;
        // The semantic sidecar indexes triangles, while this loop walks the
        // flattened index array (three entries per triangle).
        const quint8 material = triangleMaterial(triangle / 3, a, b, c);
        for (quint32 index : {a, b, c}) {
            const QVector3D point = displayVertex(int(index));
            const QVector3D color = grouped
                ? groupColorVector(vertexGroup_.value(int(index)))
                : semanticColor(material);
            vertices.append(point.x());
            vertices.append(point.y());
            vertices.append(point.z());
            vertices.append(color.x());
            vertices.append(color.y());
            vertices.append(color.z());
            expandedIndices.append(quint32(expandedIndices.size()));
        }
    }
    gpuVertexBuffer_.bind();
    gpuVertexBuffer_.allocate(vertices.constData(), int(vertices.size() * sizeof(float)));
    gpuVertexBuffer_.release();
    gpuIndexBuffer_.bind();
    gpuIndexBuffer_.allocate(expandedIndices.constData(),
                             int(expandedIndices.size() * sizeof(quint32)));
    gpuIndexBuffer_.release();
    gpuIndexCount_ = expandedIndices.size();
    gpuMeshDirty_ = false;
}

void MeshProjectionView::paintEvent(QPaintEvent* event)
{
    // QOpenGLWidget has no usable context on some headless/remote desktops
    // (for example Qt's offscreen platform).  Keep the engineering preview
    // useful there by painting the same complete mesh with the deterministic
    // software path instead of leaving a blank viewport.  A valid context
    // continues through Qt's accelerated paintGL path.
    if (!context() || !context()->isValid()) {
        if (softwareFallback_) {
            softwareFallback_->setGeometry(rect());
            softwareFallback_->show();
            softwareFallback_->raise();
            softwareFallback_->update();
        }
        return;
    }
    if (softwareFallback_) softwareFallback_->hide();
    QOpenGLWidget::paintEvent(event);
}

QImage MeshProjectionView::renderSoftwareSnapshot()
{
    // The interactive viewport may currently be using the GPU and therefore
    // have no CPU projection cache. An explicit evidence/export snapshot must
    // still render the complete current scene deterministically.
    rebuildScreenPoints(true);
    QImage image(size(), QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    QPainter painter(&image);
    paintSoftware(painter);
    return image;
}

QMatrix4x4 MeshProjectionView::gpuMvp() const
{
    const double scale = std::max(1e-9, viewScalePxPerMm_ * zoomFactor_);
    const double halfWidth = std::max(1.0, double(width())) / (2.0 * scale);
    const double halfHeight = std::max(1.0, double(height())) / (2.0 * scale);
    QMatrix4x4 projection;
    projection.ortho((-halfWidth - panOffsetPx_.x() / scale),
                     (halfWidth - panOffsetPx_.x() / scale),
                     (-halfHeight + panOffsetPx_.y() / scale),
                     (halfHeight + panOffsetPx_.y() / scale), 1000.0, -1000.0);
    QMatrix4x4 axis;
    if (upAxis_ == UpAxis::Z) {
        axis.setRow(1, QVector4D(0, 0, 1, 0));
        axis.setRow(2, QVector4D(0, -1, 0, 0));
    }
    QMatrix4x4 centre;
    centre.translate(-meshCenterMm_);
    const QMatrix3x3 rotation3 = cameraOrientation_.toRotationMatrix();
    QMatrix4x4 rotation;
    for (int row = 0; row < 3; ++row)
        for (int column = 0; column < 3; ++column)
            rotation(row, column) = rotation3(row, column);
    return projection * rotation * axis
        * centre * modelTransform_;
}

void MeshProjectionView::paintGL()
{
    if (!gpuActive()) {
        QPainter painter(this);
        if (painter.isActive()) paintSoftware(painter);
        return;
    }
    auto* functions = context()->functions();
    functions->glClearColor(17.0f / 255.0f, 22.0f / 255.0f, 28.0f / 255.0f, 1.0f);
    functions->glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    // QOpenGLWidget may leave blending/dithering enabled after a painter
    // pass.  The preview shader emits opaque semantic colours; disable those
    // states so the board mask, copper and bodies are not washed together.
    functions->glDisable(GL_BLEND);
    functions->glDisable(GL_DITHER);
    functions->glEnable(GL_DEPTH_TEST);
    uploadGpuMesh();
    gpuProgram_->bind();
    gpuProgram_->setUniformValue("mvp", gpuMvp());
    gpuVertexBuffer_.bind();
    gpuIndexBuffer_.bind();
    gpuProgram_->enableAttributeArray("position");
    gpuProgram_->enableAttributeArray("vertexColor");
    gpuProgram_->setAttributeBuffer("position", GL_FLOAT, 0, 3, 6 * int(sizeof(float)));
    gpuProgram_->setAttributeBuffer("vertexColor", GL_FLOAT, 3 * int(sizeof(float)),
                                    3, 6 * int(sizeof(float)));
    // Never cut the index stream at an arbitrary triangle boundary.  That
    // creates visible holes and flicker during orbit.  The GPU path is already
    // bounded by the tessellator and can render the complete valid mesh.
    functions->glDrawElements(GL_TRIANGLES, int(gpuIndexCount_), GL_UNSIGNED_INT, nullptr);
    gpuProgram_->disableAttributeArray("position");
    gpuProgram_->disableAttributeArray("vertexColor");
    gpuIndexBuffer_.release();
    gpuVertexBuffer_.release();
    gpuProgram_->release();
    functions->glDisable(GL_DEPTH_TEST);

    // Keep the navigation affordances visible over the accelerated mesh.  The
    // software renderer owns the full overlay; this lightweight pass avoids
    // rebuilding CPU triangles just to draw labels and the orientation triad.
    QPainter overlay(this);
    overlay.setRenderHint(QPainter::Antialiasing, true);
    overlay.setPen(QColor(180, 190, 204));
    overlay.drawText(QRect(14, 10, width() - 28, 24), Qt::AlignLeft,
                     projectionName().toUpper() + QStringLiteral(" · accelerated STEP/CAD view"));
    const QPointF triadOrigin(54.0, height() - 50.0);
    const struct Axis { QVector3D world; QColor color; const char* label; } axes[] = {
        {QVector3D(1, 0, 0), QColor("#ff5c5c"), "X"},
        {QVector3D(0, 1, 0), QColor("#65d46e"), "Y"},
        {QVector3D(0, 0, 1), QColor("#58a6ff"), "Z"},
    };
    for (const Axis& axis : axes) {
        const QVector3D canonical = upAxis_ == UpAxis::Z
            ? QVector3D(axis.world.x(), axis.world.z(), -axis.world.y()) : axis.world;
        const QVector3D camera = cameraOrientation_.rotatedVector(canonical).normalized();
        const QPointF end = triadOrigin + QPointF(camera.x() * 28.0, -camera.y() * 28.0);
        overlay.setPen(QPen(axis.color, 2.2));
        overlay.drawLine(triadOrigin, end);
        overlay.drawText(end + QPointF(3, -3), QString::fromLatin1(axis.label));
    }
    overlay.setPen(QColor(146, 157, 171));
    overlay.drawText(QRect(14, height() - 30, width() - 28, 20),
                     Qt::AlignRight | Qt::AlignVCenter,
                     QStringLiteral("Left drag / Shift+Right drag: orbit   Right/Middle drag: pan   "
                                    "Ctrl+Right drag or wheel: zoom   Double-click/Home: fit"));
}

QQuaternion MeshProjectionView::orientationFor(Projection projection) const
{
    switch (projection) {
    case Projection::Front:
        return QQuaternion();
    case Projection::Right:
        return QQuaternion::fromAxisAndAngle(QVector3D(0, 1, 0), -90.0f);
    case Projection::Top:
        return QQuaternion::fromAxisAndAngle(QVector3D(1, 0, 0), -90.0f);
    case Projection::Bottom:
        return QQuaternion::fromAxisAndAngle(QVector3D(1, 0, 0), 90.0f);
    case Projection::Isometric:
        return QQuaternion::fromAxisAndAngle(QVector3D(1, 0, 0), -35.264f)
            * QQuaternion::fromAxisAndAngle(QVector3D(0, 1, 0), 45.0f);
    }
    return {};
}

void MeshProjectionView::setProjection(Projection projection)
{
    projection_ = projection;
    customView_ = false;
    cameraOrientation_ = orientationFor(projection_);
    fitView();
}

void MeshProjectionView::setUpAxis(UpAxis axis)
{
    if (upAxis_ == axis) return;
    upAxis_ = axis;
    fitView();
}

void MeshProjectionView::setDisplayMode(DisplayMode mode)
{
    if (displayMode_ == mode) return;
    displayMode_ = mode;
    if (!gpuActive()) rebuildScreenPoints();
    update();
}

void MeshProjectionView::setSectionMode(bool enabled)
{
    if (sectionMode_ == enabled) return;
    sectionMode_ = enabled;
    // Section mode intentionally uses the complete software painter.  A
    // clipped GPU index stream would make the section boundary dependent on
    // arbitrary STEP triangle ordering.  The CPU path filters whole triangles
    // at a deterministic board-mid-plane instead.
    rebuildScreenPoints();
    update();
}

void MeshProjectionView::setComponentBodiesVisible(bool visible)
{
    if (componentBodiesVisible_ == visible) return;
    componentBodiesVisible_ = visible;
    gpuMeshDirty_ = true;
    rebuildScreenPoints();
    update();
}

void MeshProjectionView::setExploded(bool enabled)
{
    if (explodeEnabled_ == enabled) return;
    explodeEnabled_ = enabled;
    if (explodeEnabled_) {
        if (vertexGroup_.isEmpty()) rebuildComponentGroups();
        applyExplodeOffsets();
    }
    // The explode offset is applied to displayed positions, so both the GPU
    // stream and the CPU painter must be rebuilt from the source mesh.
    gpuMeshDirty_ = true;
    fitView();
    update();
}

void MeshProjectionView::rebuildComponentGroups()
{
    vertexGroup_.clear();
    groupOffsets_.clear();
    groupCount_ = 0;
    if (!mesh_) return;
    const qsizetype vertexCount = mesh_->verticesMm().size();
    const qsizetype indexCount = mesh_->triangleIndices().size();
    if (vertexCount == 0 || vertexCount > 5'000'000 || indexCount < 3) return;

    // Weld coincident tessellation vertices so face-boundary seams inside one
    // solid stay connected, while disjoint solids remain separate groups.
    struct Cell { qint64 x; qint64 y; qint64 z; bool operator==(const Cell& o) const {
        return x == o.x && y == o.y && z == o.z; } };
    struct CellHash { std::size_t operator()(const Cell& c) const noexcept {
        auto mix = [](qint64 v) -> std::size_t {
            quint64 u = static_cast<quint64>(v);
            u ^= u >> 33; u *= 0xff51afd7ed558ccdULL; u ^= u >> 33;
            return static_cast<std::size_t>(u); };
        return mix(c.x) ^ (mix(c.y) * 0x9e3779b97f4a7c15ULL)
             ^ (mix(c.z) * 0xc2b2ae3d27d4eb4fULL); } };
    constexpr double kCellMm = 0.02;
    constexpr double kWeldMm2 = 0.05 * 0.05;
    auto cellOf = [&](const QVector3D& p) {
        return Cell{qint64(std::floor(p.x() / kCellMm)),
                    qint64(std::floor(p.y() / kCellMm)),
                    qint64(std::floor(p.z() / kCellMm))}; };
    std::unordered_map<Cell, quint32, CellHash> weld;
    weld.reserve(static_cast<std::size_t>(vertexCount) * 2);
    QVector<quint32> representative(static_cast<int>(vertexCount),
                                    std::numeric_limits<quint32>::max());
    for (int i = 0; i < static_cast<int>(vertexCount); ++i) {
        const QVector3D& point = mesh_->verticesMm().at(i);
        const Cell base = cellOf(point);
        quint32 found = std::numeric_limits<quint32>::max();
        for (int dx = -1; dx <= 1 && found == std::numeric_limits<quint32>::max(); ++dx)
            for (int dy = -1; dy <= 1 && found == std::numeric_limits<quint32>::max(); ++dy)
                for (int dz = -1; dz <= 1; ++dz) {
                    const auto it = weld.find(Cell{base.x + dx, base.y + dy, base.z + dz});
                    if (it == weld.end()) continue;
                    const QVector3D delta = mesh_->verticesMm().at(it->second) - point;
                    if (delta.lengthSquared() <= float(kWeldMm2)) { found = it->second; break; }
                }
        if (found == std::numeric_limits<quint32>::max()) {
            found = static_cast<quint32>(i);
            weld.emplace(base, found);
        }
        representative[i] = found;
    }

    // Union-find over triangles that share welded vertex representatives.
    // The previous implementation passed a vertex index into a triangle-sized
    // parent array, so a one-triangle GLB could index past the end of `parent`
    // during the first smoke-test paint.  Keep the two index domains separate.
    const qsizetype triangleCount = indexCount / 3;
    std::vector<quint32> parent(static_cast<std::size_t>(triangleCount));
    for (std::size_t i = 0; i < parent.size(); ++i) parent[i] = static_cast<quint32>(i);
    auto find = [&parent](quint32 x) {
        while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x; };
    auto unite = [&parent, &find](quint32 a, quint32 b) {
        a = find(a); b = find(b); if (a != b) parent[b] = a; };
    const QVector<quint32>& indices = mesh_->triangleIndices();
    std::unordered_map<quint32, quint32> firstTriangle;
    firstTriangle.reserve(static_cast<std::size_t>(triangleCount) * 2);
    QVector<quint32> firstTriangleForVertex(
        static_cast<int>(vertexCount), std::numeric_limits<quint32>::max());
    for (qsizetype t = 0; t + 2 < indexCount; t += 3) {
        const quint32 a = indices.at(t), b = indices.at(t + 1), c = indices.at(t + 2);
        if (a >= quint32(vertexCount) || b >= quint32(vertexCount)
            || c >= quint32(vertexCount)) continue;
        const quint32 triangle = static_cast<quint32>(t / 3);
        for (const quint32 vertex : {a, b, c}) {
            firstTriangleForVertex[int(vertex)] = triangle;
            const quint32 welded = representative[int(vertex)];
            const auto [it, inserted] = firstTriangle.emplace(welded, triangle);
            if (!inserted) unite(triangle, it->second);
        }
    }
    QHash<quint32, quint32> denseGroup;
    vertexGroup_.resize(static_cast<int>(vertexCount));
    for (int i = 0; i < static_cast<int>(vertexCount); ++i) {
        const quint32 triangle = firstTriangleForVertex.at(i);
        const quint32 root = triangle == std::numeric_limits<quint32>::max()
            ? std::numeric_limits<quint32>::max() : find(triangle);
        auto it = denseGroup.find(root);
        if (it == denseGroup.end() && root != std::numeric_limits<quint32>::max())
            it = denseGroup.insert(root, groupCount_++);
        vertexGroup_[i] = it == denseGroup.end() ? 0u : it.value();
    }
}

void MeshProjectionView::applyExplodeOffsets()
{
    groupOffsets_.fill(QVector3D(), static_cast<qsizetype>(groupCount_));
    if (groupCount_ <= 0 || vertexGroup_.isEmpty() || !mesh_) return;
    std::vector<QVector3D> accumulator(static_cast<std::size_t>(groupCount_));
    std::vector<quint32> samples(static_cast<std::size_t>(groupCount_), 0u);
    for (int i = 0; i < vertexGroup_.size(); ++i) {
        const quint32 group = vertexGroup_.at(i);
        if (group >= quint32(groupCount_)) continue;
        accumulator[group] += mesh_->verticesMm().at(i);
        ++samples[group];
    }
    for (int g = 0; g < groupCount_; ++g) {
        if (samples[static_cast<std::size_t>(g)] == 0u) continue;
        const QVector3D centroid =
            accumulator[static_cast<std::size_t>(g)] / float(samples[static_cast<std::size_t>(g)]);
        const QVector3D direction = centroid - meshCenterMm_;
        const float length = direction.length();
        // Push each solid outward from the assembly centre: proportional
        // separation plus a fixed floor so thin gaps still open visibly.
        groupOffsets_[g] = length > 1e-6f
            ? direction * 0.55f + direction / length * 12.0f
            : QVector3D();
    }
}

QVector3D MeshProjectionView::displayVertex(int index) const
{
    const QVector3D& vertex = mesh_->verticesMm().at(index);
    if (!explodeEnabled_ || index >= vertexGroup_.size()) return vertex;
    const quint32 group = vertexGroup_.at(index);
    if (group >= quint32(groupOffsets_.size())) return vertex;
    return vertex + groupOffsets_.at(group);
}

bool MeshProjectionView::groupedColoringActive() const noexcept
{
    // Bare STEP compounds without a semantic sidecar carry no per-component
    // identity of their own.  Colouring by connected display group keeps the
    // housing, optics and interior reservations distinguishable instead of
    // collapsing the whole model into one material tone.  An AP242 identity
    // that resolved to a single component distinguishes nothing either.
    return groupCount_ > 1
        && !(mesh_ && mesh_->assembly() && mesh_->assembly()->identityAvailable
             && mesh_->assembly()->components.size() > 1);
}


void MeshProjectionView::setClearanceOverlay(bool enabled)
{
    if (clearanceOverlay_ == enabled) return;
    clearanceOverlay_ = enabled;
    // The overlay is derived from semantic component ranges and is painted in
    // screen space, so keep it on the complete CPU painter path.  It is an
    // inspection aid only; it does not alter source geometry or DRC results.
    rebuildScreenPoints();
    update();
}

void MeshProjectionView::setScenePalette(ScenePalette palette, double boardThicknessMm)
{
    scenePalette_ = palette;
    if (std::isfinite(boardThicknessMm) && boardThicknessMm > 0.0)
        boardThicknessMm_ = boardThicknessMm;
    rebuildPaintedTriangles();
    update();
}

void MeshProjectionView::setNavigationEnabled(bool enabled)
{
    navigationEnabled_ = enabled;
    dragMode_ = DragMode::None;
    orbitGuideVisible_ = false;
    setCursor(enabled ? Qt::OpenHandCursor : Qt::CrossCursor);
}

void MeshProjectionView::setScaleToConfirmedMm(double scale)
{
    scaleToConfirmedMm_ = std::isfinite(scale) && scale > 0.0 ? scale : 1.0;
}

void MeshProjectionView::setModelTransform(const QMatrix4x4& transform)
{
    modelTransform_ = transform;
    rebuildScreenPoints();
    update();
    emit viewChanged();
}

void MeshProjectionView::setMarkedVertices(const QVector<quint32>& vertices)
{
    markedVertices_ = vertices;
    update();
}

void MeshProjectionView::setInteractionPreview(bool enabled)
{
    if (interactionPreview_ == enabled) return;
    interactionPreview_ = enabled;
    if (!gpuActive()) rebuildPaintedTriangles();
    update();
}

QString MeshProjectionView::projectionName() const
{
    if (customView_) return QStringLiteral("free orbit");
    switch (projection_) {
    case Projection::Front: return QStringLiteral("front");
    case Projection::Right: return QStringLiteral("right");
    case Projection::Top: return QStringLiteral("top");
    case Projection::Bottom: return QStringLiteral("bottom / underside");
    case Projection::Isometric: return QStringLiteral("isometric");
    }
    return QStringLiteral("unknown");
}

QVector3D MeshProjectionView::canonicalPoint(const QVector3D& point) const
{
    // Keep the source centre as the visual origin. Translation in the placement
    // matrix therefore remains visible instead of being cancelled by refitting,
    // while rotation occurs in the component's authoring coordinate system.
    const QVector3D centered = modelTransform_.map(point) - meshCenterMm_;
    // The enclosure GLB route uses glTF's Y-up convention. PCB and mechanical
    // STEP use Z-up, so remap them into the same camera coordinate convention.
    return upAxis_ == UpAxis::Z
        ? QVector3D(centered.x(), centered.z(), -centered.y())
        : centered;
}

QVector3D MeshProjectionView::cameraPoint(const QVector3D& point) const
{
    return cameraOrientation_.rotatedVector(canonicalPoint(point));
}

QPointF MeshProjectionView::screenPoint(const QVector3D& camera) const
{
    const double scale = viewScalePxPerMm_ * zoomFactor_;
    return QPointF(width() * 0.5 + panOffsetPx_.x() + camera.x() * scale,
                   height() * 0.5 + panOffsetPx_.y() - camera.y() * scale);
}

void MeshProjectionView::fitView()
{
    zoomFactor_ = 1.0;
    panOffsetPx_ = {};
    viewScalePxPerMm_ = 1.0;
    if (mesh_ && !mesh_->verticesMm().isEmpty() && width() > 0 && height() > 0) {
        double minX = std::numeric_limits<double>::max();
        double minY = std::numeric_limits<double>::max();
        double maxX = std::numeric_limits<double>::lowest();
        double maxY = std::numeric_limits<double>::lowest();
        for (int vertexIndex = 0; vertexIndex < mesh_->verticesMm().size(); ++vertexIndex) {
            const QVector3D camera = cameraPoint(displayVertex(vertexIndex));
            minX = std::min(minX, double(camera.x()));
            minY = std::min(minY, double(camera.y()));
            maxX = std::max(maxX, double(camera.x()));
            maxY = std::max(maxY, double(camera.y()));
        }
        const double spanX = std::max(1e-6, maxX - minX);
        const double spanY = std::max(1e-6, maxY - minY);
        viewScalePxPerMm_ = std::max(1e-9,
            std::min(std::max(1.0, width() - 2.0 * PaddingPx) / spanX,
                     std::max(1.0, height() - 2.0 * PaddingPx) / spanY));
        panOffsetPx_.setX(-(minX + maxX) * 0.5 * viewScalePxPerMm_);
        panOffsetPx_.setY((minY + maxY) * 0.5 * viewScalePxPerMm_);
    }
    rebuildScreenPoints();
    update();
    emit viewChanged();
}

void MeshProjectionView::rebuildScreenPoints(bool forceSoftware)
{
    screenPoints_.clear();
    cameraPoints_.clear();
    paintedTriangles_.clear();
    if (!forceSoftware && gpuActive()) return;
    if (!mesh_ || mesh_->verticesMm().isEmpty() || width() <= 0 || height() <= 0) return;
    screenPoints_.reserve(mesh_->verticesMm().size());
    cameraPoints_.reserve(mesh_->verticesMm().size());
    for (int vertexIndex = 0; vertexIndex < mesh_->verticesMm().size(); ++vertexIndex) {
        const QVector3D camera = cameraPoint(displayVertex(vertexIndex));
        cameraPoints_.push_back(camera);
        screenPoints_.push_back(screenPoint(camera));
    }
    rebuildPaintedTriangles();
}

void MeshProjectionView::rebuildPaintedTriangles()
{
    if (!mesh_) return;
    const QVector<quint32>& indices = mesh_->triangleIndices();
    const qsizetype triangleCount = std::min(indices.size() / 3, paintedTriangleLimit());
    paintedTriangles_.reserve(triangleCount);
    const QVector3D lightDirection = QVector3D(0.35f, 0.55f, 0.76f).normalized();
    for (qsizetype triangle = 0; triangle < triangleCount; ++triangle) {
        const quint32 a = indices.at(triangle * 3);
        const quint32 b = indices.at(triangle * 3 + 1);
        const quint32 c = indices.at(triangle * 3 + 2);
        if (a >= quint32(cameraPoints_.size()) || b >= quint32(cameraPoints_.size())
            || c >= quint32(cameraPoints_.size())) continue;
        if (sectionMode_) {
            const float sourceZ = (mesh_->verticesMm().at(a).z()
                                   + mesh_->verticesMm().at(b).z()
                                   + mesh_->verticesMm().at(c).z()) / 3.0f;
            // The source PCB contract is 1.6 mm. Keep the dielectric and the
            // lower half of pads/vias, while hiding the component bodies above
            // the cut.  This is an inspection slice, never a geometry edit.
            if (sourceZ > float(boardThicknessMm_ * 0.56)) continue;
        }
        const QVector3D ab = cameraPoints_.at(b) - cameraPoints_.at(a);
        const QVector3D ac = cameraPoints_.at(c) - cameraPoints_.at(a);
        QVector3D normal = QVector3D::crossProduct(ab, ac);
        if (normal.lengthSquared() <= 1e-16f) continue;
        normal.normalize();
        const float diffuse = std::abs(QVector3D::dotProduct(normal, lightDirection));
        quint8 material = scenePalette_ == ScenePalette::PcbAssembly
            ? triangleMaterial(triangle, a, b, c) : 0;
        if (scenePalette_ == ScenePalette::PcbAssembly
            && !componentBodiesVisible_ && material >= 3)
            continue;
        const quint32 group = !vertexGroup_.isEmpty() && a < quint32(vertexGroup_.size())
            ? vertexGroup_.at(int(a)) : 0u;
        paintedTriangles_.push_back({a, b, c, triangle,
            (cameraPoints_.at(a).z() + cameraPoints_.at(b).z()
             + cameraPoints_.at(c).z()) / 3.0f,
            0.30f + diffuse * 0.70f, material, group});
    }
    const auto componentOnViewSide = [this](const PaintedTriangle& triangle) {
        if (scenePalette_ != ScenePalette::PcbAssembly || triangle.material < 3
            || !mesh_) return false;
        const QVector<quint32>& indices = mesh_->triangleIndices();
        const qsizetype offset = triangle.sourceTriangle * 3;
        if (offset < 0 || offset + 2 >= indices.size()) return false;
        const quint32 a = indices.at(offset);
        const quint32 b = indices.at(offset + 1);
        const quint32 c = indices.at(offset + 2);
        if (a >= quint32(mesh_->verticesMm().size())
            || b >= quint32(mesh_->verticesMm().size())
            || c >= quint32(mesh_->verticesMm().size())) return false;
        const float sourceZ = (mesh_->verticesMm().at(a).z()
                               + mesh_->verticesMm().at(b).z()
                               + mesh_->verticesMm().at(c).z()) / 3.0f;
        // A painter cannot resolve all of the board/component intersections
        // that a depth buffer can.  Bias only bodies on the camera-facing
        // side so the software fallback remains a useful assembly preview;
        // the underside projection uses the bottom-side bodies instead.
        return projection_ == Projection::Bottom
            ? sourceZ < -0.05f
            : sourceZ > float(boardThicknessMm_ + 0.02);
    };
    std::stable_sort(paintedTriangles_.begin(), paintedTriangles_.end(),
                     [&componentOnViewSide](const PaintedTriangle& a,
                                            const PaintedTriangle& b) {
                         const bool aFront = componentOnViewSide(a);
                         const bool bFront = componentOnViewSide(b);
                         if (aFront != bFront) return !aFront && bFront;
                         // The camera looks toward +Z in this projection, so
                         // larger camera-Z faces are farther away and must be
                         // painted first for the painter's algorithm.
                         return a.depth > b.depth;
                     });
}

qsizetype MeshProjectionView::paintedTriangleLimit() const noexcept
{
    Q_UNUSED(interactionPreview_);
    // A partial index stream produces holes and flicker because STEP triangles
    // are not ordered as a spatially decimated surface.  The accelerated path
    // renders all triangles; the software fallback must preserve that same
    // complete topology for visual correctness.
    return std::numeric_limits<qsizetype>::max() / 3;
}

void MeshProjectionView::paintSoftware(QPainter& painter)
{
    if (mesh_ && screenPoints_.size() != mesh_->verticesMm().size()) rebuildScreenPoints();
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.fillRect(rect(), QColor(17, 22, 28));

    painter.setPen(QColor(180, 190, 204));
    painter.drawText(QRect(14, 10, width() - 28, 24), Qt::AlignLeft,
                     projectionName().toUpper() + QStringLiteral(" · interactive STEP/CAD view"));
    if (!mesh_ || screenPoints_.isEmpty()) {
        painter.setPen(QColor(139, 148, 158));
        painter.drawText(rect(), Qt::AlignCenter,
                         QStringLiteral("Load a CAD mesh to inspect the model."));
        return;
    }

    if (displayMode_ != DisplayMode::Wireframe) {
        const QPen edgePen = displayMode_ == DisplayMode::ShadedEdges
            ? QPen(QColor(9, 18, 24, 125), 0.7) : QPen(Qt::NoPen);
        painter.setPen(edgePen);
        for (const PaintedTriangle& triangle : paintedTriangles_) {
            QPolygonF polygon;
            polygon << screenPoints_.at(triangle.a)
                    << screenPoints_.at(triangle.b)
                    << screenPoints_.at(triangle.c);
            QColor brush;
            if (groupedColoringActive())
                brush = shadedGroupColor(triangle.group, triangle.light);
            else if (scenePalette_ == ScenePalette::PcbAssembly)
                brush = (triangle.material >= 3 && int(triangle.material) - 3 < semanticColors_.size()
                       ? QColor::fromRgbF(semanticColors_.at(int(triangle.material) - 3).x(),
                                          semanticColors_.at(int(triangle.material) - 3).y(),
                                          semanticColors_.at(int(triangle.material) - 3).z())
                       : shadedPcbColor(triangle.material, triangle.light));
            else
                brush = shadedCadColor(triangle.light);
            if (triangleSelected(triangle.sourceTriangle))
                brush = brush.lighter(155);
            else if (scenePalette_ == ScenePalette::PcbAssembly
                     && triangle.material >= 3 && int(triangle.material) - 3 < semanticColors_.size()) {
                const float light = triangle.light;
                brush = QColor::fromRgbF(std::clamp(brush.redF() * (0.45f + 0.55f * light), 0.0f, 1.0f),
                                         std::clamp(brush.greenF() * (0.45f + 0.55f * light), 0.0f, 1.0f),
                                         std::clamp(brush.blueF() * (0.45f + 0.55f * light), 0.0f, 1.0f));
            }
            painter.setBrush(brush);
            painter.drawPolygon(polygon);
        }
    } else {
        painter.setBrush(Qt::NoBrush);
        painter.setPen(QPen(QColor(70, 168, 255, 180), 1.0));
        QPainterPath path;
        for (const PaintedTriangle& triangle : paintedTriangles_) {
            path.moveTo(screenPoints_.at(triangle.a));
            path.lineTo(screenPoints_.at(triangle.b));
            path.lineTo(screenPoints_.at(triangle.c));
            path.closeSubpath();
        }
        painter.drawPath(path);
    }

    if (clearanceOverlay_ && scenePalette_ == ScenePalette::PcbAssembly)
        paintClearanceOverlay(painter);

    painter.setPen(QPen(QColor(255, 205, 80), 2));
    painter.setBrush(QColor(255, 205, 80, 80));
    for (quint32 index : markedVertices_) {
        if (index >= quint32(screenPoints_.size())) continue;
        painter.drawEllipse(screenPoints_.at(index), 5.0, 5.0);
    }

    // Screen-space XYZ triad follows the camera so orientation remains obvious.
    const QPointF triadOrigin(54.0, height() - 50.0);
    const struct Axis { QVector3D world; QColor color; const char* label; } axes[] = {
        {QVector3D(1, 0, 0), QColor("#ff5c5c"), "X"},
        {QVector3D(0, 1, 0), QColor("#65d46e"), "Y"},
        {QVector3D(0, 0, 1), QColor("#58a6ff"), "Z"},
    };
    for (const Axis& axis : axes) {
        const QVector3D canonical = upAxis_ == UpAxis::Z
            ? QVector3D(axis.world.x(), axis.world.z(), -axis.world.y()) : axis.world;
        const QVector3D camera = cameraOrientation_.rotatedVector(canonical).normalized();
        const QPointF end = triadOrigin + QPointF(camera.x() * 28.0, -camera.y() * 28.0);
        painter.setPen(QPen(axis.color, 2.2));
        painter.drawLine(triadOrigin, end);
        painter.drawText(end + QPointF(3, -3), QString::fromLatin1(axis.label));
    }

    if (orbitGuideVisible_) {
        painter.setBrush(Qt::NoBrush);
        painter.setPen(QPen(QColor(88, 166, 255, 170), 1.2, Qt::DashLine));
        painter.drawEllipse(orbitGuideCenter_, 55, 55);
        painter.drawLine(orbitGuideCenter_ + QPointF(-65, 0),
                         orbitGuideCenter_ + QPointF(65, 0));
        painter.drawLine(orbitGuideCenter_ + QPointF(0, -65),
                         orbitGuideCenter_ + QPointF(0, 65));
    }

    painter.setPen(QColor(146, 157, 171));
    const QString controls = navigationEnabled_
        ? QStringLiteral("Left drag / Shift+Right drag: orbit   Right/Middle drag: pan   "
                         "Ctrl+Right drag or wheel: zoom   Double-click/Home: fit")
        : QStringLiteral("Click a mesh vertex to place an evidence marker");
    painter.drawText(QRect(14, height() - 30, width() - 28, 20),
                     Qt::AlignRight | Qt::AlignVCenter, controls);

}

void MeshProjectionView::paintClearanceOverlay(QPainter& painter)
{
    if (!mesh_ || !mesh_->assembly() || !mesh_->assembly()->identityAvailable) {
        painter.setPen(QColor(245, 190, 66));
        painter.drawText(QRect(14, 36, width() - 28, 22), Qt::AlignLeft,
                         QStringLiteral("CLEARANCE OVERLAY unavailable · AP242 component identity not loaded"));
        return;
    }

    // The semantic sidecar owns triangle ranges for each component.  Project
    // those ranges into the current camera and inflate their screen boxes by
    // a small review margin.  This is deliberately a visual collision/keepout
    // cue, not a numeric clearance or manufacturing decision.
    const QVector<quint32>& indices = mesh_->triangleIndices();
    painter.setFont(QFont(QStringLiteral("Sans"), 8));
    for (const SemanticAssemblyComponent& component : mesh_->assembly()->components) {
        if (!component.name.startsWith(QStringLiteral("Component_"))) continue;
        const qsizetype first = std::max<qsizetype>(0, component.triangleStart);
        const qsizetype last = std::min<qsizetype>(indices.size() / 3,
                                                    component.triangleStart
                                                        + component.triangleCount);
        if (first >= last) continue;
        QRectF bounds;
        bool havePoint = false;
        for (qsizetype triangle = first; triangle < last; ++triangle) {
            for (int corner = 0; corner < 3; ++corner) {
                const qsizetype offset = triangle * 3 + corner;
                if (offset >= indices.size()) continue;
                const quint32 vertex = indices.at(offset);
                if (vertex >= quint32(cameraPoints_.size())) continue;
                const QPointF point = screenPoints_.at(vertex);
                if (!havePoint) {
                    bounds = QRectF(point, QSizeF(0, 0));
                    havePoint = true;
                } else {
                    bounds |= QRectF(point, QSizeF(0, 0));
                }
            }
        }
        if (!havePoint) continue;
        bounds.adjust(-6.0, -6.0, 6.0, 6.0);
        const QColor color = component.color.isValid()
            ? component.color.lighter(125) : QColor(255, 196, 72);
        painter.setPen(QPen(color, 1.2, Qt::DashLine));
        painter.setBrush(Qt::NoBrush);
        painter.drawRoundedRect(bounds, 3.0, 3.0);
        painter.setPen(color);
        const QString label = component.referenceDesignator.isEmpty()
            ? component.name : component.referenceDesignator;
        painter.drawText(bounds.topLeft() + QPointF(2.0, -3.0), label);
    }
    painter.setPen(QColor(245, 190, 66));
    painter.drawText(QRect(14, 36, width() - 28, 22), Qt::AlignLeft,
                     QStringLiteral("CLEARANCE OVERLAY · visual review margin only · not DRC"));
}

void MeshProjectionView::updateDragCursor()
{
    switch (dragMode_) {
    case DragMode::Orbit: setCursor(Qt::SizeAllCursor); break;
    case DragMode::Pan: setCursor(Qt::ClosedHandCursor); break;
    case DragMode::Zoom: setCursor(Qt::SizeVerCursor); break;
    case DragMode::None:
        setCursor(navigationEnabled_ ? Qt::OpenHandCursor : Qt::CrossCursor);
        break;
    }
}

void MeshProjectionView::mousePressEvent(QMouseEvent* event)
{
    setFocus(Qt::MouseFocusReason);
    dragMoved_ = false;
    dragStart_ = dragLast_ = event->pos();
    if (navigationEnabled_) {
        if (event->button() == Qt::LeftButton
            || (event->button() == Qt::RightButton
                && event->modifiers().testFlag(Qt::ShiftModifier))) {
            dragMode_ = DragMode::Orbit;
            orbitGuideVisible_ = event->button() == Qt::RightButton;
            orbitGuideCenter_ = event->position();
        } else if (event->button() == Qt::RightButton
                   && event->modifiers().testFlag(Qt::ControlModifier)) {
            dragMode_ = DragMode::Zoom;
        } else if (event->button() == Qt::RightButton
                   || event->button() == Qt::MiddleButton) {
            dragMode_ = DragMode::Pan;
        }
        if (dragMode_ != DragMode::None) {
            setInteractionPreview(true);
            updateDragCursor();
            event->accept();
            update();
            return;
        }
    }

    if (event->button() != Qt::LeftButton || !mesh_ || screenPoints_.isEmpty()) return;
    qsizetype nearest = -1;
    double bestSquared = PickRadiusPx * PickRadiusPx;
    for (qsizetype i = 0; i < screenPoints_.size(); ++i) {
        const QPointF delta = screenPoints_.at(i) - event->position();
        const double squared = delta.x() * delta.x() + delta.y() * delta.y();
        if (squared <= bestSquared) { bestSquared = squared; nearest = i; }
    }
    if (nearest < 0) return;
    if (mesh_->assembly() && mesh_->assembly()->identityAvailable) {
        const QString semanticId = mesh_->assembly()->componentForVertex(
            quint32(nearest), mesh_->triangleIndices());
        if (!semanticId.isEmpty()) {
            const int component = mesh_->assembly()->componentIndex(semanticId);
            emit componentPicked(semanticId, component >= 0
                                     ? mesh_->assembly()->components.at(component).referenceDesignator
                                     : QString());
        }
    }
    emit vertexPicked(quint32(nearest),
                      modelTransform_.map(mesh_->verticesMm().at(nearest))
                          * float(scaleToConfirmedMm_),
                      projectionName());
}

void MeshProjectionView::mouseMoveEvent(QMouseEvent* event)
{
    if (dragMode_ == DragMode::None) return;
    const QPoint delta = event->pos() - dragLast_;
    dragLast_ = event->pos();
    const QPoint total = event->pos() - dragStart_;
    if (total.x() * total.x() + total.y() * total.y() > DragThresholdSquared)
        dragMoved_ = true;
    if (delta.isNull()) return;

    if (dragMode_ == DragMode::Orbit) {
        const QQuaternion yaw = QQuaternion::fromAxisAndAngle(
            QVector3D(0, 1, 0), float(delta.x() * OrbitDegreesPerPixel));
        const QQuaternion pitch = QQuaternion::fromAxisAndAngle(
            QVector3D(1, 0, 0), float(delta.y() * OrbitDegreesPerPixel));
        cameraOrientation_ = (pitch * yaw * cameraOrientation_).normalized();
        customView_ = true;
        rebuildScreenPoints();
    } else if (dragMode_ == DragMode::Pan) {
        panOffsetPx_ += QPointF(delta);
        rebuildScreenPoints();
    } else if (dragMode_ == DragMode::Zoom) {
        zoomAt(event->position(), std::pow(1.01, -delta.y()));
    }
    update();
    emit viewChanged();
    event->accept();
}

void MeshProjectionView::mouseReleaseEvent(QMouseEvent* event)
{
    if (dragMode_ == DragMode::None) return;
    dragMode_ = DragMode::None;
    orbitGuideVisible_ = false;
    setInteractionPreview(false);
    updateDragCursor();
    update();
    event->accept();
}

void MeshProjectionView::mouseDoubleClickEvent(QMouseEvent* event)
{
    if (navigationEnabled_ && event->button() == Qt::LeftButton) {
        fitView();
        event->accept();
        return;
    }
    QWidget::mouseDoubleClickEvent(event);
}

void MeshProjectionView::zoomAt(const QPointF& cursor, double factor)
{
    if (!std::isfinite(factor) || factor <= 0.0) return;
    const double oldZoom = zoomFactor_;
    zoomFactor_ = std::clamp(oldZoom * factor, 0.05, 100.0);
    const double applied = zoomFactor_ / oldZoom;
    const QPointF relative = cursor - QPointF(width() * 0.5, height() * 0.5);
    panOffsetPx_ = relative - (relative - panOffsetPx_) * applied;
    rebuildScreenPoints();
}

void MeshProjectionView::wheelEvent(QWheelEvent* event)
{
    if (!navigationEnabled_) {
        QWidget::wheelEvent(event);
        return;
    }
    if (event->modifiers().testFlag(Qt::ShiftModifier)) {
        panOffsetPx_.rx() += event->angleDelta().y() * 0.35;
        rebuildScreenPoints();
    } else {
        zoomAt(event->position(), std::pow(1.0015, event->angleDelta().y()));
    }
    update();
    emit viewChanged();
    event->accept();
}

void MeshProjectionView::keyPressEvent(QKeyEvent* event)
{
    if (!navigationEnabled_) {
        QWidget::keyPressEvent(event);
        return;
    }
    switch (event->key()) {
    case Qt::Key_Home:
    case Qt::Key_F:
        fitView(); break;
    case Qt::Key_0:
    case Qt::Key_I:
        setProjection(Projection::Isometric); break;
    case Qt::Key_T:
        setProjection(Projection::Top); break;
    case Qt::Key_1:
        setProjection(Projection::Front); break;
    case Qt::Key_3:
        setProjection(Projection::Right); break;
    case Qt::Key_Plus:
    case Qt::Key_Equal:
        zoomAt(QPointF(width() / 2.0, height() / 2.0), 1.2); update(); break;
    case Qt::Key_Minus:
        zoomAt(QPointF(width() / 2.0, height() / 2.0), 1.0 / 1.2); update(); break;
    default:
        QWidget::keyPressEvent(event); return;
    }
    event->accept();
}

void MeshProjectionView::resizeEvent(QResizeEvent* event)
{
    if (softwareFallback_) softwareFallback_->setGeometry(rect());
    if (event->oldSize().width() <= 0 || event->oldSize().height() <= 0) fitView();
    else rebuildScreenPoints();
    QWidget::resizeEvent(event);
}

} // namespace designstudio
