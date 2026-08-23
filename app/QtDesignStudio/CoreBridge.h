#pragma once
#include <QString>
#include <QVector>
#include <QJsonObject>
#include <QHash>
#include <QPointF>
#include <QMutex>
#include "ProjectModel.h"

// Opaque handle type (matches DcBoardHandle in c_api.h)
using DcBoardHandle = void*;

// ── Result types ──────────────────────────────────────────────────────────────
struct RouteResult {
    bool    ok{false};
    QString message;
    int     segmentsAdded{0};
    int     viasAdded{0};
    int     routedConnections{0};
    int     unroutedConnections{0};
    // Collected by autoRoute() — applied to the model on the UI thread after
    // the background job returns, so no model writes happen off-thread.
    QVector<ProjTrace> newTraces;
    QVector<ProjVia>   newVias;
};

struct DrcViolation {
    enum Severity { Error, Warning, Info };
    Severity severity{Error};
    int      ruleCode{};
    QString  rule;        // human-readable rule name
    QString  description;
    double   x_mm{}, y_mm{};
};

struct InteractiveTraceMove { int modelIndex{-1}; ProjTrace trace; };
struct InteractiveViaMove { int modelIndex{-1}; ProjVia via; };
struct InteractivePreview {
    bool accepted{false};
    QString message;
    QVector<ProjTrace> addedTraces;
    QVector<ProjVia> addedVias;
    QVector<InteractiveTraceMove> movedTraces;
    QVector<InteractiveViaMove> movedVias;
};

// ── CoreBridge — thin dlopen wrapper around libdesigncore.so ─────────────────
// Mirrors RebuildNative() from BoardDocument.cs: the native board is rebuilt
// from scratch before every operation.
class CoreBridge {
public:
    static CoreBridge* create(const QString& libraryPath, QString& errorOut);
    ~CoreBridge();

    bool isLoaded() const { return m_lib != nullptr; }

    RouteResult           autoRoute(ProjectModel* model);
    QVector<DrcViolation> runDrc(ProjectModel* model);
    QJsonObject           buildVerificationReport(ProjectModel* model);
    bool                  pourCopper(ProjectModel* model, int netId, int layer);
    InteractivePreview    beginInteractiveRoute(ProjectModel* model, const QPointF& start,
                                                int layer, int netId, bool shove);
    InteractivePreview    updateInteractiveRoute(const QPointF& cursor);
    InteractivePreview    placeInteractiveVia(int targetLayer);
    bool                  commitInteractiveRoute(ProjectModel* model, QString& errorOut);
    void                  cancelInteractiveRoute();

private:
    CoreBridge() = default;
    void* m_lib{nullptr};
    DcBoardHandle m_board{nullptr};
    QRecursiveMutex m_boardMutex;

    // ── Mirror of DcPadDef ───────────────────────────────────────────────────
    struct DcPadDef {
        int64_t  x, y, w, h;
        int32_t  netId;
        int32_t  throughHole;
        int64_t  drill;
        char     name[16];
    };
    struct DcPadDefV2 {
        int64_t x, y, w, h;
        int32_t netId, throughHole;
        int64_t drill;
        int32_t shape, reserved;
        int64_t cornerRadius;
        char name[16];
    };
    struct DcPoint { int64_t x, y; };

