#include "ProjectModel.h"
#include "ApplicationConcept.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QEventLoop>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTemporaryDir>
#include <QTimer>

#include <iostream>

namespace {

bool check(bool condition, const char* message)
{
    if (condition) return true;
    std::cerr << "FAIL: " << message << '\n';
    return false;
}

QJsonObject flatProject(double width, const QString& netName = QStringLiteral("GND"))
{
    return QJsonObject{
        {"version", 2},
        {"board_width_mm", width},
        {"board_height_mm", 80.0},
        {"grid_mm", 1.0},
        {"copper_layers", 2},
        {"nets", QJsonValue::Null},
        {"net_table", QJsonArray{QJsonObject{{"id", 0}, {"name", netName}, {"class", 0}}}},
        {"net_classes", QJsonArray{}},
        {"footprints", QJsonArray{}},
        {"traces", QJsonArray{}},
        {"vias", QJsonArray{}},
    };
}

bool writeJson(const QString& path, const QJsonObject& object)
{
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) return false;
    return file.write(QJsonDocument(object).toJson()) >= 0;
}

QByteArray readBytes(const QString& path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    return file.readAll();
}

bool rejectedFormatsPreserveStateAndFiles(const QString& dir)
{
    const QString activePath = dir + "/active.dsproj";
    const QString v3Path = dir + "/canonical-v3.dsproj";
    const QString unknownPath = dir + "/unknown.dsproj";
    const QString unknownVersionPath = dir + "/unknown-version.dsproj";
    const QString invalidPlacementPath = dir + "/invalid-placement.dsproj";
    const QString invalidRoutingPath = dir + "/invalid-routing.dsproj";
    const QString invalidMechanicalPath = dir + "/invalid-mechanical.dsproj";
    if (!writeJson(activePath, flatProject(123.0, "ACTIVE"))) return false;

    ProjectModel model;
    if (!check(model.loadFromFile(activePath), "seed v2 project should load")) return false;
    const QString originalPath = model.filePath();
    const double originalWidth = model.boardWidthMm;
    const QString originalNet = model.netName(0);

    QJsonObject embeddedBoard = flatProject(999.0);
    embeddedBoard["document_id"] = QStringLiteral("project:v3-verification-fixture");
    embeddedBoard["revision"] = 7;
    const QJsonObject canonicalV3{
        {"format", "design-studio.project/3"},
        {"units", "nm"},
        {"board", embeddedBoard},
        {"parts", QJsonArray{}},
    };
    if (!writeJson(v3Path, canonicalV3)) return false;
    const QByteArray v3Before = readBytes(v3Path);
    if (!check(!model.loadFromFile(v3Path), "canonical v3 must be rejected")) return false;
    if (!check(model.lastError().contains("project/3"), "v3 rejection should explain the format")) return false;
    if (!check(readBytes(v3Path) == v3Before, "rejected v3 file must remain byte-identical")) return false;
    if (!check(model.filePath() == originalPath && model.boardWidthMm == originalWidth
                   && model.netName(0) == originalNet,
               "v3 rejection must preserve the active in-memory model")) return false;

    ProjectModel verificationModel;
    if (!check(verificationModel.loadForVerification(v3Path),
               "verification-only adapter should load a canonical v3 board")) return false;
    if (!check(verificationModel.filePath() == v3Path
                   && verificationModel.boardWidthMm == 999.0
                   && verificationModel.documentId() == "project:v3-verification-fixture"
                   && verificationModel.revision() == 7,
               "verification adapter should expose the embedded board identity and geometry"))
        return false;
    const QString v3Digest = QString::fromLatin1(
        QCryptographicHash::hash(v3Before, QCryptographicHash::Sha256).toHex());
    if (!check(verificationModel.fileSha256() == v3Digest,
               "verification evidence must bind the exact outer v3 bytes")) return false;
    if (!check(!verificationModel.saveToFile(dir + "/must-not-flatten.dsproj")
                   && verificationModel.lastError().contains("read-only"),
               "verification-only v3 adapter must reject every save")) return false;

    const QJsonObject unknown{{"format", "vendor.project/12"}, {"payload", QJsonObject{}}};
    if (!writeJson(unknownPath, unknown)) return false;
    const QByteArray unknownBefore = readBytes(unknownPath);
    if (!check(!model.loadFromFile(unknownPath), "unknown named format must be rejected")) return false;
    if (!check(model.lastError().contains("Unsupported project format"),
               "unknown format rejection should be explicit")) return false;
    if (!check(readBytes(unknownPath) == unknownBefore, "unknown file must remain byte-identical")) return false;

    QJsonObject unknownVersion = flatProject(456.0);
    unknownVersion["version"] = 99;
    if (!writeJson(unknownVersionPath, unknownVersion)) return false;
    const QByteArray versionBefore = readBytes(unknownVersionPath);
    if (!check(!model.loadFromFile(unknownVersionPath), "unknown flat version must be rejected")) return false;
    if (!check(model.lastError().contains("version 99"), "unknown version should be named")) return false;
    if (!check(readBytes(unknownVersionPath) == versionBefore,
               "unknown-version file must remain byte-identical")) return false;
    QJsonObject invalidPlacement = flatProject(456.0);
    invalidPlacement["footprints"] = QJsonArray{QJsonObject{
        {"ref", "J1"}, {"placement", QJsonObject{{"edge_anchor", "diagonal"}}}}};
    if (!writeJson(invalidPlacementPath, invalidPlacement)) return false;
    const QByteArray placementBefore = readBytes(invalidPlacementPath);
    if (!check(!model.loadFromFile(invalidPlacementPath),
               "invalid placement constraints must be rejected")) return false;
    if (!check(model.lastError().contains("edge_anchor"),
               "invalid placement rejection should name the field")) return false;
    if (!check(readBytes(invalidPlacementPath) == placementBefore,
               "invalid-placement file must remain byte-identical")) return false;
    QJsonObject invalidRouting = flatProject(456.0);
    invalidRouting["layer_policies"] = QJsonArray{QJsonObject{
        {"layer", 8}, {"role", "plane"}, {"preferred_direction", "any"},
        {"allow_routing", false}, {"copper_thickness_mm", 0.035},
        {"source", "test"}, {"source_revision", "1"}}};
    if (!writeJson(invalidRoutingPath, invalidRouting)) return false;
    const QByteArray routingBefore = readBytes(invalidRoutingPath);
    if (!check(!model.loadFromFile(invalidRoutingPath),
               "invalid routing constraints must be rejected")) return false;
    if (!check(model.lastError().contains("layer policy"),
               "invalid routing rejection should identify the layer policy")) return false;
    if (!check(readBytes(invalidRoutingPath) == routingBefore,
               "invalid-routing file must remain byte-identical")) return false;
    QJsonObject invalidMechanical = flatProject(456.0);
    invalidMechanical["mechanical_contract"] = QJsonObject{
        {"schema", "design-studio.mechanical-contract/1"}, {"status", "draft"},
        {"units", "mm"}, {"contract_digest", QString(64, 'a')},
        {"board", QJsonObject{{"outline_pts", QJsonArray{
            QJsonArray{0, 0}, QJsonArray{10, 0}, QJsonArray{0, 10}}}}}};
    if (!writeJson(invalidMechanicalPath, invalidMechanical)) return false;
    const QByteArray mechanicalBefore = readBytes(invalidMechanicalPath);
    if (!check(!model.loadFromFile(invalidMechanicalPath),
               "unlocked mechanical contract must be rejected")) return false;
    if (!check(model.lastError().contains("mechanical contract"),
               "mechanical contract rejection should identify the boundary")) return false;
    if (!check(readBytes(invalidMechanicalPath) == mechanicalBefore,
               "invalid mechanical-contract file must remain byte-identical")) return false;
    return check(model.filePath() == originalPath && model.boardWidthMm == originalWidth
                     && model.netName(0) == originalNet,
                 "all rejected loads must leave active state untouched");
}

