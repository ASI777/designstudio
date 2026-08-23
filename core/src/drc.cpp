#include "designcore/drc.h"
#include "designcore/obstacle_index.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <numeric>
#include <tuple>
#include <unordered_map>

namespace dc {

namespace {

double mm(double nm) { return nm / double(NM_PER_MM); }

std::string fmt(const char* f, double a, double b) {
    char buf[192];
    std::snprintf(buf, sizeof buf, f, a, b);
    return buf;
}

const char* kindName(Obstacle::Kind k) {
    switch (k) {
        case Obstacle::Kind::PadRect: return "pad";
        case Obstacle::Kind::PadRounded: return "pad";
        case Obstacle::Kind::Polygon: return "custom copper";
        case Obstacle::Kind::Segment: return "trace";
        case Obstacle::Kind::Circle:  return "via";
    }
    return "item";
}

std::string itemLabel(const Obstacle& o) {
    if ((o.kind == Obstacle::Kind::PadRect || o.kind == Obstacle::Kind::PadRounded
         || o.kind == Obstacle::Kind::Polygon)
        && o.fp && o.pad)
        return o.fp->refDes + "." + o.pad->name;
    return kindName(o.kind);
}

Vec2 anchor(const Obstacle& o) {
    switch (o.kind) {
        case Obstacle::Kind::PadRect: return o.quad.p[0];
        case Obstacle::Kind::PadRounded:
            return o.coreIsQuad ? o.quad.p[0] : o.a;
        case Obstacle::Kind::Polygon: return o.polygon.empty() ? Vec2{} : o.polygon[0];
        case Obstacle::Kind::Segment: return o.a;
        case Obstacle::Kind::Circle:  return o.center;
    }
    return {};
}

// min copper-to-copper distance between two obstacles (0 = touching/overlap)
double copperDistance(const Obstacle& x, const Obstacle& y) {
    return ObstacleIndex::distanceBetween(x, y);
}

double segmentBoardGap(const Board& board, Vec2 a, Vec2 b) {
    if (!board.containsPoint(a) || !board.containsPoint(b)) return -1.0;
    const Vec2 mid{coord_t(std::llround((double(a.x) + double(b.x)) * 0.5)),
                   coord_t(std::llround((double(a.y) + double(b.y)) * 0.5))};
    if (!board.containsPoint(mid)) return -1.0;
    return board.distanceSegmentToEdge(a, b);
}

double obstacleBoardGap(const Board& board, const Obstacle& o) {
    auto quadGap = [&](const Quad& q) {
        double d = 1e300;
        for (int i = 0; i < 4; ++i)
            d = std::min(d, segmentBoardGap(board, q.p[i], q.p[(i + 1) % 4]));
        return d;
    };
    switch (o.kind) {
        case Obstacle::Kind::PadRect:
            return quadGap(o.quad);
        case Obstacle::Kind::PadRounded: {
            const double core = o.coreIsQuad ? quadGap(o.quad)
                                              : segmentBoardGap(board, o.a, o.b);
            return core < 0 ? core : core - double(o.halfWidth);
        }
        case Obstacle::Kind::Polygon: {
            if (o.polygon.size() < 3) return -1.0;
            double d = 1e300;
            for (std::size_t i = 0; i < o.polygon.size(); ++i)
                d = std::min(d, segmentBoardGap(board, o.polygon[i],
                                                o.polygon[(i + 1) % o.polygon.size()]));
            return d;
        }
        case Obstacle::Kind::Segment: {
            const double core = segmentBoardGap(board, o.a, o.b);
            return core < 0 ? core : core - double(o.halfWidth);
        }
        case Obstacle::Kind::Circle:
            if (!board.containsPoint(o.center)) return -1.0;
            return board.distancePointToEdge(o.center) - double(o.halfWidth);
    }
    return -1.0;
}

bool obstacleIntersectsArea(const Obstacle& o, const RuleArea& area) {
    if (area.layer >= 0 && !(o.layers & layerBit(area.layer))) return false;
    Obstacle region;
    region.kind = Obstacle::Kind::Polygon;
    region.polygon = area.polygon;
    return ObstacleIndex::distanceBetween(o, region) < 0.5;
}

double polygonAreaMm2(const std::vector<Vec2>& polygon) {
    long double twice = 0;
    for (std::size_t i = 0; i < polygon.size(); ++i) {
        const Vec2 a = polygon[i], b = polygon[(i + 1) % polygon.size()];
        twice += static_cast<long double>(a.x) * b.y - static_cast<long double>(b.x) * a.y;
    }
    return std::abs(double(twice)) * 0.5 / double(NM_PER_MM) / double(NM_PER_MM);
}

DrcRule clearanceRule(const Obstacle& x, const Obstacle& y) {
    using K = Obstacle::Kind;
    auto has = [&](K k) { return x.kind == k || y.kind == k; };
    auto isPad = [](K k) { return k == K::PadRect || k == K::PadRounded || k == K::Polygon; };
    if (x.kind == K::Segment && y.kind == K::Segment) return DrcRule::TraceTraceClearance;
    if (isPad(x.kind) && isPad(y.kind)) return DrcRule::PadPadClearance;
    if (has(K::Circle) && (isPad(x.kind) || isPad(y.kind))) return DrcRule::ViaPadClearance;
    if (has(K::Circle) && has(K::Segment)) return DrcRule::ViaTraceClearance;
    if (x.kind == K::Circle && y.kind == K::Circle) return DrcRule::ViaViaClearance;
    return DrcRule::PadTraceClearance;
}

// ---- connectivity (union-find over same-net copper) ----

struct UF {
    std::vector<int> p;
    explicit UF(int n) : p(n) { std::iota(p.begin(), p.end(), 0); }
    int find(int a) { while (p[a] != a) { p[a] = p[p[a]]; a = p[a]; } return a; }
    void unite(int a, int b) { a = find(a); b = find(b); if (a != b) p[a] = b; }
};

// Electrical connectivity requires physical copper contact. A one-nanometre
// tolerance only absorbs integer/analytic rounding; it must never bridge a
// manufacturable air gap.
constexpr coord_t kSnapTol = 1;

struct NetNode {
    Vec2 pos;
    layermask_t layers;
    const Obstacle* src;
};

std::vector<NetIslandComponent>
islandComponentsForCopper(const std::vector<const Obstacle*>& copper) {
    if (copper.empty()) return {};
    UF uf(int(copper.size()));
    for (std::size_t i = 0; i < copper.size(); ++i)
        for (std::size_t j = i + 1; j < copper.size(); ++j) {
            if (!(copper[i]->layers & copper[j]->layers)) continue;
            if (copperDistance(*copper[i], *copper[j]) <= double(kSnapTol))
                uf.unite(int(i), int(j));
        }

    // count distinct roots among pad/via nodes (those are what must connect);
    // if there are none, count trace endpoint roots instead
    bool havePadVia = std::any_of(copper.begin(), copper.end(), [](const Obstacle* o) {
        return o->kind != Obstacle::Kind::Segment;
    });
    std::map<int, NetIslandComponent> byRoot;
    for (int i = 0; i < int(copper.size()); ++i) {
        if (havePadVia && copper[i]->kind == Obstacle::Kind::Segment) continue;
        int r = uf.find(i);
        auto [it, inserted] = byRoot.try_emplace(r);
        if (inserted) {
            it->second.anchor = anchor(*copper[i]);
            it->second.representativeOwnerId = copper[i]->ownerId;
        }
        ++it->second.terminalCount;
    }
    for (int i = 0; i < int(copper.size()); ++i) {
        auto it = byRoot.find(uf.find(i));
        if (it == byRoot.end()) continue;
        ++it->second.obstacleCount;
        it->second.layers |= copper[i]->layers;
    }
    std::vector<NetIslandComponent> result;
    result.reserve(byRoot.size());
    for (const auto& [_, component] : byRoot) result.push_back(component);
    std::sort(result.begin(), result.end(), [](const auto& a, const auto& b) {
        return std::tie(a.anchor.x, a.anchor.y, a.representativeOwnerId)
             < std::tie(b.anchor.x, b.anchor.y, b.representativeOwnerId);
    });
    return result;
}

int islandsForNet(const std::vector<const Obstacle*>& copper, int /*netId*/) {
    return int(islandComponentsForCopper(copper).size());
}

} // namespace

int netIslandCount(const Board& board, int netId) {
    return int(netIslandComponents(board, netId).size());
}

std::vector<NetIslandComponent> netIslandComponents(const Board& board, int netId) {
    ObstacleIndex index(board);
    std::vector<const Obstacle*> copper;
    for (const auto& o : index.obstacles())
        if (o.netId == netId) copper.push_back(&o);
    return islandComponentsForCopper(copper);
}

std::vector<DrcViolation> runDrc(const Board& board, const DrcOptions& opt) {
    std::vector<DrcViolation> out;
    ObstacleIndex index(board);
    const auto& obs = index.obstacles();

    auto clearanceOf = [&](int netId) -> coord_t {
        const NetClass& nc = board.rulesFor(netId);
        return netId >= 0 ? nc.clearance : opt.defaultClearance;
    };
    auto clearanceOfObstacle = [&](const Obstacle& o) -> coord_t {
        coord_t value = clearanceOf(o.netId);
        if (o.pad && o.pad->clearanceOverride > 0)
            value = o.pad->clearanceOverride;
        return value;
    };

    // ---- 0. explicit layer, via and polygon-zone policies ----
    std::unordered_map<int, int> viaCountByNet;
    for (const auto& trace : board.traces()) {
        const bool classAllows = trace.layer >= 0 && trace.layer < board.copperLayerCount
            && (board.rulesFor(trace.netId).allowedLayers & layerBit(trace.layer));
        if (!classAllows || (!trace.isPour && !board.layerRoutingAllowed(trace.netId, trace.layer)))
            out.push_back({DrcRule::LayerPolicyViolation,
                "Trace uses a layer prohibited by its net class or layer policy",
                trace.a, trace.id, 0});
    }
    for (const auto& via : board.vias()) {
        ++viaCountByNet[via.netId];
        if (!board.viaTypeAllowed(via.netId, via.type))
            out.push_back({DrcRule::ViaPolicyViolation,
                "Via technology is prohibited by the net class",
                via.pos, via.id, 0});
        for (int layer = via.fromLayer; layer <= via.toLayer; ++layer)
            if (!(board.rulesFor(via.netId).allowedLayers & layerBit(layer))) {
                out.push_back({DrcRule::ViaPolicyViolation,
                    "Via spans a layer prohibited by the net class or layer policy",
                    via.pos, via.id, 0});
                break;
            }
    }
    for (const auto& [netId, count] : viaCountByNet) {
        const int limit = board.maxViaCountForNet(netId);
        if (limit > 0 && count > limit)
            out.push_back({DrcRule::ViaPolicyViolation,
                "Net via count " + std::to_string(count) + " exceeds limit " + std::to_string(limit),
                {}, 0, 0});
    }
    for (const auto& zone : board.copperZones()) {
        bool valid = board.findNet(zone.netId) && zone.layer >= 0
            && zone.layer < board.copperLayerCount && zone.polygon.size() >= 3;
        for (std::size_t i = 0; valid && i < zone.polygon.size(); ++i) {
            const Vec2 a = zone.polygon[i], b = zone.polygon[(i + 1) % zone.polygon.size()];
            const Vec2 mid{coord_t((a.x + b.x) / 2), coord_t((a.y + b.y) / 2)};
            valid = board.containsPoint(a) && board.containsPoint(mid);
        }
        if (!valid || polygonAreaMm2(zone.polygon) + 1e-9 < zone.minIslandAreaMm2) {
            out.push_back({DrcRule::ZoneValidity,
                zone.name + " has an invalid net/layer/boundary or is below its minimum island area",
                zone.polygon.empty() ? Vec2{} : zone.polygon.front(), 0, 0});
            continue;
        }
        if (zone.requireConnection) {
            RuleArea area; area.layer = zone.layer; area.polygon = zone.polygon;
            const bool connected = std::any_of(obs.begin(), obs.end(), [&](const Obstacle& o) {
                return !o.viaKeepout && o.netId == zone.netId
                    && (o.layers & layerBit(zone.layer)) && obstacleIntersectsArea(o, area);
            });
            if (!connected)
                out.push_back({DrcRule::ZoneValidity,
                    zone.name + " requires a same-net copper connection",
                    zone.polygon.front(), 0, 0});
        }
    }

    // widest clearance any class demands — sizes the neighbourhood probe
    coord_t maxClearance = opt.defaultClearance;
    for (const auto& nc : board.netClasses()) maxClearance = std::max(maxClearance, nc.clearance);

    // ---- 1. copper-to-copper clearance (layer-aware, all kind pairs) ----
    for (std::size_t i = 0; i < obs.size(); ++i) {
        const Obstacle& a = obs[i];
        if (a.viaKeepout) continue;                          // not real copper
        const coord_t clrA = clearanceOfObstacle(a);
        const coord_t probePad = std::max(clrA, maxClearance) + a.halfWidth + NM_PER_MM / 2;
        index.query(a.bbox.inflated(probePad), [&](const Obstacle& b) {
            // visit each unordered pair once
            if (&b <= &a) return;
            if (b.viaKeepout) return;                        // courtyard zone, not copper
            if (a.netId == b.netId) return;                  // also skips -1 vs -1
            if (!(a.layers & b.layers)) return;              // no shared copper layer
            if (a.isPour && b.isPour) return;                // pour regen handles pour-pour
            coord_t requiredNm = std::max(clrA, clearanceOfObstacle(b));
            // An explicit pad clearance describes the manufacturer-controlled
            // local fanout envelope around that pad.  It therefore replaces a
            // coarser net-pair rule for pad/pad, pad/trace and pad/via checks;
            // rule-area constraints below remain authoritative.
            const bool explicitPadRule = (a.pad && a.pad->clearanceOverride > 0)
                || (b.pad && b.pad->clearanceOverride > 0);
            if (explicitPadRule) {
                requiredNm = 0;
                if (a.pad && a.pad->clearanceOverride > 0)
                    requiredNm = std::max(requiredNm, a.pad->clearanceOverride);
                if (b.pad && b.pad->clearanceOverride > 0)
                    requiredNm = std::max(requiredNm, b.pad->clearanceOverride);
            } else {
                requiredNm = board.pairClearance(a.netId, b.netId, requiredNm);
            }
            const layermask_t sharedLayers = a.layers & b.layers;
            for (const auto& area : board.ruleAreas()) {
                if (area.layer >= 0 && !(sharedLayers & layerBit(area.layer))) continue;
                if (area.clearance > 0
                    && (obstacleIntersectsArea(a, area) || obstacleIntersectsArea(b, area)))
                    requiredNm = std::max(requiredNm, area.clearance);
            }
            const double required = double(requiredNm);
            const double d = copperDistance(a, b);
            // Analytic distance and integer-nanometre geometry can differ by a
            // sub-nanometre rounding residue at an exactly legal boundary.
            if (d + 1.0 < required) {
                out.push_back({clearanceRule(a, b),
                    itemLabel(a) + " to " + itemLabel(b) +
                        fmt(" gap %.3f mm < required %.3f mm", mm(d), mm(required)),
                    anchor(a), a.ownerId, b.ownerId});
            }
        });
    }

    // ---- 2. board edge / cutout containment ----
    for (const auto& o : obs) {
        // A connector/sensor with an explicit edge anchor is intentionally
        // allowed to project beyond the routed board.  Its copper pads remain
        // authoritative and are still checked below; only the component body
        // courtyard is exempt from outline containment.
        if (o.viaKeepout && o.fp && !o.fp->edgeAnchor.empty()) continue;
        const double gap = obstacleBoardGap(board, o);
        if (gap <= 0.0)
            out.push_back({DrcRule::BoardEdge,
                (o.viaKeepout && o.fp ? o.fp->refDes : itemLabel(o))
                    + " extends outside board outline or into a cutout",
                anchor(o), o.ownerId, 0});
    }

    // ---- 2b. exact copper setback from outer edge and cutouts ----
    if (opt.minCopperToEdge > 0) {
        for (const auto& o : obs) {
            if (o.viaKeepout) continue;
            const double gap = obstacleBoardGap(board, o);
            if (gap < double(opt.minCopperToEdge))
                out.push_back({DrcRule::CopperToEdge,
                    itemLabel(o) + fmt(" edge gap %.3f mm < required %.3f mm",
                                       mm(gap), mm(double(opt.minCopperToEdge))),
                    anchor(o), o.ownerId, 0});
        }
    }

    // ---- 2c. same-side component courtyard/body clearance ----
    if (opt.minCourtyardClearance > 0) {
        for (const auto& court : obs) {
            if (!court.viaKeepout || !court.fp) continue;
            index.query(court.bbox.inflated(opt.minCourtyardClearance),
                        [&](const Obstacle& other) {
                if (&other <= &court || !other.viaKeepout || !other.fp) return;
                if (court.fp->side != other.fp->side) return;
                // Courtyards describe the component assembly envelope.  The
                // manufacturing profile still owns the required air gap between
                // two envelopes.  Using a single internal unit here made a
                // configured 0.2 mm rule display as 0.000 mm and allowed nearly
                // touching parts to pass.
                const double required = double(opt.minCourtyardClearance);
                const double gap = copperDistance(court, other);
                if (gap < required)
                    out.push_back({DrcRule::CourtyardOverlap,
                        court.fp->refDes + " to " + other.fp->refDes
                            + fmt(" courtyard/body gap %.3f mm < required %.3f mm",
                                  mm(gap), mm(required)),
                        anchor(court), court.ownerId, other.ownerId});
            });
        }
    }

    // ---- 2d. explicit rule-area prohibitions ----
    for (const auto& trace : board.traces()) {
        if (!board.routingAllowed(trace.a, trace.b, trace.layer))
            out.push_back({DrcRule::RuleAreaViolation,
                "Trace enters a no-routing rule area", trace.a, trace.id, 0});
    }
    for (const auto& via : board.vias()) {
        if (!board.viaAllowed(via.pos, via.mask()))
            out.push_back({DrcRule::RuleAreaViolation,
                "Via enters a no-via rule area", via.pos, via.id, 0});
    }
    for (const auto& court : obs) {
        if (!court.viaKeepout || !court.fp) continue;
        for (const auto& area : board.ruleAreas()) {
            if (!area.forbidPlacement || !obstacleIntersectsArea(court, area)) continue;
            out.push_back({DrcRule::RuleAreaViolation,
                court.fp->refDes + " enters a no-placement rule area",
                anchor(court), court.ownerId, 0});
            break;
        }
    }

    // ---- 2e. component placement constraints ----
    std::vector<const Obstacle*> componentBounds;
    for (const auto& o : obs)
        if (o.viaKeepout && o.fp) componentBounds.push_back(&o);

    for (const Obstacle* bound : componentBounds) {
        const Footprint& fp = *bound->fp;
        for (const auto& area : board.ruleAreas()) {
            if (area.maxHeight <= 0 || fp.bodyHeight <= area.maxHeight
                || !obstacleIntersectsArea(*bound, area)) continue;
            out.push_back({DrcRule::HeightConstraint,
                fp.refDes + fmt(" height %.3f mm exceeds area limit %.3f mm",
                                mm(double(fp.bodyHeight)), mm(double(area.maxHeight))),
                anchor(*bound), fp.id, 0});
            break;
        }
        if (fp.testAccessRequired && fp.testAccessHalo > 0) {
            const double edgeGap = obstacleBoardGap(board, *bound);
            if (edgeGap < double(fp.testAccessHalo))
                out.push_back({DrcRule::TestAccess,
                    fp.refDes + fmt(" access-to-edge gap %.3f mm < required %.3f mm",
                                    mm(edgeGap), mm(double(fp.testAccessHalo))),
                    anchor(*bound), fp.id, 0});
        }
    }

    for (std::size_t i = 0; i < componentBounds.size(); ++i) {
        const Obstacle& a = *componentBounds[i];
        for (std::size_t j = i + 1; j < componentBounds.size(); ++j) {
            const Obstacle& b = *componentBounds[j];
            if (a.fp->side != b.fp->side) continue;
            const double gap = copperDistance(a, b);
            coord_t thermalRequired = 0;
            if (a.fp->thermalPowerW > 0) thermalRequired = a.fp->thermalClearance;
            if (b.fp->thermalPowerW > 0)
                thermalRequired = std::max(thermalRequired, b.fp->thermalClearance);
            if (thermalRequired > 0 && gap < double(thermalRequired))
                out.push_back({DrcRule::ThermalSpacing,
                    a.fp->refDes + " to " + b.fp->refDes
                        + fmt(" thermal gap %.3f mm < required %.3f mm",
                              mm(gap), mm(double(thermalRequired))),
                    anchor(a), a.fp->id, b.fp->id});

            const coord_t accessRequired = std::max(
                a.fp->testAccessRequired ? a.fp->testAccessHalo : coord_t(0),
                b.fp->testAccessRequired ? b.fp->testAccessHalo : coord_t(0));
            if (accessRequired > 0 && gap < double(accessRequired))
                out.push_back({DrcRule::TestAccess,
                    a.fp->refDes + " to " + b.fp->refDes
                        + fmt(" test-access gap %.3f mm < required %.3f mm",
                              mm(gap), mm(double(accessRequired))),
                    anchor(a), a.fp->id, b.fp->id});
        }
    }

    // ---- 3. minimum trace width ----
    for (const auto& t : board.traces()) {
        if (t.isPour) continue;
        coord_t requiredWidth = t.minWidthOverride > 0
            ? t.minWidthOverride
            : std::max(opt.minTraceWidth, board.rulesFor(t.netId).traceWidth);
        requiredWidth = board.minWidthForSegment(t.a, t.b, t.layer, requiredWidth);
        if (t.width < requiredWidth)
            out.push_back({DrcRule::TraceWidth,
                fmt("Trace width %.3f mm < minimum %.3f mm", mm(double(t.width)), mm(double(requiredWidth))),
                t.a, t.id, 0});
    }

    // ---- 3b. right-angle routing quality ----
    // Exact orthogonal corners are manufacturable, but are discouraged and
    // become release-blocking when the Qt SI verifier sees them on a
    // high-speed class. Report only true two-segment bends: T junctions,
    // collinear joins, pours, and corners terminating inside a same-net pad are
    // intentionally excluded.
    if (opt.checkRightAngles) {
        using Endpoint = std::tuple<int, int, coord_t, coord_t>;
        std::map<Endpoint, int> incidence;
        for (const auto& trace : board.traces()) {
            if (trace.isPour || trace.netId < 0) continue;
            ++incidence[{trace.netId, trace.layer, trace.a.x, trace.a.y}];
            ++incidence[{trace.netId, trace.layer, trace.b.x, trace.b.y}];
        }
        auto isPadTerminal = [&](Vec2 point, int netId, int layer) {
            for (const auto& footprint : board.footprints())
                for (const auto& pad : footprint.pads)
                    if (pad.netId == netId
                        && (board.padMask(footprint, pad) & layerBit(layer))
                        && pointInQuad(point, footprint.padWorldQuad(pad)))
                        return true;
            return false;
        };
        const auto& traces = board.traces();
        for (std::size_t i = 0; i < traces.size(); ++i) {
            const auto& first = traces[i];
            if (first.isPour || first.netId < 0) continue;
            for (std::size_t j = i + 1; j < traces.size(); ++j) {
                const auto& second = traces[j];
                if (second.isPour || second.netId != first.netId
                    || second.layer != first.layer) continue;
                Vec2 corner, firstOther, secondOther;
                bool shared = true;
                if (first.a == second.a) {
                    corner = first.a; firstOther = first.b; secondOther = second.b;
                } else if (first.a == second.b) {
                    corner = first.a; firstOther = first.b; secondOther = second.a;
                } else if (first.b == second.a) {
                    corner = first.b; firstOther = first.a; secondOther = second.b;
                } else if (first.b == second.b) {
                    corner = first.b; firstOther = first.a; secondOther = second.a;
                } else {
                    shared = false;
                }
                if (!shared
                    || incidence[{first.netId, first.layer, corner.x, corner.y}] != 2
                    || isPadTerminal(corner, first.netId, first.layer))
                    continue;
                const Vec2 a = firstOther - corner;
                const Vec2 b = secondOther - corner;
                const long double dot = static_cast<long double>(a.x) * b.x
                                      + static_cast<long double>(a.y) * b.y;
                const long double cross = static_cast<long double>(a.x) * b.y
                                        - static_cast<long double>(a.y) * b.x;
                if (dot == 0 && cross != 0)
                    out.push_back({DrcRule::RightAngleBend,
                        "Routed copper has a 90-degree bend; use two 45-degree segments",
                        corner, first.id, second.id});
            }
        }
    }

    // ---- 4. drill size + annular ring ----
    for (const auto& fp : board.footprints())
        for (const auto& pad : fp.pads) {
            if (!pad.throughHole || pad.drill <= 0) continue;
            const Vec2 at = fp.padWorldPos(pad);
            if (pad.drill < opt.minDrill)
                out.push_back({DrcRule::DrillSize,
                    fp.refDes + "." + pad.name +
                        fmt(" drill %.3f mm < minimum %.3f mm", mm(double(pad.drill)), mm(double(opt.minDrill))),
                    at, fp.id, 0});
            const coord_t ring = (std::min(pad.size.x, pad.size.y) - pad.drill) / 2;
            if (ring < opt.minAnnularRing)
                out.push_back({DrcRule::AnnularRing,
                    fp.refDes + "." + pad.name +
                        fmt(" annular ring %.3f mm < minimum %.3f mm", mm(double(ring)), mm(double(opt.minAnnularRing))),
                    at, fp.id, 0});
        }
    for (const auto& v : board.vias()) {
        // Only explicitly laser-formed microvias receive the HDI drill floor.
        // Blind/buried mechanical vias retain the mechanical drill constraint.
        const bool micro = v.isMicro();
        const coord_t drillFloor = micro ? opt.minMicroviaDrill : opt.minDrill;
        if (v.drill < drillFloor)
            out.push_back({DrcRule::DrillSize,
                fmt("Via drill %.3f mm < minimum %.3f mm", mm(double(v.drill)), mm(double(drillFloor))),
                v.pos, v.id, 0});
        const coord_t ring = (v.diameter - v.drill) / 2;
        if (ring < opt.minAnnularRing)
            out.push_back({DrcRule::AnnularRing,
                fmt("Via annular ring %.3f mm < minimum %.3f mm", mm(double(ring)), mm(double(opt.minAnnularRing))),
                v.pos, v.id, 0});
    }

    // ---- 5. drill-to-drill spacing (hole wall integrity) ----
    {
        struct Hole { Vec2 pos; coord_t drill; id_t owner; bool micro; };
        std::vector<Hole> holes;
        for (const auto& fp : board.footprints())
            for (const auto& pad : fp.pads)
                if (pad.throughHole && pad.drill > 0)
                    holes.push_back({fp.padWorldPos(pad), pad.drill, fp.id, false});
        for (const auto& v : board.vias())
            if (v.drill > 0) holes.push_back({v.pos, v.drill, v.id, v.isMicro()});
        for (std::size_t i = 0; i < holes.size(); ++i)
            for (std::size_t j = i + 1; j < holes.size(); ++j) {
                const double wall = (holes[i].pos - holes[j].pos).length()
                                  - double(holes[i].drill + holes[j].drill) / 2.0;
                // Microvia-to-microvia walls use the relaxed laser/HDI floor.
                const coord_t wallMin = (holes[i].micro && holes[j].micro)
                                          ? opt.minMicroviaWall : opt.minDrillToDrill;
                if (wall < double(wallMin))
                    out.push_back({DrcRule::DrillToDrill,
                        fmt("Hole-to-hole wall %.3f mm < minimum %.3f mm", mm(wall), mm(double(wallMin))),
                        holes[i].pos, holes[i].owner, holes[j].owner});
            }
    }

    // ---- 5b. foreign copper to plated-hole clearance ----
    // NPTH/slots are added by the outline/feature model in the geometry package;
    // this check covers the plated pad/via holes represented by today's Board.
    if (opt.minCopperToHole > 0) {
        struct CopperHole {
            Vec2 pos;
            coord_t drill;
            int netId;
            layermask_t layers;
            id_t owner;
            const Pad* pad;
        };
        std::vector<CopperHole> holes;
        for (const auto& fp : board.footprints())
            for (const auto& pad : fp.pads)
                if (pad.throughHole && pad.drill > 0)
                    holes.push_back({fp.padWorldPos(pad), pad.drill, pad.netId,
                                     board.allLayersMask(), fp.id, &pad});
        for (const auto& via : board.vias())
            if (via.drill > 0)
                holes.push_back({via.pos, via.drill, via.netId, via.mask(), via.id, nullptr});

        for (const auto& hole : holes) {
            const coord_t radius = hole.drill / 2;
            const coord_t reach = radius + opt.minCopperToHole;
            const Rect probe{{hole.pos.x - reach, hole.pos.y - reach},
                             {hole.pos.x + reach, hole.pos.y + reach}};
            Obstacle holeShape;
            holeShape.kind = Obstacle::Kind::Circle;
            holeShape.center = hole.pos;
            holeShape.halfWidth = radius;
            index.query(probe, [&](const Obstacle& copper) {
                if (copper.viaKeepout || !(copper.layers & hole.layers)) return;
                if (hole.pad && copper.pad == hole.pad) return;
                if (!hole.pad && copper.ownerId == hole.owner) return;
                if (hole.netId >= 0 && copper.netId == hole.netId) return;
                const double gap = copperDistance(holeShape, copper);
                if (gap < double(opt.minCopperToHole))
                    out.push_back({DrcRule::CopperToHole,
                        itemLabel(copper) + fmt(" to hole gap %.3f mm < required %.3f mm",
                                                mm(gap), mm(double(opt.minCopperToHole))),
                        hole.pos, copper.ownerId, hole.owner});
            });
        }
    }

    // ---- 5c. routed copper must have an intentional net assignment ----
    if (opt.checkUnassignedCopper) {
        for (const auto& trace : board.traces())
            if (trace.netId < 0)
                out.push_back({DrcRule::UnassignedCopper,
                    "Trace has no net assignment", trace.a, trace.id, 0});
        for (const auto& via : board.vias())
            if (via.netId < 0)
                out.push_back({DrcRule::UnassignedCopper,
                    "Via has no net assignment", via.pos, via.id, 0});
    }

    // ---- 6. unconnected nets ----
    if (opt.checkConnectivity) {
        std::unordered_map<int, std::vector<const Obstacle*>> byNet;
        for (const auto& o : obs)
            if (o.netId >= 0) byNet[o.netId].push_back(&o);
        for (const auto& net : board.nets()) {
            auto it = byNet.find(net.id);
            if (it == byNet.end()) continue;
            const int islands = islandsForNet(it->second, net.id);
            if (islands > 1) {
                Vec2 at = anchor(*it->second.front());
                char buf[160];
                std::snprintf(buf, sizeof buf, "Net %s: %d unconnected islands",
                              net.name.c_str(), islands);
                out.push_back({DrcRule::UnconnectedNet, buf, at, 0, 0});
            }
        }
    }

    // ---- 7. length matching (differential pairs / matched groups) ----
    if (opt.checkSkew) {
        for (const auto& nc : board.netClasses()) {
            if (nc.maxSkew <= 0) continue;
            std::vector<const Net*> members;
            for (const auto& n : board.nets())
                if (n.classId == nc.id) members.push_back(&n);
            if (members.size() < 2) continue;
            coord_t lenMin = INT64_MAX, lenMax = INT64_MIN;
            const Net* shortest = nullptr; const Net* longest = nullptr;
            for (const Net* n : members) {
                const coord_t len = board.netRoutedLength(n->id);
                if (len < lenMin) { lenMin = len; shortest = n; }
                if (len > lenMax) { lenMax = len; longest = n; }
            }
            if (lenMax - lenMin > nc.maxSkew && shortest && longest) {
                // anchor on any trace of the longest net
                Vec2 at{};
                for (const auto& t : board.traces())
                    if (t.netId == longest->id && !t.isPour) { at = t.a; break; }
                char buf[192];
                std::snprintf(buf, sizeof buf,
                    "Class %s skew %.3f mm > max %.3f mm (%s longest, %s shortest)",
                    nc.name.c_str(), mm(double(lenMax - lenMin)), mm(double(nc.maxSkew)),
                    longest->name.c_str(), shortest->name.c_str());
                out.push_back({DrcRule::SkewExceeded, buf, at, 0, 0});
            }
        }
    }

    return out;
}

} // namespace dc
