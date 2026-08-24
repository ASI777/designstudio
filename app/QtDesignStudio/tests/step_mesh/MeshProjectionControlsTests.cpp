#include "GlbMeshReader.h"
#include "MeshProjectionView.h"
#include "SemanticAssembly.h"
#include "StepMeshReader.h"

#include <QApplication>
#include <QImage>
#include <QLineF>
#include <QMouseEvent>
#include <QSet>
#include <QWheelEvent>

#include <cmath>
#include <iostream>
#include <memory>

namespace {

bool check(bool condition, const char* message)
{
    if (condition) return true;
    std::cerr << "FAIL: " << message << '\n';
    return false;
}

std::shared_ptr<const designstudio::GlbMesh> boxMesh()
{
    QVector<QVector3D> vertices{
        {-10, -5, -0.8f}, {10, -5, -0.8f}, {10, 5, -0.8f}, {-10, 5, -0.8f},
        {-10, -5,  0.8f}, {10, -5,  0.8f}, {10, 5,  0.8f}, {-10, 5,  0.8f},
    };
    QVector<quint32> indices{
        0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5,
        2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7,
    };
    return std::make_shared<designstudio::GlbMesh>(
        std::move(vertices), std::move(indices), QVector3D(-10, -5, -0.8f),
        QVector3D(10, 5, 0.8f), QStringLiteral("fixture"),
        QStringLiteral("box.step"), 1);
}

std::shared_ptr<const designstudio::GlbMesh> semanticBoxMesh()
{
    const auto base = boxMesh();
    auto assembly = std::make_shared<designstudio::SemanticAssembly>();
    assembly->schema = QStringLiteral("design-studio.semantic-assembly/2");
    assembly->sourceFormat = QStringLiteral("AP242");
    assembly->identityAvailable = true;
    assembly->legacyFlattened = false;
    designstudio::SemanticAssemblyComponent first;
    first.semanticId = QStringLiteral("u1");
    first.referenceDesignator = QStringLiteral("U1");
    first.name = QStringLiteral("Controller U1");
    first.material = QStringLiteral("silicon");
    first.color = QColor("#d97706");
    first.triangleCount = 6;
    designstudio::SemanticAssemblyComponent second;
    second.semanticId = QStringLiteral("j1");
    second.referenceDesignator = QStringLiteral("J1");
    second.name = QStringLiteral("Connector J1");
    second.material = QStringLiteral("FR4");
    second.color = QColor("#2563eb");
    second.triangleStart = 6;
    second.triangleCount = 6;
    assembly->components = {first, second};
    return std::make_shared<const designstudio::GlbMesh>(
        base->verticesMm(), base->triangleIndices(), base->boundsMinMm(),
        base->boundsMaxMm(), base->sha256(), base->sourceName(), base->sourceBytes(),
        std::move(assembly));
}

void mouse(QWidget& widget, QEvent::Type type, const QPointF& point,
           Qt::MouseButton button, Qt::MouseButtons buttons,
           Qt::KeyboardModifiers modifiers = Qt::NoModifier)
{
    QMouseEvent event(type, point, point, point, button, buttons, modifiers);
    QApplication::sendEvent(&widget, &event);
}

void wheel(QWidget& widget, const QPointF& point, int angleDelta,
           Qt::KeyboardModifiers modifiers = Qt::NoModifier)
{
    QWheelEvent event(point, point, QPoint(), QPoint(0, angleDelta), Qt::NoButton,
                      modifiers, Qt::NoScrollPhase, false);
    QApplication::sendEvent(&widget, &event);
}

bool differentOrientation(const QQuaternion& a, const QQuaternion& b)
{
    return std::abs(QQuaternion::dotProduct(a.normalized(), b.normalized())) < 0.999f;
}

} // namespace

std::shared_ptr<const designstudio::GlbMesh> twoBoxMesh()
{
    // Two disjoint 10 mm solids separated by a 20 mm air gap: the minimal
    // stand-in for a housing plus an interior reservation.
    QVector<QVector3D> vertices;
    QVector<quint32> indices;
    const float centers[] = {-20.f, 20.f};
    for (int box = 0; box < 2; ++box) {
        const quint32 base = quint32(vertices.size());
        const float cx = centers[box];
        const float x0 = cx - 5, x1 = cx + 5;
        vertices.append({
            {x0, -5, -5}, {x1, -5, -5}, {x1, 5, -5}, {x0, 5, -5},
            {x0, -5,  5}, {x1, -5,  5}, {x1, 5,  5}, {x0, 5,  5},
        });
        indices.append({
            base+0, base+2, base+1, base+0, base+3, base+2,
            base+4, base+5, base+6, base+4, base+6, base+7,
            base+0, base+1, base+5, base+0, base+5, base+4,
            base+1, base+2, base+6, base+1, base+6, base+5,
            base+2, base+3, base+7, base+2, base+7, base+6,
            base+3, base+0, base+4, base+3, base+4, base+7,
        });
    }
    return std::make_shared<designstudio::GlbMesh>(
        std::move(vertices), std::move(indices), QVector3D(-25, -5, -5),
        QVector3D(25, 5, 5), QStringLiteral("fixture"),
        QStringLiteral("two-box.step"), 1);
}