bool supportedProjectsRoundTrip(const QString& dir)
{
    const QString inputPath = dir + "/supported-v2.dsproj";
    const QString outputPath = dir + "/roundtrip-v2.dsproj";
    QJsonObject supported = flatProject(142.5, "VBUS");
    supported["controller_acceptance"] = QJsonObject{
        {"schema", "design-studio.controller-acceptance/1"},
        {"test_access", QJsonArray{QJsonObject{{"net", "VBUS"}}}}};
    supported["stackup"] = QJsonObject{{"finished_thickness_mm", 1.6}};
    supported["schematic"] = QJsonObject{
        {"symbols", QJsonArray{}}, {"wires", QJsonArray{}},
        {"sheets", QJsonArray{QJsonObject{{"name", "Root"}}}},
        {"erc", QJsonObject{{"status", "pass"}}}};
    if (!writeJson(inputPath, supported)) return false;

    ProjectModel model;
    if (!check(model.loadFromFile(inputPath), "supported v2 must load")) return false;
    if (!check(model.boardWidthMm == 142.5 && model.netName(0) == "VBUS",
               "v2 values should be parsed")) return false;
    if (!check(model.pcbRules.schemaVersion == 1
                   && model.pcbRules.ipcPerformanceClass == 2
                   && model.pcbRules.producibilityLevel == "B"
                   && model.validatePcbRuleProfile().isEmpty(),
               "legacy v2 must receive a valid industrial rule profile")) return false;
    model.pcbRules.schemaVersion = 99;
    if (!check(!model.validatePcbRuleProfile().isEmpty(),
               "unsupported rule profile versions must fail closed")) return false;
    model.pcbRules.schemaVersion = 1;
    model.pcbRules.source = "fabricator:example";
    model.pcbRules.sourceRevision = "2026-07";
    model.pcbRules.minTraceWidthMm = 0.125;
    model.netClasses[0].allowedLayers = {0};
    model.netClasses[0].allowedViaTypes = {"through"};
    model.netClasses[0].maxViaCount = 4;
    model.netClasses[0].signalFrequencyHz = 100e6;
    model.netClasses[0].impedanceTolerancePct = 12.5;
    model.nets[0].requiredCurrentA = 1.25;
    model.nets[0].nominalVoltageV = 5.0;
    ProjTrace allocatedBranch;
    allocatedBranch.ax_mm = 1; allocatedBranch.ay_mm = 1;
    allocatedBranch.bx_mm = 5; allocatedBranch.by_mm = 1;
    allocatedBranch.width_mm = .5; allocatedBranch.netId = 0;
    allocatedBranch.requiredCurrentA = .75;
    model.traces.append(allocatedBranch);
    model.verificationRequirements.requireSignalIntegrity = true;
    model.verificationRequirements.requirePowerIntegrity = true;
    model.boardOutline = {{0, 0}, {142.5, 0}, {142.5, 80}, {0, 80}};
    model.boardCutouts = {{{10, 10}, {20, 10}, {20, 20}, {10, 20}}};
    ProjRuleArea area;
    area.id = 7; area.name = "HV"; area.layer = 0; area.clearanceMm = 1.2;
    area.maxHeightMm = 4.5;
    area.points = {{30, 30}, {50, 30}, {50, 50}, {30, 50}};
    area.source = "safety-review"; area.sourceRevision = "A";
    model.ruleAreas.append(area);
    ProjFootprint constrained;
    constrained.ref = "J1"; constrained.lib = "Connector_Test";
    constrained.mpn = "ABC-123"; constrained.manufacturer = "Acme";
    constrained.datasheetEvidence = QJsonObject{
        {"schema", "design-studio.datasheet-evidence/1"}, {"source_kind", "local"},
        {"source", "/tmp/ABC-123.pdf"}, {"retrieved_utc", "2026-07-12T00:00:00Z"},
        {"bytes", 100}, {"sha256", QString(64, 'a')},
        {"expected_mpn", "ABC123"}, {"mpn_match", "exact"}};
    constrained.boundComponent = QJsonObject{
        {"schema", "design-studio.bound-component-ref/1"},
        {"binding_id", "component:ABC-123"}, {"binding_digest", QString(64, 'c')},
        {"record_uri", "/library/ABC-123.bound.json"},
        {"model_3d", QJsonObject{
            {"format", "step"}, {"asset_uri", "/library/ABC-123.step"},
            {"claimed_mpn", "ABC-123"},
            {"sha256", QString(64, 'd')}, {"byte_size", 1000},
            {"alignment_status", "verified"},
            {"model_to_footprint", QJsonArray{1, 0, 0, 0, 0, 1, 0, 0,
                                               0, 0, 1, 0, 0, 0, 0, 1}}}}};
    constrained.x_mm = 8; constrained.y_mm = 20; constrained.h3d_mm = 6.0;
    constrained.placementLocked = true; constrained.functionalGroup = "external-io";
    constrained.edgeAnchor = "left"; constrained.thermalPowerW = 0.5;
    constrained.thermalClearanceMm = 1.5; constrained.testAccessRequired = true;
    constrained.testAccessHaloMm = 2.0;
    model.footprints.append(constrained);
    model.unresolvedComponents = QJsonArray{QJsonObject{{"ref", "U9"}, {"mpn", "MISSING"}}};
    ProjClassPairRule pairRule;
    pairRule.classA = 0; pairRule.classB = 1; pairRule.clearanceMm = 0.8;
    pairRule.source = "fabricator:example"; pairRule.sourceRevision = "2026-07";
    model.classPairRules.append(pairRule);
    ProjLayerPolicy layerPolicy;
    layerPolicy.layer = 0; layerPolicy.name = "Top signal";
    layerPolicy.preferredDirection = "horizontal";
    layerPolicy.source = "stackup-review"; layerPolicy.sourceRevision = "A";
    model.layerPolicies.append(layerPolicy);
    ProjCopperZone zone;
    zone.id = 9; zone.name = "VBUS zone"; zone.netId = 0; zone.layer = 0;
    zone.points = {{60, 20}, {80, 20}, {80, 40}, {60, 40}};
    zone.clearanceMm = 0.3; zone.minIslandAreaMm2 = 2.0;
    zone.source = "layout-review"; zone.sourceRevision = "B";
    model.copperZones.append(zone);
    model.mechanicalContract = QJsonObject{
        {"schema", "design-studio.mechanical-contract/1"},
        {"contract_id", "freecad:test"}, {"revision", 1},
        {"status", "locked"}, {"units", "mm"},
        {"contract_digest", QString(64, 'b')},
        {"board", QJsonObject{{"outline_pts", QJsonArray{
            QJsonArray{0, 0}, QJsonArray{142.5, 0},
            QJsonArray{142.5, 80}, QJsonArray{0, 80}}}}}};
    if (!check(model.saveToFile(outputPath), "supported v2 must save")) return false;

    const QJsonDocument saved = QJsonDocument::fromJson(readBytes(outputPath));
    if (!check(saved.isObject() && saved.object().value("version").toInt() == 2,
               "round-trip output must remain explicit v2")) return false;
    if (!check(saved.object().value("document_id").toString() == model.documentId()
                   && saved.object().value("revision").toInteger(-1) == model.revision()
                   && model.fileSha256().size() == 64,
               "saved v2 project must persist identity, revision, and digest")) return false;
    if (!check(saved.object().value("pcb_rules").toObject().value("source").toString()
                   == "fabricator:example",
               "saved v2 project must persist rule provenance")) return false;
    const QJsonObject savedRoot = saved.object();
    if (!check(savedRoot.value("controller_acceptance").toObject()
                       .value("schema").toString()
                   == "design-studio.controller-acceptance/1"
                   && savedRoot.value("stackup").toObject()
                          .value("finished_thickness_mm").toDouble() == 1.6
                   && savedRoot.value("schematic").toObject()
                          .value("sheets").toArray().size() == 1
                   && savedRoot.value("schematic").toObject()
                          .value("erc").toObject().value("status").toString() == "pass",
               "unknown root and schematic extensions must survive round-trip"))
        return false;
    const QString profilePath = dir + "/fabricator-profile.json";
    if (!writeJson(profilePath, saved.object().value("pcb_rules").toObject())) return false;
    ProjPcbRuleProfile imported;
    QString profileError;
    if (!check(model.loadPcbRuleProfileFile(profilePath, imported, profileError)
                   && imported.source == "fabricator:example"
                   && imported.minTraceWidthMm == 0.125,
               "complete fabricator profile must import with provenance")) return false;
    const QString incompleteProfilePath = dir + "/incomplete-profile.json";
    if (!writeJson(incompleteProfilePath, QJsonObject{{"schema_version", 1}, {"id", "bad"}}))
        return false;
    const QString sourceBeforeReject = imported.source;
    if (!check(!model.loadPcbRuleProfileFile(incompleteProfilePath, imported, profileError)
                   && imported.source == sourceBeforeReject
                   && profileError.contains("required field"),
               "incomplete profile import must fail without mutating output")) return false;

    ProjectModel roundTripped;
    if (!check(roundTripped.loadFromFile(outputPath), "round-trip v2 must reload")) return false;
    if (!check(roundTripped.boardWidthMm == 142.5 && roundTripped.netName(0) == "VBUS"
                   && roundTripped.pcbRules.minTraceWidthMm == 0.125
                   && roundTripped.pcbRules.sourceRevision == "2026-07"
                   && roundTripped.netClasses[0].allowedLayers == QVector<int>{0}
                   && roundTripped.netClasses[0].allowedViaTypes == QStringList{"through"}
                   && roundTripped.netClasses[0].maxViaCount == 4
                   && roundTripped.netClasses[0].signalFrequencyHz == 100e6
                   && roundTripped.netClasses[0].impedanceTolerancePct == 12.5
                   && roundTripped.nets[0].requiredCurrentA == 1.25
                   && roundTripped.nets[0].nominalVoltageV == 5.0
                   && roundTripped.traces.size() == 1
                   && roundTripped.traces[0].requiredCurrentA == .75
                   && roundTripped.verificationRequirements.requireSignalIntegrity
                   && roundTripped.verificationRequirements.requirePowerIntegrity
                   && roundTripped.verificationRequirements.requireComponentSemantics
                   && roundTripped.boardOutline.size() == 4
                   && roundTripped.boardCutouts.size() == 1
                   && roundTripped.ruleAreas.size() == 1
                   && roundTripped.ruleAreas[0].clearanceMm == 1.2
                   && roundTripped.ruleAreas[0].maxHeightMm == 4.5
                   && roundTripped.footprints.size() == 1
                   && roundTripped.footprints[0].placementLocked
                   && roundTripped.footprints[0].mpn == "ABC-123"
                   && roundTripped.footprints[0].manufacturer == "Acme"
                   && roundTripped.footprints[0].datasheetEvidence.value("mpn_match") == "exact"
                   && roundTripped.footprints[0].boundComponent.value("binding_id")
                        == "component:ABC-123"
                   && roundTripped.footprints[0].functionalGroup == "external-io"
                   && roundTripped.footprints[0].edgeAnchor == "left"
                   && roundTripped.footprints[0].thermalClearanceMm == 1.5
                   && roundTripped.footprints[0].testAccessRequired
                   && roundTripped.footprints[0].testAccessHaloMm == 2.0
                   && roundTripped.classPairRules.size() == 1
                   && roundTripped.classPairRules[0].clearanceMm == 0.8
                   && roundTripped.layerPolicies.size() == 1
                   && roundTripped.layerPolicies[0].preferredDirection == "horizontal"
                   && roundTripped.copperZones.size() == 1
                   && roundTripped.copperZones[0].minIslandAreaMm2 == 2.0
                   && roundTripped.unresolvedComponents.size() == 1
                   && roundTripped.mechanicalContract.value("status") == "locked"
                   && roundTripped.mechanicalContract.value("contract_id") == "freecad:test",
               "round-trip v2 must preserve supported data")) return false;
    if (!check(roundTripped.documentId() == model.documentId()
                   && roundTripped.revision() == model.revision()
                   && roundTripped.fileSha256() == model.fileSha256(),
               "round-trip must preserve CAD-assist project binding")) return false;

    const qint64 revisionBeforeEdit = roundTripped.revision();
    roundTripped.setModified(true);
    roundTripped.setModified(true);
    if (!check(roundTripped.revision() == revisionBeforeEdit + 1,
               "one dirty edit cycle must advance revision exactly once")) return false;

    // Version-less v1 is still explicitly supported, including its string net list.
    QJsonObject legacy = flatProject(90.0);
    legacy.remove("version");
    legacy.remove("net_table");
    legacy["nets"] = QJsonArray{"GND", "VCC"};
    const QString legacyPath = dir + "/legacy-v1.dsproj";
    if (!writeJson(legacyPath, legacy)) return false;
    ProjectModel legacyModel;
    if (!check(legacyModel.loadFromFile(legacyPath)
                   && legacyModel.netName(0) == "GND"
                   && legacyModel.netName(1) == "VCC",
               "version-less v1 net names must continue to migrate")) return false;
    if (!check(!legacyModel.hasPersistedIdentity(),
               "legacy identity must remain unavailable until explicit save")) return false;
    const QString migratedPath = dir + "/legacy-v1-with-identity.dsproj";
    return check(legacyModel.saveToFile(migratedPath)
                     && legacyModel.hasPersistedIdentity(),
                 "explicit save must persist a legacy project's CAD-assist identity");
}

