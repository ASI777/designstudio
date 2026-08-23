namespace DesignStudio.Model;

// ============================================================================
// 1.2 Push-and-shove (first generation).
//
// When an interactively routed segment lands on top of another net's trace,
// the blocking trace bends out of the way instead of producing a DRC error.
// The shove is a connectivity-preserving "Z-bump": the blocker keeps both
// endpoints and gains a displaced middle that clears the new copper by the
// resolved clearance (rule tree aware). Single-level with third-party
// verification: if a bumped piece would itself collide with yet another
// net, the whole operation reverts and the caller is told to route around.
//
// This is deliberately the simple, always-correct slice of KiCad-style PNS:
// no springback, no via shoving yet — those build on the same primitives.
// ============================================================================

public static class ShoveRouter
{
    public record Result(bool Placed, bool Shoved, string Message);

    private const double Margin = 0.05;   // extra air beyond resolved clearance

    /// <summary>
    /// Place a trace; shove other-net blockers aside if needed. One undo entry
    /// covers the trace and every displaced blocker.
    /// </summary>
    public static Result Place(BoardDocument doc, double ax, double ay, double bx, double by,
                               double width, int netId, int layer)
    {
        var newTrace = new TraceItem { Ax = ax, Ay = ay, Bx = bx, By = by, Width = width, NetId = netId, Layer = layer };

        // candidate blockers from the spatial index
        double cx = (ax + bx) / 2, cy = (ay + by) / 2;
        double reach = newTrace.LengthMm / 2 + 5;
        var blockers = BoardIndex.Traces(doc).Query(cx, cy, reach)
            .Where(t => t.Layer == layer && t.NetId != netId && !t.IsPour &&
                        Collides(newTrace, t, Need(doc, netId, t, width)))
            .Distinct().ToList();

        if (blockers.Count == 0)
        {
            doc.AddTraceDirect(ax, ay, bx, by, width, netId, layer);
            return new(true, false, "placed");
        }

        var removed = new List<TraceItem>();
        var added = new List<TraceItem>();
        foreach (var blocker in blockers)
        {
            double need = Need(doc, netId, blocker, width);
            var bump = MakeBump(newTrace, blocker, need + Margin);
            if (bump is null)
                return Fail(doc, removed, added, $"cannot clear {doc.NetName(blocker.NetId)} (no room)");

            // third-party check: bumped pieces must not hit any further net
            foreach (var piece in bump)
            {
                var third = BoardIndex.Traces(doc).Query((piece.Ax + piece.Bx) / 2, (piece.Ay + piece.By) / 2, 5)
                    .Any(t => !removed.Contains(t) && t != blocker && t.Layer == layer && !t.IsPour &&
                              t.NetId != blocker.NetId && t.NetId != netId &&
                              Collides(piece, t, Need(doc, blocker.NetId, t, piece.Width)));
                if (third)
                    return Fail(doc, removed, added, $"shoving {doc.NetName(blocker.NetId)} would hit a third net");
                // the bump must clear the new trace itself
                if (Collides(newTrace, piece, need))
                    return Fail(doc, removed, added, $"cannot displace {doc.NetName(blocker.NetId)} far enough");
            }

            doc.InternalRemoveTrace(blocker);
            removed.Add(blocker);
            foreach (var piece in bump) { doc.InternalAddTrace(piece); added.Add(piece); }
        }

        doc.InternalAddTrace(newTrace);
        added.Add(newTrace);
        doc.Undo.Push("Route with shove",
            undo: () => { foreach (var t in added) doc.InternalRemoveTrace(t); foreach (var t in removed) doc.InternalAddTrace(t); },
            redo: () => { foreach (var t in removed) doc.InternalRemoveTrace(t); foreach (var t in added) doc.InternalAddTrace(t); });
        return new(true, true, $"shoved {removed.Count} segment(s) of " +
            string.Join(", ", removed.Select(t => doc.NetName(t.NetId)).Distinct()));
    }

    private static Result Fail(BoardDocument doc, List<TraceItem> removed, List<TraceItem> added, string why)
    {
        foreach (var t in added) doc.InternalRemoveTrace(t);
        foreach (var t in removed) doc.InternalAddTrace(t);
        return new(false, false, why);
    }

    /// <summary>Centre-to-centre clearance needed between the new net and a blocker.</summary>
    private static double Need(BoardDocument doc, int netId, TraceItem blocker, double width)
        => RuleResolver.Clearance(doc, netId, blocker.NetId) + (width + blocker.Width) / 2;

    private static bool Collides(TraceItem a, TraceItem b, double need)
        => SegSegDistance(a, b) < need - 1e-9;

