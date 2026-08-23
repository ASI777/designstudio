#include "ProjectModel.h"
#include <QFile>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonParseError>
#include <QJsonValue>
#include <QSaveFile>
#include <QSet>
#include <QCryptographicHash>
#include <QRegularExpression>
#include <QUuid>
#include <QLineF>
#include <algorithm>
#include <cmath>

namespace {
ProjPcbRuleProfile defaultPcbRules()
{
    ProjPcbRuleProfile p;
    const QStringList blocking{
        "TRACE_CLEARANCE", "PAD_TRACE_CLEARANCE", "PAD_CLEARANCE", "BOARD_EDGE",
        "MIN_WIDTH", "MIN_DRILL", "VIA_TRACE_CLEARANCE", "VIA_PAD_CLEARANCE",
        "VIA_CLEARANCE", "ANNULAR_RING", "DRILL_TO_DRILL", "NET_ISLAND",
        "UNASSIGNED_COPPER", "COPPER_TO_EDGE", "COPPER_TO_HOLE",
        "COURTYARD_OVERLAP", "HEIGHT_CONSTRAINT", "THERMAL_SPACING", "TEST_ACCESS",
        "LAYER_POLICY_VIOLATION", "VIA_POLICY_VIOLATION", "ZONE_VALIDITY"
    };
    for (const auto& rule : blocking) p.severityByRule.insert(rule, "error");
    p.severityByRule.insert("SKEW", "error");
    p.severityByRule.insert("RULE_AREA_VIOLATION", "error");
    p.severityByRule.insert("RIGHT_ANGLE_BEND", "warning");
    return p;
}

QString validatePlacementJson(const QJsonObject& root)
{
    const QSet<QString> anchors{"", "none", "left", "right", "top", "bottom"};
    auto normalizeMpn = [](QString value) {
        value = value.toUpper(); value.remove(QRegularExpression("[^A-Z0-9]")); return value;
    };
    for (const auto& value : root.value("footprints").toArray()) {
        if (!value.isObject()) return "footprints must contain objects";
        const QJsonObject fp = value.toObject();
        if (fp.contains("bound_component")) {
            if (!fp.value("bound_component").isObject())
                return QString("%1 bound_component must be an object").arg(
                    fp.value("ref").toString("footprint"));
            const QJsonObject binding = fp.value("bound_component").toObject();
            static const QRegularExpression bindingSha("^[0-9a-f]{64}$");
            const QJsonObject model = binding.value("model_3d").toObject();
            if (binding.value("schema").toString()
                    != "design-studio.bound-component-ref/1"
                || binding.value("binding_id").toString().isEmpty()
                || !bindingSha.match(binding.value("binding_digest").toString()).hasMatch()
                || model.value("format").toString() != "step"
                || model.value("asset_uri").toString().isEmpty()
                || !bindingSha.match(model.value("sha256").toString()).hasMatch()
                || model.value("model_to_footprint").toArray().size() != 16
                || normalizeMpn(model.value("claimed_mpn").toString())
                    != normalizeMpn(fp.value("mpn").toString()))
                return QString("%1 has an invalid bound-component reference").arg(
                    fp.value("ref").toString("footprint"));
        }
        if (fp.contains("asset_3d")) {
            if (!fp.value("asset_3d").isObject())
                return QString("%1 asset_3d must be an object").arg(
                    fp.value("ref").toString("footprint"));
            const QJsonObject asset = fp.value("asset_3d").toObject();
            static const QRegularExpression assetSha("^[0-9a-f]{64}$");
            const QSet<QString> states{"ready", "proxy_ready", "cloud_pending",
                                       "cloud_failed", "failed"};
            if (asset.value("schema").toString()
                    != "design-studio.component-3d-asset/1"
                || !states.contains(asset.value("status").toString())
                || asset.value("format").toString() != "glb"
                || asset.value("asset_uri").toString().isEmpty()
                || !assetSha.match(asset.value("sha256").toString()).hasMatch()
                || asset.value("model_to_footprint").toArray().size() != 16)
                return QString("%1 has an invalid datasheet-derived 3D asset").arg(
                    fp.value("ref").toString("footprint"));
        }
        const QString mpn = fp.value("mpn").toString();
        const QJsonObject evidence = fp.value("datasheet_evidence").toObject();
        if (!mpn.isEmpty() && !evidence.isEmpty()) {
            static const QRegularExpression sha("^[0-9a-f]{64}$");
            if (evidence.value("schema").toString() != "design-studio.datasheet-evidence/1"
                || evidence.value("mpn_match").toString() != "exact"
                || normalizeMpn(evidence.value("expected_mpn").toString()) != normalizeMpn(mpn)
                || !sha.match(evidence.value("sha256").toString().toLower()).hasMatch())
                return QString("%1 has inconsistent datasheet evidence").arg(
                    fp.value("ref").toString("footprint"));
        }
        if (!fp.contains("placement")) continue;
        if (!fp.value("placement").isObject())
            return QString("%1 placement must be an object").arg(fp.value("ref").toString("footprint"));
        const QJsonObject p = fp.value("placement").toObject();
        const QString ref = fp.value("ref").toString("footprint");
        const QString anchor = p.value("edge_anchor").toString().trimmed().toLower();
        if (!anchors.contains(anchor)) return QString("%1 has invalid edge_anchor").arg(ref);
        if (p.contains("functional_group") && !p.value("functional_group").isString())
            return QString("%1 functional_group must be a string").arg(ref);
        for (const char* key : {"locked", "test_access_required"})
            if (p.contains(key) && !p.value(key).isBool())
                return QString("%1 %2 must be boolean").arg(ref, key);
        for (const char* key : {"thermal_power_w", "thermal_clearance_mm", "test_access_halo_mm"}) {
            if (!p.contains(key)) continue;
            const double number = p.value(key).toDouble(-1);
            if (!p.value(key).isDouble() || !std::isfinite(number) || number < 0)
                return QString("%1 %2 must be finite and non-negative").arg(ref, key);
        }
    }
    if (root.contains("unresolved_components") && !root.value("unresolved_components").isArray())
        return "unresolved_components must be an array";
    for (const auto& value : root.value("rule_areas").toArray()) {
        const QJsonObject area = value.toObject();
        if (!area.contains("max_height_mm")) continue;
        const double height = area.value("max_height_mm").toDouble(-1);
        if (!area.value("max_height_mm").isDouble() || !std::isfinite(height) || height < 0)
            return "rule area max_height_mm must be finite and non-negative";
    }
    return {};
}

QVector<QPointF> localPlacementPolygon(const ProjFootprint& fp)
{
    if (fp.courtyard.size() >= 3) return fp.courtyard;
    if (fp.bodyW_mm > 0 && fp.bodyH_mm > 0) {
        const double x0 = fp.bodyCx_mm - fp.bodyW_mm / 2;
        const double x1 = fp.bodyCx_mm + fp.bodyW_mm / 2;
        const double y0 = fp.bodyCy_mm - fp.bodyH_mm / 2;
        const double y1 = fp.bodyCy_mm + fp.bodyH_mm / 2;
        return {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
    }
    if (fp.pads.isEmpty()) return {{-1, -1}, {1, -1}, {1, 1}, {-1, 1}};
    double minX = 1e300, minY = 1e300, maxX = -1e300, maxY = -1e300;
    for (const auto& pad : fp.pads) {
        minX = std::min(minX, pad.x_mm - pad.w_mm / 2);
        minY = std::min(minY, pad.y_mm - pad.h_mm / 2);
        maxX = std::max(maxX, pad.x_mm + pad.w_mm / 2);
        maxY = std::max(maxY, pad.y_mm + pad.h_mm / 2);
    }
    return {{minX, minY}, {maxX, minY}, {maxX, maxY}, {minX, maxY}};
}

QVector<QPointF> worldPlacementPolygon(const ProjFootprint& fp, double x, double y, double rot)
{
    QVector<QPointF> result;
    for (const auto& p : localPlacementPolygon(fp))
        result.append(footprintLocalToBoard(fp, p, x, y, rot));
    return result;
}

bool pointOnSegment(const QPointF& p, const QPointF& a, const QPointF& b)
{
    const QPointF ab = b - a, ap = p - a;
    const double cross = ab.x() * ap.y() - ab.y() * ap.x();
    if (std::abs(cross) > 1e-7) return false;
    const double dot = QPointF::dotProduct(ap, ab);
    return dot >= -1e-7 && dot <= QPointF::dotProduct(ab, ab) + 1e-7;
}

bool pointInPolygon(const QPointF& p, const QVector<QPointF>& poly)
{
    if (poly.size() < 3) return false;
    bool inside = false;
    for (qsizetype i = 0, j = poly.size() - 1; i < poly.size(); j = i++) {
        const auto& a = poly[i]; const auto& b = poly[j];
        if (pointOnSegment(p, a, b)) return true;
        if ((a.y() > p.y()) != (b.y() > p.y())) {
            const double x = (b.x() - a.x()) * (p.y() - a.y())
                           / (b.y() - a.y()) + a.x();
            if (p.x() < x) inside = !inside;
        }
    }
    return inside;
}

double pointSegmentDistance(const QPointF& p, const QPointF& a, const QPointF& b)
{
    const QPointF ab = b - a;
    const double lengthSquared = QPointF::dotProduct(ab, ab);
    if (lengthSquared <= 1e-14) return QLineF(p, a).length();
    const double t = std::clamp(QPointF::dotProduct(p - a, ab) / lengthSquared, 0.0, 1.0);
    return QLineF(p, a + t * ab).length();
}

int orientation(const QPointF& a, const QPointF& b, const QPointF& c)
{
    const double cross = (b.x() - a.x()) * (c.y() - a.y())
                       - (b.y() - a.y()) * (c.x() - a.x());
    return cross > 1e-7 ? 1 : cross < -1e-7 ? -1 : 0;
}

bool segmentsIntersect(const QPointF& a, const QPointF& b,
                       const QPointF& c, const QPointF& d)
{
    const int o1 = orientation(a, b, c), o2 = orientation(a, b, d);
    const int o3 = orientation(c, d, a), o4 = orientation(c, d, b);
    return (o1 != o2 && o3 != o4)
        || (o1 == 0 && pointOnSegment(c, a, b))
        || (o2 == 0 && pointOnSegment(d, a, b))
        || (o3 == 0 && pointOnSegment(a, c, d))
        || (o4 == 0 && pointOnSegment(b, c, d));
}

bool polygonsIntersect(const QVector<QPointF>& a, const QVector<QPointF>& b)
{
    for (qsizetype i = 0; i < a.size(); ++i)
        for (qsizetype j = 0; j < b.size(); ++j)
            if (segmentsIntersect(a[i], a[(i + 1) % a.size()],
                                  b[j], b[(j + 1) % b.size()])) return true;
    return pointInPolygon(a.first(), b) || pointInPolygon(b.first(), a);
}

QString validateMechanicalContractJson(const QJsonObject& root)
{
    if (!root.contains("mechanical_contract")) return {};
    if (!root.value("mechanical_contract").isObject())
        return "mechanical_contract must be an object";
    const QJsonObject contract = root.value("mechanical_contract").toObject();
    if (contract.value("schema").toString()
            != QStringLiteral("design-studio.mechanical-contract/1"))
        return "mechanical_contract has an unsupported schema";
    if (contract.value("status").toString() != QStringLiteral("locked"))
        return "mechanical_contract must be locked before electronics editing";
    if (contract.value("units").toString() != QStringLiteral("mm"))
        return "mechanical_contract units must be mm";
    static const QRegularExpression sha(QStringLiteral("^[0-9a-f]{64}$"));
    if (!sha.match(contract.value("contract_digest").toString().toLower()).hasMatch())
        return "mechanical_contract contract_digest must be SHA-256";
    const QJsonObject board = contract.value("board").toObject();
    if (board.value("outline_pts").toArray().size() < 3)
        return "mechanical_contract board outline needs at least three points";
    return {};
}

QString validateRoutingJson(const QJsonObject& root)
{
    const int layers = root.value("copper_layers").toInt(2);
    if (layers < 2 || layers > 32) return "copper_layers must be between 2 and 32";
    const QSet<QString> viaTypes{"through", "blind", "buried", "microvia"};
    for (const auto& value : root.value("net_classes").toArray()) {
        const QJsonObject nc = value.toObject();
        QSet<int> seenLayers;
        for (const auto& layerValue : nc.value("allowed_layers").toArray()) {
            const double raw = layerValue.toDouble(-1);
            if (!layerValue.isDouble() || std::floor(raw) != raw || raw < 0 || raw >= layers)
                return "net class allowed_layers contains an invalid copper layer";
            if (seenLayers.contains(int(raw))) return "net class allowed_layers contains a duplicate";
            seenLayers.insert(int(raw));
        }
        if (nc.contains("allowed_via_types")) {
            const QJsonArray types = nc.value("allowed_via_types").toArray();
            if (types.isEmpty()) return "net class allowed_via_types cannot be empty";
            for (const auto& type : types)
                if (!type.isString() || !viaTypes.contains(type.toString()))
                    return "net class allowed_via_types contains an invalid technology";
        }
        const double maxVias = nc.value("max_via_count").toDouble(0);
        if (maxVias < 0 || std::floor(maxVias) != maxVias)
            return "net class max_via_count must be a non-negative integer";
        const double frequency = nc.value("signal_frequency_hz").toDouble(0);
        const double tolerance = nc.value("impedance_tolerance_pct").toDouble(10);
        if (!std::isfinite(frequency) || frequency < 0 || !std::isfinite(tolerance)
            || tolerance <= 0 || tolerance > 50)
            return "net class frequency/tolerance must be finite and within supported limits";
    }
    for (const auto& value : root.value("net_table").toArray()) {
        const QJsonObject net = value.toObject();
        const double current = net.value("required_current_a").toDouble(0);
        const double voltage = net.value("nominal_voltage_v").toDouble(0);
        if (!std::isfinite(current) || current < 0 || !std::isfinite(voltage) || voltage < 0)
            return "net current and voltage requirements must be finite and non-negative";
    }
    for (const auto& value : root.value("traces").toArray()) {
        const double current = value.toObject().value("required_current_a").toDouble(0);
        if (!std::isfinite(current) || current < 0)
            return "trace required_current_a must be finite and non-negative";
    }
    const QSet<QString> roles{"signal", "plane", "mixed"};
    const QSet<QString> directions{"any", "horizontal", "vertical"};
    QSet<int> policyLayers;
    for (const auto& value : root.value("layer_policies").toArray()) {
        if (!value.isObject()) return "layer_policies must contain objects";
        const QJsonObject p = value.toObject();
        const int layer = p.value("layer").toInt(-1);
        const double thickness = p.value("copper_thickness_mm").toDouble(-1);
        if (layer < 0 || layer >= layers || policyLayers.contains(layer))
            return "layer policy has an invalid or duplicate layer";
        if (!roles.contains(p.value("role").toString("signal").toLower())
            || !directions.contains(p.value("preferred_direction").toString("any").toLower()))
            return "layer policy has an invalid role or preferred_direction";
        if (!std::isfinite(thickness) || thickness <= 0)
            return "layer policy copper_thickness_mm must be finite and positive";
        if (p.value("source").toString().trimmed().isEmpty()
            || p.value("source_revision").toString().trimmed().isEmpty())
            return "layer policy source and source_revision are required";
        policyLayers.insert(layer);
    }
    QSet<int> zoneIds;
    for (const auto& value : root.value("copper_zones").toArray()) {
        if (!value.isObject()) return "copper_zones must contain objects";
        const QJsonObject z = value.toObject();
        const int id = z.value("id").toInt(-1), layer = z.value("layer").toInt(-1);
        const double clearance = z.value("clearance_mm").toDouble(-1);
        const double island = z.value("min_island_area_mm2").toDouble(-1);
        if (id < 0 || zoneIds.contains(id) || layer < 0 || layer >= layers
            || z.value("net").toInt(-1) < 0 || z.value("pts").toArray().size() < 3)
            return "copper zone has an invalid id, net, layer, or polygon";
        if (!std::isfinite(clearance) || clearance < 0 || !std::isfinite(island) || island < 0)
            return "copper zone clearance/island limits must be finite and non-negative";
        if (z.value("source").toString().trimmed().isEmpty()
            || z.value("source_revision").toString().trimmed().isEmpty())
            return "copper zone source and source_revision are required";
        for (const auto& pv : z.value("pts").toArray()) {
            const QJsonArray point = pv.toArray();
            if (point.size() != 2 || !point[0].isDouble() || !point[1].isDouble()
                || !std::isfinite(point[0].toDouble()) || !std::isfinite(point[1].toDouble()))
                return "copper zone polygon points must be finite coordinate pairs";
        }
        zoneIds.insert(id);
    }
    return {};
}

QString validateVerificationJson(const QJsonObject& root)
{
    if (!root.contains("verification_requirements")) return {};
    if (!root.value("verification_requirements").isObject())
        return "verification_requirements must be an object";
    const QJsonObject v = root.value("verification_requirements").toObject();
    for (const char* key : {"require_signal_integrity", "require_power_integrity",
                            "require_thermal", "require_component_semantics",
                            "require_enclosure_evidence"})
        if (v.contains(key) && !v.value(key).isBool())
            return QString("%1 must be boolean").arg(key);
    if (v.value("require_enclosure_evidence").toBool(false)) {
        const QString path = v.value("enclosure_evidence_path").toString();
        const QString hash = v.value("enclosure_evidence_sha256").toString().toLower();
        static const QRegularExpression sha("^[0-9a-f]{64}$");
        if (path.trimmed().isEmpty() || !sha.match(hash).hasMatch())
            return "required enclosure evidence needs a path and SHA-256 digest";
    }
    return {};
}
}

ProjectModel::ProjectModel(QObject* parent) : QObject(parent) {
    m_documentId = QStringLiteral("project:")
        + QUuid::createUuid().toString(QUuid::WithoutBraces);
    // Default net class
    ProjNetClass def;
    def.id = 0; def.name = "Default";
    def.clearanceMm = 0.2; def.traceWidthMm = 0.25;
    def.viaDiaMm = 0.6; def.viaDrillMm = 0.3;
    netClasses.append(def);
    pcbRules = defaultPcbRules();
    verificationRequirements = {};
}

void ProjectModel::clear() {
    nets.clear();
    netClasses.clear();
    ruleAreas.clear();
    classPairRules.clear();
    layerPolicies.clear();
    copperZones.clear();
    footprints.clear();
    traces.clear();
    vias.clear();
    unresolvedComponents = {};
    placementState = {};
    mechanicalContract = {};
    m_rootExtensions = {};
    m_schematicExtensions = {};
    symbols.clear();
    schWires.clear();
    ProjNetClass def; def.id=0; def.name="Default";
    def.clearanceMm=0.2; def.traceWidthMm=0.25;
    def.viaDiaMm=0.6; def.viaDrillMm=0.3;
    netClasses.append(def);
    pcbRules = defaultPcbRules();
    verificationRequirements = {};
    boardWidthMm=100; boardHeightMm=80; boardOutline.clear(); boardCutouts.clear();
    copperLayers=2;
    m_modified=false; m_filePath.clear(); m_fileSha256.clear();
    m_documentId = QStringLiteral("project:")
        + QUuid::createUuid().toString(QUuid::WithoutBraces);
    m_revision = 0;
    m_identityPersisted = false;
    m_readOnlyProject3 = false;
    m_lastError.clear();
    emit loaded();
}

void ProjectModel::setModified(bool v) {
    if (v && !m_modified) ++m_revision;
    m_modified = v;
    if (v) emit modified();
}

QString ProjectModel::netName(int id) const {
    if (id < 0) return {};
    for (auto& n : nets) if (n.id == id) return n.name;
    return QString::number(id);
}

int ProjectModel::netIdByName(const QString& name) const {
    for (auto& n : nets) if (n.name == name) return n.id;
    return -1;
}

QString ProjectModel::validatePcbRuleProfile() const {
    return validatePcbRuleProfile(pcbRules);
}

QString ProjectModel::validatePcbRuleProfile(const ProjPcbRuleProfile& profile) const {
    if (profile.schemaVersion != 1) return "unsupported pcb_rules schema_version";
    static const QRegularExpression stableId("^[A-Za-z][A-Za-z0-9._:-]{0,127}$");
    if (!stableId.match(profile.id).hasMatch()) return "pcb_rules id is not a stable identifier";
    if (profile.ipcPerformanceClass < 1 || profile.ipcPerformanceClass > 3)
        return "pcb_rules ipc_performance_class must be 1, 2, or 3";
    if (profile.producibilityLevel != "A" && profile.producibilityLevel != "B"
        && profile.producibilityLevel != "C")
        return "pcb_rules producibility_level must be A, B, or C";
    if (profile.source.trimmed().isEmpty() || profile.sourceRevision.trimmed().isEmpty())
        return "pcb_rules source and source_revision are required";
    const double positive[] = {
        profile.defaultClearanceMm, profile.minTraceWidthMm,
        profile.minMechanicalDrillMm, profile.minAnnularRingMm,
        profile.minDrillToDrillMm, profile.minMicroviaDrillMm,
        profile.minMicroviaWallMm, profile.minCopperToEdgeMm,
        profile.minCopperToHoleMm, profile.minCourtyardClearanceMm,
        profile.minMaskSliverMm, profile.minSilkWidthMm
    };
    for (double value : positive)
        if (!std::isfinite(value) || value <= 0) return "pcb_rules dimensions must be finite and positive";
    static const QSet<QString> allowed{"error", "warning", "info", "disabled"};
    for (auto it = profile.severityByRule.cbegin(); it != profile.severityByRule.cend(); ++it)
        if (!allowed.contains(it.value().toLower()))
            return QString("pcb_rules severity for %1 is invalid").arg(it.key());
    return {};
}

bool ProjectModel::loadPcbRuleProfileFile(const QString& path, ProjPcbRuleProfile& out,
                                          QString& error) const {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        error = QString("Could not read profile '%1': %2").arg(path, file.errorString());
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        error = QString("Invalid profile JSON: %1").arg(parseError.errorString());
        return false;
    }
    QJsonObject object = document.object();
    if (object.contains("pcb_rules")) object = object["pcb_rules"].toObject();
    const QStringList required{"schema_version", "id", "name", "ipc_performance_class",
                               "producibility_level", "source", "source_revision",
                               "limits_mm", "checks", "severity"};
    for (const auto& key : required) {
        if (!object.contains(key)) {
            error = QString("Invalid profile: required field '%1' is missing").arg(key);
            return false;
        }
    }
    const QJsonObject limits = object["limits_mm"].toObject();
    const QStringList requiredLimits{
        "default_clearance", "min_trace_width", "min_mechanical_drill",
        "min_annular_ring", "min_drill_to_drill", "min_microvia_drill",
        "min_microvia_wall", "min_copper_to_edge", "min_copper_to_hole",
        "min_courtyard_clearance", "min_mask_sliver", "min_silk_width"
    };
    for (const auto& key : requiredLimits) {
        if (!limits.value(key).isDouble()) {
            error = QString("Invalid profile: numeric limit '%1' is missing").arg(key);
            return false;
        }
    }
    const ProjPcbRuleProfile parsed = parsePcbRuleProfile(object);
    error = validatePcbRuleProfile(parsed);
    if (!error.isEmpty()) return false;
    out = parsed;
    return true;
}

