#include "CoreBridge.h"
#include <QMap>
#include <QPointF>
#include <QRectF>
#include <QJsonArray>
#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QDir>
#include <QCryptographicHash>
#include <QSet>
#include <dlfcn.h>
#include <cstring>
#include <vector>
#include <cmath>
#include <algorithm>
#include <limits>

static constexpr double NM = 1e6;  // nm per mm

// ── Factory ───────────────────────────────────────────────────────────────────
CoreBridge* CoreBridge::create(const QString& libPath, QString& errOut) {
    auto* b = new CoreBridge();
    b->m_lib = dlopen(libPath.toUtf8().constData(), RTLD_LAZY | RTLD_LOCAL);
    if (!b->m_lib) { errOut = dlerror(); delete b; return nullptr; }
    if (!b->loadSymbols(errOut)) { dlclose(b->m_lib); delete b; return nullptr; }

    b->m_board = b->fn_boardCreate();
    if (!b->m_board) { errOut = "dc_board_create returned null"; dlclose(b->m_lib); delete b; return nullptr; }
    return b;
}

CoreBridge::~CoreBridge() {
    if (m_board && fn_boardDestroy) fn_boardDestroy(m_board);
    if (m_lib) dlclose(m_lib);
}

// ── Symbol loader ─────────────────────────────────────────────────────────────
#define LOAD(ptr, sym) \
    ptr = reinterpret_cast<decltype(ptr)>(dlsym(m_lib, sym)); \
    if (!ptr) { errOut = "Missing symbol: " + QString(sym); return false; }

bool CoreBridge::loadSymbols(QString& errOut) {
    LOAD(fn_boardCreate,      "dc_board_create")
    LOAD(fn_boardDestroy,     "dc_board_destroy")
    LOAD(fn_boardSetOutline,  "dc_board_set_outline")
    fn_boardSetOutlinePolygon = reinterpret_cast<decltype(fn_boardSetOutlinePolygon)>(
        dlsym(m_lib, "dc_board_set_outline_polygon"));
    fn_boardCutoutAdd = reinterpret_cast<decltype(fn_boardCutoutAdd)>(
        dlsym(m_lib, "dc_board_cutout_add"));
    LOAD(fn_boardSetLayers,   "dc_board_set_copper_layers")
    LOAD(fn_netSet,           "dc_net_set")
    LOAD(fn_netIslands,       "dc_net_islands")
    LOAD(fn_classSet,         "dc_class_set")
    fn_classRoutingPolicySet = reinterpret_cast<decltype(fn_classRoutingPolicySet)>(
        dlsym(m_lib, "dc_class_routing_policy_set"));
    fn_layerPolicySet = reinterpret_cast<decltype(fn_layerPolicySet)>(
        dlsym(m_lib, "dc_layer_policy_set"));
    fn_ruleAreaSet = reinterpret_cast<decltype(fn_ruleAreaSet)>(dlsym(m_lib, "dc_rule_area_set"));
    fn_ruleAreaHeightSet = reinterpret_cast<decltype(fn_ruleAreaHeightSet)>(
        dlsym(m_lib, "dc_rule_area_height_set"));
    fn_classPairSet = reinterpret_cast<decltype(fn_classPairSet)>(dlsym(m_lib, "dc_class_pair_set"));
    fn_copperZoneSet = reinterpret_cast<decltype(fn_copperZoneSet)>(dlsym(m_lib, "dc_copper_zone_set"));
    LOAD(fn_footprintAdd,     "dc_footprint_add")
    fn_footprintAddV2 = reinterpret_cast<decltype(fn_footprintAddV2)>(
        dlsym(m_lib, "dc_footprint_add_v2"));
    fn_footprintRegionAdd = reinterpret_cast<decltype(fn_footprintRegionAdd)>(
        dlsym(m_lib, "dc_footprint_region_add"));
    fn_footprintGeometrySet = reinterpret_cast<decltype(fn_footprintGeometrySet)>(
        dlsym(m_lib, "dc_footprint_geometry_set"));
    fn_footprintPlacementSet = reinterpret_cast<decltype(fn_footprintPlacementSet)>(
        dlsym(m_lib, "dc_footprint_placement_set"));
    fn_padClearanceSet = reinterpret_cast<decltype(fn_padClearanceSet)>(
        dlsym(m_lib, "dc_pad_clearance_set"));
    LOAD(fn_traceAdd,         "dc_trace_add")
    fn_traceMinWidthSet = reinterpret_cast<decltype(fn_traceMinWidthSet)>(
        dlsym(m_lib, "dc_trace_min_width_set"));
    LOAD(fn_viaAdd,           "dc_via_add")
    fn_viaAddV2 = reinterpret_cast<decltype(fn_viaAddV2)>(dlsym(m_lib, "dc_via_add_v2"));
    LOAD(fn_routeRun,         "dc_route_run")
    LOAD(fn_routePointCount,  "dc_route_point_count")
    LOAD(fn_routeGetPoints,   "dc_route_get_points")
    LOAD(fn_routeViaCount,    "dc_route_via_count")
    LOAD(fn_routeGetVias,     "dc_route_get_vias")
    LOAD(fn_interactiveBegin, "dc_interactive_route_begin")
    LOAD(fn_interactiveUpdate, "dc_interactive_route_update")
    LOAD(fn_interactivePlaceVia, "dc_interactive_route_place_via")
    LOAD(fn_interactiveCommit, "dc_interactive_route_commit")
    LOAD(fn_interactiveCancel, "dc_interactive_route_cancel")
    LOAD(fn_interactiveInfo, "dc_interactive_route_preview_info")
    LOAD(fn_interactiveGetTraces, "dc_interactive_route_get_traces")
    LOAD(fn_interactiveGetVias, "dc_interactive_route_get_vias")
    LOAD(fn_interactiveGetMoved, "dc_interactive_route_get_moved")
    LOAD(fn_drcRun,           "dc_drc_run")
    fn_drcRunV2 = reinterpret_cast<decltype(fn_drcRunV2)>(dlsym(m_lib, "dc_drc_run_v2"));
    LOAD(fn_drcGet,           "dc_drc_get")
    LOAD(fn_pourRun,          "dc_pour_run")
    fn_pourZoneRun = reinterpret_cast<decltype(fn_pourZoneRun)>(dlsym(m_lib, "dc_pour_zone_run"));
    LOAD(fn_pourGet,          "dc_pour_get")
    LOAD(fn_physMicrostrip,   "dc_phys_microstrip")
    LOAD(fn_physDiffMicrostrip, "dc_phys_diff_microstrip")
    LOAD(fn_physIpcCurrent,   "dc_phys_ipc2221_current")
    return true;
}