bool verifyExplodedView()
{
    designstudio::MeshProjectionView view;
    view.resize(800, 600);
    view.setUpAxis(designstudio::MeshProjectionView::UpAxis::Z);
    view.setNavigationEnabled(true);
    view.setDisplayMode(designstudio::MeshProjectionView::DisplayMode::Shaded);
    view.setScenePalette(designstudio::MeshProjectionView::ScenePalette::PcbAssembly, 1.6);
    view.setMesh(twoBoxMesh());
    view.show();
    QApplication::processEvents();

    bool ok = check(view.componentGroupCount() == 2,
                    "disjoint STEP solids were not segmented into two display groups");

    const double fittedScale = view.viewScalePxPerMm();
    view.setExploded(true);
    ok = check(view.explodedView(), "exploded mode did not latch") && ok;
    ok = check(view.viewScalePxPerMm() < fittedScale,
               "exploded separation did not widen the fitted bounds") && ok;

    const QImage exploded = view.renderSoftwareSnapshot();
    QSet<QRgb> hues;
    for (int y = 0; y < exploded.height(); y += 4)
        for (int x = 0; x < exploded.width(); x += 4) {
            const QColor pixel = exploded.pixelColor(x, y);
            if (std::abs(pixel.red() - 17) + std::abs(pixel.green() - 22)
                    + std::abs(pixel.blue() - 28) > 60)
                hues.insert(pixel.rgb());
        }
    ok = check(hues.size() >= 8,
               "exploded groups did not render with distinct component hues") && ok;

    view.setExploded(false);
    ok = check(!view.explodedView()
                   && std::abs(view.viewScalePxPerMm() - fittedScale) < 1e-9,
               "leaving exploded mode did not restore the assembled fit") && ok;
    return ok;
}

