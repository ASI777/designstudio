#pragma once
// PCB document model: board, N-layer stackup, footprints, pads, traces,
// vias (through / blind-buried / micro), nets and net classes.
// Owned by C++; the C# UI rebuilds the native board from its document before
// every native operation (single source of truth: the document), so nothing
// here can drift from what is on screen.

#include "geometry.h"
#include <string>
#include <unordered_map>
#include <vector>

namespace dc {

using id_t = std::uint64_t;
using layermask_t = std::uint32_t;

constexpr int kMaxCopperLayers = 32;

inline layermask_t layerBit(int layer) { return layermask_t(1u) << layer; }
inline layermask_t layerSpanMask(int from, int to) {
    if (from > to) { int t = from; from = to; to = t; }
    layermask_t m = 0;
    for (int l = from; l <= to; ++l) m |= layerBit(l);
    return m;
}

// Per-class routing/clearance rules. Class 0 ("Default") always exists.
// High-speed designs (DDR, SerDes, MIPI) get their own classes with tighter
// clearances, controlled widths and length-matching limits.
struct NetClass {
    int id = 0;
    std::string name = "Default";
    coord_t clearance     = 200'000;   // 0.2 mm
    coord_t traceWidth    = 250'000;   // 0.25 mm
    coord_t viaDiameter   = 600'000;   // 0.6 mm
    coord_t viaDrill      = 300'000;   // 0.3 mm
    coord_t diffPairGap   = 0;         // 0 = not a differential class
    coord_t maxSkew       = 0;         // 0 = no length-matching check
    bool    allowMicrovia = false;     // HDI: adjacent-layer microvias
    layermask_t allowedLayers = ~layermask_t(0); // class routing layer mask
    std::uint32_t allowedViaTypes = 0x0f;        // bit(ViaType)
    int maxViaCount = 0;                         // 0 = unrestricted
};

struct ClassPairRule {
    int classA = 0, classB = 0;
    coord_t clearance = 0;
    std::string source;
    std::string sourceRevision;
};

struct Net {
    int id = -1;                       // stable: survives deletion of other nets
    std::string name;
    int classId = 0;
};

enum class PadShape : std::int32_t {
    Rect = 0,
    RoundRect = 1,
    Oval = 2,
    Circle = 3,
};

struct Pad {
    id_t id = 0;
    Vec2 pos;                          // relative to footprint origin
    Vec2 size;                         // w x h
    bool throughHole = false;
    coord_t drill = 0;
    int netId = -1;
    std::string name;                  // "1", "GND", ...
    PadShape shape = PadShape::Rect;
    coord_t cornerRadius = 0;          // RoundRect only; clamped to half minor axis
    coord_t clearanceOverride = 0;     // 0 = inherit class/pair/area
};

struct CopperRegion {
    std::vector<Vec2> points;           // footprint-local outer polygon
    std::vector<Vec2> hole;             // optional footprint-local cutout
    int netId = -1;
};

struct Footprint {
    id_t id = 0;
    std::string refDes;                // "U1", "R5"
    std::string libName;               // "SOIC-8"
    Vec2 pos;
    double rotationDeg = 0;
    int side = 0;                      // assembly side: 0 = top, 1 = bottom
    Vec2 bodyCenter;                   // footprint-local assembly body centre
    Vec2 bodySize;                     // 0,0 when body is unknown
    coord_t bodyHeight = 0;
    std::vector<Vec2> courtyard;       // footprint-local exact courtyard polygon
    bool placementLocked = false;
    std::string functionalGroup;
    std::string edgeAnchor;            // left | right | top | bottom | empty
    double thermalPowerW = 0;
    coord_t thermalClearance = 0;
    bool testAccessRequired = false;
    coord_t testAccessHalo = 0;
    std::vector<Pad> pads;
    std::vector<CopperRegion> regions;
    Rect boundingBox() const;
    Vec2 padWorldPos(const Pad& pad) const;    // rotation-aware pad centre
    Quad padWorldQuad(const Pad& pad) const;   // rotation-aware pad outline
    std::vector<Vec2> regionWorldPoints(const CopperRegion& region) const;
    std::vector<Vec2> regionWorldHole(const CopperRegion& region) const;
    Vec2 localToWorld(Vec2 p) const;
    Quad bodyWorldQuad() const;
    std::vector<Vec2> courtyardWorldPoints() const;
};

struct TraceSegment {
    id_t id = 0;
    Vec2 a, b;
    coord_t width = 0;
    int layer = 0;                     // copper layer index, 0..copperLayerCount-1
    int netId = -1;
    bool isPour = false;               // generated pour fill stroke
    coord_t minWidthOverride = 0;      // explicit local neck-down minimum; 0 = inherit
    double lengthMm() const { return (b - a).length() / double(NM_PER_MM); }
};

enum class ViaType : std::int32_t {
    Through = 0,
    Blind = 1,
    Buried = 2,
    Microvia = 3,
};

struct Via {
    id_t id = 0;
    Vec2 pos;
    coord_t diameter = 0, drill = 0;
    int netId = -1;
    int fromLayer = 0, toLayer = 1;    // inclusive copper span
    ViaType type = ViaType::Through;
    bool isThrough(int copperLayers) const {
        return type == ViaType::Through
            && fromLayer == 0 && toLayer == copperLayers - 1;
    }
    bool isMicro() const { return type == ViaType::Microvia; }
    layermask_t mask() const { return layerSpanMask(fromLayer, toLayer); }
};

struct RuleArea {
    int id = 0;
    std::string name;
    std::vector<Vec2> polygon;
    int layer = -1;                    // -1 = every copper layer
    coord_t clearance = 0;             // 0 = inherit
    coord_t minTraceWidth = 0;         // 0 = inherit
    bool forbidRouting = false;
    bool forbidVias = false;
    bool forbidPlacement = false;
    coord_t maxHeight = 0;             // 0 = unrestricted component height
    std::string source;
    std::string sourceRevision;
};

enum class LayerRole : std::int32_t { Signal = 0, Plane = 1, Mixed = 2 };
enum class PreferredDirection : std::int32_t { Any = 0, Horizontal = 1, Vertical = 2 };

struct LayerPolicy {
    int layer = 0;
    std::string name;
    LayerRole role = LayerRole::Signal;
    PreferredDirection preferredDirection = PreferredDirection::Any;
    bool allowRouting = true;
    coord_t copperThickness = 35'000;
    std::string source;
    std::string sourceRevision;
};

struct CopperZone {
    int id = 0;
    std::string name;
    std::vector<Vec2> polygon;
    int netId = -1;
    int layer = 0;
    coord_t clearance = 0;
    double minIslandAreaMm2 = 0;
    bool requireConnection = true;
    std::string source;
    std::string sourceRevision;
};

class Board {
public:
    // ---- items (ids assigned by the board, O(1) lookup/removal) ----
    id_t addFootprint(Footprint fp);
    id_t addTrace(TraceSegment t);
    id_t addVia(Via v);
    bool removeItem(id_t id);
    bool moveFootprint(id_t id, Vec2 newPos, double rotationDeg);
    Footprint* findFootprint(id_t id);
    const Footprint* findFootprint(id_t id) const;
    TraceSegment* findTrace(id_t id);
    const TraceSegment* findTrace(id_t id) const;
    Via* findVia(id_t id);
    const Via* findVia(id_t id) const;
    // Transactional tools edit geometry in a private Board copy. These
    // mutators preserve the object's immutable id and its index entry.
    bool moveTrace(id_t id, Vec2 newA, Vec2 newB);
    bool moveVia(id_t id, Vec2 newPos);