QString ProjectModel::severityForRule(const QString& ruleName) const {
    return pcbRules.severityByRule.value(
        ruleName, ruleName == "RIGHT_ANGLE_BEND" ? "warning" : "error").toLower();
}

bool ProjectModel::isFootprintInsideBoard(const ProjFootprint& fp, double xMm, double yMm,
                                          double rotationDeg, double marginMm) const
{
    QVector<QPointF> outer = boardOutline;
    if (outer.size() < 3)
        outer = {{0, 0}, {boardWidthMm, 0}, {boardWidthMm, boardHeightMm}, {0, boardHeightMm}};
    const QVector<QPointF> polygon = worldPlacementPolygon(fp, xMm, yMm, rotationDeg);
    QVector<QPointF> probes = polygon;
    for (qsizetype i = 0; i < polygon.size(); ++i)
        probes.append((polygon[i] + polygon[(i + 1) % polygon.size()]) / 2.0);
    if (!std::all_of(probes.cbegin(), probes.cend(), [&](const QPointF& point) {
            if (!pointInPolygon(point, outer)) return false;
            if (marginMm <= 0) return true;
            double edgeDistance = 1e300;
            for (qsizetype i = 0; i < outer.size(); ++i)
                edgeDistance = std::min(edgeDistance,
                    pointSegmentDistance(point, outer[i], outer[(i + 1) % outer.size()]));
            return edgeDistance + 1e-7 >= marginMm;
        })) return false;
    return std::none_of(boardCutouts.cbegin(), boardCutouts.cend(),
                        [&](const QVector<QPointF>& cutout) {
        return polygonsIntersect(polygon, cutout);
    });
}

