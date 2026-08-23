using System.Text.Json.Serialization;
using Clipper2Lib;

namespace DesignStudio.Model;

// ============================================================================
// 3.0 Polygon plane shapes — copper planes as real geometry.
//
// A PlaneShape is (net, layer, clearance, outline). Regeneration computes
// Fill = outline − inflate(other-net copper, clearance) with Clipper2 boolean
// ops at 1 µm integer resolution. Fill is a set of closed polygons; Clipper's
// orientation convention (outer CCW, holes CW with EvenOdd fill) flows
// straight into WPF rendering and Gerber LPD/LPC regions.
//
// Same-net pads/vias are NOT subtracted — the plane connects to them (solid
// connect; thermal reliefs are a later refinement). This is what makes a
// plane electrically meaningful where the old hatch pour was only paint.
// ============================================================================

public class PlaneShape
{
    [JsonPropertyName("net")] public int NetId { get; set; } = -1;
    [JsonPropertyName("layer")] public int Layer { get; set; }
    [JsonPropertyName("clearance_mm")] public double ClearanceMm { get; set; } = 0.25;
    /// <summary>Boundary polygon, mm. Empty = whole board outline.</summary>
    [JsonPropertyName("outline")] public List<double[]>? Outline { get; set; }

    /// <summary>Computed copper (closed polygons; CW = hole). Not serialized — regenerated on load.</summary>
    [JsonIgnore] public List<List<(double x, double y)>> Fill { get; set; } = new();
}

public static class PlaneGenerator
{
    private const double Scale = 1000;   // mm → µm integer space

    /// <summary>Regenerate one plane's fill from current board state.</summary>
    public static void Regenerate(BoardDocument doc, PlaneShape plane)
    {
        // subject: explicit outline or full board rect
        var subject = new PathsD
        {
            plane.Outline is { Count: >= 3 }
                ? new PathD(plane.Outline.Select(p => new PointD(p[0], p[1])))
                : new PathD(doc.EffectiveOutline().Select(p => new PointD(p[0], p[1])))
        };

        // obstacles: copper of OTHER nets on this layer, inflated by clearance
        var blockers = new PathsD();

        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                bool onLayer = pad.ThroughHole || (fp.Side <= 0 ? 0 : doc.CopperLayers - 1) == plane.Layer;
                if (!onLayer || pad.NetId == plane.NetId) continue;
                blockers.Add(PadPoly(fp, pad));
            }

        foreach (var t in doc.Traces)
        {
            if (t.Layer != plane.Layer || t.NetId == plane.NetId) continue;
            blockers.AddRange(Clipper.InflatePaths(
                new PathsD { new PathD(new[] { new PointD(t.Ax, t.Ay), new PointD(t.Bx, t.By) }) },
                t.Width / 2, JoinType.Round, EndType.Round, 4));
        }

        foreach (var v in doc.Vias)
        {
            bool onLayer = plane.Layer >= Math.Min(v.FromLayer, v.ToLayer) &&
                           plane.Layer <= Math.Max(v.FromLayer, v.ToLayer);
            if (!onLayer || v.NetId == plane.NetId) continue;
            blockers.Add(Circle(v.X, v.Y, v.DiameterMm / 2));
        }

        var inflated = Clipper.InflatePaths(blockers, plane.ClearanceMm, JoinType.Round, EndType.Polygon, 4);
        var fill = Clipper.Difference(subject, inflated, FillRule.NonZero, 4);

        plane.Fill = fill.Select(path => path.Select(p => (p.x, p.y)).ToList()).ToList();
    }

    /// <summary>Regenerate every plane (called after edits via BoardDocument).</summary>
    public static void RegenerateAll(BoardDocument doc)
    {
        foreach (var p in doc.Planes) Regenerate(doc, p);
    }

    private static PathD PadPoly(FootprintItem fp, PadItem pad)
    {
        double r = fp.RotationDeg * Math.PI / 180, c = Math.Cos(r), s = Math.Sin(r);
        var pts = new PointD[4];
        var corners = new (double lx, double ly)[]
            { (-pad.W / 2, -pad.H / 2), (pad.W / 2, -pad.H / 2), (pad.W / 2, pad.H / 2), (-pad.W / 2, pad.H / 2) };
        for (int i = 0; i < 4; i++)
        {
            // rotate pad corner around footprint origin together with the pad centre
            double wx = fp.X + (pad.X + corners[i].lx) * c - (pad.Y + corners[i].ly) * s;
            double wy = fp.Y + (pad.X + corners[i].lx) * s + (pad.Y + corners[i].ly) * c;
            pts[i] = new PointD(wx, wy);
        }
        return new PathD(pts);
    }

    private static PathD Circle(double cx, double cy, double r, int n = 16)
    {
        var pts = new PointD[n];
        for (int i = 0; i < n; i++)
        {
            double a = 2 * Math.PI * i / n;
            pts[i] = new PointD(cx + r * Math.Cos(a), cy + r * Math.Sin(a));
        }
        return new PathD(pts);
    }

    /// <summary>Signed area: positive = outer boundary, negative = hole (Clipper convention).</summary>
    public static double SignedArea(List<(double x, double y)> poly)
    {
        double a = 0;
        for (int i = 0; i < poly.Count; i++)
        {
            var (x1, y1) = poly[i];
            var (x2, y2) = poly[(i + 1) % poly.Count];
            a += x1 * y2 - x2 * y1;
        }
        return a / 2;
    }
}