    // ---- nets: caller-supplied stable ids (the document owns numbering) ----
    bool setNet(int id, std::string name, int classId);   // add or update
    bool removeNet(int id);
    const Net* findNet(int id) const;

    // ---- net classes ----
    void setNetClass(NetClass nc);                        // add or update by id
    bool setClassPairRule(ClassPairRule rule);
    const NetClass* findNetClass(int id) const;
    // Effective rules for a net (class fallback -> Default class).
    const NetClass& rulesFor(int netId) const;
    coord_t pairClearance(int netA, int netB, coord_t inherited) const;
    coord_t maxClearanceForNet(int netId, coord_t inherited) const;

    void clear();                                          // full reset (keeps nothing)

    const std::vector<Footprint>& footprints() const { return footprints_; }
    const std::vector<TraceSegment>& traces() const { return traces_; }
    const std::vector<Via>& vias() const { return vias_; }
    const std::vector<Net>& nets() const { return nets_; }
    const std::vector<NetClass>& netClasses() const { return classes_; }
    const std::vector<ClassPairRule>& classPairRules() const { return classPairRules_; }
    const std::vector<RuleArea>& ruleAreas() const { return ruleAreas_; }
    const std::vector<LayerPolicy>& layerPolicies() const { return layerPolicies_; }
    const std::vector<CopperZone>& copperZones() const { return copperZones_; }

