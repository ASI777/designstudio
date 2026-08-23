namespace DesignStudio.Model;

// ============================================================================
// E12. Reference-plane integrity & return path at scale.
//
// On a 4-layer board "is there a plane?" is a fine check. On a 24-layer server
// board every signal layer has its *own* adjacent reference plane, and the
// failures that matter are subtler:
//   • a controlled trace crossing a gap/void/split in the plane it references
//     (the return current has nowhere to go → a big inductive discontinuity);
//   • a layer transition where the signal's reference plane *changes* and there
//     is no stitching via / cap to carry the return current between the planes.
//
// This tracks each controlled net's reference plane(s) via
// ImpedanceEngine.ReferencePlanes, tests the trace against the actual plane
// copper (PlaneShape.Fill, even-odd point-in-polygon), and audits per-net
// reference continuity — the S7 exit criterion ("every controlled net has a
// continuous reference"). Kept separate from the main DRC run (rules 18/19) so
// it doesn't perturb existing rule counts.
// ============================================================================

public static class ReferenceIntegrity
{
    private const double StitchRadiusMm = 1.5;
    private const int SamplesPerMm = 2;

    public record NetReference(int NetId, string Name, bool Continuous, string Detail);

    /// <summary>Per controlled net: does every routed segment keep a continuous
    /// reference plane (an adjacent plane layer, and — where plane copper is
    /// drawn — staying over it)?</summary>
    public static List<NetReference> Audit(BoardDocument doc)
    {
        var result = new List<NetReference>();
        if (doc.Stackup.Count != doc.CopperLayers) return result;
        doc.PlanesUpToDate();

        foreach (int netId in Controlled(doc))
        {
            var segs = doc.Traces.Where(t => !t.IsPour && t.NetId == netId).ToList();
            if (segs.Count == 0) continue;

            bool continuous = true;
            string detail = "referenced";
            foreach (var t in segs)
            {
                var (above, below) = ImpedanceEngine.ReferencePlanes(doc, t.Layer);
                if (above < 0 && below < 0)
                {
                    continuous = false; detail = $"{doc.LayerName(t.Layer)} has no adjacent reference plane";
                    break;
                }
                if (CrossesVoid(doc, t, above, below, out double vx, out double vy))
                {
                    continuous = false;
                    detail = $"crosses a reference void near ({vx:F1}, {vy:F1}) mm";
                    break;
                }
            }
            result.Add(new NetReference(netId, doc.NetName(netId), continuous, detail));
        }
        return result;
    }

    /// <summary>DRC rules 18 (reference void crossing) and 19 (reference change
    /// at a via without a stitching via).</summary>
    public static List<DrcResultItem> Check(BoardDocument doc)
    {
        var r = new List<DrcResultItem>();
        if (doc.Stackup.Count != doc.CopperLayers) return r;
        doc.PlanesUpToDate();
        var controlled = Controlled(doc);
        var gnd = GroundNets(doc);

        // rule 18 — controlled trace crossing a void/split in its reference plane
        foreach (var t in doc.Traces.Where(t => !t.IsPour && controlled.Contains(t.NetId)))
        {
            var (above, below) = ImpedanceEngine.ReferencePlanes(doc, t.Layer);
            if (CrossesVoid(doc, t, above, below, out double vx, out double vy))
                r.Add(new DrcResultItem(18, "Reference integrity",
                    $"{doc.NetName(t.NetId)} on {doc.LayerName(t.Layer)} crosses a gap in its reference " +
                    $"plane near ({vx:F1}, {vy:F1}) mm — the return current has no path; reroute over solid " +
                    $"copper or stitch the planes.", vx, vy));
        }

        // rule 19 — reference plane changes across a via with no stitching nearby
        foreach (var v in doc.Vias.Where(v => controlled.Contains(v.NetId)))
        {
            var (fa, fb) = ImpedanceEngine.ReferencePlanes(doc, v.FromLayer);
            var (ta, tb) = ImpedanceEngine.ReferencePlanes(doc, v.ToLayer);
            int refFrom = Nearest(v.FromLayer, fa, fb);
            int refTo = Nearest(v.ToLayer, ta, tb);
            if (refFrom < 0 || refTo < 0 || refFrom == refTo) continue;   // same reference: fine

            int lo = Math.Min(refFrom, refTo), hi = Math.Max(refFrom, refTo);
            bool stitched = doc.Vias.Any(s =>
                (gnd.Contains(s.NetId) || IsPlaneNet(doc, s.NetId, refFrom, refTo)) &&
                Math.Min(s.FromLayer, s.ToLayer) <= lo && Math.Max(s.FromLayer, s.ToLayer) >= hi &&
                (s.X - v.X) * (s.X - v.X) + (s.Y - v.Y) * (s.Y - v.Y) <= StitchRadiusMm * StitchRadiusMm);
            if (!stitched)
                r.Add(new DrcResultItem(19, "Return path",
                    $"{doc.NetName(v.NetId)}: via at ({v.X:F2}, {v.Y:F2}) changes reference from " +
                    $"{doc.LayerName(refFrom)} to {doc.LayerName(refTo)} with no stitching via within " +
                    $"{StitchRadiusMm:F1} mm — add a ground/plane stitch via beside it.", v.X, v.Y));
        }
        return r;
    }