bool externalRewriteRequiresExplicitReload(const QString& dir)
{
    const QString path = dir + "/externally-rewritten.dsproj";
    if (!writeJson(path, flatProject(100.0, "ORIGINAL"))) return false;

    ProjectModel model;
    int loadedSignals = 0;
    QObject::connect(&model, &ProjectModel::loaded, [&loadedSignals] { ++loadedSignals; });
    if (!check(model.loadFromFile(path), "external-rewrite fixture must load")) return false;
    if (!writeJson(path, flatProject(250.0, "EXTERNAL"))) return false;

    // Give any accidental filesystem watcher ample time to dispatch. The model
    // must remain authoritative until an explicit reload call is made.
    QEventLoop loop;
    QTimer::singleShot(150, &loop, &QEventLoop::quit);
    loop.exec();
    if (!check(model.boardWidthMm == 100.0 && model.netName(0) == "ORIGINAL",
               "external rewrite must not silently mutate in-memory state")) return false;
    if (!check(loadedSignals == 1, "external rewrite must not emit loaded")) return false;

    if (!check(model.loadFromFile(path), "explicit reload should accept supported v2")) return false;
    return check(model.boardWidthMm == 250.0 && model.netName(0) == "EXTERNAL"
                     && loadedSignals == 2,
                 "only explicit reload may replace in-memory state");
}

