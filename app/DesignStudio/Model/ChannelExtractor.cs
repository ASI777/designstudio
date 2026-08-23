using System.IO;
using System.Numerics;
using System.Text;

namespace DesignStudio.Model;

// ============================================================================
// 2.3 Channel S-parameter extraction → Touchstone export.
//
// A routed net becomes a cascade of two-ports: each trace segment is a lossy
// transmission line, each via is an L-C π discontinuity. ABCD matrices multiply
// along the path; the result converts to S-parameters at 50 Ω and writes
// industry-standard .s2p that ADS / HyperLynx / scikit-rf consume directly.
//
// Phase S2: when the stackup is live, each segment's per-unit-length γ(f) and
// complex Zc(f) come from the MoM RLGC solver (MoM2D, E1) driven by the causal
// Djordjevic-Sarkar material model and surface-roughness loss (E2) — replacing
// the closed-form Z0 + flat-tanδ + bare-skin estimate that is only good to a
// few GHz. The cross-section solve is cached per (layer, width) and evaluated
// on a small frequency grid, then interpolated, so a full sweep stays cheap.
// Without a live stackup the original closed-form path is used unchanged.
// ============================================================================

public static class ChannelExtractor
{
    private const double C0 = 2.99792458e8;        // m/s
    private const double ViaLnHPerMm = 0.2;        // barrel inductance ≈ 0.2 nH/mm
    private const double ViaPadCpF = 0.3;          // pad/antipad capacitance per transition

    public record FreqPoint(double FreqHz, Complex S11, Complex S21, Complex S12, Complex S22);

    public static List<FreqPoint> Extract(BoardDocument doc, int netId,
                                          double fStartHz = 1e7, double fStopHz = 2e10, int points = 201,
                                          bool useMoM = true)
    {
        var freqs = new double[points];
        for (int k = 0; k < points; k++)
            freqs[k] = fStartHz * Math.Pow(fStopHz / fStartHz, k / (double)(points - 1));
        return ExtractAt(doc, netId, freqs, useMoM);
    }