    // Copper presence of items on a layer.
    layermask_t allLayersMask() const { return layerSpanMask(0, copperLayerCount - 1); }
    layermask_t padMask(const Footprint& fp, const Pad& p) const {
        return p.throughHole ? allLayersMask()
                             : layerBit(fp.side == 1 ? copperLayerCount - 1 : 0);
    }

    // Total routed length of a net in nm (pour strokes excluded).
    coord_t netRoutedLength(int netId) const;

    // Authoritative fabrication topology. Empty outlinePolygon means the legacy
    // rectangle in `outline`; cutouts are voids inside either outer form.
    bool containsPoint(Vec2 p) const;
    double distancePointToEdge(Vec2 p) const;
    double distanceSegmentToEdge(Vec2 a, Vec2 b) const;
    bool segmentWithinOutline(Vec2 a, Vec2 b, coord_t margin = 0) const;
    bool setOutlinePolygon(std::vector<Vec2> points);
    bool addCutout(std::vector<Vec2> points);
    bool setRuleArea(RuleArea area);
    bool setLayerPolicy(LayerPolicy policy);
    bool setCopperZone(CopperZone zone);
    const LayerPolicy* layerPolicy(int layer) const;
    bool layerRoutingAllowed(int netId, int layer) const;
    bool viaTypeAllowed(int netId, ViaType type) const;
    int maxViaCountForNet(int netId) const;
    bool ruleAreaContains(const RuleArea& area, Vec2 p, int layer) const;
    bool ruleAreaIntersectsSegment(const RuleArea& area, Vec2 a, Vec2 b, int layer) const;
    coord_t clearanceForSegment(Vec2 a, Vec2 b, int layer, coord_t inherited) const;
    coord_t minWidthForSegment(Vec2 a, Vec2 b, int layer, coord_t inherited) const;
    bool routingAllowed(Vec2 a, Vec2 b, int layer) const;
    bool viaAllowed(Vec2 p, layermask_t span) const;

    Rect outline{{0, 0}, {100 * NM_PER_MM, 80 * NM_PER_MM}};
    std::vector<Vec2> outlinePolygon;
    std::vector<std::vector<Vec2>> cutouts;
    int copperLayerCount = 2;          // 2..kMaxCopperLayers

private:
    id_t nextId_ = 1;
    std::vector<Footprint> footprints_;
    std::vector<TraceSegment> traces_;
    std::vector<Via> vias_;
    std::vector<Net> nets_;
    std::vector<NetClass> classes_{NetClass{}};   // class 0 always present
    std::vector<ClassPairRule> classPairRules_;
    std::vector<RuleArea> ruleAreas_;
    std::vector<LayerPolicy> layerPolicies_;
    std::vector<CopperZone> copperZones_;
    // id -> (container tag, index). Containers use swap-erase; map kept in sync.
    enum class Tag : std::uint8_t { Fp, Trace, Via };
    std::unordered_map<id_t, std::pair<Tag, std::size_t>> index_;
    void reindex(Tag tag, std::size_t idx);
};

} // namespace dc
