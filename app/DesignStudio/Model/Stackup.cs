using System.Text.Json.Serialization;

namespace DesignStudio.Model;

// ============================================================================
// 1. Stackup model — per-layer electrical description of the board.
//
// Each copper layer carries its role (signal or plane) and copper thickness;
// the dielectric *below* layer i (between copper i and i+1) carries height,
// Er, and loss tangent. Impedance and delay math derive everything else.
// ============================================================================

public enum LayerRole { Signal, Plane }

public class StackupLayer
{
    [JsonPropertyName("role")] public LayerRole Role { get; set; } = LayerRole.Signal;
    [JsonPropertyName("copper_t_mm")] public double CopperThicknessMm { get; set; } = 0.035;   // 1 oz
    /// <summary>Copper weight in oz (1 oz ≈ 0.035 mm). Thick inner power planes are often 2 oz.</summary>
    [JsonPropertyName("copper_oz")] public double CopperWeightOz { get; set; } = 1.0;
    /// <summary>Dielectric between this copper layer and the next one down. Unused on the bottom layer.</summary>
    [JsonPropertyName("diel_h_mm")] public double DielectricHeightMm { get; set; } = 0.2;
    [JsonPropertyName("diel_er")] public double DielectricEr { get; set; } = 4.4;
    [JsonPropertyName("loss_tangent")] public double LossTangent { get; set; } = 0.02;
    [JsonPropertyName("plane_net")] public int PlaneNetId { get; set; } = -1;   // GND/PWR net for plane layers
    /// <summary>Laminate id from MaterialsLibrary ("megtron6"…); empty = use the raw Er/tanδ above.</summary>
    [JsonPropertyName("material")] public string MaterialId { get; set; } = "";

    /// <summary>Er at frequency: the referenced laminate's dispersion curve, else the raw value.</summary>
    public double ErAt(double fGHz = 1)
        => MaterialsLibrary.Find(MaterialId)?.DkAt(fGHz) ?? DielectricEr;

    /// <summary>tanδ at frequency from the laminate, else the raw value.</summary>
    public double TanAt(double fGHz = 1)
        => MaterialsLibrary.Find(MaterialId)?.DfAt(fGHz) ?? LossTangent;

    public override string ToString() => Role == LayerRole.Plane ? "Plane" : "Signal";
}

// ============================================================================
// 2. Impedance engine — closed-form IPC-2141 / Wadell approximations.
//
// Microstrip (outer layer over a plane) and symmetric stripline (inner layer
// between planes), single-ended and edge-coupled differential, plus inverse
// solvers (target Ω → width / gap) by bisection.
// ============================================================================

public static class ImpedanceEngine
{
    private const double PsPerMmVacuum = 3.3356;   // 1/c

    // ---- forward: geometry → Ω ----

    /// <summary>IPC-2141 surface microstrip. h = dielectric to reference plane, all mm.</summary>
    public static double MicrostripZ0(double wMm, double tMm, double hMm, double er)
        => 87.0 / Math.Sqrt(er + 1.41) * Math.Log(5.98 * hMm / (0.8 * wMm + tMm));

    /// <summary>IPC-2141 symmetric stripline. h = trace-to-plane spacing (each side), all mm.</summary>
    public static double StriplineZ0(double wMm, double tMm, double hMm, double er)
        => 60.0 / Math.Sqrt(er) * Math.Log(1.9 * (2 * hMm + tMm) / (0.8 * wMm + tMm));

    /// <summary>Edge-coupled differential microstrip (IPC-2141 coupling factor). s = edge-to-edge gap.</summary>
    public static double MicrostripZdiff(double wMm, double sMm, double tMm, double hMm, double er)
        => 2 * MicrostripZ0(wMm, tMm, hMm, er) * (1 - 0.48 * Math.Exp(-0.96 * sMm / hMm));

    /// <summary>Edge-coupled differential stripline.</summary>
    public static double StriplineZdiff(double wMm, double sMm, double tMm, double hMm, double er)
        => 2 * StriplineZ0(wMm, tMm, hMm, er) * (1 - 0.347 * Math.Exp(-2.9 * sMm / hMm));

    // ---- propagation delay (ps/mm) ----

    public static double MicrostripDelayPsPerMm(double er)
        => PsPerMmVacuum * Math.Sqrt(0.475 * er + 0.67);   // effective Er, field partly in air

    public static double StriplineDelayPsPerMm(double er)
        => PsPerMmVacuum * Math.Sqrt(er);

    // ---- inverse: target Ω → geometry (bisection over width / gap) ----