QString ProjectModel::legalizeFootprints(bool preserveLocked)
{
    QVector<QPointF> outer = boardOutline;
    if (outer.size() < 3)
        outer = {{0, 0}, {boardWidthMm, 0}, {boardWidthMm, boardHeightMm}, {0, boardHeightMm}};
    double minX = 1e300, minY = 1e300, maxX = -1e300, maxY = -1e300;
    for (const auto& point : outer) {
        minX = std::min(minX, point.x()); maxX = std::max(maxX, point.x());
        minY = std::min(minY, point.y()); maxY = std::max(maxY, point.y());
    }
    const double step = std::max(gridMm > 0 ? gridMm : 0.5, 0.5);
    QStringList errors;
    bool moved = false;
    for (auto& fp : footprints) {
        if (isFootprintInsideBoard(fp, fp.x_mm, fp.y_mm, fp.rotDeg)) continue;
        const bool locked = fp.placementLocked;
        if (locked && preserveLocked) {
            errors.append(QStringLiteral("%1 is locked outside the PCB substrate").arg(fp.ref));
            continue;
        }
        QPointF best;
        double bestDistance = 1e300;
        bool found = false;
        for (double y = minY; y <= maxY + 1e-9; y += step)
            for (double x = minX; x <= maxX + 1e-9; x += step) {
                if (!isFootprintInsideBoard(fp, x, y, fp.rotDeg)) continue;
                const double distance = (x - fp.x_mm) * (x - fp.x_mm)
                                      + (y - fp.y_mm) * (y - fp.y_mm);
                if (distance < bestDistance) {
                    best = QPointF(x, y); bestDistance = distance; found = true;
                }
            }
        if (!found) {
            errors.append(QStringLiteral("%1 cannot fit on the PCB substrate").arg(fp.ref));
            continue;
        }
        fp.x_mm = std::round(best.x() * 1000.0) / 1000.0;
        fp.y_mm = std::round(best.y() * 1000.0) / 1000.0;
        moved = true;
    }
    if (moved) setModified(true);
    return errors.join(QStringLiteral("; "));
}

int ProjectModel::createNet(const QString& name) {
    int maxId = -1;
    for (const auto& n : nets) maxId = std::max(maxId, n.id);
    ProjNet n;
    n.id = maxId + 1;
    n.name = name.isEmpty() ? QString("N$%1").arg(n.id) : name;
    n.classId = 0;
    nets.append(n);
    return n.id;
}

void ProjectModel::setPinNet(const QString& ref, const QString& pinNumber, int netId) {
    // Update the schematic symbol's pin.
    for (auto& s : symbols) {
        if (s.ref != ref) continue;
        for (auto& p : s.pins)
            if (p.number == pinNumber) { p.netId = netId; break; }
        break;
    }
    // Forward-annotate: update the matching PCB footprint pad.
    for (auto& fp : footprints) {
        if (fp.ref != ref) continue;
        for (auto& pad : fp.pads)
            if (pad.name == pinNumber) { pad.netId = netId; break; }
        break;
    }
}

