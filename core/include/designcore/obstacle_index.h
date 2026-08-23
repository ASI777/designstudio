#pragma once
// Spatial index over board copper: every pad, trace and via flattened into an
// obstacle record with world geometry, net, layer mask and AABB, bucketed in a
// uniform grid. Router, DRC and pour generation all query through this instead
// of scanning every item — O(1) neighbourhood lookups instead of O(n) sweeps,
// which is what makes 5,000-part datacenter boards tractable.

#include "pcb_model.h"
#include <cstdint>
#include <functional>
#include <vector>

namespace dc {

struct Obstacle {
    enum class Kind : std::uint8_t { PadRect, PadRounded, Polygon, Segment, Circle };
    Kind kind = Kind::Segment;
    int netId = -1;
    layermask_t layers = 0;       // copper layers this obstacle occupies
    Rect bbox;                    // world AABB (geometry only, no clearance)
    id_t ownerId = 0;             // footprint / trace / via id (DRC reporting)
    // geometry payload
    Quad quad{};                  // PadRect
    Vec2 a{}, b{};                // Segment endpoints
    coord_t halfWidth = 0;        // Segment: width/2 ; Circle: radius
    Vec2 center{};                // Circle
    bool coreIsQuad = false;      // PadRounded: quad core, otherwise a..b core
    std::vector<Vec2> polygon;     // Polygon: outer boundary in world space
    std::vector<Vec2> hole;        // Polygon: optional cutout
    coord_t drill = 0;            // hole diameter (TH pads, vias), 0 = none
    // reporting context
    const Footprint* fp = nullptr;
    const Pad* pad = nullptr;
    bool isPour = false;
    // A component courtyard: blocks VIAS (vias under a body wick solder / short to
    // the part) but NOT traces (which may legitimately run under a body). Modelled
    // as a PadRect-shaped zone the trace router ignores and the via router honours.
    bool viaKeepout = false;
};

class ObstacleIndex {
public:
    // cell: grid bucket size; pick ~ the largest common clearance+width scale.
    explicit ObstacleIndex(const Board& board, coord_t cell = 2 * NM_PER_MM);

    const std::vector<Obstacle>& obstacles() const { return obstacles_; }
    coord_t maxDrillRadius() const { return maxDrillRadius_; }

    // Visit obstacles whose AABB intersects r (each at most once).
    void query(const Rect& r, const std::function<void(const Obstacle&)>& f) const;

    // Minimum copper-to-point distance helpers (0 when inside).
    static double distanceTo(const Obstacle& o, Vec2 p);
    static double distanceTo(const Obstacle& o, Vec2 segA, Vec2 segB);
    static double distanceBetween(const Obstacle& a, const Obstacle& b);

private:
    void insert(std::size_t idx);
    std::int64_t key(std::int64_t cx, std::int64_t cy) const { return cx * 73856093 ^ cy * 19349663; }

    coord_t cell_;
    coord_t maxDrillRadius_ = 0;
    std::vector<Obstacle> obstacles_;
    std::unordered_map<std::int64_t, std::vector<std::uint32_t>> grid_;
    mutable std::vector<std::uint32_t> stamp_;   // dedup marker per query
    mutable std::uint32_t stampGen_ = 0;
};

} // namespace dc
