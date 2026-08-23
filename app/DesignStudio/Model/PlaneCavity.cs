using System.Numerics;

namespace DesignStudio.Model;

// ============================================================================
// E6. Plane-cavity PDN solver — spatial Z(f) with decap placement.
//
// The lumped PdnAnalyzer treats the plane as one capacitor and every decap as
// if it sat on top of the load. Above ~100 MHz that is wrong: the power/ground
// plane pair is a 2-D resonant cavity, and a decap only helps where it can
// reach — its effectiveness depends on *where* it is relative to the load and
// the mode antinodes.
//
// This solves the rectangular plane pair analytically with the cavity-resonator
// modal expansion (Lei/Okhmatovski form):
//
//   Z_pq(ω) = jωμd/(ab) · Σ_m Σ_n  Cm²Cn²·sinc²·cos(mπxp/a)cos(nπyp/b)·
//                                        cos(mπxq/a)cos(nπyq/b) / (k² − k_mn²)
//
// with k² = ω²μεrε0(1 − j·tanδ_eff). The (0,0) mode is exactly the plane
// capacitance; k = k_mn gives the plane resonances f_mn = (c/2√εr)·√((m/a)²+(n/b)²).
// Decaps are ports terminated by their series R-L-C; the input impedance at the
// BGA follows from the multiport Schur complement
//   Z_in = Z00 − Z0d·(Zdd + diag(Zcap))⁻¹·Zd0 ,  then ∥ the VRM branch.
//
// From that we rank each decap by how much Z it removes and, at the worst
// frequency, scan the plane for the highest-|Z| spot to suggest where the next
// decap should go — the "guesses upgraded to solved" deliverable.
//
// Validated: (0,0) mode = plane C to 0.02 %, resonances on the analytic f_mn,
// a decap crushes Z near its SRF. Full 3-D / multi-cavity stitching is later.
// ============================================================================

public sealed class PlaneCavity
{
    private const double Eps0 = 8.8541878128e-12;
    private const double Mu0 = 4e-7 * Math.PI;
    private const double C0 = 2.99792458e8;
    private const double SigmaCu = 5.8e7;

    public record Port(double XMm, double YMm, string Label);
    public record Decap(int PortIndex, double C, double EslH, double EsrOhm, string RefDes);

    private readonly double _aMm, _bMm, _dMm, _er, _tanD, _portMm;
    private readonly List<Port> _ports;
    private readonly List<Decap> _decaps;
    private readonly double _vrmR, _vrmL;
    private readonly (int m, int n, double kx, double ky, double cc)[] _modes;

    /// <param name="aMm">plane width</param><param name="bMm">plane height</param>
    /// <param name="dMm">plane-pair separation</param>
    /// <param name="portMm">port (antipad) size for the sinc smoothing</param>
    public PlaneCavity(double aMm, double bMm, double dMm, double er, double tanD,
                       IReadOnlyList<Port> ports, IReadOnlyList<Decap> decaps,
                       double vrmR = 0.001, double vrmLnH = 10, double portMm = 1.0,
                       double fMaxHz = 2e9)
    {
        _aMm = aMm; _bMm = bMm; _dMm = dMm; _er = er; _tanD = tanD; _portMm = portMm;
        _ports = ports.ToList(); _decaps = decaps.ToList();
        _vrmR = vrmR; _vrmL = vrmLnH * 1e-9;
        _modes = BuildModes(aMm * 1e-3, bMm * 1e-3, er, fMaxHz);
    }

    // ---- modal Z matrix ------------------------------------------------------