    // ── Mirror of DcRouteRequest ─────────────────────────────────────────────
    struct DcRouteRequest {
        int32_t  netId;
        int64_t  sx, sy;   int32_t startLayer;
        int64_t  gx, gy;   int32_t goalLayer;
        int64_t  traceWidth, clearance, gridStep;
        int32_t  allowVias, allowMicrovia;
        int64_t  viaDiameter, viaDrill;
        double   viaCostMm;
        int64_t  maxExpansions;
        int64_t  minDrillToDrill;
    };
    struct DcRoutePoint { int64_t x, y; int32_t layer; int32_t _pad; };
    struct DcRouteVia   { int64_t x, y; int32_t fromLayer, toLayer; };
    struct DcInteractiveRouteRequest {
        uint32_t abiVersion, structSize;
        int32_t netId, startLayer;
        int64_t startX, startY;
        int64_t traceWidth, clearance, gridStep;
        int64_t viaDiameter, viaDrill, minDrillToDrill;
        int32_t allowVias, allowMicrovia, mode, reserved;
        uint64_t maxShoveDepth, maxExpansions;
    };
    struct DcInteractivePreviewInfo {
        int32_t accepted, failure, traceCount, viaCount, movedCount, findingCount;
        char message[192];
    };
    struct DcInteractiveTrace {
        uint64_t id;
        int64_t ax, ay, bx, by, width;
        int32_t layer, netId;
    };
    struct DcInteractiveVia {
        uint64_t id;
        int64_t x, y, diameter, drill;
        int32_t netId, fromLayer, toLayer, viaType;
    };
    struct DcInteractiveMovedItem {
        uint64_t id;
        int32_t kind, reserved;
        int64_t oldAx, oldAy, oldBx, oldBy;
        int64_t newAx, newAy, newBx, newBy;
    };

    // ── Mirror of DcDrcOptions / DcDrcViolation ──────────────────────────────
    struct DcDrcOptions {
        int64_t defaultClearance, minTraceWidth, minDrill, minAnnularRing, minDrillToDrill;
        int32_t checkConnectivity, checkSkew;
        int64_t minMicroviaDrill, minMicroviaWall;
    };
    struct DcDrcOptionsV2 {
        uint32_t abiVersion, structSize;
        int64_t defaultClearance, minTraceWidth, minDrill, minAnnularRing, minDrillToDrill;
        int32_t checkConnectivity, checkSkew;
        int64_t minMicroviaDrill, minMicroviaWall;
        int64_t minCopperToEdge, minCopperToHole, minCourtyardClearance;
        int32_t checkUnassignedCopper, reserved;
    };
    struct DcDrcViolation {
        int32_t  rule;
        int32_t  _pad;
        int64_t  x, y;
        uint64_t itemA, itemB;
        char     message[192];
    };

