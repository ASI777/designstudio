#include "designcore/pcb_model.h"
#include <algorithm>
#include <cmath>

namespace dc {

static Vec2 rotated(Vec2 p, double deg) {
    const double r = deg * 3.14159265358979323846 / 180.0;
    const double c = std::cos(r), s = std::sin(r);
    return {coord_t(std::llround(p.x * c - p.y * s)), coord_t(std::llround(p.x * s + p.y * c))};
}

Vec2 Footprint::padWorldPos(const Pad& pad) const {
    return localToWorld(pad.pos);
}

Vec2 Footprint::localToWorld(Vec2 p) const {
    if (side == 1) p.y = -p.y;
    return pos + rotated(p, rotationDeg);
}

Quad Footprint::padWorldQuad(const Pad& pad) const {
    // Pad rectangle rotates with the footprint.
    return makePadQuad(padWorldPos(pad), pad.size, rotationDeg);
}

std::vector<Vec2> Footprint::regionWorldPoints(const CopperRegion& region) const {
    std::vector<Vec2> out;
    out.reserve(region.points.size());
    for (Vec2 p : region.points) out.push_back(localToWorld(p));
    return out;
}

std::vector<Vec2> Footprint::regionWorldHole(const CopperRegion& region) const {
    std::vector<Vec2> out;
    out.reserve(region.hole.size());
    for (Vec2 p : region.hole) out.push_back(localToWorld(p));
    return out;
}

Quad Footprint::bodyWorldQuad() const {
    return makePadQuad(localToWorld(bodyCenter), bodySize, rotationDeg);
}

std::vector<Vec2> Footprint::courtyardWorldPoints() const {
    std::vector<Vec2> out;
    out.reserve(courtyard.size());
    for (Vec2 p : courtyard) out.push_back(localToWorld(p));
    return out;
}

Rect Footprint::boundingBox() const {
    if (pads.empty() && regions.empty()) return {pos, pos};
    Rect bb{{INT64_MAX, INT64_MAX}, {INT64_MIN, INT64_MIN}};
    for (const auto& pad : pads) {
        const Rect pr = padWorldQuad(pad).bbox();   // rotation-aware
        bb.min.x = std::min(bb.min.x, pr.min.x); bb.min.y = std::min(bb.min.y, pr.min.y);
        bb.max.x = std::max(bb.max.x, pr.max.x); bb.max.y = std::max(bb.max.y, pr.max.y);
    }
    for (const auto& region : regions) {
        for (Vec2 p : regionWorldPoints(region)) {
            bb.min.x = std::min(bb.min.x, p.x); bb.min.y = std::min(bb.min.y, p.y);
            bb.max.x = std::max(bb.max.x, p.x); bb.max.y = std::max(bb.max.y, p.y);
        }
    }
    if (bodySize.x > 0 && bodySize.y > 0) {
        const Rect body = bodyWorldQuad().bbox();
        bb.min.x = std::min(bb.min.x, body.min.x); bb.min.y = std::min(bb.min.y, body.min.y);
        bb.max.x = std::max(bb.max.x, body.max.x); bb.max.y = std::max(bb.max.y, body.max.y);
    }
    for (Vec2 p : courtyardWorldPoints()) {
        bb.min.x = std::min(bb.min.x, p.x); bb.min.y = std::min(bb.min.y, p.y);
        bb.max.x = std::max(bb.max.x, p.x); bb.max.y = std::max(bb.max.y, p.y);
    }
    return bb;
}

// ---- items ----

id_t Board::addFootprint(Footprint fp) {
    fp.id = nextId_++;
    index_[fp.id] = {Tag::Fp, footprints_.size()};
    footprints_.push_back(std::move(fp));
    return footprints_.back().id;
}

id_t Board::addTrace(TraceSegment t) {
    t.id = nextId_++;
    index_[t.id] = {Tag::Trace, traces_.size()};
    traces_.push_back(t);
    return t.id;
}

id_t Board::addVia(Via v) {
    v.id = nextId_++;
    index_[v.id] = {Tag::Via, vias_.size()};
    vias_.push_back(v);
    return v.id;
}

void Board::reindex(Tag tag, std::size_t idx) {
    switch (tag) {
        case Tag::Fp:    if (idx < footprints_.size()) index_[footprints_[idx].id] = {tag, idx}; break;
        case Tag::Trace: if (idx < traces_.size())     index_[traces_[idx].id]     = {tag, idx}; break;
        case Tag::Via:   if (idx < vias_.size())       index_[vias_[idx].id]       = {tag, idx}; break;
    }
}

bool Board::removeItem(id_t id) {
    auto it = index_.find(id);
    if (it == index_.end()) return false;
    auto [tag, idx] = it->second;
    index_.erase(it);
    // swap-erase, then fix the index entry of the element that moved into idx
    switch (tag) {
        case Tag::Fp:
            footprints_[idx] = std::move(footprints_.back());
            footprints_.pop_back();
            break;
        case Tag::Trace:
            traces_[idx] = traces_.back();
            traces_.pop_back();
            break;
        case Tag::Via:
            vias_[idx] = vias_.back();
            vias_.pop_back();
            break;
    }
    reindex(tag, idx);
    return true;
}

bool Board::moveFootprint(id_t id, Vec2 newPos, double rotationDeg) {
    if (auto* fp = findFootprint(id)) { fp->pos = newPos; fp->rotationDeg = rotationDeg; return true; }
    return false;
}

Footprint* Board::findFootprint(id_t id) {
    auto it = index_.find(id);
    if (it == index_.end() || it->second.first != Tag::Fp) return nullptr;
    return &footprints_[it->second.second];
}

const Footprint* Board::findFootprint(id_t id) const {
    return const_cast<Board*>(this)->findFootprint(id);
}

TraceSegment* Board::findTrace(id_t id) {
    auto it = index_.find(id);
    if (it == index_.end() || it->second.first != Tag::Trace) return nullptr;
    return &traces_[it->second.second];
}

const TraceSegment* Board::findTrace(id_t id) const {
    return const_cast<Board*>(this)->findTrace(id);
}

Via* Board::findVia(id_t id) {
    auto it = index_.find(id);
    if (it == index_.end() || it->second.first != Tag::Via) return nullptr;
    return &vias_[it->second.second];
}

const Via* Board::findVia(id_t id) const {
    return const_cast<Board*>(this)->findVia(id);
}

bool Board::moveTrace(id_t id, Vec2 newA, Vec2 newB) {
    if (newA == newB) return false;
    if (auto* trace = findTrace(id)) {
        trace->a = newA;
        trace->b = newB;
        return true;
    }
    return false;
}

bool Board::moveVia(id_t id, Vec2 newPos) {
    if (auto* via = findVia(id)) {
        via->pos = newPos;
        return true;
    }
    return false;
}

// ---- nets ----

bool Board::setNet(int id, std::string name, int classId) {
    if (id < 0) return false;
    for (auto& n : nets_)
        if (n.id == id) { n.name = std::move(name); n.classId = classId; return true; }
    Net n; n.id = id; n.name = std::move(name); n.classId = classId;
    nets_.push_back(std::move(n));
    return true;
}

bool Board::removeNet(int id) {
    auto it = std::find_if(nets_.begin(), nets_.end(), [id](const Net& n) { return n.id == id; });
    if (it == nets_.end()) return false;
    nets_.erase(it);
    // Detach the net from copper that referenced it (no dangling ids).
    for (auto& fp : footprints_)
        for (auto& p : fp.pads)
            if (p.netId == id) p.netId = -1;
    for (auto& t : traces_) if (t.netId == id) t.netId = -1;
    for (auto& v : vias_)   if (v.netId == id) v.netId = -1;
    return true;
}

const Net* Board::findNet(int id) const {
    for (const auto& n : nets_) if (n.id == id) return &n;
    return nullptr;
}

// ---- net classes ----

void Board::setNetClass(NetClass nc) {
    for (auto& c : classes_)
        if (c.id == nc.id) { c = std::move(nc); return; }
    classes_.push_back(std::move(nc));
}

const NetClass* Board::findNetClass(int id) const {
    for (const auto& c : classes_) if (c.id == id) return &c;
    return nullptr;
}

bool Board::setClassPairRule(ClassPairRule rule) {
    if (rule.classA < 0 || rule.classB < 0 || rule.clearance <= 0) return false;
    if (rule.classA > rule.classB) std::swap(rule.classA, rule.classB);
    for (auto& existing : classPairRules_)
        if (existing.classA == rule.classA && existing.classB == rule.classB) {
            existing = std::move(rule); return true;
        }
    classPairRules_.push_back(std::move(rule));
    return true;
}

const NetClass& Board::rulesFor(int netId) const {
    if (const Net* n = findNet(netId))
        if (const NetClass* c = findNetClass(n->classId)) return *c;
    // A moved-from Board remains a valid object.  Qt can deliver a final paint
    // or teardown callback after a model swap, at which point its vectors are
    // allowed to be empty.  Keep the read-only rules query total instead of
    // dereferencing the moved-from storage.
    static const NetClass fallback{};
    return classes_.empty() ? fallback : classes_.front();
}

coord_t Board::pairClearance(int netA, int netB, coord_t inherited) const {
    const Net* a = findNet(netA); const Net* b = findNet(netB);
    if (!a || !b) return inherited;
    int ca = a->classId, cb = b->classId;
    if (ca > cb) std::swap(ca, cb);
    for (const auto& rule : classPairRules_)
        if (rule.classA == ca && rule.classB == cb)
            return std::max(inherited, rule.clearance);
    return inherited;
}

coord_t Board::maxClearanceForNet(int netId, coord_t inherited) const {
    coord_t out = inherited;
    for (const auto& netClass : classes_) out = std::max(out, netClass.clearance);
    for (const auto& fp : footprints_)
        for (const auto& pad : fp.pads)
            out = std::max(out, pad.clearanceOverride);
    const Net* net = findNet(netId);
    if (!net) return out;
    for (const auto& rule : classPairRules_)
        if (rule.classA == net->classId || rule.classB == net->classId)
            out = std::max(out, rule.clearance);
    return out;
}

void Board::clear() {
    footprints_.clear();
    traces_.clear();
    vias_.clear();
    nets_.clear();
    classes_ = {NetClass{}};
    classPairRules_.clear();
    ruleAreas_.clear();
    layerPolicies_.clear();
    copperZones_.clear();
    index_.clear();
    nextId_ = 1;
    copperLayerCount = 2;
    outline = {{0, 0}, {100 * NM_PER_MM, 80 * NM_PER_MM}};
    outlinePolygon.clear();
    cutouts.clear();
}

coord_t Board::netRoutedLength(int netId) const {
    double total = 0;
    for (const auto& t : traces_)
        if (t.netId == netId && !t.isPour) total += (t.b - t.a).length();
    return coord_t(std::llround(total));
}

namespace {
std::vector<Vec2> rectRing(const Rect& r) {
    return {r.min, {r.max.x, r.min.y}, r.max, {r.min.x, r.max.y}};
}
}

bool Board::containsPoint(Vec2 p) const {
    const bool inOuter = outlinePolygon.size() >= 3
        ? pointInPolygon(p, outlinePolygon) : outline.contains(p);
    if (!inOuter) return false;
    for (const auto& cutout : cutouts)
        if (pointInPolygon(p, cutout)) return false;
    return true;
}

double Board::distancePointToEdge(Vec2 p) const {
    const auto outer = outlinePolygon.size() >= 3 ? outlinePolygon : rectRing(outline);
    double d = distancePointPolygonBoundary(p, outer);
    for (const auto& cutout : cutouts)
        d = std::min(d, distancePointPolygonBoundary(p, cutout));
    return d;
}

double Board::distanceSegmentToEdge(Vec2 a, Vec2 b) const {
    const auto outer = outlinePolygon.size() >= 3 ? outlinePolygon : rectRing(outline);
    double d = distanceSegmentPolygonBoundary(a, b, outer);
    for (const auto& cutout : cutouts)
        d = std::min(d, distanceSegmentPolygonBoundary(a, b, cutout));
    return d;
}

bool Board::segmentWithinOutline(Vec2 a, Vec2 b, coord_t margin) const {
    if (!containsPoint(a) || !containsPoint(b)) return false;
    const Vec2 mid{coord_t(std::llround((double(a.x) + double(b.x)) * 0.5)),
                   coord_t(std::llround((double(a.y) + double(b.y)) * 0.5))};
    if (!containsPoint(mid)) return false;
    const double d = distanceSegmentToEdge(a, b);
    if (d < 0.5) return false;               // crosses or touches an edge/cutout
    return d + 0.5 >= double(margin);
}

bool Board::setOutlinePolygon(std::vector<Vec2> points) {
    if (points.size() < 3) return false;
    Rect bb{{points[0].x, points[0].y}, {points[0].x, points[0].y}};
    for (Vec2 p : points) {
        bb.min.x = std::min(bb.min.x, p.x); bb.min.y = std::min(bb.min.y, p.y);
        bb.max.x = std::max(bb.max.x, p.x); bb.max.y = std::max(bb.max.y, p.y);
    }
    if (bb.min.x == bb.max.x || bb.min.y == bb.max.y) return false;
    outlinePolygon = std::move(points); outline = bb; cutouts.clear();
    return true;
}

bool Board::addCutout(std::vector<Vec2> points) {
    if (points.size() < 3) return false;
    for (Vec2 p : points)
        if (!(outlinePolygon.size() >= 3 ? pointInPolygon(p, outlinePolygon)
                                         : outline.contains(p))) return false;
    cutouts.push_back(std::move(points));
    return true;
}

bool Board::setRuleArea(RuleArea area) {
    if (area.id < 0 || area.polygon.size() < 3 || area.clearance < 0
        || area.maxHeight < 0
        || area.minTraceWidth < 0 || area.layer < -1 || area.layer >= copperLayerCount)
        return false;
    for (auto& existing : ruleAreas_)
        if (existing.id == area.id) { existing = std::move(area); return true; }
    ruleAreas_.push_back(std::move(area));
    return true;
}

bool Board::setLayerPolicy(LayerPolicy policy) {
    if (policy.layer < 0 || policy.layer >= copperLayerCount || policy.copperThickness <= 0)
        return false;
    for (auto& existing : layerPolicies_)
        if (existing.layer == policy.layer) { existing = std::move(policy); return true; }
    layerPolicies_.push_back(std::move(policy));
    return true;
}

bool Board::setCopperZone(CopperZone zone) {
    if (zone.id < 0 || zone.polygon.size() < 3 || zone.netId < 0
        || zone.layer < 0 || zone.layer >= copperLayerCount || zone.clearance < 0
        || !std::isfinite(zone.minIslandAreaMm2) || zone.minIslandAreaMm2 < 0)
        return false;
    for (auto& existing : copperZones_)
        if (existing.id == zone.id) { existing = std::move(zone); return true; }
    copperZones_.push_back(std::move(zone));
    return true;
}

const LayerPolicy* Board::layerPolicy(int layer) const {
    for (const auto& policy : layerPolicies_)
        if (policy.layer == layer) return &policy;
    return nullptr;
}

bool Board::layerRoutingAllowed(int netId, int layer) const {
    if (layer < 0 || layer >= copperLayerCount) return false;
    if (const LayerPolicy* policy = layerPolicy(layer); policy && !policy->allowRouting)
        return false;
    return (rulesFor(netId).allowedLayers & layerBit(layer)) != 0;
}

bool Board::viaTypeAllowed(int netId, ViaType type) const {
    const auto bit = std::uint32_t(1u << std::uint32_t(type));
    return (rulesFor(netId).allowedViaTypes & bit) != 0;
}

int Board::maxViaCountForNet(int netId) const { return rulesFor(netId).maxViaCount; }

bool Board::ruleAreaContains(const RuleArea& area, Vec2 p, int layer) const {
    return (area.layer < 0 || area.layer == layer) && pointInPolygon(p, area.polygon);
}

bool Board::ruleAreaIntersectsSegment(const RuleArea& area, Vec2 a, Vec2 b, int layer) const {
    if (area.layer >= 0 && area.layer != layer) return false;
    if (pointInPolygon(a, area.polygon) || pointInPolygon(b, area.polygon)) return true;
    return distanceSegmentPolygonBoundary(a, b, area.polygon) < 0.5;
}

coord_t Board::clearanceForSegment(Vec2 a, Vec2 b, int layer, coord_t inherited) const {
    coord_t out = inherited;
    for (const auto& area : ruleAreas_)
        if (area.clearance > 0 && ruleAreaIntersectsSegment(area, a, b, layer))
            out = std::max(out, area.clearance);
    return out;
}

coord_t Board::minWidthForSegment(Vec2 a, Vec2 b, int layer, coord_t inherited) const {
    coord_t out = inherited;
    for (const auto& area : ruleAreas_)
        if (area.minTraceWidth > 0 && ruleAreaIntersectsSegment(area, a, b, layer))
            out = std::max(out, area.minTraceWidth);
    return out;
}

bool Board::routingAllowed(Vec2 a, Vec2 b, int layer) const {
    for (const auto& area : ruleAreas_)
        if (area.forbidRouting && ruleAreaIntersectsSegment(area, a, b, layer)) return false;
    return true;
}

bool Board::viaAllowed(Vec2 p, layermask_t span) const {
    for (const auto& area : ruleAreas_) {
        if (!area.forbidVias || !pointInPolygon(p, area.polygon)) continue;
        if (area.layer < 0 || (span & layerBit(area.layer))) return false;
    }
    return true;
}

} // namespace dc
