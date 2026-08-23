using System.Numerics;

namespace DesignStudio.Model;

// ============================================================================
// E1. Full 2D electromagnetic solver — Method of Moments RLGC extraction.
//
// The shipped FieldSolver2D solves only Laplace (capacitance) on a finite-
// difference grid: good for single-ended Z0 to ~5 GHz, but it has no coupled
// N×N matrices, no inductance, and no per-frequency loss. At 16–50 GHz the
// design IS the extracted RLGC(f), so S2 replaces it with a boundary-element
// (Method-of-Moments) solver over the conductor and dielectric surfaces.
//
// Method (validated to ~1% vs Hammerstad-Jensen / Cohn, exact for homogeneous
// and partial-fill references):
//   • Equivalent free-space line charges σ on conductor surfaces (potential
//     condition) and on dielectric–dielectric interfaces (normal-D continuity:
//       (εa+εb)/(2ε0(εa−εb))·σ + Ē_n,others = 0 ).
//     Ground planes are exact via image charges, so only the trace and the
//     substrate-top interface are discretised.
//   • Maxwell capacitance matrix C from the free charge (εr-weighted per face);
//     external inductance L = μ0ε0·C_vacuum⁻¹.
//   • R(f) via Wheeler's incremental-inductance rule (recede conductor walls by
//     δ(f)/2, R = ω·ΔL ⇒ the correct √f skin-effect scaling), scaled by the
//     material's surface-roughness factor Ksr(f).
//   • G(f) = ω·C·q·tanδ(f) with the dielectric filling factor q and the causal
//     loss tangent from the Djordjevic-Sarkar model (see Materials.cs).
//   • Modal decomposition of the 2×2 system → even/odd impedances and the
//     differential/common-mode Z and εeff for coupled pairs.
//
// IMPORTANT implementation note (cost a real bug to find): every dielectric
// interface segment must be oriented consistently (here: increasing x, normal
// +y toward the "above" medium). A flipped normal silently negates that
// segment's bound-charge contribution and the dielectric loading cancels out.
//
// Pure-managed and deterministic so it validates in the Linux CI test project;
// this is the C++ `core/mom2d.cpp` hot-path migration candidate noted in the
// roadmap once profiling demands it.
// ============================================================================

public static class MoM2D
{
    private const double Eps0 = 8.8541878128e-12;   // F/m
    private const double Mu0 = 4e-7 * Math.PI;       // H/m
    private const double C0 = 2.99792458e8;          // m/s

    // ---- public result types -------------------------------------------------

    /// <summary>Single-ended result, API-compatible with FieldSolver2D.Solution.</summary>
    public record Solution(double Z0, double EpsEff, double DelayPsPerMm);

    /// <summary>Per-unit-length RLGC for an n-conductor system at one frequency.</summary>
    public record Rlgc(double[,] L, double[,] C, double[,] R, double[,] G, double FreqHz)
    {
        public int N => C.GetLength(0);
        /// <summary>Single-ended characteristic impedance of conductor i (Ω), lossless part.</summary>
        public double Z0(int i = 0) => Math.Sqrt(L[i, i] / C[i, i]);
        /// <summary>Effective permittivity of conductor i.</summary>
        public double EpsEff(int i = 0) => C0 * C0 * L[i, i] * C[i, i];
        /// <summary>Complex propagation constant γ = √((R+jωL)(G+jωC)) for conductor i.</summary>
        public Complex Gamma(int i = 0)
        {
            double w = 2 * Math.PI * FreqHz;
            var zs = new Complex(R[i, i], w * L[i, i]);
            var ys = new Complex(G[i, i], w * C[i, i]);
            return Complex.Sqrt(zs * ys);
        }
        /// <summary>Complex characteristic impedance Zc = √((R+jωL)/(G+jωC)).</summary>
        public Complex Zc(int i = 0)
        {
            double w = 2 * Math.PI * FreqHz;
            var zs = new Complex(R[i, i], w * L[i, i]);
            var ys = new Complex(G[i, i], w * C[i, i]);
            return Complex.Sqrt(zs / ys);
        }
    }

    /// <summary>Even/odd modal impedances and εeff for a coupled pair.</summary>
    public record DiffModes(double ZoddOhm, double ZevenOhm, double ZdiffOhm, double ZcommOhm,
                            double EeOdd, double EeEven);