bool clearResetsVerificationEvidenceRequirements()
{
    ProjectModel model;
    model.verificationRequirements.requireEnclosureEvidence = true;
    model.verificationRequirements.enclosureEvidencePath = "../contracts/stale.json";
    model.verificationRequirements.enclosureEvidenceSha256 = QString(64, 'a');
    model.clear();
    return check(!model.verificationRequirements.requireEnclosureEvidence
                     && model.verificationRequirements.enclosureEvidencePath.isEmpty()
                     && model.verificationRequirements.enclosureEvidenceSha256.isEmpty(),
                 "new workspaces must not inherit stale enclosure evidence requirements");
}

bool substratePlacementIsLegalized()
{
    ProjectModel model;
    model.boardWidthMm = 20;
    model.boardHeightMm = 20;
    model.gridMm = 1;
    ProjFootprint fp;
    fp.ref = "U1";
    fp.bodyW_mm = 8;
    fp.bodyH_mm = 2;
    fp.courtyard = {{-6, -4}, {6, -4}, {6, 4}, {-6, 4}};
    fp.x_mm = 1;
    fp.y_mm = 1;
    fp.rotDeg = 45;
    model.footprints.append(fp);

    if (!check(!model.isFootprintInsideBoard(model.footprints[0], 1, 1, 45),
               "rotated footprint outside substrate should be detected")) return false;
    const QString error = model.legalizeFootprints();
    if (!check(error.isEmpty(), "a fitting footprint should be legalized")) return false;
    return check(model.isFootprintInsideBoard(model.footprints[0],
                                              model.footprints[0].x_mm,
                                              model.footprints[0].y_mm,
                                              model.footprints[0].rotDeg),
                 "legalized footprint should be fully on substrate");
}

