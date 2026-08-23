#include "designcore/c_api.h"
#include "designcore/drc.h"
#include "designcore/interactive_router.h"
#include "designcore/linalg.h"
#include "designcore/mechanical.h"
#include "designcore/pcb_model.h"
#include "designcore/physics.h"
#include "designcore/pour.h"
#include "designcore/router.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <vector>

using namespace dc;

namespace {

// The handle owns the board plus the last route / DRC / pour results, so the
// API never needs caller-sized buffers that silently truncate.
struct Session {
    Board board;
    RouteResult route;
    std::unique_ptr<InteractiveRouterSession> interactive;
    std::vector<DrcViolation> drc;
    PourResult pour;
};

struct MechanicalSession {
    dc::mechanical::MechanicalEngine engine;
    dc::mechanical::LinearSystem system;
    dc::mechanical::SolveResult result;
    bool configured = false;
};

Session* asSession(DcBoardHandle h) { return static_cast<Session*>(h); }

void copyMessage(char* dst, std::size_t cap, const std::string& src) {
    const std::size_t len = std::min(src.size(), cap - 1);
    std::memcpy(dst, src.data(), len);
    dst[len] = '\0';
}

} // namespace

DC_API DcBoardHandle dc_board_create() { return new Session(); }
DC_API void dc_board_destroy(DcBoardHandle h) { delete asSession(h); }

DC_API std::int32_t dc_board_clear(DcBoardHandle h) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    s->board.clear();
    s->interactive.reset();
    s->route = {};
    s->drc.clear();
    s->pour = {};
    return DC_OK;
}

DC_API std::int32_t dc_board_set_outline(DcBoardHandle h, std::int64_t x0, std::int64_t y0, std::int64_t x1, std::int64_t y1) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (x1 <= x0 || y1 <= y0) return DC_ERR_INVALID_ARG;
    s->board.outline = {{x0, y0}, {x1, y1}};
    s->board.outlinePolygon.clear();
    s->board.cutouts.clear();
    return DC_OK;
}

DC_API std::int32_t dc_board_set_outline_polygon(DcBoardHandle h, const DcPoint* points,
                                                  std::int32_t pointCount) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!points || pointCount < 3) return DC_ERR_INVALID_ARG;
    std::vector<Vec2> polygon;
    polygon.reserve(pointCount);
    for (int i = 0; i < pointCount; ++i) polygon.push_back({points[i].x, points[i].y});
    return s->board.setOutlinePolygon(std::move(polygon)) ? DC_OK : DC_ERR_INVALID_ARG;
}

DC_API std::int32_t dc_board_cutout_add(DcBoardHandle h, const DcPoint* points,
                                         std::int32_t pointCount) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!points || pointCount < 3) return DC_ERR_INVALID_ARG;
    std::vector<Vec2> polygon;
    polygon.reserve(pointCount);
    for (int i = 0; i < pointCount; ++i) polygon.push_back({points[i].x, points[i].y});
    return s->board.addCutout(std::move(polygon)) ? DC_OK : DC_ERR_INVALID_ARG;
}

DC_API std::int32_t dc_board_set_copper_layers(DcBoardHandle h, std::int32_t count) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (count < 2 || count > kMaxCopperLayers) return DC_ERR_INVALID_ARG;
    s->board.copperLayerCount = count;
    return DC_OK;
}

DC_API std::int32_t dc_net_set(DcBoardHandle h, std::int32_t netId, const char* name, std::int32_t classId) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (netId < 0) return DC_ERR_INVALID_ARG;
    return s->board.setNet(netId, name ? name : "", classId) ? DC_OK : DC_ERR_INVALID_ARG;
}

DC_API std::int32_t dc_net_remove(DcBoardHandle h, std::int32_t netId) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    return s->board.removeNet(netId) ? DC_OK : DC_ERR_NOT_FOUND;
}

DC_API std::int32_t dc_class_set(DcBoardHandle h, std::int32_t classId, const char* name,
                                 std::int64_t clearance, std::int64_t traceWidth,
                                 std::int64_t viaDiameter, std::int64_t viaDrill,
                                 std::int64_t diffPairGap, std::int64_t maxSkew,
                                 std::int32_t allowMicrovia) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (classId < 0 || clearance < 0 || traceWidth <= 0) return DC_ERR_INVALID_ARG;
    NetClass nc;
    nc.id = classId;
    nc.name = name ? name : "";
    nc.clearance = clearance;
    nc.traceWidth = traceWidth;
    nc.viaDiameter = viaDiameter;
    nc.viaDrill = viaDrill;
    nc.diffPairGap = diffPairGap;
    nc.maxSkew = maxSkew;
    nc.allowMicrovia = allowMicrovia != 0;
    s->board.setNetClass(std::move(nc));
    return DC_OK;
}

DC_API std::int32_t dc_class_routing_policy_set(DcBoardHandle h, std::int32_t classId,
                                                std::uint64_t allowedLayers,
                                                std::uint32_t allowedViaTypes,
                                                std::int32_t maxViaCount) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    const NetClass* existing = s->board.findNetClass(classId);
    if (!existing) return DC_ERR_NOT_FOUND;
    if (allowedLayers == 0 || allowedViaTypes == 0 || (allowedViaTypes & ~0x0fu)
        || maxViaCount < 0) return DC_ERR_INVALID_ARG;
    NetClass updated = *existing;
    updated.allowedLayers = allowedLayers;
    updated.allowedViaTypes = allowedViaTypes;
    updated.maxViaCount = maxViaCount;
    s->board.setNetClass(std::move(updated));
    return DC_OK;
}

DC_API std::int32_t dc_layer_policy_set(DcBoardHandle h, std::int32_t layer,
                                        const char* name, std::int32_t role,
                                        std::int32_t preferredDirection,
                                        std::int32_t allowRouting,
                                        std::int64_t copperThickness,
                                        const char* source, const char* sourceRevision) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (role < 0 || role > 2 || preferredDirection < 0 || preferredDirection > 2)
        return DC_ERR_INVALID_ARG;
    LayerPolicy policy;
    policy.layer = layer; policy.name = name ? name : "";
    policy.role = static_cast<LayerRole>(role);
    policy.preferredDirection = static_cast<PreferredDirection>(preferredDirection);
    policy.allowRouting = allowRouting != 0; policy.copperThickness = copperThickness;
    policy.source = source ? source : ""; policy.sourceRevision = sourceRevision ? sourceRevision : "";
    return s->board.setLayerPolicy(std::move(policy)) ? DC_OK : DC_ERR_INVALID_ARG;
}