    // ---- geometry primitives -------------------------------------------------

    private enum Kind { Conductor, Dielectric }

    private struct Seg
    {
        public double Ax, Ay, Bx, By;     // endpoints
        public double Nx, Ny;             // unit normal
        public double Len;
        public Kind Kind;
        public int Cond;                  // conductor index (conductor segs)
        public double FaceEr;             // εr of the medium the conductor face touches
        public double Ea, Eb;             // above/below εr (dielectric segs; normal → 'a')
    }

    private sealed class Geometry
    {
        public readonly List<Seg> Segs = new();
        public readonly List<double> Planes = new();    // ground-plane y positions
        public int NCond;

        public void AddRectConductor(int ci, double cx, double yBot, double w, double t,
                                     double erBottom, double erTop, double erSide, int nSide)
        {
            // four faces: bottom (→erBottom), right (→erSide), top (→erTop), left (→erSide)
            var p = new (double x, double y)[]
            {
                (cx - w / 2, yBot), (cx + w / 2, yBot),
                (cx + w / 2, yBot + t), (cx - w / 2, yBot + t)
            };
            double[] face = { erBottom, erSide, erTop, erSide };
            int nW = Math.Max(2, (int)(nSide * w / (w + t)));
            int nT = Math.Max(2, (int)(nSide * t / (w + t)));
            int[] n = { nW, nT, nW, nT };
            for (int e = 0; e < 4; e++)
            {
                var a = p[e]; var b = p[(e + 1) % 4];
                for (int k = 0; k < n[e]; k++)
                {
                    double u0 = k / (double)n[e], u1 = (k + 1) / (double)n[e];
                    AddSeg(Kind.Conductor,
                        a.x + (b.x - a.x) * u0, a.y + (b.y - a.y) * u0,
                        a.x + (b.x - a.x) * u1, a.y + (b.y - a.y) * u1,
                        ci, face[e], 1, 1);
                }
            }
        }

        /// <summary>Horizontal substrate-top interface at y, air above (or erAbove),
        /// dielectric erBelow below, excluding the conductor footprints. Every
        /// segment is emitted increasing-x so its normal points +y (toward 'a').</summary>
        public void AddInterface(double y, double erAbove, double erBelow,
                                 (double x0, double x1)[] conductorSpans, double span, int nPerStretch)
        {
            // a same-on-both-sides "interface" carries no bound charge — skip it,
            // so the vacuum reference solve is a clean conductor-only problem.
            if (Math.Abs(erAbove - erBelow) < 1e-12) return;
            // breakpoints: sorted conductor edges within [-span, span]
            var edges = new List<double> { -span, span };
            foreach (var (x0, x1) in conductorSpans) { edges.Add(x0); edges.Add(x1); }
            edges.Sort();
            // walk intervals; emit those not inside a conductor
            for (int i = 0; i + 1 < edges.Count; i++)
            {
                double a = edges[i], b = edges[i + 1];
                if (b - a < 1e-9) continue;
                double mid = 0.5 * (a + b);
                bool insideMetal = conductorSpans.Any(s => mid > s.x0 + 1e-12 && mid < s.x1 - 1e-12);
                if (insideMetal) continue;
                bool outerLeft = i == 0;
                bool outerRight = i + 1 == edges.Count - 1;
                MeshStretch(a, b, y, erAbove, erBelow, nPerStretch, outerLeft, outerRight);
            }
        }

        private void MeshStretch(double x0, double x1, double y, double ea, double eb, int n,
                                 bool clusterRight, bool clusterLeft)
        {
            // node distribution clustered toward conductor edges (the non-outer ends)
            var xs = new double[n + 1];
            for (int k = 0; k <= n; k++)
            {
                double u = k / (double)n;
                double s;
                if (!clusterLeft && !clusterRight)
                    s = 0.5 * (1 - Math.Cos(Math.PI * u));              // both ends (cosine)
                else if (clusterRight)                                  // outer-left: cluster at right (inner) end
                    s = 1 - Math.Pow(1 - u, 2.0);
                else                                                    // outer-right: cluster at left (inner) end
                    s = Math.Pow(u, 2.0);
                xs[k] = x0 + (x1 - x0) * s;
            }
            for (int k = 0; k < n; k++)
            {
                if (xs[k + 1] - xs[k] < 1e-12) continue;
                AddSeg(Kind.Dielectric, xs[k], y, xs[k + 1], y, -1, 0, ea, eb);
            }
        }

