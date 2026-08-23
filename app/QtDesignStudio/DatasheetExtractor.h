// DatasheetExtractor.h — read a datasheet recommended-layout SVG and solve its
// footprint with the dimension constraint solver. Gray (#4b4b4b) = dimensions/
// extension lines + numbers; black (#000000) = copper areas. Left layout only.
//
// Parsing uses Qt's own XML reader (no extra dependency). For full curved-path
// fidelity later, swap the 'd' parser for lib2geom (Geom::PathVector/boundsExact)
// — see DimSolverCore.h. The numbers must be real <text> in the SVG (export the
// vector WITHOUT text-as-path so values are readable).
#pragma once
#include "ProjectModel.h"
#include <QString>

namespace dimextract {
// Parse `svgPath`, solve, and return a footprint (mm, datum-centred). On failure the
// footprint has no pads and *msg (if given) explains why.
ProjFootprint solveFootprintFromSvg(const QString& svgPath, QString* msg = nullptr);
}