DC_API std::int32_t dc_rule_area_set(DcBoardHandle h, std::int32_t areaId, const char* name,
                                     const DcPoint* points, std::int32_t pointCount,
                                     std::int32_t layer, std::int64_t clearance,
                                     std::int64_t minTraceWidth,
                                     std::int32_t forbidRouting, std::int32_t forbidVias,
                                     std::int32_t forbidPlacement,
                                     const char* source, const char* sourceRevision) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!points || pointCount < 3) return DC_ERR_INVALID_ARG;
    RuleArea area;
    area.id = areaId; area.name = name ? name : ""; area.layer = layer;
    area.clearance = clearance; area.minTraceWidth = minTraceWidth;
    area.forbidRouting = forbidRouting != 0; area.forbidVias = forbidVias != 0;
    area.forbidPlacement = forbidPlacement != 0;
    area.source = source ? source : "";
    area.sourceRevision = sourceRevision ? sourceRevision : "";
    for (int i = 0; i < pointCount; ++i) area.polygon.push_back({points[i].x, points[i].y});
    return s->board.setRuleArea(std::move(area)) ? DC_OK : DC_ERR_INVALID_ARG;
}

DC_API std::int32_t dc_rule_area_height_set(DcBoardHandle h, std::int32_t areaId,
                                            std::int64_t maxHeight) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (maxHeight < 0) return DC_ERR_INVALID_ARG;
    for (const auto& existing : s->board.ruleAreas()) {
        if (existing.id != areaId) continue;
        RuleArea updated = existing;
        updated.maxHeight = maxHeight;
        return s->board.setRuleArea(std::move(updated)) ? DC_OK : DC_ERR_INVALID_ARG;
    }
    return DC_ERR_NOT_FOUND;
}

DC_API std::int32_t dc_class_pair_set(DcBoardHandle h, std::int32_t classA,
                                      std::int32_t classB, std::int64_t clearance,
                                      const char* source, const char* sourceRevision) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    ClassPairRule rule;
    rule.classA = classA; rule.classB = classB; rule.clearance = clearance;
    rule.source = source ? source : "";
    rule.sourceRevision = sourceRevision ? sourceRevision : "";
    return s->board.setClassPairRule(std::move(rule)) ? DC_OK : DC_ERR_INVALID_ARG;
}

DC_API std::int32_t dc_copper_zone_set(DcBoardHandle h, std::int32_t zoneId,
                                       const char* name, const DcPoint* points,
                                       std::int32_t pointCount, std::int32_t netId,
                                       std::int32_t layer, std::int64_t clearance,
                                       double minIslandAreaMm2,
                                       std::int32_t requireConnection,
                                       const char* source, const char* sourceRevision) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!points || pointCount < 3) return DC_ERR_INVALID_ARG;
    CopperZone zone;
    zone.id = zoneId; zone.name = name ? name : ""; zone.netId = netId; zone.layer = layer;
    zone.clearance = clearance; zone.minIslandAreaMm2 = minIslandAreaMm2;
    zone.requireConnection = requireConnection != 0;
    zone.source = source ? source : ""; zone.sourceRevision = sourceRevision ? sourceRevision : "";
    for (int i = 0; i < pointCount; ++i) zone.polygon.push_back({points[i].x, points[i].y});
    return s->board.setCopperZone(std::move(zone)) ? DC_OK : DC_ERR_INVALID_ARG;
}

DC_API std::uint64_t dc_footprint_add(DcBoardHandle h, const char* refDes, const char* libName,
                                      std::int64_t x, std::int64_t y, double rotationDeg, std::int32_t side,
                                      const DcPadDef* pads, std::int32_t padCount) {
    Session* s = asSession(h);
    if (!s || padCount < 0 || (padCount > 0 && !pads) || side < 0 || side > 1
        || !std::isfinite(rotationDeg)) return 0;
    Footprint fp;
    fp.refDes = refDes ? refDes : "";
    fp.libName = libName ? libName : "";
    fp.pos = {x, y};
    fp.rotationDeg = rotationDeg;
    fp.side = side;
    for (std::int32_t i = 0; i < padCount; ++i) {
        if (pads[i].w <= 0 || pads[i].h <= 0 || pads[i].drill < 0
            || (pads[i].throughHole && pads[i].drill <= 0)) return 0;
        Pad p;
        p.pos = {pads[i].x, pads[i].y};
        p.size = {pads[i].w, pads[i].h};
        p.netId = pads[i].netId;
        p.throughHole = pads[i].throughHole != 0;
        p.drill = pads[i].drill;
        p.name.assign(pads[i].name, strnlen(pads[i].name, sizeof pads[i].name));
        fp.pads.push_back(std::move(p));
    }
    return s->board.addFootprint(std::move(fp));
}

DC_API std::uint64_t dc_footprint_add_v2(DcBoardHandle h, const char* refDes, const char* libName,
                                         std::int64_t x, std::int64_t y, double rotationDeg,
                                         std::int32_t side, const DcPadDefV2* pads,
                                         std::int32_t padCount) {
    Session* s = asSession(h);
    if (!s || padCount < 0 || (padCount > 0 && !pads) || side < 0 || side > 1
        || !std::isfinite(rotationDeg)) return 0;
    Footprint fp;
    fp.refDes = refDes ? refDes : "";
    fp.libName = libName ? libName : "";
    fp.pos = {x, y}; fp.rotationDeg = rotationDeg; fp.side = side;
    for (std::int32_t i = 0; i < padCount; ++i) {
        if (pads[i].w <= 0 || pads[i].h <= 0 || pads[i].drill < 0
            || pads[i].cornerRadius < 0 || (pads[i].throughHole && pads[i].drill <= 0)) return 0;
        if (pads[i].shape < std::int32_t(PadShape::Rect)
            || pads[i].shape > std::int32_t(PadShape::Circle)) return 0;
        Pad p;
        p.pos = {pads[i].x, pads[i].y}; p.size = {pads[i].w, pads[i].h};
        p.netId = pads[i].netId; p.throughHole = pads[i].throughHole != 0;
        p.drill = pads[i].drill; p.shape = static_cast<PadShape>(pads[i].shape);
        p.cornerRadius = pads[i].cornerRadius;
        p.name.assign(pads[i].name, strnlen(pads[i].name, sizeof pads[i].name));
        fp.pads.push_back(std::move(p));
    }
    return s->board.addFootprint(std::move(fp));
}

