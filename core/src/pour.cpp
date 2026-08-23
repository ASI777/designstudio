#include "designcore/pour.h"
#include "designcore/obstacle_index.h"
#include <algorithm>
#include <cmath>

namespace dc {

namespace {

struct Interval { coord_t lo, hi; };

void addBlocked(std::vector<Interval>& blocked, coord_t lo, coord_t hi) {
    if (lo < hi) blocked.push_back({lo, hi});
}

std::vector<Interval> ringInterior(const std::vector<Vec2>& ring, coord_t y) {
    std::vector<double> xs;
    for (std::size_t i = 0; i < ring.size(); ++i) {
        const Vec2 a = ring[i], b = ring[(i + 1) % ring.size()];
        if ((a.y > y) == (b.y > y)) continue;
        xs.push_back(double(a.x) + double(y - a.y) * double(b.x - a.x)
                                  / double(b.y - a.y));
    }
    std::sort(xs.begin(), xs.end());
    std::vector<Interval> out;
    for (std::size_t i = 1; i < xs.size(); i += 2)
        out.push_back({coord_t(std::llround(xs[i - 1])), coord_t(std::llround(xs[i]))});
    return out;
}

// x-interval blocked by an obstacle on scanline y, with the stroke's copper
// half-width + clearance already folded into `expand`.
void blockFor(const Obstacle& o, coord_t y, coord_t expand, std::vector<Interval>& blocked) {
    switch (o.kind) {
        case Obstacle::Kind::Polygon: {
            // Exact boundary dilation; the filled interior is conservatively
            // represented by its scanline crossings. A custom copper region's
            // inner hole remains conservatively over-cleared here; board and
            // zone polygon cutouts are clipped explicitly by generatePour.
            std::vector<double> xs;
            for (std::size_t i = 0; i < o.polygon.size(); ++i) {
                const Vec2 a = o.polygon[i], b = o.polygon[(i + 1) % o.polygon.size()];
                if ((a.y > y) != (b.y > y))
                    xs.push_back(double(a.x) + double(y - a.y) * double(b.x - a.x) / double(b.y - a.y));
                Obstacle edge;
                edge.kind = Obstacle::Kind::Segment; edge.a = a; edge.b = b;
                blockFor(edge, y, expand, blocked);
            }
            std::sort(xs.begin(), xs.end());
            for (std::size_t i = 1; i < xs.size(); i += 2)
                addBlocked(blocked, coord_t(xs[i - 1]), coord_t(xs[i]));
            return;
        }
        case Obstacle::Kind::PadRounded: {
            // A rounded pad is its core dilated by halfWidth. Reuse the exact
            // core scan conversion with that radius folded into expansion.
            Obstacle core = o;
            if (o.coreIsQuad) {
                core.kind = Obstacle::Kind::PadRect;
                blockFor(core, y, expand + o.halfWidth, blocked);
            } else {
                core.kind = Obstacle::Kind::Segment;
                core.halfWidth = o.halfWidth;
                blockFor(core, y, expand, blocked);
            }
            return;
        }
        case Obstacle::Kind::Circle: {
            const double rTot = double(o.halfWidth + expand);
            const double dy = double(y - o.center.y);
            if (std::abs(dy) >= rTot) return;
            const double dx = std::sqrt(rTot * rTot - dy * dy);
            addBlocked(blocked, coord_t(o.center.x - dx), coord_t(o.center.x + dx));
            return;
        }
        case Obstacle::Kind::Segment: {
            // capsule vs horizontal band: clip the centreline to the band
            // [y-r, y+r], then block the x-extent of the clip ± r.
            const double r = double(o.halfWidth + expand);
            double ay = double(o.a.y), by = double(o.b.y);
            double ax = double(o.a.x), bx = double(o.b.x);
            double t0 = 0, t1 = 1;
            if (std::abs(by - ay) < 1e-9) {
                if (std::abs(double(y) - ay) >= r) return;
            } else {
                double tA = (double(y) - r - ay) / (by - ay);
                double tB = (double(y) + r - ay) / (by - ay);
                t0 = std::max(0.0, std::min(tA, tB));
                t1 = std::min(1.0, std::max(tA, tB));
                if (t0 > t1) return;
            }
            const double x0 = ax + (bx - ax) * t0, x1 = ax + (bx - ax) * t1;
            addBlocked(blocked, coord_t(std::min(x0, x1) - r), coord_t(std::max(x0, x1) + r));
            return;
        }
        case Obstacle::Kind::PadRect: {
            // quad vs band [y-e, y+e]: x-extent of edge crossings + contained corners
            const double e = double(expand);
            const double yLo = double(y) - e, yHi = double(y) + e;
            double xMin = 1e300, xMax = -1e300;
            for (int i = 0; i < 4; ++i) {
                const Vec2 p1 = o.quad.p[i], p2 = o.quad.p[(i + 1) % 4];
                const double p1y = double(p1.y), p2y = double(p2.y);
                // corner inside band
                if (p1y >= yLo && p1y <= yHi) {
                    xMin = std::min(xMin, double(p1.x));
                    xMax = std::max(xMax, double(p1.x));
                }
                // edge crossing band boundaries
                for (double yb : {yLo, yHi}) {
                    if ((p1y - yb) * (p2y - yb) < 0) {
                        const double t = (yb - p1y) / (p2y - p1y);
                        const double x = double(p1.x) + t * (double(p2.x) - double(p1.x));
                        xMin = std::min(xMin, x);
                        xMax = std::max(xMax, x);
                    }
                }
            }
            if (xMin > xMax) return;
            addBlocked(blocked, coord_t(xMin - e), coord_t(xMax + e));
            return;
        }
    }
}

} // namespace

PourResult generatePour(const Board& board, const PourRequest& req) {
    PourResult res;
    if (req.layer < 0 || req.layer >= board.copperLayerCount || req.lineWidth <= 0) return res;

    ObstacleIndex index(board);

    // Stay inside both the requested region and the board outline.
    Rect region = req.region;
    region.min.x = std::max(region.min.x, board.outline.min.x);
    region.min.y = std::max(region.min.y, board.outline.min.y);
    region.max.x = std::min(region.max.x, board.outline.max.x);
    region.max.y = std::min(region.max.y, board.outline.max.y);
    if (region.min.x >= region.max.x || region.min.y >= region.max.y) return res;

    const coord_t half = req.lineWidth / 2;
    const coord_t edge = half + req.clearance;          // copper-to-region/board boundary
    const coord_t pitch = std::max<coord_t>(coord_t(req.lineWidth * 4 / 5), 50'000);
    const coord_t expand = req.clearance + half;        // gap + stroke half-width
    // Board edges and routed cutouts are manufacturing boundaries, not merely
    // clipping polygons. Keep the requested copper clearance outside the
    // stroke itself, just as for foreign copper obstacles.
    const coord_t edgeExpand = req.clearance + half;
    const layermask_t layerMask = layerBit(req.layer);

    for (coord_t y = region.min.y + edge; y <= region.max.y - edge; y += pitch) {
        std::vector<Interval> blocked;
        const coord_t xBegin = region.min.x + edge;
        const coord_t xLimit = region.max.x - edge;

        if (board.outlinePolygon.size() >= 3) {
            const auto inside = ringInterior(board.outlinePolygon, y);
            coord_t cursor = xBegin;
            for (const auto& interval : inside) {
                const coord_t lo = std::max(xBegin, interval.lo);
                const coord_t hi = std::min(xLimit, interval.hi);
                addBlocked(blocked, cursor, lo);
                cursor = std::max(cursor, hi);
            }
            addBlocked(blocked, cursor, xLimit);
            // Keep the whole stroke, not just its centreline, inside the edge.
            for (std::size_t i = 0; i < board.outlinePolygon.size(); ++i) {
                Obstacle boundary;
                boundary.kind = Obstacle::Kind::Segment;
                boundary.a = board.outlinePolygon[i];
                boundary.b = board.outlinePolygon[(i + 1) % board.outlinePolygon.size()];
                blockFor(boundary, y, edgeExpand, blocked);
            }
        }
        if (req.zonePolygon.size() >= 3) {
            const auto inside = ringInterior(req.zonePolygon, y);
            coord_t cursor = xBegin;
            for (const auto& interval : inside) {
                const coord_t lo = std::max(xBegin, interval.lo);
                const coord_t hi = std::min(xLimit, interval.hi);
                addBlocked(blocked, cursor, lo);
                cursor = std::max(cursor, hi);
            }
            addBlocked(blocked, cursor, xLimit);
            for (std::size_t i = 0; i < req.zonePolygon.size(); ++i) {
                Obstacle boundary;
                boundary.kind = Obstacle::Kind::Segment;
                boundary.a = req.zonePolygon[i];
                boundary.b = req.zonePolygon[(i + 1) % req.zonePolygon.size()];
                blockFor(boundary, y, half, blocked);
            }
        }
        for (const auto& cutout : board.cutouts) {
            Obstacle voidShape;
            voidShape.kind = Obstacle::Kind::Polygon;
            voidShape.polygon = cutout;
            blockFor(voidShape, y, edgeExpand, blocked);
        }
        // probe band: tallest obstacle influence is its own size (in bbox) + expand
        const Rect band{{region.min.x, y - expand}, {region.max.x, y + expand}};
        index.query(band, [&](const Obstacle& o) {
            if (o.viaKeepout) return;                           // courtyard: not copper
            if (o.netId == req.netId && o.drill <= 0) return;   // same net: connect
            // Copper (or a drill) only affects this layer if the item spans it.
            // Blind/buried vias do not drill through layers outside their span.
            if (!(o.layers & layerMask)) return;
            if (o.netId == req.netId && o.drill > 0) {
                // same-net hole: keep copper, just don't bridge the drill itself
                Obstacle holeOnly = o;
                holeOnly.kind = Obstacle::Kind::Circle;
                holeOnly.center = o.center;
                holeOnly.halfWidth = o.drill / 2;
                blockFor(holeOnly, y, half / 2, blocked);       // tiny margin
                return;
            }
            blockFor(o, y, expand, blocked);
        });

        std::sort(blocked.begin(), blocked.end(), [](const Interval& a, const Interval& b) { return a.lo < b.lo; });

        coord_t cur = xBegin;
        const coord_t xEnd = xLimit;
        auto emit = [&](coord_t x0, coord_t x1) {
            if (x1 - x0 < req.lineWidth) return;        // too short to manufacture
            TraceSegment t;
            t.a = {x0, y}; t.b = {x1, y};
            t.width = req.lineWidth;
            t.layer = req.layer;
            t.netId = req.netId;
            t.isPour = true;
            res.strokes.push_back(t);
        };
        for (const Interval& iv : blocked) {
            if (iv.lo > cur) emit(cur, std::min(iv.lo, xEnd));
            cur = std::max(cur, iv.hi);
            if (cur >= xEnd) break;
        }
        if (cur < xEnd) emit(cur, xEnd);
    }
    return res;
}

} // namespace dc