    /// <summary>Width that hits a single-ended target. Returns NaN when unreachable in 0.05–2.0 mm.</summary>
    public static double SolveWidth(double targetOhm, bool microstrip, double tMm, double hMm, double er)
        => Bisect(0.05, 2.0, w =>
            (microstrip ? MicrostripZ0(w, tMm, hMm, er) : StriplineZ0(w, tMm, hMm, er)) - targetOhm);

    /// <summary>
    /// (width, gap) for a differential target: width is chosen so each line is
    /// ~targetDiff/2 + a margin for coupling, then the gap is solved exactly.
    /// </summary>
    public static (double widthMm, double gapMm) SolveDiffPair(double targetDiffOhm, bool microstrip,
                                                               double tMm, double hMm, double er)
    {
        // single-ended target slightly above Zdiff/2 because coupling lowers Zdiff
        double w = SolveWidth(targetDiffOhm / 2 / 0.85, microstrip, tMm, hMm, er);
        if (double.IsNaN(w)) return (double.NaN, double.NaN);
        double g = Bisect(0.05, 3.0, s =>
            (microstrip ? MicrostripZdiff(w, s, tMm, hMm, er) : StriplineZdiff(w, s, tMm, hMm, er)) - targetDiffOhm);
        return (Math.Round(w, 3), Math.Round(g, 3));
    }

    private static double Bisect(double lo, double hi, Func<double, double> f)
    {
        double flo = f(lo), fhi = f(hi);
        if (Math.Sign(flo) == Math.Sign(fhi)) return double.NaN;   // target unreachable
        for (int i = 0; i < 60; i++)
        {
            double mid = (lo + hi) / 2, fm = f(mid);
            if (Math.Abs(fm) < 1e-4) return mid;
            if (Math.Sign(fm) == Math.Sign(flo)) { lo = mid; flo = fm; } else hi = mid;
        }
        return (lo + hi) / 2;
    }

    // ---- stackup-aware helpers ----

    /// <summary>
    /// Reference geometry for a signal layer: distance to the nearest plane
    /// layer (mm), its Er, and whether the trace behaves as microstrip
    /// (one reference side / outer layer) or stripline.
    /// </summary>
    public static (double hMm, double er, bool microstrip) Reference(BoardDocument doc, int layer)
    {
        var st = doc.Stackup;
        if (st.Count != doc.CopperLayers || st.All(l => l.Role != LayerRole.Plane))
            // legacy fallback: single global dielectric, outer layers microstrip
            return (doc.DielectricHeightMm, doc.DielectricEr,
                    layer == 0 || layer == doc.CopperLayers - 1);

        double down = double.NaN, up = double.NaN, erDown = 4.4, erUp = 4.4, acc, er = 4.4;
        acc = 0;
        for (int i = layer; i + 1 < st.Count; i++)             // search downward
        {
            acc += st[i].DielectricHeightMm;
            if (st[i + 1].Role == LayerRole.Plane) { down = acc; erDown = st[i].ErAt(); break; }
            acc += st[i + 1].CopperThicknessMm;
        }
        acc = 0;
        for (int i = layer - 1; i >= 0; i--)                    // search upward
        {
            acc += st[i].DielectricHeightMm;
            if (st[i].Role == LayerRole.Plane) { up = acc; erUp = st[i].ErAt(); break; }
            acc += st[i].CopperThicknessMm;
        }
        bool hasDown = !double.IsNaN(down), hasUp = !double.IsNaN(up);
        if (hasDown && hasUp)                                   // stripline: nearest plane sets h
        {
            er = (erDown + erUp) / 2;
            return (Math.Min(down, up), er, false);
        }
        if (hasDown) return (down, erDown, true);
        if (hasUp) return (up, erUp, true);
        return (doc.DielectricHeightMm, doc.DielectricEr, true);
    }

    /// <summary>
    /// Nearest plane-layer indices above and below a signal layer (−1 if none).
    /// This is the signal's reference plane(s) — what return-path / reference-
    /// integrity analysis tracks on a many-plane stackup.
    /// </summary>
    public static (int above, int below) ReferencePlanes(BoardDocument doc, int layer)
    {
        var st = doc.Stackup;
        if (st.Count != doc.CopperLayers) return (-1, -1);
        int above = -1, below = -1;
        for (int i = layer - 1; i >= 0; i--) if (st[i].Role == LayerRole.Plane) { above = i; break; }
        for (int i = layer + 1; i < st.Count; i++) if (st[i].Role == LayerRole.Plane) { below = i; break; }
        return (above, below);
    }

    /// <summary>Single-ended impedance of an existing trace given the live stackup.</summary>
    public static double TraceZ0(BoardDocument doc, TraceItem t)
    {
        var (h, er, ms) = Reference(doc, t.Layer);
        double cu = doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup[t.Layer].CopperThicknessMm : doc.CopperThicknessMm;
        return ms ? MicrostripZ0(t.Width, cu, h, er) : StriplineZ0(t.Width, cu, h, er);
    }
}