    /// <summary>
    /// Extract S-parameters at an arbitrary set of frequencies (e.g. a linear
    /// grid for the time-domain link simulator). f = 0 is treated as a near-DC
    /// through (S21 = 1) so the IDFT has a clean DC bin.
    /// </summary>
    public static List<FreqPoint> ExtractAt(BoardDocument doc, int netId, double[] freqs,
                                            bool useMoM = true)
    {
        var segs = doc.Traces.Where(t => !t.IsPour && t.NetId == netId).ToList();
        var vias = doc.Vias.Where(v => v.NetId == netId).ToList();
        var result = new List<FreqPoint>(freqs.Length);

        // RLGC cross-section models are cached over the band the sweep spans
        double mf0 = freqs.Where(x => x > 0).DefaultIfEmpty(1e7).Min();
        double mf1 = Math.Max(freqs.Length > 0 ? freqs.Max() : 1e10, mf0 * 1.0001);
        bool stackupLive = doc.Stackup.Count == doc.CopperLayers;
        var models = new Dictionary<(int, long), CrossSectionModel?>();
        CrossSectionModel? ModelFor(TraceItem t)
        {
            if (!useMoM || !stackupLive) return null;
            var key = (t.Layer, (long)Math.Round(t.Width * 1e4));
            if (models.TryGetValue(key, out var m)) return m;
            m = CrossSectionModel.TryBuild(doc, t, mf0, mf1);
            models[key] = m;
            return m;
        }

        // physics-based via models (E3): the stub hangs below the deepest layer
        // the net actually routes on. Geometry is frequency-independent, so build
        // each via's model once and just evaluate Abcd(f) inside the sweep.
        int deepestUsed = segs.Select(t => t.Layer).DefaultIfEmpty(0).Max();
        var viaModels = vias
            .Select(v => useMoM && stackupLive ? ViaModel.ForVia(doc, v, deepestUsed) : null)
            .ToList();

        foreach (double fk in freqs)
        {
            if (fk <= 0)   // DC bin for the IDFT: ideal through
            {
                result.Add(new FreqPoint(0, Complex.Zero, Complex.One, Complex.One, Complex.Zero));
                continue;
            }
            double f = fk;
            // cascade ABCD
            Complex A = 1, B = 0, Cm = 0, D = 1;
            void Mul(Complex a2, Complex b2, Complex c2, Complex d2)
            {
                Complex na = A * a2 + B * c2, nb = A * b2 + B * d2;
                Complex nc = Cm * a2 + D * c2, nd = Cm * b2 + D * d2;
                A = na; B = nb; Cm = nc; D = nd;
            }

            foreach (var t in segs)
            {
                double lenM = t.LengthMm / 1000.0;
                Complex gamma, zc;

                var model = ModelFor(t);
                if (model != null)
                {
                    (gamma, zc) = model.At(f);        // MoM RLGC + causal materials + roughness
                }
                else
                {
                    // closed-form fallback (no live stackup): Z0 + flat-tanδ + bare skin
                    var (h, er, ms) = ImpedanceEngine.Reference(doc, t.Layer);
                    double z0 = ImpedanceEngine.TraceZ0(doc, t);
                    if (z0 <= 0 || double.IsNaN(z0)) z0 = 50;
                    double psPerMm = ms ? ImpedanceEngine.MicrostripDelayPsPerMm(er)
                                        : ImpedanceEngine.StriplineDelayPsPerMm(er);
                    double beta = 2 * Math.PI * f * (psPerMm * 1e-12 / 1e-3);
                    double tan = LayerTan(doc, t.Layer, f / 1e9);
                    double alphaD = Math.PI * f / C0 * Math.Sqrt(er) * tan;
                    double rs = Math.Sqrt(Math.PI * f * 4e-7 * Math.PI / 5.8e7);
                    double alphaC = rs / (z0 * Math.Max(t.Width, 0.05) / 1000.0) / 2;
                    gamma = new Complex(alphaD + alphaC, beta);
                    zc = z0;
                }

                Complex gl = gamma * lenM;
                Complex ch = Complex.Cosh(gl), sh = Complex.Sinh(gl);
                Mul(ch, zc * sh, sh / zc, ch);
            }

            // vendor S-parameter blocks attached to this net (connectors, cables…)
            foreach (var blk in doc.ChannelBlocks.Where(b => b.NetId == netId))
            {
                var sp = blk.Parsed();
                if (sp is null) continue;                   // unreadable file: skip, surfaced in UI
                var (a2, b2, c2, d2) = sp.Abcd(f);
                Mul(a2, b2, c2, d2);
            }

            for (int vi = 0; vi < vias.Count; vi++)
            {
                var vm = viaModels[vi];
                if (vm != null)
                {
                    var (a2, b2, c2, d2) = vm.Abcd(f);   // coaxial barrel + open-stub suckout
                    Mul(a2, b2, c2, d2);
                }
                else
                {
                    // lumped L-C π fallback (no live stackup)
                    var v = vias[vi];
                    double barrelMm = BarrelLengthMm(doc, v);
                    double L = ViaLnHPerMm * barrelMm * 1e-9;
                    double Cv = ViaPadCpF * 1e-12;
                    Complex jwL = new Complex(0, 2 * Math.PI * f * L);
                    Complex jwC = new Complex(0, 2 * Math.PI * f * Cv);
                    Mul(1, 0, jwC / 2, 1);          // shunt C/2
                    Mul(1, jwL, 0, 1);              // series L
                    Mul(1, 0, jwC / 2, 1);          // shunt C/2
                }
            }

            // ABCD → S at Zref = 50
            const double zr = 50;
            Complex den = A + B / zr + Cm * zr + D;
            var s11 = (A + B / zr - Cm * zr - D) / den;
            var s21 = 2 / den;
            var s12 = 2 * (A * D - B * Cm) / den;
            var s22 = (-A + B / zr - Cm * zr + D) / den;
            result.Add(new FreqPoint(f, s11, s21, s12, s22));
        }
        return result;
    }