bool roboticApplicationConceptIsFamilyCorrectAndInsideBoard()
{
    const QString prompt = QStringLiteral(
        "3-axis BLDC robotic arm controller, 24 V, CAN, encoders and E-stop");
    if (!check(designstudio::classifyApplicationFamily(prompt)
                   == QStringLiteral("robotic_joint_capstone"),
               "robotic arm prompt should resolve to the robotic family")) return false;

    ProjectModel model;
    model.boardWidthMm = 100;
    model.boardHeightMm = 60;
    model.boardOutline = {{0, 0}, {100, 0}, {100, 60}, {0, 60}};
    model.copperLayers = 4;
    designstudio::ApplicationConceptRequest request;
    request.family = QStringLiteral("robotic_joint_capstone");
    request.inputMinV = 18;
    request.inputMaxV = 30;
    request.interfaceName = QStringLiteral("CAN");
    request.axisCount = 3;
    request.axisCurrentA = 2;
    const auto result = designstudio::buildApplicationConcept(model, request);
    if (!check(result.ok && result.axisCount == 3,
               "three-axis robotic concept should build")) return false;

    auto hasNet = [&model](const QString& name) {
        for (const auto& net : model.nets) if (net.name == name) return true;
        return false;
    };
    if (!check(hasNet(QStringLiteral("CAN_H")) && hasNet(QStringLiteral("CAN_L"))
                   && hasNet(QStringLiteral("ESTOP_FIELD"))
                   && hasNet(QStringLiteral("AX1_PHASE_U"))
                   && hasNet(QStringLiteral("AX2_PHASE_V"))
                   && hasNet(QStringLiteral("AX3_PHASE_W"))
                   && hasNet(QStringLiteral("AX3_ENC_Z")),
               "robotic concept should contain CAN, E-stop, motor and encoder nets")) return false;
    if (!check(!hasNet(QStringLiteral("RS485_A")) && !hasNet(QStringLiteral("TEMP_SENSE")),
               "robotic concept must not contain condition-monitor nets")) return false;
    for (const auto& footprint : model.footprints) {
        if (!check(model.isFootprintInsideBoard(footprint, footprint.x_mm,
                                                footprint.y_mm, footprint.rotDeg),
                   "generated concept footprint must be inside the substrate")) return false;
    }
    return check(model.unresolvedComponents.size() == model.footprints.size(),
                 "every architecture placeholder should request automatic datasheet extraction");
}