// ── Rebuild board from Qt model ───────────────────────────────────────────────
bool CoreBridge::rebuildBoard(ProjectModel* m) {
    if (m_board) { fn_boardDestroy(m_board); m_board = nullptr; }
    m_board = fn_boardCreate();
    if (!m_board) return false;
    m_traceModelIndex.clear();
    m_viaModelIndex.clear();

    if (fn_boardSetLayers(m_board, m->copperLayers) != 0) return false;
    if (fn_boardSetOutline(m_board,
        0, 0,
        (int64_t)(m->boardWidthMm  * NM),
        (int64_t)(m->boardHeightMm * NM)) != 0) return false;
    if (!m->boardOutline.isEmpty()) {
        if (!fn_boardSetOutlinePolygon || m->boardOutline.size() < 3) return false;
        std::vector<DcPoint> points;
        points.reserve(m->boardOutline.size());
        for (const auto& p : m->boardOutline)
            points.push_back({(int64_t)(p.x() * NM), (int64_t)(p.y() * NM)});
        if (fn_boardSetOutlinePolygon(m_board, points.data(), (int32_t)points.size()) != 0)
            return false;
    }
    if (!m->boardCutouts.isEmpty() && !fn_boardCutoutAdd) return false;
    for (const auto& cutout : m->boardCutouts) {
        if (cutout.size() < 3) return false;
        std::vector<DcPoint> points;
        points.reserve(cutout.size());
        for (const auto& p : cutout)
            points.push_back({(int64_t)(p.x() * NM), (int64_t)(p.y() * NM)});
        if (fn_boardCutoutAdd(m_board, points.data(), (int32_t)points.size()) != 0)
            return false;
    }

    for (auto& nc : m->netClasses) {
        if (fn_classSet(m_board, nc.id, nc.name.toUtf8().constData(),
            (int64_t)(nc.clearanceMm  * NM),
            (int64_t)(nc.traceWidthMm * NM),
            (int64_t)(nc.viaDiaMm     * NM),
            (int64_t)(nc.viaDrillMm   * NM),
            (int64_t)(nc.diffPairGapMm * NM),
            (int64_t)(nc.maxSkewMm     * NM),
            nc.allowMicrovia ? 1 : 0) != 0) return false;
        uint64_t layerMask = nc.allowedLayers.isEmpty()
            ? (m->copperLayers >= 64 ? ~uint64_t(0) : ((uint64_t(1) << m->copperLayers) - 1)) : 0;
        for (int layer : nc.allowedLayers) {
            if (layer < 0 || layer >= m->copperLayers || layer >= 64) return false;
            layerMask |= uint64_t(1) << layer;
        }
        uint32_t viaMask = 0;
        for (const auto& type : nc.allowedViaTypes) {
            if (type == "through") viaMask |= 1u;
            else if (type == "blind") viaMask |= 2u;
            else if (type == "buried") viaMask |= 4u;
            else if (type == "microvia") viaMask |= 8u;
            else return false;
        }
        const bool customPolicy = !nc.allowedLayers.isEmpty() || viaMask != 0x0f || nc.maxViaCount > 0;
        if (customPolicy && (!fn_classRoutingPolicySet
            || fn_classRoutingPolicySet(m_board, nc.id, layerMask, viaMask, nc.maxViaCount) != 0))
            return false;
    }

    if (!m->layerPolicies.isEmpty() && !fn_layerPolicySet) return false;
    for (const auto& policy : m->layerPolicies) {
        const int role = policy.role == "plane" ? 1 : policy.role == "mixed" ? 2 : 0;
        const int direction = policy.preferredDirection == "horizontal" ? 1
                            : policy.preferredDirection == "vertical" ? 2 : 0;
        if (fn_layerPolicySet(m_board, policy.layer, policy.name.toUtf8().constData(),
                role, direction, policy.allowRouting ? 1 : 0,
                (int64_t)(policy.copperThicknessMm * NM),
                policy.source.toUtf8().constData(),
                policy.sourceRevision.toUtf8().constData()) != 0) return false;
    }

    if (!m->ruleAreas.isEmpty() && !fn_ruleAreaSet) return false;
    for (const auto& area : m->ruleAreas) {
        if (area.points.size() < 3) return false;
        std::vector<DcPoint> points;
        points.reserve(area.points.size());
        for (const auto& p : area.points)
            points.push_back({(int64_t)(p.x() * NM), (int64_t)(p.y() * NM)});
        if (fn_ruleAreaSet(m_board, area.id, area.name.toUtf8().constData(),
                points.data(), (int32_t)points.size(), area.layer,
                (int64_t)(area.clearanceMm * NM),
                (int64_t)(area.minTraceWidthMm * NM),
                area.forbidRouting ? 1 : 0, area.forbidVias ? 1 : 0,
                area.forbidPlacement ? 1 : 0,
                area.source.toUtf8().constData(), area.sourceRevision.toUtf8().constData()) != 0)
            return false;
        if (area.maxHeightMm > 0) {
            if (!fn_ruleAreaHeightSet
                || fn_ruleAreaHeightSet(m_board, area.id,
                        (int64_t)(area.maxHeightMm * NM)) != 0)
                return false;
        }
    }
    if (!m->classPairRules.isEmpty() && !fn_classPairSet) return false;
    for (const auto& rule : m->classPairRules)
        if (fn_classPairSet(m_board, rule.classA, rule.classB,
                (int64_t)(rule.clearanceMm * NM), rule.source.toUtf8().constData(),
                rule.sourceRevision.toUtf8().constData()) != 0) return false;

    for (auto& n : m->nets)
        if (fn_netSet(m_board, n.id, n.name.toUtf8().constData(), n.classId) != 0)
            return false;

    if (!m->copperZones.isEmpty() && !fn_copperZoneSet) return false;
    for (const auto& zone : m->copperZones) {
        std::vector<DcPoint> points;
        points.reserve(zone.points.size());
        for (const auto& p : zone.points)
            points.push_back({(int64_t)(p.x() * NM), (int64_t)(p.y() * NM)});
        if (points.size() < 3 || fn_copperZoneSet(m_board, zone.id,
                zone.name.toUtf8().constData(), points.data(), (int32_t)points.size(),
                zone.netId, zone.layer, (int64_t)(zone.clearanceMm * NM),
                zone.minIslandAreaMm2, zone.requireConnection ? 1 : 0,
                zone.source.toUtf8().constData(), zone.sourceRevision.toUtf8().constData()) != 0)
            return false;
    }

    for (auto& fp : m->footprints) {
        // Build DcPadDef array
        std::vector<DcPadDef> pads;
        pads.reserve(fp.pads.size());
        for (auto& p : fp.pads) {
            DcPadDef d{};
            d.x          = (int64_t)(p.x_mm   * NM);
            d.y          = (int64_t)(p.y_mm   * NM);
            d.w          = (int64_t)(p.w_mm   * NM);
            d.h          = (int64_t)(p.h_mm   * NM);
            d.netId      = p.netId;
            d.throughHole= p.throughHole ? 1 : 0;
            d.drill      = (int64_t)(p.drillMm * NM);
            std::strncpy(d.name, p.name.toUtf8().constData(), 15);
            pads.push_back(d);
        }
        uint64_t footprintId = 0;
        if (fn_footprintAddV2) {
            std::vector<DcPadDefV2> padsV2;
            padsV2.reserve(fp.pads.size());
            for (const auto& p : fp.pads) {
                DcPadDefV2 d{};
                d.x = (int64_t)(p.x_mm * NM); d.y = (int64_t)(p.y_mm * NM);
                d.w = (int64_t)(p.w_mm * NM); d.h = (int64_t)(p.h_mm * NM);
                d.netId = p.netId; d.throughHole = p.throughHole ? 1 : 0;
                d.drill = (int64_t)(p.drillMm * NM);
                d.shape = p.shape == "roundrect" ? 1 : p.shape == "oval" ? 2
                        : p.shape == "circle" ? 3 : 0;
                d.cornerRadius = (int64_t)(p.cornerR_mm * NM);
                std::strncpy(d.name, p.name.toUtf8().constData(), 15);
                padsV2.push_back(d);
            }
            footprintId = fn_footprintAddV2(m_board, fp.ref.toUtf8().constData(), fp.lib.toUtf8().constData(),
                (int64_t)(fp.x_mm * NM), (int64_t)(fp.y_mm * NM), fp.rotDeg, fp.side,
                padsV2.empty() ? nullptr : padsV2.data(), (int32_t)padsV2.size());
        } else {
            footprintId = fn_footprintAdd(m_board,
                fp.ref.toUtf8().constData(), fp.lib.toUtf8().constData(),
                (int64_t)(fp.x_mm * NM), (int64_t)(fp.y_mm * NM), fp.rotDeg, fp.side,
                pads.empty() ? nullptr : pads.data(), (int32_t)pads.size());
        }
        if (footprintId == 0) return false;
        if (!fp.regions.isEmpty() && (!fn_footprintRegionAdd || footprintId == 0)) return false;
        for (const auto& region : fp.regions) {
            std::vector<DcPoint> outer, hole;
            outer.reserve(region.points.size()); hole.reserve(region.hole.size());
            for (const auto& p : region.points)
                outer.push_back({(int64_t)(p.x() * NM), (int64_t)(p.y() * NM)});
            for (const auto& p : region.hole)
                hole.push_back({(int64_t)(p.x() * NM), (int64_t)(p.y() * NM)});
            if (fn_footprintRegionAdd(m_board, footprintId, region.netId,
                    outer.data(), (int32_t)outer.size(),
                    hole.empty() ? nullptr : hole.data(), (int32_t)hole.size()) != 0)
                return false;
        }
        if ((fp.bodyW_mm > 0 || fp.bodyH_mm > 0 || !fp.courtyard.isEmpty())
            && (!fn_footprintGeometrySet || footprintId == 0)) return false;
        if (fn_footprintGeometrySet) {
            std::vector<DcPoint> courtyard;
            courtyard.reserve(fp.courtyard.size());
            for (const auto& p : fp.courtyard)
                courtyard.push_back({(int64_t)(p.x() * NM), (int64_t)(p.y() * NM)});
            if (fn_footprintGeometrySet(m_board, footprintId,
                    (int64_t)(fp.bodyCx_mm * NM), (int64_t)(fp.bodyCy_mm * NM),
                    (int64_t)(fp.bodyW_mm * NM), (int64_t)(fp.bodyH_mm * NM),
                    (int64_t)(fp.h3d_mm * NM),
                    courtyard.empty() ? nullptr : courtyard.data(), (int32_t)courtyard.size()) != 0)
                return false;
        }
        const bool hasPlacementConstraints = fp.placementLocked || !fp.functionalGroup.isEmpty()
            || !fp.edgeAnchor.isEmpty() || fp.thermalPowerW > 0 || fp.thermalClearanceMm > 0
            || fp.testAccessRequired || fp.testAccessHaloMm > 0;
        if (hasPlacementConstraints) {
            if (!fn_footprintPlacementSet || footprintId == 0
                || fn_footprintPlacementSet(m_board, footprintId,
                        fp.placementLocked ? 1 : 0,
                        fp.functionalGroup.toUtf8().constData(),
                        fp.edgeAnchor.toUtf8().constData(), fp.thermalPowerW,
                        (int64_t)(fp.thermalClearanceMm * NM),
                        fp.testAccessRequired ? 1 : 0,
                        (int64_t)(fp.testAccessHaloMm * NM)) != 0)
                return false;
        }
        for (const auto& pad : fp.pads) {
            if (pad.clearanceMm <= 0) continue;
            if (!fn_padClearanceSet
                || fn_padClearanceSet(m_board, footprintId, pad.name.toUtf8().constData(),
                                      (int64_t)(pad.clearanceMm * NM)) != 0)
                return false;
        }
    }

    for (int traceIndex = 0; traceIndex < m->traces.size(); ++traceIndex) {
        auto& t = m->traces[traceIndex];
        const uint64_t id = fn_traceAdd(m_board,
            (int64_t)(t.ax_mm    * NM), (int64_t)(t.ay_mm    * NM),
            (int64_t)(t.bx_mm    * NM), (int64_t)(t.by_mm    * NM),
            (int64_t)(t.width_mm * NM), t.layer, t.netId,
            t.isPour ? 1 : 0);
        if (id == 0) return false;
        if (fn_traceMinWidthSet && t.minWidthOverrideMm > 0)
            fn_traceMinWidthSet(m_board, id, (int64_t)(t.minWidthOverrideMm * NM));
        m_traceModelIndex.insert(id, traceIndex);
    }

    auto viaType = [](const QString& type) {
        if (type == "blind") return 1;
        if (type == "buried") return 2;
        if (type == "microvia") return 3;
        return 0;
    };
    for (int viaIndex = 0; viaIndex < m->vias.size(); ++viaIndex) {
        auto& v = m->vias[viaIndex];
        uint64_t viaId = 0;
        if (fn_viaAddV2)
            viaId = fn_viaAddV2(m_board,
                (int64_t)(v.x_mm * NM), (int64_t)(v.y_mm * NM),
                (int64_t)(v.diaMm * NM), (int64_t)(v.drillMm * NM),
                v.netId, v.fromLayer, v.toLayer, viaType(v.type));
        else
            viaId = fn_viaAdd(m_board,
                (int64_t)(v.x_mm * NM), (int64_t)(v.y_mm * NM),
                (int64_t)(v.diaMm * NM), (int64_t)(v.drillMm * NM),
                v.netId, v.fromLayer, v.toLayer);
        if (viaId == 0) return false;
        m_viaModelIndex.insert(viaId, viaIndex);
    }

    return true;
}

