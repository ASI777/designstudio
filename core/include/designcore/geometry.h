#pragma once
// Basic 2D/3D geometry primitives used across the PCB and mechanical kernels.
// Units: all PCB coordinates are in nanometres (int64) to avoid floating-point
// drift in DRC — the same approach KiCad and commercial EDA tools use.

#include <cstdint>
#include <cmath>
#include <vector>

namespace dc {

using coord_t = std::int64_t;            // nanometres
constexpr coord_t NM_PER_MM = 1'000'000;

struct Vec2 {
    coord_t x = 0, y = 0;
    Vec2 operator+(Vec2 o) const { return {x + o.x, y + o.y}; }
    Vec2 operator-(Vec2 o) const { return {x - o.x, y - o.y}; }
    bool operator==(const Vec2&) const = default;
    double length() const { return std::hypot(double(x), double(y)); }
};

struct Vec3 {
    double x = 0, y = 0, z = 0;          // mm, for the 3D kernel
};

struct Rect {
    Vec2 min, max;
    bool intersects(const Rect& o) const {
        return min.x <= o.max.x && max.x >= o.min.x &&
               min.y <= o.max.y && max.y >= o.min.y;
    }
    bool contains(Vec2 p) const {
        return p.x >= min.x && p.x <= max.x && p.y >= min.y && p.y <= max.y;
    }
    Rect inflated(coord_t d) const { return {{min.x - d, min.y - d}, {max.x + d, max.y + d}}; }
};

// Distance from point to segment — workhorse of clearance checking.
double distancePointSegment(Vec2 p, Vec2 a, Vec2 b);

// Minimum distance between two segments (for trace-to-trace clearance).
double distanceSegmentSegment(Vec2 a1, Vec2 a2, Vec2 b1, Vec2 b2);

// Convex quad = rotated rectangular pad in world space. Corners in order.
struct Quad {
    Vec2 p[4];
    Rect bbox() const;
};

// World-space quad of a rectangular pad: centre c, size w x h, rotated by deg.
Quad makePadQuad(Vec2 c, Vec2 size, double deg);

bool pointInQuad(Vec2 pt, const Quad& q);

// 0 if touching/overlapping/contained, else minimum separation distance.
double distancePointQuad(Vec2 pt, const Quad& q);
double distanceSegmentQuad(Vec2 a, Vec2 b, const Quad& q);
double distanceQuadQuad(const Quad& a, const Quad& b);

// Simple polygon/ring helpers used by board outlines, cutouts and rule areas.
// Rings may be convex or concave and are implicitly closed.
bool pointInPolygon(Vec2 pt, const std::vector<Vec2>& ring);
double distancePointPolygonBoundary(Vec2 pt, const std::vector<Vec2>& ring);
double distanceSegmentPolygonBoundary(Vec2 a, Vec2 b, const std::vector<Vec2>& ring);

} // namespace dc