// ── Symbol generation ─────────────────────────────────────────────────────────
namespace {
// Infer a pin's electrical role from its name (we don't carry electrical_type
// on the loaded footprint pads). Returns one of: power_in, power_out, passive.
QString inferEtype(const QString& pinName) {
    const QString u = pinName.toUpper();
    auto has = [&](const char* s){ return u.contains(s); };
    if (has("GND") || has("VSS") || has("AGND") || has("PGND") || has("VSSQ"))
        return "power_in";                 // ground is a power input
    if (has("VOUT") || u == "VO" || has("OUT_V"))
        return "power_out";
    if (has("VCC") || has("VDD") || has("VBUS") || has("VIN") || has("VBAT") ||
        has("VPP") || has("VDDQ") || has("3V3") || has("5V") || has("1V8") ||
        has("1V2") || has("2V5") || has("VREF") || has("AVDD") || has("DVDD"))
        return "power_in";
    return "passive";
}
bool isGround(const QString& pinName) {
    const QString u = pinName.toUpper();
    return u.contains("GND") || u.contains("VSS") || u == "EP";
}
} // namespace

int ProjectModel::generateSymbolsFromFootprints() {
    if (!symbols.isEmpty()) return 0;       // schematic already exists — don't clobber

    // 1) One symbol per footprint, pins bucketed by side (power top, ground
    //    bottom, signals split left/right). Placement is assigned in step 2.
    for (const auto& fp : footprints) {
        SchSymbol sym;
        sym.ref = fp.ref;
        sym.lib = fp.lib;

        int topN = 0, botN = 0, leftN = 0, rightN = 0;
        int signalToggle = 0;
        for (const auto& pad : fp.pads) {
            SchPin pin;
            pin.number = pad.name;
            pin.netId  = pad.netId;
            // Footprint pads are usually named by NUMBER, not function. Classify
            // by the connected NET name when available (that carries the role —
            // GND, VCC, SDA…); fall back to the pad name otherwise.
            const QString netNm = netName(pad.netId);
            const QString roleSrc = netNm.isEmpty() ? pad.name : netNm;
            pin.name  = netNm.isEmpty() ? pad.name : netNm;
            pin.etype = inferEtype(roleSrc);

            if (isGround(roleSrc)) {
                pin.side = PinSide::Bottom; pin.order = botN++;
            } else if (pin.etype == "power_in") {
                pin.side = PinSide::Top;    pin.order = topN++;
            } else if (pin.etype == "power_out") {
                pin.side = PinSide::Right;  pin.order = rightN++;
            } else {
                if (signalToggle++ % 2 == 0) { pin.side = PinSide::Left;  pin.order = leftN++; }
                else                         { pin.side = PinSide::Right; pin.order = rightN++; }
            }
            sym.pins.append(pin);
        }
        symbols.append(sym);
    }

    // 2) Function-grouped placement. ICs (anchors) sit in a row of functional
    //    zones; each support part (satellite) is placed under the anchor it
    //    shares the most SIGNAL nets with — so an IC and its decoupling / crystal
    //    / matching parts cluster into one block, like a hand-drawn sheet. Power
    //    and ground nets are excluded from the "shared net" score (they touch
    //    almost everything, so they carry no grouping information).
    const int N = symbols.size();
    if (N == 0) return 0;

    auto isPowerNet = [this](int nid) {
        if (nid < 0) return true;
        const QString n = netName(nid).toUpper();
        return n.contains("GND") || n.contains("VCC") || n.contains("VDD")
            || n.contains("VBAT") || n.contains("VBUS") || n.contains("VLED")
            || n.contains("VIN")  || n.contains("3V")   || n.startsWith("+");
    };
    QVector<QSet<int>> sig(N);
    for (int i = 0; i < N; ++i)
        for (const auto& pin : symbols[i].pins)
            if (!isPowerNet(pin.netId)) sig[i].insert(pin.netId);

    auto isAnchor = [](const SchSymbol& s) {
        const QChar c = s.ref.isEmpty() ? QChar('X') : s.ref[0].toUpper();
        return s.pins.size() >= 6 || c == QChar('U') || c == QChar('J');
    };
    QVector<int> anchors, sats;
    for (int i = 0; i < N; ++i) (isAnchor(symbols[i]) ? anchors : sats).append(i);
    if (anchors.isEmpty()) { anchors = sats; sats.clear(); }   // all-passive board

    const double zoneW = 62.0, topY = 24.0, satTop = 58.0, satDX = 18.0, satDY = 13.0;
    QMap<int,int> colOf;                       // anchor symbol idx -> zone column
    QVector<int>  load(anchors.size(), 0);     // satellites already stacked / zone
    for (int c = 0; c < anchors.size(); ++c) {
        symbols[anchors[c]].x_mm = 24.0 + c * zoneW;
        symbols[anchors[c]].y_mm = topY;
        colOf[anchors[c]] = c;
    }
    for (int s : sats) {
        int bestCol = 0, bestShare = -1;
        for (int a : anchors) {
            int share = 0;
            for (int nid : sig[s]) if (sig[a].contains(nid)) ++share;
            if (share > bestShare) { bestShare = share; bestCol = colOf[a]; }
        }
        if (bestShare <= 0)                    // power-only part: balance the zones
            bestCol = int(std::min_element(load.begin(), load.end()) - load.begin());
        const int row = load[bestCol]++;
        symbols[s].x_mm = 24.0 + bestCol * zoneW + (row % 3) * satDX;
        symbols[s].y_mm = satTop + (row / 3) * satDY;
    }
    return symbols.size();
}

// ── JSON parsers ──────────────────────────────────────────────────────────────
ProjPad ProjectModel::parsePad(const QJsonObject& o) const {
    ProjPad p;
    p.name        = o["name"].toString();
    p.x_mm        = o["x_mm"].toDouble();
    p.y_mm        = o["y_mm"].toDouble();
    p.w_mm        = o["w_mm"].toDouble(0.6);
    p.h_mm        = o["h_mm"].toDouble(0.25);
    p.netId       = o["net"].toInt(-1);
    p.throughHole = o["th"].toBool(false);
    p.drillMm     = o["drill_mm"].toDouble();
    p.shape       = o["shape"].toString("rect");
    p.cornerR_mm  = o["corner_r_mm"].toDouble(0);
    p.clearanceMm = o["clearance_mm"].toDouble(0);
    return p;
}

ProjFootprint ProjectModel::parseFp(const QJsonObject& o) const {
    ProjFootprint fp;
    fp.ref    = o["ref"].toString();
    fp.lib    = o["lib"].toString();
    fp.mpn    = o["mpn"].toString();
    fp.manufacturer = o["manufacturer"].toString();
    fp.value = o["value"].toString();
    fp.datasheetEvidence = o["datasheet_evidence"].toObject();
    fp.boundComponent = o["bound_component"].toObject();
    fp.asset3d = o["asset_3d"].toObject();
    fp.x_mm   = o["x_mm"].toDouble();
    fp.y_mm   = o["y_mm"].toDouble();
    fp.rotDeg = o["rot_deg"].toDouble();
    fp.side   = o["side"].toInt();
    fp.h3d_mm = o["h3d_mm"].toDouble();
    fp.bodyW_mm  = o["body_w_mm"].toDouble();
    fp.bodyH_mm  = o["body_h_mm"].toDouble();
    fp.bodyCx_mm = o["body_cx_mm"].toDouble();
    fp.bodyCy_mm = o["body_cy_mm"].toDouble();
    const QJsonObject placement = o["placement"].toObject();
    fp.placementLocked = placement["locked"].toBool(false);
    fp.functionalGroup = placement["functional_group"].toString();
    fp.edgeAnchor = placement["edge_anchor"].toString().trimmed().toLower();
    fp.thermalPowerW = placement["thermal_power_w"].toDouble();
    fp.thermalClearanceMm = placement["thermal_clearance_mm"].toDouble();
    fp.testAccessRequired = placement["test_access_required"].toBool(false);
    fp.testAccessHaloMm = placement["test_access_halo_mm"].toDouble();
    for (auto pv : o["courtyard_pts"].toArray()) {
        const QJsonArray pt = pv.toArray();
        if (pt.size() == 2) fp.courtyard.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
    }
    for (auto v : o["pads"].toArray())
        fp.pads.append(parsePad(v.toObject()));
    for (auto v : o["regions"].toArray()) {
        QJsonObject ro = v.toObject();
        ProjRegion rg;
        rg.netId = ro["net"].toInt(-1);
        for (auto pv : ro["pts"].toArray()) {
            QJsonArray pt = pv.toArray();
            if (pt.size() == 2)
                rg.points.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
        }
        for (auto pv : ro["hole_pts"].toArray()) {
            QJsonArray pt = pv.toArray();
            if (pt.size() == 2)
                rg.hole.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
        }
        if (rg.points.size() >= 3) fp.regions.append(rg);
    }
    return fp;
}

