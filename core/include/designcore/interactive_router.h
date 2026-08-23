#pragma once

// Transactional, cursor-driven routing. A session owns private board copies;
// updateCursor() never mutates the caller's board and every update starts from
// the last explicitly accepted waypoint. That invariant makes cancellation,
// rollback, deterministic replay, and shove springback inexpensive and exact.

#include "drc.h"
#include "router.h"
#include <string>
#include <vector>

namespace dc {

enum class InteractiveRouteMode : std::int32_t { Walkaround = 0, Shove = 1 };

enum class InteractiveRouteFail : std::int32_t {
    None = 0,
    Inactive,
    BadRequest,
    NoPath,
    BlockedByFixedItem,
    ShoveDepthLimit,
    OnlineDrc,
};

const char* interactiveRouteFailText(InteractiveRouteFail failure);

struct InteractiveRouteRequest {
    int netId = -1;
    Vec2 start;
    int startLayer = 0;
    coord_t traceWidth = 250'000;
    coord_t clearance = 200'000;
    coord_t gridStep = 100'000;
    coord_t viaDiameter = 600'000;
    coord_t viaDrill = 300'000;
    coord_t minDrillToDrill = 250'000;
    bool allowVias = true;
    bool allowMicrovia = false;
    InteractiveRouteMode mode = InteractiveRouteMode::Walkaround;
    std::size_t maxShoveDepth = 8;
    std::size_t maxExpansions = 500'000;
};

enum class InteractiveItemKind : std::int32_t { Trace = 0, Via = 1 };

struct InteractiveMovedItem {
    id_t id = 0;
    InteractiveItemKind kind = InteractiveItemKind::Trace;
    Vec2 oldA, oldB;
    Vec2 newA, newB;
};

struct InteractiveRoutePreview {
    bool accepted = false;
    InteractiveRouteFail failure = InteractiveRouteFail::Inactive;
    std::string message;
    std::vector<TraceSegment> addedTraces;
    std::vector<Via> addedVias;
    std::vector<InteractiveMovedItem> movedItems;
    std::vector<DrcViolation> findings;
};

class InteractiveRouterSession {
public:
    explicit InteractiveRouterSession(Board& target);

    const InteractiveRoutePreview& begin(const InteractiveRouteRequest& request);
    const InteractiveRoutePreview& updateCursor(Vec2 cursor);
    const InteractiveRoutePreview& placeVia(int targetLayer);
    bool commit();
    void cancel();

    bool active() const { return active_; }
    const InteractiveRoutePreview& preview() const { return preview_; }
    const Board& previewBoard() const { return previewBoard_; }

private:
    Board& target_;
    Board original_;
    Board staged_;
    Board previewBoard_;
    InteractiveRouteRequest request_;
    InteractiveRoutePreview preview_;
    Vec2 anchor_;
    Vec2 cursor_;
    int layer_ = 0;
    bool active_ = false;
    bool hasAcceptedPreview_ = false;

    RouteRequest legRequest(Vec2 goal, int goalLayer) const;
    bool buildWalkaround(Board& candidate, Vec2 cursor);
    bool buildShove(Board& candidate, Vec2 cursor);
    bool validatePreview(const Board& before, Board& candidate);
    void describeChanges(const Board& before, const Board& after);
    void reject(InteractiveRouteFail failure, std::string message);
};

} // namespace dc