bool sixAxisRobotBuildsDistributedCoordinator()
{
    ProjectModel model;
    model.boardWidthMm = 100;
    model.boardHeightMm = 60;
    model.boardOutline = {{0, 0}, {100, 0}, {100, 60}, {0, 60}};
    model.copperLayers = 4;
    designstudio::ApplicationConceptRequest request;
    request.family = QStringLiteral("robotic_joint_capstone");
    request.inputMinV = 42;
    request.inputMaxV = 54;
    request.interfaceName = QStringLiteral("CAN");
    request.axisCount = 6;
    request.axisCurrentA = 3;
    const auto result = designstudio::buildApplicationConcept(model, request);
    if (!check(result.ok && result.axisCount == 6,
               "six-axis robotic concept should build a distributed coordinator")) return false;
    int links = 0;
    bool containsMotorStage = false;
    for (const auto& footprint : model.footprints) {
        if (footprint.ref.startsWith(QStringLiteral("JL"))) ++links;
        if (footprint.lib.contains(QStringLiteral("THREE_PHASE_STAGE"))) containsMotorStage = true;
        if (!check(model.isFootprintInsideBoard(footprint, footprint.x_mm,
                                                footprint.y_mm, footprint.rotDeg),
                   "coordinator footprint must remain inside its substrate")) return false;
    }
    return check(links == 6 && !containsMotorStage,
                 "coordinator should expose six links while motor stages remain on joint boards");
}