ProjTrace ProjectModel::parseTrace(const QJsonObject& o) const {
    ProjTrace t;
    t.ax_mm   = o["ax_mm"].toDouble();
    t.ay_mm   = o["ay_mm"].toDouble();
    t.bx_mm   = o["bx_mm"].toDouble();
    t.by_mm   = o["by_mm"].toDouble();
    t.width_mm = o["w_mm"].toDouble(0.25);
    t.netId   = o["net"].toInt(-1);
    t.layer   = o["layer"].toInt();
    t.isPour  = o["pour"].toBool(false);
    t.minWidthOverrideMm = o["min_width_override_mm"].toDouble();
    t.requiredCurrentA = o["required_current_a"].toDouble();
    return t;
}

ProjVia ProjectModel::parseVia(const QJsonObject& o) const {
    ProjVia v;
    v.x_mm     = o["x_mm"].toDouble();
    v.y_mm     = o["y_mm"].toDouble();
    v.diaMm    = o["dia_mm"].toDouble(0.6);
    v.drillMm  = o["drill_mm"].toDouble(0.3);
    v.netId    = o["net"].toInt(-1);
    v.fromLayer = o["from"].toInt();
    v.toLayer   = o["to"].toInt(1);
    v.type       = o["via_type"].toString();
    if (v.type.isEmpty()) {
        if (v.fromLayer == 0 && v.toLayer == copperLayers - 1) v.type = "through";
        else if (std::abs(v.toLayer - v.fromLayer) == 1)       v.type = "microvia";
        else                                                   v.type = "buried";
    }
    return v;
}

ProjNet ProjectModel::parseNet(const QJsonObject& o) const {
    ProjNet n; n.id = o["id"].toInt(); n.name = o["name"].toString(); n.classId = o["class"].toInt();
    n.requiredCurrentA = o["required_current_a"].toDouble();
    n.nominalVoltageV = o["nominal_voltage_v"].toDouble();
    return n;
}

ProjNetClass ProjectModel::parseNetClass(const QJsonObject& o) const {
    ProjNetClass nc;
    nc.id          = o["id"].toInt();
    nc.name        = o["name"].toString("Default");
    nc.clearanceMm = o["clearance_mm"].toDouble(0.2);
    nc.traceWidthMm = o["trace_width_mm"].toDouble(0.25);
    nc.viaDiaMm    = o["via_diameter_mm"].toDouble(0.6);
    nc.viaDrillMm  = o["via_drill_mm"].toDouble(0.3);
    nc.diffPairGapMm = o["diff_pair_gap_mm"].toDouble();
    nc.maxSkewMm     = o["max_skew_mm"].toDouble();
    nc.allowMicrovia = o["microvia"].toBool(false);
    nc.z0Ohm       = o["z0_ohm"].toDouble();
    nc.zdiffOhm    = o["zdiff_ohm"].toDouble();
    for (const auto& v : o["allowed_layers"].toArray()) nc.allowedLayers.append(v.toInt());
    if (o.contains("allowed_via_types")) {
        nc.allowedViaTypes.clear();
        for (const auto& v : o["allowed_via_types"].toArray()) nc.allowedViaTypes.append(v.toString());
    }
    nc.maxViaCount = o["max_via_count"].toInt(0);
    nc.signalFrequencyHz = o["signal_frequency_hz"].toDouble();
    nc.impedanceTolerancePct = o["impedance_tolerance_pct"].toDouble(10);
    return nc;
}

ProjPcbRuleProfile ProjectModel::parsePcbRuleProfile(const QJsonObject& o) const {
    ProjPcbRuleProfile p = defaultPcbRules();
    p.schemaVersion = o["schema_version"].toInt(1);
    p.id = o["id"].toString(p.id);
    p.name = o["name"].toString(p.name);
    p.ipcPerformanceClass = o["ipc_performance_class"].toInt(2);
    p.producibilityLevel = o["producibility_level"].toString("B").toUpper();
    p.source = o["source"].toString(p.source);
    p.sourceRevision = o["source_revision"].toString(p.sourceRevision);
    p.fabricator = o["fabricator"].toString();
    p.assembler = o["assembler"].toString();
    const QJsonObject limits = o["limits_mm"].toObject();
    p.defaultClearanceMm = limits["default_clearance"].toDouble(p.defaultClearanceMm);
    p.minTraceWidthMm = limits["min_trace_width"].toDouble(p.minTraceWidthMm);
    p.minMechanicalDrillMm = limits["min_mechanical_drill"].toDouble(p.minMechanicalDrillMm);
    p.minAnnularRingMm = limits["min_annular_ring"].toDouble(p.minAnnularRingMm);
    p.minDrillToDrillMm = limits["min_drill_to_drill"].toDouble(p.minDrillToDrillMm);
    p.minMicroviaDrillMm = limits["min_microvia_drill"].toDouble(p.minMicroviaDrillMm);
    p.minMicroviaWallMm = limits["min_microvia_wall"].toDouble(p.minMicroviaWallMm);
    p.minCopperToEdgeMm = limits["min_copper_to_edge"].toDouble(p.minCopperToEdgeMm);
    p.minCopperToHoleMm = limits["min_copper_to_hole"].toDouble(p.minCopperToHoleMm);
    p.minCourtyardClearanceMm = limits["min_courtyard_clearance"].toDouble(p.minCourtyardClearanceMm);
    p.minMaskSliverMm = limits["min_mask_sliver"].toDouble(p.minMaskSliverMm);
    p.minSilkWidthMm = limits["min_silk_width"].toDouble(p.minSilkWidthMm);
    const QJsonObject checks = o["checks"].toObject();
    p.checkConnectivity = checks["connectivity"].toBool(true);
    p.checkSkew = checks["skew"].toBool(true);
    p.releaseRequiresNativeDrc = checks["release_requires_native_drc"].toBool(true);
    const QJsonObject severity = o["severity"].toObject();
    for (auto it = severity.begin(); it != severity.end(); ++it)
        p.severityByRule[it.key()] = it.value().toString("error").toLower();
    return p;
}

ProjRuleArea ProjectModel::parseRuleArea(const QJsonObject& o) const {
    ProjRuleArea a;
    a.id = o["id"].toInt(); a.name = o["name"].toString();
    a.layer = o["layer"].toInt(-1);
    a.clearanceMm = o["clearance_mm"].toDouble();
    a.minTraceWidthMm = o["min_trace_width_mm"].toDouble();
    a.forbidRouting = o["forbid_routing"].toBool(false);
    a.forbidVias = o["forbid_vias"].toBool(false);
    a.forbidPlacement = o["forbid_placement"].toBool(false);
    a.maxHeightMm = o["max_height_mm"].toDouble();
    a.source = o["source"].toString("project");
    a.sourceRevision = o["source_revision"].toString("1");
    for (auto pv : o["pts"].toArray()) {
        const QJsonArray pt = pv.toArray();
        if (pt.size() == 2) a.points.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
    }
    return a;
}

ProjClassPairRule ProjectModel::parseClassPairRule(const QJsonObject& o) const {
    ProjClassPairRule r;
    r.classA = o["class_a"].toInt(); r.classB = o["class_b"].toInt();
    r.clearanceMm = o["clearance_mm"].toDouble();
    r.source = o["source"].toString("project");
    r.sourceRevision = o["source_revision"].toString("1");
    return r;
}

ProjLayerPolicy ProjectModel::parseLayerPolicy(const QJsonObject& o) const {
    ProjLayerPolicy p;
    p.layer = o["layer"].toInt(); p.name = o["name"].toString();
    p.role = o["role"].toString("signal").toLower();
    p.preferredDirection = o["preferred_direction"].toString("any").toLower();
    p.allowRouting = o["allow_routing"].toBool(true);
    p.copperThicknessMm = o["copper_thickness_mm"].toDouble(0.035);
    p.source = o["source"].toString("project");
    p.sourceRevision = o["source_revision"].toString("1");
    return p;
}

ProjCopperZone ProjectModel::parseCopperZone(const QJsonObject& o) const {
    ProjCopperZone z;
    z.id = o["id"].toInt(); z.name = o["name"].toString();
    z.netId = o["net"].toInt(-1); z.layer = o["layer"].toInt();
    z.clearanceMm = o["clearance_mm"].toDouble(0.2);
    z.minIslandAreaMm2 = o["min_island_area_mm2"].toDouble();
    z.requireConnection = o["require_connection"].toBool(true);
    z.source = o["source"].toString("project");
    z.sourceRevision = o["source_revision"].toString("1");
    for (const auto& pv : o["pts"].toArray()) {
        const QJsonArray pt = pv.toArray();
        if (pt.size() == 2) z.points.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
    }
    return z;
}

SchSymbol ProjectModel::parseSymbol(const QJsonObject& o) const {
    SchSymbol s;
    s.ref    = o["ref"].toString();
    s.lib    = o["lib"].toString();
    s.value  = o["value"].toString();
    s.x_mm   = o["x_mm"].toDouble();
    s.y_mm   = o["y_mm"].toDouble();
    s.rotDeg = o["rot_deg"].toDouble();
    s.unit   = o["unit"].toInt(1);
    for (auto v : o["pins"].toArray()) {
        QJsonObject po = v.toObject();
        SchPin pin;
        pin.number = po["num"].toString();
        pin.name   = po["name"].toString();
        pin.etype  = po["etype"].toString("passive");
        pin.side   = static_cast<PinSide>(po["side"].toInt(0));
        pin.order  = po["order"].toInt(0);
        pin.netId  = po["net"].toInt(-1);
        s.pins.append(pin);
    }
    return s;
}

