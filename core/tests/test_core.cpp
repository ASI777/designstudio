// Core engine test suite (no framework dependency).
// Covers: model identity, multi-layer routing with via insertion, route
// failure reporting, the full DRC rule set, copper pours, connectivity,
// length matching and scalability smoke.
#include "designcore/c_api.h"
#include "designcore/drc.h"
#include "designcore/interactive_router.h"
#include "designcore/linalg.h"
#include "designcore/obstacle_index.h"
#include "designcore/pcb_model.h"
#include "designcore/physics.h"
#include "designcore/pour.h"
#include "designcore/router.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

using namespace dc;

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond, msg)                                                     \
    do {                                                                     \
        ++g_checks;                                                          \
        if (!(cond)) {                                                       \
            std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, msg);        \
            ++g_failures;                                                    \
        }                                                                    \
    } while (0)

static bool hasRule(const std::vector<DrcViolation>& vs, DrcRule r) {
    return std::any_of(vs.begin(), vs.end(), [r](const DrcViolation& v) { return v.rule == r; });
}

static int countRule(const std::vector<DrcViolation>& vs, DrcRule r) {
    return int(std::count_if(vs.begin(), vs.end(), [r](const DrcViolation& v) { return v.rule == r; }));
}

static Board makeBoard(int layers = 2, coord_t wMm = 50, coord_t hMm = 50) {
    Board b;
    b.outline = {{0, 0}, {wMm * NM_PER_MM, hMm * NM_PER_MM}};
    b.copperLayerCount = layers;
    return b;
}

struct MmPoint { double x, y; };

static Footprint onePadPart(const char* refDes, MmPoint posMm, MmPoint sizeMm, int netId,
                            bool th = false, coord_t drillNm = 0) {
    Footprint fp;
    fp.refDes = refDes;
    fp.pos = {coord_t(posMm.x * NM_PER_MM), coord_t(posMm.y * NM_PER_MM)};
    Pad p;
    p.size = {coord_t(sizeMm.x * NM_PER_MM), coord_t(sizeMm.y * NM_PER_MM)};
    p.netId = netId;
    p.throughHole = th;
    p.drill = drillNm;
    p.name = "1";
    fp.pads.push_back(p);
    return fp;
}

// ---------------------------------------------------------------- model ----