InteractivePreview CoreBridge::readInteractivePreview() {
    InteractivePreview result;
    DcInteractivePreviewInfo info{};
    if (fn_interactiveInfo(m_board, &info) != 0) {
        result.message = QStringLiteral("Interactive routing session is unavailable");
        return result;
    }
    result.accepted = info.accepted != 0;
    result.message = QString::fromUtf8(info.message);

    std::vector<DcInteractiveTrace> traces(std::max(0, info.traceCount));
    if (!traces.empty()) fn_interactiveGetTraces(m_board, traces.data(), info.traceCount);
    for (const auto& value : traces) {
        ProjTrace trace;
        trace.ax_mm = value.ax / NM; trace.ay_mm = value.ay / NM;
        trace.bx_mm = value.bx / NM; trace.by_mm = value.by / NM;
        trace.width_mm = value.width / NM; trace.layer = value.layer;
        trace.netId = value.netId;
        result.addedTraces.append(trace);
    }
    std::vector<DcInteractiveVia> vias(std::max(0, info.viaCount));
    if (!vias.empty()) fn_interactiveGetVias(m_board, vias.data(), info.viaCount);
    for (const auto& value : vias) {
        ProjVia via;
        via.x_mm = value.x / NM; via.y_mm = value.y / NM;
        via.diaMm = value.diameter / NM; via.drillMm = value.drill / NM;
        via.netId = value.netId; via.fromLayer = value.fromLayer;
        via.toLayer = value.toLayer;
        via.type = value.viaType == 3 ? QStringLiteral("microvia")
                 : value.viaType == 2 ? QStringLiteral("buried")
                 : value.viaType == 1 ? QStringLiteral("blind")
                                      : QStringLiteral("through");
        result.addedVias.append(via);
    }
    std::vector<DcInteractiveMovedItem> moved(std::max(0, info.movedCount));
    if (!moved.empty()) fn_interactiveGetMoved(m_board, moved.data(), info.movedCount);
    for (const auto& value : moved) {
        if (value.kind == 0 && m_traceModelIndex.contains(value.id)) {
            const int index = m_traceModelIndex.value(value.id);
            ProjTrace trace = m_interactiveModel && index < m_interactiveModel->traces.size()
                ? m_interactiveModel->traces[index] : ProjTrace{};
            trace.ax_mm = value.newAx / NM; trace.ay_mm = value.newAy / NM;
            trace.bx_mm = value.newBx / NM; trace.by_mm = value.newBy / NM;
            result.movedTraces.append({index, trace});
        } else if (value.kind == 1 && m_viaModelIndex.contains(value.id)) {
            const int index = m_viaModelIndex.value(value.id);
            ProjVia via = m_interactiveModel && index < m_interactiveModel->vias.size()
                ? m_interactiveModel->vias[index] : ProjVia{};
            via.x_mm = value.newAx / NM; via.y_mm = value.newAy / NM;
            result.movedVias.append({index, via});
        }
    }
    return result;
}

InteractivePreview CoreBridge::beginInteractiveRoute(ProjectModel* model,
                                                       const QPointF& start, int layer,
                                                       int netId, bool shove) {
    m_lastInteractivePreview = {};
    m_interactiveModel = model;
    if (!model || !rebuildBoard(model)) {
        m_lastInteractivePreview.message = QStringLiteral("Unable to build native board");
        return m_lastInteractivePreview;
    }
    const ProjNet* net = nullptr;
    for (const auto& candidate : model->nets)
        if (candidate.id == netId) { net = &candidate; break; }
    const ProjNetClass* rules = nullptr;
    const int classId = net ? net->classId : 0;
    for (const auto& candidate : model->netClasses)
        if (candidate.id == classId) { rules = &candidate; break; }
    ProjNetClass defaults;
    if (!rules) rules = &defaults;

    DcInteractiveRouteRequest request{};
    request.abiVersion = 1;
    request.structSize = sizeof(request);
    request.netId = netId; request.startLayer = layer;
    request.startX = int64_t(std::llround(start.x() * NM));
    request.startY = int64_t(std::llround(start.y() * NM));
    request.traceWidth = int64_t(std::llround(rules->traceWidthMm * NM));
    request.clearance = int64_t(std::llround(rules->clearanceMm * NM));
    request.gridStep = int64_t(std::llround(std::max(0.025, model->gridMm) * NM));
    request.viaDiameter = int64_t(std::llround(rules->viaDiaMm * NM));
    request.viaDrill = int64_t(std::llround(rules->viaDrillMm * NM));
    request.minDrillToDrill = int64_t(std::llround(model->pcbRules.minDrillToDrillMm * NM));
    request.allowVias = 1; request.allowMicrovia = rules->allowMicrovia ? 1 : 0;
    request.mode = shove ? 1 : 0;
    request.maxShoveDepth = 8; request.maxExpansions = 500000;
    fn_interactiveBegin(m_board, &request);
    m_lastInteractivePreview = readInteractivePreview();
    return m_lastInteractivePreview;
}

