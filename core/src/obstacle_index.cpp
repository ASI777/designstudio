#include "designcore/obstacle_index.h"
#include <algorithm>
#include <cmath>
#include <limits>

namespace dc {

namespace {
Vec2 rotateVector(Vec2 p, double deg) {
    const double r = deg * 3.14159265358979323846 / 180.0;
    const double c = std::cos(r), s = std::sin(r);
    return {coord_t(std::llround(p.x * c - p.y * s)),
            coord_t(std::llround(p.x * s + p.y * c))};
}

double radiusOf(const Obstacle& o) {
    return o.kind == Obstacle::Kind::PadRect ? 0.0 : double(o.halfWidth);
}

bool quadCore(const Obstacle& o) {
    return o.kind == Obstacle::Kind::PadRect
        || (o.kind == Obstacle::Kind::PadRounded && o.coreIsQuad);
}

bool pointInRing(Vec2 p, const std::vector<Vec2>& ring) {
    if (ring.size() < 3) return false;
    bool inside = false;
    for (std::size_t i = 0, j = ring.size() - 1; i < ring.size(); j = i++) {
        const Vec2 a = ring[i], b = ring[j];
        const bool crosses = ((a.y > p.y) != (b.y > p.y));
        if (!crosses) continue;
        const double x = double(b.x - a.x) * double(p.y - a.y) / double(b.y - a.y) + a.x;
        if (double(p.x) < x) inside = !inside;
    }
    return inside;
}

bool pointInCopperPolygon(Vec2 p, const Obstacle& o) {
    return pointInRing(p, o.polygon) && !pointInRing(p, o.hole);
}

double distancePointRing(Vec2 p, const std::vector<Vec2>& ring) {
    if (ring.empty()) return 1e300;
    double d = 1e300;
    for (std::size_t i = 0; i < ring.size(); ++i)
        d = std::min(d, distancePointSegment(p, ring[i], ring[(i + 1) % ring.size()]));
    return d;
}

double distancePointPolygon(Vec2 p, const Obstacle& o) {
    if (pointInCopperPolygon(p, o)) return 0.0;
    return std::min(distancePointRing(p, o.polygon), distancePointRing(p, o.hole));
}

double distanceSegmentRing(Vec2 a, Vec2 b, const std::vector<Vec2>& ring) {
    if (ring.empty()) return 1e300;
    double d = 1e300;
    for (std::size_t i = 0; i < ring.size(); ++i)
        d = std::min(d, distanceSegmentSegment(a, b, ring[i], ring[(i + 1) % ring.size()]));
    return d;
}

double distanceSegmentPolygon(Vec2 a, Vec2 b, const Obstacle& o) {
    if (pointInCopperPolygon(a, o) || pointInCopperPolygon(b, o)) return 0.0;
    return std::min(distanceSegmentRing(a, b, o.polygon), distanceSegmentRing(a, b, o.hole));
}

double distanceQuadPolygon(const Quad& q, const Obstacle& o) {
    for (Vec2 p : q.p) if (pointInCopperPolygon(p, o)) return 0.0;
    for (Vec2 p : o.polygon) if (pointInQuad(p, q)) return 0.0;
    double d = 1e300;
    for (int i = 0; i < 4; ++i)
        d = std::min(d, distanceSegmentPolygon(q.p[i], q.p[(i + 1) % 4], o));
    return d;
}

double distancePolygonPolygon(const Obstacle& a, const Obstacle& b) {
    for (Vec2 p : a.polygon) if (pointInCopperPolygon(p, b)) return 0.0;
    for (Vec2 p : b.polygon) if (pointInCopperPolygon(p, a)) return 0.0;
    double d = 1e300;
    for (std::size_t i = 0; i < a.polygon.size(); ++i)
        d = std::min(d, distanceSegmentPolygon(a.polygon[i],
                                               a.polygon[(i + 1) % a.polygon.size()], b));
    for (std::size_t i = 0; i < a.hole.size(); ++i)
        d = std::min(d, distanceSegmentPolygon(a.hole[i], a.hole[(i + 1) % a.hole.size()], b));
    return d;
}

void segmentCore(const Obstacle& o, Vec2& a, Vec2& b) {
    if (o.kind == Obstacle::Kind::Circle) a = b = o.center;
    else { a = o.a; b = o.b; }
}
}

ObstacleIndex::ObstacleIndex(const Board& board, coord_t cell) : cell_(std::max<coord_t>(cell, 100'000)) {
    // Flatten board copper into obstacle records.
    for (const auto& fp : board.footprints()) {
        Rect court{{ (std::numeric_limits<coord_t>::max)(),  (std::numeric_limits<coord_t>::max)() },
                   {(std::numeric_limits<coord_t>::min)(), (std::numeric_limits<coord_t>::min)() }};
        bool any = false;
        for (const auto& pad : fp.pads) {
            Obstacle o;
            o.netId = pad.netId;
            o.layers = board.padMask(fp, pad);
            const Vec2 center = fp.padWorldPos(pad);
            coord_t radius = 0;
            if (pad.shape == PadShape::Circle || pad.shape == PadShape::Oval)
                radius = std::min(pad.size.x, pad.size.y) / 2;
            else if (pad.shape == PadShape::RoundRect)
                radius = std::clamp(pad.cornerRadius, coord_t(0),
                                    std::min(pad.size.x, pad.size.y) / 2);

            if (radius <= 0) {
                o.kind = Obstacle::Kind::PadRect;
                o.quad = fp.padWorldQuad(pad);
                o.bbox = o.quad.bbox();
            } else {
                o.kind = Obstacle::Kind::PadRounded;
                o.halfWidth = radius;
                const coord_t cw = std::max<coord_t>(0, pad.size.x - 2 * radius);
                const coord_t ch = std::max<coord_t>(0, pad.size.y - 2 * radius);
                if (cw > 0 && ch > 0) {
                    o.coreIsQuad = true;
                    o.quad = makePadQuad(center, {cw, ch}, fp.rotationDeg);
                    o.bbox = o.quad.bbox().inflated(radius);
                } else {
                    const Vec2 half = cw > 0 ? Vec2{cw / 2, 0} : Vec2{0, ch / 2};
                    const Vec2 worldHalf = rotateVector(half, fp.rotationDeg);
                    o.a = center - worldHalf;
                    o.b = center + worldHalf;
                    o.bbox = {{std::min(o.a.x, o.b.x) - radius,
                               std::min(o.a.y, o.b.y) - radius},
                              {std::max(o.a.x, o.b.x) + radius,
                               std::max(o.a.y, o.b.y) + radius}};
                }
            }
            o.ownerId = fp.id;
            o.fp = &fp;
            o.pad = &pad;
            o.drill = pad.throughHole ? pad.drill : 0;
            if (o.drill > 0) o.center = fp.padWorldPos(pad);
            maxDrillRadius_ = std::max(maxDrillRadius_, o.drill / 2);
            obstacles_.push_back(o);
            const Rect pb = o.bbox;                  // accumulate pad bbox → courtyard
            court.min.x = std::min(court.min.x, pb.min.x); court.min.y = std::min(court.min.y, pb.min.y);
            court.max.x = std::max(court.max.x, pb.max.x); court.max.y = std::max(court.max.y, pb.max.y);
            any = true;
        }
        for (const auto& region : fp.regions) {
            Obstacle o;
            o.kind = Obstacle::Kind::Polygon;
            o.netId = region.netId;
            o.layers = layerBit(fp.side);
            o.ownerId = fp.id;
            o.fp = &fp;
            o.polygon = fp.regionWorldPoints(region);
            o.hole = fp.regionWorldHole(region);
            if (o.polygon.size() < 3) continue;
            o.bbox = {{o.polygon[0].x, o.polygon[0].y}, {o.polygon[0].x, o.polygon[0].y}};
            for (Vec2 p : o.polygon) {
                o.bbox.min.x = std::min(o.bbox.min.x, p.x); o.bbox.min.y = std::min(o.bbox.min.y, p.y);
                o.bbox.max.x = std::max(o.bbox.max.x, p.x); o.bbox.max.y = std::max(o.bbox.max.y, p.y);
            }
            obstacles_.push_back(o);
            court.min.x = std::min(court.min.x, o.bbox.min.x); court.min.y = std::min(court.min.y, o.bbox.min.y);
            court.max.x = std::max(court.max.x, o.bbox.max.x); court.max.y = std::max(court.max.y, o.bbox.max.y);
            any = true;
        }
        const auto courtyardPts = fp.courtyardWorldPoints();
        const bool hasBody = fp.bodySize.x > 0 && fp.bodySize.y > 0;
        // Exact courtyard/body via-keepout when supplied; pad bounding box is a
        // compatibility fallback for legacy footprints without assembly data.
        if (any || hasBody || courtyardPts.size() >= 3) {
            Obstacle c;
            c.netId = -1;
            c.layers = board.allLayersMask();
            if (courtyardPts.size() >= 3) {
                c.kind = Obstacle::Kind::Polygon;
                c.polygon = courtyardPts;
                c.bbox = {{courtyardPts[0].x, courtyardPts[0].y},
                          {courtyardPts[0].x, courtyardPts[0].y}};
                for (Vec2 p : courtyardPts) {
                    c.bbox.min.x = std::min(c.bbox.min.x, p.x); c.bbox.min.y = std::min(c.bbox.min.y, p.y);
                    c.bbox.max.x = std::max(c.bbox.max.x, p.x); c.bbox.max.y = std::max(c.bbox.max.y, p.y);
                }
            } else if (hasBody) {
                c.kind = Obstacle::Kind::PadRect;
                c.quad = fp.bodyWorldQuad();
                c.bbox = c.quad.bbox();
            } else {
                c.kind = Obstacle::Kind::PadRect;
                c.quad = Quad{{ court.min, {court.max.x, court.min.y},
                                court.max, {court.min.x, court.max.y} }};
                c.bbox = court;
            }
            c.ownerId = fp.id;
            c.fp = &fp;
            c.viaKeepout = true;
            obstacles_.push_back(c);
        }
    }
    for (const auto& t : board.traces()) {
        Obstacle o;
        o.kind = Obstacle::Kind::Segment;
        o.netId = t.netId;
        o.layers = layerBit(t.layer);
        o.a = t.a; o.b = t.b;
        o.halfWidth = t.width / 2;
        o.bbox = {{std::min(t.a.x, t.b.x) - o.halfWidth, std::min(t.a.y, t.b.y) - o.halfWidth},
                  {std::max(t.a.x, t.b.x) + o.halfWidth, std::max(t.a.y, t.b.y) + o.halfWidth}};
        o.ownerId = t.id;
        o.isPour = t.isPour;
        obstacles_.push_back(o);
    }
    for (const auto& v : board.vias()) {
        Obstacle o;
        o.kind = Obstacle::Kind::Circle;
        o.netId = v.netId;
        o.layers = v.mask();
        o.center = v.pos;
        o.halfWidth = v.diameter / 2;
        o.drill = v.drill;
        maxDrillRadius_ = std::max(maxDrillRadius_, o.drill / 2);
        o.bbox = {{v.pos.x - o.halfWidth, v.pos.y - o.halfWidth},
                  {v.pos.x + o.halfWidth, v.pos.y + o.halfWidth}};
        o.ownerId = v.id;
        obstacles_.push_back(o);
    }

    grid_.reserve(obstacles_.size() * 2);
    for (std::size_t i = 0; i < obstacles_.size(); ++i) insert(i);
    stamp_.assign(obstacles_.size(), 0);
}

void ObstacleIndex::insert(std::size_t idx) {
    const Rect& r = obstacles_[idx].bbox;
    const std::int64_t cx0 = r.min.x / cell_ - 1, cx1 = r.max.x / cell_ + 1;
    const std::int64_t cy0 = r.min.y / cell_ - 1, cy1 = r.max.y / cell_ + 1;
    for (std::int64_t cy = cy0; cy <= cy1; ++cy)
        for (std::int64_t cx = cx0; cx <= cx1; ++cx)
            grid_[key(cx, cy)].push_back(std::uint32_t(idx));
}

void ObstacleIndex::query(const Rect& r, const std::function<void(const Obstacle&)>& f) const {
    ++stampGen_;
    if (stampGen_ == 0) { std::fill(stamp_.begin(), stamp_.end(), 0); stampGen_ = 1; }
    const std::int64_t cx0 = r.min.x / cell_ - 1, cx1 = r.max.x / cell_ + 1;
    const std::int64_t cy0 = r.min.y / cell_ - 1, cy1 = r.max.y / cell_ + 1;
    for (std::int64_t cy = cy0; cy <= cy1; ++cy)
        for (std::int64_t cx = cx0; cx <= cx1; ++cx) {
            auto it = grid_.find(key(cx, cy));
            if (it == grid_.end()) continue;
            for (std::uint32_t idx : it->second) {
                if (stamp_[idx] == stampGen_) continue;
                stamp_[idx] = stampGen_;
                if (obstacles_[idx].bbox.intersects(r)) f(obstacles_[idx]);
            }
        }
}

double ObstacleIndex::distanceTo(const Obstacle& o, Vec2 p) {
    switch (o.kind) {
        case Obstacle::Kind::PadRect: return distancePointQuad(p, o.quad);
        case Obstacle::Kind::PadRounded:
            return std::max(0.0, (o.coreIsQuad ? distancePointQuad(p, o.quad)
                                                  : distancePointSegment(p, o.a, o.b))
                                  - double(o.halfWidth));
        case Obstacle::Kind::Polygon: return distancePointPolygon(p, o);
        case Obstacle::Kind::Segment: return std::max(0.0, distancePointSegment(p, o.a, o.b) - double(o.halfWidth));
        case Obstacle::Kind::Circle:  return std::max(0.0, (p - o.center).length() - double(o.halfWidth));
    }
    return 0.0;
}

double ObstacleIndex::distanceTo(const Obstacle& o, Vec2 segA, Vec2 segB) {
    switch (o.kind) {
        case Obstacle::Kind::PadRect: return distanceSegmentQuad(segA, segB, o.quad);
        case Obstacle::Kind::PadRounded:
            return std::max(0.0, (o.coreIsQuad ? distanceSegmentQuad(segA, segB, o.quad)
                                                  : distanceSegmentSegment(segA, segB, o.a, o.b))
                                  - double(o.halfWidth));
        case Obstacle::Kind::Polygon: return distanceSegmentPolygon(segA, segB, o);
        case Obstacle::Kind::Segment: return std::max(0.0, distanceSegmentSegment(segA, segB, o.a, o.b) - double(o.halfWidth));
        case Obstacle::Kind::Circle:  return std::max(0.0, distancePointSegment(o.center, segA, segB) - double(o.halfWidth));
    }
    return 0.0;
}

double ObstacleIndex::distanceBetween(const Obstacle& x, const Obstacle& y) {
    if (x.kind == Obstacle::Kind::Polygon && y.kind == Obstacle::Kind::Polygon)
        return distancePolygonPolygon(x, y);
    if (x.kind == Obstacle::Kind::Polygon || y.kind == Obstacle::Kind::Polygon) {
        const Obstacle& poly = x.kind == Obstacle::Kind::Polygon ? x : y;
        const Obstacle& other = x.kind == Obstacle::Kind::Polygon ? y : x;
        double core;
        if (quadCore(other)) core = distanceQuadPolygon(other.quad, poly);
        else {
            Vec2 a, b; segmentCore(other, a, b);
            core = distanceSegmentPolygon(a, b, poly);
        }
        return std::max(0.0, core - radiusOf(other));
    }
    const bool xq = quadCore(x), yq = quadCore(y);
    double core;
    if (xq && yq) {
        core = distanceQuadQuad(x.quad, y.quad);
    } else if (xq) {
        Vec2 a, b; segmentCore(y, a, b);
        core = distanceSegmentQuad(a, b, x.quad);
    } else if (yq) {
        Vec2 a, b; segmentCore(x, a, b);
        core = distanceSegmentQuad(a, b, y.quad);
    } else {
        Vec2 xa, xb, ya, yb;
        segmentCore(x, xa, xb); segmentCore(y, ya, yb);
        core = distanceSegmentSegment(xa, xb, ya, yb);
    }
    return std::max(0.0, core - radiusOf(x) - radiusOf(y));
}

} // namespace dc