DC_API std::int32_t dc_footprint_region_add(DcBoardHandle h, std::uint64_t footprintId,
                                            std::int32_t netId,
                                            const DcPoint* outer, std::int32_t outerCount,
                                            const DcPoint* hole, std::int32_t holeCount) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!outer || outerCount < 3 || holeCount < 0 || (holeCount > 0 && !hole))
        return DC_ERR_INVALID_ARG;
    Footprint* fp = s->board.findFootprint(footprintId);
    if (!fp) return DC_ERR_NOT_FOUND;
    CopperRegion region;
    region.netId = netId;
    for (int i = 0; i < outerCount; ++i) region.points.push_back({outer[i].x, outer[i].y});
    for (int i = 0; i < holeCount; ++i) region.hole.push_back({hole[i].x, hole[i].y});
    fp->regions.push_back(std::move(region));
    return DC_OK;
}

DC_API std::int32_t dc_footprint_geometry_set(DcBoardHandle h, std::uint64_t footprintId,
                                              std::int64_t bodyCx, std::int64_t bodyCy,
                                              std::int64_t bodyW, std::int64_t bodyH,
                                              std::int64_t bodyHeight,
                                              const DcPoint* courtyard,
                                              std::int32_t courtyardCount) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (bodyW < 0 || bodyH < 0 || bodyHeight < 0 || courtyardCount < 0
        || (courtyardCount > 0 && !courtyard)) return DC_ERR_INVALID_ARG;
    Footprint* fp = s->board.findFootprint(footprintId);
    if (!fp) return DC_ERR_NOT_FOUND;
    fp->bodyCenter = {bodyCx, bodyCy}; fp->bodySize = {bodyW, bodyH};
    fp->bodyHeight = bodyHeight; fp->courtyard.clear();
    for (int i = 0; i < courtyardCount; ++i)
        fp->courtyard.push_back({courtyard[i].x, courtyard[i].y});
    return DC_OK;
}

DC_API std::int32_t dc_footprint_placement_set(DcBoardHandle h, std::uint64_t footprintId,
                                               std::int32_t locked,
                                               const char* functionalGroup,
                                               const char* edgeAnchor,
                                               double thermalPowerW,
                                               std::int64_t thermalClearance,
                                               std::int32_t testAccessRequired,
                                               std::int64_t testAccessHalo) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (thermalPowerW < 0 || thermalClearance < 0 || testAccessHalo < 0)
        return DC_ERR_INVALID_ARG;
    Footprint* fp = s->board.findFootprint(footprintId);
    if (!fp) return DC_ERR_NOT_FOUND;
    fp->placementLocked = locked != 0;
    fp->functionalGroup = functionalGroup ? functionalGroup : "";
    fp->edgeAnchor = edgeAnchor ? edgeAnchor : "";
    fp->thermalPowerW = thermalPowerW;
    fp->thermalClearance = thermalClearance;
    fp->testAccessRequired = testAccessRequired != 0;
    fp->testAccessHalo = testAccessHalo;
    return DC_OK;
}

DC_API std::int32_t dc_pad_clearance_set(DcBoardHandle h, std::uint64_t footprintId,
                                         const char* padName, std::int64_t clearance) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!padName || clearance < 0) return DC_ERR_INVALID_ARG;
    Footprint* fp = s->board.findFootprint(footprintId);
    if (!fp) return DC_ERR_NOT_FOUND;
    for (auto& pad : fp->pads) {
        if (pad.name == padName) { pad.clearanceOverride = clearance; return DC_OK; }
    }
    return DC_ERR_NOT_FOUND;
}

DC_API std::int32_t dc_footprint_move(DcBoardHandle h, std::uint64_t id, std::int64_t x, std::int64_t y, double rot) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    return s->board.moveFootprint(id, {x, y}, rot) ? DC_OK : DC_ERR_NOT_FOUND;
}

DC_API std::int32_t dc_item_remove(DcBoardHandle h, std::uint64_t id) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    return s->board.removeItem(id) ? DC_OK : DC_ERR_NOT_FOUND;
}

DC_API std::uint64_t dc_trace_add(DcBoardHandle h, std::int64_t ax, std::int64_t ay,
                                  std::int64_t bx, std::int64_t by,
                                  std::int64_t width, std::int32_t layer, std::int32_t netId,
                                  std::int32_t isPour) {
    Session* s = asSession(h);
    if (!s || width <= 0) return 0;
    if (layer < 0 || layer >= s->board.copperLayerCount) return 0;
    TraceSegment t;
    t.a = {ax, ay}; t.b = {bx, by};
    t.width = width;
    t.layer = layer;
    t.netId = netId;
    t.isPour = isPour != 0;
    return s->board.addTrace(t);
}

DC_API std::int32_t dc_trace_min_width_set(DcBoardHandle h, std::uint64_t traceId,
                                           std::int64_t minWidth) {
    Session* s = asSession(h);
    if (!s || minWidth < 0) return DC_ERR_INVALID_ARG;
    TraceSegment* trace = s->board.findTrace(traceId);
    if (!trace) return DC_ERR_NOT_FOUND;
    trace->minWidthOverride = minWidth;
    return DC_OK;
}

DC_API std::uint64_t dc_via_add_v2(DcBoardHandle h, std::int64_t x, std::int64_t y,
                                   std::int64_t diameter, std::int64_t drill, std::int32_t netId,
                                   std::int32_t fromLayer, std::int32_t toLayer,
                                   std::int32_t viaType) {
    Session* s = asSession(h);
    if (!s || diameter <= 0 || drill <= 0 || drill >= diameter) return 0;
    const int n = s->board.copperLayerCount;
    if (fromLayer < 0 || toLayer < 0 || fromLayer >= n || toLayer >= n || fromLayer == toLayer) return 0;
    Via v;
    v.pos = {x, y};
    v.diameter = diameter;
    v.drill = drill;
    v.netId = netId;
    v.fromLayer = std::min(fromLayer, toLayer);
    v.toLayer = std::max(fromLayer, toLayer);
    if (viaType < std::int32_t(ViaType::Through)
        || viaType > std::int32_t(ViaType::Microvia)) return 0;
    v.type = static_cast<ViaType>(viaType);
    if (v.type == ViaType::Through && (v.fromLayer != 0 || v.toLayer != n - 1)) return 0;
    if (v.type == ViaType::Microvia && v.toLayer - v.fromLayer != 1) return 0;
    return s->board.addVia(v);
}