bool everyReferenceFamilyBuildsWithoutCrossFamilyFallback()
{
    const QStringList families{
        QStringLiteral("synchronous_buck_converter"),
        QStringLiteral("precision_acquisition_board"),
        QStringLiteral("wireless_sensor_2_4ghz"),
        QStringLiteral("usb_gigabit_high_speed_board"),
        QStringLiteral("bldc_servo_controller"),
        QStringLiteral("industrial_condition_monitor"),
        QStringLiteral("robotic_joint_capstone")};
    for (const QString& family : families) {
        ProjectModel model;
        model.boardWidthMm = 100;
        model.boardHeightMm = 60;
        model.boardOutline = {{0, 0}, {100, 0}, {100, 60}, {0, 60}};
        model.copperLayers = 4;
        designstudio::ApplicationConceptRequest request;
        request.family = family;
        request.inputMinV = family == QStringLiteral("wireless_sensor_2_4ghz") ? 3.0 : 18.0;
        request.inputMaxV = family == QStringLiteral("wireless_sensor_2_4ghz") ? 5.5 : 30.0;
        request.interfaceName = family.contains(QStringLiteral("robot"))
            || family == QStringLiteral("bldc_servo_controller")
            ? QStringLiteral("CAN") : QStringLiteral("Ethernet");
        request.axisCount = family == QStringLiteral("robotic_joint_capstone") ? 3 : 1;
        const auto result = designstudio::buildApplicationConcept(model, request);
        if (!check(result.ok, "every reference family should have its own electrical builder"))
            return false;
        if (!check(result.family == family, "builder must preserve the selected family"))
            return false;
        for (const auto& footprint : model.footprints) {
            if (!check(model.isFootprintInsideBoard(footprint, footprint.x_mm,
                                                    footprint.y_mm, footprint.rotDeg),
                       "reference-family placeholder must start within the substrate"))
                return false;
        }
    }
    return true;
}