    private static (int, int, double, double, double)[] BuildModes(double am, double bm, double er, double fMax)
    {
        // keep modes whose resonance is within 3× the top frequency (plus the
        // DC/capacitive (0,0)); cap the order so the sum stays cheap.
        double kMax = 3 * (2 * Math.PI * fMax) * Math.Sqrt(er) / C0;
        int mMax = Math.Min(60, (int)(kMax * am / Math.PI) + 2);
        int nMax = Math.Min(60, (int)(kMax * bm / Math.PI) + 2);
        var list = new List<(int, int, double, double, double)>();
        for (int m = 0; m <= mMax; m++)
        {
            double kx = m * Math.PI / am, cm = m == 0 ? 1 : Math.Sqrt(2.0);
            for (int n = 0; n <= nMax; n++)
            {
                double ky = n * Math.PI / bm, cn = n == 0 ? 1 : Math.Sqrt(2.0);
                if (kx * kx + ky * ky > kMax * kMax && (m != 0 || n != 0)) continue;
                list.Add((m, n, kx, ky, cm * cm * cn * cn));
            }
        }
        return list.ToArray();
    }

    private double TanDEff(double f)
    {
        double skin = Math.Sqrt(2.0 / (2 * Math.PI * f * Mu0 * SigmaCu));   // skin depth
        return Math.Min(1.0, _tanD + skin / (_dMm * 1e-3));                 // + plane conductor loss
    }

    /// <summary>Full port Z matrix (Ω) at frequency f.</summary>
    public Complex[,] ZMatrix(double f)
    {
        double w = 2 * Math.PI * f;
        double am = _aMm * 1e-3, bm = _bMm * 1e-3, dm = _dMm * 1e-3, ts = _portMm * 1e-3;
        Complex k2 = w * w * Mu0 * _er * Eps0 * new Complex(1, -TanDEff(f));
        Complex pre = new Complex(0, w * Mu0 * dm / (am * bm));
        int P = _ports.Count;
        var Z = new Complex[P, P];

        // per-port modal shape cache
        var shape = new double[P][];
        for (int p = 0; p < P; p++)
        {
            shape[p] = new double[_modes.Length];
            for (int i = 0; i < _modes.Length; i++)
            {
                var (_, _, kx, ky, _) = _modes[i];
                shape[p][i] = Math.Cos(kx * _ports[p].XMm * 1e-3) * Math.Cos(ky * _ports[p].YMm * 1e-3);
            }
        }

        for (int i = 0; i < _modes.Length; i++)
        {
            var (_, _, kx, ky, cc) = _modes[i];
            double sm = Sinc(kx * ts / 2), sn = Sinc(ky * ts / 2);
            Complex denom = k2 - (kx * kx + ky * ky);
            if (denom == Complex.Zero) denom = 1e-30;
            Complex fac = cc * sm * sm * sn * sn / denom;
            for (int p = 0; p < P; p++)
            {
                Complex fp = fac * shape[p][i];
                for (int q = p; q < P; q++) Z[p, q] += fp * shape[q][i];
            }
        }
        for (int p = 0; p < P; p++)
            for (int q = p; q < P; q++) { Z[p, q] *= pre; if (q != p) Z[q, p] = Z[p, q]; }
        return Z;
    }

    private Complex CapZ(double f, Decap d)
    {
        double w = 2 * Math.PI * f;
        return new Complex(d.EsrOhm, w * d.EslH - 1.0 / (w * d.C));
    }

    /// <summary>Input impedance at port 0 (the BGA) with decaps terminated and the VRM in parallel.</summary>
    public Complex Zin(double f)
    {
        var Z = ZMatrix(f);
        Complex z00 = ReduceToPort0(Z, f);
        Complex yVrm = 1.0 / new Complex(_vrmR, 2 * Math.PI * f * _vrmL);
        return 1.0 / (1.0 / z00 + yVrm);
    }

