#pragma once
// Multi-layer auto-router: A* over (x, y, copper layer) on a uniform grid.
// Layer changes insert vias — through vias always, adjacent-layer microvias
// when the net class allows them (HDI smartphone-style stackups). Failures
// are reported with an explicit reason, never silently.

#include "obstacle_index.h"
#include "pcb_model.h"

namespace dc {

enum class RouteFail : std::int32_t {
    None           = 0,
    NoPath         = 1,   // search space exhausted
    IterationLimit = 2,   // expansion cap hit — board too dense for this grid
    StartBlocked   = 3,   // start point inside another net's keepout
    GoalBlocked    = 4,
    BadRequest     = 5,   // invalid layers / degenerate geometry
};

const char* routeFailText(RouteFail f);

struct RouteRequest {
    int netId = -1;
    Vec2 start, goal;
    int startLayer = 0, goalLayer = 0;
    coord_t traceWidth = 250'000;
    coord_t clearance  = 200'000;
    // Copper setback from both the outer board edge and every internal
    // cutout.  This is independent of copper-to-copper clearance.
    coord_t edgeClearance = 250'000;
    coord_t gridStep   = 100'000;        // 0.1 mm routing grid
    // via insertion
    bool allowVias = true;
    bool allowMicrovia = false;          // adjacent-layer spans (HDI)
    coord_t viaDiameter = 600'000;
    coord_t viaDrill    = 300'000;
    coord_t minDrillToDrill = 250'000;   // minimum finished wall between holes
    double viaCostMm = 3.0;              // detour a via is "worth", in mm
    std::size_t maxExpansions = 4'000'000;
};

struct RoutePoint { Vec2 p; int layer; };

struct RouteVia {
    Vec2 pos;
    int fromLayer, toLayer;
    ViaType type = ViaType::Through;
};

struct RouteResult {
    bool success = false;
    RouteFail reason = RouteFail::NoPath;
    std::size_t expansions = 0;
    std::vector<RoutePoint> path;        // polyline; layer changes = vias
    std::vector<RouteVia> vias;          // explicit via list derived from path
};

class Router {
public:
    explicit Router(const Board& board) : board_(board), index_(board) {}
    RouteResult route(const RouteRequest& req) const;

private:
    const Board& board_;
    ObstacleIndex index_;

    bool isBlocked(Vec2 p, int layer, const RouteRequest& req) const;
    bool isSegmentBlocked(Vec2 a, Vec2 b, int layer, const RouteRequest& req) const;
    bool viaFits(Vec2 p, layermask_t span, ViaType type, const RouteRequest& req) const;
};

} // namespace dc