DC_API std::uint64_t dc_via_add(DcBoardHandle h, std::int64_t x, std::int64_t y,
                                std::int64_t diameter, std::int64_t drill, std::int32_t netId,
                                std::int32_t fromLayer, std::int32_t toLayer) {
    Session* s = asSession(h);
    if (!s) return 0;
    const int lo = std::min(fromLayer, toLayer);
    const int hi = std::max(fromLayer, toLayer);
    const ViaType inferred = (lo == 0 && hi == s->board.copperLayerCount - 1)
        ? ViaType::Through
        : (hi - lo == 1 ? ViaType::Microvia : ViaType::Buried);
    return dc_via_add_v2(h, x, y, diameter, drill, netId, fromLayer, toLayer,
                         std::int32_t(inferred));
}

// ---- routing ----

DC_API std::int32_t dc_route_run(DcBoardHandle h, const DcRouteRequest* r) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!r) return DC_ERR_INVALID_ARG;

    RouteRequest req;
    req.netId = r->netId;
    req.start = {r->sx, r->sy};
    req.goal = {r->gx, r->gy};
    req.startLayer = r->startLayer;
    req.goalLayer = r->goalLayer;
    req.traceWidth = r->traceWidth;
    req.clearance = r->clearance;
    // Legacy callers predate an explicit fabrication edge rule. Preserve ABI
    // while applying the engine's conservative production default.
    req.edgeClearance = 250'000;
    req.gridStep = r->gridStep > 0 ? r->gridStep : 100'000;
    req.allowVias = r->allowVias != 0;
    req.allowMicrovia = r->allowMicrovia != 0;
    req.viaDiameter = r->viaDiameter;
    req.viaDrill = r->viaDrill;
    req.viaCostMm = r->viaCostMm > 0 ? r->viaCostMm : 3.0;
    if (r->maxExpansions > 0) req.maxExpansions = std::size_t(r->maxExpansions);
    if (r->minDrillToDrill > 0) req.minDrillToDrill = r->minDrillToDrill;

    Router router(s->board);
    s->route = router.route(req);
    if (s->route.success) return DC_OK;
    switch (s->route.reason) {
        case RouteFail::IterationLimit: return DC_ERR_ROUTE_ITER_LIMIT;
        case RouteFail::StartBlocked:   return DC_ERR_ROUTE_START_BLOCKED;
        case RouteFail::GoalBlocked:    return DC_ERR_ROUTE_GOAL_BLOCKED;
        case RouteFail::BadRequest:     return DC_ERR_INVALID_ARG;
        default:                        return DC_ERR_ROUTE_NO_PATH;
    }
}

DC_API std::int32_t dc_route_run_v2(DcBoardHandle h, const DcRouteRequestV2* r) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!r || r->abiVersion != 2 || r->structSize < sizeof(DcRouteRequestV2))
        return DC_ERR_INVALID_ARG;

    RouteRequest req;
    req.netId = r->netId;
    req.start = {r->sx, r->sy};
    req.goal = {r->gx, r->gy};
    req.startLayer = r->startLayer;
    req.goalLayer = r->goalLayer;
    req.traceWidth = r->traceWidth;
    req.clearance = r->clearance;
    req.edgeClearance = r->minCopperToEdge > 0
        ? r->minCopperToEdge : 250'000;
    req.gridStep = r->gridStep > 0 ? r->gridStep : 100'000;
    req.allowVias = r->allowVias != 0;
    req.allowMicrovia = r->allowMicrovia != 0;
    req.viaDiameter = r->viaDiameter;
    req.viaDrill = r->viaDrill;
    req.viaCostMm = r->viaCostMm > 0 ? r->viaCostMm : 3.0;
    if (r->maxExpansions > 0) req.maxExpansions = std::size_t(r->maxExpansions);
    if (r->minDrillToDrill > 0) req.minDrillToDrill = r->minDrillToDrill;

    Router router(s->board);
    s->route = router.route(req);
    if (s->route.success) return DC_OK;
    switch (s->route.reason) {
        case RouteFail::IterationLimit: return DC_ERR_ROUTE_ITER_LIMIT;
        case RouteFail::StartBlocked:   return DC_ERR_ROUTE_START_BLOCKED;
        case RouteFail::GoalBlocked:    return DC_ERR_ROUTE_GOAL_BLOCKED;
        case RouteFail::BadRequest:     return DC_ERR_INVALID_ARG;
        default:                        return DC_ERR_ROUTE_NO_PATH;
    }
}

DC_API std::int32_t dc_route_point_count(DcBoardHandle h) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    return std::int32_t(s->route.path.size());
}

DC_API std::int32_t dc_route_get_points(DcBoardHandle h, DcRoutePoint* out, std::int32_t cap) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!out || cap < 0) return DC_ERR_INVALID_ARG;
    const std::int32_t n = std::int32_t(std::min<std::size_t>(s->route.path.size(), std::size_t(cap)));
    for (std::int32_t i = 0; i < n; ++i)
        out[i] = {s->route.path[i].p.x, s->route.path[i].p.y, s->route.path[i].layer, 0};
    return n;
}

DC_API std::int32_t dc_route_via_count(DcBoardHandle h) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    return std::int32_t(s->route.vias.size());
}

DC_API std::int32_t dc_route_get_vias(DcBoardHandle h, DcRouteVia* out, std::int32_t cap) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!out || cap < 0) return DC_ERR_INVALID_ARG;
    const std::int32_t n = std::int32_t(std::min<std::size_t>(s->route.vias.size(), std::size_t(cap)));
    for (std::int32_t i = 0; i < n; ++i)
        out[i] = {s->route.vias[i].pos.x, s->route.vias[i].pos.y,
                  s->route.vias[i].fromLayer, s->route.vias[i].toLayer};
    return n;
}

// ---- transactional interactive routing ----

namespace {
std::int32_t interactiveStatus(const InteractiveRoutePreview& preview) {
    if (preview.accepted) return DC_OK;
    switch (preview.failure) {
        case InteractiveRouteFail::Inactive: return DC_ERR_ROUTE_INACTIVE;
        case InteractiveRouteFail::BadRequest: return DC_ERR_INVALID_ARG;
        case InteractiveRouteFail::NoPath: return DC_ERR_ROUTE_NO_PATH;
        case InteractiveRouteFail::BlockedByFixedItem: return DC_ERR_ROUTE_FIXED_BLOCKER;
        case InteractiveRouteFail::ShoveDepthLimit: return DC_ERR_ROUTE_SHOVE_LIMIT;
        case InteractiveRouteFail::OnlineDrc: return DC_ERR_ROUTE_ONLINE_DRC;
        case InteractiveRouteFail::None: return DC_OK;
    }
    return DC_ERR_INVALID_ARG;
}
}

