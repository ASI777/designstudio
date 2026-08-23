#include "designcore/interactive_router.h"
#include "designcore/obstacle_index.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <unordered_set>

namespace dc {

const char* interactiveRouteFailText(InteractiveRouteFail failure) {
    switch (failure) {
        case InteractiveRouteFail::None: return "ok";
        case InteractiveRouteFail::Inactive: return "routing session is not active";
        case InteractiveRouteFail::BadRequest: return "invalid interactive route request";
        case InteractiveRouteFail::NoPath: return "no legal walkaround path";
        case InteractiveRouteFail::BlockedByFixedItem: return "route is blocked by fixed copper or a footprint";
        case InteractiveRouteFail::ShoveDepthLimit: return "recursive shove depth limit reached";
        case InteractiveRouteFail::OnlineDrc: return "preview violates an online design rule";
    }
    return "unknown interactive routing failure";
}

namespace {

coord_t effectiveClearance(const Board& board, int a, int b, coord_t requested) {
    coord_t clearance = std::max({requested, board.rulesFor(a).clearance,
                                  board.rulesFor(b).clearance});
    return board.pairClearance(a, b, clearance);
}

Rect traceProbe(const TraceSegment& trace, coord_t extra) {
    const coord_t r = trace.width / 2 + extra;
    return {{std::min(trace.a.x, trace.b.x) - r, std::min(trace.a.y, trace.b.y) - r},
            {std::max(trace.a.x, trace.b.x) + r, std::max(trace.a.y, trace.b.y) + r}};
}

double signedDistance(Vec2 p, Vec2 a, Vec2 b) {
    const double dx = double(b.x - a.x), dy = double(b.y - a.y);
    const double length = std::hypot(dx, dy);
    if (length == 0) return 0;
    return (dx * double(p.y - a.y) - dy * double(p.x - a.x)) / length;
}

Vec2 normalShift(Vec2 a, Vec2 b, double amount) {
    const double dx = double(b.x - a.x), dy = double(b.y - a.y);
    const double length = std::hypot(dx, dy);
    if (length == 0) return {};
    return {coord_t(std::llround(-dy * amount / length)),
            coord_t(std::llround( dx * amount / length))};
}

bool obstacleHitsTrace(const Board& board, const TraceSegment& moving,
                       const Obstacle& obstacle, coord_t requestedClearance) {
    if (!(obstacle.layers & layerBit(moving.layer)) || obstacle.ownerId == moving.id
        || obstacle.viaKeepout || obstacle.netId == moving.netId) return false;
    const coord_t clearance = effectiveClearance(board, moving.netId,
                                                 obstacle.netId, requestedClearance);
    return ObstacleIndex::distanceTo(obstacle, moving.a, moving.b)
        < double(moving.width / 2 + clearance);
}

struct ShoveContext {
    coord_t clearance = 0;
    std::size_t maxDepth = 0;
    bool depthExceeded = false;
    std::unordered_set<id_t> stack;
};

bool viaPositionLegal(const Board& board, const Via& via, id_t ignore,
                      coord_t requestedClearance) {
    if (!board.containsPoint(via.pos)
        || board.distancePointToEdge(via.pos) < double(via.diameter / 2)
        || !board.viaAllowed(via.pos, via.mask())) return false;
    ObstacleIndex index(board);
    const coord_t probe = board.maxClearanceForNet(via.netId, requestedClearance)
                        + via.diameter / 2 + index.maxDrillRadius();
    bool legal = true;
    index.query({{via.pos.x - probe, via.pos.y - probe},
                 {via.pos.x + probe, via.pos.y + probe}}, [&](const Obstacle& obstacle) {
        if (!legal || obstacle.ownerId == ignore || !(obstacle.layers & via.mask())) return;
        if (obstacle.netId == via.netId && obstacle.drill == 0) return;
        const coord_t clearance = effectiveClearance(board, via.netId,
                                                     obstacle.netId, requestedClearance);
        if (ObstacleIndex::distanceTo(obstacle, via.pos)
            < double(via.diameter / 2 + clearance)) legal = false;
    });
    return legal;
}

bool shoveTraceAway(Board& board, id_t traceId, Vec2 avoidA, Vec2 avoidB,
                    coord_t avoidWidth, int avoidNet, std::size_t depth,
                    ShoveContext& context);

bool shoveViaAway(Board& board, id_t viaId, Vec2 avoidA, Vec2 avoidB,
                  coord_t avoidWidth, int avoidNet, std::size_t depth,
                  ShoveContext& context) {
    if (depth > context.maxDepth) { context.depthExceeded = true; return false; }
    const Via* original = board.findVia(viaId);
    if (!original || context.stack.contains(viaId)) return false;
    const coord_t required = avoidWidth / 2 + original->diameter / 2
                           + effectiveClearance(board, avoidNet, original->netId,
                                                context.clearance) + 2;
    const double side = signedDistance(original->pos, avoidA, avoidB);
    std::vector<double> shifts{double(required) - side, -double(required) - side};
    std::stable_sort(shifts.begin(), shifts.end(), [](double a, double b) {
        return std::abs(a) < std::abs(b);
    });
    context.stack.insert(viaId);
    for (double amount : shifts) {
        Board trial = board;
        const Vec2 delta = normalShift(avoidA, avoidB, amount);
        const Via* prior = trial.findVia(viaId);
        if (!prior || delta == Vec2{}) continue;
        trial.moveVia(viaId, prior->pos + delta);
        const Via* moved = trial.findVia(viaId);
        if (moved && viaPositionLegal(trial, *moved, viaId, context.clearance)) {
            board = std::move(trial);
            context.stack.erase(viaId);
            return true;
        }
    }
    context.stack.erase(viaId);
    return false;
}

bool resolveTraceCollisions(Board& board, id_t traceId, std::size_t depth,
                            ShoveContext& context) {
    const TraceSegment* movingPtr = board.findTrace(traceId);
    if (!movingPtr) return false;
    const TraceSegment moving = *movingPtr;
    if (!board.segmentWithinOutline(moving.a, moving.b, moving.width / 2)
        || !board.routingAllowed(moving.a, moving.b, moving.layer)) return false;

    for (;;) {
        ObstacleIndex index(board);
        id_t blocker = 0;
        Obstacle blockerObstacle;
        index.query(traceProbe(moving, board.maxClearanceForNet(moving.netId,
                                                               context.clearance)),
                    [&](const Obstacle& obstacle) {
            if (!blocker && obstacleHitsTrace(board, moving, obstacle, context.clearance)) {
                blocker = obstacle.ownerId;
                blockerObstacle = obstacle;
            }
        });
        if (!blocker) return true;

        if (const TraceSegment* other = board.findTrace(blocker)) {
            if (other->isPour) return false;
            if (!shoveTraceAway(board, blocker, moving.a, moving.b, moving.width,
                                moving.netId, depth + 1, context)) return false;
        } else if (board.findVia(blocker)) {
            if (!shoveViaAway(board, blocker, moving.a, moving.b, moving.width,
                              moving.netId, depth + 1, context)) return false;
        } else {
            return false; // pads, custom copper, and component geometry are fixed
        }
    }
}

bool shoveTraceAway(Board& board, id_t traceId, Vec2 avoidA, Vec2 avoidB,
                    coord_t avoidWidth, int avoidNet, std::size_t depth,
                    ShoveContext& context) {
    if (depth > context.maxDepth) { context.depthExceeded = true; return false; }
    const TraceSegment* original = board.findTrace(traceId);
    if (!original || original->isPour || context.stack.contains(traceId)) return false;
    const coord_t required = avoidWidth / 2 + original->width / 2
                           + effectiveClearance(board, avoidNet, original->netId,
                                                context.clearance) + 2;
    const double low = std::min(signedDistance(original->a, avoidA, avoidB),
                                signedDistance(original->b, avoidA, avoidB));
    const double high = std::max(signedDistance(original->a, avoidA, avoidB),
                                 signedDistance(original->b, avoidA, avoidB));
    std::vector<double> shifts{double(required) - low, -double(required) - high};
    std::stable_sort(shifts.begin(), shifts.end(), [](double a, double b) {
        return std::abs(a) < std::abs(b);
    });

    context.stack.insert(traceId);
    for (double amount : shifts) {
        Board trial = board;
        const TraceSegment* prior = trial.findTrace(traceId);
        const Vec2 delta = normalShift(avoidA, avoidB, amount);
        if (!prior || delta == Vec2{}) continue;
        trial.moveTrace(traceId, prior->a + delta, prior->b + delta);
        if (resolveTraceCollisions(trial, traceId, depth, context)) {
            board = std::move(trial);
            context.stack.erase(traceId);
            return true;
        }
    }
    context.stack.erase(traceId);
    return false;
}

std::unordered_set<id_t> changedIds(const Board& before, const Board& after) {
    std::unordered_set<id_t> ids;
    for (const auto& trace : after.traces()) {
        const auto* old = before.findTrace(trace.id);
        if (!old || old->a != trace.a || old->b != trace.b) ids.insert(trace.id);
    }
    for (const auto& via : after.vias()) {
        const auto* old = before.findVia(via.id);
        if (!old || old->pos != via.pos) ids.insert(via.id);
    }
    return ids;
}

} // namespace

InteractiveRouterSession::InteractiveRouterSession(Board& target)
    : target_(target), original_(target), staged_(target), previewBoard_(target) {}

void InteractiveRouterSession::reject(InteractiveRouteFail failure, std::string message) {
    preview_.accepted = false;
    preview_.failure = failure;
    preview_.message = std::move(message);
    hasAcceptedPreview_ = false;
    previewBoard_ = staged_;
}

const InteractiveRoutePreview& InteractiveRouterSession::begin(
    const InteractiveRouteRequest& request) {
    request_ = request;
    preview_ = {};
    original_ = target_;
    staged_ = target_;
    previewBoard_ = target_;
    anchor_ = cursor_ = request.start;
    layer_ = request.startLayer;
    active_ = false;
    hasAcceptedPreview_ = false;
    if (!target_.findNet(request.netId) || request.startLayer < 0
        || request.startLayer >= target_.copperLayerCount || request.traceWidth <= 0
        || request.clearance < 0 || request.gridStep <= 0 || request.maxShoveDepth == 0
        || !target_.containsPoint(request.start)) {
        reject(InteractiveRouteFail::BadRequest, interactiveRouteFailText(InteractiveRouteFail::BadRequest));
        return preview_;
    }
    active_ = true;
    preview_.accepted = true;
    preview_.failure = InteractiveRouteFail::None;
    preview_.message = interactiveRouteFailText(InteractiveRouteFail::None);
    hasAcceptedPreview_ = true;
    return preview_;
}

RouteRequest InteractiveRouterSession::legRequest(Vec2 goal, int goalLayer) const {
    RouteRequest request;
    request.netId = request_.netId;
    request.start = anchor_;
    request.goal = goal;
    request.startLayer = layer_;
    request.goalLayer = goalLayer;
    request.traceWidth = request_.traceWidth;
    request.clearance = request_.clearance;
    request.gridStep = request_.gridStep;
    request.allowVias = request_.allowVias;
    request.allowMicrovia = request_.allowMicrovia;
    request.viaDiameter = request_.viaDiameter;
    request.viaDrill = request_.viaDrill;
    request.minDrillToDrill = request_.minDrillToDrill;
    request.maxExpansions = request_.maxExpansions;
    return request;
}

bool InteractiveRouterSession::buildWalkaround(Board& candidate, Vec2 cursor) {
    RouteRequest request = legRequest(cursor, layer_);
    request.allowVias = false;
    Router router(candidate);
    const RouteResult route = router.route(request);
    if (!route.success) {
        reject(InteractiveRouteFail::NoPath, routeFailText(route.reason));
        return false;
    }
    for (std::size_t i = 1; i < route.path.size(); ++i) {
        if (route.path[i - 1].p == route.path[i].p) continue;
        candidate.addTrace({0, route.path[i - 1].p, route.path[i].p,
                            request_.traceWidth, layer_, request_.netId, false});
    }
    return true;
}

bool InteractiveRouterSession::buildShove(Board& candidate, Vec2 cursor) {
    if (cursor == anchor_) return true;
    const Board beforeHead = candidate;
    const id_t head = candidate.addTrace({0, anchor_, cursor, request_.traceWidth,
                                          layer_, request_.netId, false});
    ShoveContext context{request_.clearance, request_.maxShoveDepth, false, {}};
    if (!resolveTraceCollisions(candidate, head, 0, context)) {
        // Fixed copper cannot move, but shove mode can still find a legal
        // walkaround. Restart from the pre-head snapshot so failed recursive
        // displacement never leaks into the fallback search.
        if (!context.depthExceeded) {
            candidate = beforeHead;
            return buildWalkaround(candidate, cursor);
        }
        reject(context.depthExceeded ? InteractiveRouteFail::ShoveDepthLimit
                                     : InteractiveRouteFail::BlockedByFixedItem,
               interactiveRouteFailText(context.depthExceeded
                   ? InteractiveRouteFail::ShoveDepthLimit
                   : InteractiveRouteFail::BlockedByFixedItem));
        return false;
    }
    return true;
}

void InteractiveRouterSession::describeChanges(const Board& before, const Board& after) {
    preview_.addedTraces.clear();
    preview_.addedVias.clear();
    preview_.movedItems.clear();
    for (const auto& trace : after.traces()) {
        const TraceSegment* old = before.findTrace(trace.id);
        if (!old) preview_.addedTraces.push_back(trace);
        else if (old->a != trace.a || old->b != trace.b)
            preview_.movedItems.push_back({trace.id, InteractiveItemKind::Trace,
                                           old->a, old->b, trace.a, trace.b});
    }
    for (const auto& via : after.vias()) {
        const Via* old = before.findVia(via.id);
        if (!old) preview_.addedVias.push_back(via);
        else if (old->pos != via.pos)
            preview_.movedItems.push_back({via.id, InteractiveItemKind::Via,
                                           old->pos, old->pos, via.pos, via.pos});
    }
}

bool InteractiveRouterSession::validatePreview(const Board& before, Board& candidate) {
    const auto ids = changedIds(before, candidate);
    DrcOptions options;
    options.defaultClearance = request_.clearance;
    options.minTraceWidth = std::min(request_.traceWidth, coord_t(100'000));
    options.minDrillToDrill = request_.minDrillToDrill;
    options.checkConnectivity = false;
    options.checkSkew = false;
    options.checkUnassignedCopper = false;
    const auto all = runDrc(candidate, options);
    preview_.findings.clear();
    for (const auto& finding : all)
        if (ids.contains(finding.itemA) || ids.contains(finding.itemB))
            preview_.findings.push_back(finding);
    if (!preview_.findings.empty()) {
        reject(InteractiveRouteFail::OnlineDrc,
               preview_.findings.front().message.empty()
                   ? interactiveRouteFailText(InteractiveRouteFail::OnlineDrc)
                   : preview_.findings.front().message);
        return false;
    }
    return true;
}

const InteractiveRoutePreview& InteractiveRouterSession::updateCursor(Vec2 cursor) {
    preview_ = {};
    if (!active_) {
        reject(InteractiveRouteFail::Inactive, interactiveRouteFailText(InteractiveRouteFail::Inactive));
        return preview_;
    }
    cursor_ = cursor;
    Board candidate = staged_; // cursor previews always spring back to this snapshot
    const bool built = request_.mode == InteractiveRouteMode::Shove
        ? buildShove(candidate, cursor) : buildWalkaround(candidate, cursor);
    if (!built) return preview_;
    describeChanges(original_, candidate);
    if (!validatePreview(staged_, candidate)) return preview_;
    previewBoard_ = std::move(candidate);
    preview_.accepted = true;
    preview_.failure = InteractiveRouteFail::None;
    preview_.message = interactiveRouteFailText(InteractiveRouteFail::None);
    hasAcceptedPreview_ = true;
    return preview_;
}

const InteractiveRoutePreview& InteractiveRouterSession::placeVia(int targetLayer) {
    if (!active_) {
        preview_ = {};
        reject(InteractiveRouteFail::Inactive, interactiveRouteFailText(InteractiveRouteFail::Inactive));
        return preview_;
    }
    if (!hasAcceptedPreview_ || !request_.allowVias || targetLayer < 0
        || targetLayer >= target_.copperLayerCount || targetLayer == layer_) {
        preview_ = {};
        reject(InteractiveRouteFail::BadRequest, interactiveRouteFailText(InteractiveRouteFail::BadRequest));
        return preview_;
    }

    staged_ = previewBoard_;
    anchor_ = cursor_;
    Board candidate = staged_;
    const RouteResult transition = Router(candidate).route(legRequest(anchor_, targetLayer));
    if (!transition.success || transition.vias.empty()) {
        preview_ = {};
        reject(InteractiveRouteFail::BlockedByFixedItem, routeFailText(transition.reason));
        return preview_;
    }
    for (const auto& via : transition.vias) {
        Via item;
        item.pos = via.pos;
        item.diameter = request_.viaDiameter;
        item.drill = request_.viaDrill;
        item.netId = request_.netId;
        item.fromLayer = std::min(via.fromLayer, via.toLayer);
        item.toLayer = std::max(via.fromLayer, via.toLayer);
        item.type = via.type;
        candidate.addVia(item);
    }
    preview_ = {};
    describeChanges(original_, candidate);
    if (!validatePreview(staged_, candidate)) return preview_;
    staged_ = candidate;
    previewBoard_ = std::move(candidate);
    layer_ = targetLayer;
    preview_.accepted = true;
    preview_.failure = InteractiveRouteFail::None;
    preview_.message = interactiveRouteFailText(InteractiveRouteFail::None);
    hasAcceptedPreview_ = true;
    return preview_;
}

bool InteractiveRouterSession::commit() {
    if (!active_ || !hasAcceptedPreview_) return false;
    target_ = previewBoard_;
    staged_ = target_;
    active_ = false;
    hasAcceptedPreview_ = false;
    return true;
}

void InteractiveRouterSession::cancel() {
    staged_ = target_;
    previewBoard_ = target_;
    preview_ = {};
    preview_.failure = InteractiveRouteFail::Inactive;
    preview_.message = interactiveRouteFailText(InteractiveRouteFail::Inactive);
    active_ = false;
    hasAcceptedPreview_ = false;
}

} // namespace dc