InteractivePreview CoreBridge::updateInteractiveRoute(const QPointF& cursor) {
    fn_interactiveUpdate(m_board, int64_t(std::llround(cursor.x() * NM)),
                         int64_t(std::llround(cursor.y() * NM)));
    m_lastInteractivePreview = readInteractivePreview();
    return m_lastInteractivePreview;
}

InteractivePreview CoreBridge::placeInteractiveVia(int targetLayer) {
    fn_interactivePlaceVia(m_board, targetLayer);
    m_lastInteractivePreview = readInteractivePreview();
    return m_lastInteractivePreview;
}

bool CoreBridge::commitInteractiveRoute(ProjectModel* model, QString& errorOut) {
    if (!model || !m_lastInteractivePreview.accepted || fn_interactiveCommit(m_board) != 0) {
        errorOut = m_lastInteractivePreview.message.isEmpty()
            ? QStringLiteral("No legal routing preview to commit")
            : m_lastInteractivePreview.message;
        return false;
    }
    for (const auto& move : m_lastInteractivePreview.movedTraces) {
        if (move.modelIndex < 0 || move.modelIndex >= model->traces.size()) continue;
        ProjTrace replacement = move.trace;
        replacement.width_mm = model->traces[move.modelIndex].width_mm;
        replacement.layer = model->traces[move.modelIndex].layer;
        replacement.netId = model->traces[move.modelIndex].netId;
        replacement.isPour = model->traces[move.modelIndex].isPour;
        replacement.minWidthOverrideMm = model->traces[move.modelIndex].minWidthOverrideMm;
        replacement.requiredCurrentA = model->traces[move.modelIndex].requiredCurrentA;
        model->traces[move.modelIndex] = replacement;
    }
    for (const auto& move : m_lastInteractivePreview.movedVias) {
        if (move.modelIndex < 0 || move.modelIndex >= model->vias.size()) continue;
        ProjVia replacement = model->vias[move.modelIndex];
        replacement.x_mm = move.via.x_mm; replacement.y_mm = move.via.y_mm;
        model->vias[move.modelIndex] = replacement;
    }
    model->traces += m_lastInteractivePreview.addedTraces;
    model->vias += m_lastInteractivePreview.addedVias;
    model->setModified(true);
    m_interactiveModel = nullptr;
    return true;
}

void CoreBridge::cancelInteractiveRoute() {
    if (m_board && fn_interactiveCancel) fn_interactiveCancel(m_board);
    m_lastInteractivePreview = {};
    m_interactiveModel = nullptr;
}

