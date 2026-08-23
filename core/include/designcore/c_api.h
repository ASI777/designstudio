#pragma once
// Flat C API consumed by the C# app via P/Invoke. Opaque handle + plain structs.
// Rules: nothing C++ (no classes, exceptions, STL) crosses this boundary;
// every call returns an explicit DcStatus or a count; results larger than a
// scalar are stored on the handle and fetched in a second call, so nothing is
// ever silently truncated.

#include <cstdint>

#if defined(_WIN32)
  #define DC_API extern "C" __declspec(dllexport)
#else
  #define DC_API extern "C" __attribute__((visibility("default")))
#endif

using DcBoardHandle = void*;

// ---- status codes (negative = error). Route failures mirror dc::RouteFail. ----
enum DcStatus : std::int32_t {
    DC_OK                   = 0,
    DC_ERR_INVALID_HANDLE   = -1,
    DC_ERR_INVALID_ARG      = -2,
    DC_ERR_NOT_FOUND        = -3,
    DC_ERR_ROUTE_NO_PATH    = -10,
    DC_ERR_ROUTE_ITER_LIMIT = -11,
    DC_ERR_ROUTE_START_BLOCKED = -12,
    DC_ERR_ROUTE_GOAL_BLOCKED  = -13,
    DC_ERR_ROUTE_INACTIVE      = -14,
    DC_ERR_ROUTE_FIXED_BLOCKER = -15,
    DC_ERR_ROUTE_SHOVE_LIMIT   = -16,
    DC_ERR_ROUTE_ONLINE_DRC    = -17,
    DC_ERR_SOLVER_UNAVAILABLE  = -30,
    DC_ERR_SOLVER_INVALID      = -31,
    DC_ERR_SOLVER_NOT_CONVERGED = -32,
};

struct DcPoint { std::int64_t x, y; };           // nanometres

struct DcPadDef {
    std::int64_t x, y, w, h;
    std::int32_t netId;
    std::int32_t throughHole;
    std::int64_t drill;
    char name[16];                                // pad name/number, UTF-8
};
struct DcPadDefV2 {
    std::int64_t x, y, w, h;
    std::int32_t netId;
    std::int32_t throughHole;
    std::int64_t drill;
    std::int32_t shape;                 // mirrors dc::PadShape
    std::int32_t reserved;
    std::int64_t cornerRadius;
    char name[16];
};

// ---- board lifecycle ----
DC_API DcBoardHandle dc_board_create();
DC_API void          dc_board_destroy(DcBoardHandle h);
DC_API std::int32_t  dc_board_clear(DcBoardHandle h);
DC_API std::int32_t  dc_board_set_outline(DcBoardHandle h, std::int64_t x0, std::int64_t y0, std::int64_t x1, std::int64_t y1);
DC_API std::int32_t  dc_board_set_outline_polygon(DcBoardHandle h, const DcPoint* points,
                                                  std::int32_t pointCount);
DC_API std::int32_t  dc_board_cutout_add(DcBoardHandle h, const DcPoint* points,
                                         std::int32_t pointCount);
DC_API std::int32_t  dc_board_set_copper_layers(DcBoardHandle h, std::int32_t count);

// ---- nets & net classes (caller owns net/class ids — stable across deletes) ----
DC_API std::int32_t  dc_net_set(DcBoardHandle h, std::int32_t netId, const char* name, std::int32_t classId);
DC_API std::int32_t  dc_net_remove(DcBoardHandle h, std::int32_t netId);
DC_API std::int32_t  dc_class_set(DcBoardHandle h, std::int32_t classId, const char* name,
                                  std::int64_t clearance, std::int64_t traceWidth,
                                  std::int64_t viaDiameter, std::int64_t viaDrill,
                                  std::int64_t diffPairGap, std::int64_t maxSkew,
                                  std::int32_t allowMicrovia);
DC_API std::int32_t  dc_class_routing_policy_set(DcBoardHandle h, std::int32_t classId,
                                                 std::uint64_t allowedLayers,
                                                 std::uint32_t allowedViaTypes,
                                                 std::int32_t maxViaCount);