    // ── Function pointer types ────────────────────────────────────────────────
    using Fn_BoardCreate  = DcBoardHandle (*)();
    using Fn_BoardDestroy = void (*)(DcBoardHandle);
    using Fn_BoardSetOutline = int32_t (*)(DcBoardHandle, int64_t, int64_t, int64_t, int64_t);
    using Fn_BoardSetOutlinePolygon = int32_t (*)(DcBoardHandle, const DcPoint*, int32_t);
    using Fn_BoardCutoutAdd = int32_t (*)(DcBoardHandle, const DcPoint*, int32_t);
    using Fn_BoardSetLayers  = int32_t (*)(DcBoardHandle, int32_t);
    using Fn_NetSet       = int32_t (*)(DcBoardHandle, int32_t, const char*, int32_t);
    using Fn_NetIslands   = int32_t (*)(DcBoardHandle, int32_t);
    using Fn_ClassSet     = int32_t (*)(DcBoardHandle, int32_t, const char*,
                                        int64_t, int64_t, int64_t, int64_t,
                                        int64_t, int64_t, int32_t);
    using Fn_ClassRoutingPolicySet = int32_t (*)(DcBoardHandle, int32_t, uint64_t,
                                                 uint32_t, int32_t);
    using Fn_LayerPolicySet = int32_t (*)(DcBoardHandle, int32_t, const char*, int32_t,
                                          int32_t, int32_t, int64_t, const char*, const char*);
    using Fn_RuleAreaSet  = int32_t (*)(DcBoardHandle, int32_t, const char*,
                                        const DcPoint*, int32_t, int32_t,
                                        int64_t, int64_t, int32_t, int32_t, int32_t,
                                        const char*, const char*);
    using Fn_RuleAreaHeightSet = int32_t (*)(DcBoardHandle, int32_t, int64_t);
    using Fn_ClassPairSet = int32_t (*)(DcBoardHandle, int32_t, int32_t, int64_t,
                                        const char*, const char*);
    using Fn_CopperZoneSet = int32_t (*)(DcBoardHandle, int32_t, const char*, const DcPoint*,
                                         int32_t, int32_t, int32_t, int64_t, double,
                                         int32_t, const char*, const char*);
    using Fn_FootprintAdd = uint64_t (*)(DcBoardHandle, const char*, const char*,
                                         int64_t, int64_t, double, int32_t,
                                         const DcPadDef*, int32_t);
    using Fn_FootprintAddV2 = uint64_t (*)(DcBoardHandle, const char*, const char*,
                                           int64_t, int64_t, double, int32_t,
                                           const DcPadDefV2*, int32_t);
    using Fn_FootprintRegionAdd = int32_t (*)(DcBoardHandle, uint64_t, int32_t,
                                              const DcPoint*, int32_t,
                                              const DcPoint*, int32_t);
    using Fn_FootprintGeometrySet = int32_t (*)(DcBoardHandle, uint64_t,
                                                int64_t, int64_t, int64_t, int64_t, int64_t,
                                                const DcPoint*, int32_t);
    using Fn_FootprintPlacementSet = int32_t (*)(DcBoardHandle, uint64_t, int32_t,
                                                 const char*, const char*, double,
                                                 int64_t, int32_t, int64_t);
    using Fn_PadClearanceSet = int32_t (*)(DcBoardHandle, uint64_t, const char*, int64_t);
    using Fn_TraceAdd     = uint64_t (*)(DcBoardHandle, int64_t, int64_t,
                                         int64_t, int64_t, int64_t, int32_t, int32_t, int32_t);
    using Fn_TraceMinWidthSet = int32_t (*)(DcBoardHandle, uint64_t, int64_t);
    using Fn_ViaAdd       = uint64_t (*)(DcBoardHandle, int64_t, int64_t,
                                         int64_t, int64_t, int32_t, int32_t, int32_t);
    using Fn_ViaAddV2     = uint64_t (*)(DcBoardHandle, int64_t, int64_t,
                                         int64_t, int64_t, int32_t, int32_t, int32_t, int32_t);
    using Fn_RouteRun     = int32_t (*)(DcBoardHandle, const DcRouteRequest*);
    using Fn_RoutePointCount = int32_t (*)(DcBoardHandle);
    using Fn_RouteGetPoints  = int32_t (*)(DcBoardHandle, DcRoutePoint*, int32_t);
    using Fn_RouteViaCount   = int32_t (*)(DcBoardHandle);
    using Fn_RouteGetVias    = int32_t (*)(DcBoardHandle, DcRouteVia*, int32_t);
    using Fn_InteractiveBegin = int32_t (*)(DcBoardHandle, const DcInteractiveRouteRequest*);
    using Fn_InteractiveUpdate = int32_t (*)(DcBoardHandle, int64_t, int64_t);
    using Fn_InteractivePlaceVia = int32_t (*)(DcBoardHandle, int32_t);
    using Fn_InteractiveCommit = int32_t (*)(DcBoardHandle);
    using Fn_InteractiveCancel = int32_t (*)(DcBoardHandle);
    using Fn_InteractiveInfo = int32_t (*)(DcBoardHandle, DcInteractivePreviewInfo*);
    using Fn_InteractiveGetTraces = int32_t (*)(DcBoardHandle, DcInteractiveTrace*, int32_t);
    using Fn_InteractiveGetVias = int32_t (*)(DcBoardHandle, DcInteractiveVia*, int32_t);
    using Fn_InteractiveGetMoved = int32_t (*)(DcBoardHandle, DcInteractiveMovedItem*, int32_t);
    using Fn_DrcRun       = int32_t (*)(DcBoardHandle, const DcDrcOptions*);
    using Fn_DrcRunV2     = int32_t (*)(DcBoardHandle, const DcDrcOptionsV2*);
    using Fn_DrcGet       = int32_t (*)(DcBoardHandle, int32_t, DcDrcViolation*);
    using Fn_PourRun      = int32_t (*)(DcBoardHandle, int32_t, int32_t,
                                        int64_t, int64_t,
                                        int64_t, int64_t, int64_t, int64_t);
    using Fn_PourZoneRun  = int32_t (*)(DcBoardHandle, int32_t, int64_t);
    struct DcPourStroke { int64_t ax, ay, bx, by, width; };
    using Fn_PourGet      = int32_t (*)(DcBoardHandle, int32_t, DcPourStroke*);
    using Fn_PhysMicrostrip = int32_t (*)(double, double, double, double,
                                          double*, double*, double*);
    using Fn_PhysDiffMicrostrip = double (*)(double, double, double, double, double);
    using Fn_PhysIpcCurrent = double (*)(double, double, double, int32_t);