SchWire ProjectModel::parseWire(const QJsonObject& o) const {
    SchWire w;
    w.netId = o["net"].toInt(-1);
    for (auto v : o["pts"].toArray()) {
        QJsonArray pt = v.toArray();
        if (pt.size() == 2)
            w.points.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
    }
    return w;
}

// ── JSON serialisers ──────────────────────────────────────────────────────────
QJsonObject ProjectModel::toJson(const ProjPad& p) const {
    return QJsonObject{
        {"name", p.name}, {"x_mm", p.x_mm}, {"y_mm", p.y_mm},
        {"w_mm", p.w_mm}, {"h_mm", p.h_mm}, {"net", p.netId},
        {"th", p.throughHole}, {"drill_mm", p.drillMm}, {"pkg_delay_mm", 0.0},
        {"shape", p.shape}, {"corner_r_mm", p.cornerR_mm},
        {"clearance_mm", p.clearanceMm}
    };
}

QJsonObject ProjectModel::toJson(const ProjFootprint& fp) const {
    QJsonArray pads;
    for (auto& p : fp.pads) pads.append(toJson(p));
    QJsonArray regions;
    QJsonArray courtyard;
    for (const auto& pt : fp.courtyard) courtyard.append(QJsonArray{pt.x(), pt.y()});
    for (auto& rg : fp.regions) {
        QJsonArray pts, holePts;
        for (auto& pt : rg.points) pts.append(QJsonArray{pt.x(), pt.y()});
        for (auto& pt : rg.hole)   holePts.append(QJsonArray{pt.x(), pt.y()});
        regions.append(QJsonObject{{"net", rg.netId}, {"pts", pts}, {"hole_pts", holePts}});
    }
    QJsonObject result{
        {"ref", fp.ref}, {"lib", fp.lib}, {"x_mm", fp.x_mm}, {"y_mm", fp.y_mm},
        {"mpn", fp.mpn}, {"manufacturer", fp.manufacturer}, {"value", fp.value},
        {"datasheet_evidence", fp.datasheetEvidence},
        {"rot_deg", fp.rotDeg}, {"side", fp.side}, {"h3d_mm", fp.h3d_mm},
        {"body_w_mm", fp.bodyW_mm}, {"body_h_mm", fp.bodyH_mm},
        {"body_cx_mm", fp.bodyCx_mm}, {"body_cy_mm", fp.bodyCy_mm},
        {"courtyard_pts", courtyard},
        {"placement", QJsonObject{
            {"locked", fp.placementLocked}, {"functional_group", fp.functionalGroup},
            {"edge_anchor", fp.edgeAnchor}, {"thermal_power_w", fp.thermalPowerW},
            {"thermal_clearance_mm", fp.thermalClearanceMm},
            {"test_access_required", fp.testAccessRequired},
            {"test_access_halo_mm", fp.testAccessHaloMm}
        }},
        {"pads", pads}, {"regions", regions}
    };
    if (!fp.boundComponent.isEmpty()) result.insert("bound_component", fp.boundComponent);
    if (!fp.asset3d.isEmpty()) result.insert("asset_3d", fp.asset3d);
    return result;
}

QJsonObject ProjectModel::toJson(const ProjTrace& t) const {
    QJsonObject result{
        {"ax_mm",t.ax_mm},{"ay_mm",t.ay_mm},{"bx_mm",t.bx_mm},{"by_mm",t.by_mm},
        {"w_mm",t.width_mm},{"net",t.netId},{"layer",t.layer},{"pour",t.isPour}
    };
    if (t.minWidthOverrideMm > 0)
        result.insert("min_width_override_mm", t.minWidthOverrideMm);
    if (t.requiredCurrentA > 0)
        result.insert("required_current_a", t.requiredCurrentA);
    return result;
}

QJsonObject ProjectModel::toJson(const ProjVia& v) const {
    return QJsonObject{
        {"x_mm",v.x_mm},{"y_mm",v.y_mm},{"dia_mm",v.diaMm},{"drill_mm",v.drillMm},
        {"net",v.netId},{"from",v.fromLayer},{"to",v.toLayer},
        {"antipad_mm",0.0},{"backdrill_to",-1},{"via_type",v.type}
    };
}

QJsonObject ProjectModel::toJson(const ProjNet& n) const {
    return QJsonObject{{"id",n.id},{"name",n.name},{"class",n.classId},
                       {"required_current_a", n.requiredCurrentA},
                       {"nominal_voltage_v", n.nominalVoltageV}};
}

QJsonObject ProjectModel::toJson(const ProjNetClass& nc) const {
    QJsonArray allowedLayers, allowedViaTypes;
    for (int layer : nc.allowedLayers) allowedLayers.append(layer);
    for (const auto& type : nc.allowedViaTypes) allowedViaTypes.append(type);
    return QJsonObject{
        {"id",nc.id},{"name",nc.name},{"clearance_mm",nc.clearanceMm},
        {"trace_width_mm",nc.traceWidthMm},{"via_diameter_mm",nc.viaDiaMm},
        {"via_drill_mm",nc.viaDrillMm},{"diff_pair_gap_mm",nc.diffPairGapMm},
        {"max_skew_mm",nc.maxSkewMm},{"microvia",nc.allowMicrovia},
        {"z0_ohm",nc.z0Ohm},{"zdiff_ohm",nc.zdiffOhm},
        {"allowed_layers", allowedLayers}, {"allowed_via_types", allowedViaTypes},
        {"max_via_count", nc.maxViaCount}, {"signal_frequency_hz", nc.signalFrequencyHz},
        {"impedance_tolerance_pct", nc.impedanceTolerancePct}
    };
}

QJsonObject ProjectModel::toJson(const ProjPcbRuleProfile& p) const {
    QJsonObject severity;
    for (auto it = p.severityByRule.cbegin(); it != p.severityByRule.cend(); ++it)
        severity.insert(it.key(), it.value());
    return QJsonObject{
        {"schema_version", p.schemaVersion}, {"id", p.id}, {"name", p.name},
        {"ipc_performance_class", p.ipcPerformanceClass},
        {"producibility_level", p.producibilityLevel},
        {"source", p.source}, {"source_revision", p.sourceRevision},
        {"fabricator", p.fabricator}, {"assembler", p.assembler},
        {"limits_mm", QJsonObject{
            {"default_clearance", p.defaultClearanceMm},
            {"min_trace_width", p.minTraceWidthMm},
            {"min_mechanical_drill", p.minMechanicalDrillMm},
            {"min_annular_ring", p.minAnnularRingMm},
            {"min_drill_to_drill", p.minDrillToDrillMm},
            {"min_microvia_drill", p.minMicroviaDrillMm},
            {"min_microvia_wall", p.minMicroviaWallMm},
            {"min_copper_to_edge", p.minCopperToEdgeMm},
            {"min_copper_to_hole", p.minCopperToHoleMm},
            {"min_courtyard_clearance", p.minCourtyardClearanceMm},
            {"min_mask_sliver", p.minMaskSliverMm},
            {"min_silk_width", p.minSilkWidthMm}
        }},
        {"checks", QJsonObject{
            {"connectivity", p.checkConnectivity}, {"skew", p.checkSkew},
            {"release_requires_native_drc", p.releaseRequiresNativeDrc}
        }},
        {"severity", severity}
    };
}

QJsonObject ProjectModel::toJson(const ProjRuleArea& a) const {
    QJsonArray points;
    for (const auto& p : a.points) points.append(QJsonArray{p.x(), p.y()});
    return QJsonObject{
        {"id", a.id}, {"name", a.name}, {"pts", points}, {"layer", a.layer},
        {"clearance_mm", a.clearanceMm}, {"min_trace_width_mm", a.minTraceWidthMm},
        {"forbid_routing", a.forbidRouting}, {"forbid_vias", a.forbidVias},
        {"forbid_placement", a.forbidPlacement}, {"max_height_mm", a.maxHeightMm},
        {"source", a.source},
        {"source_revision", a.sourceRevision}
    };
}

QJsonObject ProjectModel::toJson(const ProjClassPairRule& r) const {
    return QJsonObject{{"class_a", r.classA}, {"class_b", r.classB},
                       {"clearance_mm", r.clearanceMm}, {"source", r.source},
                       {"source_revision", r.sourceRevision}};
}

QJsonObject ProjectModel::toJson(const ProjLayerPolicy& p) const {
    return QJsonObject{{"layer", p.layer}, {"name", p.name}, {"role", p.role},
        {"preferred_direction", p.preferredDirection}, {"allow_routing", p.allowRouting},
        {"copper_thickness_mm", p.copperThicknessMm}, {"source", p.source},
        {"source_revision", p.sourceRevision}};
}

QJsonObject ProjectModel::toJson(const ProjCopperZone& z) const {
    QJsonArray points;
    for (const auto& p : z.points) points.append(QJsonArray{p.x(), p.y()});
    return QJsonObject{{"id", z.id}, {"name", z.name}, {"pts", points},
        {"net", z.netId}, {"layer", z.layer}, {"clearance_mm", z.clearanceMm},
        {"min_island_area_mm2", z.minIslandAreaMm2},
        {"require_connection", z.requireConnection}, {"source", z.source},
        {"source_revision", z.sourceRevision}};
}