static void testNetIdentityStability() {
    Board b = makeBoard();
    b.setNet(0, "GND", 0);
    b.setNet(1, "VCC", 0);
    b.setNet(2, "SIG", 0);
    b.removeNet(1);
    CHECK(b.findNet(0) && b.findNet(0)->name == "GND", "net 0 survives removal of net 1");
    CHECK(b.findNet(2) && b.findNet(2)->name == "SIG", "net 2 keeps its id after net 1 removed");
    CHECK(b.findNet(1) == nullptr, "net 1 gone");

    // copper that referenced the removed net is detached, not dangling
    b.setNet(1, "VCC2", 0);
    b.addTrace({0, {0, 0}, {NM_PER_MM, 0}, 250'000, 0, 1});
    b.removeNet(1);
    CHECK(b.traces()[0].netId == -1, "trace netId detached on net removal");
}

static void testItemRemovalIndex() {
    Board b = makeBoard();
    dc::id_t t1 = b.addTrace({0, {0, 0}, {NM_PER_MM, 0}, 250'000, 0, -1});
    dc::id_t t2 = b.addTrace({0, {0, NM_PER_MM}, {NM_PER_MM, NM_PER_MM}, 250'000, 0, -1});
    dc::id_t t3 = b.addTrace({0, {0, 2 * NM_PER_MM}, {NM_PER_MM, 2 * NM_PER_MM}, 250'000, 0, -1});
    CHECK(b.removeItem(t1), "remove first trace");
    CHECK(!b.removeItem(t1), "double remove fails cleanly");
    CHECK(b.removeItem(t3), "remove last trace after swap-erase");
    CHECK(b.traces().size() == 1 && b.traces()[0].id == t2, "remaining trace is t2");

    dc::id_t f = b.addFootprint(onePadPart("U1", {10, 10}, {1, 1}, -1));
    CHECK(b.findFootprint(f) != nullptr, "footprint findable");
    CHECK(b.removeItem(f) && b.findFootprint(f) == nullptr, "footprint removed from index");
}

static void testNetClassRules() {
    Board b = makeBoard();
    NetClass hs;
    hs.id = 1; hs.name = "DDR"; hs.clearance = 100'000; hs.traceWidth = 90'000;
    hs.maxSkew = 500'000;
    b.setNetClass(hs);
    b.setNet(0, "DQ0", 1);
    b.setNet(1, "MISC", 0);
    CHECK(b.rulesFor(0).name == "DDR", "net 0 resolves to DDR class");
    CHECK(b.rulesFor(1).name == "Default", "net 1 falls back to Default");
    CHECK(b.rulesFor(999).name == "Default", "unknown net falls back to Default");
}

// ---------------------------------------------------------- geometry/rot ----

static void testRotatedPadGeometry() {
    Board b = makeBoard();
    Footprint rfp;
    rfp.refDes = "U2";
    rfp.pos = {25 * NM_PER_MM, 25 * NM_PER_MM};
    rfp.rotationDeg = 90;
    rfp.pads.push_back({0, {0, 0}, {10 * NM_PER_MM, 500'000}, false, 0, 0, "1"});
    dc::id_t rid = b.addFootprint(std::move(rfp));
    const Footprint* rf = b.findFootprint(rid);
    Rect bb = rf->boundingBox();
    CHECK(bb.max.y - bb.min.y > bb.max.x - bb.min.x, "bbox rotates with the pad");

    Quad q = rf->padWorldQuad(rf->pads[0]);
    CHECK(pointInQuad({25 * NM_PER_MM, 22 * NM_PER_MM}, q), "rotated pad covers vertical extent");
    CHECK(!pointInQuad({28 * NM_PER_MM, 25 * NM_PER_MM}, q), "rotated pad clears horizontal extent");
}

static void testBottomFootprintMirrorsLocalY() {
    Footprint top;
    top.refDes = "TOP";
    top.pos = {25 * NM_PER_MM, 25 * NM_PER_MM};
    top.rotationDeg = 90;
    top.pads.push_back({0, {2 * NM_PER_MM, NM_PER_MM},
                        {500'000, 500'000}, false, 0, 0, "1"});
    Footprint bottom = top;
    bottom.refDes = "BOTTOM";
    bottom.side = 1;
    const Vec2 topPosition = top.padWorldPos(top.pads[0]);
    const Vec2 bottomPosition = bottom.padWorldPos(bottom.pads[0]);
    CHECK(topPosition.x == 24 * NM_PER_MM && topPosition.y == 27 * NM_PER_MM,
          "top pad uses positive counter-clockwise instance rotation");
    CHECK(bottomPosition.x == 26 * NM_PER_MM && bottomPosition.y == 27 * NM_PER_MM,
          "bottom pad mirrors local Y before the same instance rotation");
}

static void testShapeAccuratePadClearanceAndConnectivity() {
    Board b = makeBoard();
    NetClass fine;
    fine.id = 0; fine.name = "Fine"; fine.clearance = 100'000;
    b.setNetClass(fine);
    b.setNet(0, "ROUND", 0);
    b.setNet(1, "TRACE", 0);
    Footprint fp = onePadPart("P1", {10, 10}, {2, 2}, 0);
    fp.pads[0].shape = PadShape::Circle;
    b.addFootprint(std::move(fp));
    // This trace lies inside the old 2x2 rectangular corner but outside the
    // actual radius-1 circle by ~0.13 mm; with 0.1 mm clearance it must pass.
    b.addTrace({0, {coord_t(10.8 * NM_PER_MM), coord_t(10.8 * NM_PER_MM)},
                   {coord_t(11.8 * NM_PER_MM), coord_t(11.8 * NM_PER_MM)},
                   50'000, 0, 1});
    DrcOptions opt;
    opt.defaultClearance = 100'000;
    opt.minCopperToEdge = 0; opt.minCopperToHole = 0;
    opt.checkConnectivity = false; opt.checkUnassignedCopper = false;
    const auto vs = runDrc(b, opt);
    CHECK(!hasRule(vs, DrcRule::PadTraceClearance),
          "circle pad clearance uses the circle, not its rectangular bounding box");

    Board connected = makeBoard();
    connected.setNet(0, "N", 0);
    Footprint oval = onePadPart("P2", {20, 20}, {4, 1}, 0);
    oval.pads[0].shape = PadShape::Oval;
    connected.addFootprint(std::move(oval));
    connected.addTrace({0, {22 * NM_PER_MM, 20 * NM_PER_MM},
                            {30 * NM_PER_MM, 20 * NM_PER_MM}, 100'000, 0, 0});
    CHECK(netIslandCount(connected, 0) == 1,
          "connectivity uses full oval copper extent rather than pad centre distance");
}

static void testCustomCopperRegionGeometry() {
    Board b = makeBoard();
    b.setNet(0, "REGION", 0);
    b.setNet(1, "TRACE", 0);
    Footprint fp;
    fp.refDes = "J1"; fp.pos = {10 * NM_PER_MM, 10 * NM_PER_MM};
    CopperRegion region;
    region.netId = 0;
    region.points = {{0, 0}, {4 * NM_PER_MM, 0}, {0, 4 * NM_PER_MM}};
    fp.regions.push_back(region);
    b.addFootprint(std::move(fp));
    // Inside the triangle's AABB but outside its actual diagonal edge.
    b.addTrace({0, {coord_t(13.5 * NM_PER_MM), coord_t(13.5 * NM_PER_MM)},
                   {coord_t(14.5 * NM_PER_MM), coord_t(14.5 * NM_PER_MM)},
                   100'000, 0, 1});
    DrcOptions opt;
    opt.defaultClearance = 200'000; opt.checkConnectivity = false;
    opt.minCopperToEdge = 0; opt.minCopperToHole = 0; opt.checkUnassignedCopper = false;
    const auto vs = runDrc(b, opt);
    CHECK(!hasRule(vs, DrcRule::PadTraceClearance),
          "custom copper clearance uses polygon boundary rather than its AABB");

    b.addTrace({0, {12 * NM_PER_MM, 11 * NM_PER_MM},
                   {20 * NM_PER_MM, 11 * NM_PER_MM}, 100'000, 0, 0});
    CHECK(netIslandCount(b, 0) == 1, "trace connects to custom polygon copper");
}

// ---------------------------------------------------------------- router ----

static void testRouteAroundObstacle() {
    Board b = makeBoard();
    b.setNet(0, "GND", 0);
    b.setNet(1, "SIG", 0);
    Footprint fp = onePadPart("U1", {25, 25}, {5, 5}, 0);
    b.addFootprint(std::move(fp));

    Router router(b);
    RouteRequest req;
    req.netId = 1;
    req.start = {5 * NM_PER_MM, 25 * NM_PER_MM};
    req.goal = {45 * NM_PER_MM, 25 * NM_PER_MM};
    req.gridStep = 500'000;
    req.allowVias = false;
    auto res = router.route(req);
    CHECK(res.success, "router finds a path around the pad");
    CHECK(res.path.size() >= 2, "path has at least two points");
    // path must detour: some point off the straight line
    bool detours = std::any_of(res.path.begin(), res.path.end(),
        [](const RoutePoint& p) { return std::abs(p.p.y - 25 * NM_PER_MM) > 27 * NM_PER_MM / 10; });
    CHECK(detours, "path detours around the obstacle");
}

static void testRouteFailureReasons() {
    Board b = makeBoard();
    b.setNet(0, "WALL", 0);
    b.setNet(1, "SIG", 0);
    // wall of copper across the whole board on layer 0
    b.addTrace({0, {0, 25 * NM_PER_MM}, {50 * NM_PER_MM, 25 * NM_PER_MM}, 2 * NM_PER_MM, 0, 0});

    Router router(b);
    RouteRequest req;
    req.netId = 1;
    req.start = {5 * NM_PER_MM, 5 * NM_PER_MM};
    req.goal = {5 * NM_PER_MM, 45 * NM_PER_MM};
    req.gridStep = 500'000;
    req.allowVias = false;
    auto res = router.route(req);
    CHECK(!res.success && res.reason == RouteFail::NoPath, "blocked board reports NoPath");

    // start inside the wall keepout
    req.start = {25 * NM_PER_MM, 25 * NM_PER_MM};
    res = router.route(req);
    CHECK(!res.success && res.reason == RouteFail::StartBlocked, "reports StartBlocked");

    // iteration limit reported, not silent
    req.start = {5 * NM_PER_MM, 5 * NM_PER_MM};
    req.goal = {5 * NM_PER_MM, 45 * NM_PER_MM};
    req.maxExpansions = 10;
    res = router.route(req);
    CHECK(!res.success && res.reason == RouteFail::IterationLimit, "reports IterationLimit");

    // bad layer
    RouteRequest bad = req;
    bad.maxExpansions = 1000;
    bad.goalLayer = 7;   // board has 2 layers
    res = router.route(bad);
    CHECK(!res.success && res.reason == RouteFail::BadRequest, "reports BadRequest for bad layer");
}

static void testMultiLayerRouteWithVia() {
    Board b = makeBoard(2);
    b.setNet(0, "WALL", 0);
    b.setNet(1, "SIG", 0);
    // wall on layer 0 only — router must drop to layer 1 and come back
    b.addTrace({0, {25 * NM_PER_MM, 0}, {25 * NM_PER_MM, 50 * NM_PER_MM}, 2 * NM_PER_MM, 0, 0});

    Router router(b);
    RouteRequest req;
    req.netId = 1;
    req.start = {5 * NM_PER_MM, 25 * NM_PER_MM};
    req.startLayer = 0;
    req.goal = {45 * NM_PER_MM, 25 * NM_PER_MM};
    req.goalLayer = 0;
    req.gridStep = 500'000;
    req.allowVias = true;
    auto res = router.route(req);
    CHECK(res.success, "multi-layer route succeeds through layer 1");
    CHECK(res.vias.size() >= 2, "at least two vias (down and back up)");
    bool usesLayer1 = std::any_of(res.path.begin(), res.path.end(),
        [](const RoutePoint& p) { return p.layer == 1; });
    CHECK(usesLayer1, "path actually uses layer 1");
    // via positions must clear the wall by via clearance
    for (const auto& v : res.vias) {
        double d = std::abs(double(v.pos.x) - 25 * NM_PER_MM) - 1 * NM_PER_MM; // wall halfwidth
        CHECK(d + 1e-9 >= double(req.clearance), "via keeps clearance from the wall");
    }
}

static void testMicroviaRouting() {
    Board b = makeBoard(4);
    b.setNet(1, "SIG", 0);
    Router router(b);
    RouteRequest req;
    req.netId = 1;
    req.start = {5 * NM_PER_MM, 5 * NM_PER_MM};
    req.startLayer = 0;
    req.goal = {45 * NM_PER_MM, 45 * NM_PER_MM};
    req.goalLayer = 1;                  // end one layer down
    req.gridStep = 500'000;
    req.allowMicrovia = true;
    auto res = router.route(req);
    CHECK(res.success, "microvia route succeeds");
    CHECK(!res.vias.empty(), "layer change produced a via");
    if (!res.vias.empty()) {
        const auto& v = res.vias.front();
        CHECK(v.toLayer - v.fromLayer == 1, "microvia spans adjacent layers only");
    }
}

static void testRouterKeepsDrillWallOnSameNet() {
    Board b = makeBoard(2);
    b.setNet(1, "SIG", 0);
    const Vec2 existing{10 * NM_PER_MM, 10 * NM_PER_MM};
    b.addVia({0, existing, 600'000, 300'000, 1, 0, 1, ViaType::Through});

    Router router(b);
    RouteRequest req;
    req.netId = 1;
    req.start = {10 * NM_PER_MM + 700'000, 10 * NM_PER_MM};
    req.goal = req.start;
    req.startLayer = 0;
    req.goalLayer = 1;
    req.gridStep = 100'000;
    req.viaDiameter = 600'000;
    req.viaDrill = 300'000;
    req.minDrillToDrill = 500'000;
    auto res = router.route(req);
    CHECK(res.success, "router detours a layer transition away from a same-net drill");
    const double required = double(req.viaDrill) + double(req.minDrillToDrill);
    for (const auto& via : res.vias)
        CHECK((via.pos - existing).length() + 1e-9 >= required,
              "generated via satisfies same-net drill-to-drill wall");
}

static void testRouterRotationAwareness() {
    // A long thin pad rotated 90°: the router must not cross where the pad
    // actually is (vertical), but may cross where it would have been (horizontal).
    Board b = makeBoard();
    b.setNet(0, "A", 0);
    b.setNet(1, "SIG", 0);
    Footprint fp;
    fp.refDes = "U1";
    fp.pos = {25 * NM_PER_MM, 25 * NM_PER_MM};
    fp.rotationDeg = 90;
    fp.pads.push_back({0, {0, 0}, {30 * NM_PER_MM, 500'000}, false, 0, 0, "1"});  // 30 mm wide → 30 mm tall
    b.addFootprint(std::move(fp));

    Router router(b);
    RouteRequest req;
    req.netId = 1;
    req.gridStep = 500'000;
    req.allowVias = false;
    // horizontal corridor at y=25 is blocked by the rotated pad; vertical at x=25±>0.25+clr is open
    req.start = {20 * NM_PER_MM, 25 * NM_PER_MM};
    req.goal = {30 * NM_PER_MM, 25 * NM_PER_MM};
    auto res = router.route(req);
    CHECK(res.success, "route around rotated pad succeeds");
    bool crossesPad = false;
    for (const auto& p : res.path)
        if (std::abs(p.p.x - 25 * NM_PER_MM) < 250'000 + 200'000 &&
            std::abs(p.p.y - 25 * NM_PER_MM) < 15 * NM_PER_MM)
            crossesPad = true;
    CHECK(!crossesPad, "path avoids the rotated pad copper");
}

static InteractiveRouteRequest interactiveRequest(InteractiveRouteMode mode) {
    InteractiveRouteRequest request;
    request.netId = 0;
    request.start = {5 * NM_PER_MM, 10 * NM_PER_MM};
    request.startLayer = 0;
    request.traceWidth = 250'000;
    request.clearance = 200'000;
    request.gridStep = 100'000;
    request.mode = mode;
    return request;
}

static void testInteractiveRouteCommitCancelAndSpringback() {
    Board board = makeBoard();
    board.setNet(0, "HEAD", 0);
    board.setNet(1, "BLOCKER", 0);
    const dc::id_t blocker = board.addTrace({0, {15 * NM_PER_MM, 5 * NM_PER_MM},
        {15 * NM_PER_MM, 15 * NM_PER_MM}, 250'000, 0, 1});
    const TraceSegment original = *board.findTrace(blocker);

    InteractiveRouterSession session(board);
    CHECK(session.begin(interactiveRequest(InteractiveRouteMode::Shove)).accepted,
          "interactive shove session begins");
    const auto& collision = session.updateCursor({25 * NM_PER_MM, 10 * NM_PER_MM});
    CHECK(collision.accepted && collision.addedTraces.size() == 1,
          "shove preview contains one routed head");
    CHECK(collision.movedItems.size() == 1 && collision.movedItems[0].id == blocker,
          "foreign trace is moved while retaining its immutable id");
    CHECK(board.traces().size() == 1 && board.findTrace(blocker)->a == original.a,
          "preview does not mutate caller board");

    const auto& springback = session.updateCursor({10 * NM_PER_MM, 10 * NM_PER_MM});
    CHECK(springback.accepted && springback.movedItems.empty(),
          "moving cursor away springs blocker back to staged geometry");
    CHECK(session.previewBoard().findTrace(blocker)->a == original.a,
          "springback restores exact blocker coordinates");
    session.cancel();
    CHECK(board.traces().size() == 1 && board.findTrace(blocker)->a == original.a,
          "cancel discards all route and shove geometry");

    CHECK(session.begin(interactiveRequest(InteractiveRouteMode::Shove)).accepted,
          "session can restart after cancel");
    CHECK(session.updateCursor({25 * NM_PER_MM, 10 * NM_PER_MM}).accepted,
          "replayed cursor position is accepted");
    CHECK(session.commit(), "commit applies latest legal preview");
    CHECK(board.traces().size() == 2 && board.findTrace(blocker) != nullptr,
          "commit adds head and preserves blocker identity");
    CHECK(board.findTrace(blocker)->a != original.a,
          "committed blocker uses previewed displaced geometry");
}

static void testInteractiveRouteRollbackAndRecursiveShove() {
    Board fixed = makeBoard();
    fixed.setNet(0, "HEAD", 0);
    fixed.setNet(1, "PAD", 0);
    fixed.addFootprint(onePadPart("U1", {15, 10}, {2, 2}, 1));
    InteractiveRouterSession blocked(fixed);
    CHECK(blocked.begin(interactiveRequest(InteractiveRouteMode::Shove)).accepted,
          "fixed-blocker session begins");
    const auto& detour = blocked.updateCursor({25 * NM_PER_MM, 10 * NM_PER_MM});
    CHECK(detour.accepted && detour.addedTraces.size() > 1,
          "shove mode walks around fixed pad copper when displacement is illegal");
    CHECK(fixed.traces().empty(), "walkaround preview still leaves caller board unchanged");
    const auto& rejected = blocked.updateCursor({15 * NM_PER_MM, 10 * NM_PER_MM});
    CHECK(!rejected.accepted && rejected.failure == InteractiveRouteFail::NoPath,
          "goal inside fixed copper returns an explicit failure");
    CHECK(fixed.traces().empty(), "rejected fixed-copper preview rolls back all geometry");

    Board chain = makeBoard();
    chain.setNet(0, "HEAD", 0);
    chain.setNet(1, "CHAIN_A", 0);
    chain.setNet(2, "CHAIN_B", 0);
    const dc::id_t first = chain.addTrace({0, {500'000, 12 * NM_PER_MM},
        {4 * NM_PER_MM, 12 * NM_PER_MM}, 250'000, 0, 1});
    const dc::id_t second = chain.addTrace({0, {4'500'000, 12 * NM_PER_MM},
        {8 * NM_PER_MM, 12 * NM_PER_MM}, 250'000, 0, 2});
    InteractiveRouteRequest request = interactiveRequest(InteractiveRouteMode::Shove);
    request.start = {2 * NM_PER_MM, 5 * NM_PER_MM};
    InteractiveRouterSession recursive(chain);
    CHECK(recursive.begin(request).accepted, "recursive shove session begins");
    const auto& preview = recursive.updateCursor({2 * NM_PER_MM, 25 * NM_PER_MM});
    CHECK(preview.accepted, "bounded recursive shove resolves a two-trace chain");
    const bool movedFirst = std::any_of(preview.movedItems.begin(), preview.movedItems.end(),
        [first](const InteractiveMovedItem& item) { return item.id == first; });
    const bool movedSecond = std::any_of(preview.movedItems.begin(), preview.movedItems.end(),
        [second](const InteractiveMovedItem& item) { return item.id == second; });
    CHECK(movedFirst && movedSecond, "recursive shove reports every displaced item");
}

static void testInteractiveRouteCapiCompatibility() {
    DcBoardHandle board = dc_board_create();
    CHECK(board != nullptr, "C API board allocated for interactive route");
    CHECK(dc_board_set_outline(board, 0, 0, 50 * NM_PER_MM, 50 * NM_PER_MM) == DC_OK,
          "C API interactive board outline set");
    CHECK(dc_net_set(board, 0, "SIG", 0) == DC_OK, "C API interactive net set");
    DcInteractiveRouteRequest request{};
    request.abiVersion = 1;
    request.structSize = sizeof(request);
    request.netId = 0;
    request.startLayer = 0;
    request.startX = 5 * NM_PER_MM;
    request.startY = 5 * NM_PER_MM;
    request.traceWidth = 250'000;
    request.clearance = 200'000;
    request.gridStep = 100'000;
    request.viaDiameter = 600'000;
    request.viaDrill = 300'000;
    request.minDrillToDrill = 250'000;
    request.allowVias = 1;
    request.mode = 0;
    request.maxShoveDepth = 8;
    request.maxExpansions = 100'000;
    CHECK(dc_interactive_route_begin(board, &request) == DC_OK,
          "versioned additive C API begins route");
    CHECK(dc_interactive_route_update(board, 20 * NM_PER_MM, 5 * NM_PER_MM) == DC_OK,
          "C API produces legal cursor preview");
    DcInteractivePreviewInfo info{};
    CHECK(dc_interactive_route_preview_info(board, &info) == DC_OK
              && info.accepted && info.traceCount > 0 && info.findingCount == 0,
          "C API exposes preview counts and online DRC state");
    std::vector<DcInteractiveTrace> traces(std::size_t(info.traceCount));
    CHECK(dc_interactive_route_get_traces(board, traces.data(), info.traceCount)
              == info.traceCount,
          "C API fetches preview geometry without truncation");
    CHECK(dc_interactive_route_place_via(board, 1) == DC_OK,
          "C API stages a legal layer-change via at the accepted cursor");
    CHECK(dc_interactive_route_preview_info(board, &info) == DC_OK
              && info.accepted && info.viaCount == 1,
          "via preview reports its staged geometry");
    CHECK(dc_interactive_route_cancel(board) == DC_OK && dc_net_length(board, 0) == 0,
          "C API cancel leaves authoritative board unchanged");
    CHECK(dc_interactive_route_begin(board, &request) == DC_OK
              && dc_interactive_route_update(board, 20 * NM_PER_MM, 5 * NM_PER_MM) == DC_OK
              && dc_interactive_route_commit(board) == DC_OK
              && dc_net_length(board, 0) > 0,
          "C API commit applies the preview transaction");
    dc_board_destroy(board);
}

static void testInteractiveRoutePreviewLatency() {
    Board board = makeBoard(2);
    board.setNet(0, "HEAD", 0);
    board.setNet(1, "BACKGROUND", 0);
    for (int row = 0; row < 8; ++row)
        for (int col = 0; col < 8; ++col) {
            const coord_t x = (2 + col * 6) * NM_PER_MM;
            const coord_t y = (2 + row * 6) * NM_PER_MM;
            board.addTrace({0, {x, y}, {x + NM_PER_MM, y}, 250'000, 1, 1});
        }
    InteractiveRouteRequest request = interactiveRequest(InteractiveRouteMode::Shove);
    request.start = {5 * NM_PER_MM, 10 * NM_PER_MM};
    InteractiveRouterSession session(board);
    CHECK(session.begin(request).accepted, "latency routing session begins");
    std::vector<double> milliseconds;
    milliseconds.reserve(120);
    for (int index = 0; index < 120; ++index) {
        const Vec2 cursor{(10 + index % 31) * NM_PER_MM,
                          (10 + (index / 31) % 3) * NM_PER_MM};
        const auto started = std::chrono::steady_clock::now();
        const bool accepted = session.updateCursor(cursor).accepted;
        const auto stopped = std::chrono::steady_clock::now();
        CHECK(accepted, "latency corpus cursor remains legal");
        milliseconds.push_back(std::chrono::duration<double, std::milli>(stopped - started).count());
    }
    std::sort(milliseconds.begin(), milliseconds.end());
    const double p95 = milliseconds[std::size_t(milliseconds.size() * 95 / 100)];
    std::printf("  interactive route update p95: %.3f ms (64 background items)\n", p95);
    CHECK(p95 < 16.7, "interactive cursor update meets 16.7 ms p95 baseline");
}

// ------------------------------------------------------------------- DRC ----

static void testDrcClearanceFamilies() {
    Board b = makeBoard();
    b.setNet(0, "A", 0);
    b.setNet(1, "B", 0);

    // trace-trace
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM + 100'000}, {20 * NM_PER_MM, 10 * NM_PER_MM + 100'000}, 250'000, 0, 1});
    // pad-trace
    b.addFootprint(onePadPart("R1", {30, 10}, {1, 1}, 0));
    b.addTrace({0, {28 * NM_PER_MM, 10 * NM_PER_MM + 600'000}, {32 * NM_PER_MM, 10 * NM_PER_MM + 600'000}, 250'000, 0, 1});
    // pad-pad
    b.addFootprint(onePadPart("C1", {10, 30}, {1, 1}, 0));
    b.addFootprint(onePadPart("C2", {11.05, 30}, {1, 1}, 1));
    // via-trace
    b.addVia({0, {30 * NM_PER_MM, 30 * NM_PER_MM}, 600'000, 300'000, 0, 0, 1});
    b.addTrace({0, {28 * NM_PER_MM, 30 * NM_PER_MM + 400'000}, {32 * NM_PER_MM, 30 * NM_PER_MM + 400'000}, 250'000, 0, 1});
    // via-via
    b.addVia({0, {40 * NM_PER_MM, 40 * NM_PER_MM}, 600'000, 300'000, 0, 0, 1});
    b.addVia({0, {40 * NM_PER_MM + 700'000, 40 * NM_PER_MM}, 600'000, 300'000, 1, 0, 1});

    DrcOptions opt;
    opt.checkConnectivity = false;
    auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::TraceTraceClearance), "trace-trace violation found");
    CHECK(hasRule(vs, DrcRule::PadTraceClearance), "pad-trace violation found");
    CHECK(hasRule(vs, DrcRule::PadPadClearance), "pad-pad violation found");
    CHECK(hasRule(vs, DrcRule::ViaTraceClearance), "via-trace violation found");
    CHECK(hasRule(vs, DrcRule::ViaViaClearance), "via-via violation found");

    for (const auto& v : vs) {
        CHECK(!v.message.empty(), "every violation carries a message");
    }
}

static void testDrcLayerAwareness() {
    Board b = makeBoard(2);
    b.setNet(0, "A", 0);
    b.setNet(1, "B", 0);
    // same x/y, different layers — no violation
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 1, 1});
    DrcOptions opt;
    opt.checkConnectivity = false;
    auto vs = runDrc(b, opt);
    CHECK(countRule(vs, DrcRule::TraceTraceClearance) == 0, "different layers don't clash");

    // a through via at the same spot DOES clash with both
    b.addVia({0, {15 * NM_PER_MM, 10 * NM_PER_MM + 100'000}, 600'000, 300'000, -2 /*net id -2 unused*/, 0, 1});
    // use a real net id so the -1/-1 skip doesn't apply
    Board b2 = makeBoard(2);
    b2.setNet(0, "A", 0);
    b2.setNet(1, "B", 0);
    b2.setNet(2, "C", 0);
    b2.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    b2.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 1, 1});
    b2.addVia({0, {15 * NM_PER_MM, 10 * NM_PER_MM + 100'000}, 600'000, 300'000, 2, 0, 1});
    auto vs2 = runDrc(b2, opt);
    CHECK(countRule(vs2, DrcRule::ViaTraceClearance) == 2, "through via clashes on both layers");
}

static void testDrcAnnularAndDrill() {
    Board b = makeBoard();
    b.setNet(0, "A", 0);
    // via with thin ring: (0.4 - 0.3)/2 = 0.05 < 0.1 min
    b.addVia({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, 400'000, 300'000, 0, 0, 1});
    // tiny drill
    b.addVia({0, {20 * NM_PER_MM, 10 * NM_PER_MM}, 600'000, 100'000, 0, 0, 1});
    // TH pad with bad ring: pad 1.0, drill 0.9
    b.addFootprint(onePadPart("J1", {30, 10}, {1, 1}, 0, true, 900'000));
    // two holes too close
    b.addVia({0, {10 * NM_PER_MM, 30 * NM_PER_MM}, 600'000, 300'000, 0, 0, 1});
    b.addVia({0, {10 * NM_PER_MM + 450'000, 30 * NM_PER_MM}, 600'000, 300'000, 0, 0, 1});

    DrcOptions opt;
    opt.checkConnectivity = false;
    auto vs = runDrc(b, opt);
    CHECK(countRule(vs, DrcRule::AnnularRing) >= 2, "annular ring on via and TH pad");
    CHECK(hasRule(vs, DrcRule::DrillSize), "minimum drill enforced");
    CHECK(hasRule(vs, DrcRule::DrillToDrill), "hole-to-hole wall enforced");
}

static void testExplicitViaTechnologyDrillFloors() {
    Board b = makeBoard(4);
    b.setNet(0, "A", 0);
    Via blind;
    blind.pos = {10 * NM_PER_MM, 10 * NM_PER_MM};
    blind.diameter = 500'000; blind.drill = 100'000;
    blind.netId = 0; blind.fromLayer = 0; blind.toLayer = 1;
    blind.type = ViaType::Blind;
    b.addVia(blind);
    Via micro = blind;
    micro.pos = {20 * NM_PER_MM, 10 * NM_PER_MM};
    micro.type = ViaType::Microvia;
    b.addVia(micro);

    DrcOptions opt;
    opt.minDrill = 200'000;
    opt.minMicroviaDrill = 75'000;
    opt.checkConnectivity = false;
    const auto vs = runDrc(b, opt);
    CHECK(countRule(vs, DrcRule::DrillSize) == 1,
          "mechanical blind via fails mechanical floor while explicit microvia uses laser floor");
}

static void testManufacturingDrcExtensions() {
    Board b = makeBoard();
    b.setNet(0, "PTH", 0);
    b.setNet(1, "SIG", 0);
    b.addTrace({0, {100'000, 5 * NM_PER_MM}, {5 * NM_PER_MM, 5 * NM_PER_MM},
                100'000, 0, 1});
    b.addTrace({0, {20 * NM_PER_MM, 20 * NM_PER_MM}, {25 * NM_PER_MM, 20 * NM_PER_MM},
                100'000, 0, -1});
    b.addFootprint(onePadPart("J1", {10, 10}, {0.8, 0.8}, 0, true, 600'000));
    b.addTrace({0, {8 * NM_PER_MM, 10 * NM_PER_MM + 500'000},
                   {12 * NM_PER_MM, 10 * NM_PER_MM + 500'000}, 100'000, 0, 1});

    DrcOptions opt;
    opt.checkConnectivity = false;
    const auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::CopperToEdge), "copper-to-edge setback enforced");
    CHECK(hasRule(vs, DrcRule::CopperToHole), "foreign copper-to-hole clearance enforced");
    CHECK(hasRule(vs, DrcRule::UnassignedCopper), "unassigned routed copper enforced");
}

static void testCourtyardAndBodyClearance() {
    Board b = makeBoard();
    Footprint a = onePadPart("U1", {10, 10}, {0.5, 0.5}, -1);
    a.bodySize = {4 * NM_PER_MM, 4 * NM_PER_MM};
    Footprint c = onePadPart("U2", {14.1, 10}, {0.5, 0.5}, -1);
    c.bodySize = {4 * NM_PER_MM, 4 * NM_PER_MM};
    b.addFootprint(std::move(a)); b.addFootprint(std::move(c));
    DrcOptions opt;
    opt.checkConnectivity = false; opt.checkUnassignedCopper = false;
    opt.minCopperToEdge = 0; opt.minCopperToHole = 0;
    opt.minCourtyardClearance = 250'000;
    const auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::CourtyardOverlap),
          "component body gap below placement profile is reported");

    Board opposite = makeBoard();
    Footprint top = onePadPart("T", {10, 10}, {0.5, 0.5}, -1);
    top.bodySize = {4 * NM_PER_MM, 4 * NM_PER_MM}; top.side = 0;
    Footprint bottom = top; bottom.refDes = "B"; bottom.side = 1;
    opposite.addFootprint(top); opposite.addFootprint(bottom);
    const auto oppositeVs = runDrc(opposite, opt);
    CHECK(!hasRule(oppositeVs, DrcRule::CourtyardOverlap),
          "opposite-side components may overlap in XY when height rules are not requested");

    Board explicitCourtyards = makeBoard();
    Footprint left = onePadPart("C1", {10, 10}, {0.5, 0.5}, -1);
    left.bodySize = {2 * NM_PER_MM, 2 * NM_PER_MM};
    left.courtyard = {{-NM_PER_MM, -NM_PER_MM}, {NM_PER_MM, -NM_PER_MM},
                      {NM_PER_MM, NM_PER_MM}, {-NM_PER_MM, NM_PER_MM}};
    Footprint right = left;
    right.refDes = "C2";
    right.pos = {12 * NM_PER_MM + 100'000, 10 * NM_PER_MM}; // 0.1 mm gap
    explicitCourtyards.addFootprint(std::move(left));
    explicitCourtyards.addFootprint(std::move(right));
    const auto explicitVs = runDrc(explicitCourtyards, opt);
    CHECK(hasRule(explicitVs, DrcRule::CourtyardOverlap),
          "explicit courtyard gap uses the configured manufacturing clearance");
    const auto explicitMessage = std::find_if(explicitVs.begin(), explicitVs.end(),
        [](const DrcViolation& item) { return item.rule == DrcRule::CourtyardOverlap; });
    CHECK(explicitMessage != explicitVs.end()
              && explicitMessage->message.find("required 0.250 mm") != std::string::npos,
          "explicit courtyard violation reports the configured clearance in millimetres");
}

static void testRightAngleBendQualityRule() {
    Board bend = makeBoard();
    bend.setNet(0, "SIG", 0);
    bend.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM},
                       {20 * NM_PER_MM, 10 * NM_PER_MM}, 200'000, 0, 0});
    bend.addTrace({0, {20 * NM_PER_MM, 10 * NM_PER_MM},
                       {20 * NM_PER_MM, 20 * NM_PER_MM}, 200'000, 0, 0});
    DrcOptions opt;
    opt.checkConnectivity = false;
    opt.checkUnassignedCopper = false;
    opt.minCopperToEdge = 0;
    opt.minCopperToHole = 0;
    opt.minCourtyardClearance = 0;
    CHECK(hasRule(runDrc(bend, opt), DrcRule::RightAngleBend),
          "a true two-segment 90-degree bend is reported");

    Board terminal = makeBoard();
    terminal.setNet(0, "SIG", 0);
    Footprint fp = onePadPart("U1", {20, 10}, {2, 2}, 0);
    terminal.addFootprint(std::move(fp));
    terminal.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM},
                           {20 * NM_PER_MM, 10 * NM_PER_MM}, 200'000, 0, 0});
    terminal.addTrace({0, {20 * NM_PER_MM, 10 * NM_PER_MM},
                           {20 * NM_PER_MM, 20 * NM_PER_MM}, 200'000, 0, 0});
    CHECK(!hasRule(runDrc(terminal, opt), DrcRule::RightAngleBend),
          "a corner terminating on a same-net pad is not a routing-bend warning");

    bend.addTrace({0, {20 * NM_PER_MM, 10 * NM_PER_MM},
                       {30 * NM_PER_MM, 10 * NM_PER_MM}, 200'000, 0, 0});
    CHECK(!hasRule(runDrc(bend, opt), DrcRule::RightAngleBend),
          "a three-segment T junction is not misreported as a bend");
}

static void testStructuredPlacementDrc() {
    Board b = makeBoard();
    RuleArea low;
    low.id = 7; low.name = "under-enclosure-lip"; low.maxHeight = 2 * NM_PER_MM;
    low.polygon = {{5 * NM_PER_MM, 5 * NM_PER_MM}, {20 * NM_PER_MM, 5 * NM_PER_MM},
                   {20 * NM_PER_MM, 20 * NM_PER_MM}, {5 * NM_PER_MM, 20 * NM_PER_MM}};
    CHECK(b.setRuleArea(low), "height-limited placement area accepted");

    Footprint hot = onePadPart("Q1", {10, 10}, {0.5, 0.5}, -1);
    hot.bodySize = {4 * NM_PER_MM, 4 * NM_PER_MM}; hot.bodyHeight = 3 * NM_PER_MM;
    hot.thermalPowerW = 2.0; hot.thermalClearance = 2 * NM_PER_MM;
    hot.testAccessRequired = true; hot.testAccessHalo = 3 * NM_PER_MM;
    Footprint neighbour = onePadPart("U1", {15, 10}, {0.5, 0.5}, -1);
    neighbour.bodySize = {4 * NM_PER_MM, 4 * NM_PER_MM};
    b.addFootprint(std::move(hot)); b.addFootprint(std::move(neighbour));

    DrcOptions opt;
    opt.checkConnectivity = false; opt.checkUnassignedCopper = false;
    opt.minCopperToEdge = 0; opt.minCopperToHole = 0; opt.minCourtyardClearance = 0;
    const auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::HeightConstraint), "component above rule-area height is reported");
    CHECK(hasRule(vs, DrcRule::ThermalSpacing), "thermal component halo is enforced");
    CHECK(hasRule(vs, DrcRule::TestAccess), "test-access halo is enforced");

    Board edge = makeBoard();
    Footprint probe = onePadPart("TP1", {2, 2}, {0.5, 0.5}, -1);
    probe.bodySize = {2 * NM_PER_MM, 2 * NM_PER_MM};
    probe.testAccessRequired = true; probe.testAccessHalo = 2 * NM_PER_MM;
    edge.addFootprint(std::move(probe));
    const auto edgeVs = runDrc(edge, opt);
    CHECK(hasRule(edgeVs, DrcRule::TestAccess), "test-access halo to board edge is enforced");
}

static void testLayerViaAndZonePolicies() {
    Board b = makeBoard(4);
    NetClass controlled;
    controlled.id = 3; controlled.name = "CONTROLLED";
    controlled.allowedLayers = layerBit(0) | layerBit(1);
    controlled.allowedViaTypes = 1u << std::uint32_t(ViaType::Through);
    controlled.maxViaCount = 1;
    b.setNetClass(controlled); b.setNet(4, "SIG", 3);
    LayerPolicy plane;
    plane.layer = 1; plane.name = "GND plane"; plane.role = LayerRole::Plane;
    plane.allowRouting = false;
    CHECK(b.setLayerPolicy(plane), "plane layer policy accepted");
    b.addTrace({0, {5 * NM_PER_MM, 5 * NM_PER_MM}, {10 * NM_PER_MM, 5 * NM_PER_MM},
                250'000, 1, 4, false});
    b.addVia({0, {15 * NM_PER_MM, 15 * NM_PER_MM}, 500'000, 200'000, 4, 0, 1,
              ViaType::Microvia});
    b.addVia({0, {20 * NM_PER_MM, 15 * NM_PER_MM}, 600'000, 300'000, 4, 0, 3,
              ViaType::Through});

    CopperZone outside;
    outside.id = 1; outside.name = "bad-zone"; outside.netId = 4; outside.layer = 0;
    outside.polygon = {{45 * NM_PER_MM, 45 * NM_PER_MM}, {55 * NM_PER_MM, 45 * NM_PER_MM},
                       {55 * NM_PER_MM, 55 * NM_PER_MM}, {45 * NM_PER_MM, 55 * NM_PER_MM}};
    CHECK(b.setCopperZone(outside), "zone record accepted for DRC validation");
    DrcOptions opt;
    opt.checkConnectivity = false; opt.checkUnassignedCopper = false;
    opt.minCopperToEdge = 0; opt.minCopperToHole = 0; opt.minCourtyardClearance = 0;
    const auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::LayerPolicyViolation), "trace on plane-only layer is reported");
    CHECK(hasRule(vs, DrcRule::ViaPolicyViolation), "via type/count policy is reported");
    CHECK(hasRule(vs, DrcRule::ZoneValidity), "zone outside board is reported");

    Router router(b);
    RouteRequest req;
    req.netId = 4; req.start = {2 * NM_PER_MM, 2 * NM_PER_MM};
    req.goal = {3 * NM_PER_MM, 3 * NM_PER_MM}; req.startLayer = 0; req.goalLayer = 1;
    CHECK(router.route(req).reason == RouteFail::BadRequest,
          "router rejects a target layer prohibited for signal routing");
}

static void testBottomSmdMapsToLastCopperLayer() {
    Board b = makeBoard(4);
    b.setNet(0, "BOTTOM", 0);
    Footprint bottom = onePadPart("U1", {10, 10}, {1, 1}, 0);
    bottom.side = 1;
    b.addFootprint(std::move(bottom));
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM},
                    {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 3, 0});
    b.addVia({0, {20 * NM_PER_MM, 10 * NM_PER_MM}, 600'000, 300'000,
              0, 0, 3, ViaType::Through});
    b.addTrace({0, {20 * NM_PER_MM, 10 * NM_PER_MM},
                    {25 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    CHECK(netIslandCount(b, 0) == 1,
          "bottom SMD pad maps to the last copper layer on a four-layer board");
}

static void testManufacturingCapiV2Contract() {
    DcBoardHandle h = dc_board_create();
    CHECK(h != nullptr, "v2 contract board created");
    CHECK(dc_board_set_copper_layers(h, 4) == DC_OK, "v2 contract layer stack set");
    CHECK(dc_board_set_outline(h, 0, 0, 50 * NM_PER_MM, 50 * NM_PER_MM) == DC_OK,
          "v2 contract outline set");
    DcPoint outline[4] = {{0, 0}, {50 * NM_PER_MM, 0},
                          {50 * NM_PER_MM, 50 * NM_PER_MM}, {0, 50 * NM_PER_MM}};
    DcPoint cutout[4] = {{30 * NM_PER_MM, 30 * NM_PER_MM},
                         {35 * NM_PER_MM, 30 * NM_PER_MM},
                         {35 * NM_PER_MM, 35 * NM_PER_MM},
                         {30 * NM_PER_MM, 35 * NM_PER_MM}};
    CHECK(dc_board_set_outline_polygon(h, outline, 4) == DC_OK,
          "polygon outline crosses v2 C API");
    CHECK(dc_board_cutout_add(h, cutout, 4) == DC_OK,
          "board cutout crosses v2 C API");
    DcPoint rulePoints[4] = {{5 * NM_PER_MM, 5 * NM_PER_MM},
                             {15 * NM_PER_MM, 5 * NM_PER_MM},
                             {15 * NM_PER_MM, 15 * NM_PER_MM},
                             {5 * NM_PER_MM, 15 * NM_PER_MM}};
    CHECK(dc_rule_area_set(h, 1, "keepout", rulePoints, 4, 0, 400'000, 300'000,
                           0, 1, 0, "test", "1") == DC_OK,
          "rule area and provenance cross v2 C API");
    CHECK(dc_rule_area_height_set(h, 1, 2 * NM_PER_MM) == DC_OK,
          "placement height limit crosses additive C API");
    CHECK(dc_layer_policy_set(h, 1, "L2 plane", 1, 0, 0, 35'000,
                              "stackup", "A") == DC_OK,
          "layer role and routing policy cross additive C API");
    CHECK(dc_net_set(h, 0, "N", 0) == DC_OK, "v2 contract net set");
    CHECK(dc_class_pair_set(h, 0, 0, 350'000, "fab", "2026") == DC_OK,
          "class-pair rule crosses v2 C API");
    CHECK(dc_class_routing_policy_set(h, 0, layerBit(0) | layerBit(1), 1u, 2) == DC_OK,
          "net-class layer/via policy crosses additive C API");
    CHECK(dc_copper_zone_set(h, 3, "GND zone", rulePoints, 4, 0, 0, 200'000,
                             1.0, 1, "layout", "A") == DC_OK,
          "polygon-zone policy crosses additive C API");

    DcPadDefV2 pad{};
    pad.w = 2 * NM_PER_MM; pad.h = 2 * NM_PER_MM; pad.netId = 0;
    pad.shape = std::int32_t(PadShape::Circle); std::strcpy(pad.name, "1");
    const std::uint64_t fp = dc_footprint_add_v2(
        h, "U1", "TEST", 10 * NM_PER_MM, 10 * NM_PER_MM, 0, 0, &pad, 1);
    CHECK(fp != 0, "shape-aware footprint crosses v2 C API");
    CHECK(dc_pad_clearance_set(h, fp, "1", 450'000) == DC_OK,
          "pad-specific rule crosses v2 C API");
    DcPoint court[4] = {{-2 * NM_PER_MM, -2 * NM_PER_MM},
                        { 2 * NM_PER_MM, -2 * NM_PER_MM},
                        { 2 * NM_PER_MM,  2 * NM_PER_MM},
                        {-2 * NM_PER_MM,  2 * NM_PER_MM}};
    CHECK(dc_footprint_geometry_set(h, fp, 0, 0, 3 * NM_PER_MM, 3 * NM_PER_MM,
                                    NM_PER_MM, court, 4) == DC_OK,
          "body/courtyard geometry crosses v2 C API");
    CHECK(dc_footprint_placement_set(h, fp, 1, "power", "left", 1.5,
                                     2 * NM_PER_MM, 1, NM_PER_MM) == DC_OK,
          "structured placement constraints cross additive C API");
    CHECK(dc_via_add_v2(h, 20 * NM_PER_MM, 20 * NM_PER_MM, 500'000, 100'000,
                        0, 0, 1, std::int32_t(ViaType::Blind)) != 0,
          "explicit blind via crosses v2 C API");
    CHECK(dc_via_add_v2(h, 25 * NM_PER_MM, 20 * NM_PER_MM, 500'000, 100'000,
                        0, 0, 2, std::int32_t(ViaType::Through)) == 0,
          "invalid partial-span through via rejected");

    DcRouteRequestV2 route{};
    route.abiVersion = 2; route.structSize = sizeof route; route.netId = 0;
    route.sx = 5 * NM_PER_MM; route.sy = 25 * NM_PER_MM; route.startLayer = 0;
    route.gx = 45 * NM_PER_MM; route.gy = 25 * NM_PER_MM; route.goalLayer = 0;
    route.traceWidth = 200'000; route.clearance = 200'000;
    route.minCopperToEdge = 400'000; route.gridStep = 250'000;
    route.viaDiameter = 600'000; route.viaDrill = 300'000;
    route.maxExpansions = 100'000; route.minDrillToDrill = 250'000;
    CHECK(dc_route_run_v2(h, &route) == DC_OK,
          "fabrication edge setback crosses route v2 C API");
    --route.structSize;
    CHECK(dc_route_run_v2(h, &route) == DC_ERR_INVALID_ARG,
          "route v2 rejects ABI size mismatch");

    DcDrcOptionsV2 opt{};
    opt.abiVersion = 2; opt.structSize = sizeof opt;
    opt.defaultClearance = 200'000; opt.minTraceWidth = 100'000;
    opt.minDrill = 200'000; opt.minAnnularRing = 50'000;
    opt.minDrillToDrill = 250'000; opt.minMicroviaDrill = 75'000;
    opt.minMicroviaWall = 100'000; opt.minCopperToEdge = 250'000;
    opt.minCopperToHole = 250'000; opt.minCourtyardClearance = 250'000;
    opt.checkConnectivity = 1; opt.checkSkew = 1; opt.checkUnassignedCopper = 1;
    CHECK(dc_drc_run_v2(h, &opt) >= 0, "v2 manufacturing DRC contract accepted");
    --opt.structSize;
    CHECK(dc_drc_run_v2(h, &opt) == DC_ERR_INVALID_ARG,
          "v2 manufacturing DRC rejects ABI size mismatch");
    dc_board_destroy(h);
}

static void testDrcConnectivity() {
    Board b = makeBoard();
    b.setNet(0, "GND", 0);
    b.addFootprint(onePadPart("R1", {10, 10}, {1, 1}, 0));
    b.addFootprint(onePadPart("R2", {30, 10}, {1, 1}, 0));
    CHECK(netIslandCount(b, 0) == 2, "two unconnected pads = 2 islands");
    const auto disconnected = netIslandComponents(b, 0);
    CHECK(disconnected.size() == 2,
          "topology exposes both disconnected terminal components");
    CHECK(disconnected[0].terminalCount == 1
          && disconnected[1].terminalCount == 1,
          "island summaries count terminal copper");
    CHECK(disconnected[0].anchor.x < disconnected[1].anchor.x,
          "island summaries have deterministic geometric order");

    auto vs = runDrc(b);
    CHECK(hasRule(vs, DrcRule::UnconnectedNet), "unconnected net reported");

    // connect them
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {30 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    CHECK(netIslandCount(b, 0) == 1, "trace joins the islands");
    const auto connected = netIslandComponents(b, 0);
    CHECK(connected.size() == 1 && connected[0].terminalCount == 2
          && connected[0].obstacleCount == 3,
          "joined topology summary includes two pads and their trace");
    auto vs2 = runDrc(b);
    CHECK(!hasRule(vs2, DrcRule::UnconnectedNet), "no unconnected report when joined");

    Board near = makeBoard(); near.setNet(0, "NEAR", 0);
    near.addFootprint(onePadPart("P1", {10, 20}, {1, 1}, 0));
    near.addFootprint(onePadPart("P2", {11.1, 20}, {1, 1}, 0)); // 0.1 mm air gap
    CHECK(netIslandCount(near, 0) == 2,
          "connectivity never bridges a small but real copper air gap");

    // multi-layer: pad on top, pad on bottom, joined only by a via
    Board b3 = makeBoard(2);
    b3.setNet(0, "SIG", 0);
    b3.addFootprint(onePadPart("A", {10, 10}, {1, 1}, 0));
    Footprint bottom = onePadPart("B", {30, 10}, {1, 1}, 0);
    bottom.side = 1;
    b3.addFootprint(std::move(bottom));
    b3.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    b3.addTrace({0, {20 * NM_PER_MM, 10 * NM_PER_MM}, {30 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 1, 0});
    CHECK(netIslandCount(b3, 0) == 2, "layer change without via = still split");
    b3.addVia({0, {20 * NM_PER_MM, 10 * NM_PER_MM}, 600'000, 300'000, 0, 0, 1});
    CHECK(netIslandCount(b3, 0) == 1, "via stitches the layers");

    DcBoardHandle h = dc_board_create();
    CHECK(h != nullptr, "island query C API board created");
    CHECK(dc_board_set_outline(h, 0, 0, 40 * NM_PER_MM, 20 * NM_PER_MM) == DC_OK,
          "island query C API outline set");
    CHECK(dc_net_set(h, 0, "GND", 0) == DC_OK,
          "island query C API net set");
    DcPadDef pad{};
    pad.w = NM_PER_MM; pad.h = NM_PER_MM; pad.netId = 0;
    std::strcpy(pad.name, "1");
    CHECK(dc_footprint_add(h, "P1", "TEST", 10 * NM_PER_MM, 10 * NM_PER_MM,
                           0, 0, &pad, 1) != 0,
          "first C API island terminal added");
    CHECK(dc_footprint_add(h, "P2", "TEST", 30 * NM_PER_MM, 10 * NM_PER_MM,
                           0, 0, &pad, 1) != 0,
          "second C API island terminal added");
    CHECK(dc_net_island_components(h, 0, nullptr, 0) == 2,
          "C API reports required island summary count");
    DcNetIslandComponent summaries[2]{};
    CHECK(dc_net_island_components(h, 0, summaries, 1) == 2
          && summaries[0].terminalCount == 1,
          "C API reports required count while honoring output capacity");
    CHECK(dc_net_island_components(h, 0, nullptr, 1) == DC_ERR_INVALID_ARG,
          "C API rejects a nonzero capacity without output storage");
    CHECK(dc_trace_add(h, 10 * NM_PER_MM, 10 * NM_PER_MM,
                       30 * NM_PER_MM, 10 * NM_PER_MM,
                       250'000, 0, 0, 0) != 0,
          "C API island terminals joined");
    CHECK(dc_net_island_components(h, 0, summaries, 2) == 1
          && summaries[0].terminalCount == 2,
          "C API exposes the joined topology component");
    dc_board_destroy(h);
}

static void testDrcSkew() {
    Board b = makeBoard();
    NetClass pair;
    pair.id = 1; pair.name = "USB"; pair.maxSkew = 1 * NM_PER_MM;
    b.setNetClass(pair);
    b.setNet(0, "D_P", 1);
    b.setNet(1, "D_N", 1);
    b.addTrace({0, {0, 10 * NM_PER_MM}, {10 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    b.addTrace({0, {0, 12 * NM_PER_MM}, {25 * NM_PER_MM, 12 * NM_PER_MM}, 250'000, 0, 1});
    DrcOptions opt;
    opt.checkConnectivity = false;
    auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::SkewExceeded), "15 mm skew over 1 mm budget reported");

    // matched pair passes
    Board b2 = makeBoard();
    b2.setNetClass(pair);
    b2.setNet(0, "D_P", 1);
    b2.setNet(1, "D_N", 1);
    b2.addTrace({0, {0, 10 * NM_PER_MM}, {10 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    b2.addTrace({0, {0, 12 * NM_PER_MM}, {10 * NM_PER_MM + 500'000, 12 * NM_PER_MM}, 250'000, 0, 1});
    auto vs2 = runDrc(b2, opt);
    CHECK(!hasRule(vs2, DrcRule::SkewExceeded), "0.5 mm skew within 1 mm budget passes");
}

static void testDrcNetClassClearance() {
    Board b = makeBoard();
    NetClass hv;
    hv.id = 1; hv.name = "HV"; hv.clearance = 1 * NM_PER_MM;
    b.setNetClass(hv);
    b.setNet(0, "HV1", 1);
    b.setNet(1, "LV", 0);
    // gap of 0.5 mm: fine for default 0.2, violates HV 1.0
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM}, {20 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    b.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM + 750'000}, {20 * NM_PER_MM, 10 * NM_PER_MM + 750'000}, 250'000, 0, 1});
    DrcOptions opt;
    opt.checkConnectivity = false;
    auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::TraceTraceClearance), "net-class clearance (max of pair) applied");
}

static void testClassPairAndPadRulePrecedence() {
    Board pairBoard = makeBoard();
    NetClass a; a.id = 1; a.name = "A"; a.clearance = 200'000;
    NetClass b; b.id = 2; b.name = "B"; b.clearance = 200'000;
    pairBoard.setNetClass(a); pairBoard.setNetClass(b);
    pairBoard.setNet(0, "A0", 1); pairBoard.setNet(1, "B0", 2);
    ClassPairRule pair; pair.classA = 1; pair.classB = 2; pair.clearance = NM_PER_MM;
    CHECK(pairBoard.setClassPairRule(pair), "class-pair rule accepted");
    pairBoard.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM},
                           {20 * NM_PER_MM, 10 * NM_PER_MM}, 100'000, 0, 0});
    pairBoard.addTrace({0, {10 * NM_PER_MM, 10 * NM_PER_MM + 600'000},
                           {20 * NM_PER_MM, 10 * NM_PER_MM + 600'000}, 100'000, 0, 1});
    DrcOptions opt; opt.checkConnectivity = false; opt.minCopperToEdge = 0;
    opt.minCopperToHole = 0; opt.checkUnassignedCopper = false;
    CHECK(hasRule(runDrc(pairBoard, opt), DrcRule::TraceTraceClearance),
          "class-pair clearance overrides both member classes");

    Board padBoard = makeBoard();
    padBoard.setNet(0, "PAD", 0); padBoard.setNet(1, "SIG", 0);
    Footprint fp = onePadPart("P1", {10, 10}, {1, 1}, 0);
    fp.pads[0].clearanceOverride = NM_PER_MM;
    padBoard.addFootprint(std::move(fp));
    padBoard.addTrace({0, {8 * NM_PER_MM, 10 * NM_PER_MM + NM_PER_MM},
                          {12 * NM_PER_MM, 10 * NM_PER_MM + NM_PER_MM}, 100'000, 0, 1});
    CHECK(hasRule(runDrc(padBoard, opt), DrcRule::PadTraceClearance),
          "pad-specific clearance overrides class-pair and board defaults");

    Board finePitch = makeBoard();
    finePitch.setNet(0, "A", 0); finePitch.setNet(1, "B", 0);
    Footprint left = onePadPart("U1", {10, 10}, {1, 1}, 0);
    Footprint right = onePadPart("U1", {11.1, 10}, {1, 1}, 1);
    left.pads[0].clearanceOverride = 50'000;
    right.pads[0].clearanceOverride = 50'000;
    finePitch.addFootprint(std::move(left));
    finePitch.addFootprint(std::move(right));
    CHECK(!hasRule(runDrc(finePitch, opt), DrcRule::PadPadClearance),
          "two explicit fine-pitch pad clearances replace coarse net rules");
    finePitch.addTrace({0, {9 * NM_PER_MM, 10 * NM_PER_MM + 600'000},
                           {12 * NM_PER_MM, 10 * NM_PER_MM + 600'000},
                           100'000, 0, 1});
    CHECK(!hasRule(runDrc(finePitch, opt), DrcRule::PadTraceClearance),
          "fine-pitch pad clearance governs its local trace fanout envelope");

    Board neckDown = makeBoard();
    NetClass power; power.id = 2; power.name = "Power";
    power.traceWidth = NM_PER_MM; power.clearance = 200'000;
    neckDown.setNetClass(power); neckDown.setNet(0, "VBUS", 2);
    TraceSegment shortFanout{0, {10 * NM_PER_MM, 10 * NM_PER_MM},
        {11 * NM_PER_MM, 10 * NM_PER_MM}, 150'000, 0, 0};
    shortFanout.minWidthOverride = 150'000;
    neckDown.addTrace(shortFanout);
    CHECK(!hasRule(runDrc(neckDown, opt), DrcRule::TraceWidth),
          "explicit local trace minimum permits a documented package neck-down");
}

// ------------------------------------------------------------------ pours ----

static void testPourGeneration() {
    Board b = makeBoard();
    b.setNet(0, "GND", 0);
    b.setNet(1, "SIG", 0);
    b.addTrace({0, {10 * NM_PER_MM, 25 * NM_PER_MM}, {40 * NM_PER_MM, 25 * NM_PER_MM}, 250'000, 0, 1});
    b.addFootprint(onePadPart("U1", {25, 10}, {3, 3}, 1));

    PourRequest req;
    req.netId = 0;
    req.layer = 0;
    req.region = b.outline;
    req.clearance = 250'000;
    auto pour = generatePour(b, req);
    CHECK(!pour.strokes.empty(), "pour produces strokes");

    // verify the pour respects clearance by adding it and running DRC
    for (const auto& sgmt : pour.strokes) b.addTrace(sgmt);
    DrcOptions opt;
    opt.checkConnectivity = false;
    auto vs = runDrc(b, opt);
    int pourClashes = 0;
    for (const auto& v : vs)
        if (v.rule == DrcRule::TraceTraceClearance || v.rule == DrcRule::PadTraceClearance)
            ++pourClashes;
    CHECK(pourClashes == 0, "pour keeps clearance from other-net copper (DRC-verified)");
    CHECK(!hasRule(vs, DrcRule::CopperToEdge),
          "pour keeps its configured manufacturing clearance from the board edge");

    // pour must not flood the SIG pad interior (pad spans x 23.5..26.5 at y 10)
    bool floodsPad = false;
    for (const auto& sgmt : pour.strokes)
        if (sgmt.a.x < 26 * NM_PER_MM && sgmt.b.x > 24 * NM_PER_MM &&
            std::abs(sgmt.a.y - 10 * NM_PER_MM) < 1 * NM_PER_MM)
            floodsPad = true;
    CHECK(!floodsPad, "no stroke crosses the foreign pad interior");
}

static void testPourConnectsSameNet() {
    Board b = makeBoard();
    b.setNet(0, "GND", 0);
    b.addFootprint(onePadPart("C1", {25, 25}, {2, 2}, 0));
    PourRequest req;
    req.netId = 0;
    req.layer = 0;
    req.region = b.outline;
    auto pour = generatePour(b, req);
    // some stroke should overlap the same-net pad (direct connect)
    bool touches = false;
    for (const auto& sgmt : pour.strokes)
        if (std::abs(sgmt.a.y - 25 * NM_PER_MM) < 1 * NM_PER_MM &&
            sgmt.a.x < 24 * NM_PER_MM && sgmt.b.x > 26 * NM_PER_MM)
            touches = true;
    CHECK(touches, "pour floods across the same-net pad");
}

static void testPourClipsToAuthoritativeZonePolygon() {
    Board b = makeBoard(); b.setNet(0, "GND", 0);
    PourRequest req;
    req.netId = 0; req.layer = 0;
    req.region = {{10 * NM_PER_MM, 10 * NM_PER_MM}, {30 * NM_PER_MM, 30 * NM_PER_MM}};
    req.zonePolygon = {{10 * NM_PER_MM, 10 * NM_PER_MM},
                       {30 * NM_PER_MM, 10 * NM_PER_MM},
                       {20 * NM_PER_MM, 30 * NM_PER_MM}};
    const auto pour = generatePour(b, req);
    CHECK(!pour.strokes.empty(), "polygon zone produces clipped copper");
    bool escaped = false;
    for (const auto& stroke : pour.strokes)
        if (!pointInPolygon(stroke.a, req.zonePolygon) || !pointInPolygon(stroke.b, req.zonePolygon))
            escaped = true;
    CHECK(!escaped, "all generated pour endpoints remain inside the authoritative zone polygon");
}

// ----------------------------------------------------------------- scale ----

static void testScalabilitySmoke() {
    // 5,000 pads + 2,000 traces: DRC and a route must complete quickly.
    Board b = makeBoard(4, 200, 200);
    b.setNet(0, "GND", 0);
    int net = 1;
    for (int i = 0; i < 50; ++i)
        for (int j = 0; j < 50; ++j) {
            Footprint fp = onePadPart(("R" + std::to_string(i * 50 + j)).c_str(),
                                      {double(4 + i * 3.9), double(4 + j * 3.9)}, {1, 1}, -1);
            fp.pads[0].netId = (net++ % 97);
            b.addFootprint(std::move(fp));
        }
    for (int n = 0; n < 97; ++n) b.setNet(n, "N" + std::to_string(n), 0);
    for (int i = 0; i < 2000; ++i) {
        coord_t y = (coord_t(i) % 180 + 5) * NM_PER_MM + (i % 7) * 137'000;
        b.addTrace({0, {5 * NM_PER_MM, y}, {195 * NM_PER_MM, y}, 150'000, 1 + (i % 3), i % 97});
    }

    auto t0 = std::chrono::steady_clock::now();
    DrcOptions opt;
    opt.checkConnectivity = false;   // island check is O(n^2) per net; keep smoke fast
    opt.checkSkew = false;
    auto vs = runDrc(b, opt);
    auto t1 = std::chrono::steady_clock::now();
    const double drcMs = std::chrono::duration<double, std::milli>(t1 - t0).count();
    std::printf("  scale: DRC on 2.5k pads + 2k traces: %.0f ms, %zu violations\n", drcMs, vs.size());
    CHECK(drcMs < 30'000, "DRC completes in bounded time on a large board");

    t0 = std::chrono::steady_clock::now();
    Router router(b);
    RouteRequest req;
    req.netId = 0;
    req.start = {2 * NM_PER_MM, 2 * NM_PER_MM};
    req.goal = {198 * NM_PER_MM, 198 * NM_PER_MM};
    req.gridStep = 1 * NM_PER_MM;
    auto res = router.route(req);
    t1 = std::chrono::steady_clock::now();
    const double routeMs = std::chrono::duration<double, std::milli>(t1 - t0).count();
    std::printf("  scale: 280 mm route on dense board: %.0f ms, success=%d, expansions=%zu\n",
                routeMs, int(res.success), res.expansions);
    CHECK(res.success || res.reason != RouteFail::NoPath, "long route resolves (or reports a bounded reason)");
    CHECK(routeMs < 30'000, "router completes in bounded time");
}


// ---------------------------------------------------------------- physics ----

static void testMathEngine() {
    using namespace dc::phys;
    // K(0) = pi/2 exactly; K(1/sqrt(2)) = 1.85407467730137 (Legendre)
    CHECK(std::abs(ellipticK(0.0) - 1.5707963267948966) < 1e-12, "K(0) = pi/2");
    CHECK(std::abs(ellipticK(0.7071067811865476) - 1.8540746773013719) < 1e-9,
          "K(1/sqrt2) matches Legendre value");
    CHECK(std::isnan(ellipticK(1.5)), "K rejects k >= 1");
}

static void testMicrostripCanonical() {
    using namespace dc::phys;
    LineResult r;
    // Hammerstad-Jensen vs published value: w/h = 1, er = 4.4, t = 0
    CHECK(microstrip(0.2, 0.2, 0.0, 4.4, r), "microstrip evaluates");
    // Hammerstad eq. 2.116 ("Microstrip Lines and Slotlines" 4th ed., p.90)
    // evaluates to 71.0 ohm for w/h = 1, er = 4.4 — H-J must agree within 1%.
    CHECK(std::abs(r.z0 - 71.0) < 1.0, "w/h=1 on FR-4 = ~71 ohm (book eq. 2.116)");
    CHECK(r.eEff > 2.5 && r.eEff < 4.4, "eEff between air and substrate");

    // monotone: wider = lower impedance
    LineResult wide, narrow;
    microstrip(1.0, 0.2, 0.035, 4.4, wide);
    microstrip(0.1, 0.2, 0.035, 4.4, narrow);
    CHECK(wide.z0 < narrow.z0, "Z0 falls with width");

    // solver round-trip at 50 ohm
    const double w = microstripWidthForZ0(50.0, 0.2, 0.035, 4.4);
    CHECK(!std::isnan(w), "50-ohm width solvable");
    microstrip(w, 0.2, 0.035, 4.4, r);
    CHECK(std::abs(r.z0 - 50.0) < 0.01, "width solver round-trips to 50 ohm");
    // FR-4 microstrip delay is famously ~140-150 ps/inch = 5.5-6.0 ps/mm
    CHECK(r.delayPsPerMm > 5.0 && r.delayPsPerMm < 6.5, "FR-4 delay plausible");
}

static void testStriplineCanonical() {
    using namespace dc::phys;
    LineResult r;
    // Cohn exact, thin strip, w/b = 1, er = 1: chart value ~65.4 ohm
    CHECK(stripline(1.0, 1.0, 0.0, 1.0, r), "stripline evaluates");
    CHECK(std::abs(r.z0 - 65.35) < 0.7, "Cohn w/b=1 air = ~65.4 ohm");

    const double w = striplineWidthForZ0(50.0, 0.5, 0.018, 4.2);
    CHECK(!std::isnan(w), "stripline 50-ohm width solvable");
    stripline(w, 0.5, 0.018, 4.2, r);
    CHECK(std::abs(r.z0 - 50.0) < 0.01, "stripline solver round-trips");
    // homogeneous dielectric: eEff == er
    CHECK(std::abs(r.eEff - 4.2) < 1e-12, "stripline eEff = er");
}

static void testDifferentialPairs() {
    using namespace dc::phys;
    // USB: 90-ohm differential on a thin HDI stack
    const double w = diffMicrostripWidthForZ(90.0, 0.15, 0.15, 0.035, 4.4);
    CHECK(!std::isnan(w), "90-ohm diff width solvable");
    const double zd = differentialMicrostrip(w, 0.15, 0.15, 0.035, 4.4);
    CHECK(std::abs(zd - 90.0) < 0.05, "diff solver round-trips to 90 ohm");
    // wider gap -> weaker coupling -> Zdiff approaches 2*Z0 from below
    const double zNear = differentialMicrostrip(0.3, 0.1, 0.2, 0.035, 4.4);
    const double zFar  = differentialMicrostrip(0.3, 2.0, 0.2, 0.035, 4.4);
    CHECK(zFar > zNear, "coupling weakens with gap");
    LineResult se;
    microstrip(0.3, 0.2, 0.035, 4.4, se);
    CHECK(zFar < 2.0 * se.z0 + 0.5 && zFar > 1.8 * se.z0, "Zdiff -> 2*Z0 limit");
}

static void testSkinAndLosses() {
    using namespace dc::phys;
    // copper at 1 GHz: 2.09 um (textbook value)
    CHECK(std::abs(skinDepthMm(1e9) * 1000.0 - 2.09) < 0.02, "Cu skin depth @1GHz = 2.09 um");
    // R_ac grows with sqrt(f) once skin-limited
    const double r1 = traceResistanceAc(0.3, 0.035, 100.0, 1e9);
    const double r4 = traceResistanceAc(0.3, 0.035, 100.0, 4e9);
    CHECK(r4 > 1.5 * r1 && r4 < 2.5 * r1, "AC resistance ~ sqrt(f) scaling");
    CHECK(traceResistanceAc(0.3, 0.035, 100.0, 1e3) ==
          traceResistanceDc(0.3, 0.035, 100.0), "low f falls back to DC");
    // loss positive and rising with frequency
    const double l1 = microstripLossDbPerM(0.367, 0.2, 0.035, 4.4, 0.02, 1e9);
    const double l5 = microstripLossDbPerM(0.367, 0.2, 0.035, 4.4, 0.02, 5e9);
    CHECK(l1 > 1.0 && l1 < 20.0, "FR-4 1 GHz loss in plausible range");
    CHECK(l5 > l1, "loss increases with frequency");
}

static void testCurrentCapacity() {
    using namespace dc::phys;
    // 1 mm / 1 oz external, dT = 10C: published IPC-2221 calculators ~2.2-2.6 A
    const double iExt = ipc2221CurrentA(1.0, 0.035, 10.0, true);
    CHECK(iExt > 2.0 && iExt < 2.8, "IPC-2221 external ballpark");
    CHECK(std::abs(ipc2221CurrentA(1.0, 0.035, 10.0, false) - iExt / 2.0) < 1e-9,
          "internal = half of external");
    // fusing must exceed continuous rating by a wide margin
    CHECK(onderdonkFusingA(1.0, 0.035, 1.0) > 3.0 * iExt, "fusing >> continuous");
    CHECK(onderdonkFusingA(1.0, 0.035, 0.01) > onderdonkFusingA(1.0, 0.035, 1.0),
          "shorter pulse tolerates more current");
}

static void testViaPhysics() {
    using namespace dc::phys;
    // 1.6 mm board, 0.3 mm drill: the classic ~1.2-1.3 nH via
    const double L = viaInductanceNH(1.6, 0.3);
    CHECK(L > 1.0 && L < 1.6, "through-via inductance ballpark");
    CHECK(viaInductanceNH(0.1, 0.1) < L, "microvia much smaller L");
    const double C = viaCapacitancePF(4.4, 1.6, 0.6, 1.0);
    CHECK(C > 0.3 && C < 1.0, "via capacitance sub-pF");
    const double R = viaResistanceOhm(1.6, 0.3, 0.025);
    CHECK(R > 0.5e-3 && R < 5e-3, "via resistance ~1 mOhm");
    CHECK(viaThermalResistanceKW(1.6, 0.3, 0.025) > 0, "thermal resistance positive");
    CHECK(viaCurrentA(0.3, 0.025, 10.0) > 0.3, "via carries meaningful current");
}

static void testCouplingAndPlanes() {
    using namespace dc::phys;
    CHECK(std::abs(crosstalkCoefficient(1.0, 1.0) - 0.5) < 1e-12, "s=h -> 0.5");
    CHECK(crosstalkCoefficient(3.0, 1.0) < 0.11, "3h spacing -> ~10%");
    // 10x10 mm plane pair, 0.1 mm apart, er 4.4: C = eps0*er*A/d ~ 39 pF
    const double C = planeCapacitancePF(100.0, 0.1, 4.4);
    CHECK(std::abs(C - 38.96) < 0.5, "plane capacitance matches eps0*er*A/d");
}


static void testRouteEndpointExactness() {
    // Off-grid pad centres must be hit exactly — a route that stops on the
    // grid next to the pad is not connected copper.
    Board b = makeBoard();
    b.setNet(0, "SIG", 0);
    Footprint a = onePadPart("A", {10.05, 10.05}, {1, 1}, 0);
    Footprint c = onePadPart("B", {40.07, 25.03}, {1, 1}, 0);
    b.addFootprint(std::move(a));
    b.addFootprint(std::move(c));

    Router router(b);
    RouteRequest req;
    req.netId = 0;
    req.start = {coord_t(10.05 * NM_PER_MM), coord_t(10.05 * NM_PER_MM)};
    req.goal  = {coord_t(40.07 * NM_PER_MM), coord_t(25.03 * NM_PER_MM)};
    req.gridStep = 500'000;
    auto res = router.route(req);
    CHECK(res.success, "off-grid pad-to-pad route succeeds");
    CHECK(res.path.front().p == req.start, "path starts exactly at the start pad centre");
    CHECK(res.path.back().p == req.goal, "path ends exactly at the goal pad centre");

    // add the route as copper and confirm the engine sees one island
    for (std::size_t i = 0; i + 1 < res.path.size(); ++i) {
        if (res.path[i].layer != res.path[i + 1].layer) continue;
        if (res.path[i].p == res.path[i + 1].p) continue;
        b.addTrace({0, res.path[i].p, res.path[i + 1].p, 250'000, res.path[i].layer, 0});
    }
    CHECK(netIslandCount(b, 0) == 1, "routed pins form one connected island");
}

static void testPolygonOutlineCutoutAndSweptRouting() {
    Board b = makeBoard();
    CHECK(b.setOutlinePolygon({{0, 0}, {50 * NM_PER_MM, 0},
                               {50 * NM_PER_MM, 50 * NM_PER_MM},
                               {0, 50 * NM_PER_MM}}),
          "polygon outline accepted");
    CHECK(b.addCutout({{20 * NM_PER_MM, 10 * NM_PER_MM},
                       {30 * NM_PER_MM, 10 * NM_PER_MM},
                       {30 * NM_PER_MM, 40 * NM_PER_MM},
                       {20 * NM_PER_MM, 40 * NM_PER_MM}}),
          "board cutout accepted");
    CHECK(b.containsPoint({5 * NM_PER_MM, 25 * NM_PER_MM}), "substrate point contained");
    CHECK(!b.containsPoint({25 * NM_PER_MM, 25 * NM_PER_MM}), "cutout point excluded");

    b.setNet(0, "SIG", 0);
    Router router(b);
    RouteRequest req;
    req.netId = 0; req.start = {5 * NM_PER_MM, 25 * NM_PER_MM};
    req.goal = {45 * NM_PER_MM, 25 * NM_PER_MM};
    req.gridStep = 500'000; req.allowVias = false;
    req.edgeClearance = 750'000;
    const auto route = router.route(req);
    CHECK(route.success, "router finds path around a routed board cutout");
    for (std::size_t i = 1; i < route.path.size(); ++i) {
        if (route.path[i - 1].layer != route.path[i].layer) continue;
        CHECK(b.segmentWithinOutline(route.path[i - 1].p, route.path[i].p,
                                     req.traceWidth / 2 + req.edgeClearance),
              "every routed segment preserves copper setback from cutouts");
    }
    PourRequest pourReq;
    pourReq.netId = 0; pourReq.layer = 0; pourReq.region = b.outline;
    pourReq.lineWidth = 500'000; pourReq.clearance = 250'000;
    const auto pour = generatePour(b, pourReq);
    CHECK(!pour.strokes.empty(), "polygon board produces copper pour strokes");
    for (const auto& stroke : pour.strokes)
        CHECK(b.segmentWithinOutline(stroke.a, stroke.b, stroke.width / 2),
              "pour stroke is clipped to outline and cutouts");
    for (const auto& stroke : pour.strokes) b.addTrace(stroke);
    DrcOptions pourOpt;
    pourOpt.checkConnectivity = false;
    pourOpt.checkUnassignedCopper = false;
    const auto pourVs = runDrc(b, pourOpt);
    CHECK(!hasRule(pourVs, DrcRule::BoardEdge)
              && !hasRule(pourVs, DrcRule::CopperToEdge),
          "pour keeps clearance from polygon outline and routed cutouts");

    b.addTrace({0, {10 * NM_PER_MM, 25 * NM_PER_MM},
                   {40 * NM_PER_MM, 25 * NM_PER_MM}, 100'000, 0, 0});
    DrcOptions opt;
    opt.checkConnectivity = false; opt.checkUnassignedCopper = false;
    const auto vs = runDrc(b, opt);
    CHECK(hasRule(vs, DrcRule::BoardEdge), "trace crossing a cutout fails board containment");
    CHECK(hasRule(vs, DrcRule::CopperToEdge), "cutout participates in copper edge setback");
}

static void testRouterPreventsDiagonalCornerCutting() {
    Board b = makeBoard();
    b.setNet(0, "BLOCK", 0); b.setNet(1, "SIG", 0);
    Footprint blocker = onePadPart("X1", {10.5, 10.5}, {0.8, 0.8}, 0);
    b.addFootprint(std::move(blocker));
    Router router(b);
    RouteRequest req;
    req.netId = 1; req.start = {9 * NM_PER_MM, 9 * NM_PER_MM};
    req.goal = {12 * NM_PER_MM, 12 * NM_PER_MM}; req.gridStep = NM_PER_MM;
    req.traceWidth = 100'000; req.clearance = 100'000; req.allowVias = false;
    const auto route = router.route(req);
    CHECK(route.success, "corner-cut fixture remains routable by detour");
    bool crossesDirectly = route.path.size() == 2;
    CHECK(!crossesDirectly, "swept segment check rejects diagonal crossing between clear grid nodes");
}

static void testRuleAreaPrecedenceAndProhibitions() {
    Board b = makeBoard();
    b.setNet(0, "A", 0); b.setNet(1, "B", 0);
    RuleArea keepout;
    keepout.id = 1; keepout.name = "connector keepout"; keepout.layer = 0;
    keepout.polygon = {{20 * NM_PER_MM, 10 * NM_PER_MM},
                       {30 * NM_PER_MM, 10 * NM_PER_MM},
                       {30 * NM_PER_MM, 40 * NM_PER_MM},
                       {20 * NM_PER_MM, 40 * NM_PER_MM}};
    keepout.forbidRouting = true; keepout.forbidVias = true;
    keepout.source = "test"; keepout.sourceRevision = "1";
    CHECK(b.setRuleArea(keepout), "rule area accepted");

    Router router(b);
    RouteRequest req;
    req.netId = 0; req.start = {5 * NM_PER_MM, 25 * NM_PER_MM};
    req.goal = {45 * NM_PER_MM, 25 * NM_PER_MM}; req.gridStep = 500'000;
    req.allowVias = false;
    const auto route = router.route(req);
    CHECK(route.success, "router detours around no-routing area");
    for (std::size_t i = 1; i < route.path.size(); ++i)
        CHECK(!b.ruleAreaIntersectsSegment(keepout, route.path[i - 1].p,
                                           route.path[i].p, route.path[i].layer),
              "routed segment does not enter prohibited area");

    b.addTrace({0, {10 * NM_PER_MM, 25 * NM_PER_MM},
                   {40 * NM_PER_MM, 25 * NM_PER_MM}, 100'000, 0, 0});
    b.addVia({0, {25 * NM_PER_MM, 25 * NM_PER_MM}, 600'000, 300'000,
              0, 0, 1, ViaType::Through});
    DrcOptions opt;
    opt.checkConnectivity = false; opt.minCopperToEdge = 0; opt.minCopperToHole = 0;
    const auto prohibited = runDrc(b, opt);
    CHECK(countRule(prohibited, DrcRule::RuleAreaViolation) >= 2,
          "DRC reports trace and via rule-area violations");

    Board widthBoard = makeBoard();
    widthBoard.setNet(0, "SIG", 0);
    RuleArea widthArea;
    widthArea.id = 2; widthArea.name = "power neck"; widthArea.layer = 0;
    widthArea.polygon = {{5 * NM_PER_MM, 5 * NM_PER_MM}, {20 * NM_PER_MM, 5 * NM_PER_MM},
                         {20 * NM_PER_MM, 15 * NM_PER_MM}, {5 * NM_PER_MM, 15 * NM_PER_MM}};
    widthArea.minTraceWidth = 500'000; widthArea.clearance = 600'000;
    CHECK(widthBoard.setRuleArea(widthArea), "width/clearance rule area accepted");
    widthBoard.addTrace({0, {7 * NM_PER_MM, 10 * NM_PER_MM},
                            {18 * NM_PER_MM, 10 * NM_PER_MM}, 250'000, 0, 0});
    const auto widthVs = runDrc(widthBoard, opt);
    CHECK(hasRule(widthVs, DrcRule::TraceWidth),
          "rule-area minimum width overrides net class and board profile");
}

// ------------------------------------------------- dense linear algebra ----

static void testDenseLuSolve() {
    // 3x3 system with a known solution: A*x = b for x = (1, 2, 3).
    double A[9] = {
        2, 1, 1,
        4, -6, 0,
        -2, 7, 2
    };
    // x = (1, 2, 3) => b = A*x
    double b[3] = {
        2 * 1 + 1 * 2 + 1 * 3,      // 7
        4 * 1 + -6 * 2 + 0 * 3,     // -8
        -2 * 1 + 7 * 2 + 2 * 3      // 18
    };
    double x[3] = {0, 0, 0};
    bool ok = dc::dense_lu_solve(A, 3, b, 1, x);
    CHECK(ok, "dense_lu_solve returns success");
    CHECK(std::fabs(x[0] - 1) < 1e-9, "LU solves x0");
    CHECK(std::fabs(x[1] - 2) < 1e-9, "LU solves x1");
    CHECK(std::fabs(x[2] - 3) < 1e-9, "LU solves x2");

    // multi-RHS: identity columns recover the inverse, A*inv(A) = I.
    double A2[4] = {4, 3, 6, 3};
    double B2[4] = {1, 0, 0, 1};   // two RHS columns (col-major blocks)
    double X2[4] = {0, 0, 0, 0};
    CHECK(dc::dense_lu_solve(A2, 2, B2, 2, X2), "multi-RHS solve succeeds");
    // inv(A2) = 1/det [ 3 -3; -6 4 ], det = 4*3 - 3*6 = -6
    CHECK(std::fabs(X2[0] - (3.0 / -6.0)) < 1e-9, "inverse col0 row0");
    CHECK(std::fabs(X2[1] - (-6.0 / -6.0)) < 1e-9, "inverse col0 row1");

    // bad sizes fail cleanly
    CHECK(!dc::dense_lu_solve(A, 0, b, 1, x), "n=0 rejected");
}

// U2: mechatronic document — v2 project migrates forward to schema v3, the
// electronic board is preserved, the migration is recorded in the rationale
// trail, and a v3 file round-trips through the dc_doc_* boundary.
static void testMechatronicDocMigration() {
    const std::string v2 = "u2_v2_tmp.dsproj";
    const std::string v3 = "u2_v3_tmp.dsproj";
    {
        std::ofstream f(v2, std::ios::binary | std::ios::trunc);
        f << "{\n  \"version\": 2,\n  \"board_width_mm\": 100,\n  \"net_table\": []\n}\n";
    }

    DcDocHandle h = nullptr;
    CHECK(dc_doc_open(v2.c_str(), &h) == DC_OK && h != nullptr, "open + migrate v2 project");
    CHECK(dc_doc_version(h) == 3, "v2 migrates to schema v3");
    CHECK(dc_doc_save(h, v3.c_str()) == DC_OK, "save migrated v3 document");
    dc_doc_destroy(h);

    std::ifstream in(v3, std::ios::binary);
    const std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    CHECK(text.find("design-studio.project/3") != std::string::npos, "saved as v3 format");
    CHECK(text.find("board_width_mm") != std::string::npos, "electronic board preserved");
    CHECK(text.find("Migrated from project v2") != std::string::npos, "rationale records migration");

    DcDocHandle h2 = nullptr;
    CHECK(dc_doc_open(v3.c_str(), &h2) == DC_OK, "reopen v3 file");
    CHECK(dc_doc_version(h2) == 3, "v3 loads as v3 (not re-migrated)");
    dc_doc_destroy(h2);

    DcDocHandle bad = nullptr;
    CHECK(dc_doc_open("u2_does_not_exist.dsproj", &bad) == DC_ERR_IO, "missing file -> DC_ERR_IO");
    CHECK(dc_doc_version(nullptr) == DC_ERR_INVALID_HANDLE, "null handle guarded");

    std::remove(v2.c_str());
    std::remove(v3.c_str());
}

// ------------------------------------------------------------------ main ----

int main() {
    testNetIdentityStability();
    testItemRemovalIndex();
    testNetClassRules();
    testRotatedPadGeometry();
    testBottomFootprintMirrorsLocalY();
    testShapeAccuratePadClearanceAndConnectivity();
    testCustomCopperRegionGeometry();
    testRouteAroundObstacle();
    testRouteFailureReasons();
    testMultiLayerRouteWithVia();
    testMicroviaRouting();
    testRouterKeepsDrillWallOnSameNet();
    testRouteEndpointExactness();
    testPolygonOutlineCutoutAndSweptRouting();
    testRouterPreventsDiagonalCornerCutting();
    testRuleAreaPrecedenceAndProhibitions();
    testRouterRotationAwareness();
    testInteractiveRouteCommitCancelAndSpringback();
    testInteractiveRouteRollbackAndRecursiveShove();
    testInteractiveRouteCapiCompatibility();
    testInteractiveRoutePreviewLatency();
    testDrcClearanceFamilies();
    testDrcLayerAwareness();
    testDrcAnnularAndDrill();
    testExplicitViaTechnologyDrillFloors();
    testManufacturingDrcExtensions();
    testCourtyardAndBodyClearance();
    testRightAngleBendQualityRule();
    testStructuredPlacementDrc();
    testLayerViaAndZonePolicies();
    testBottomSmdMapsToLastCopperLayer();
    testManufacturingCapiV2Contract();
    testDrcConnectivity();
    testDrcSkew();
    testDrcNetClassClearance();
    testClassPairAndPadRulePrecedence();
    testPourGeneration();
    testPourConnectsSameNet();
    testPourClipsToAuthoritativeZonePolygon();
    testMathEngine();
    testMicrostripCanonical();
    testStriplineCanonical();
    testDifferentialPairs();
    testSkinAndLosses();
    testCurrentCapacity();
    testViaPhysics();
    testCouplingAndPlanes();
    testScalabilitySmoke();
    testDenseLuSolve();
    testMechatronicDocMigration();

    if (g_failures == 0) {
        std::printf("All core tests passed (%d checks).\n", g_checks);
        return 0;
    }
    std::printf("%d/%d checks FAILED.\n", g_failures, g_checks);
    return 1;
}