    Fn_BoardCreate   fn_boardCreate{};
    Fn_BoardDestroy  fn_boardDestroy{};
    Fn_BoardSetOutline fn_boardSetOutline{};
    Fn_BoardSetOutlinePolygon fn_boardSetOutlinePolygon{};
    Fn_BoardCutoutAdd fn_boardCutoutAdd{};
    Fn_BoardSetLayers  fn_boardSetLayers{};
    Fn_NetSet        fn_netSet{};
    Fn_NetIslands    fn_netIslands{};
    Fn_ClassSet      fn_classSet{};
    Fn_ClassRoutingPolicySet fn_classRoutingPolicySet{};
    Fn_LayerPolicySet fn_layerPolicySet{};
    Fn_RuleAreaSet   fn_ruleAreaSet{};
    Fn_RuleAreaHeightSet fn_ruleAreaHeightSet{};
    Fn_ClassPairSet  fn_classPairSet{};
    Fn_CopperZoneSet fn_copperZoneSet{};
    Fn_FootprintAdd  fn_footprintAdd{};
    Fn_FootprintAddV2 fn_footprintAddV2{};
    Fn_FootprintRegionAdd fn_footprintRegionAdd{};
    Fn_FootprintGeometrySet fn_footprintGeometrySet{};
    Fn_FootprintPlacementSet fn_footprintPlacementSet{};
    Fn_PadClearanceSet fn_padClearanceSet{};
    Fn_TraceAdd      fn_traceAdd{};
    Fn_TraceMinWidthSet fn_traceMinWidthSet{};
    Fn_ViaAdd        fn_viaAdd{};
    Fn_ViaAddV2      fn_viaAddV2{};
    Fn_RouteRun      fn_routeRun{};
    Fn_RoutePointCount fn_routePointCount{};
    Fn_RouteGetPoints  fn_routeGetPoints{};
    Fn_RouteViaCount   fn_routeViaCount{};
    Fn_RouteGetVias    fn_routeGetVias{};
    Fn_InteractiveBegin fn_interactiveBegin{};
    Fn_InteractiveUpdate fn_interactiveUpdate{};
    Fn_InteractivePlaceVia fn_interactivePlaceVia{};
    Fn_InteractiveCommit fn_interactiveCommit{};
    Fn_InteractiveCancel fn_interactiveCancel{};
    Fn_InteractiveInfo fn_interactiveInfo{};
    Fn_InteractiveGetTraces fn_interactiveGetTraces{};
    Fn_InteractiveGetVias fn_interactiveGetVias{};
    Fn_InteractiveGetMoved fn_interactiveGetMoved{};
    Fn_DrcRun        fn_drcRun{};
    Fn_DrcRunV2      fn_drcRunV2{};
    Fn_DrcGet        fn_drcGet{};
    Fn_PourRun       fn_pourRun{};
    Fn_PourZoneRun   fn_pourZoneRun{};
    Fn_PourGet       fn_pourGet{};
    Fn_PhysMicrostrip fn_physMicrostrip{};
    Fn_PhysDiffMicrostrip fn_physDiffMicrostrip{};
    Fn_PhysIpcCurrent fn_physIpcCurrent{};

    bool  loadSymbols(QString& err);
    bool  rebuildBoard(ProjectModel* model);
    InteractivePreview readInteractivePreview();
    QHash<uint64_t, int> m_traceModelIndex;
    QHash<uint64_t, int> m_viaModelIndex;
    InteractivePreview m_lastInteractivePreview;
    ProjectModel* m_interactiveModel{};
};