DC_API std::int32_t dc_interactive_route_begin(
    DcBoardHandle h, const DcInteractiveRouteRequest* r) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!r || r->abiVersion != 1 || r->structSize != sizeof(DcInteractiveRouteRequest)
        || r->mode < 0 || r->mode > 1) return DC_ERR_INVALID_ARG;
    InteractiveRouteRequest request;
    request.netId = r->netId;
    request.start = {r->startX, r->startY};
    request.startLayer = r->startLayer;
    request.traceWidth = r->traceWidth;
    request.clearance = r->clearance;
    request.gridStep = r->gridStep;
    request.viaDiameter = r->viaDiameter;
    request.viaDrill = r->viaDrill;
    request.minDrillToDrill = r->minDrillToDrill;
    request.allowVias = r->allowVias != 0;
    request.allowMicrovia = r->allowMicrovia != 0;
    request.mode = static_cast<InteractiveRouteMode>(r->mode);
    request.maxShoveDepth = std::size_t(r->maxShoveDepth);
    request.maxExpansions = std::size_t(r->maxExpansions);
    s->interactive = std::make_unique<InteractiveRouterSession>(s->board);
    return interactiveStatus(s->interactive->begin(request));
}

DC_API std::int32_t dc_interactive_route_update(DcBoardHandle h,
                                                 std::int64_t x, std::int64_t y) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    return interactiveStatus(s->interactive->updateCursor({x, y}));
}

DC_API std::int32_t dc_interactive_route_place_via(DcBoardHandle h,
                                                    std::int32_t targetLayer) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    return interactiveStatus(s->interactive->placeVia(targetLayer));
}

DC_API std::int32_t dc_interactive_route_commit(DcBoardHandle h) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive || !s->interactive->commit()) return DC_ERR_ROUTE_INACTIVE;
    return DC_OK;
}

DC_API std::int32_t dc_interactive_route_cancel(DcBoardHandle h) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    s->interactive->cancel();
    return DC_OK;
}

DC_API std::int32_t dc_interactive_route_preview_info(
    DcBoardHandle h, DcInteractivePreviewInfo* out) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    if (!out) return DC_ERR_INVALID_ARG;
    const auto& preview = s->interactive->preview();
    *out = {};
    out->accepted = preview.accepted ? 1 : 0;
    out->failure = std::int32_t(preview.failure);
    out->traceCount = std::int32_t(preview.addedTraces.size());
    out->viaCount = std::int32_t(preview.addedVias.size());
    out->movedCount = std::int32_t(preview.movedItems.size());
    out->findingCount = std::int32_t(preview.findings.size());
    copyMessage(out->message, sizeof(out->message), preview.message);
    return DC_OK;
}

DC_API std::int32_t dc_interactive_route_get_traces(
    DcBoardHandle h, DcInteractiveTrace* out, std::int32_t cap) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    if (!out || cap < 0) return DC_ERR_INVALID_ARG;
    const auto& values = s->interactive->preview().addedTraces;
    const auto count = std::int32_t(std::min<std::size_t>(values.size(), std::size_t(cap)));
    for (std::int32_t i = 0; i < count; ++i) {
        const auto& t = values[std::size_t(i)];
        out[i] = {t.id, t.a.x, t.a.y, t.b.x, t.b.y, t.width, t.layer, t.netId};
    }
    return count;
}

DC_API std::int32_t dc_interactive_route_get_vias(
    DcBoardHandle h, DcInteractiveVia* out, std::int32_t cap) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    if (!out || cap < 0) return DC_ERR_INVALID_ARG;
    const auto& values = s->interactive->preview().addedVias;
    const auto count = std::int32_t(std::min<std::size_t>(values.size(), std::size_t(cap)));
    for (std::int32_t i = 0; i < count; ++i) {
        const auto& v = values[std::size_t(i)];
        out[i] = {v.id, v.pos.x, v.pos.y, v.diameter, v.drill, v.netId,
                  v.fromLayer, v.toLayer, std::int32_t(v.type)};
    }
    return count;
}

DC_API std::int32_t dc_interactive_route_get_moved(
    DcBoardHandle h, DcInteractiveMovedItem* out, std::int32_t cap) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    if (!out || cap < 0) return DC_ERR_INVALID_ARG;
    const auto& values = s->interactive->preview().movedItems;
    const auto count = std::int32_t(std::min<std::size_t>(values.size(), std::size_t(cap)));
    for (std::int32_t i = 0; i < count; ++i) {
        const auto& v = values[std::size_t(i)];
        out[i] = {v.id, std::int32_t(v.kind), 0, v.oldA.x, v.oldA.y, v.oldB.x,
                  v.oldB.y, v.newA.x, v.newA.y, v.newB.x, v.newB.y};
    }
    return count;
}

DC_API std::int32_t dc_interactive_route_get_finding(
    DcBoardHandle h, std::int32_t index, DcDrcViolation* out) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->interactive) return DC_ERR_ROUTE_INACTIVE;
    if (!out || index < 0) return DC_ERR_INVALID_ARG;
    const auto& values = s->interactive->preview().findings;
    if (std::size_t(index) >= values.size()) return DC_ERR_NOT_FOUND;
    const auto& value = values[std::size_t(index)];
    out->rule = std::int32_t(value.rule);
    out->_pad = 0;
    out->x = value.location.x;
    out->y = value.location.y;
    out->itemA = value.itemA;
    out->itemB = value.itemB;
    copyMessage(out->message, sizeof(out->message), value.message);
    return DC_OK;
}

// ---- DRC ----