bool twoDimensionalAndStepInstanceTransformsMatch()
{
    ProjFootprint footprint;
    footprint.x_mm = 10.0;
    footprint.y_mm = 20.0;
    footprint.rotDeg = 90.0;
    const QPointF local(2.0, 1.0);
    const QPointF top = footprintLocalToBoard(footprint, local);
    if (!check(std::abs(top.x() - 9.0) < 1e-9 && std::abs(top.y() - 22.0) < 1e-9,
               "top 2D footprint must use the STEP assembly rotation convention")) return false;
    footprint.side = 1;
    const QPointF bottom = footprintLocalToBoard(footprint, local);
    return check(std::abs(bottom.x() - 11.0) < 1e-9
                     && std::abs(bottom.y() - 22.0) < 1e-9,
                 "bottom 2D footprint must mirror exactly like the STEP assembly");
}

} // namespace

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    QTemporaryDir temporary;
    if (!temporary.isValid()) {
        std::cerr << "FAIL: could not create temporary directory\n";
        return 1;
    }

    const bool ok = rejectedFormatsPreserveStateAndFiles(temporary.path())
                 && supportedProjectsRoundTrip(temporary.path())
                 && externalRewriteRequiresExplicitReload(temporary.path())
                 && clearResetsVerificationEvidenceRequirements()
                 && substratePlacementIsLegalized()
                 && roboticApplicationConceptIsFamilyCorrectAndInsideBoard()
                 && sixAxisRobotBuildsDistributedCoordinator()
                 && everyReferenceFamilyBuildsWithoutCrossFamilyFallback()
                 && twoDimensionalAndStepInstanceTransformsMatch();
    if (ok) std::cout << "Project model boundary tests passed\n";
    return ok ? 0 : 1;
}