    // Schur-complement the decap ports (terminated by their cap impedance) out of the matrix.
    private Complex ReduceToPort0(Complex[,] Z, double f, int skipDecap = -1)
    {
        var dec = _decaps.Where((_, i) => i != skipDecap).ToList();
        int K = dec.Count;
        if (K == 0) return Z[0, 0];

        // assemble (Zdd + diag(Zcap)) and Zd0 over decap ports
        var A = new Complex[K, K];
        var rhs = new Complex[K];
        for (int i = 0; i < K; i++)
        {
            int pi = dec[i].PortIndex;
            for (int j = 0; j < K; j++) A[i, j] = Z[pi, dec[j].PortIndex];
            A[i, i] += CapZ(f, dec[i]);
            rhs[i] = Z[pi, 0];
        }
        var x = SolveComplex(A, rhs);                 // x = (Zdd+diag)⁻¹ Zd0
        Complex corr = 0;
        for (int i = 0; i < K; i++) corr += Z[0, dec[i].PortIndex] * x[i];
        return Z[0, 0] - corr;
    }

    // ---- analysis ------------------------------------------------------------

    public record Sweep(double FreqHz, double ZOhm);
    public record DecapRank(string RefDes, double WorstFreqContribOhm);
    public record PlacementHint(double XMm, double YMm, double FreqHz, string Reason);
    public record Result(double TargetZOhm, List<Sweep> Curve, double WorstZOhm, double WorstFreqHz,
                         List<double> ResonancesHz, List<DecapRank> DecapRanking, PlacementHint? Hint);

    public Result Analyze(double targetZOhm, double fStart = 1e5, double fStop = 2e9, int points = 161)
    {
        var curve = new List<Sweep>(points + 1);
        int K = _decaps.Count;
        var worstWithout = new double[K];               // band-worst Z with decap i removed
        double worstZ = 0, worstF = fStart;

        // single pass: ZMatrix once per frequency, reused for the baseline and
        // every "decap removed" reduction so effectiveness is a band metric.
        for (int kk = 0; kk <= points; kk++)
        {
            double f = fStart * Math.Pow(fStop / fStart, kk / (double)points);
            var Z = ZMatrix(f);
            Complex yVrm = 1.0 / new Complex(_vrmR, 2 * Math.PI * f * _vrmL);
            double zBase = (1.0 / (1.0 / ReduceToPort0(Z, f, -1) + yVrm)).Magnitude;
            curve.Add(new Sweep(f, zBase));
            if (zBase > worstZ) { worstZ = zBase; worstF = f; }
            for (int i = 0; i < K; i++)
            {
                double zi = (1.0 / (1.0 / ReduceToPort0(Z, f, skipDecap: i) + yVrm)).Magnitude;
                if (zi > worstWithout[i]) worstWithout[i] = zi;
            }
        }

        // analytic plane resonances within band
        var res = new List<double>();
        for (int m = 0; m <= 4; m++)
            for (int n = 0; n <= 4; n++)
            {
                if (m == 0 && n == 0) continue;
                double fmn = C0 / (2 * Math.Sqrt(_er)) *
                             Math.Sqrt(Math.Pow(m / (_aMm * 1e-3), 2) + Math.Pow(n / (_bMm * 1e-3), 2));
                if (fmn <= fStop) res.Add(fmn);
            }
        res.Sort();

        // effectiveness = how much worse the band-worst Z gets without each decap
        // (positive ⇒ helpful; negative ⇒ the decap creates a net anti-resonance)
        var ranking = new List<DecapRank>();
        for (int i = 0; i < K; i++)
            ranking.Add(new DecapRank(_decaps[i].RefDes, worstWithout[i] - worstZ));
        ranking.Sort((x, y) => y.WorstFreqContribOhm.CompareTo(x.WorstFreqContribOhm));

        // placement hint: at the worst frequency, scan the plane for the highest
        // transfer impedance to the BGA — that is where a decap bites hardest.
        PlacementHint? hint = null;
        if (worstZ > targetZOhm)
        {
            var (hx, hy) = HighestTransferZ(worstF);
            bool nearRes = res.Any(r => Math.Abs(r - worstF) < 0.15 * r);
            hint = new PlacementHint(hx, hy, worstF,
                nearRes ? $"plane resonance near {worstF / 1e6:F0} MHz — place a decap at the antinode ({hx:F1}, {hy:F1}) mm"
                        : $"Z peak at {worstF / 1e6:F0} MHz — add a decap resonant there near ({hx:F1}, {hy:F1}) mm");
        }

        return new Result(targetZOhm, curve, worstZ, worstF, res, ranking, hint);
    }