DC_API std::int32_t dc_drc_run(DcBoardHandle h, const DcDrcOptions* o) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    DrcOptions opt;
    // Preserve the original API's 13-rule behavior. New clients opt into the
    // additive manufacturing checks through dc_drc_run_v2.
    opt.minCopperToEdge = 0;
    opt.minCopperToHole = 0;
    opt.minCourtyardClearance = 0;
    opt.checkUnassignedCopper = false;
    if (o) {
        if (o->defaultClearance > 0) opt.defaultClearance = o->defaultClearance;
        if (o->minTraceWidth > 0)    opt.minTraceWidth = o->minTraceWidth;
        if (o->minDrill > 0)         opt.minDrill = o->minDrill;
        if (o->minAnnularRing > 0)   opt.minAnnularRing = o->minAnnularRing;
        if (o->minDrillToDrill > 0)  opt.minDrillToDrill = o->minDrillToDrill;
        if (o->minMicroviaDrill > 0) opt.minMicroviaDrill = o->minMicroviaDrill;
        if (o->minMicroviaWall > 0)  opt.minMicroviaWall = o->minMicroviaWall;
        opt.checkConnectivity = o->checkConnectivity != 0;
        opt.checkSkew = o->checkSkew != 0;
    }
    s->drc = runDrc(s->board, opt);
    return std::int32_t(s->drc.size());
}

DC_API std::int32_t dc_drc_run_v2(DcBoardHandle h, const DcDrcOptionsV2* o) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!o || o->abiVersion != 2 || o->structSize != sizeof(DcDrcOptionsV2))
        return DC_ERR_INVALID_ARG;
    DrcOptions opt;
    if (o->defaultClearance > 0) opt.defaultClearance = o->defaultClearance;
    if (o->minTraceWidth > 0) opt.minTraceWidth = o->minTraceWidth;
    if (o->minDrill > 0) opt.minDrill = o->minDrill;
    if (o->minAnnularRing > 0) opt.minAnnularRing = o->minAnnularRing;
    if (o->minDrillToDrill > 0) opt.minDrillToDrill = o->minDrillToDrill;
    if (o->minMicroviaDrill > 0) opt.minMicroviaDrill = o->minMicroviaDrill;
    if (o->minMicroviaWall > 0) opt.minMicroviaWall = o->minMicroviaWall;
    opt.minCopperToEdge = std::max<std::int64_t>(0, o->minCopperToEdge);
    opt.minCopperToHole = std::max<std::int64_t>(0, o->minCopperToHole);
    opt.minCourtyardClearance = std::max<std::int64_t>(0, o->minCourtyardClearance);
    opt.checkConnectivity = o->checkConnectivity != 0;
    opt.checkSkew = o->checkSkew != 0;
    opt.checkUnassignedCopper = o->checkUnassignedCopper != 0;
    s->drc = runDrc(s->board, opt);
    return std::int32_t(s->drc.size());
}

DC_API std::int32_t dc_drc_get(DcBoardHandle h, std::int32_t index, DcDrcViolation* out) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!out || index < 0 || index >= std::int32_t(s->drc.size())) return DC_ERR_INVALID_ARG;
    const DrcViolation& v = s->drc[std::size_t(index)];
    out->rule = std::int32_t(v.rule);
    out->_pad = 0;
    out->x = v.location.x;
    out->y = v.location.y;
    out->itemA = v.itemA;
    out->itemB = v.itemB;
    copyMessage(out->message, sizeof out->message, v.message);
    return DC_OK;
}

// ---- pours ----

DC_API std::int32_t dc_pour_run(DcBoardHandle h, std::int32_t netId, std::int32_t layer,
                                std::int64_t lineWidth, std::int64_t clearance,
                                std::int64_t x0, std::int64_t y0, std::int64_t x1, std::int64_t y1) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (lineWidth <= 0 || clearance < 0 || x1 <= x0 || y1 <= y0) return DC_ERR_INVALID_ARG;
    PourRequest req;
    req.netId = netId;
    req.layer = layer;
    req.lineWidth = lineWidth;
    req.clearance = clearance;
    req.region = {{x0, y0}, {x1, y1}};
    s->pour = generatePour(s->board, req);
    return std::int32_t(s->pour.strokes.size());
}

DC_API std::int32_t dc_pour_zone_run(DcBoardHandle h, std::int32_t zoneId,
                                     std::int64_t lineWidth) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (lineWidth <= 0) return DC_ERR_INVALID_ARG;
    for (const auto& zone : s->board.copperZones()) {
        if (zone.id != zoneId) continue;
        Rect bounds{{INT64_MAX, INT64_MAX}, {INT64_MIN, INT64_MIN}};
        for (const Vec2 p : zone.polygon) {
            bounds.min.x = std::min(bounds.min.x, p.x); bounds.min.y = std::min(bounds.min.y, p.y);
            bounds.max.x = std::max(bounds.max.x, p.x); bounds.max.y = std::max(bounds.max.y, p.y);
        }
        PourRequest req;
        req.netId = zone.netId; req.layer = zone.layer; req.lineWidth = lineWidth;
        req.clearance = zone.clearance; req.region = bounds; req.zonePolygon = zone.polygon;
        s->pour = generatePour(s->board, req);
        return std::int32_t(s->pour.strokes.size());
    }
    return DC_ERR_NOT_FOUND;
}

DC_API std::int32_t dc_pour_get(DcBoardHandle h, std::int32_t index, DcPourStroke* out) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!out || index < 0 || index >= std::int32_t(s->pour.strokes.size())) return DC_ERR_INVALID_ARG;
    const TraceSegment& t = s->pour.strokes[std::size_t(index)];
    *out = {t.a.x, t.a.y, t.b.x, t.b.y, t.width};
    return DC_OK;
}

// ---- queries ----

DC_API std::int64_t dc_net_length(DcBoardHandle h, std::int32_t netId) {
    Session* s = asSession(h);
    if (!s || !s->board.findNet(netId)) return -1;
    return s->board.netRoutedLength(netId);
}

DC_API std::int32_t dc_net_islands(DcBoardHandle h, std::int32_t netId) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->board.findNet(netId)) return DC_ERR_NOT_FOUND;
    return netIslandCount(s->board, netId);
}

DC_API std::int32_t dc_net_island_components(DcBoardHandle h, std::int32_t netId,
                                              DcNetIslandComponent* out,
                                              std::int32_t cap) {
    Session* s = asSession(h);
    if (!s) return DC_ERR_INVALID_HANDLE;
    if (!s->board.findNet(netId)) return DC_ERR_NOT_FOUND;
    if (cap < 0 || (cap > 0 && !out)) return DC_ERR_INVALID_ARG;
    const auto components = netIslandComponents(s->board, netId);
    const auto count = std::int32_t(components.size());
    const auto writeCount = std::min(cap, count);
    for (std::int32_t i = 0; i < writeCount; ++i) {
        const auto& c = components[std::size_t(i)];
        out[i] = {c.anchor.x, c.anchor.y, c.representativeOwnerId, c.layers,
                  c.obstacleCount, c.terminalCount};
    }
    return count;
}