        private void AddSeg(Kind kind, double ax, double ay, double bx, double by,
                            int cond, double faceEr, double ea, double eb)
        {
            double dx = bx - ax, dy = by - ay, len = Math.Sqrt(dx * dx + dy * dy);
            if (len < 1e-15) return;
            Segs.Add(new Seg
            {
                Ax = ax, Ay = ay, Bx = bx, By = by, Len = len,
                Nx = -dy / len, Ny = dx / len,        // left normal of a→b
                Kind = kind, Cond = cond, FaceEr = faceEr, Ea = ea, Eb = eb
            });
        }
    }

    // ---- analytic segment kernels (validated) --------------------------------

    /// <summary>∫_seg ln|P − r'| ds' for a uniform line charge on segment a→b.</summary>
    private static double SegPotentialIntegral(double px, double py,
                                               double ax, double ay, double bx, double by)
    {
        double tx = bx - ax, ty = by - ay, L = Math.Sqrt(tx * tx + ty * ty);
        if (L < 1e-15) return 0;
        tx /= L; ty /= L;
        double nx = -ty, ny = tx;
        double dx = px - ax, dy = py - ay;
        double u = dx * tx + dy * ty;
        double v = dx * nx + dy * ny;
        double F(double x)
        {
            double r2 = x * x + v * v;
            double term = x * (r2 > 0 ? Math.Log(r2) : 0) - 2 * x;
            if (Math.Abs(v) > 1e-15) term += 2 * v * Math.Atan(x / v);
            return term;
        }
        return 0.5 * (F(L - u) - F(-u));
    }

    /// <summary>E-field (global x,y) of a uniform line charge on segment a→b,
    /// per unit σ, with the 1/(2πε0) factor included.</summary>
    private static (double ex, double ey) SegField(double px, double py,
                                                   double ax, double ay, double bx, double by)
    {
        double tx = bx - ax, ty = by - ay, L = Math.Sqrt(tx * tx + ty * ty);
        if (L < 1e-15) return (0, 0);
        tx /= L; ty /= L;
        double nx = -ty, ny = tx;
        double dx = px - ax, dy = py - ay;
        double u = dx * tx + dy * ty;
        double v = dx * nx + dy * ny;
        double r0 = u * u + v * v, rL = (u - L) * (u - L) + v * v;
        double et = (r0 > 0 && rL > 0) ? 0.5 * (Math.Log(r0) - Math.Log(rL)) : 0;
        double en = Math.Abs(v) > 1e-15 ? Math.Atan(u / v) - Math.Atan((u - L) / v) : 0;
        double ex = (et * tx + en * nx) / (2 * Math.PI * Eps0);
        double ey = (et * ty + en * ny) / (2 * Math.PI * Eps0);
        return (ex, ey);
    }

    // image transforms: (sign, y-reflection map). Microstrip→1, stripline→series.
    private static List<(double sign, double y0, double scale)> ImageMaps(List<double> planes, int kmax = 40)
    {
        // a reflected y is computed as: y' = y0 + scale*(y - p0)  (we encode via lambda below)
        // To keep it allocation-light we return parameters consumed by ReflectY.
        var maps = new List<(double sign, double y0, double scale)>();
        if (planes.Count == 1)
        {
            double p = planes[0];
            maps.Add((-1.0, 2 * p, -1.0));            // y' = 2p − y
        }
        else if (planes.Count >= 2)
        {
            double p0 = Math.Min(planes[0], planes[1]);
            double p1 = Math.Max(planes[0], planes[1]);
            double b = p1 - p0;
            for (int k = -kmax; k <= kmax; k++)
            {
                if (k != 0) maps.Add((1.0, p0 + 2 * k * b, 1.0));     // y' = (y−p0)+2kb+p0
                maps.Add((-1.0, p0 + 2 * k * b, -1.0));               // y' = −(y−p0)+2kb+p0
            }
        }
        return maps;
    }

    private static double ReflectY(double y, double y0, double scale, double p0)
        => scale > 0 ? (y - p0) + y0 : -(y - p0) + y0;

    // ---- the solve -----------------------------------------------------------

