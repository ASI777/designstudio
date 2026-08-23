namespace DesignStudio.Model;

// ============================================================================
// 6.2 Constraint rule tree (first slice).
//
// Resolution order, most specific wins:
//   per-net override  →  class-pair rule  →  net class  →  design default.
//
// Today this resolves clearance and trace width; the API is the seed the
// router, DRC and future region rules all query through, so adding levels
// later (regions, layer sets) does not change any caller.
// ============================================================================

/// <summary>Extra clearance required between two specific net classes (e.g. HV ↔ logic).</summary>
public class ClassPairRule
{
    public int ClassA { get; set; }
    public int ClassB { get; set; }
    public double ClearanceMm { get; set; } = 0.5;
    public override string ToString() => $"class {ClassA} ↔ {ClassB}: {ClearanceMm} mm";
}

/// <summary>Per-net override — beats every class-level value when set (> 0).</summary>
public class NetRule
{
    public int NetId { get; set; } = -1;
    public double TraceWidthMm { get; set; }     // 0 = inherit
    public double ClearanceMm { get; set; }      // 0 = inherit
}

public static class RuleResolver
{
    /// <summary>Effective trace width for a net.</summary>
    public static double Width(BoardDocument doc, int netId)
    {
        var o = doc.NetRules.FirstOrDefault(r => r.NetId == netId);
        if (o is { TraceWidthMm: > 0 }) return o.TraceWidthMm;
        return doc.ClassFor(netId).TraceWidthMm;
    }

    /// <summary>Effective clearance between two nets: max of each side's own
    /// requirement and any class-pair rule.</summary>
    public static double Clearance(BoardDocument doc, int netA, int netB)
    {
        double a = OwnClearance(doc, netA), b = OwnClearance(doc, netB);
        double req = Math.Max(a, b);
        int ca = doc.ClassFor(netA).Id, cb = doc.ClassFor(netB).Id;
        foreach (var pr in doc.ClassPairRules)
            if ((pr.ClassA == ca && pr.ClassB == cb) || (pr.ClassA == cb && pr.ClassB == ca))
                req = Math.Max(req, pr.ClearanceMm);
        return req;
    }

    private static double OwnClearance(BoardDocument doc, int netId)
    {
        var o = doc.NetRules.FirstOrDefault(r => r.NetId == netId);
        if (o is { ClearanceMm: > 0 }) return o.ClearanceMm;
        return doc.ClassFor(netId).ClearanceMm;
    }

    /// <summary>
    /// Rule 19: class-pair / per-net clearances that exceed what the native
    /// DRC (per-class only) enforces. Spatial index keeps this O(n log n).
    /// </summary>
    public static List<DrcResultItem> CheckPairClearances(BoardDocument doc)
    {
        var results = new List<DrcResultItem>();
        if (doc.ClassPairRules.Count == 0 && doc.NetRules.Count == 0) return results;

        var idx = BoardIndex.Traces(doc);
        var reported = new HashSet<(TraceItem, TraceItem)>();
        foreach (var ta in doc.Traces.Where(t => !t.IsPour && t.NetId >= 0))
        {
            double cx = (ta.Ax + ta.Bx) / 2, cy = (ta.Ay + ta.By) / 2;
            double reach = ta.LengthMm / 2 + 3;
            foreach (var tb in idx.Query(cx, cy, reach))
            {
                if (ReferenceEquals(ta, tb) || tb.NetId < 0 || tb.NetId == ta.NetId) continue;
                if (tb.Layer != ta.Layer) continue;
                if (reported.Contains((tb, ta))) continue;
                double need = Clearance(doc, ta.NetId, tb.NetId) + (ta.Width + tb.Width) / 2;
                double dist = SegSegDistance(ta, tb);
                // only flag what the native per-class check would have passed
                double native = Math.Max(doc.ClassFor(ta.NetId).ClearanceMm, doc.ClassFor(tb.NetId).ClearanceMm)
                                + (ta.Width + tb.Width) / 2;
                if (dist < need && dist >= native - 1e-9)
                {
                    results.Add(new DrcResultItem(19, "Class-pair clearance",
                        $"{doc.NetName(ta.NetId)} ↔ {doc.NetName(tb.NetId)}: {dist - (ta.Width + tb.Width) / 2:F2} mm gap, " +
                        $"rule requires {Clearance(doc, ta.NetId, tb.NetId):F2} mm.",
                        cx, cy));
                    reported.Add((ta, tb));
                }
            }
        }
        return results;
    }

    private static double SegSegDistance(TraceItem a, TraceItem b)
    {
        double d1 = PointSeg(a.Ax, a.Ay, b), d2 = PointSeg(a.Bx, a.By, b);
        double d3 = PointSeg(b.Ax, b.Ay, a), d4 = PointSeg(b.Bx, b.By, a);
        return Math.Min(Math.Min(d1, d2), Math.Min(d3, d4));
    }

    private static double PointSeg(double px, double py, TraceItem t)
    {
        double dx = t.Bx - t.Ax, dy = t.By - t.Ay, l2 = dx * dx + dy * dy;
        double u = l2 < 1e-12 ? 0 : Math.Clamp(((px - t.Ax) * dx + (py - t.Ay) * dy) / l2, 0, 1);
        double qx = t.Ax + u * dx - px, qy = t.Ay + u * dy - py;
        return Math.Sqrt(qx * qx + qy * qy);
    }
}

// ============================================================================
// 4.2 Backdrilling — controlled removal of unused via barrel.
// ============================================================================

public static class Backdrill
{
    /// <summary>Backdrill bit oversize relative to the plated drill (industry typical +0.2 mm).</summary>
    public const double OversizeMm = 0.2;

    /// <summary>
    /// Marks every controlled-net via whose barrel extends beyond its deepest
    /// used layer (the rule-15 condition) for backdrilling to that layer.
    /// Returns how many vias were marked.
    /// </summary>
    public static int ApplyAll(BoardDocument doc)
    {
        var controlled = doc.Nets.Where(n =>
        {
            var c = doc.ClassFor(n.Id);
            return c.TargetImpedanceOhm > 0 || c.TargetDiffImpedanceOhm > 0 || c.MaxSkewMm > 0;
        }).Select(n => n.Id).ToHashSet();

        int marked = 0;
        foreach (var v in doc.Vias.Where(v => controlled.Contains(v.NetId)))
        {
            var layersUsed = doc.Traces.Where(t => !t.IsPour && t.NetId == v.NetId)
                                       .Select(t => t.Layer).Distinct().ToList();
            if (layersUsed.Count == 0) continue;
            int deepest = layersUsed.Max();
            if (v.ToLayer > deepest && v.BackdrillToLayer != deepest)
            {
                v.BackdrillToLayer = deepest;
                marked++;
            }
        }
        if (marked > 0) doc.NotifyChanged();
        return marked;
    }
}
