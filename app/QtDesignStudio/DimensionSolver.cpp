#include "DimensionSolver.h"
#include "DimSolverCore.h"
#include <cmath>
#include <vector>

namespace dimstudio {

ProjFootprint buildFootprint(const QVector<DimSeg>& gray,
                             const QVector<DimNum>& numbers,
                             const QVector<QRectF>& copperBoxes,
                             double overallX, double overallY,
                             const QString& ref, const QString& lib) {
    // axis edge candidates: vertical segments mark X-edges, horizontal mark Y-edges
    std::vector<double> xc, yc;
    const double tol = 1.0;
    for (const DimSeg& s : gray) {
        if (std::fabs(s.x2 - s.x1) < tol) xc.push_back((s.x1 + s.x2) * 0.5);   // vertical
        if (std::fabs(s.y2 - s.y1) < tol) yc.push_back((s.y1 + s.y2) * 0.5);   // horizontal
    }
    std::vector<double> vals;
    vals.reserve(numbers.size());
    for (const DimNum& n : numbers) vals.push_back(n.value);

    dimsolve::AxisResult rx = dimsolve::solveAxis(xc, vals, overallX);
    dimsolve::AxisResult ry = dimsolve::solveAxis(yc, vals, overallY);

    ProjFootprint fp;
    fp.ref = ref; fp.lib = lib;
    fp.bodyW_mm = overallX; fp.bodyH_mm = overallY;
    if (!rx.ok || !ry.ok) return fp;     // caller checks fp.pads.isEmpty()

    const double sx = rx.scale, sy = ry.scale, dx = rx.datumDraw, dy = ry.datumDraw;

    int idx = 1;
    for (const QRectF& b : copperBoxes) {
        const double w = b.width() * sx, h = b.height() * sy;
        if (w < 0.05 || h < 0.05) continue;
        ProjPad p;
        p.x_mm = (b.center().x() - dx) * sx;
        p.y_mm = (dy - b.center().y()) * sy;             // y up (drawing y is down)
        p.w_mm = w; p.h_mm = h;
        const bool hole = (w > 0.35 && w < 0.8 && h > 0.35 && h < 0.8 && std::fabs(w - h) < 0.25);
        const bool pad  = (w > 0.15 && w < 0.5 && h > 0.5 && h < 1.2);
        if (hole) {
            p.shape = QStringLiteral("circle"); p.throughHole = true;
            p.drillMm = std::min(w, h); p.name = QStringLiteral("H%1").arg(idx);
        } else if (pad) {
            p.shape = QStringLiteral("rect"); p.name = QString::number(idx);
        } else {
            p.shape = QStringLiteral("roundrect"); p.cornerR_mm = 0.1;
            p.name = QStringLiteral("M%1").arg(idx);
        }
        fp.pads.push_back(p);
        ++idx;
    }
    return fp;
}

} // namespace dimstudio