    /// <summary>Maxwell capacitance matrix (F/m). If vacuum, all εr collapse to 1.</summary>
    private static double[,] SolveCapacitance(Geometry g, bool vacuum)
    {
        var segs = g.Segs;
        int N = segs.Count;
        var maps = ImageMaps(g.Planes);
        double p0 = g.Planes.Count > 0 ? g.Planes.Min() : 0;

        var M = new double[N, N];
        for (int i = 0; i < N; i++)
        {
            var si = segs[i];
            double pmx = 0.5 * (si.Ax + si.Bx), pmy = 0.5 * (si.Ay + si.By);
            for (int j = 0; j < N; j++)
            {
                var sj = segs[j];
                if (si.Kind == Kind.Conductor)
                {
                    double val = SegPotentialIntegral(pmx, pmy, sj.Ax, sj.Ay, sj.Bx, sj.By);
                    foreach (var (sign, y0, scale) in maps)
                        val += sign * SegPotentialIntegral(pmx, pmy,
                            sj.Ax, ReflectY(sj.Ay, y0, scale, p0), sj.Bx, ReflectY(sj.By, y0, scale, p0));
                    M[i, j] = -val / (2 * Math.PI * Eps0);
                }
                else if (vacuum)
                {
                    // vacuum reference: no dielectric loading — pin σ=0 with an
                    // identity row (off-diagonals zeroed, not just the diagonal).
                    M[i, j] = i == j ? 1.0 : 0.0;
                }
                else
                {
                    double ex = 0, ey = 0;
                    if (j != i)
                    {
                        var (fx, fy) = SegField(pmx, pmy, sj.Ax, sj.Ay, sj.Bx, sj.By);
                        ex += fx; ey += fy;
                    }
                    foreach (var (sign, y0, scale) in maps)
                    {
                        var (fx, fy) = SegField(pmx, pmy,
                            sj.Ax, ReflectY(sj.Ay, y0, scale, p0), sj.Bx, ReflectY(sj.By, y0, scale, p0));
                        ex += sign * fx; ey += sign * fy;
                    }
                    M[i, j] = ex * si.Nx + ey * si.Ny;     // normal field from segment j
                    // D-normal continuity self term (εa ≠ εb guaranteed by AddInterface)
                    if (j == i) M[i, j] += (si.Ea + si.Eb) / (2 * Eps0 * (si.Ea - si.Eb));
                }
            }
        }

        // RHS: excite each conductor to 1 V in turn. The dense factor + solve is
        // the hot path — DenseSolver runs it in the C++ core when available and
        // falls back to its managed LU (identical result) otherwise (E20).
        var rhsCols = new double[g.NCond][];
        for (int exc = 0; exc < g.NCond; exc++)
        {
            var rhs = new double[N];
            for (int i = 0; i < N; i++)
                if (segs[i].Kind == Kind.Conductor && segs[i].Cond == exc) rhs[i] = 1.0;
            rhsCols[exc] = rhs;
        }
        var sols = Solve.DenseSolver.SolveColumns(M, rhsCols);

        var Q = new double[g.NCond, g.NCond];
        for (int exc = 0; exc < g.NCond; exc++)
        {
            var sig = sols[exc];
            for (int ci = 0; ci < g.NCond; ci++)
            {
                double q = 0;
                for (int i = 0; i < N; i++)
                    if (segs[i].Kind == Kind.Conductor && segs[i].Cond == ci)
                    {
                        double w = vacuum ? 1.0 : segs[i].FaceEr;   // free charge = εr_face · σ
                        q += sig[i] * segs[i].Len * w;
                    }
                Q[ci, exc] = q;
            }
        }
        return Q;
    }

    // ---- builders for the standard cross-sections ----------------------------

    private static Geometry MicrostripGeom(double[] cx, double w, double t, double h, double er,
                                           int nSide, double spanMm, int nIface)
    {
        var g = new Geometry { NCond = cx.Length };
        g.Planes.Add(0.0);                                   // ground at y=0
        var spans = new (double, double)[cx.Length];
        for (int i = 0; i < cx.Length; i++)
        {
            g.AddRectConductor(i, cx[i], h, w, t, erBottom: er, erTop: 1.0, erSide: 1.0, nSide);
            spans[i] = (cx[i] - w / 2, cx[i] + w / 2);
        }
        g.AddInterface(h, erAbove: 1.0, erBelow: er, spans, spanMm, nIface);
        return g;
    }