// Adaptive routing grid: uniform 25 µm is ~3.8 s per long route (measured) and
// impractical board-wide, but SHORT routes at 25 µm are cheap (few cells). So we
// pick the grid per route by its span — fine where it matters (threading
// fine-pitch pads, which are short hops), coarse where fine would be too slow.
static int64_t adaptiveGrid(double spanMm) {
    if (spanMm <  8.0) return (int64_t)(0.025 * 1e6);   // 25 µm — fine-pitch threading
    if (spanMm < 20.0) return (int64_t)(0.050 * 1e6);   // 50 µm
    if (spanMm < 40.0) return (int64_t)(0.100 * 1e6);   // 100 µm
    return (int64_t)(0.150 * 1e6);                       // 150 µm — long hauls
}
static int64_t adaptiveBudget(int64_t gridNm) {
    // Finer grid → more cells → larger budget, but short routes never reach it.
    if (gridNm <= 25'000)  return 1'500'000;
    if (gridNm <= 50'000)  return 2'000'000;
    if (gridNm <= 100'000) return 1'000'000;
    return 600'000;
}

// ── Auto-route (adaptive-grid A* with rip-up & reroute) ──────────────────────
RouteResult CoreBridge::autoRoute(ProjectModel* model) {
    QMutexLocker<QRecursiveMutex> lock(&m_boardMutex);
    RouteResult res;
    if (!rebuildBoard(model)) { res.message = "Board rebuild failed"; return res; }

    // Build ratsnest: for each net, find pad pairs and route them.
    // Each pad starts on the copper layer it physically lives on:
    //   • through-hole pads span the stack → start on layer 0 (router may via anywhere)
    //   • SMD pads on the bottom side → bottom copper (copperLayers-1)
    //   • SMD pads on the top side → layer 0
    // Without this, every bottom-side pad forces an immediate via-on-departure,
    // flooding the board with spurious vias.
    const int botLayer = std::max(0, model->copperLayers - 1);
    QMap<int, QVector<QPair<QPointF,int>>> netPads; // netId → [(pos, layer)]
    for (auto& fp : model->footprints) {
        for (auto& pad : fp.pads) {
            if (pad.netId < 0) continue;
            // Transform to board space
            double rad = fp.rotDeg * M_PI / 180.0;
            double px = fp.x_mm + pad.x_mm * cos(rad) - pad.y_mm * sin(rad);
            double py = fp.y_mm + pad.x_mm * sin(rad) + pad.y_mm * cos(rad);
            int padLayer = pad.throughHole ? 0 : (fp.side == 1 ? botLayer : 0);
            netPads[pad.netId].append({{px, py}, padLayer});
        }
    }

    // ── Build the route tasks: one per MST edge of each multi-pad net ──────────
    struct Task {
        int     netId;
        QPointF start, goal;
        int     sLayer, gLayer;
        double  tw, cl, vd, vdr;
        bool    allowMicrovia{false};
        double  spanMm;
        bool    routed = false;
        QVector<ProjTrace> traces;
        QVector<ProjVia>   vias;
        QRectF  bbox;        // routed-geometry bbox (or start/goal box if unrouted)
    };
    QVector<Task> tasks;

    for (auto it = netPads.begin(); it != netPads.end(); ++it) {
        auto& pts = it.value();
        if (pts.size() < 2) continue;

        int classId = 0;
        for (auto& n : model->nets)
            if (n.id == it.key()) { classId = n.classId; break; }
        double tw = 0.25, cl = 0.2, vd = 0.6, vdr = 0.3;
        bool allowMicrovia = false;
        for (auto& nc : model->netClasses)
            if (nc.id == classId) {
                tw=nc.traceWidthMm; cl=nc.clearanceMm; vd=nc.viaDiaMm;
                vdr=nc.viaDrillMm; allowMicrovia=nc.allowMicrovia; break;
            }

        // Minimum spanning tree (Prim's) over the pads → one task per MST edge.
        const int nP = pts.size();
        std::vector<bool> inTree(nP, false);
        inTree[0] = true;
        for (int added = 1; added < nP; ++added) {
            double best = 1e30; int bi = -1, bj = -1;
            for (int i = 0; i < nP; ++i) {
                if (!inTree[i]) continue;
                for (int j = 0; j < nP; ++j) {
                    if (inTree[j]) continue;
                    double dx = pts[i].first.x() - pts[j].first.x();
                    double dy = pts[i].first.y() - pts[j].first.y();
                    double d2 = dx*dx + dy*dy;
                    if (d2 < best) { best = d2; bi = i; bj = j; }
                }
            }
            if (bj < 0) break;
            inTree[bj] = true;
            Task t;
            t.netId  = it.key();
            t.start  = pts[bi].first; t.sLayer = pts[bi].second;
            t.goal   = pts[bj].first; t.gLayer = pts[bj].second;
            t.tw=tw; t.cl=cl; t.vd=vd; t.vdr=vdr;
            t.allowMicrovia = allowMicrovia;
            t.spanMm = std::hypot(t.goal.x()-t.start.x(), t.goal.y()-t.start.y());
            tasks.append(t);
        }
    }

    // Route long/hard nets first (more freedom on an emptier board; RnR can rip
    // them up later for shorter, more-constrained nets).
    std::sort(tasks.begin(), tasks.end(),
              [](const Task& a, const Task& b){ return a.spanMm > b.spanMm; });

    // ── Route one task against the CURRENT native board state ──────────────────
    auto routeOne = [&](Task& t) -> bool {
        DcRouteRequest req{};
        req.netId       = t.netId;
        req.sx          = (int64_t)(t.start.x() * NM);
        req.sy          = (int64_t)(t.start.y() * NM);
        req.startLayer  = t.sLayer;
        req.gx          = (int64_t)(t.goal.x()  * NM);
        req.gy          = (int64_t)(t.goal.y()  * NM);
        req.goalLayer   = t.gLayer;
        req.traceWidth  = (int64_t)(t.tw  * NM);
        req.clearance   = (int64_t)(t.cl  * NM);
        req.gridStep    = adaptiveGrid(t.spanMm);
        req.maxExpansions = adaptiveBudget(req.gridStep);
        req.allowVias   = 1;
        req.allowMicrovia = t.allowMicrovia ? 1 : 0;
        req.viaDiameter = (int64_t)(t.vd  * NM);
        req.viaDrill    = (int64_t)(t.vdr * NM);
        req.viaCostMm   = 3.0;
        req.minDrillToDrill = (int64_t)(model->pcbRules.minDrillToDrillMm * NM);

        t.traces.clear(); t.vias.clear(); t.routed = false;
        if (fn_routeRun(m_board, &req) != 0) return false;

        double minx=1e18, miny=1e18, maxx=-1e18, maxy=-1e18;
        int nPts = fn_routePointCount(m_board);
        std::vector<DcRoutePoint> rpts(nPts);
        fn_routeGetPoints(m_board, rpts.data(), nPts);
        for (int j = 1; j < nPts; ++j) {
            if (rpts[j].layer == rpts[j-1].layer) {
                ProjTrace tr;
                tr.ax_mm = rpts[j-1].x / NM; tr.ay_mm = rpts[j-1].y / NM;
                tr.bx_mm = rpts[j].x   / NM; tr.by_mm = rpts[j].y   / NM;
                tr.width_mm = t.tw; tr.layer = rpts[j].layer; tr.netId = t.netId;
                t.traces.append(tr);
                minx=std::min({minx,tr.ax_mm,tr.bx_mm}); maxx=std::max({maxx,tr.ax_mm,tr.bx_mm});
                miny=std::min({miny,tr.ay_mm,tr.by_mm}); maxy=std::max({maxy,tr.ay_mm,tr.by_mm});
            }
        }
        int nVias = fn_routeViaCount(m_board);
        std::vector<DcRouteVia> rvias(nVias);
        fn_routeGetVias(m_board, rvias.data(), nVias);
        for (auto& rv : rvias) {
            ProjVia v;
            v.x_mm = rv.x / NM; v.y_mm = rv.y / NM;
            v.diaMm = t.vd; v.drillMm = t.vdr; v.netId = t.netId;
            v.fromLayer = rv.fromLayer; v.toLayer = rv.toLayer;
            v.type = t.allowMicrovia && rv.toLayer - rv.fromLayer == 1
                ? "microvia" : "through";
            t.vias.append(v);
        }
        if (maxx >= minx) t.bbox = QRectF(QPointF(minx,miny), QPointF(maxx,maxy));
        t.routed = true;
        return true;
    };

    // Commit a routed task's geometry to the native board so later routes avoid it.
    auto commit = [&](const Task& t){
        for (auto& tr : t.traces)
            fn_traceAdd(m_board,
                (int64_t)(tr.ax_mm*NM),(int64_t)(tr.ay_mm*NM),
                (int64_t)(tr.bx_mm*NM),(int64_t)(tr.by_mm*NM),
                (int64_t)(tr.width_mm*NM), tr.layer, tr.netId, 0);
        for (auto& v : t.vias) {
            if (fn_viaAddV2)
                fn_viaAddV2(m_board,
                    (int64_t)(v.x_mm*NM),(int64_t)(v.y_mm*NM),
                    (int64_t)(v.diaMm*NM),(int64_t)(v.drillMm*NM),
                    v.netId, v.fromLayer, v.toLayer, v.type == "microvia" ? 3 : 0);
            else
                fn_viaAdd(m_board,
                    (int64_t)(v.x_mm*NM),(int64_t)(v.y_mm*NM),
                    (int64_t)(v.diaMm*NM),(int64_t)(v.drillMm*NM),
                    v.netId, v.fromLayer, v.toLayer);
        }
    };
    // Rip-up support: rebuild the board with the model + a chosen committed set.
    auto rebuildWith = [&](const QVector<int>& keep){
        rebuildBoard(model);
        for (int i : keep) commit(tasks[i]);
    };
    auto bboxOf = [&](const Task& t) -> QRectF {
        if (t.routed && t.bbox.isValid()) return t.bbox;
        return QRectF(t.start, t.goal).normalized();
    };

    // ── Pass 1: greedy route, committing each success so nets avoid each other ──
    QVector<int> committed, failed;
    for (int i = 0; i < tasks.size(); ++i) {
        if (routeOne(tasks[i])) { commit(tasks[i]); committed.append(i); }
        else                      failed.append(i);
    }

    // ── Rip-up & reroute: free a few blockers for each failed net, retry ───────
    const int MAX_RNR = 3;
    for (int pass = 0; pass < MAX_RNR && !failed.isEmpty(); ++pass) {
        QVector<int> stillFailed;
        bool progress = false;
        for (int fi : failed) {
            const QRectF fb = bboxOf(tasks[fi]).adjusted(-1,-1,1,1);
            // blockers = committed tasks (different net) whose geometry overlaps
            QVector<int> blockers;
            for (int c : committed)
                if (tasks[c].netId != tasks[fi].netId && bboxOf(tasks[c]).intersects(fb))
                    blockers.append(c);
            if (blockers.isEmpty()) { stillFailed.append(fi); continue; }
            if (blockers.size() > 6) blockers.resize(6);   // bound the disruption

            QVector<int> kept;
            for (int c : committed) if (!blockers.contains(c)) kept.append(c);
            rebuildWith(kept);                               // board WITHOUT blockers

            if (routeOne(tasks[fi])) {
                commit(tasks[fi]); kept.append(fi); progress = true;
                // Reroute the ripped blockers around the new trace.
                for (int b : blockers) {
                    if (routeOne(tasks[b])) { commit(tasks[b]); kept.append(b); }
                    else                      stillFailed.append(b);
                }
                committed = kept;
            } else {
                rebuildWith(committed);                      // restore, give up on fi
                stillFailed.append(fi);
            }
        }
        failed = stillFailed;
        if (!progress) break;
    }

    // ── Emit all committed geometry ────────────────────────────────────────────
    for (int i : committed) {
        res.newTraces += tasks[i].traces;
        res.newVias   += tasks[i].vias;
        res.segmentsAdded += tasks[i].traces.size();
        res.viasAdded     += tasks[i].vias.size();
    }
    res.routedConnections = committed.size();
    res.unroutedConnections = failed.size();
    res.ok = failed.isEmpty();
    res.message = QString("Routed %1/%2 connections, %3 segments, %4 vias%5")
        .arg(committed.size()).arg(tasks.size())
        .arg(res.segmentsAdded).arg(res.viasAdded)
        .arg(failed.isEmpty() ? "" : QString(" — %1 unroutable").arg(failed.size()));
    return res;
}

// ── DRC ──────────────────────────────────────────────────────────────────────
QVector<DrcViolation> CoreBridge::runDrc(ProjectModel* model) {
    QMutexLocker<QRecursiveMutex> lock(&m_boardMutex);
    QVector<DrcViolation> result;
    auto engineFailure = [&](const QString& rule, const QString& description) {
        DrcViolation v; v.severity = DrcViolation::Error; v.rule = rule; v.description = description;
        result.append(v);
    };
    const QString profileError = model->validatePcbRuleProfile();
    if (!profileError.isEmpty()) {
        DrcViolation v;
        v.severity = DrcViolation::Error;
        v.rule = "PROFILE_INVALID";
        v.description = profileError;
        result.append(v);
        return result;
    }
    if (!rebuildBoard(model)) {
        engineFailure("DRC_REBUILD_FAILED", "Native board rebuild rejected project geometry or constraints");
        return result;
    }

    DcDrcOptions opts{};
    const auto& profile = model->pcbRules;
    opts.defaultClearance = (int64_t)(profile.defaultClearanceMm * NM);
    opts.minTraceWidth    = (int64_t)(profile.minTraceWidthMm * NM);
    opts.minDrill         = (int64_t)(profile.minMechanicalDrillMm * NM);
    opts.minAnnularRing   = (int64_t)(profile.minAnnularRingMm * NM);
    opts.minDrillToDrill  = (int64_t)(profile.minDrillToDrillMm * NM);
    opts.minMicroviaDrill = (int64_t)(profile.minMicroviaDrillMm * NM);
    opts.minMicroviaWall  = (int64_t)(profile.minMicroviaWallMm * NM);
    opts.checkConnectivity = profile.checkConnectivity ? 1 : 0;
    opts.checkSkew         = profile.checkSkew ? 1 : 0;

    int count;
    if (profile.releaseRequiresNativeDrc && !fn_drcRunV2) {
        engineFailure("DRC_API_V2_REQUIRED",
            "Constraint profile requires the versioned native manufacturing DRC API");
        return result;
    }
    if (fn_drcRunV2) {
        DcDrcOptionsV2 v2{};
        v2.abiVersion = 2;
        v2.structSize = sizeof v2;
        v2.defaultClearance = opts.defaultClearance;
        v2.minTraceWidth = opts.minTraceWidth;
        v2.minDrill = opts.minDrill;
        v2.minAnnularRing = opts.minAnnularRing;
        v2.minDrillToDrill = opts.minDrillToDrill;
        v2.checkConnectivity = opts.checkConnectivity;
        v2.checkSkew = opts.checkSkew;
        v2.minMicroviaDrill = opts.minMicroviaDrill;
        v2.minMicroviaWall = opts.minMicroviaWall;
        v2.minCopperToEdge = (int64_t)(profile.minCopperToEdgeMm * NM);
        v2.minCopperToHole = (int64_t)(profile.minCopperToHoleMm * NM);
        v2.minCourtyardClearance = (int64_t)(profile.minCourtyardClearanceMm * NM);
        v2.checkUnassignedCopper = 1;
        count = fn_drcRunV2(m_board, &v2);
    } else {
        count = fn_drcRun(m_board, &opts);
    }
    if (count < 0) {
        engineFailure("DRC_ENGINE_FAILED", QString("Native DRC returned error %1").arg(count));
        return result;
    }

    for (int i = 0; i < count; ++i) {
        DcDrcViolation v{};
        if (fn_drcGet(m_board, i, &v) < 0) {
            engineFailure("DRC_RESULT_INCOMPLETE", "Native DRC result retrieval failed");
            break;
        }

        // Rule code → name map. Index = dc::DrcRule value (drc.h); rule codes
        // are 1-based, so slot 0 is unused. Keep in sync with the enum.
        static const char* ruleNames[] = {
            "?","TRACE_CLEARANCE","PAD_TRACE_CLEARANCE","PAD_CLEARANCE","BOARD_EDGE",
            "MIN_WIDTH","MIN_DRILL","VIA_TRACE_CLEARANCE","VIA_PAD_CLEARANCE",
            "VIA_CLEARANCE","ANNULAR_RING","DRILL_TO_DRILL","NET_ISLAND","SKEW",
            "UNASSIGNED_COPPER","COPPER_TO_EDGE","COPPER_TO_HOLE","COURTYARD_OVERLAP",
            "RULE_AREA_VIOLATION","HEIGHT_CONSTRAINT","THERMAL_SPACING","TEST_ACCESS",
            "LAYER_POLICY_VIOLATION","VIA_POLICY_VIOLATION","ZONE_VALIDITY",
            "RIGHT_ANGLE_BEND"
        };
        DrcViolation dv;
        dv.ruleCode   = v.rule;
        dv.rule       = (v.rule >= 1 && v.rule <= 25) ? ruleNames[v.rule] : QString("RULE_%1").arg(v.rule);
        dv.description = QString::fromUtf8(v.message);
        dv.x_mm       = v.x / NM;
        dv.y_mm       = v.y / NM;
        const QString severity = model->severityForRule(dv.rule);
        if (severity == "disabled") continue;
        dv.severity = severity == "info" ? DrcViolation::Info
                    : severity == "warning" ? DrcViolation::Warning
                    : DrcViolation::Error;
        result.append(dv);
    }
    return result;
}

QJsonObject CoreBridge::buildVerificationReport(ProjectModel* model) {
    QMutexLocker<QRecursiveMutex> lock(&m_boardMutex);
    const QVector<DrcViolation> violations = runDrc(model); // rebuilds authoritative native board
    auto finding = [](const QString& code, const QString& severity, const QString& message,
                      const QJsonObject& metrics = QJsonObject{}) {
        return QJsonObject{{"code", code}, {"severity", severity}, {"message", message},
                           {"metrics", metrics}};
    };
    auto category = [](const QString& status, bool required, const QJsonArray& findings,
                       const QJsonObject& metrics = QJsonObject{}) {
        return QJsonObject{{"status", status}, {"required", required},
                           {"findings", findings}, {"metrics", metrics}};
    };

    QJsonArray drcFindings;
    int drcErrors = 0, drcWarnings = 0;
    for (const auto& v : violations) {
        const QString severity = v.severity == DrcViolation::Error ? "error"
                               : v.severity == DrcViolation::Warning ? "warning" : "info";
        if (v.severity == DrcViolation::Error) ++drcErrors;
        if (v.severity == DrcViolation::Warning) ++drcWarnings;
        drcFindings.append(finding(v.rule, severity, v.description,
            QJsonObject{{"x_mm", v.x_mm}, {"y_mm", v.y_mm}, {"rule_code", v.ruleCode}}));
    }
    const QJsonObject drcCategory = category(drcErrors ? "fail" : "pass", true, drcFindings,
        QJsonObject{{"errors", drcErrors}, {"warnings", drcWarnings},
                    {"engine", "designcore-native"}});

    QJsonArray connectivityFindings;
    int checkedNets = 0, disconnectedNets = 0;
    const QSet<QString> engineRules{"PROFILE_INVALID", "DRC_REBUILD_FAILED", "DRC_API_V2_REQUIRED",
        "DRC_ENGINE_FAILED", "DRC_RESULT_INCOMPLETE"};
    const bool nativeReady = std::none_of(violations.begin(), violations.end(),
        [&](const DrcViolation& violation) { return engineRules.contains(violation.rule); });
    if (!nativeReady) {
        ++disconnectedNets;
        connectivityFindings.append(finding("CONNECTIVITY_ENGINE_UNAVAILABLE", "error",
            "Connectivity cannot pass because the authoritative native board/DRC run failed"));
    }
    for (const auto& net : model->nets) {
        if (!nativeReady) break;
        int terminals = 0;
        for (const auto& fp : model->footprints)
            for (const auto& pad : fp.pads) if (pad.netId == net.id) ++terminals;
        if (terminals < 2) continue;
        ++checkedNets;
        const int islands = fn_netIslands(m_board, net.id);
        if (islands != 1) {
            ++disconnectedNets;
            connectivityFindings.append(finding("NET_ISLAND", "error",
                QString("%1 has %2 disconnected copper islands").arg(net.name).arg(islands),
                QJsonObject{{"net_id", net.id}, {"terminals", terminals}, {"islands", islands}}));
        }
    }
    const QString connectivityStatus = disconnectedNets ? "fail" : "pass";
    const QJsonObject connectivityCategory = category(connectivityStatus, true, connectivityFindings,
        QJsonObject{{"checked_nets", checkedNets}, {"disconnected_nets", disconnectedNets}});

    QJsonArray siFindings;
    int siTargets = 0, siFailures = 0;
    for (const auto& nc : model->netClasses) {
        if (nc.z0Ohm <= 0 && nc.zdiffOhm <= 0) continue;
        ++siTargets;
        QVector<const ProjTrace*> routed;
        QSet<int> routedLayers;
        int viaCount = 0;
        double minActualWidth = std::numeric_limits<double>::infinity();
        double maxActualWidth = 0;
        double totalRoutedLength = 0;
        double offNominalLength = 0;
        QSet<int> classNetIds;
        for (const auto& net : model->nets)
            if (net.classId == nc.id) classNetIds.insert(net.id);
        for (const auto& trace : model->traces) {
            if (trace.isPour || !classNetIds.contains(trace.netId)) continue;
            routed.append(&trace);
            routedLayers.insert(trace.layer);
            minActualWidth = std::min(minActualWidth, trace.width_mm);
            maxActualWidth = std::max(maxActualWidth, trace.width_mm);
            const double length = std::hypot(trace.bx_mm - trace.ax_mm,
                                             trace.by_mm - trace.ay_mm);
            totalRoutedLength += length;
            if (std::abs(trace.width_mm - nc.traceWidthMm)
                    > std::max(0.001, nc.traceWidthMm * 0.01))
                offNominalLength += length;
        }
        for (const auto& via : model->vias)
            if (classNetIds.contains(via.netId)) ++viaCount;

        int rightAngleCorners = 0;
        QMap<QString, QVector<QPointF>> endpointDirections;
        auto endpointKey = [](int netId, int layer, double x, double y) {
            return QStringLiteral("%1/%2/%3/%4").arg(netId).arg(layer)
                .arg(x, 0, 'f', 6).arg(y, 0, 'f', 6);
        };
        QSet<QString> terminalEndpointKeys;
        for (const auto& fp : model->footprints) {
            for (const auto& pad : fp.pads) {
                if (!classNetIds.contains(pad.netId)) continue;
                const QPointF world = footprintLocalToBoard(fp, {pad.x_mm, pad.y_mm});
                if (pad.throughHole) {
                    for (int layer = 0; layer < model->copperLayers; ++layer)
                        terminalEndpointKeys.insert(
                            endpointKey(pad.netId, layer, world.x(), world.y()));
                } else {
                    const int layer = fp.side == 0 ? 0 : model->copperLayers - 1;
                    terminalEndpointKeys.insert(
                        endpointKey(pad.netId, layer, world.x(), world.y()));
                }
            }
        }
        for (const ProjTrace* trace : routed) {
            const QPointF delta(trace->bx_mm - trace->ax_mm, trace->by_mm - trace->ay_mm);
            const double length = std::hypot(delta.x(), delta.y());
            if (length <= 1e-9) continue;
            const QPointF direction(delta.x() / length, delta.y() / length);
            endpointDirections[endpointKey(trace->netId, trace->layer,
                                            trace->ax_mm, trace->ay_mm)].append(direction);
            endpointDirections[endpointKey(trace->netId, trace->layer,
                                            trace->bx_mm, trace->by_mm)].append(-direction);
        }
        for (auto it = endpointDirections.cbegin(); it != endpointDirections.cend(); ++it) {
            if (terminalEndpointKeys.contains(it.key())) continue;
            const auto& directions = it.value();
            for (qsizetype a = 0; a < directions.size(); ++a) {
                for (qsizetype b = a + 1; b < directions.size(); ++b) {
                    const double dot = directions[a].x() * directions[b].x()
                                     + directions[a].y() * directions[b].y();
                    if (std::abs(dot) < 1e-6) ++rightAngleCorners;
                }
            }
        }
        if (routed.isEmpty()) {
            ++siFailures;
            siFindings.append(finding("SI_ROUTE_MISSING", "error",
                nc.name + " has an impedance target but no routed copper"));
        } else {
            const double widthTolerance = std::max(0.001, nc.traceWidthMm * 0.01);
            const double allowedNeckdownLength = std::max(1.0, totalRoutedLength * 0.02);
            if ((std::abs(minActualWidth - nc.traceWidthMm) > widthTolerance
                 || std::abs(maxActualWidth - nc.traceWidthMm) > widthTolerance)
                && offNominalLength > allowedNeckdownLength) {
                ++siFailures;
                siFindings.append(finding("SI_ACTUAL_WIDTH_DISCONTINUITY", "error",
                    QString("%1 has %2 mm of off-nominal routed width (%3 to %4 mm); "
                            "the impedance model assumes %5 mm")
                        .arg(nc.name).arg(offNominalLength, 0, 'f', 3)
                        .arg(minActualWidth, 0, 'f', 3)
                        .arg(maxActualWidth, 0, 'f', 3).arg(nc.traceWidthMm, 0, 'f', 3),
                    QJsonObject{{"minimum_actual_width_mm", minActualWidth},
                                {"maximum_actual_width_mm", maxActualWidth},
                                {"model_width_mm", nc.traceWidthMm},
                                {"off_nominal_length_mm", offNominalLength},
                                {"allowed_neckdown_length_mm", allowedNeckdownLength}}));
            }
            if (nc.signalFrequencyHz >= 100e6 && rightAngleCorners > 0) {
                ++siFailures;
                siFindings.append(finding("SI_RIGHT_ANGLE_CORNERS", "error",
                    QString("%1 contains %2 right-angle routed corner(s); use arcs or 45-degree bends")
                        .arg(nc.name).arg(rightAngleCorners),
                    QJsonObject{{"right_angle_corners", rightAngleCorners}}));
            }
        }
        double z0 = 0, eEff = 0, delay = 0;
        const bool solved = fn_physMicrostrip(nc.traceWidthMm, model->dielectricHMm,
                                               model->copperTMm, model->erDielectric,
                                               &z0, &eEff, &delay) == 0;
        if (!solved) {
            ++siFailures;
            siFindings.append(finding("SI_MODEL_INPUT", "error",
                nc.name + " impedance estimate could not be evaluated"));
            continue;
        }
        auto assess = [&](const QString& code, double predicted, double target) {
            if (target <= 0) return;
            const double errorPct = std::abs(predicted - target) / target * 100.0;
            const bool pass = errorPct <= nc.impedanceTolerancePct;
            if (!pass) ++siFailures;
            siFindings.append(finding(code, pass ? "info" : "error",
                QString("%1 predicted %2 ohm vs target %3 ohm (%4% error)")
                    .arg(nc.name).arg(predicted, 0, 'f', 2).arg(target, 0, 'f', 2)
                    .arg(errorPct, 0, 'f', 1),
                QJsonObject{{"class_id", nc.id}, {"predicted_ohm", predicted},
                            {"target_ohm", target}, {"tolerance_pct", nc.impedanceTolerancePct},
                            {"method", "closed-form-microstrip-screening"}}));
        };
        assess("SINGLE_ENDED_IMPEDANCE", z0, nc.z0Ohm);
        if (nc.zdiffOhm > 0 && nc.diffPairGapMm > 0)
            assess("DIFFERENTIAL_IMPEDANCE",
                fn_physDiffMicrostrip(nc.traceWidthMm, nc.diffPairGapMm,
                    model->dielectricHMm, model->copperTMm, model->erDielectric), nc.zdiffOhm);
        else if (nc.zdiffOhm > 0) {
            ++siFailures;
            siFindings.append(finding("DIFF_PAIR_GAP_MISSING", "error",
                nc.name + " has a differential impedance target but no pair gap"));
        }
    }
    const bool requireSi = model->verificationRequirements.requireSignalIntegrity;
    const QString siStatus = siFailures ? "fail" : siTargets ? "pass"
                           : requireSi ? "incomplete" : "not_applicable";
    if (requireSi && !siTargets)
        siFindings.append(finding("SI_TARGETS_MISSING", "error",
            "Signal-integrity sign-off is required but no impedance targets are defined"));
    const QJsonObject siCategory = category(siStatus, requireSi, siFindings,
        QJsonObject{{"target_classes", siTargets}, {"failed_targets", siFailures},
                    {"method", "actual-route-screening-plus-closed-form-impedance"}});

    QJsonArray piFindings;
    int piNets = 0, piFailures = 0;
    for (const auto& net : model->nets) {
        if (net.requiredCurrentA <= 0) continue;
        ++piNets;
        double capacity = 1e300;
        double limitingRequired = net.requiredCurrentA;
        double minMargin = 1e300;
        int traceCount = 0;
        for (const auto& trace : model->traces) {
            if (trace.netId != net.id || trace.isPour) continue;
            ++traceCount;
            double thickness = model->copperTMm;
            for (const auto& policy : model->layerPolicies)
                if (policy.layer == trace.layer) thickness = policy.copperThicknessMm;
            const bool external = trace.layer == 0 || trace.layer == model->copperLayers - 1;
            const double traceCapacity =
                fn_physIpcCurrent(trace.width_mm, thickness, 10.0, external ? 1 : 0);
            const double traceRequired = trace.requiredCurrentA > 0
                ? trace.requiredCurrentA : net.requiredCurrentA;
            const double margin = traceRequired > 0 ? traceCapacity / traceRequired : 1e300;
            if (margin < minMargin) {
                minMargin = margin;
                capacity = traceCapacity;
                limitingRequired = traceRequired;
            }
        }
        const bool pass = traceCount > 0 && std::isfinite(capacity)
                       && std::isfinite(minMargin) && minMargin >= 1.0;
        if (!pass) ++piFailures;
        piFindings.append(finding("DC_CURRENT_CAPACITY", pass ? "info" : "error",
            traceCount == 0 ? net.name + " has a current requirement but no routed traces"
                            : QString("%1 limiting branch capacity %2 A vs allocated %3 A at 10 C rise")
                                .arg(net.name).arg(capacity, 0, 'f', 3)
                                .arg(limitingRequired, 0, 'f', 3),
            QJsonObject{{"net_id", net.id}, {"required_current_a", net.requiredCurrentA},
                        {"limiting_branch_required_current_a", limitingRequired},
                        {"capacity_a", traceCount ? capacity : 0},
                        {"minimum_margin_ratio", traceCount ? minMargin : 0},
                        {"trace_count", traceCount},
                        {"method", "IPC-2221-screening"}}));
    }
    const bool requirePi = model->verificationRequirements.requirePowerIntegrity;
    const QString piStatus = piFailures ? "fail" : piNets ? "pass"
                           : requirePi ? "incomplete" : "not_applicable";
    if (requirePi && !piNets)
        piFindings.append(finding("PI_REQUIREMENTS_MISSING", "error",
            "Power-integrity sign-off is required but no net current requirements are defined"));
    const QJsonObject piCategory = category(piStatus, requirePi, piFindings,
        QJsonObject{{"specified_power_nets", piNets}, {"failed_power_nets", piFailures},
                    {"temperature_rise_c", 10.0}});

    QJsonArray thermalFindings;
    int thermalParts = 0, thermalMissing = 0, thermalDrcFailures = 0;
    for (const auto& fp : model->footprints) {
        if (fp.thermalPowerW <= 0) continue;
        ++thermalParts;
        if (fp.thermalClearanceMm <= 0) {
            ++thermalMissing;
            thermalFindings.append(finding("THERMAL_CONSTRAINT_MISSING", "error",
                fp.ref + " has power dissipation but no thermal clearance requirement"));
        }
    }
    for (const auto& v : violations)
        if (v.rule == "THERMAL_SPACING" && v.severity == DrcViolation::Error) ++thermalDrcFailures;
    const bool requireThermal = model->verificationRequirements.requireThermal;
    const QString thermalStatus = thermalMissing || thermalDrcFailures ? "fail"
        : thermalParts ? "pass" : requireThermal ? "incomplete" : "not_applicable";
    if (requireThermal && !thermalParts)
        thermalFindings.append(finding("THERMAL_INPUTS_MISSING", "error",
            "Thermal sign-off is required but component power dissipation is not specified"));
    const QJsonObject thermalCategory = category(thermalStatus, requireThermal, thermalFindings,
        QJsonObject{{"powered_components", thermalParts}, {"missing_constraints", thermalMissing},
                    {"spacing_failures", thermalDrcFailures}});

    QJsonArray mechanicalFindings;
    int incompleteBodies = 0, mechanicalDrcFailures = 0;
    for (const auto& fp : model->footprints) {
        if (fp.bodyW_mm <= 0 || fp.bodyH_mm <= 0 || fp.h3d_mm <= 0) {
            ++incompleteBodies;
            mechanicalFindings.append(finding("COMPONENT_ENVELOPE_MISSING", "error",
                fp.ref + " lacks a complete body width/height/Z envelope"));
        }
    }
    const QSet<QString> mechanicalRules{"BOARD_EDGE", "COURTYARD_OVERLAP", "HEIGHT_CONSTRAINT",
        "TEST_ACCESS", "RULE_AREA_VIOLATION"};
    for (const auto& v : violations)
        if (mechanicalRules.contains(v.rule) && v.severity == DrcViolation::Error)
            ++mechanicalDrcFailures;
    bool enclosureEvidenceValid = false;
    const auto& requirements = model->verificationRequirements;
    if (!requirements.enclosureEvidencePath.isEmpty()) {
        QString evidencePath = requirements.enclosureEvidencePath;
        if (QFileInfo(evidencePath).isRelative()) {
            QDir documentDirectory(QFileInfo(model->filePath()).absolutePath());
            // Unified workspaces keep the electronics document in
            // <workspace>/electronics and record evidence paths from the
            // workspace root. Legacy .dsproj files remain document-relative.
            if (documentDirectory.dirName() == QStringLiteral("electronics")
                && !evidencePath.startsWith(QStringLiteral("../"))) {
                documentDirectory.cdUp();
            }
            evidencePath = documentDirectory.filePath(evidencePath);
        }
        QFile evidence(evidencePath);
        if (evidence.open(QIODevice::ReadOnly)) {
            const QString actual = QString::fromLatin1(
                QCryptographicHash::hash(evidence.readAll(), QCryptographicHash::Sha256).toHex());
            enclosureEvidenceValid = actual.compare(requirements.enclosureEvidenceSha256,
                                                      Qt::CaseInsensitive) == 0;
        }
        if (!enclosureEvidenceValid)
            mechanicalFindings.append(finding("ENCLOSURE_EVIDENCE_INVALID", "error",
                "Enclosure evidence is missing or its SHA-256 digest does not match"));
    }
    const bool enclosureRequired = requirements.requireEnclosureEvidence;
    const bool mechanicalFail = mechanicalDrcFailures > 0
        || (enclosureRequired && !enclosureEvidenceValid);
    const QString mechanicalStatus = mechanicalFail ? "fail"
        : incompleteBodies ? "incomplete" : "pass";
    const QJsonObject mechanicalCategory = category(mechanicalStatus, true, mechanicalFindings,
        QJsonObject{{"components_missing_envelope", incompleteBodies},
                    {"mechanical_drc_failures", mechanicalDrcFailures},
                    {"enclosure_evidence_required", enclosureRequired},
                    {"enclosure_evidence_valid", enclosureEvidenceValid}});

    const QJsonObject categories{{"drc", drcCategory}, {"connectivity", connectivityCategory},
        {"signal_integrity", siCategory}, {"power_integrity", piCategory},
        {"thermal", thermalCategory}, {"mechanical", mechanicalCategory}};
    QString overall = "pass";
    for (auto it = categories.begin(); it != categories.end(); ++it) {
        const QJsonObject c = it.value().toObject();
        if (c.value("status").toString() == "fail") { overall = "fail"; break; }
        if (c.value("required").toBool()
            && c.value("status").toString() == "incomplete") overall = "incomplete";
    }
    return QJsonObject{
        {"schema", "design-studio.verification/1"},
        {"generated_utc", QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
        {"project", QJsonObject{{"document_id", model->documentId()},
            {"revision", model->revision()}, {"file_sha256", model->fileSha256()}}},
        {"constraint_profile", QJsonObject{{"id", model->pcbRules.id},
            {"source", model->pcbRules.source}, {"source_revision", model->pcbRules.sourceRevision},
            {"fabricator", model->pcbRules.fabricator}, {"assembler", model->pcbRules.assembler},
            {"ipc_performance_class", model->pcbRules.ipcPerformanceClass},
            {"producibility_level", model->pcbRules.producibilityLevel}}},
        {"overall_status", overall}, {"categories", categories}
    };
}

// ── Copper pour ───────────────────────────────────────────────────────────────
bool CoreBridge::pourCopper(ProjectModel* model, int netId, int layer) {
    QMutexLocker<QRecursiveMutex> lock(&m_boardMutex);
    if (!rebuildBoard(model)) return false;

    int64_t lineWidth = (int64_t)(0.25 * NM);
    int64_t clearance = (int64_t)(0.2  * NM);

    model->traces.erase(std::remove_if(model->traces.begin(), model->traces.end(),
        [netId, layer](const ProjTrace& t) {
            return t.isPour && t.netId == netId && t.layer == layer;
        }), model->traces.end());
    auto appendStrokes = [&](int strokes) {
        if (strokes <= 0) return false;
        for (int i = 0; i < strokes; ++i) {
            DcPourStroke stroke{};
            if (fn_pourGet(m_board, i, &stroke) != 0) return false;
            ProjTrace t;
            t.ax_mm = stroke.ax / NM; t.ay_mm = stroke.ay / NM;
            t.bx_mm = stroke.bx / NM; t.by_mm = stroke.by / NM;
            t.width_mm = stroke.width / NM; t.netId = netId; t.layer = layer;
            t.isPour = true;
            model->traces.append(t);
        }
        return true;
    };
    bool hadZone = false, generated = false;
    for (const auto& zone : model->copperZones) {
        if (zone.netId != netId || zone.layer != layer) continue;
        hadZone = true;
        if (!fn_pourZoneRun || !appendStrokes(fn_pourZoneRun(m_board, zone.id, lineWidth)))
            return false;
        generated = true;
    }
    if (!hadZone) {
        const int strokes = fn_pourRun(m_board, netId, layer, lineWidth, clearance,
            0, 0, (int64_t)(model->boardWidthMm * NM),
            (int64_t)(model->boardHeightMm * NM));
        if (!appendStrokes(strokes)) return false;
        generated = true;
    }
    if (!generated) return false;
    model->setModified(true);
    return true;
}