    /// <summary>
    /// Extract many nets in parallel across all cores (E20 scale-out). Results
    /// are returned as a net-id → S-parameter map; a board-wide
    /// <see cref="Solve.ExtractionCache{T}"/> (keyed on <see cref="BoardDocument.Version"/>
    /// + net + grid) skips nets that haven't changed since the last run, so an
    /// incremental edit recomputes only what moved. <paramref name="progress"/>
    /// reports completed-net count and <paramref name="ct"/> cancels the sweep.
    /// This is the path a full 24-layer board's controlled nets run through.
    /// </summary>
    public static Dictionary<int, List<FreqPoint>> ExtractManyParallel(
        BoardDocument doc, IReadOnlyList<int> netIds, double[] freqs,
        Solve.ExtractionCache<List<FreqPoint>>? cache = null,
        IProgress<Solve.SweepProgress>? progress = null,
        System.Threading.CancellationToken ct = default, bool useMoM = true, int maxDegree = 0)
    {
        cache ??= new Solve.ExtractionCache<List<FreqPoint>>();
        long version = doc.Version;
        string sig = Solve.ExtractionCache<List<FreqPoint>>.GridSignature(freqs);

        // Extraction only reads the document, but warming the first net serially
        // forces any one-time lazy document state to settle before the parallel
        // fan-out (and it lands in the cache, so it is not redundant work).
        if (netIds.Count > 0)
            cache.GetOrAdd(version, netIds[0], sig, () => ExtractAt(doc, netIds[0], freqs, useMoM));

        var items = netIds.Select(id => (key: id, input: id)).ToList();
        return Solve.SweepScheduler.RunKeyed<int, int, List<FreqPoint>>(
            items,
            id => cache.GetOrAdd(version, id, sig, () => ExtractAt(doc, id, freqs, useMoM)),
            progress, ct, maxDegree);
    }

    private static double LayerTan(BoardDocument doc, int layer, double fGHz)
        => doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup[Math.Clamp(layer, 0, doc.Stackup.Count - 1)].TanAt(fGHz)
            : doc.DielectricLossTangent;

    private static double BarrelLengthMm(BoardDocument doc, ViaItem v)
    {
        if (doc.Stackup.Count != doc.CopperLayers)
            return (Math.Abs(v.ToLayer - v.FromLayer)) * (doc.DielectricHeightMm + doc.CopperThicknessMm);
        double len = 0;
        int hi = v.BackdrillToLayer >= 0 ? v.BackdrillToLayer : Math.Max(v.FromLayer, v.ToLayer);
        for (int i = Math.Min(v.FromLayer, v.ToLayer); i < hi && i < doc.Stackup.Count; i++)
            len += doc.Stackup[i].DielectricHeightMm + doc.Stackup[i].CopperThicknessMm;
        return len;
    }

    // ------------------------------------------------------------------------
    // MoM RLGC cross-section model for one (layer, width). Solves the boundary-
    // element problem (causal Dk/Df + roughness) on a small frequency grid and
    // interpolates the per-unit-length L,C,R,G — γ and Zc are then formed at the
    // exact sweep frequency. L,C are ~flat, R∝√f, G∝f·tanδ, all smooth in log-f,
    // so a 6-point grid carries the sweep to <1% interpolation error.
    // ------------------------------------------------------------------------
    private sealed class CrossSectionModel
    {
        private readonly double[] _f, _L, _C, _R, _G;

        private CrossSectionModel(double[] f, double[] l, double[] c, double[] r, double[] g)
        { _f = f; _L = l; _C = c; _R = r; _G = g; }