    private static Geometry StriplineGeom(double[] cx, double w, double t, double h, double er,
                                          int nSide)
    {
        // symmetric: planes a distance h from each trace face; homogeneous fill (no interface)
        var g = new Geometry { NCond = cx.Length };
        g.Planes.Add(0.0);
        g.Planes.Add(2 * h + t);                             // top plane h above the trace top
        for (int i = 0; i < cx.Length; i++)
            g.AddRectConductor(i, cx[i], h, w, t, erBottom: er, erTop: er, erSide: er, nSide);
        return g;
    }

    // ---- high-level API ------------------------------------------------------

    /// <summary>Surface microstrip Z0/εeff (FieldSolver2D-compatible).</summary>
    public static Solution Microstrip(double wMm, double tMm, double hMm, double er)
    {
        double span = Span(wMm, hMm);
        var ge = MicrostripGeom(new[] { 0.0 }, wMm, tMm, hMm, er, NSide(wMm, tMm, hMm), span, NIface(hMm, span));
        var ga = MicrostripGeom(new[] { 0.0 }, wMm, tMm, hMm, 1.0, NSide(wMm, tMm, hMm), span, NIface(hMm, span));
        double cer = SolveCapacitance(ge, vacuum: false)[0, 0];
        double cair = SolveCapacitance(ga, vacuum: true)[0, 0];
        return SingleEnded(cer, cair);
    }

    /// <summary>Symmetric stripline Z0/εeff (FieldSolver2D-compatible).</summary>
    public static Solution Stripline(double wMm, double tMm, double hMm, double er)
    {
        var ge = StriplineGeom(new[] { 0.0 }, wMm, tMm, hMm, er, NSide(wMm, tMm, hMm));
        double cer = SolveCapacitance(ge, vacuum: false)[0, 0];
        double cair = SolveCapacitance(ge, vacuum: true)[0, 0];   // vacuum collapses εr→1
        return SingleEnded(cer, cair);
    }

    /// <summary>Edge-coupled microstrip differential/common modes.</summary>
    public static DiffModes CoupledMicrostrip(double wMm, double sMm, double tMm, double hMm, double er)
    {
        double d = (wMm + sMm) / 2;
        double span = Span(wMm, hMm) + d;
        var cx = new[] { -d, d };
        var ce = SolveCapacitance(MicrostripGeom(cx, wMm, tMm, hMm, er, NSide(wMm, tMm, hMm), span, NIface(hMm, span)), false);
        var ca = SolveCapacitance(MicrostripGeom(cx, wMm, tMm, hMm, 1.0, NSide(wMm, tMm, hMm), span, NIface(hMm, span)), true);
        return Modal(ce, ca);
    }

    /// <summary>Edge-coupled symmetric stripline differential/common modes.</summary>
    public static DiffModes CoupledStripline(double wMm, double sMm, double tMm, double hMm, double er)
    {
        double d = (wMm + sMm) / 2;
        var cx = new[] { -d, d };
        var ce = SolveCapacitance(StriplineGeom(cx, wMm, tMm, hMm, er, NSide(wMm, tMm, hMm)), false);
        var ca = SolveCapacitance(StriplineGeom(cx, wMm, tMm, hMm, 1.0, NSide(wMm, tMm, hMm)), true);
        return Modal(ce, ca);
    }