QJsonObject ProjectModel::toJson(const SchSymbol& s) const {
    QJsonArray pins;
    for (auto& p : s.pins)
        pins.append(QJsonObject{
            {"num", p.number}, {"name", p.name}, {"etype", p.etype},
            {"side", int(p.side)}, {"order", p.order}, {"net", p.netId}
        });
    return QJsonObject{
        {"ref", s.ref}, {"lib", s.lib}, {"value", s.value},
        {"x_mm", s.x_mm}, {"y_mm", s.y_mm},
        {"rot_deg", s.rotDeg}, {"unit", s.unit}, {"pins", pins}
    };
}

QJsonObject ProjectModel::toJson(const SchWire& w) const {
    QJsonArray pts;
    for (auto& pt : w.points)
        pts.append(QJsonArray{pt.x(), pt.y()});
    return QJsonObject{{"net", w.netId}, {"pts", pts}};
}

// ── File I/O ──────────────────────────────────────────────────────────────────
bool ProjectModel::loadFromFile(const QString& path) {
    return loadFromFileInternal(path, false);
}

bool ProjectModel::loadForVerification(const QString& path) {
    return loadFromFileInternal(path, true);
}

bool ProjectModel::loadFromFileInternal(const QString& path,
                                        bool allowProject3ForVerification) {
    if (path.trimmed().isEmpty()) {
        m_lastError = QStringLiteral("Could not read project: path is empty.");
        return false;
    }

    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) {
        m_lastError = QString("Could not read '%1': %2").arg(path, f.errorString());
        return false;
    }

    QJsonParseError parseError;
    const QByteArray fileBytes = f.readAll();
    const QJsonDocument document = QJsonDocument::fromJson(fileBytes, &parseError);
    f.close();
    if (parseError.error != QJsonParseError::NoError) {
        m_lastError = QString("Invalid project JSON at byte %1: %2")
                          .arg(parseError.offset)
                          .arg(parseError.errorString());
        return false;
    }
    if (!document.isObject()) {
        m_lastError = "Invalid project: the top-level JSON value must be an object.";
        return false;
    }

    const QJsonObject outerRoot = document.object();
    QJsonObject root = outerRoot;
    bool readOnlyProject3 = false;

    // ProjectModel remains the legacy/v2 flat-board editor. Normal editing
    // rejects project/3 to prevent a later save from flattening and destroying
    // its mechanical/rationale data. The explicit verification-only path may
    // read the embedded board, but binds fileSha256 to the exact outer wrapper
    // and marks the model unsaveable.
    if (root.contains("format")) {
        const QString format = root.value("format").toString();
        if (format == "design-studio.project/3") {
            if (!allowProject3ForVerification) {
                m_lastError =
                    "Canonical design-studio.project/3 documents are not supported "
                    "by the Qt legacy/v2 editor. The file was left unchanged.";
                return false;
            }
            if (root.value("units").toString() != QStringLiteral("nm")
                || !root.value("board").isObject()) {
                m_lastError =
                    "Invalid design-studio.project/3 verification input: units must be nm "
                    "and board must be an embedded object.";
                return false;
            }
            root = root.value("board").toObject();
            readOnlyProject3 = true;
        } else {
            m_lastError = QString("Unsupported project format '%1'. The file was left unchanged.")
                              .arg(format.isEmpty() ? QString("<invalid>") : format);
            return false;
        }
    }

    int version = 1; // version-less flat files are the supported v1 legacy form
    if (root.contains("version")) {
        const QJsonValue value = root.value("version");
        const double rawVersion = value.toDouble(-1);
        if (!value.isDouble() || !std::isfinite(rawVersion)
            || std::floor(rawVersion) != rawVersion) {
            m_lastError = "Invalid project version: expected integer legacy version 1 or 2.";
            return false;
        }
        if (rawVersion != 1.0 && rawVersion != 2.0) {
            m_lastError = QString("Unsupported flat project version %1; this editor supports only 1 and 2. "
                                  "The file was left unchanged.")
                              .arg(QString::number(rawVersion, 'g', 16));
            return false;
        }
        version = static_cast<int>(rawVersion);
    }
    if (version != 1 && version != 2) {
        m_lastError = QString("Unsupported flat project version %1; this editor supports only 1 and 2. "
                              "The file was left unchanged.")
                          .arg(version);
        return false;
    }

    // Refuse arbitrary JSON that merely lacks a format marker. Every supported
    // v1/v2 project has board dimensions plus the two primary geometry arrays.
    if (!root.value("board_width_mm").isDouble()
        || !root.value("board_height_mm").isDouble()
        || !root.value("footprints").isArray()
        || !root.value("traces").isArray()) {
        m_lastError = "Unrecognized legacy project structure; required board dimensions, "
                      "footprints, and traces are missing or have invalid types.";
        return false;
    }

    QString loadedDocumentId;
    const bool loadedIdentityPersisted = root.contains("document_id")
        && root.contains("revision");
    if (root.contains("document_id")) {
        if (!root.value("document_id").isString()) {
            m_lastError = "Invalid project document_id: expected a stable identifier string.";
            return false;
        }
        loadedDocumentId = root.value("document_id").toString();
        static const QRegularExpression stableId(
            QStringLiteral("^[A-Za-z][A-Za-z0-9._:-]{0,127}$"));
        if (!stableId.match(loadedDocumentId).hasMatch()) {
            m_lastError = "Invalid project document_id: expected a stable identifier.";
            return false;
        }
    } else {
        // Legacy v1/v2 files predate project identity. Give the in-memory
        // document an identity now; the next explicit save persists it.
        loadedDocumentId = QStringLiteral("project:")
            + QUuid::createUuid().toString(QUuid::WithoutBraces);
    }

    qint64 loadedRevision = 0;
    if (root.contains("revision")) {
        const QJsonValue revisionValue = root.value("revision");
        const double rawRevision = revisionValue.toDouble(-1);
        if (!revisionValue.isDouble() || !std::isfinite(rawRevision)
            || std::floor(rawRevision) != rawRevision || rawRevision < 0
            || rawRevision > 9007199254740991.0) {
            m_lastError = "Invalid project revision: expected a non-negative exact JSON integer.";
            return false;
        }
        loadedRevision = static_cast<qint64>(rawRevision);
    }

    const ProjPcbRuleProfile loadedPcbRules = root.contains("pcb_rules")
        ? parsePcbRuleProfile(root.value("pcb_rules").toObject()) : defaultPcbRules();
    const QString ruleError = validatePcbRuleProfile(loadedPcbRules);
    if (!ruleError.isEmpty()) {
        m_lastError = "Invalid PCB rule profile: " + ruleError + ". The file was left unchanged.";
        return false;
    }
    const QString placementError = validatePlacementJson(root);
    if (!placementError.isEmpty()) {
        m_lastError = "Invalid placement constraints: " + placementError + ". The file was left unchanged.";
        return false;
    }
    const QString mechanicalError = validateMechanicalContractJson(root);
    if (!mechanicalError.isEmpty()) {
        m_lastError = "Invalid FreeCAD mechanical contract: " + mechanicalError
            + ". The file was left unchanged.";
        return false;
    }
    const QString routingError = validateRoutingJson(root);
    if (!routingError.isEmpty()) {
        m_lastError = "Invalid routing constraints: " + routingError + ". The file was left unchanged.";
        return false;
    }
    const QString verificationError = validateVerificationJson(root);
    if (!verificationError.isEmpty()) {
        m_lastError = "Invalid verification requirements: " + verificationError
            + ". The file was left unchanged.";
        return false;
    }

    // No member state is changed before all boundary checks above succeed.
    // This makes a failed open non-destructive to the active in-memory project.

    boardWidthMm  = root["board_width_mm"].toDouble(100);
    boardHeightMm = root["board_height_mm"].toDouble(80);
    boardOutline.clear(); boardCutouts.clear();
    for (auto pv : root["board_outline_pts"].toArray()) {
        const QJsonArray pt = pv.toArray();
        if (pt.size() == 2) boardOutline.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
    }
    for (auto cv : root["board_cutouts"].toArray()) {
        QVector<QPointF> cutout;
        for (auto pv : cv.toArray()) {
            const QJsonArray pt = pv.toArray();
            if (pt.size() == 2) cutout.append(QPointF(pt[0].toDouble(), pt[1].toDouble()));
        }
        if (cutout.size() >= 3) boardCutouts.append(cutout);
    }
    gridMm        = root["grid_mm"].toDouble(1.27);
    copperLayers  = root["copper_layers"].toInt(2);
    erDielectric  = root["dielectric_er"].toDouble(4.4);
    dielectricHMm = root["dielectric_h_mm"].toDouble(0.2);
    lossTangent   = root["loss_tangent"].toDouble(0.02);
    copperTMm     = root["copper_t_mm"].toDouble(0.035);
    pcbRules      = loadedPcbRules;
    const QJsonObject verification = root.value("verification_requirements").toObject();
    verificationRequirements.requireSignalIntegrity = verification["require_signal_integrity"].toBool(false);
    verificationRequirements.requirePowerIntegrity = verification["require_power_integrity"].toBool(false);
    verificationRequirements.requireThermal = verification["require_thermal"].toBool(false);
    verificationRequirements.requireComponentSemantics =
        verification["require_component_semantics"].toBool(true);
    verificationRequirements.requireEnclosureEvidence = verification["require_enclosure_evidence"].toBool(false);
    verificationRequirements.enclosureEvidencePath = verification["enclosure_evidence_path"].toString();
    verificationRequirements.enclosureEvidenceSha256 = verification["enclosure_evidence_sha256"].toString();

    nets.clear();
    for (auto v : root["net_table"].toArray())
        nets.append(parseNet(v.toObject()));
    if (nets.isEmpty()) {
        // v1 migration: the legacy `nets` array stored names by index.
        const QJsonArray legacyNets = root.value("nets").toArray();
        for (qsizetype i = 0; i < legacyNets.size(); ++i) {
            if (!legacyNets.at(i).isString()) continue;
            ProjNet net;
            net.id = static_cast<int>(i);
            net.name = legacyNets.at(i).toString();
            net.classId = 0;
            nets.append(net);
        }
    }

    netClasses.clear();
    for (auto v : root["net_classes"].toArray())
        netClasses.append(parseNetClass(v.toObject()));
    if (netClasses.isEmpty()) {
        ProjNetClass def; def.id=0; def.name="Default";
        def.clearanceMm=0.2; def.traceWidthMm=0.25;
        def.viaDiaMm=0.6; def.viaDrillMm=0.3;
        netClasses.append(def);
    }
    ruleAreas.clear();
    for (auto v : root["rule_areas"].toArray()) {
        ProjRuleArea area = parseRuleArea(v.toObject());
        if (area.points.size() >= 3) ruleAreas.append(area);
    }
    classPairRules.clear();
    for (auto v : root["class_pair_rules"].toArray()) {
        ProjClassPairRule rule = parseClassPairRule(v.toObject());
        if (rule.clearanceMm > 0) classPairRules.append(rule);
    }
    layerPolicies.clear();
    for (const auto& v : root["layer_policies"].toArray())
        layerPolicies.append(parseLayerPolicy(v.toObject()));
    copperZones.clear();
    for (const auto& v : root["copper_zones"].toArray()) {
        ProjCopperZone zone = parseCopperZone(v.toObject());
        if (zone.points.size() >= 3) copperZones.append(zone);
    }

    footprints.clear();
    for (auto v : root["footprints"].toArray())
        footprints.append(parseFp(v.toObject()));

    traces.clear();
    for (auto v : root["traces"].toArray())
        traces.append(parseTrace(v.toObject()));

    vias.clear();
    if (root.contains("vias"))
        for (auto v : root["vias"].toArray())
            vias.append(parseVia(v.toObject()));
    unresolvedComponents = root.value("unresolved_components").toArray();
    placementState = root.value("placement_state").toObject();
    mechanicalContract = root.value("mechanical_contract").toObject();

    // Schematic block (optional — absent in older/PCB-only projects)
    symbols.clear();
    schWires.clear();
    if (root.contains("schematic")) {
        QJsonObject sch = root["schematic"].toObject();
        for (auto v : sch["symbols"].toArray())
            symbols.append(parseSymbol(v.toObject()));
        for (auto v : sch["wires"].toArray())
            schWires.append(parseWire(v.toObject()));
    }

    // Preserve every root and schematic field not authored by this editor.
    // This keeps release, fabrication, mechanical, acceptance, provenance, and
    // future-schema data lossless across an ordinary load/edit/save cycle.
    m_rootExtensions = root;
    const QStringList ownedRootKeys{
        "version", "document_id", "revision", "board_width_mm", "board_height_mm",
        "board_outline_pts", "board_cutouts", "grid_mm", "copper_layers",
        "dielectric_er", "dielectric_h_mm", "loss_tangent", "copper_t_mm",
        "pcb_rules", "verification_requirements", "nets", "net_table",
        "net_classes", "rule_areas", "class_pair_rules", "layer_policies",
        "copper_zones", "footprints", "traces", "vias", "unresolved_components",
        "placement_state", "mechanical_contract", "schematic"};
    for (const QString& key : ownedRootKeys)
        m_rootExtensions.remove(key);
    m_schematicExtensions = root.value("schematic").toObject();
    m_schematicExtensions.remove("symbols");
    m_schematicExtensions.remove("wires");

    m_filePath = path;
    m_documentId = loadedDocumentId;
    m_revision = loadedRevision;
    m_identityPersisted = loadedIdentityPersisted;
    m_readOnlyProject3 = readOnlyProject3;
    m_fileSha256 = QString::fromLatin1(
        QCryptographicHash::hash(fileBytes, QCryptographicHash::Sha256).toHex());
    m_modified = false;
    m_lastError.clear();
    emit loaded();
    return true;
}

