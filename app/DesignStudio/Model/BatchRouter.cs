namespace DesignStudio.Model;

// ============================================================================
// 1.3 Batch autorouter — negotiated rip-up-and-reroute over the A* engine.
//
// Greedy sequential routing fails because early nets wall off later ones.
// This router negotiates:
//   pass 1: route every connection (net MST edges), shortest first.
//   then:   for each failure, identify other-net copper inside the failed
//           connection's corridor, rip those nets up, route the failed
//           connection while the corridor is clear, then re-route the
//           victims. A per-connection failure history widens the corridor
//           each round (the PathFinder idea: persistent congestion cost),
//           and a hard iteration cap guarantees termination.
//
// Everything goes through BoardDocument.AutoRoute, so clearances, width,
// vias and layer choice all follow the net classes; one undo entry wraps
// the whole run.
// ============================================================================

public static class BatchRouter
{
    public record Connection(int NetId, double Ax, double Ay, double Bx, double By)
    {
        public double LengthMm => Math.Sqrt((Bx - Ax) * (Bx - Ax) + (By - Ay) * (By - Ay));
    }

    public record Result(int Routed, int Failed, int Iterations, List<Connection> Unrouted);

    /// <summary>All MST edges of every net that still needs copper.</summary>
    public static List<Connection> UnroutedConnections(BoardDocument doc)
    {
        var list = new List<Connection>();
        foreach (var net in doc.Nets)
        {
            var pads = doc.Footprints
                .SelectMany(fp => fp.Pads.Where(p => p.NetId == net.Id).Select(p => fp.PadWorld(p)))
                .ToList();
            if (pads.Count < 2) continue;
            if (doc.NetIslands(net.Id) <= 1) continue;    // already fully connected

            // Prim's MST over pad positions — the minimal set of connections
            var inTree = new List<(double x, double y)> { pads[0] };
            var rest = pads.Skip(1).ToList();
            while (rest.Count > 0)
            {
                double best = double.MaxValue; int bi = 0; (double x, double y) from = inTree[0];
                foreach (var t in inTree)
                    for (int i = 0; i < rest.Count; i++)
                    {
                        double d = (rest[i].x - t.x) * (rest[i].x - t.x) + (rest[i].y - t.y) * (rest[i].y - t.y);
                        if (d < best) { best = d; bi = i; from = t; }
                    }
                list.Add(new Connection(net.Id, from.x, from.y, rest[bi].x, rest[bi].y));
                inTree.Add(rest[bi]);
                rest.RemoveAt(bi);
            }
        }
        return list;
    }

    public static Result RouteAll(BoardDocument doc, int maxIterations = 4,
                                  Action<string>? progress = null)
    {
        var pending = UnroutedConnections(doc).OrderBy(c => c.LengthMm).ToList();
        if (pending.Count == 0) return new(0, 0, 0, new());
        int total = pending.Count, routed = 0;

        var tracesBefore = doc.Traces.Where(t => !t.IsPour).ToHashSet();
        var viasBefore = doc.Vias.ToHashSet();
        var failHistory = new Dictionary<Connection, int>();

        // ---- pass 1: plain sequential, shortest first ----
        var failed = new List<Connection>();
        foreach (var c in pending)
        {
            if (TryRoute(doc, c)) routed++;
            else { failed.Add(c); failHistory[c] = 1; }
        }
        progress?.Invoke($"pass 1: {routed}/{total} routed");

        // ---- negotiation passes ----
        int iter = 1;
        while (failed.Count > 0 && iter < maxIterations)
        {
            iter++;
            var stillFailed = new List<Connection>();
            foreach (var c in failed)
            {
                // corridor widens with failure count (persistent congestion pressure)
                double corridor = 1.0 + 2.0 * failHistory.GetValueOrDefault(c, 1);
                var victims = VictimNets(doc, c, corridor);

                foreach (int v in victims)
                {
                    foreach (var t in doc.Traces.Where(t => !t.IsPour && t.NetId == v && !tracesBefore.Contains(t)).ToList())
                        doc.InternalRemoveTrace(t);
                    foreach (var vi in doc.Vias.Where(vi => vi.NetId == v && !viasBefore.Contains(vi)).ToList())
                        doc.InternalRemoveVia(vi);
                }

                bool ok = TryRoute(doc, c);
                if (ok) routed++;
                else { stillFailed.Add(c); failHistory[c] = failHistory.GetValueOrDefault(c, 1) + 1; }

                // re-route the victims around the new copper
                foreach (int v in victims.OrderBy(v => v))
                {
                    foreach (var edge in UnroutedConnections(doc).Where(e => e.NetId == v))
                        if (!TryRoute(doc, edge) && ok)
                        {
                            // victim now blocked: queue it for the next pass
                            stillFailed.Add(edge);
                            failHistory[edge] = failHistory.GetValueOrDefault(edge, 0) + 1;
                            routed--;            // net no longer fully routed
                            routed = Math.Max(routed, 0);
                        }
                }
            }
            failed = stillFailed.Distinct().ToList();
            progress?.Invoke($"pass {iter}: {failed.Count} connection(s) still open");
        }

        return new(routed, failed.Count, iter, failed);
    }

    private static bool TryRoute(BoardDocument doc, Connection c)
        => doc.AutoRoute(c.Ax, c.Ay, c.Bx, c.By, c.NetId, 0, 0);

    /// <summary>Nets (other than the connection's own) with copper inside the corridor.</summary>
    private static List<int> VictimNets(BoardDocument doc, Connection c, double corridorMm)
    {
        double minX = Math.Min(c.Ax, c.Bx) - corridorMm, maxX = Math.Max(c.Ax, c.Bx) + corridorMm;
        double minY = Math.Min(c.Ay, c.By) - corridorMm, maxY = Math.Max(c.Ay, c.By) + corridorMm;
        var hits = new List<TraceItem>();
        BoardIndex.Traces(doc).Query(new BBox(minX, minY, maxX, maxY), hits);
        return hits.Where(t => t.NetId >= 0 && t.NetId != c.NetId)
                   .Select(t => t.NetId).Distinct().Take(3).ToList();   // bounded blast radius
    }
}