DC_API std::int32_t  dc_layer_policy_set(DcBoardHandle h, std::int32_t layer,
                                         const char* name, std::int32_t role,
                                         std::int32_t preferredDirection,
                                         std::int32_t allowRouting,
                                         std::int64_t copperThickness,
                                         const char* source, const char* sourceRevision);
DC_API std::int32_t  dc_rule_area_set(DcBoardHandle h, std::int32_t areaId, const char* name,
                                      const DcPoint* points, std::int32_t pointCount,
                                      std::int32_t layer, std::int64_t clearance,
                                      std::int64_t minTraceWidth,
                                      std::int32_t forbidRouting, std::int32_t forbidVias,
                                      std::int32_t forbidPlacement,
                                      const char* source, const char* sourceRevision);
DC_API std::int32_t  dc_rule_area_height_set(DcBoardHandle h, std::int32_t areaId,
                                             std::int64_t maxHeight);
DC_API std::int32_t  dc_class_pair_set(DcBoardHandle h, std::int32_t classA,
                                       std::int32_t classB, std::int64_t clearance,
                                       const char* source, const char* sourceRevision);
DC_API std::int32_t  dc_copper_zone_set(DcBoardHandle h, std::int32_t zoneId,
                                        const char* name, const DcPoint* points,
                                        std::int32_t pointCount, std::int32_t netId,
                                        std::int32_t layer, std::int64_t clearance,
                                        double minIslandAreaMm2,
                                        std::int32_t requireConnection,
                                        const char* source, const char* sourceRevision);

// ---- items ----
DC_API std::uint64_t dc_footprint_add(DcBoardHandle h, const char* refDes, const char* libName,
                                      std::int64_t x, std::int64_t y, double rotationDeg, std::int32_t side,
                                      const DcPadDef* pads, std::int32_t padCount);
DC_API std::uint64_t dc_footprint_add_v2(DcBoardHandle h, const char* refDes, const char* libName,
                                         std::int64_t x, std::int64_t y, double rotationDeg,
                                         std::int32_t side, const DcPadDefV2* pads,
                                         std::int32_t padCount);
DC_API std::int32_t dc_footprint_region_add(DcBoardHandle h, std::uint64_t footprintId,
                                            std::int32_t netId,
                                            const DcPoint* outer, std::int32_t outerCount,
                                            const DcPoint* hole, std::int32_t holeCount);
DC_API std::int32_t dc_footprint_geometry_set(DcBoardHandle h, std::uint64_t footprintId,
                                              std::int64_t bodyCx, std::int64_t bodyCy,
                                              std::int64_t bodyW, std::int64_t bodyH,
                                              std::int64_t bodyHeight,
                                              const DcPoint* courtyard,
                                              std::int32_t courtyardCount);
DC_API std::int32_t dc_footprint_placement_set(DcBoardHandle h, std::uint64_t footprintId,
                                               std::int32_t locked,
                                               const char* functionalGroup,
                                               const char* edgeAnchor,
                                               double thermalPowerW,
                                               std::int64_t thermalClearance,
                                               std::int32_t testAccessRequired,
                                               std::int64_t testAccessHalo);
DC_API std::int32_t dc_pad_clearance_set(DcBoardHandle h, std::uint64_t footprintId,
                                         const char* padName, std::int64_t clearance);
DC_API std::int32_t  dc_footprint_move(DcBoardHandle h, std::uint64_t id, std::int64_t x, std::int64_t y, double rotationDeg);
DC_API std::int32_t  dc_item_remove(DcBoardHandle h, std::uint64_t id);
DC_API std::uint64_t dc_trace_add(DcBoardHandle h, std::int64_t ax, std::int64_t ay,
                                  std::int64_t bx, std::int64_t by,
                                  std::int64_t width, std::int32_t layer, std::int32_t netId,
                                  std::int32_t isPour);