    /// <summary>
    /// Full single-ended RLGC(f) for one trace, using the causal material model
    /// for Dk(f)/Df(f) and its surface-roughness factor for conductor loss.
    /// microstrip = true for surface trace over one plane, false for stripline.
    /// </summary>
    public static Rlgc ExtractRlgc(double wMm, double tMm, double hMm, MaterialDef mat,
                                   double fHz, bool microstrip)
    {
        double fGHz = fHz / 1e9;
        double er = mat.DkCausalAt(fGHz);
        double tan = mat.DfCausalAt(fGHz);
        double w = 2 * Math.PI * fHz;

        double cer, cair;
        if (microstrip)
        {
            double span = Span(wMm, hMm);
            int ns = NSide(wMm, tMm, hMm), ni = NIface(hMm, span);
            cer = SolveCapacitance(MicrostripGeom(new[] { 0.0 }, wMm, tMm, hMm, er, ns, span, ni), false)[0, 0];
            cair = SolveCapacitance(MicrostripGeom(new[] { 0.0 }, wMm, tMm, hMm, 1.0, ns, span, ni), true)[0, 0];
        }
        else
        {
            int ns = NSide(wMm, tMm, hMm);
            cer = SolveCapacitance(StriplineGeom(new[] { 0.0 }, wMm, tMm, hMm, er, ns), false)[0, 0];
            cair = SolveCapacitance(StriplineGeom(new[] { 0.0 }, wMm, tMm, hMm, er, ns), true)[0, 0];
        }

        double L = Mu0 * Eps0 / cair;                        // external inductance, H/m
        double C = cer;

        // R(f): Wheeler incremental inductance — recede every conducting wall by
        // δ(f)/2, R = ω·ΔL, then scale by surface roughness. The trace shrinks by
        // δ on each dimension (δ/2 per side) and the trace-to-plane gap grows by a
        // full δ (the trace face recedes δ/2 and the plane recedes δ/2), which is
        // what folds the ground-plane loss into R.
        double delta = SurfaceRoughness.SkinDepth(fHz);
        double dMm = delta * 1e3;
        double wRec = Math.Max(wMm - dMm, wMm * 0.5);
        double tRec = Math.Max(tMm - dMm, tMm * 0.5);
        double cairRec;
        if (microstrip)
        {
            double span = Span(wMm, hMm);
            int ns = NSide(wMm, tMm, hMm), ni = NIface(hMm, span);
            cairRec = SolveCapacitance(MicrostripGeom(new[] { 0.0 }, wRec, tRec, hMm + dMm, 1.0, ns, span, ni), true)[0, 0];
        }
        else
        {
            int ns = NSide(wMm, tMm, hMm);
            cairRec = SolveCapacitance(StriplineGeom(new[] { 0.0 }, wRec, tRec, hMm + dMm, er, ns), true)[0, 0];
        }
        double Lrec = Mu0 * Eps0 / cairRec;
        double R = Math.Max(0, w * (Lrec - L)) * mat.RoughnessFactor(fHz);

        // G(f) = ω·C·q·tanδ with dielectric filling factor q = (εeff−1)/(εr−1)
        double eeff = C0 * C0 * L * C;
        double q = er > 1.0001 ? Math.Clamp((eeff - 1) / (er - 1), 0, 1) : 1.0;
        double G = w * C * q * tan;

        return new Rlgc(
            new[,] { { L } }, new[,] { { C } }, new[,] { { R } }, new[,] { { G } }, fHz);
    }

    // ---- helpers -------------------------------------------------------------

    private static Solution SingleEnded(double cer, double cair)
    {
        double ee = cer / cair;
        double z0 = 1.0 / (C0 * Math.Sqrt(cer * cair));
        double delayPsPerMm = 3.3356409519815204 * Math.Sqrt(ee);   // (1/c)·√ee in ps/mm
        return new Solution(z0, ee, delayPsPerMm);
    }

    private static DiffModes Modal(double[,] cEr, double[,] cAir)
    {
        // symmetric pair: even drive [1,1], odd drive [1,−1]
        double cEven = cEr[0, 0] + cEr[0, 1];
        double cOdd = cEr[0, 0] - cEr[0, 1];
        double aEven = cAir[0, 0] + cAir[0, 1];
        double aOdd = cAir[0, 0] - cAir[0, 1];
        double zEven = 1.0 / (C0 * Math.Sqrt(Math.Abs(cEven * aEven)));
        double zOdd = 1.0 / (C0 * Math.Sqrt(Math.Abs(cOdd * aOdd)));
        return new DiffModes(zOdd, zEven, 2 * zOdd, zEven / 2, cOdd / aOdd, cEven / aEven);
    }

    // mesh-density heuristics validated against HJ/Cohn: Z0 ~1%, εeff ~1.5% at
    // N≈420 (finer meshes change the answer by <0.3%, so this is the sweet spot
    // for accuracy vs the per-frequency solve cost in the channel cascade).
    private static double Span(double w, double h) => Math.Max(25 * h, 8 * w);
    private static int NSide(double w, double t, double h) => 60;
    private static int NIface(double h, double span) => 150;
}