// ---- physics & mathematics engine ----

DC_API std::int32_t dc_phys_microstrip(double wMm, double hMm, double tMm, double er,
                                       double* z0, double* eEff, double* delayPsPerMm) {
    if (!z0 || !eEff || !delayPsPerMm) return DC_ERR_INVALID_ARG;
    dc::phys::LineResult r;
    if (!dc::phys::microstrip(wMm, hMm, tMm, er, r)) return DC_ERR_INVALID_ARG;
    *z0 = r.z0; *eEff = r.eEff; *delayPsPerMm = r.delayPsPerMm;
    return DC_OK;
}

DC_API double dc_phys_microstrip_width(double targetZ0, double hMm, double tMm, double er) {
    return dc::phys::microstripWidthForZ0(targetZ0, hMm, tMm, er);
}

DC_API std::int32_t dc_phys_stripline(double wMm, double bMm, double tMm, double er,
                                      double* z0, double* delayPsPerMm) {
    if (!z0 || !delayPsPerMm) return DC_ERR_INVALID_ARG;
    dc::phys::LineResult r;
    if (!dc::phys::stripline(wMm, bMm, tMm, er, r)) return DC_ERR_INVALID_ARG;
    *z0 = r.z0; *delayPsPerMm = r.delayPsPerMm;
    return DC_OK;
}

DC_API double dc_phys_stripline_width(double targetZ0, double bMm, double tMm, double er) {
    return dc::phys::striplineWidthForZ0(targetZ0, bMm, tMm, er);
}

DC_API double dc_phys_diff_microstrip(double wMm, double sMm, double hMm, double tMm, double er) {
    return dc::phys::differentialMicrostrip(wMm, sMm, hMm, tMm, er);
}

DC_API double dc_phys_diff_stripline(double wMm, double sMm, double bMm, double tMm, double er) {
    return dc::phys::differentialStripline(wMm, sMm, bMm, tMm, er);
}

DC_API double dc_phys_diff_microstrip_width(double targetZdiff, double sMm, double hMm, double tMm, double er) {
    return dc::phys::diffMicrostripWidthForZ(targetZdiff, sMm, hMm, tMm, er);
}

DC_API double dc_phys_skin_depth_mm(double fHz) { return dc::phys::skinDepthMm(fHz); }

DC_API double dc_phys_microstrip_loss_db_per_m(double wMm, double hMm, double tMm, double er,
                                               double tanDelta, double fHz) {
    return dc::phys::microstripLossDbPerM(wMm, hMm, tMm, er, tanDelta, fHz);
}

DC_API double dc_phys_trace_resistance(double wMm, double tMm, double lengthMm, double fHz, double tempC) {
    return fHz > 0 ? dc::phys::traceResistanceAc(wMm, tMm, lengthMm, fHz, tempC)
                   : dc::phys::traceResistanceDc(wMm, tMm, lengthMm, tempC);
}

DC_API double dc_phys_ipc2221_current(double widthMm, double thickMm, double deltaTC, std::int32_t external) {
    return dc::phys::ipc2221CurrentA(widthMm, thickMm, deltaTC, external != 0);
}

DC_API double dc_phys_onderdonk_current(double widthMm, double thickMm, double seconds, double ambientC) {
    return dc::phys::onderdonkFusingA(widthMm, thickMm, seconds, ambientC);
}

DC_API double dc_phys_via_inductance_nh(double heightMm, double drillMm) {
    return dc::phys::viaInductanceNH(heightMm, drillMm);
}

DC_API double dc_phys_via_capacitance_pf(double er, double boardThickMm, double padMm, double antipadMm) {
    return dc::phys::viaCapacitancePF(er, boardThickMm, padMm, antipadMm);
}

DC_API double dc_phys_via_resistance(double heightMm, double drillMm, double platingMm, double tempC) {
    return dc::phys::viaResistanceOhm(heightMm, drillMm, platingMm, tempC);
}

DC_API double dc_phys_via_thermal_kw(double heightMm, double drillMm, double platingMm) {
    return dc::phys::viaThermalResistanceKW(heightMm, drillMm, platingMm);
}

DC_API double dc_phys_via_current(double drillMm, double platingMm, double deltaTC) {
    return dc::phys::viaCurrentA(drillMm, platingMm, deltaTC);
}

DC_API double dc_phys_crosstalk(double sMm, double hMm) {
    return dc::phys::crosstalkCoefficient(sMm, hMm);
}

DC_API double dc_phys_plane_capacitance_pf(double areaMm2, double dielectricMm, double er) {
    return dc::phys::planeCapacitancePF(areaMm2, dielectricMm, er);
}

DC_API std::int32_t dc_dense_lu_solve(double* A, std::int32_t n, const double* B,
                                      std::int32_t rhs, double* X) {
    return dc::dense_lu_solve(A, n, B, rhs, X) ? DC_OK : DC_ERR_INVALID_ARG;
}

// ---- mechanical analysis engine ----

DC_API DcMechanicalHandle dc_mechanical_create() {
    return new MechanicalSession();
}

DC_API void dc_mechanical_destroy(DcMechanicalHandle h) {
    delete static_cast<MechanicalSession*>(h);
}

DC_API std::int32_t dc_mechanical_set_system(
    DcMechanicalHandle h, const DcMechanicalCsr* stiffness,
    const double* rhs, const std::int32_t* fixedDofs,
    const double* fixedValues, std::int32_t fixedCount) {
    auto* session = static_cast<MechanicalSession*>(h);
    if (!session) return DC_ERR_INVALID_HANDLE;
    if (!stiffness || stiffness->abiVersion != 1
        || stiffness->structSize < sizeof(DcMechanicalCsr)
        || !rhs || fixedCount < 0
        || (fixedCount > 0 && (!fixedDofs || !fixedValues))
        || stiffness->rows <= 0 || stiffness->cols <= 0 || stiffness->nnz < 0
        || !stiffness->rowOffsets
        || (stiffness->nnz > 0 && (!stiffness->columns || !stiffness->values)))
        return DC_ERR_SOLVER_INVALID;

    dc::mechanical::LinearSystem system;
    system.stiffness.rows = stiffness->rows;
    system.stiffness.cols = stiffness->cols;
    system.stiffness.rowOffsets.assign(stiffness->rowOffsets,
                                       stiffness->rowOffsets + stiffness->rows + 1);
    if (stiffness->nnz > 0) {
        system.stiffness.columns.assign(stiffness->columns,
                                        stiffness->columns + stiffness->nnz);
        system.stiffness.values.assign(stiffness->values,
                                       stiffness->values + stiffness->nnz);
    }
    system.rhs.assign(rhs, rhs + stiffness->rows);
    if (fixedCount > 0) {
        system.fixedDofs.assign(fixedDofs, fixedDofs + fixedCount);
        system.fixedValues.assign(fixedValues, fixedValues + fixedCount);
    }
    std::string diagnostic;
    if (!system.stiffness.valid(&diagnostic)) return DC_ERR_SOLVER_INVALID;
    session->system = std::move(system);
    session->result = {};
    session->configured = true;
    return DC_OK;
}