        public static CrossSectionModel? TryBuild(BoardDocument doc, TraceItem t, double f0, double f1)
        {
            var (h, erRef, micro) = ImpedanceEngine.Reference(doc, t.Layer);
            if (h <= 0 || double.IsNaN(h) || t.Width <= 0) return null;
            double cu = doc.Stackup.Count == doc.CopperLayers
                ? doc.Stackup[t.Layer].CopperThicknessMm : doc.CopperThicknessMm;
            if (cu <= 0) cu = 0.035;
            var mat = MaterialFor(doc, t.Layer, erRef);

            const int n = 6;
            var fs = new double[n]; var L = new double[n]; var C = new double[n];
            var R = new double[n]; var G = new double[n];
            try
            {
                for (int k = 0; k < n; k++)
                {
                    double f = f0 * Math.Pow(f1 / f0, k / (double)(n - 1));
                    var rl = MoM2D.ExtractRlgc(t.Width, cu, h, mat, f, micro);
                    fs[k] = f; L[k] = rl.L[0, 0]; C[k] = rl.C[0, 0];
                    R[k] = rl.R[0, 0]; G[k] = rl.G[0, 0];
                    if (double.IsNaN(L[k]) || double.IsNaN(C[k]) || C[k] <= 0) return null;
                }
            }
            catch { return null; }
            return new CrossSectionModel(fs, L, C, R, G);
        }

        public (Complex gamma, Complex zc) At(double f)
        {
            double L = Interp(f, _L), C = Interp(f, _C), R = Interp(f, _R), G = Interp(f, _G);
            double w = 2 * Math.PI * f;
            var zs = new Complex(R, w * L);
            var ys = new Complex(G, w * C);
            return (Complex.Sqrt(zs * ys), Complex.Sqrt(zs / ys));
        }

        private double Interp(double f, double[] y)
        {
            if (f <= _f[0]) return y[0];
            if (f >= _f[^1]) return y[^1];
            for (int i = 0; i + 1 < _f.Length; i++)
                if (f <= _f[i + 1])
                {
                    double u = (Math.Log(f) - Math.Log(_f[i])) / (Math.Log(_f[i + 1]) - Math.Log(_f[i]));
                    return y[i] + u * (y[i + 1] - y[i]);
                }
            return y[^1];
        }

        private static MaterialDef MaterialFor(BoardDocument doc, int layer, double erRef)
        {
            if (doc.Stackup.Count == doc.CopperLayers)
            {
                var st = doc.Stackup[layer];
                var m = MaterialsLibrary.Find(st.MaterialId);
                if (m != null) return m;
                return new MaterialDef { Points = { new MaterialPoint(1, erRef, st.TanAt(1)) } };
            }
            return new MaterialDef { Points = { new MaterialPoint(1, erRef, doc.DielectricLossTangent) } };
        }
    }

    /// <summary>Write Touchstone v1 .s2p (Hz, S, real/imaginary, 50 Ω reference).</summary>
    public static void WriteTouchstone(string path, List<FreqPoint> data, string netName)
    {
        var sb = new StringBuilder();
        sb.AppendLine($"! Design Studio channel extraction — net {netName}");
        sb.AppendLine("! 2-port: port 1 = net start, port 2 = net end. Via models: L-C pi.");
        sb.AppendLine("# Hz S RI R 50");
        foreach (var p in data)
            sb.AppendLine(string.Join(' ',
                p.FreqHz.ToString("G9", System.Globalization.CultureInfo.InvariantCulture),
                Fmt(p.S11), Fmt(p.S21), Fmt(p.S12), Fmt(p.S22)));
        File.WriteAllText(path, sb.ToString());

        static string Fmt(Complex c) =>
            c.Real.ToString("G6", System.Globalization.CultureInfo.InvariantCulture) + " " +
            c.Imaginary.ToString("G6", System.Globalization.CultureInfo.InvariantCulture);
    }
}
