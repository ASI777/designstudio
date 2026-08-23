#include <QApplication>
#include <algorithm>
#include <QCommandLineParser>
#include <QComboBox>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPixmap>
#include <QImage>
#include <QTimer>
#include <QTabWidget>
#include <QTableWidget>
#include <QPushButton>
#include <QLineEdit>
#include <QFrame>
#include <QLabel>
#include <QToolBar>
#include <QDockWidget>
#include <QMenuBar>
#include <QSet>
#include <QElapsedTimer>
#include <QThread>
#include "MainWindow.h"
#include "FootprintLibTab.h"
#include "Pcb3DView.h"
#include "PcbCanvas.h"
#include "SchematicView.h"
#include "PhysicalDesignPanel.h"
#include "DesignFlowPanel.h"

namespace {

bool hasVisibleDesignContent(QWidget* widget)
{
    if (!widget || widget->width() <= 0 || widget->height() <= 0) return false;
    const QImage image = widget->grab().toImage().convertToFormat(QImage::Format_RGB32);
    QSet<QRgb> sampledColors;
    for (int y = 0; y < image.height(); y += 6)
        for (int x = 0; x < image.width(); x += 6)
            sampledColors.insert(image.pixel(x, y));
    return sampledColors.size() >= 24;
}

void waitForPcb3D(designstudio::Pcb3DView* view, int timeoutMs = 30000)
{
    if (!view) return;
    QElapsedTimer timer;
    timer.start();
    while (view->loadInProgress() && timer.elapsed() < timeoutMs) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 25);
        QThread::msleep(5);
    }
    QCoreApplication::processEvents(QEventLoop::AllEvents, 25);
}

} // namespace