int main(int argc, char** argv)
{
    QApplication application(argc, argv);
    std::shared_ptr<const designstudio::GlbMesh> mesh = semanticBoxMesh();
    if (application.arguments().size() >= 2) {
        const auto loaded = designstudio::StepMeshReader::loadFile(
            application.arguments().at(1), 0.2);
        if (!loaded.ok()) {
            std::cerr << loaded.error.toStdString() << '\n';
            return 2;
        }
        mesh = loaded.mesh;
    }
    designstudio::MeshProjectionView view;
    view.resize(800, 600);
    view.setUpAxis(designstudio::MeshProjectionView::UpAxis::Z);
    view.setNavigationEnabled(true);
    view.setDisplayMode(designstudio::MeshProjectionView::DisplayMode::Shaded);
    view.setScenePalette(designstudio::MeshProjectionView::ScenePalette::PcbAssembly, 1.6);
    view.setMesh(mesh);
    view.setProjection(designstudio::MeshProjectionView::Projection::Isometric);
    view.show();
    application.processEvents();

    bool ok = check(view.zoomFactor() == 1.0, "fit did not initialize zoom")
        && check(view.navigationEnabled(), "navigation mode was not enabled")
        && check(view.scenePalette()
                     == designstudio::MeshProjectionView::ScenePalette::PcbAssembly,
                 "PCB viewer palette was not retained")
        && check(view.upAxis() == designstudio::MeshProjectionView::UpAxis::Z,
                 "STEP viewer is not Z-up")
        && check(view.semanticIdentityAvailable(),
                 "semantic AP242 identity was not exposed to the viewport");
    view.setSelectedComponentReference(QStringLiteral("J1"));
    ok = check(view.selectedComponent() == QStringLiteral("j1"),
               "reference-designator selection did not map to semantic object") && ok;
    view.setSelectedComponent(QStringLiteral("u1"));
    ok = check(view.selectedComponent() == QStringLiteral("u1"),
               "semantic-object selection did not persist") && ok;
    const QQuaternion fittedOrientation = view.cameraOrientation();
    const QPointF fittedPan = view.panOffsetPx();

    mouse(view, QEvent::MouseButtonPress, {400, 300}, Qt::LeftButton, Qt::LeftButton);
    mouse(view, QEvent::MouseMove, {455, 330}, Qt::NoButton, Qt::LeftButton);
    mouse(view, QEvent::MouseButtonRelease, {455, 330}, Qt::LeftButton, Qt::NoButton);
    ok = check(differentOrientation(fittedOrientation, view.cameraOrientation()),
               "left-drag did not orbit") && ok;
    ok = check(view.projectionName() == QStringLiteral("free orbit"),
               "orbit did not enter free-camera state") && ok;

    const QPointF beforePan = view.panOffsetPx();
    mouse(view, QEvent::MouseButtonPress, {400, 300}, Qt::RightButton, Qt::RightButton);
    mouse(view, QEvent::MouseMove, {430, 325}, Qt::NoButton, Qt::RightButton);
    mouse(view, QEvent::MouseButtonRelease, {430, 325}, Qt::RightButton, Qt::NoButton);
    ok = check(QLineF(beforePan, view.panOffsetPx()).length() > 20.0,
               "right-drag did not pan") && ok;

    const double beforeZoom = view.zoomFactor();
    mouse(view, QEvent::MouseButtonPress, {400, 300}, Qt::RightButton, Qt::RightButton,
          Qt::ControlModifier);
    mouse(view, QEvent::MouseMove, {400, 250}, Qt::NoButton, Qt::RightButton,
          Qt::ControlModifier);
    mouse(view, QEvent::MouseButtonRelease, {400, 250}, Qt::RightButton, Qt::NoButton,
          Qt::ControlModifier);
    ok = check(view.zoomFactor() > beforeZoom, "Ctrl+right-drag up did not zoom in") && ok;

    const QQuaternion beforeAltiumOrbit = view.cameraOrientation();
    mouse(view, QEvent::MouseButtonPress, {400, 300}, Qt::RightButton, Qt::RightButton,
          Qt::ShiftModifier);
    mouse(view, QEvent::MouseMove, {365, 275}, Qt::NoButton, Qt::RightButton,
          Qt::ShiftModifier);
    mouse(view, QEvent::MouseButtonRelease, {365, 275}, Qt::RightButton, Qt::NoButton,
          Qt::ShiftModifier);
    ok = check(differentOrientation(beforeAltiumOrbit, view.cameraOrientation()),
               "Shift+right-drag did not orbit") && ok;

    const double beforeWheelZoom = view.zoomFactor();
    wheel(view, {460, 310}, 120);
    ok = check(view.zoomFactor() > beforeWheelZoom, "mouse wheel did not zoom") && ok;

    mouse(view, QEvent::MouseButtonDblClick, {400, 300}, Qt::LeftButton,
          Qt::LeftButton);
    ok = check(std::abs(view.zoomFactor() - 1.0) < 1e-9,
               "double-click did not fit the view") && ok;

    view.setProjection(designstudio::MeshProjectionView::Projection::Isometric);
    ok = check(!differentOrientation(fittedOrientation, view.cameraOrientation()),
               "standard isometric view was not restored") && ok;
    ok = check(std::abs(view.zoomFactor() - 1.0) < 1e-9
                   && QLineF(fittedPan, view.panOffsetPx()).length() < 0.01,
               "standard view did not fit and recenter the model") && ok;

    // Evidence exports must remain deterministic and complete regardless of
    // whether the interactive viewport is using OpenGL or its CPU fallback.
    QImage image = view.renderSoftwareSnapshot();
    int colored = 0;
    for (int y = 0; y < image.height(); y += 8)
        for (int x = 0; x < image.width(); x += 8) {
            const QColor pixel = image.pixelColor(x, y);
            // The selected semantic body may legitimately be orange, blue,
            // or another component colour. Detect rendered geometry against
            // the CAD background instead of coupling the regression to blue.
            if (std::abs(pixel.red() - 17) + std::abs(pixel.green() - 22)
                    + std::abs(pixel.blue() - 28) > 60)
                ++colored;
        }
    ok = check(colored > 100, "shaded CAD scene did not render") && ok;
    if (application.arguments().size() >= 3
        && !image.save(application.arguments().at(2))) {
        std::cerr << "FAIL: could not save rendered inspection image\n";
        ok = false;
    }
    ok = verifyExplodedView() && ok;
    return ok ? 0 : 1;
}