// Additive compatibility boundary for intentional package neck-downs.  The
// override is a local manufacturing minimum, not a new preferred class width.
DC_API std::int32_t dc_trace_min_width_set(DcBoardHandle h, std::uint64_t traceId,
                                           std::int64_t minWidth);
DC_API std::uint64_t dc_via_add(DcBoardHandle h, std::int64_t x, std::int64_t y,
                                std::int64_t diameter, std::int64_t drill, std::int32_t netId,
                                std::int32_t fromLayer, std::int32_t toLayer);
// Additive v2 boundary: explicit via technology avoids incorrectly treating
// every blind/buried span as a laser microvia. viaType mirrors dc::ViaType.
DC_API std::uint64_t dc_via_add_v2(DcBoardHandle h, std::int64_t x, std::int64_t y,
                                   std::int64_t diameter, std::int64_t drill, std::int32_t netId,
                                   std::int32_t fromLayer, std::int32_t toLayer,
                                   std::int32_t viaType);

// ---- routing (multi-layer, via-inserting) ----
struct DcRouteRequest {
    std::int32_t netId;
    std::int64_t sx, sy;  std::int32_t startLayer;
    std::int64_t gx, gy;  std::int32_t goalLayer;
    std::int64_t traceWidth, clearance, gridStep;
    std::int32_t allowVias, allowMicrovia;
    std::int64_t viaDiameter, viaDrill;
    double       viaCostMm;
    std::int64_t maxExpansions;
    std::int64_t minDrillToDrill;
};
// Additive route request carrying the fabrication edge/cutout setback.
// Version/size fields prevent clients from accidentally passing the legacy
// layout to the v2 entry point.
struct DcRouteRequestV2 {
    std::uint32_t abiVersion;       // must be 2
    std::uint32_t structSize;       // sizeof(DcRouteRequestV2)
    std::int32_t netId;
    std::int32_t reserved;
    std::int64_t sx, sy;  std::int32_t startLayer;
    std::int32_t startReserved;
    std::int64_t gx, gy;  std::int32_t goalLayer;
    std::int32_t goalReserved;
    std::int64_t traceWidth, clearance, minCopperToEdge, gridStep;
    std::int32_t allowVias, allowMicrovia;
    std::int64_t viaDiameter, viaDrill;
    double       viaCostMm;
    std::int64_t maxExpansions;
    std::int64_t minDrillToDrill;
};
struct DcRoutePoint { std::int64_t x, y; std::int32_t layer; std::int32_t _pad; };
struct DcRouteVia   { std::int64_t x, y; std::int32_t fromLayer, toLayer; };

// Runs the router; result stored on the handle. Returns DC_OK or a route error.
DC_API std::int32_t  dc_route_run(DcBoardHandle h, const DcRouteRequest* req);
DC_API std::int32_t  dc_route_run_v2(DcBoardHandle h, const DcRouteRequestV2* req);
DC_API std::int32_t  dc_route_point_count(DcBoardHandle h);
DC_API std::int32_t  dc_route_get_points(DcBoardHandle h, DcRoutePoint* out, std::int32_t cap);
DC_API std::int32_t  dc_route_via_count(DcBoardHandle h);
DC_API std::int32_t  dc_route_get_vias(DcBoardHandle h, DcRouteVia* out, std::int32_t cap);

// ---- transactional interactive routing (additive ABI v1) ----
struct DcInteractiveRouteRequest {
    std::uint32_t abiVersion;       // must be 1
    std::uint32_t structSize;       // sizeof(DcInteractiveRouteRequest)
    std::int32_t netId;
    std::int32_t startLayer;
    std::int64_t startX, startY;
    std::int64_t traceWidth, clearance, gridStep;
    std::int64_t viaDiameter, viaDrill, minDrillToDrill;
    std::int32_t allowVias, allowMicrovia;
    std::int32_t mode;              // 0 walkaround, 1 push-and-shove
    std::int32_t reserved;
    std::uint64_t maxShoveDepth, maxExpansions;
};