int main(int argc, char* argv[]) {
    // The Footprint Lib tab embeds the real Inkscape by reparenting its X11
    // window into a Qt container — which only works when DesignStudio is itself
    // an X11 client. Under XWayland (DISPLAY set) force the xcb platform unless
    // the user has explicitly chosen one.
    if (qEnvironmentVariableIsEmpty("QT_QPA_PLATFORM") &&
        !qEnvironmentVariableIsEmpty("DISPLAY")) {
        qputenv("QT_QPA_PLATFORM", "xcb");
    }

    QApplication app(argc, argv);
    app.setApplicationName("DesignStudio");
    app.setApplicationVersion("1.0.0");
    app.setOrganizationName("DesignStudio");
    app.setOrganizationDomain("design-studio.local");

    QCommandLineParser parser;
    parser.setApplicationDescription("DesignStudio PCB/Schematic Editor");
    parser.addHelpOption();
    parser.addVersionOption();
    QCommandLineOption smokeOption(
        "smoke-test",
        "Construct and show the supported Qt application, process one event "
        "cycle, then exit. Intended for headless CI startup validation.");
    parser.addOption(smokeOption);
    QCommandLineOption uiContractOption(
        "ui-contract-test",
        "Verify the focused production navigation and contextual tool allowlists, then exit.");
    parser.addOption(uiContractOption);
    QCommandLineOption physicalDesignWorkflowOption(
        "physical-design-workflow-test",
        "Verify the eight-stage guided physical-design workflow and typed operation handoff, then exit.");
    parser.addOption(physicalDesignWorkflowOption);
    QCommandLineOption industrialDesignFlowOption(
        "industrial-design-flow-test",
        "Verify the visible industrial design flow, stage progression, and approval-gated prompts, then exit.");
    parser.addOption(industrialDesignFlowOption);
    QCommandLineOption demoCaptureOption(
        "demo-capture-dir",
        "Capture the fitted Schematic, PCB, PCB 3D, and Verification workspaces, then exit.",
        "directory");
    parser.addOption(demoCaptureOption);
    QCommandLineOption electronicsOnlyOption(
        "electronics-only",
        "Run as the FreeCAD electronics companion. Keeps PCB, schematic, footprint, "
        "routing, DRC, and analysis UI; omits enclosure and CAD-assist UI owned by FreeCAD.");
    parser.addOption(electronicsOnlyOption);
    QCommandLineOption cadAssistOption(
        "cad-assist",
        "Load and attach a validated CAD-assist evidence session after opening the project.",
        "contract");
    parser.addOption(cadAssistOption);
    QCommandLineOption enclosureGlbOption(
        "enclosure-glb",
        "Load a self-contained GLB into the enclosure concept workspace.",
        "glb");
    parser.addOption(enclosureGlbOption);
    QCommandLineOption meshEvidenceOutOption(
        "mesh-evidence-out",
        "Export mesh-evidence/1 JSON after loading --enclosure-glb.",
        "json");
    parser.addOption(meshEvidenceOutOption);
    QCommandLineOption verificationOutOption(
        "verification-out",
        "Run native unified verification and export design-studio.verification/1 JSON.",
        "json");
    parser.addOption(verificationOutOption);
    QCommandLineOption balancingSphereOption(
        "generate-balancing-sphere",
        "Generate the validated self-balancing sphere STEP demonstration package.",
        "directory");
    parser.addOption(balancingSphereOption);
    parser.addPositionalArgument("file", "Workspace directory/manifest or project file (.dsproj) to open");
    parser.process(app);

    // Dev self-test: DESIGNSTUDIO_DIMTEST=<svg> exercises the dimension overlay
    // paint path (import + showPadDimensions + grab → forces drawForeground) and
    // exits. Used to verify "Detect Dimensions" doesn't crash, headlessly.
    if (!qEnvironmentVariableIsEmpty("DESIGNSTUDIO_DIMTEST")) {
        const QString svg = qEnvironmentVariable("DESIGNSTUDIO_DIMTEST");
        FpCanvas canvas; canvas.resize(900, 700);
        QString err; canvas.importSvgEditable(svg, &err);
        const QString js = QFileInfo(svg).absolutePath() + "/" +
                           QFileInfo(svg).completeBaseName() + ".pads.json";
        QFile f(js);
        if (f.open(QIODevice::ReadOnly)) {
            canvas.showPadDimensions(QJsonDocument::fromJson(f.readAll()).object());
            f.close();
        }
        canvas.show();
        for (double z : {1.0, 0.05, 40.0}) {        // exercise extreme zooms too
            canvas.resetTransform(); canvas.scale(8.0 * z, -8.0 * z);
            QPixmap pm = canvas.grab();              // forces drawForeground
            pm.save(QString("/tmp/claude-1000/dimtest_z%1.png").arg(z));
        }
        qInfo("DIMTEST: rendered without crash"); return 0;
    }

    const bool electronicsOnly = parser.isSet(electronicsOnlyOption);
    MainWindow win(nullptr, electronicsOnly
                                ? MainWindow::UiProfile::FreeCadEmbedded
                                : MainWindow::UiProfile::Standalone);
    win.setNonInteractive(parser.isSet(smokeOption)
                          || parser.isSet(uiContractOption)
                          || parser.isSet(physicalDesignWorkflowOption)
                          || parser.isSet(industrialDesignFlowOption)
                          || parser.isSet(demoCaptureOption)
                          || parser.isSet(verificationOutOption));
    win.show();

    if (electronicsOnly
        && (parser.isSet(cadAssistOption) || parser.isSet(enclosureGlbOption)
            || parser.isSet(meshEvidenceOutOption) || parser.isSet(balancingSphereOption))) {
        qCritical("FREECAD_PROFILE_CONFLICT: mechanical CAD options are unavailable with --electronics-only");
        return 2;
    }
    if (electronicsOnly)
        qInfo("FREECAD_ELECTRONICS_PROFILE: schematic and PCB interfaces active; mechanical CAD delegated to FreeCAD");

    // Open file from command line
    bool startupOk = true;
    auto args = parser.positionalArguments();
    if (!args.isEmpty()) {
        const QFileInfo input(args.first());
        if (input.isDir() || input.fileName() == QStringLiteral("manifest.json")) {
            QString error;
            startupOk = win.openWorkspace(args.first(), &error);
            if (!startupOk) qCritical().noquote() << "WORKSPACE_REJECTED:" << error;
        } else if (parser.isSet(verificationOutOption)) {
            QString error;
            startupOk = win.openFileForVerification(args.first(), &error);
            if (!startupOk) qCritical().noquote() << "VERIFICATION_INPUT_REJECTED:" << error;
        } else {
            startupOk = win.openFile(args.first());
        }
    }
    if (parser.isSet(cadAssistOption)) {
        QString error;
        if (args.isEmpty()) {
            error = QStringLiteral("--cad-assist requires an open project positional argument");
            startupOk = false;
        } else if (startupOk
                   && !win.loadCadAssistContract(parser.value(cadAssistOption), &error)) {
            startupOk = false;
        }
        if (startupOk) {
            qInfo("CAD_ASSIST_CONNECTED: evidence validated against project identity, revision, and SHA-256");
        } else {
            qCritical().noquote() << "CAD_ASSIST_REJECTED:" << error;
        }
    }
    bool enclosureReady = false;
    if (parser.isSet(enclosureGlbOption)) {
        QString error;
        enclosureReady = win.loadEnclosureGlb(parser.value(enclosureGlbOption), &error);
        if (!enclosureReady) {
            startupOk = false;
            qCritical().noquote() << "ENCLOSURE_GLB_REJECTED:" << error;
        } else {
            qInfo("ENCLOSURE_GLB_CONNECTED: self-contained mesh validated and loaded");
        }
    }
    if (parser.isSet(meshEvidenceOutOption)) {
        QString error;
        bool evidenceOk = true;
        if (!parser.isSet(enclosureGlbOption)) {
            evidenceOk = false;
            error = QStringLiteral("--mesh-evidence-out requires --enclosure-glb");
        } else if (!enclosureReady) {
            evidenceOk = false;
            error = QStringLiteral("mesh evidence cannot be exported because GLB validation failed");
        } else if (!win.exportEnclosureEvidence(parser.value(meshEvidenceOutOption), &error)) {
            evidenceOk = false;
        }
        startupOk = startupOk && evidenceOk;
        if (evidenceOk)
            qInfo("MESH_EVIDENCE_EXPORTED: versioned GLB evidence written");
        else
            qCritical().noquote() << "MESH_EVIDENCE_REJECTED:" << error;
    }
    if (parser.isSet(verificationOutOption)) {
        QString error;
        bool verificationOk = !args.isEmpty() && startupOk;
        if (!verificationOk && error.isEmpty())
            error = QStringLiteral("--verification-out requires a valid project positional argument");
        if (verificationOk
            && !win.exportVerificationReport(parser.value(verificationOutOption), &error))
            verificationOk = false;
        startupOk = startupOk && verificationOk;
        if (verificationOk)
            qInfo("VERIFICATION_EXPORTED: unified native report written");
        else
            qCritical().noquote() << "VERIFICATION_REJECTED:" << error;
    }
    if (parser.isSet(balancingSphereOption)) {
        QString error;
        const bool generated = win.generateBalancingSphereDemo(
            parser.value(balancingSphereOption), &error);
        startupOk = startupOk && generated;
        if (generated)
            qInfo("BALANCING_SPHERE_GENERATED: STEP package and controller response validated");
        else
            qCritical().noquote() << "BALANCING_SPHERE_REJECTED:" << error;
    }

    if (parser.isSet(industrialDesignFlowOption)) {
        QTimer::singleShot(0, &app, [&app, &win, startupOk]() {
            bool ok = startupOk;
            auto* panel = win.findChild<DesignFlowPanel*>(
                QStringLiteral("industrial_design_flow"));
            auto* productInput = win.findChild<QLineEdit*>(
                QStringLiteral("industrial_flow_product_input"));
            auto* start = win.findChild<QPushButton*>(
                QStringLiteral("industrial_flow_start_button"));
            auto* resume = win.findChild<QPushButton*>(
                QStringLiteral("industrial_flow_resume_button"));
            const auto stages = win.findChildren<QFrame*>(
                QStringLiteral("industrial_flow_stage"));
            const auto statuses = win.findChildren<QLabel*>(
                QStringLiteral("industrial_flow_stage_status"));
            ok = ok && panel && productInput && start && resume
                && stages.size() == 8 && statuses.size() == 8;

            QString startPrompt;
            QString resumePrompt;
            if (panel) {
                QObject::connect(panel, &DesignFlowPanel::startRequested,
                    panel, [&](const QString& prompt) { startPrompt = prompt; });
                QObject::connect(panel, &DesignFlowPanel::resumeRequested,
                    panel, [&](const QString& prompt) { resumePrompt = prompt; });
            }
            if (productInput) productInput->setText(QStringLiteral(
                "industrial condition monitor enclosure and controller"));
            if (start) start->click();
            ok = ok && startPrompt.contains(QStringLiteral("requirements contract"))
                && startPrompt.contains(QStringLiteral("mechanical CAD"))
                && startPrompt.contains(QStringLiteral("exact electronics"))
                && startPrompt.contains(QStringLiteral("harness integration"))
                && startPrompt.contains(QStringLiteral("manufacturing release"))
                && statuses.size() == 8
                && statuses.at(0)->text() == QStringLiteral("Active");

            const QList<QString> tools{
                QStringLiteral("create_requirements_contract"),
                QStringLiteral("create_product_workspace"),
                QStringLiteral("apply_mechanical_stage"),
                QStringLiteral("build_electronics_from_datasheets"),
                QStringLiteral("apply_pcb_stage"),
                QStringLiteral("run_verification_checks"),
                QStringLiteral("generate_authoritative_drawings"),
                QStringLiteral("commit_configuration")};
            for (const QString& tool : tools) {
                if (!panel) break;
                panel->setToolPending(tool, QJsonObject{{QStringLiteral("test"), true}});
                panel->setToolResult(tool, true, QStringLiteral("test operation completed"));
            }
            for (QLabel* status : statuses)
                ok = ok && status->text() == QStringLiteral("Complete");
            if (resume) resume->click();
            ok = ok && resumePrompt.contains(QStringLiteral("first incomplete stage"))
                && resumePrompt.contains(QStringLiteral("approval-gated proposal"));
            qInfo().noquote() << (ok ? "INDUSTRIAL_DESIGN_FLOW_OK"
                                      : "INDUSTRIAL_DESIGN_FLOW_FAILED")
                              << "stages=" << stages.size()
                              << "completed="
                              << std::count_if(statuses.cbegin(), statuses.cend(),
                                  [](QLabel* status) {
                                      return status->text() == QStringLiteral("Complete");
                                  });
            app.exit(ok ? 0 : 1);
        });
    } else if (parser.isSet(physicalDesignWorkflowOption)) {
        QTimer::singleShot(0, &app, [&app, &win, startupOk]() {
            bool ok = startupOk;
            auto* panel = win.findChild<PhysicalDesignPanel*>(QStringLiteral("physical_design_panel"));
            auto* steps = win.findChild<QTabWidget*>(QStringLiteral("physical_design_steps"));
            const QStringList requiredPages{QStringLiteral("physical_step_evidence"),
                QStringLiteral("physical_step_calibration"), QStringLiteral("physical_step_workflow"),
                QStringLiteral("physical_step_locked_volumes"), QStringLiteral("physical_step_intent_process"),
                QStringLiteral("physical_step_candidates"), QStringLiteral("physical_step_grip_testing"),
                QStringLiteral("physical_step_local_redesign"), QStringLiteral("physical_step_pcb_topology"),
                QStringLiteral("physical_step_mechanical_system")};
            ok = ok && panel && steps && steps->count() == 10;
            for (const QString& name : requiredPages) ok = ok && panel && panel->findChild<QWidget*>(name);
            const QStringList requiredControls{QStringLiteral("physical_evidence_upload"),
                QStringLiteral("physical_known_length_mm"), QStringLiteral("physical_workflow_mode"),
                QStringLiteral("physical_locked_volumes"), QStringLiteral("physical_material"),
                QStringLiteral("physical_create_guided_session"),
                QStringLiteral("physical_candidate_comparison"), QStringLiteral("physical_export_grip_bucks"),
                QStringLiteral("physical_record_observation"), QStringLiteral("physical_capture_face_redesign"),
                QStringLiteral("physical_ask_codex_redesign"),
                QStringLiteral("physical_apply_redesign"), QStringLiteral("physical_modify_redesign"),
                QStringLiteral("physical_reject_redesign"), QStringLiteral("physical_rollback_redesign"),
                QStringLiteral("physical_generate_topology_study"),
                QStringLiteral("physical_topology_candidates"),
                QStringLiteral("physical_derive_mechanical_requirements"),
                QStringLiteral("physical_mechanical_requirements")};
            for (const QString& name : requiredControls) ok = ok && panel && panel->findChild<QWidget*>(name);
            auto* comparison = panel ? panel->findChild<QTableWidget*>(QStringLiteral("physical_candidate_comparison")) : nullptr;
            ok = ok && comparison && comparison->rowCount() == 3 && comparison->columnCount() == 7;
            if (comparison) {
                ok = ok && comparison->item(0, 0)->text() == QStringLiteral("compact")
                    && comparison->item(1, 0)->text() == QStringLiteral("balanced")
                    && comparison->item(2, 0)->text() == QStringLiteral("comfort");
                for (int row = 0; row < 3; ++row)
                    ok = ok && comparison->item(row, 6)->text() == QStringLiteral("provisional");
            }
            QString emittedOperation;
            QJsonObject emittedArguments;
            if (panel) QObject::connect(panel, &PhysicalDesignPanel::operationRequested,
                panel, [&](const QString& operation, const QJsonObject& arguments) {
                    emittedOperation = operation; emittedArguments = arguments;
                });
            auto* evidenceKind = panel ? panel->findChild<QComboBox*>(QStringLiteral("physical_evidence_kind")) : nullptr;
            auto* workflowMode = panel ? panel->findChild<QComboBox*>(QStringLiteral("physical_workflow_mode")) : nullptr;
            auto* volumeKind = panel ? panel->findChild<QComboBox*>(QStringLiteral("physical_locked_volume_kind")) : nullptr;
            ok = ok && evidenceKind && evidenceKind->findText(QStringLiteral("object_photo")) >= 0
                && evidenceKind->findText(QStringLiteral("hand_sketch")) >= 0;
            ok = ok && workflowMode && workflowMode->count() == 3;
            ok = ok && volumeKind && volumeKind->findText(QStringLiteral("pcb")) >= 0
                && volumeKind->findText(QStringLiteral("battery")) >= 0
                && volumeKind->findText(QStringLiteral("human_clearance")) >= 0;
            QString codexRedesignPrompt;
            if (panel) QObject::connect(panel, &PhysicalDesignPanel::codexPromptRequested,
                panel, [&](const QString& prompt) { codexRedesignPrompt = prompt; });
            auto* faceIntent = panel ? panel->findChild<QLineEdit*>(QStringLiteral("physical_face_intent")) : nullptr;
            auto* faceId = panel ? panel->findChild<QLineEdit*>(QStringLiteral("physical_face_id")) : nullptr;
            auto* captureRedesign = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_capture_face_redesign")) : nullptr;
            auto* askCodex = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_ask_codex_redesign")) : nullptr;
            auto* applyRedesign = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_apply_redesign")) : nullptr;
            auto* modifyRedesign = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_modify_redesign")) : nullptr;
            auto* rejectRedesign = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_reject_redesign")) : nullptr;
            auto* rollbackRedesign = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_rollback_redesign")) : nullptr;
            ok = ok && faceIntent && faceId && captureRedesign && askCodex && applyRedesign
                && modifyRedesign && rejectRedesign && rollbackRedesign
                && !askCodex->isEnabled() && !applyRedesign->isEnabled()
                && !modifyRedesign->isEnabled() && !rejectRedesign->isEnabled()
                && !rollbackRedesign->isEnabled();
            if (faceIntent) faceIntent->setText(QStringLiteral("Soften the selected grip surface"));
            if (captureRedesign) captureRedesign->click();
            ok = ok && emittedOperation == QStringLiteral("capture_local_redesign_v2")
                && emittedArguments.value(QStringLiteral("intent")).toString()
                    == QStringLiteral("Soften the selected grip surface")
                && emittedArguments.value(QStringLiteral("manufacturing")).toObject()
                       .value(QStringLiteral("minimum_blend_radius_mm")).toDouble() == 1.2;
            if (panel) panel->setOperationResult(QStringLiteral("capture_local_redesign_v2"), true,
                QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("message"), QStringLiteral("captured")},
                    {QStringLiteral("data"), QJsonObject{{QStringLiteral("path"), QStringLiteral("contracts/local-v2.json")},
                        {QStringLiteral("contract"), QJsonObject{{QStringLiteral("target"),
                            QJsonObject{{QStringLiteral("selected_faces"), QJsonArray{QStringLiteral("Face2")}}}}}}}}});
            ok = ok && askCodex && askCodex->isEnabled()
                && faceId && faceId->text() == QStringLiteral("Face2");
            if (askCodex) askCodex->click();
            ok = ok && codexRedesignPrompt.contains(QStringLiteral("contracts/local-v2.json"))
                && codexRedesignPrompt.contains(QStringLiteral("Do not commit"));
            if (panel) panel->setOperationResult(QStringLiteral("preview_local_redesign_v2"), true,
                QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("message"), QStringLiteral("previewed")},
                    {QStringLiteral("data"), QJsonObject{{QStringLiteral("receipt_path"), QStringLiteral("contracts/preview-v2.json")}}}});
            ok = ok && applyRedesign && applyRedesign->isEnabled()
                && modifyRedesign && modifyRedesign->isEnabled()
                && rejectRedesign && rejectRedesign->isEnabled();
            codexRedesignPrompt.clear();
            emittedOperation.clear();
            if (modifyRedesign) modifyRedesign->click();
            ok = ok && emittedOperation.isEmpty();
            if (faceIntent) faceIntent->setText(QStringLiteral(
                "Soften the grip surface and increase the blend radius"));
            if (modifyRedesign) modifyRedesign->click();
            ok = ok && emittedOperation == QStringLiteral("discard_local_redesign_v2")
                && emittedArguments.value(QStringLiteral("preview_receipt_path")).toString()
                    == QStringLiteral("contracts/preview-v2.json");
            if (panel) panel->setOperationResult(QStringLiteral("discard_local_redesign_v2"), true,
                QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("message"), QStringLiteral("discarded")},
                    {QStringLiteral("data"), QJsonObject{{QStringLiteral("receipt_path"), QStringLiteral("contracts/discard-v2.json")}}}});
            ok = ok && codexRedesignPrompt.contains(QStringLiteral("contracts/local-v2.json"));
            if (panel) panel->setOperationResult(QStringLiteral("preview_local_redesign_v2"), true,
                QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("message"), QStringLiteral("previewed")},
                    {QStringLiteral("data"), QJsonObject{{QStringLiteral("receipt_path"), QStringLiteral("contracts/preview-v2b.json")}}}});
            if (applyRedesign) applyRedesign->click();
            ok = ok && emittedOperation == QStringLiteral("commit_local_redesign_v2");
            if (panel) panel->setOperationResult(QStringLiteral("commit_local_redesign_v2"), true,
                QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("message"), QStringLiteral("committed")},
                    {QStringLiteral("data"), QJsonObject{{QStringLiteral("receipt_path"), QStringLiteral("contracts/commit-v2.json")}}}});
            ok = ok && rollbackRedesign && rollbackRedesign->isEnabled();
            if (rollbackRedesign) rollbackRedesign->click();
            ok = ok && emittedOperation == QStringLiteral("rollback_local_redesign_v2")
                && emittedArguments.value(QStringLiteral("commit_receipt_path")).toString()
                    == QStringLiteral("contracts/commit-v2.json");
            auto* createSession = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_create_guided_session")) : nullptr;
            if (createSession) createSession->click();
            ok = ok && emittedOperation == QStringLiteral("create_guided_physical_design_session")
                && emittedArguments.value(QStringLiteral("capture")).isObject();
            auto* sessionPath = panel ? panel->findChild<QLineEdit*>(QStringLiteral("physical_session_path")) : nullptr;
            auto* generate = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_generate_candidates")) : nullptr;
            if (sessionPath) sessionPath->setText(QStringLiteral("contracts/session.json"));
            if (generate) generate->click();
            ok = ok && emittedOperation == QStringLiteral("generate_constraint_candidates")
                && emittedArguments.value(QStringLiteral("session_path")).toString()
                    == QStringLiteral("contracts/session.json");
            if (panel) panel->setOperationResult(QStringLiteral("generate_constraint_candidates"), true,
                QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("data"), QJsonObject{
                    {QStringLiteral("candidates"), QJsonArray{
                        QJsonObject{{QStringLiteral("label"), QStringLiteral("precision")},
                            {QStringLiteral("status"), QStringLiteral("incomplete")},
                            {QStringLiteral("hard_failures"), QJsonArray{}},
                            {QStringLiteral("rank"), 1},
                            {QStringLiteral("analysis"), QJsonObject{
                                {QStringLiteral("reach"), QJsonObject{{QStringLiteral("status"), QStringLiteral("incomplete")}}},
                                {QStringLiteral("wall"), QJsonObject{{QStringLiteral("status"), QStringLiteral("pass")}}},
                                {QStringLiteral("manufacturability"), QJsonObject{{QStringLiteral("status"), QStringLiteral("incomplete")}}},
                                {QStringLiteral("mass"), QJsonObject{{QStringLiteral("status"), QStringLiteral("incomplete")}}},
                                {QStringLiteral("material_use"), QJsonObject{{QStringLiteral("status"), QStringLiteral("incomplete")}}}}}},
                        QJsonObject{{QStringLiteral("label"), QStringLiteral("grip")},
                            {QStringLiteral("status"), QStringLiteral("incomplete")},
                            {QStringLiteral("hard_failures"), QJsonArray{}},
                            {QStringLiteral("rank"), 2}, {QStringLiteral("analysis"), QJsonObject{}}},
                        QJsonObject{{QStringLiteral("label"), QStringLiteral("serviceable")},
                            {QStringLiteral("status"), QStringLiteral("incomplete")},
                            {QStringLiteral("hard_failures"), QJsonArray{}},
                            {QStringLiteral("rank"), 3}, {QStringLiteral("analysis"), QJsonObject{}}}}}}}});
            ok = ok && comparison && comparison->item(0, 0)->text() == QStringLiteral("precision")
                && comparison->item(1, 0)->text() == QStringLiteral("grip")
                && comparison->item(2, 0)->text() == QStringLiteral("serviceable")
                && comparison->item(0, 3)->text() == QStringLiteral("pass")
                && comparison->item(0, 6)->text() == QStringLiteral("provisional #1");
            auto* topologySource = panel ? panel->findChild<QLineEdit*>(QStringLiteral("physical_topology_source_path")) : nullptr;
            auto* topologyButton = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_generate_topology_study")) : nullptr;
            if (topologySource) topologySource->setText(QStringLiteral("contracts/topology-input.json"));
            if (topologyButton) topologyButton->click();
            ok = ok && emittedOperation == QStringLiteral("create_pcb_topology_study")
                && emittedArguments.value(QStringLiteral("source_path")).toString()
                    == QStringLiteral("contracts/topology-input.json");
            auto* mechanicsSource = panel ? panel->findChild<QLineEdit*>(QStringLiteral("physical_mechanical_source_path")) : nullptr;
            auto* mechanicsButton = panel ? panel->findChild<QPushButton*>(QStringLiteral("physical_derive_mechanical_requirements")) : nullptr;
            if (mechanicsSource) mechanicsSource->setText(QStringLiteral("contracts/mechanical-input.json"));
            if (mechanicsButton) mechanicsButton->click();
            ok = ok && emittedOperation == QStringLiteral("derive_mechanical_component_requirements")
                && emittedArguments.value(QStringLiteral("source_path")).toString()
                    == QStringLiteral("contracts/mechanical-input.json");
            qInfo().noquote() << (ok ? "PHYSICAL_DESIGN_WORKFLOW_OK" : "PHYSICAL_DESIGN_WORKFLOW_FAILED")
                              << "steps=" << (steps ? steps->count() : 0)
                              << "operation=" << emittedOperation;
            app.exit(ok ? 0 : 1);
        });
    } else if (parser.isSet(uiContractOption)) {
        QTimer::singleShot(0, &app, [&app, &win, &args, startupOk]() mutable {
            bool ok = startupOk;
            auto* tabs = qobject_cast<QTabWidget*>(win.centralWidget());
            const QStringList expectedTabs{QStringLiteral("Product Home"),
                QStringLiteral("Design Flow"), QStringLiteral("Build Map"), QStringLiteral("Mechanical"),
                QStringLiteral("Schematic"), QStringLiteral("PCB"),
                QStringLiteral("PCB 3D"), QStringLiteral("Physical Design"), QStringLiteral("Verification")};
            QStringList actualTabs;
            if (tabs) for (int i = 0; i < tabs->count(); ++i) actualTabs.append(tabs->tabText(i));
            ok = ok && actualTabs == expectedTabs;
            auto* buildMap = win.findChild<QWidget*>(QStringLiteral("build_map_panel"));
            ok = ok && buildMap
                && buildMap->findChild<QWidget*>(QStringLiteral("build_map_source_root"))
                && buildMap->findChild<QWidget*>(QStringLiteral("build_map_state"))
                && buildMap->findChild<QWidget*>(QStringLiteral("build_map_budget"))
                && buildMap->findChild<QWidget*>(QStringLiteral("build_map_gpu_gate"));
            auto* schematicSelector = win.findChild<QComboBox*>(
                QStringLiteral("schematic_subsystem_selector"));
            ok = ok && schematicSelector && schematicSelector->count() == 5;

            QStringList menus;
            for (QAction* action : win.menuBar()->actions()) menus.append(action->text().remove('&'));
            ok = ok && menus == QStringList{QStringLiteral("File"), QStringLiteral("Edit"),
                QStringLiteral("Product"), QStringLiteral("Verify"), QStringLiteral("View"),
                QStringLiteral("Help")};
            QMenu* viewMenu = nullptr;
            for (QAction* action : win.menuBar()->actions())
                if (action->text().remove('&') == QStringLiteral("View")) viewMenu = action->menu();
            QStringList viewActions;
            if (viewMenu)
                for (QAction* action : viewMenu->actions())
                    if (!action->isSeparator()) viewActions.append(action->text());
            ok = ok && viewActions.contains(QStringLiteral("Zoom In"))
                && viewActions.contains(QStringLiteral("Zoom Out"))
                && viewActions.contains(QStringLiteral("Fit Active Design to View"));

            auto* toolbar = win.findChild<QToolBar*>(QStringLiteral("toolbar_designstudio_context"));
            if (!toolbar || !tabs) ok = false;
            if (toolbar && tabs) {
                tabs->setCurrentIndex(expectedTabs.indexOf(QStringLiteral("PCB")));
                QStringList pcbActions;
                for (QAction* action : toolbar->actions())
                    if (!action->isSeparator()
                        && action->text() != QStringLiteral("Toggle Codex Side Panel"))
                        pcbActions.append(action->text());
                const QSet<QString> pcbAllowlist{QStringLiteral("Select"), QStringLiteral("Route Trace"),
                    QStringLiteral("Place Via"), QStringLiteral("Auto-route…"),
                    QStringLiteral("Board Setup…"), QStringLiteral("Copper Pour"),
                    QStringLiteral("Run Checks")};
                ok = ok && QSet<QString>(pcbActions.begin(), pcbActions.end()) == pcbAllowlist;
                tabs->setCurrentIndex(expectedTabs.indexOf(QStringLiteral("Schematic")));
                QStringList schematicActions;
                for (QAction* action : toolbar->actions())
                    if (!action->isSeparator()
                        && action->text() != QStringLiteral("Toggle Codex Side Panel"))
                        schematicActions.append(action->text());
                ok = ok && schematicActions == QStringList{
                                                            QStringLiteral("KiCad Symbol Library / Schematic…"),
                                                            QStringLiteral("Run ERC"),
                                                            QStringLiteral("Simulation")};
                tabs->setCurrentIndex(expectedTabs.indexOf(QStringLiteral("PCB 3D")));
                QStringList pcb3dActions;
                for (QAction* action : toolbar->actions())
                    if (!action->isSeparator()
                        && action->text() != QStringLiteral("Toggle Codex Side Panel"))
                        pcb3dActions.append(action->text());
                ok = ok && pcb3dActions == QStringList{
                    QStringLiteral("Fit Active Design to View")};
                auto* pcb3d = win.findChild<designstudio::Pcb3DView*>();
                if (!args.isEmpty()) {
                    waitForPcb3D(pcb3d);
                    ok = ok && pcb3d && !pcb3d->sourcePath().isEmpty()
                        && pcb3d->hasRenderableMesh();
                    if (pcb3d) {
                        const quint64 importsBeforeRefresh = pcb3d->importCount();
                        pcb3d->refreshFromProject();
                        ok = ok && pcb3d->importCount() == importsBeforeRefresh;
                    }
                    tabs->setCurrentIndex(expectedTabs.indexOf(QStringLiteral("Schematic")));
                    QCoreApplication::processEvents();
                    ok = ok && hasVisibleDesignContent(
                        win.findChild<SchematicView*>());
                    tabs->setCurrentIndex(expectedTabs.indexOf(QStringLiteral("PCB")));
                    QCoreApplication::processEvents();
                    ok = ok && hasVisibleDesignContent(win.findChild<PcbCanvas*>());
                }
            }
            auto* alternatives = win.findChild<QDockWidget*>(QStringLiteral("dock_design_alternatives"));
            ok = ok && alternatives && !alternatives->isVisible()
                && !alternatives->toggleViewAction()->isVisible();
            int visibleMainWindows = 0;
            for (QWidget* widget : QApplication::topLevelWidgets())
                if (widget->isVisible() && qobject_cast<QMainWindow*>(widget)) ++visibleMainWindows;
            ok = ok && visibleMainWindows == 1;
            qInfo().noquote() << (ok ? "UI_CONTRACT_OK" : "UI_CONTRACT_FAILED")
                              << "tabs=" << actualTabs.join(',')
                              << "menus=" << menus.join(',');
            app.exit(ok ? 0 : 1);
        });
    } else if (parser.isSet(demoCaptureOption)) {
        QTimer::singleShot(0, &app, [&app, &win, &args, &parser, startupOk]() {
            bool ok = startupOk && !args.isEmpty();
            QDir output(parser.value(QStringLiteral("demo-capture-dir")));
            if (!output.exists()) ok = output.mkpath(QStringLiteral("."));
            auto* tabs = qobject_cast<QTabWidget*>(win.centralWidget());
            const QStringList names{QStringLiteral("Schematic"), QStringLiteral("PCB"),
                                    QStringLiteral("PCB 3D"), QStringLiteral("Verification")};
            if (!tabs) ok = false;
            for (const QString& name : names) {
                int index = -1;
                if (tabs) {
                    for (int i = 0; i < tabs->count(); ++i)
                        if (tabs->tabText(i) == name) { index = i; break; }
                }
                if (index < 0) { ok = false; continue; }
                tabs->setCurrentIndex(index);
                QCoreApplication::processEvents();
                if (name == QStringLiteral("PCB 3D"))
                    waitForPcb3D(win.findChild<designstudio::Pcb3DView*>());
                QCoreApplication::processEvents();
                const QString fileName = name.toLower().replace(QLatin1Char(' '), QLatin1Char('-'))
                    + QStringLiteral(".png");
                ok = tabs->currentWidget()->grab().save(output.filePath(fileName)) && ok;
                if (name == QStringLiteral("Schematic")) {
                    auto* selector = win.findChild<QComboBox*>(
                        QStringLiteral("schematic_subsystem_selector"));
                    const QStringList scopes{
                        QStringLiteral("overview"),
                        QStringLiteral("human-interface"),
                        QStringLiteral("control-io"),
                        QStringLiteral("power"),
                        QStringLiteral("passives"),
                    };
                    if (!selector || selector->count() != scopes.size()) {
                        ok = false;
                    } else {
                        for (int scope = 1; scope < scopes.size(); ++scope) {
                            selector->setCurrentIndex(scope);
                            QCoreApplication::processEvents();
                            QCoreApplication::processEvents();
                            QWidget* schematic = tabs->currentWidget();
                            ok = hasVisibleDesignContent(schematic) && ok;
                            ok = schematic->grab().save(output.filePath(
                                QStringLiteral("schematic-") + scopes[scope]
                                + QStringLiteral(".png"))) && ok;
                        }
                        selector->setCurrentIndex(0);
                    }
                }
            }
            auto* pcb3d = win.findChild<designstudio::Pcb3DView*>();
            ok = ok && pcb3d && pcb3d->hasRenderableMesh();
            qInfo().noquote() << (ok ? "DEMO_CAPTURE_OK" : "DEMO_CAPTURE_FAILED")
                              << output.absolutePath();
            app.exit(ok ? 0 : 1);
        });
    } else if (parser.isSet(verificationOutOption)) {
        // Verification export is a command-line operation. Do not leave a
        // hidden GUI event loop running after the report has been written.
        QTimer::singleShot(0, &app, [&app, startupOk]() {
            app.exit(startupOk ? 0 : 1);
        });
    } else if (parser.isSet(smokeOption)) {
        QTimer::singleShot(0, &app, [&app, startupOk]() {
            if (startupOk)
                qInfo("QT_SMOKE_OK: DesignStudio main window initialized");
            app.exit(startupOk ? 0 : 1);
        });
    }

    return app.exec();
}