DC_API std::int32_t dc_mechanical_solve(DcMechanicalHandle h,
                                         const DcMechanicalOptions* options) {
    auto* session = static_cast<MechanicalSession*>(h);
    if (!session) return DC_ERR_INVALID_HANDLE;
    if (!session->configured) return DC_ERR_SOLVER_INVALID;

    dc::mechanical::SolverOptions native;
    if (options) {
        if (options->abiVersion != 1 || options->structSize < sizeof(DcMechanicalOptions)
            || options->backend < DC_MECHANICAL_AUTO
            || options->backend > DC_MECHANICAL_CUDA)
            return DC_ERR_SOLVER_INVALID;
        native.backend = static_cast<dc::mechanical::SolverBackend>(options->backend);
        native.maxIterations = options->maxIterations;
        native.tolerance = options->tolerance;
        native.requireSpd = options->requireSpd != 0;
    }
    session->result = session->engine.solveLinearStatic(session->system, native);
    if (session->result.converged) return DC_OK;
    if (native.backend == dc::mechanical::SolverBackend::Cuda
        && !dc::mechanical::MechanicalEngine::cudaAvailable())
        return DC_ERR_SOLVER_UNAVAILABLE;
    return DC_ERR_SOLVER_NOT_CONVERGED;
}

DC_API std::int32_t dc_mechanical_result(DcMechanicalHandle h,
                                          DcMechanicalResult* out) {
    auto* session = static_cast<MechanicalSession*>(h);
    if (!session) return DC_ERR_INVALID_HANDLE;
    if (!out) return DC_ERR_INVALID_ARG;
    const auto& result = session->result;
    out->status = result.converged ? DC_OK : DC_ERR_SOLVER_NOT_CONVERGED;
    out->backend = static_cast<std::int32_t>(result.backend);
    out->converged = result.converged ? 1 : 0;
    out->iterations = result.iterations;
    out->residualNorm = result.residualNorm;
    out->solutionCount = static_cast<std::int32_t>(result.solution.size());
    out->reserved = 0;
    copyMessage(out->message, sizeof out->message, result.diagnostic);
    return DC_OK;
}

DC_API std::int32_t dc_mechanical_solution_count(DcMechanicalHandle h) {
    auto* session = static_cast<MechanicalSession*>(h);
    if (!session) return DC_ERR_INVALID_HANDLE;
    return static_cast<std::int32_t>(session->result.solution.size());
}

DC_API std::int32_t dc_mechanical_get_solution(DcMechanicalHandle h,
                                                double* out, std::int32_t cap) {
    auto* session = static_cast<MechanicalSession*>(h);
    if (!session) return DC_ERR_INVALID_HANDLE;
    if (cap < 0 || (cap > 0 && !out)) return DC_ERR_INVALID_ARG;
    const auto count = static_cast<std::int32_t>(session->result.solution.size());
    if (cap == 0 || !out) return count;
    const auto written = std::min(cap, count);
    std::copy_n(session->result.solution.begin(), written, out);
    return count;
}

DC_API std::int32_t dc_mechanical_cuda_available() {
    return dc::mechanical::MechanicalEngine::cudaAvailable() ? 1 : 0;
}

DC_API std::int32_t dc_mechanical_uniform_cantilever_modal(
    const DcMechanicalUniformCantileverModalInput* input,
    DcMechanicalModalResult* out,
    double* frequenciesHz, std::int32_t frequencyCapacity,
    double* normalizedModeShapes, std::int32_t shapeCapacity) {
    if (!input || !out || input->abiVersion != 1
        || input->structSize < sizeof(DcMechanicalUniformCantileverModalInput)
        || frequencyCapacity < input->modeCount
        || shapeCapacity < input->modeCount * input->shapeSampleCount
        || (frequencyCapacity > 0 && !frequenciesHz)
        || (shapeCapacity > 0 && !normalizedModeShapes))
        return DC_ERR_SOLVER_INVALID;

    dc::mechanical::UniformCantileverModalModel model;
    model.lengthM = input->lengthM;
    model.youngsModulusPa = input->youngsModulusPa;
    model.secondMomentM4 = input->secondMomentM4;
    model.crossSectionAreaM2 = input->crossSectionAreaM2;
    model.densityKgPerM3 = input->densityKgPerM3;
    model.modeCount = input->modeCount;
    model.shapeSampleCount = input->shapeSampleCount;
    dc::mechanical::SolverOptions options;
    options.backend = dc::mechanical::SolverBackend::Cpu;
    const auto solved = dc::mechanical::MechanicalEngine()
        .solveUniformCantileverModes(model, options);
    out->status = solved.converged ? DC_OK : DC_ERR_SOLVER_NOT_CONVERGED;
    out->backend = static_cast<std::int32_t>(solved.backend);
    out->converged = solved.converged ? 1 : 0;
    out->modeCount = static_cast<std::int32_t>(solved.frequenciesHz.size());
    out->shapeSampleCount = solved.converged ? model.shapeSampleCount : 0;
    out->reserved = 0;
    copyMessage(out->message, sizeof out->message, solved.diagnostic);
    if (!solved.converged) return DC_ERR_SOLVER_NOT_CONVERGED;
    std::copy(solved.frequenciesHz.begin(), solved.frequenciesHz.end(), frequenciesHz);
    std::int32_t offset = 0;
    for (const auto& modeShape : solved.normalizedModeShapes) {
        std::copy(modeShape.begin(), modeShape.end(), normalizedModeShapes + offset);
        offset += static_cast<std::int32_t>(modeShape.size());
    }
    return DC_OK;
}
