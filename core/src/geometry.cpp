#include "designcore/geometry.h"
#include <algorithm>

namespace dc {

double distancePointSegment(Vec2 p, Vec2 a, Vec2 b) {
    const double abx = double(b.x - a.x), aby = double(b.y - a.y);
    const double apx = double(p.x - a.x), apy = double(p.y - a.y);
    const double len2 = abx * abx + aby * aby;
    double t = len2 > 0 ? std::clamp((apx * abx + apy * aby) / len2, 0.0, 1.0) : 0.0;
    const double cx = double(a.x) + t * abx - double(p.x);
    const double cy = double(a.y) + t * aby - double(p.y);
    return std::hypot(cx, cy);
}

static double cross(Vec2 o, Vec2 a, Vec2 b) {
    return double(a.x - o.x) * double(b.y - o.y) - double(a.y - o.y) * double(b.x - o.x);
}

double distanceSegmentSegment(Vec2 a1, Vec2 a2, Vec2 b1, Vec2 b2) {
    // Proper intersection => distance 0
    const double d1 = cross(b1, b2, a1), d2 = cross(b1, b2, a2);
    const double d3 = cross(a1, a2, b1), d4 = cross(a1, a2, b2);
    if (((d1 > 0) != (d2 > 0)) && ((d3 > 0) != (d4 > 0))) return 0.0;
    return std::min({distancePointSegment(a1, b1, b2), distancePointSegment(a2, b1, b2),
                     distancePointSegment(b1, a1, a2), distancePointSegment(b2, a1, a2)});
}

// ---- rotated pad quads ----

Rect Quad::bbox() const {
    Rect r{{p[0].x, p[0].y}, {p[0].x, p[0].y}};
    for (int i = 1; i < 4; ++i) {
        r.min.x = std::min(r.min.x, p[i].x); r.min.y = std::min(r.min.y, p[i].y);
        r.max.x = std::max(r.max.x, p[i].x); r.max.y = std::max(r.max.y, p[i].y);
    }
    return r;
}

Quad makePadQuad(Vec2 c, Vec2 size, double deg) {
    const double r = deg * 3.14159265358979323846 / 180.0;
    const double co = std::cos(r), si = std::sin(r);
    const double hw = double(size.x) / 2.0, hh = double(size.y) / 2.0;
    const double cx[4] = {-hw, hw, hw, -hw};
    const double cy[4] = {-hh, -hh, hh, hh};
    Quad q;
    for (int i = 0; i < 4; ++i) {
        q.p[i] = {coord_t(std::llround(double(c.x) + cx[i] * co - cy[i] * si)),
                  coord_t(std::llround(double(c.y) + cx[i] * si + cy[i] * co))};
    }
    return q;
}

bool pointInQuad(Vec2 pt, const Quad& q) {
    // Convex polygon: point is inside iff it is on the same side of every edge.
    bool pos = false, neg = false;
    for (int i = 0; i < 4; ++i) {
        const double c = cross(q.p[i], q.p[(i + 1) % 4], pt);
        if (c > 0) pos = true;
        if (c < 0) neg = true;
    }
    return !(pos && neg);
}

double distancePointQuad(Vec2 pt, const Quad& q) {
    if (pointInQuad(pt, q)) return 0.0;
    double d = distancePointSegment(pt, q.p[0], q.p[1]);
    for (int i = 1; i < 4; ++i)
        d = std::min(d, distancePointSegment(pt, q.p[i], q.p[(i + 1) % 4]));
    return d;
}

double distanceSegmentQuad(Vec2 a, Vec2 b, const Quad& q) {
    if (pointInQuad(a, q) || pointInQuad(b, q)) return 0.0;
    double d = distanceSegmentSegment(a, b, q.p[0], q.p[1]);
    for (int i = 1; i < 4; ++i)
        d = std::min(d, distanceSegmentSegment(a, b, q.p[i], q.p[(i + 1) % 4]));
    return d;
}

double distanceQuadQuad(const Quad& a, const Quad& b) {
    // Containment (one fully inside the other) or edge proximity.
    if (pointInQuad(a.p[0], b) || pointInQuad(b.p[0], a)) return 0.0;
    double d = 1e300;
    for (int i = 0; i < 4; ++i)
        d = std::min(d, distanceSegmentQuad(a.p[i], a.p[(i + 1) % 4], b));
    return d;
}

bool pointInPolygon(Vec2 pt, const std::vector<Vec2>& ring) {
    if (ring.size() < 3) return false;
    if (distancePointPolygonBoundary(pt, ring) < 0.5) return true;
    bool inside = false;
    for (std::size_t i = 0, j = ring.size() - 1; i < ring.size(); j = i++) {
        const Vec2 a = ring[i], b = ring[j];
        if ((a.y > pt.y) == (b.y > pt.y)) continue;
        const double x = double(b.x - a.x) * double(pt.y - a.y)
                       / double(b.y - a.y) + double(a.x);
        if (double(pt.x) < x) inside = !inside;
    }
    return inside;
}

double distancePointPolygonBoundary(Vec2 pt, const std::vector<Vec2>& ring) {
    if (ring.size() < 2) return 1e300;
    double d = 1e300;
    for (std::size_t i = 0; i < ring.size(); ++i)
        d = std::min(d, distancePointSegment(pt, ring[i], ring[(i + 1) % ring.size()]));
    return d;
}

double distanceSegmentPolygonBoundary(Vec2 a, Vec2 b, const std::vector<Vec2>& ring) {
    if (ring.size() < 2) return 1e300;
    double d = 1e300;
    for (std::size_t i = 0; i < ring.size(); ++i)
        d = std::min(d, distanceSegmentSegment(a, b, ring[i], ring[(i + 1) % ring.size()]));
    return d;
}

} // namespace dc