// ============================================================================
// 3. Delay-aware length engine — mm is the wrong unit across layer changes;
// this converts copper into picoseconds per layer, adds via barrels and pin
// package delays, and checks match groups.
// ============================================================================

/// <summary>Nets that must arrive together (a DDR byte lane, a clock pair…).</summary>
public class MatchGroup
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("net_ids")] public List<int> NetIds { get; set; } = new();
    [JsonPropertyName("tolerance_ps")] public double TolerancePs { get; set; } = 10;
    [JsonPropertyName("target_ps")] public double? TargetPs { get; set; }    // absolute target, optional
    public override string ToString() => $"{Name} ({NetIds.Count} nets, ±{TolerancePs} ps)";
}

public static class DelayEngine
{
    /// <summary>Delay of one trace segment on its layer, ps.</summary>
    public static double SegmentDelayPs(BoardDocument doc, TraceItem t)
    {
        var (_, er, ms) = ImpedanceEngine.Reference(doc, t.Layer);
        double psPerMm = ms ? ImpedanceEngine.MicrostripDelayPsPerMm(er)
                            : ImpedanceEngine.StriplineDelayPsPerMm(er);
        return t.LengthMm * psPerMm;
    }

    /// <summary>Via barrel delay: stripline-like propagation through the spanned stackup height.</summary>
    public static double ViaDelayPs(BoardDocument doc, ViaItem v)
    {
        double len = 0, er = doc.DielectricEr;
        if (doc.Stackup.Count == doc.CopperLayers)
        {
            double erSum = 0; int n = 0;
            for (int i = v.FromLayer; i < v.ToLayer && i < doc.Stackup.Count; i++)
            {
                len += doc.Stackup[i].DielectricHeightMm + doc.Stackup[i].CopperThicknessMm;
                erSum += doc.Stackup[i].ErAt(); n++;
            }
            if (n > 0) er = erSum / n;
        }
        else len = (v.ToLayer - v.FromLayer) * (doc.DielectricHeightMm + doc.CopperThicknessMm);
        return len * ImpedanceEngine.StriplineDelayPsPerMm(er);
    }

    /// <summary>
    /// Total net delay in ps: copper per layer + via barrels + pin package
    /// delays (length inside the component package, from the datasheet JSON).
    /// </summary>
    public static double NetDelayPs(BoardDocument doc, int netId)
    {
        double ps = 0;
        foreach (var t in doc.Traces)
            if (!t.IsPour && t.NetId == netId) ps += SegmentDelayPs(doc, t);
        foreach (var v in doc.Vias)
            if (v.NetId == netId) ps += ViaDelayPs(doc, v);
        foreach (var fp in doc.Footprints)
            foreach (var p in fp.Pads)
                if (p.NetId == netId && p.PackageDelayMm > 0)
                    ps += p.PackageDelayMm * ImpedanceEngine.StriplineDelayPsPerMm(doc.DielectricEr);
        return ps;
    }

    /// <summary>Match-group timing check → DRC items (rule 13).</summary>
    public static List<DrcResultItem> CheckMatchGroups(BoardDocument doc)
    {
        var results = new List<DrcResultItem>();
        foreach (var g in doc.MatchGroups)
        {
            var delays = g.NetIds
                .Where(id => doc.FindNet(id) != null)
                .Select(id => (id, ps: NetDelayPs(doc, id)))
                .ToList();
            if (delays.Count < 2 && g.TargetPs is null) continue;

            if (delays.Count >= 2)
            {
                var min = delays.MinBy(d => d.ps); var max = delays.MaxBy(d => d.ps);
                double spread = max.ps - min.ps;
                if (spread > g.TolerancePs)
                    results.Add(new DrcResultItem(13, "Length matching",
                        $"group '{g.Name}': {doc.NetName(max.id)} arrives {spread:F1} ps after " +
                        $"{doc.NetName(min.id)} (tolerance ±{g.TolerancePs} ps) — " +
                        $"add ~{spread / ImpedanceEngine.StriplineDelayPsPerMm(doc.DielectricEr):F2} mm serpentine to {doc.NetName(min.id)}",
                        0, 0));
            }
            if (g.TargetPs is double target)
                foreach (var (id, ps) in delays)
                    if (Math.Abs(ps - target) > g.TolerancePs)
                        results.Add(new DrcResultItem(13, "Length matching",
                            $"group '{g.Name}': {doc.NetName(id)} = {ps:F1} ps, target {target:F1} ±{g.TolerancePs} ps.",
                            0, 0));
        }
        return results;
    }
}