struct DcInteractivePreviewInfo {
    std::int32_t accepted;
    std::int32_t failure;           // mirrors dc::InteractiveRouteFail
    std::int32_t traceCount, viaCount, movedCount, findingCount;
    char message[192];
};

struct DcInteractiveTrace {
    std::uint64_t id;
    std::int64_t ax, ay, bx, by, width;
    std::int32_t layer, netId;
};

struct DcInteractiveVia {
    std::uint64_t id;
    std::int64_t x, y, diameter, drill;
    std::int32_t netId, fromLayer, toLayer, viaType;
};

struct DcInteractiveMovedItem {
    std::uint64_t id;
    std::int32_t kind;              // 0 trace, 1 via
    std::int32_t reserved;
    std::int64_t oldAx, oldAy, oldBx, oldBy;
    std::int64_t newAx, newAy, newBx, newBy;
};

struct DcDrcViolation;

DC_API std::int32_t dc_interactive_route_begin(DcBoardHandle h,
                                                const DcInteractiveRouteRequest* request);
DC_API std::int32_t dc_interactive_route_update(DcBoardHandle h, std::int64_t x,
                                                 std::int64_t y);
DC_API std::int32_t dc_interactive_route_place_via(DcBoardHandle h,
                                                    std::int32_t targetLayer);
DC_API std::int32_t dc_interactive_route_commit(DcBoardHandle h);
DC_API std::int32_t dc_interactive_route_cancel(DcBoardHandle h);
DC_API std::int32_t dc_interactive_route_preview_info(DcBoardHandle h,
                                                       DcInteractivePreviewInfo* out);
DC_API std::int32_t dc_interactive_route_get_traces(DcBoardHandle h,
                                                     DcInteractiveTrace* out,
                                                     std::int32_t cap);
DC_API std::int32_t dc_interactive_route_get_vias(DcBoardHandle h,
                                                   DcInteractiveVia* out,
                                                   std::int32_t cap);
DC_API std::int32_t dc_interactive_route_get_moved(DcBoardHandle h,
                                                    DcInteractiveMovedItem* out,
                                                    std::int32_t cap);
DC_API std::int32_t dc_interactive_route_get_finding(DcBoardHandle h,
                                                      std::int32_t index,
                                                      DcDrcViolation* out);

// ---- DRC ----
struct DcDrcOptions {
    std::int64_t defaultClearance, minTraceWidth, minDrill, minAnnularRing, minDrillToDrill;
    std::int32_t checkConnectivity, checkSkew;
    // Relaxed floors for explicitly typed laser microvias. 0 = engine default.
    std::int64_t minMicroviaDrill, minMicroviaWall;
};
// Versioned additive options. The size/version prefix prevents accidental ABI
// interpretation by clients built against a different layout.
struct DcDrcOptionsV2 {
    std::uint32_t abiVersion;       // must be 2
    std::uint32_t structSize;       // sizeof(DcDrcOptionsV2)
    std::int64_t defaultClearance, minTraceWidth, minDrill, minAnnularRing, minDrillToDrill;
    std::int32_t checkConnectivity, checkSkew;
    std::int64_t minMicroviaDrill, minMicroviaWall;
    std::int64_t minCopperToEdge, minCopperToHole, minCourtyardClearance;
    std::int32_t checkUnassignedCopper;
    std::int32_t reserved;
};
// Rule codes mirror dc::DrcRule (1..17).
struct DcDrcViolation {
    std::int32_t rule;
    std::int32_t _pad;            // explicit alignment padding
    std::int64_t x, y;            // nanometres
    std::uint64_t itemA, itemB;
    char message[192];            // UTF-8, NUL-terminated
};

// Runs DRC with the given options (NULL = defaults); results stored on the
// handle. Returns the violation count (>= 0) or a negative DcStatus.
DC_API std::int32_t  dc_drc_run(DcBoardHandle h, const DcDrcOptions* opt);
DC_API std::int32_t  dc_drc_run_v2(DcBoardHandle h, const DcDrcOptionsV2* opt);
DC_API std::int32_t  dc_drc_get(DcBoardHandle h, std::int32_t index, DcDrcViolation* out);

