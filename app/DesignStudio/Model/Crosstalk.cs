namespace DesignStudio.Model;

// ============================================================================
// 2.5 Crosstalk engine.
//
// For every pair of different-net segments running parallel on the same
// layer, accumulate coupled length and estimate near-end crosstalk with the
// saturated-NEXT closed form: the backward coupling coefficient
//     Kb ≈ 1 / (1 + (s/h)²)  · ¼
// (s = edge gap, h = height over the reference plane) saturates once the
// coupled length exceeds half the aggressor's rise distance, so long
// parallel runs are judged by Kb alone. Victims that are controlled-
// impedance nets get a tighter threshold. Findings raise DRC rule 22 with
// the worst location and the standard fixes (more gap, thinner dielectric,
// guard trace). Pair discovery is spatial-index bounded, O(n log n).
// ============================================================================

public static class Crosstalk
{
    public const double ThresholdControlled = 0.05;    // 5 % NEXT for controlled nets
    public const double ThresholdGeneral = 0.10;       // 10 % for everything else
    private const double MinParallelMm = 2.0;          // ignore incidental adjacency

    public record Coupling(TraceItem Aggressor, TraceItem Victim, double ParallelMm,
                           double GapMm, double NextFraction);

    /// <summary>NEXT fraction for a gap s over reference height h, length-saturated.</summary>
    public static double NextFraction(double sMm, double hMm, double parallelMm)
    {
        double kb = 0.25 / (1 + (sMm / Math.Max(hMm, 1e-3)) * (sMm / Math.Max(hMm, 1e-3)));
        // soft saturation with coupled length (fully saturated by ~20 mm at typical rise times)
        double sat = Math.Min(1.0, parallelMm / 20.0);
        return kb * sat;
    }

    public static List<Coupling> FindCouplings(BoardDocument doc)
    {
        var found = new List<Coupling>();
        var seen = new HashSet<(TraceItem, TraceItem)>();
        var idx = BoardIndex.Traces(doc);

        foreach (var a in doc.Traces.Where(t => !t.IsPour && t.NetId >= 0))
        {
            double cx = (a.Ax + a.Bx) / 2, cy = (a.Ay + a.By) / 2;
            foreach (var b in idx.Query(cx, cy, a.LengthMm / 2 + 3))
            {
                if (ReferenceEquals(a, b) || b.NetId < 0 || b.NetId == a.NetId || b.Layer != a.Layer || b.IsPour)
                    continue;
                if (!seen.Add((a, b)) || seen.Contains((b, a))) continue;

                double parallel = ParallelOverlapMm(a, b, out double gap);
                if (parallel < MinParallelMm) continue;

                var (h, _, _) = ImpedanceEngine.Reference(doc, a.Layer);
                double next = NextFraction(gap, h, parallel);
                found.Add(new Coupling(a, b, parallel, gap, next));
            }
        }
        return found;
    }

    /// <summary>Rule 22.</summary>
    public static List<DrcResultItem> Check(BoardDocument doc)
    {
        var results = new List<DrcResultItem>();
        var controlled = doc.Nets.Where(n =>
        {
            var c = doc.ClassFor(n.Id);
            return c.TargetImpedanceOhm > 0 || c.TargetDiffImpedanceOhm > 0 || c.MaxSkewMm > 0;
        }).Select(n => n.Id).ToHashSet();

        foreach (var c in FindCouplings(doc))
        {
            double threshold = controlled.Contains(c.Victim.NetId) || controlled.Contains(c.Aggressor.NetId)
                ? ThresholdControlled : ThresholdGeneral;
            if (c.NextFraction <= threshold) continue;
            double mx = (c.Victim.Ax + c.Victim.Bx) / 2, my = (c.Victim.Ay + c.Victim.By) / 2;
            results.Add(new DrcResultItem(22, "Crosstalk",
                $"{doc.NetName(c.Aggressor.NetId)} → {doc.NetName(c.Victim.NetId)}: " +
                $"~{c.NextFraction * 100:F0} % NEXT over {c.ParallelMm:F1} mm at {c.GapMm:F2} mm gap — " +
                $"increase spacing to ≥ {RequiredGapMm(doc, c, threshold):F2} mm, shorten the parallel run, or add a guard trace.",
                mx, my));
        }
        return results;
    }

    private static double RequiredGapMm(BoardDocument doc, Coupling c, double threshold)
    {
        var (h, _, _) = ImpedanceEngine.Reference(doc, c.Aggressor.Layer);
        double sat = Math.Min(1.0, c.ParallelMm / 20.0);
        // invert Kb·sat = threshold  →  s = h·√(0.25·sat/threshold − 1)
        double inner = 0.25 * sat / threshold - 1;
        return inner <= 0 ? c.GapMm : Math.Ceiling(h * Math.Sqrt(inner) * 100) / 100;
    }

    /// <summary>Length of the overlapping near-parallel run and the average edge gap (NaN-safe).</summary>
    private static double ParallelOverlapMm(TraceItem a, TraceItem b, out double gapMm)
    {
        gapMm = double.MaxValue;
        double adx = a.Bx - a.Ax, ady = a.By - a.Ay;
        double bdx = b.Bx - b.Ax, bdy = b.By - b.Ay;
        double la = Math.Sqrt(adx * adx + ady * ady), lb = Math.Sqrt(bdx * bdx + bdy * bdy);
        if (la < 1e-6 || lb < 1e-6) return 0;
        double cosAng = Math.Abs((adx * bdx + ady * bdy) / (la * lb));
        if (cosAng < 0.95) return 0;                       // > ~18° apart: not parallel

        // project b's endpoints onto a; overlap of parameter ranges
        double u1 = ((b.Ax - a.Ax) * adx + (b.Ay - a.Ay) * ady) / (la * la);
        double u2 = ((b.Bx - a.Ax) * adx + (b.By - a.Ay) * ady) / (la * la);
        if (u1 > u2) (u1, u2) = (u2, u1);
        double overlap = (Math.Min(u2, 1) - Math.Max(u1, 0)) * la;
        if (overlap <= 0) return 0;

        // gap: perpendicular distance between the lines, minus half-widths
        double mx = (b.Ax + b.Bx) / 2 - a.Ax, my = (b.Ay + b.By) / 2 - a.Ay;
        double perp = Math.Abs(mx * (-ady / la) + my * (adx / la));
        gapMm = Math.Max(0.01, perp - (a.Width + b.Width) / 2);
        return overlap;
    }
}
