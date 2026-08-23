#include "designcore/router.h"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <queue>
#include <unordered_map>

namespace dc {

const char* routeFailText(RouteFail f) {
    switch (f) {
        case RouteFail::None:           return "ok";
        case RouteFail::NoPath:         return "no path exists at this width/clearance";
        case RouteFail::IterationLimit: return "expansion limit reached (board too dense for grid step)";
        case RouteFail::StartBlocked:   return "start point is inside another net's keepout";
        case RouteFail::GoalBlocked:    return "goal point is inside another net's keepout";
        case RouteFail::BadRequest:     return "invalid request (layers/geometry)";
    }
    return "unknown";
}

namespace {

struct Key {
    coord_t x, y;
    int layer;
    bool operator==(const Key&) const = default;
};

struct KeyHash {
    std::size_t operator()(const Key& k) const {
        std::size_t h = std::hash<coord_t>()(k.x) * 1000003u;
        h ^= std::hash<coord_t>()(k.y) + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
        h ^= std::size_t(k.layer) * 2654435761u;
        return h;
    }
};

struct Node { Key k; double f; };
struct NodeCmp { bool operator()(const Node& a, const Node& b) const { return a.f > b.f; } };

double octile(Vec2 a, Vec2 b) {
    const double dx = std::abs(double(a.x - b.x)), dy = std::abs(double(a.y - b.y));
    return std::max(dx, dy) + 0.41421356 * std::min(dx, dy);
}

// Exact integer collinearity for grid paths: normalize each step direction by
// its gcd and compare. No floating point, no epsilon, no overflow.
bool sameDirection(Vec2 a, Vec2 b, Vec2 c) {
    coord_t d1x = b.x - a.x, d1y = b.y - a.y;
    coord_t d2x = c.x - b.x, d2y = c.y - b.y;
    auto norm = [](coord_t& x, coord_t& y) {
        coord_t g = std::gcd(std::abs(x), std::abs(y));
        if (g > 0) { x /= g; y /= g; }
    };
    norm(d1x, d1y);
    norm(d2x, d2y);
    return d1x == d2x && d1y == d2y;
}

coord_t obstacleClearance(const Board& board, const Obstacle& obstacle,
                          int routeNet, coord_t inherited) {
    coord_t value = std::max(inherited, board.rulesFor(obstacle.netId).clearance);
    value = board.pairClearance(routeNet, obstacle.netId, value);
    if (obstacle.pad && obstacle.pad->clearanceOverride > 0)
        value = std::max(value, obstacle.pad->clearanceOverride);
    return value;
}

} // namespace

bool Router::isBlocked(Vec2 p, int layer, const RouteRequest& req) const {
    const coord_t routeClearance = board_.clearanceForSegment(p, p, layer, req.clearance);
    const coord_t probeKeepout = board_.maxClearanceForNet(req.netId, routeClearance)
                               + req.traceWidth / 2;
    if (!board_.layerRoutingAllowed(req.netId, layer) || !board_.routingAllowed(p, p, layer)) return true;
    if (!board_.segmentWithinOutline(
            p, p, req.traceWidth / 2 + req.edgeClearance)) return true;

    bool blocked = false;
    const Rect probe{{p.x - probeKeepout, p.y - probeKeepout},
                     {p.x + probeKeepout, p.y + probeKeepout}};
    index_.query(probe, [&](const Obstacle& o) {
        if (blocked) return;
        if (o.viaKeepout) return;        // courtyards block vias only, not traces
        if (o.netId == req.netId) return;
        if (!(o.layers & layerBit(layer))) return;
        const coord_t keepout = obstacleClearance(board_, o, req.netId, routeClearance)
                              + req.traceWidth / 2;
        if (ObstacleIndex::distanceTo(o, p) < double(keepout)) blocked = true;
    });
    return blocked;
}

bool Router::isSegmentBlocked(Vec2 a, Vec2 b, int layer, const RouteRequest& req) const {
    const coord_t routeClearance = board_.clearanceForSegment(a, b, layer, req.clearance);
    const coord_t probeKeepout = board_.maxClearanceForNet(req.netId, routeClearance)
                               + req.traceWidth / 2;
    if (!board_.layerRoutingAllowed(req.netId, layer) || !board_.routingAllowed(a, b, layer)) return true;
    if (!board_.segmentWithinOutline(
            a, b, req.traceWidth / 2 + req.edgeClearance)) return true;
    bool blocked = false;
    const Rect probe{{std::min(a.x, b.x) - probeKeepout, std::min(a.y, b.y) - probeKeepout},
                     {std::max(a.x, b.x) + probeKeepout, std::max(a.y, b.y) + probeKeepout}};
    index_.query(probe, [&](const Obstacle& o) {
        if (blocked || o.viaKeepout || o.netId == req.netId) return;
        if (!(o.layers & layerBit(layer))) return;
        const coord_t keepout = obstacleClearance(board_, o, req.netId, routeClearance)
                              + req.traceWidth / 2;
        if (ObstacleIndex::distanceTo(o, a, b) < double(keepout)) blocked = true;
    });
    return blocked;
}

bool Router::viaFits(Vec2 p, layermask_t span, ViaType type, const RouteRequest& req) const {
    if (!board_.viaTypeAllowed(req.netId, type) || !board_.viaAllowed(p, span)) return false;
    coord_t clearance = req.clearance;
    for (int layer = 0; layer < board_.copperLayerCount; ++layer)
        if (span & layerBit(layer))
            clearance = board_.clearanceForSegment(p, p, layer, clearance);
    const coord_t copperKeepout = board_.maxClearanceForNet(req.netId, clearance)
                                + req.viaDiameter / 2;
    const coord_t drillKeepout = req.viaDrill / 2 + req.minDrillToDrill
                               + index_.maxDrillRadius();
    const coord_t probeKeepout = std::max(copperKeepout, drillKeepout);
    if (!board_.containsPoint(p)
        || board_.distancePointToEdge(p)
               < double(req.viaDiameter / 2 + req.edgeClearance)) return false;

    bool blocked = false;
    const Rect probe{{p.x - probeKeepout, p.y - probeKeepout},
                     {p.x + probeKeepout, p.y + probeKeepout}};
    index_.query(probe, [&](const Obstacle& o) {
        if (blocked) return;
        if (!(o.layers & span)) return;
        // Drill-wall spacing applies even on the same net. Copper connectivity
        // does not make two drilled holes manufacturable when their finished
        // walls are too close.
        if (o.drill > 0) {
            const double centerDistance = (p - o.center).length();
            const double required = double(req.viaDrill + o.drill) / 2.0
                                  + double(req.minDrillToDrill);
            if (centerDistance < required) blocked = true;
            if (blocked) return;
        }
        // Component courtyards block vias regardless of net — keeps vias out from
        // under component bodies (where an unfilled via wicks solder / shorts to
        // the part). The trace router ignores these (see isBlocked).
        if (o.viaKeepout) {
            if (ObstacleIndex::distanceTo(o, p) < double(probeKeepout)) blocked = true;
            return;
        }
        // A via must clear EVERY pad — even one on its own net. A bare via dropped
        // in a pad ("via-in-pad") wicks solder during reflow unless it is filled &
        // plated over, so we forbid it: the router instead escapes a short stub out
        // of the pad and drops the via in clear copper. Same-net TRACES are fine to
        // overlap (the via lands on its own route); other-net copper keeps clearance.
        const bool padCopper = o.kind == Obstacle::Kind::PadRect
                            || o.kind == Obstacle::Kind::PadRounded
                            || o.kind == Obstacle::Kind::Polygon;
        if (!padCopper && o.netId == req.netId) return;
        const coord_t keepout = obstacleClearance(board_, o, req.netId, clearance)
                              + req.viaDiameter / 2;
        if (ObstacleIndex::distanceTo(o, p) < double(keepout)) blocked = true;
    });
    return blocked ? false : true;
}

RouteResult Router::route(const RouteRequest& req) const {
    RouteResult res;
    const int nLayers = board_.copperLayerCount;
    if (req.startLayer < 0 || req.startLayer >= nLayers ||
        req.goalLayer  < 0 || req.goalLayer  >= nLayers ||
        !board_.layerRoutingAllowed(req.netId, req.startLayer) ||
        !board_.layerRoutingAllowed(req.netId, req.goalLayer) ||
        req.gridStep <= 0 || req.traceWidth <= 0 || req.edgeClearance < 0) {
        res.reason = RouteFail::BadRequest;
        return res;
    }

    const coord_t g = req.gridStep;
    // nearest-grid snap (plain integer division floors, biasing every route
    // toward -x/-y and away from the clicked pad centre)
    auto roundTo = [g](coord_t v) {
        return ((v >= 0 ? v + g / 2 : v - g / 2) / g) * g;
    };
    auto snap = [&](Vec2 p) { return Vec2{roundTo(p.x), roundTo(p.y)}; };
    const Vec2 start = snap(req.start), goal = snap(req.goal);
    const Key startKey{start.x, start.y, req.startLayer};
    const Key goalKey{goal.x, goal.y, req.goalLayer};

    if (isBlocked(req.start, req.startLayer, req)) { res.reason = RouteFail::StartBlocked; return res; }
    if (isBlocked(req.goal, req.goalLayer, req)) { res.reason = RouteFail::GoalBlocked; return res; }
    if (isBlocked(start, req.startLayer, req)) { res.reason = RouteFail::StartBlocked; return res; }
    if (isBlocked(goal,  req.goalLayer,  req)) { res.reason = RouteFail::GoalBlocked;  return res; }

    const double viaCost = req.viaCostMm * double(NM_PER_MM);
    // min possible layer-change cost keeps the heuristic admissible
    const double minViaCost = req.allowMicrovia ? viaCost * 0.6 : viaCost;
    auto heuristic = [&](const Key& k) {
        double h = octile({k.x, k.y}, goal);
        if (req.allowVias && k.layer != goalKey.layer) h += minViaCost;
        return h;
    };

    std::priority_queue<Node, std::vector<Node>, NodeCmp> open;
    std::unordered_map<Key, Key, KeyHash> cameFrom;
    std::unordered_map<Key, double, KeyHash> gScore;
    gScore.reserve(1 << 16);
    cameFrom.reserve(1 << 16);

    gScore[startKey] = 0;
    open.push({startKey, heuristic(startKey)});

    const Vec2 dirs[8] = {{g,0},{-g,0},{0,g},{0,-g},{g,g},{g,-g},{-g,g},{-g,-g}};
    const layermask_t throughSpan = board_.allLayersMask();
    int existingVias = 0;
    for (const auto& via : board_.vias()) if (via.netId == req.netId) ++existingVias;
    const int maxVias = board_.maxViaCountForNet(req.netId);
    const bool viaBudgetAvailable = maxVias <= 0 || existingVias < maxVias;

    bool found = false;
    while (!open.empty()) {
        const Key cur = open.top().k;
        const double curF = open.top().f;
        open.pop();
        // stale queue entry?
        auto gIt = gScore.find(cur);
        if (gIt == gScore.end() || curF > gIt->second + heuristic(cur) + 1.0) continue;

        if (++res.expansions > req.maxExpansions) {
            res.reason = RouteFail::IterationLimit;
            return res;
        }
        if (cur == goalKey) { found = true; break; }

        const double gCur = gIt->second;

        auto relax = [&](const Key& nb, double stepCost) {
            const double tentative = gCur + stepCost;
            auto it = gScore.find(nb);
            if (it == gScore.end() || tentative < it->second) {
                gScore[nb] = tentative;
                cameFrom[nb] = cur;
                open.push({nb, tentative + heuristic(nb)});
            }
        };

        // planar moves
        for (Vec2 d : dirs) {
            const Vec2 nb{cur.x + d.x, cur.y + d.y};
            if (isSegmentBlocked({cur.x, cur.y}, nb, cur.layer, req)) continue;
            double directionCost = 1.0;
            if (const LayerPolicy* policy = board_.layerPolicy(cur.layer)) {
                const bool horizontal = d.y == 0, vertical = d.x == 0;
                if ((policy->preferredDirection == PreferredDirection::Horizontal && !horizontal)
                    || (policy->preferredDirection == PreferredDirection::Vertical && !vertical))
                    directionCost = 1.15;
            }
            relax({nb.x, nb.y, cur.layer}, d.length() * directionCost);
        }

        // layer changes (vias)
        if (req.allowVias && viaBudgetAvailable && nLayers > 1) {
            const Vec2 here{cur.x, cur.y};
            // through via: any layer, blocks the full stack
            if (board_.viaTypeAllowed(req.netId, ViaType::Through)
                && viaFits(here, throughSpan, ViaType::Through, req)) {
                for (int l = 0; l < nLayers; ++l) {
                    if (l == cur.layer || !board_.layerRoutingAllowed(req.netId, l)) continue;
                    relax({cur.x, cur.y, l}, viaCost);
                }
            }
            // microvia: adjacent span only (HDI)
            if (req.allowMicrovia && board_.viaTypeAllowed(req.netId, ViaType::Microvia)) {
                for (int dl : {-1, +1}) {
                    const int l = cur.layer + dl;
                    if (l < 0 || l >= nLayers) continue;
                    if (!board_.layerRoutingAllowed(req.netId, l)) continue;
                    if (viaFits(here, layerSpanMask(cur.layer, l), ViaType::Microvia, req))
                        relax({cur.x, cur.y, l}, viaCost * 0.6);   // microvias are cheap & small
                }
            }
        }
    }

    if (!found) { res.reason = RouteFail::NoPath; return res; }

    // Reconstruct
    std::vector<Key> raw;
    for (Key k = goalKey;; k = cameFrom[k]) {
        raw.push_back(k);
        if (k == startKey) break;
    }
    std::reverse(raw.begin(), raw.end());

    // Simplify: merge collinear same-layer runs (exact integer test); keep both
    // endpoints of every layer change so vias are explicit in the polyline.
    std::vector<RoutePoint> simp;
    for (const Key& k : raw) {
        const RoutePoint rp{{k.x, k.y}, k.layer};
        while (simp.size() >= 2) {
            const RoutePoint& a = simp[simp.size() - 2];
            const RoutePoint& b = simp.back();
            if (a.layer == b.layer && b.layer == rp.layer &&
                sameDirection(a.p, b.p, rp.p))
                simp.pop_back();
            else break;
        }
        simp.push_back(rp);
    }

    // Derive the via list from layer transitions.
    for (std::size_t i = 1; i < simp.size(); ++i) {
        if (simp[i].layer != simp[i - 1].layer && simp[i].p == simp[i - 1].p) {
            int lo = std::min(simp[i].layer, simp[i - 1].layer);
            int hi = std::max(simp[i].layer, simp[i - 1].layer);
            // through vias span the whole stack unless this is a microvia hop
            if (hi - lo > 1 || !req.allowMicrovia) { lo = 0; hi = nLayers - 1; }
            const ViaType type = (hi - lo == 1 && req.allowMicrovia)
                ? ViaType::Microvia : ViaType::Through;
            res.vias.push_back({simp[i].p, lo, hi, type});
        }
    }

    // The obstacle index contains prior board holes, but not vias introduced by
    // this route. Check those pairwise before returning the route.
    for (std::size_t i = 0; i < res.vias.size(); ++i) {
        for (std::size_t j = i + 1; j < res.vias.size(); ++j) {
            const double required = double(req.viaDrill + req.minDrillToDrill);
            if ((res.vias[i].pos - res.vias[j].pos).length() < required) {
                res.reason = RouteFail::NoPath;
                res.vias.clear();
                return res;
            }
        }
    }

    res.success = true;
    res.reason = RouteFail::None;
    res.path = std::move(simp);

    // Splice the exact requested endpoints back in: pads rarely sit on the
    // routing grid, and a trace that stops half a grid-step short of the pad
    // centre is electrically unconnected. The stub is at most g/2 long and
    // terminates inside the same-net pad.
    if (!res.path.empty()) {
        if (!(res.path.front().p == req.start)) {
            if (isSegmentBlocked(req.start, res.path.front().p, req.startLayer, req)) {
                res.success = false; res.reason = RouteFail::StartBlocked;
                res.path.clear(); res.vias.clear(); return res;
            }
            res.path.insert(res.path.begin(), {req.start, req.startLayer});
        }
        if (!(res.path.back().p == req.goal)) {
            if (isSegmentBlocked(res.path.back().p, req.goal, req.goalLayer, req)) {
                res.success = false; res.reason = RouteFail::GoalBlocked;
                res.path.clear(); res.vias.clear(); return res;
            }
            res.path.push_back({req.goal, req.goalLayer});
        }
    }
    return res;
}

} // namespace dc
