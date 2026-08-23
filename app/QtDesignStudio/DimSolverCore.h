// DimSolverCore.h — datasheet dimension constraint solver (pure C++, no deps).
//
// Reconstructs exact mm edge coordinates from a dimensioned drawing by treating
// each dimension as a constraint and reconciling the network with least squares.
//
// Strategy (deterministic, no vision):
//   * project extension lines onto an axis  -> candidate EDGE positions (drawing units)
//   * one scale per axis from the overall dimension (handles "not to scale": X != Y)
//   * bind each value to the edge GAP it matches  (axis-projection / spacing compare)
//   * solve  node_b - node_a = value  (weighted least squares, datum gauge = centre 0)
//
// The geometry INPUT (extension-line positions, copper bboxes) is produced by a
// separate extractor. For Inkscape-correct path/bbox geometry, link lib2geom
// (Inkscape's own geometry engine; LGPL-2.1/MPL — linkable from non-GPL code):
//   - Geom::Path / Geom::PathVector        (svg/path-string.cpp reader)
//   - Geom::Rect bounds  -> boundsExact()  (the bbox math behind inkex .bounding_box)
//   - Geom::Crossings / intersection       (line/curve crossings)
// Re-derive nothing geometric that lib2geom already does correctly.
#pragma once
#include <vector>
#include <algorithm>
#include <cmath>
#include <string>

namespace dimsolve {

struct AxisResult {
    std::vector<double> edges;   // solved edge coordinates (mm), datum-centred, sorted
    double residualRms = 0.0;    // mm — how well the dimensions reconcile (≈0 = consistent)
    double residualMax = 0.0;    // mm
    double scale = 0.0;          // mm per drawing-unit for this axis
    double datumDraw = 0.0;      // datum position in DRAWING units (maps copper -> mm)
    int    bound = 0;            // # values successfully bound to edge gaps
    bool   ok = false;
    std::string message;
};

// ---- small dense linear solver: solve (AtA) x = Atb via Gauss elimination -----
inline bool gaussSolve(std::vector<std::vector<double>> M, std::vector<double> b,
                       std::vector<double>& x) {
    const int n = (int)b.size();
    for (int c = 0; c < n; ++c) {
        int piv = c; double best = std::fabs(M[c][c]);
        for (int r = c + 1; r < n; ++r)
            if (std::fabs(M[r][c]) > best) { best = std::fabs(M[r][c]); piv = r; }
        if (best < 1e-12) return false;
        std::swap(M[c], M[piv]); std::swap(b[c], b[piv]);
        for (int r = 0; r < n; ++r) {
            if (r == c) continue;
            double f = M[r][c] / M[c][c];
            for (int k = c; k < n; ++k) M[r][k] -= f * M[c][k];
            b[r] -= f * b[c];
        }
    }
    x.assign(n, 0.0);
    for (int i = 0; i < n; ++i) x[i] = b[i] / M[i][i];
    return true;
}

// Cluster nearby 1-D positions into edge nodes (sorted, merged within tol).
inline std::vector<double> clusterEdges(std::vector<double> pos, double tol) {
    std::sort(pos.begin(), pos.end());
    std::vector<double> nodes; std::vector<double> bucket;
    for (double p : pos) {
        if (bucket.empty() || p - bucket.back() < tol) bucket.push_back(p);
        else { double s = 0; for (double v : bucket) s += v; nodes.push_back(s / bucket.size()); bucket = {p}; }
    }
    if (!bucket.empty()) { double s = 0; for (double v : bucket) s += v; nodes.push_back(s / bucket.size()); }
    return nodes;
}

// Solve one axis.
//   edgeCands : extension-line positions on this axis (drawing units, any origin)
//   values    : dimension values for this axis (mm)
//   overall   : the overall dimension for this axis (mm) — e.g. 8.64 (X) / 6.20 (Y)
inline AxisResult solveAxis(const std::vector<double>& edgeCands,
                            const std::vector<double>& values,
                            double overall,
                            double clusterTol = 2.0,   // drawing units
                            double bindTolMm = 0.12) {  // mm
    AxisResult R;
    std::vector<double> nodes = clusterEdges(edgeCands, clusterTol);
    const int n = (int)nodes.size();
    if (n < 2) { R.message = "need >=2 edges"; return R; }

    double span = nodes.back() - nodes.front();
    if (span <= 0) { R.message = "degenerate span"; return R; }
    R.scale = overall / span;                          // mm per drawing-unit
    // predicted (uncentred) mm position of each node
    std::vector<double> pred(n);
    for (int i = 0; i < n; ++i) pred[i] = nodes[i] * R.scale;

    // bind: each value -> the node pair whose predicted gap matches it (±bindTol)
    struct Con { int a, b; double v; };
    std::vector<Con> cons;
    for (double v : values) {
        int ba = -1, bb = -1; double best = bindTolMm;
        for (int i = 0; i < n; ++i)
            for (int j = i + 1; j < n; ++j) {
                double e = std::fabs((pred[j] - pred[i]) - v);
                if (e < best) { best = e; ba = i; bb = j; }
            }
        if (ba >= 0) cons.push_back({ba, bb, v});
    }
    R.bound = (int)cons.size();
    if (cons.empty()) { R.message = "no values bound"; return R; }

    // centred geometry prior: each node ~ its measured (scaled) position. This both
    // fixes the datum gauge AND keeps the system full-rank when the dimension graph
    // is disconnected (features not chained by a dimension keep their drawn place;
    // dimensioned edges snap exact). Tikhonov regularisation with small weight.
    double meanp = 0; for (double p : pred) meanp += p; meanp /= n;
    std::vector<double> predc(n); for (int i = 0; i < n; ++i) predc[i] = pred[i] - meanp;
    { double mn = 0; for (double v : nodes) mn += v; R.datumDraw = mn / n; }  // datum in draw units

    const double wp = 0.05;                              // prior weight (<< 1)
    const int m = (int)cons.size() + n;                 // constraints + n priors
    std::vector<std::vector<double>> A(m, std::vector<double>(n, 0.0));
    std::vector<double> bvec(m, 0.0), w(m, 1.0);
    for (int k = 0; k < (int)cons.size(); ++k) {
        A[k][cons[k].b] += 1.0; A[k][cons[k].a] -= 1.0; bvec[k] = cons[k].v;
    }
    for (int i = 0; i < n; ++i) {                        // soft prior rows
        int r = (int)cons.size() + i;
        A[r][i] = 1.0; bvec[r] = predc[i]; w[r] = wp;
    }

    // normal equations AtA x = Atb (weighted)
    std::vector<std::vector<double>> AtA(n, std::vector<double>(n, 0.0));
    std::vector<double> Atb(n, 0.0);
    for (int r = 0; r < m; ++r) {
        double ww = w[r] * w[r];
        for (int i = 0; i < n; ++i) {
            if (A[r][i] == 0) continue;
            Atb[i] += ww * A[r][i] * bvec[r];
            for (int j = 0; j < n; ++j) AtA[i][j] += ww * A[r][i] * A[r][j];
        }
    }
    std::vector<double> x;
    if (!gaussSolve(AtA, Atb, x)) { R.message = "singular system"; return R; }

    // residuals over the real constraints
    double s2 = 0, mx = 0;
    for (auto& c : cons) {
        double res = (x[c.b] - x[c.a]) - c.v;
        s2 += res * res; mx = std::max(mx, std::fabs(res));
    }
    R.residualRms = std::sqrt(s2 / cons.size());
    R.residualMax = mx;
    R.edges = x; std::sort(R.edges.begin(), R.edges.end());
    R.ok = true;
    return R;
}

} // namespace dimsolve