// ---- copper pours ----
struct DcPourStroke { std::int64_t ax, ay, bx, by, width; };
// Generates pour strokes; stored on the handle. Returns stroke count or error.
DC_API std::int32_t  dc_pour_run(DcBoardHandle h, std::int32_t netId, std::int32_t layer,
                                 std::int64_t lineWidth, std::int64_t clearance,
                                 std::int64_t x0, std::int64_t y0, std::int64_t x1, std::int64_t y1);
DC_API std::int32_t  dc_pour_zone_run(DcBoardHandle h, std::int32_t zoneId,
                                      std::int64_t lineWidth);
DC_API std::int32_t  dc_pour_get(DcBoardHandle h, std::int32_t index, DcPourStroke* out);

// ---- queries ----
DC_API std::int64_t  dc_net_length(DcBoardHandle h, std::int32_t netId);   // nm, -1 on error
DC_API std::int32_t  dc_net_islands(DcBoardHandle h, std::int32_t netId);  // 1 = fully connected
struct DcNetIslandComponent {
    std::int64_t x, y;
    std::uint64_t representativeOwnerId;
    std::uint64_t layers;
    std::int32_t obstacleCount;
    std::int32_t terminalCount;
};
// Returns the required component count when out is NULL/cap is zero, otherwise
// writes up to cap summaries and still returns the required count.
DC_API std::int32_t dc_net_island_components(DcBoardHandle h, std::int32_t netId,
                                              DcNetIslandComponent* out,
                                              std::int32_t cap);

// ---- physics & mathematics engine (stateless; mm / Hz / degC / ohm) ----
// Models documented in include/designcore/physics.h. Multi-output calls
// return DC_OK or DC_ERR_INVALID_ARG; single-value calls return NaN on
// invalid input.
DC_API std::int32_t dc_phys_microstrip(double wMm, double hMm, double tMm, double er,
                                       double* z0, double* eEff, double* delayPsPerMm);
DC_API double dc_phys_microstrip_width(double targetZ0, double hMm, double tMm, double er);
DC_API std::int32_t dc_phys_stripline(double wMm, double bMm, double tMm, double er,
                                      double* z0, double* delayPsPerMm);
DC_API double dc_phys_stripline_width(double targetZ0, double bMm, double tMm, double er);
DC_API double dc_phys_diff_microstrip(double wMm, double sMm, double hMm, double tMm, double er);
DC_API double dc_phys_diff_stripline(double wMm, double sMm, double bMm, double tMm, double er);
DC_API double dc_phys_diff_microstrip_width(double targetZdiff, double sMm, double hMm, double tMm, double er);
DC_API double dc_phys_skin_depth_mm(double fHz);
DC_API double dc_phys_microstrip_loss_db_per_m(double wMm, double hMm, double tMm, double er,
                                               double tanDelta, double fHz);
DC_API double dc_phys_trace_resistance(double wMm, double tMm, double lengthMm, double fHz, double tempC);
DC_API double dc_phys_ipc2221_current(double widthMm, double thickMm, double deltaTC, std::int32_t external);
DC_API double dc_phys_onderdonk_current(double widthMm, double thickMm, double seconds, double ambientC);
DC_API double dc_phys_via_inductance_nh(double heightMm, double drillMm);
DC_API double dc_phys_via_capacitance_pf(double er, double boardThickMm, double padMm, double antipadMm);
DC_API double dc_phys_via_resistance(double heightMm, double drillMm, double platingMm, double tempC);
DC_API double dc_phys_via_thermal_kw(double heightMm, double drillMm, double platingMm);
DC_API double dc_phys_via_current(double drillMm, double platingMm, double deltaTC);
DC_API double dc_phys_crosstalk(double sMm, double hMm);
DC_API double dc_phys_plane_capacitance_pf(double areaMm2, double dielectricMm, double er);

