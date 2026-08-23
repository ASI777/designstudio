#pragma once
// Design rule check. Layer-aware, net-class-aware, spatially indexed.
// Every violation carries the rule, a human-readable message, a location and
// the offending item ids — nothing is thrown away at the API boundary.

#include "pcb_model.h"

namespace dc {

// Stable rule codes — mirrored on the C# side. Do not renumber.
enum class DrcRule : std::int32_t {
    TraceTraceClearance = 1,
    PadTraceClearance   = 2,
    PadPadClearance     = 3,
    BoardEdge           = 4,
    TraceWidth          = 5,
    DrillSize           = 6,
    ViaTraceClearance   = 7,
    ViaPadClearance     = 8,
    ViaViaClearance     = 9,
    AnnularRing         = 10,
    DrillToDrill        = 11,
    UnconnectedNet      = 12,
    SkewExceeded        = 13,
    UnassignedCopper    = 14,
    CopperToEdge        = 15,
    CopperToHole        = 16,
    CourtyardOverlap    = 17,
    RuleAreaViolation   = 18,
    HeightConstraint    = 19,
    ThermalSpacing      = 20,
    TestAccess          = 21,
    LayerPolicyViolation = 22,
    ViaPolicyViolation   = 23,
    ZoneValidity         = 24,
    RightAngleBend       = 25,
};

struct DrcViolation {
    DrcRule rule = DrcRule::TraceTraceClearance;
    std::string message;
    Vec2 location;
    id_t itemA = 0, itemB = 0;
};

struct DrcOptions {
    coord_t defaultClearance = 200'000;   // 0.2 mm (net-class rules override)
    coord_t minTraceWidth    = 100'000;   // 0.1 mm
    coord_t minDrill         = 150'000;   // 0.15 mm (laser micro: down to 0.1)
    coord_t minAnnularRing   = 100'000;   // 0.1 mm
    coord_t minDrillToDrill  = 250'000;   // 0.25 mm wall between holes
    // Explicit laser microvias carry their own floors. Blind/buried vias are
    // not assumed to be laser formed and retain the mechanical constraints.
    coord_t minMicroviaDrill = 75'000;    // 0.075 mm
    coord_t minMicroviaWall  = 100'000;   // 0.1 mm wall between two microvias
    coord_t minCopperToEdge   = 250'000;   // 0.25 mm copper setback
    coord_t minCopperToHole   = 250'000;   // 0.25 mm to foreign plated holes
    coord_t minCourtyardClearance = 250'000;
    bool checkConnectivity   = true;      // unconnected-net islands
    bool checkSkew           = true;      // length matching per net class
    bool checkUnassignedCopper = true;    // traces/vias without a net
    bool checkRightAngles = true;         // report two-segment 90-degree bends
};

std::vector<DrcViolation> runDrc(const Board& board, const DrcOptions& opt = {});

// Stable summaries of the terminal-bearing copper components used by the
// connectivity DRC. Pure orphan trace components are intentionally excluded,
// matching netIslandCount(). The summaries let repair/inspection clients
// locate a disconnected component without reimplementing copper geometry.
struct NetIslandComponent {
    Vec2 anchor;
    id_t representativeOwnerId = 0;
    layermask_t layers = 0;
    std::int32_t obstacleCount = 0;
    std::int32_t terminalCount = 0;
};

std::vector<NetIslandComponent> netIslandComponents(const Board& board, int netId);

// Connectivity islands of one net (1 = fully connected, 0 = no copper).
int netIslandCount(const Board& board, int netId);

} // namespace dc