    private (double x, double y) HighestTransferZ(double f)
    {
        // sample a grid of candidate ports against the BGA (port 0) and pick the
        // location of maximum |Z(bga, candidate)| — the worst-coupled antinode.
        var Z = ZMatrix(f);
        double bx = _ports[0].XMm, by = _ports[0].YMm;
        double best = -1, bestX = bx, bestY = by;
        int g = 11;
        for (int ix = 0; ix < g; ix++)
            for (int iy = 0; iy < g; iy++)
            {
                double x = _aMm * (ix + 0.5) / g, y = _bMm * (iy + 0.5) / g;
                Complex z = TransferZ(Z, f, bx, by, x, y);
                if (z.Magnitude > best) { best = z.Magnitude; bestX = x; bestY = y; }
            }
        return (bestX, bestY);
    }

    private Complex TransferZ(Complex[,] _, double f, double x1, double y1, double x2, double y2)
    {
        double w = 2 * Math.PI * f;
        double am = _aMm * 1e-3, bm = _bMm * 1e-3, dm = _dMm * 1e-3, ts = _portMm * 1e-3;
        Complex k2 = w * w * Mu0 * _er * Eps0 * new Complex(1, -TanDEff(f));
        Complex pre = new Complex(0, w * Mu0 * dm / (am * bm)), acc = 0;
        foreach (var (_, _, kx, ky, cc) in _modes)
        {
            double sm = Sinc(kx * ts / 2), sn = Sinc(ky * ts / 2);
            Complex denom = k2 - (kx * kx + ky * ky);
            if (denom == Complex.Zero) denom = 1e-30;
            double c1 = Math.Cos(kx * x1 * 1e-3) * Math.Cos(ky * y1 * 1e-3);
            double c2 = Math.Cos(kx * x2 * 1e-3) * Math.Cos(ky * y2 * 1e-3);
            acc += cc * sm * sm * sn * sn / denom * c1 * c2;
        }
        return pre * acc;
    }

    // ---- helpers -------------------------------------------------------------

    private static double Sinc(double x) => Math.Abs(x) < 1e-12 ? 1.0 : Math.Sin(x) / x;

    /// <summary>Solve A·x = b for complex A (Gaussian elimination, partial pivot).</summary>
    private static Complex[] SolveComplex(Complex[,] A, Complex[] b)
    {
        int n = b.Length;
        var a = (Complex[,])A.Clone();
        var x = (Complex[])b.Clone();
        for (int k = 0; k < n; k++)
        {
            int piv = k; double max = a[k, k].Magnitude;
            for (int i = k + 1; i < n; i++) if (a[i, k].Magnitude > max) { max = a[i, k].Magnitude; piv = i; }
            if (piv != k)
            {
                for (int j = 0; j < n; j++) (a[k, j], a[piv, j]) = (a[piv, j], a[k, j]);
                (x[k], x[piv]) = (x[piv], x[k]);
            }
            Complex akk = a[k, k]; if (akk == Complex.Zero) akk = 1e-30;
            for (int i = k + 1; i < n; i++)
            {
                Complex f = a[i, k] / akk;
                for (int j = k; j < n; j++) a[i, j] -= f * a[k, j];
                x[i] -= f * x[k];
            }
        }
        for (int i = n - 1; i >= 0; i--)
        {
            Complex s = x[i];
            for (int j = i + 1; j < n; j++) s -= a[i, j] * x[j];
            x[i] = s / (a[i, i] == Complex.Zero ? 1e-30 : a[i, i]);
        }
        return x;
    }
}