// ---- dense linear algebra (SI hot path, E20) ----
// Solve A*X = B for `rhs` right-hand sides. A is n*n row-major (overwritten with
// its LU factors); B and X are n*rhs in column-major blocks (rhs c at offset c*n).
// Returns DC_OK or DC_ERR_INVALID_ARG.
DC_API std::int32_t dc_dense_lu_solve(double* A, std::int32_t n, const double* B,
                                      std::int32_t rhs, double* X);

// ---- mechanical analysis engine (backend-neutral, additive ABI v1) ----
// The input is a sparse, square stiffness matrix in CSR form.  The native
// engine applies fixed displacement constraints, then solves linear static
// equilibrium with the CPU reference path or an optional CUDA path.  Modal,
// thermal, nonlinear/contact, impact, fatigue and mould-flow workers use the
// same job/result boundary but are intentionally not reported as linear-static
// results by this API.
using DcMechanicalHandle = void*;

enum DcMechanicalBackend : std::int32_t {
    DC_MECHANICAL_AUTO = 0,
    DC_MECHANICAL_CPU  = 1,
    DC_MECHANICAL_CUDA = 2,
};

struct DcMechanicalCsr {
    std::uint32_t abiVersion;       // must be 1
    std::uint32_t structSize;       // sizeof(DcMechanicalCsr) or larger
    std::int32_t rows, cols, nnz;
    const std::int32_t* rowOffsets; // rows + 1
    const std::int32_t* columns;    // nnz
    const double* values;           // nnz
};

struct DcMechanicalOptions {
    std::uint32_t abiVersion;       // must be 1
    std::uint32_t structSize;       // sizeof(DcMechanicalOptions) or larger
    std::int32_t backend;           // DcMechanicalBackend
    std::int32_t maxIterations;
    double tolerance;
    std::int32_t requireSpd;
    std::int32_t reserved;
};

struct DcMechanicalResult {
    std::int32_t status;            // 0 = converged; negative = diagnostic status
    std::int32_t backend;           // backend that produced this result
    std::int32_t converged;
    std::int32_t iterations;
    double residualNorm;
    std::int32_t solutionCount;
    std::int32_t reserved;
    char message[192];
};

DC_API DcMechanicalHandle dc_mechanical_create();
DC_API void dc_mechanical_destroy(DcMechanicalHandle h);
DC_API std::int32_t dc_mechanical_set_system(
    DcMechanicalHandle h, const DcMechanicalCsr* stiffness,
    const double* rhs, const std::int32_t* fixedDofs,
    const double* fixedValues, std::int32_t fixedCount);
DC_API std::int32_t dc_mechanical_solve(DcMechanicalHandle h,
                                         const DcMechanicalOptions* options);
DC_API std::int32_t dc_mechanical_result(DcMechanicalHandle h,
                                          DcMechanicalResult* out);
DC_API std::int32_t dc_mechanical_solution_count(DcMechanicalHandle h);
DC_API std::int32_t dc_mechanical_get_solution(DcMechanicalHandle h,
                                                double* out, std::int32_t cap);
DC_API std::int32_t dc_mechanical_cuda_available();

// Additive ABI v1 for the analytical uniform-cantilever modal reference.
// Inputs use SI units.  Mode shapes are row-major [mode][sample] and are
// normalized to +1 at the free tip.
struct DcMechanicalUniformCantileverModalInput {
    std::uint32_t abiVersion;       // must be 1
    std::uint32_t structSize;       // sizeof(...) or larger
    double lengthM;
    double youngsModulusPa;
    double secondMomentM4;
    double crossSectionAreaM2;
    double densityKgPerM3;
    std::int32_t modeCount;
    std::int32_t shapeSampleCount;
};

struct DcMechanicalModalResult {
    std::int32_t status;
    std::int32_t backend;
    std::int32_t converged;
    std::int32_t modeCount;
    std::int32_t shapeSampleCount;
    std::int32_t reserved;
    char message[192];
};

