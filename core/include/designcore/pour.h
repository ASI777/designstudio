#pragma once
// Copper pour generation: solid fills (ground / power planes) built as dense
// scanline strokes that keep net-class clearance from all other-net copper and
// connect directly to same-net copper. Strokes are ordinary trace segments
// flagged isPour, so rendering, DRC verification and Gerber export all reuse
// the trace pipeline.

#include "pcb_model.h"

namespace dc {

struct PourRequest {
    int netId = -1;
    int layer = 0;
    Rect region;                       // typically the board outline
    std::vector<Vec2> zonePolygon;     // optional authoritative pour boundary
    coord_t lineWidth = 300'000;       // 0.3 mm strokes
    coord_t clearance = 250'000;       // copper-to-pour gap
};

struct PourResult {
    std::vector<TraceSegment> strokes; // isPour = true, width = lineWidth
};

PourResult generatePour(const Board& board, const PourRequest& req);

} // namespace dc