    // ---- geometry: does the trace leave its reference plane's copper? ---------

    private static bool CrossesVoid(BoardDocument doc, TraceItem t, int above, int below,
                                    out double vx, out double vy)
    {
        vx = vy = 0;
        int refLayer = below >= 0 ? below : above;            // prefer the plane below
        if (refLayer < 0) return false;
        var planes = doc.Planes.Where(p => p.Layer == refLayer && p.Fill.Count > 0).ToList();
        if (planes.Count == 0) return false;                  // no drawn copper ⇒ assume solid

        double len = t.LengthMm;
        int n = Math.Max(2, (int)(len * SamplesPerMm));
        for (int i = 0; i <= n; i++)
        {
            double u = i / (double)n;
            double x = t.Ax + (t.Bx - t.Ax) * u, y = t.Ay + (t.By - t.Ay) * u;
            if (!InAnyPlane(planes, x, y)) { vx = x; vy = y; return true; }
        }
        return false;
    }

    private static bool InAnyPlane(List<PlaneShape> planes, double x, double y)
    {
        foreach (var p in planes)
            if (EvenOddInside(p.Fill, x, y)) return true;
        return false;
    }

    /// <summary>Even-odd point-in-polygon over a plane's fill rings (outer + holes).</summary>
    private static bool EvenOddInside(List<List<(double x, double y)>> fill, double px, double py)
    {
        bool inside = false;
        foreach (var ring in fill)
        {
            int c = ring.Count;
            for (int i = 0, j = c - 1; i < c; j = i++)
            {
                var (xi, yi) = ring[i];
                var (xj, yj) = ring[j];
                if (((yi > py) != (yj > py)) &&
                    (px < (xj - xi) * (py - yi) / (yj - yi + 1e-30) + xi))
                    inside = !inside;
            }
        }
        return inside;
    }

    // ---- helpers --------------------------------------------------------------

    private static int Nearest(int layer, int above, int below)
    {
        if (above < 0) return below;
        if (below < 0) return above;
        return (layer - above) <= (below - layer) ? above : below;
    }

    private static bool IsPlaneNet(BoardDocument doc, int netId, int a, int b)
        => (a >= 0 && a < doc.Stackup.Count && doc.Stackup[a].PlaneNetId == netId) ||
           (b >= 0 && b < doc.Stackup.Count && doc.Stackup[b].PlaneNetId == netId);

    private static HashSet<int> Controlled(BoardDocument doc)
        => doc.Nets.Where(n =>
        {
            var c = doc.ClassFor(n.Id);
            return c.TargetImpedanceOhm > 0 || c.TargetDiffImpedanceOhm > 0 || c.MaxSkewMm > 0;
        }).Select(n => n.Id).ToHashSet();

    private static HashSet<int> GroundNets(BoardDocument doc)
        => doc.Nets.Where(n => n.Name.Contains("GND", StringComparison.OrdinalIgnoreCase))
                   .Select(n => n.Id).ToHashSet();
}