DC_API std::int32_t dc_mechanical_uniform_cantilever_modal(
    const DcMechanicalUniformCantileverModalInput* input,
    DcMechanicalModalResult* result,
    double* frequenciesHz, std::int32_t frequencyCapacity,
    double* normalizedModeShapes, std::int32_t shapeCapacity);

// ═══════════════════════════════════════════════════════════════════════════
// Horizon 0 — mechatronic foundation surface (see docs/H0-FRAMEWORK.md §3.2).
// Same ABI discipline: opaque handles, plain structs, DcStatus, two-phase fetch.
// Implementations land per unit (U2 doc, U3 geometry, U5 realtime); the stubs in
// c_api_h0_stubs.cpp keep the library linkable until each unit fills them in.
// ═══════════════════════════════════════════════════════════════════════════

using DcDocHandle   = void*;   // a mechatronic project document (schema v3)
using DcShapeHandle = void*;   // an OCCT B-rep solid / compound
using DcRtHandle    = void*;   // a real-time physics world

// Extra status codes for the H0 surface (negative = error).
enum DcStatusH0 : std::int32_t {
    DC_ERR_IO          = -20,   // file open/read/write failure
    DC_ERR_PARSE       = -21,   // malformed document / schema mismatch
    DC_ERR_MIGRATE     = -22,   // version migration failed
    DC_ERR_GEOMETRY    = -23,   // kernel/geometry op failed (degenerate, healing)
    DC_ERR_UNSUPPORTED = -24,   // capability compiled out (e.g. no OCCT/Jolt)
};

// A tessellated mesh, fetched two-phase: the producer stores it on a handle and
// the caller fetches the buffers in a second call (pointers stay valid until the
// next op on that handle). idx is 3*ntris triangle indices; nrm may be null.
struct DcMesh {
    std::int64_t nverts;
    std::int64_t ntris;
    const float* xyz;   // 3*nverts
    const float* nrm;   // 3*nverts or null
    const std::int32_t* idx;  // 3*ntris
};

// Identity/appearance metadata emitted by the same OCCT tessellation pass as
// DcMesh.  The JSON is a digest-bound design-studio.semantic-assembly/2
// payload and remains valid until the next operation on the shape handle.
struct DcSemanticAssemblyJson {
    std::int64_t size;
    const char* json;
};

// ---- document (U2) — load/save the v3 mechatronic project; v2 auto-migrates ---
DC_API std::int32_t dc_doc_open(const char* path, DcDocHandle* out);
DC_API std::int32_t dc_doc_save(DcDocHandle h, const char* path);
DC_API std::int32_t dc_doc_version(DcDocHandle h);          // schema version, or <0 error
DC_API void         dc_doc_destroy(DcDocHandle h);

// ---- geometry (U3) — OCCT B-rep load + tessellation (two-phase mesh fetch) ----
DC_API std::int32_t dc_brep_load_step(const char* path, DcShapeHandle* out);
DC_API std::int32_t dc_brep_tessellate(DcShapeHandle h, double deflectionMm, DcMesh* out);
DC_API std::int32_t dc_brep_semantic_assembly(DcShapeHandle h,
                                              const char* sourceStepSha256,
                                              const char* tessellationSha256,
                                              DcSemanticAssemblyJson* out);
DC_API std::int32_t dc_assembly_flatten(DcDocHandle h, DcMesh* out);   // whole-assembly mesh
DC_API void         dc_shape_destroy(DcShapeHandle h);

// ---- real-time physics (U5) — interactive Jolt world over the assembly --------
DC_API std::int32_t dc_rt_world_new(DcDocHandle doc, DcRtHandle* out);
DC_API std::int32_t dc_rt_step(DcRtHandle h, double dtSeconds);
DC_API std::int32_t dc_rt_body_xform(DcRtHandle h, const char* partId, double* xform16);
DC_API void         dc_rt_world_destroy(DcRtHandle h);