    /// <summary>
    /// Z-bump: replace the blocker with up to 5 collinear-endpoint segments
    /// whose middle is displaced perpendicular to the *blocker* by enough to
    /// clear the new trace. Returns null when displacement leaves no room.
    /// </summary>
    private static List<TraceItem>? MakeBump(TraceItem intruder, TraceItem blocker, double need)
    {
        double dx = blocker.Bx - blocker.Ax, dy = blocker.By - blocker.Ay;
        double len = Math.Sqrt(dx * dx + dy * dy);
        if (len < 1e-6) return null;
        double ux = dx / len, uy = dy / len, nx = -uy, ny = ux;

        // displacement side: away from the intruder's midpoint
        double imx = (intruder.Ax + intruder.Bx) / 2, imy = (intruder.Ay + intruder.By) / 2;
        double side = Math.Sign((imx - blocker.Ax) * nx + (imy - blocker.Ay) * ny);
        if (side == 0) side = 1;
        nx *= -side; ny *= -side;                      // push away from the intruder

        double dist = SegSegDistance(intruder, blocker);
        double d = need - dist;                        // how much further away we must get
        if (d <= 0) d = need;

        // influence zone: projection of the intruder onto the blocker, widened
        double u1 = Math.Clamp(Project(blocker, intruder.Ax, intruder.Ay) - need / len, 0, 1);
        double u2 = Math.Clamp(Project(blocker, intruder.Bx, intruder.By) + need / len, 0, 1);
        if (u1 > u2) (u1, u2) = (u2, u1);

        (double x, double y) P(double u) => (blocker.Ax + u * dx, blocker.Ay + u * dy);
        var p1 = P(u1); var p2 = P(u2);
        var q1 = (x: p1.x + nx * d, y: p1.y + ny * d);
        var q2 = (x: p2.x + nx * d, y: p2.y + ny * d);

        var pts = new List<(double x, double y)> { (blocker.Ax, blocker.Ay) };
        if (u1 > 1e-6) pts.Add(p1);
        pts.Add(q1); pts.Add(q2);
        if (u2 < 1 - 1e-6) pts.Add(p2);
        pts.Add((blocker.Bx, blocker.By));

        var result = new List<TraceItem>();
        for (int i = 0; i + 1 < pts.Count; i++)
        {
            if (Math.Abs(pts[i].x - pts[i + 1].x) < 1e-9 && Math.Abs(pts[i].y - pts[i + 1].y) < 1e-9) continue;
            result.Add(new TraceItem
            {
                Ax = pts[i].x, Ay = pts[i].y, Bx = pts[i + 1].x, By = pts[i + 1].y,
                Width = blocker.Width, NetId = blocker.NetId, Layer = blocker.Layer
            });
        }
        return result.Count > 0 ? result : null;
    }

    private static double Project(TraceItem t, double px, double py)
    {
        double dx = t.Bx - t.Ax, dy = t.By - t.Ay, l2 = dx * dx + dy * dy;
        return l2 < 1e-12 ? 0 : ((px - t.Ax) * dx + (py - t.Ay) * dy) / l2;
    }

    internal static double SegSegDistance(TraceItem a, TraceItem b)
    {
        if (SegmentsIntersect(a, b)) return 0;
        double d1 = PointSeg(a.Ax, a.Ay, b), d2 = PointSeg(a.Bx, a.By, b);
        double d3 = PointSeg(b.Ax, b.Ay, a), d4 = PointSeg(b.Bx, b.By, a);
        return Math.Min(Math.Min(d1, d2), Math.Min(d3, d4));
    }

    private static bool SegmentsIntersect(TraceItem a, TraceItem b)
    {
        double Cross(double ox, double oy, double px, double py, double qx, double qy)
            => (px - ox) * (qy - oy) - (py - oy) * (qx - ox);
        double d1 = Cross(b.Ax, b.Ay, b.Bx, b.By, a.Ax, a.Ay);
        double d2 = Cross(b.Ax, b.Ay, b.Bx, b.By, a.Bx, a.By);
        double d3 = Cross(a.Ax, a.Ay, a.Bx, a.By, b.Ax, b.Ay);
        double d4 = Cross(a.Ax, a.Ay, a.Bx, a.By, b.Bx, b.By);
        return ((d1 > 0) != (d2 > 0)) && ((d3 > 0) != (d4 > 0));
    }

    private static double PointSeg(double px, double py, TraceItem t)
    {
        double dx = t.Bx - t.Ax, dy = t.By - t.Ay, l2 = dx * dx + dy * dy;
        double u = l2 < 1e-12 ? 0 : Math.Clamp(((px - t.Ax) * dx + (py - t.Ay) * dy) / l2, 0, 1);
        double qx = t.Ax + u * dx - px, qy = t.Ay + u * dy - py;
        return Math.Sqrt(qx * qx + qy * qy);
    }
}