bool ProjectModel::saveToFile(const QString& path) {
    if (path.trimmed().isEmpty()) {
        m_lastError = QStringLiteral("Could not write project: path is empty.");
        return false;
    }

    if (m_readOnlyProject3) {
        m_lastError =
            "The project/3 verification adapter is read-only; apply changes through a "
            "lossless project/3 writer. The canonical file was left unchanged.";
        return false;
    }
    QJsonArray netArr, ncArr, ruleAreaArr, pairRuleArr, layerPolicyArr, zoneArr, fpArr, trArr, viaArr;
    for (auto& n  : nets)       netArr.append(toJson(n));
    for (auto& nc : netClasses) ncArr.append(toJson(nc));
    for (auto& area : ruleAreas) ruleAreaArr.append(toJson(area));
    for (auto& rule : classPairRules) pairRuleArr.append(toJson(rule));
    for (auto& policy : layerPolicies) layerPolicyArr.append(toJson(policy));
    for (auto& zone : copperZones) zoneArr.append(toJson(zone));
    for (auto& fp : footprints) fpArr.append(toJson(fp));
    for (auto& t  : traces)     trArr.append(toJson(t));
    for (auto& v  : vias)       viaArr.append(toJson(v));
    QJsonArray outlineArr, cutoutArr;
    for (const auto& p : boardOutline) outlineArr.append(QJsonArray{p.x(), p.y()});
    for (const auto& cutout : boardCutouts) {
        QJsonArray points;
        for (const auto& p : cutout) points.append(QJsonArray{p.x(), p.y()});
        cutoutArr.append(points);
    }

    QJsonObject root = m_rootExtensions;
    const QJsonObject owned{
        {"version",        2},
        {"document_id",    m_documentId},
        {"revision",       m_revision},
        {"board_width_mm", boardWidthMm},
        {"board_height_mm",boardHeightMm},
        {"board_outline_pts", outlineArr},
        {"board_cutouts", cutoutArr},
        {"grid_mm",        gridMm},
        {"copper_layers",  copperLayers},
        {"dielectric_er",  erDielectric},
        {"dielectric_h_mm",dielectricHMm},
        {"loss_tangent",   lossTangent},
        {"copper_t_mm",    copperTMm},
        {"pcb_rules",      toJson(pcbRules)},
        {"verification_requirements", QJsonObject{
            {"require_signal_integrity", verificationRequirements.requireSignalIntegrity},
            {"require_power_integrity", verificationRequirements.requirePowerIntegrity},
            {"require_thermal", verificationRequirements.requireThermal},
            {"require_component_semantics", verificationRequirements.requireComponentSemantics},
            {"require_enclosure_evidence", verificationRequirements.requireEnclosureEvidence},
            {"enclosure_evidence_path", verificationRequirements.enclosureEvidencePath},
            {"enclosure_evidence_sha256", verificationRequirements.enclosureEvidenceSha256}
        }},
        {"nets",           QJsonValue::Null},
        {"net_table",      netArr},
        {"net_classes",    ncArr},
        {"rule_areas",     ruleAreaArr},
        {"class_pair_rules", pairRuleArr},
        {"layer_policies", layerPolicyArr},
        {"copper_zones", zoneArr},
        {"footprints",     fpArr},
        {"traces",         trArr},
        {"vias",           viaArr},
        {"unresolved_components", unresolvedComponents},
        {"placement_state", placementState},
    };
    for (auto it = owned.begin(); it != owned.end(); ++it)
        root.insert(it.key(), it.value());
    if (!mechanicalContract.isEmpty())
        root["mechanical_contract"] = mechanicalContract;

    // Schematic block — only written when symbols/wires exist, so PCB-only
    // projects keep their existing on-disk shape.
    if (!symbols.isEmpty() || !schWires.isEmpty() || !m_schematicExtensions.isEmpty()) {
        QJsonArray symArr, wireArr;
        for (auto& s : symbols)  symArr.append(toJson(s));
        for (auto& w : schWires) wireArr.append(toJson(w));
        QJsonObject schematic = m_schematicExtensions;
        schematic["symbols"] = symArr;
        schematic["wires"] = wireArr;
        root["schematic"] = schematic;
    }

    QSaveFile f(path);
    if (!f.open(QIODevice::WriteOnly)) {
        m_lastError = QString("Could not write '%1': %2").arg(path, f.errorString());
        return false;
    }
    const QByteArray bytes = QJsonDocument(root).toJson();
    if (f.write(bytes) != bytes.size()) {
        m_lastError = QString("Could not write complete project '%1': %2")
                          .arg(path, f.errorString());
        f.cancelWriting();
        return false;
    }
    if (!f.commit()) {
        m_lastError = QString("Could not commit project '%1': %2").arg(path, f.errorString());
        return false;
    }

    m_filePath = path;
    m_fileSha256 = QString::fromLatin1(
        QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex());
    m_identityPersisted = true;
    m_modified = false;
    m_lastError.clear();
    return true;
}
