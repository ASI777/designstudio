// DimensionSolver.h — DesignStudio-facing wrapper around the dimension constraint
// solver (DimSolverCore.h). Turns a dimensioned datasheet sketch into a footprint:
// the component outline is the datum at the grid origin, and every copper area is
// placed in mm by reconciling the dimension values (least squares), per your plan.
//
// Geometry INPUTS (gray dimension lines, parsed numbers, copper bboxes) come from an
// extractor. For Inkscape-correct path/bbox geometry, link lib2geom (Geom::PathVector,
// boundsExact, Geom::Crossings) — see DimSolverCore.h header note.
#pragma once
#include "ProjectModel.h"
#include <QVector>
#include <QRectF>

struct DimSeg { double x1, y1, x2, y2; };   // a gray dimension / extension line (draw units)
struct DimNum { double cx, cy, value; };    // a parsed dimension number (value in mm)

namespace dimstudio {
// Solve the network and place copperBoxes (bboxes in DRAWING units) into a footprint
// (mm), datum-centred on the component outline. overallX/Y = the overall dimensions
// (e.g. 8.64 / 6.20). On failure the returned footprint has no pads.
ProjFootprint buildFootprint(const QVector<DimSeg>& gray,
                             const QVector<DimNum>& numbers,
                             const QVector<QRectF>& copperBoxes,
                             double overallX, double overallY,
                             const QString& ref = QStringLiteral("J1"),
                             const QString& lib = QStringLiteral("EXTRACTED"));
}
