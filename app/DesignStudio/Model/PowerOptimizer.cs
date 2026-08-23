namespace DesignStudio.Model;

// ============================================================================
// One-click HDI power-plane optimization.
//
// Bundles the hand-tuned bring-up sequence for a fine-pitch BGA into a single
// command, with parameters DERIVED FROM THE BOARD (not hardcoded for one part):
//
//   0. Derive HDI geometry from the finest-pitch placed footprint.
//   1. Discover every plane/pour already defined (net + layer).
//   2. Set HDI clearance on the involved classes so copper can thread between
//      the vias.
//   3. Fan out a via-in-pad under every power/ground ball to its nearest plane.
//   4. Re-pour every plane with a thin fill stroke so copper reaches the tight
//      channels.
//   5. Report the few center balls that remain islanded — left to escape during
//      detailed routing.
// ============================================================================

public sealed record PowerOptimizeResult(
    int ViasPlaced,
    int PlanesPoured,
    double ClearanceMm,
    double FillStrokeMm,
    IReadOnlyList<(string Net, int Islands)> RemainingIslands,
    IReadOnlyList<string> Log);

public static class PowerOptimizer
{
    /// <param name="hdiClearanceMm">0 (default) = derive from the board's finest-pitch
    /// part. Pass an explicit value only to override the auto-derived geometry.</param>
    public static PowerOptimizeResult Run(BoardDocument doc,
                                          double hdiClearanceMm = 0,
                                          double hdiStrokeMm = 0,
                                          double viaDiameterMm = 0,
                                          double viaDrillMm = 0)
    {
        var log = new List<string>();

        // 0. derive HDI parameters from the actual component geometry so the
        //    optimizer adapts to ANY board — 0.4 / 0.5 / 0.8 / 1.0 mm pitch.
        var auto = DeriveHdiParams(doc);
        if (hdiClearanceMm <= 0) hdiClearanceMm = auto.ClearanceMm;
        if (hdiStrokeMm    <= 0) hdiStrokeMm    = auto.StrokeMm;
        if (viaDiameterMm  <= 0) viaDiameterMm  = auto.ViaDiameterMm;
        if (viaDrillMm     <= 0) viaDrillMm     = auto.ViaDrillMm;

        // 1. discover plane assignments from draw-planes AND existing pours
        var planeDefs = new HashSet<(int net, int layer)>();
        foreach (var pl in doc.Planes) if (pl.NetId >= 0) planeDefs.Add((pl.NetId, pl.Layer));
        foreach (var t in doc.Traces) if (t.IsPour && t.NetId >= 0) planeDefs.Add((t.NetId, t.Layer));
        if (planeDefs.Count == 0)
            return new PowerOptimizeResult(0, 0, hdiClearanceMm, hdiStrokeMm,
                new List<(string, int)>(),
                new List<string> { "No planes or pours found — pour GND/VDD first (Board -> Generate Copper Pour)." });

        var planeNets = planeDefs.Select(p => p.net).ToHashSet();
        var layersByNet = planeDefs.GroupBy(p => p.net)
                                   .ToDictionary(g => g.Key, g => g.Select(x => x.layer).Distinct().ToList());

        // 2. HDI clearance on every class a planed net uses, plus the Default class
        var classesTightened = new HashSet<int>();
        foreach (var net in planeNets) classesTightened.Add(doc.ClassFor(net).Id);
        classesTightened.Add(0);
        foreach (var c in doc.NetClasses)
            if (classesTightened.Contains(c.Id) && c.ClearanceMm > hdiClearanceMm)
                c.ClearanceMm = hdiClearanceMm;
        doc.NotifyChanged();

        // 3. fan out a via under every power/ground ball to its nearest plane
        int vias = 0;
        foreach (var fp in doc.Footprints)
        {
            int padLayer = fp.Side <= 0 ? 0 : doc.CopperLayers - 1;
            foreach (var pad in fp.Pads)
            {
                if (pad.NetId < 0 || !layersByNet.TryGetValue(pad.NetId, out var ls)) continue;
                int plane = ls.OrderBy(L => Math.Abs(L - padLayer)).First();
                if (plane == padLayer) continue;
                var (wx, wy) = fp.PadWorld(pad);
                if (doc.Vias.Any(v => v.NetId == pad.NetId &&
                                      Math.Abs(v.X - wx) < 0.05 && Math.Abs(v.Y - wy) < 0.05)) continue;
                int from = Math.Min(padLayer, plane), to = Math.Max(padLayer, plane);
                var via = doc.AddViaDirect(wx, wy, pad.NetId, from, to, viaDiameterMm, viaDrillMm);
                via.Type = (to - from == 1) ? ViaType.Microvia : ViaType.Blind;
                vias++;
            }
        }

        // 4. re-pour every plane with the HDI fill stroke + clearance
        int poured = 0;
        foreach (var (net, layer) in planeDefs)
        {
            doc.GeneratePour(net, layer, hdiStrokeMm, hdiClearanceMm);
            poured++;
        }

        // 5. report the irreducible remainder (NetIslands: 1 = fully connected)
        var remaining = new List<(string Net, int Islands)>();
        foreach (var net in planeNets.OrderBy(doc.NetName))
        {
            int islands = doc.NetIslands(net);
            if (islands > 1) remaining.Add((doc.NetName(net), islands));
        }

        log.Add($"HDI config (derived): clearance {hdiClearanceMm:0.###} mm, fill stroke {hdiStrokeMm:0.###} mm, via {viaDiameterMm:0.###}/{viaDrillMm:0.###} mm.");
        log.Add($"{vias} power via(s) fanned out; {poured} plane(s) re-poured.");
        log.Add(remaining.Count == 0
            ? "All planed power nets are fully connected."
            : "Center-ball islands left for detailed routing (escape on other layers): " +
              string.Join(", ", remaining.Select(r => $"{r.Net} = {r.Islands}")));

        return new PowerOptimizeResult(vias, poured, hdiClearanceMm, hdiStrokeMm, remaining, log);
    }

    public sealed record HdiParams(double ClearanceMm, double StrokeMm, double ViaDiameterMm, double ViaDrillMm);

    /// <summary>
    /// Derive pour/fanout geometry from the finest-pitch placed part — not from
    /// any one footprint's hardcoded numbers: a via-in-pad land that fits the
    /// smallest pad, a microvia drill leaving ~0.06 mm annular ring, and a fill
    /// stroke/clearance sized to thread the channel between adjacent vias.
    /// Coarser boards get looser (cheaper) values automatically.
    /// </summary>
    public static HdiParams DeriveHdiParams(BoardDocument doc)
    {
        double minPad = double.MaxValue, minPitch = double.MaxValue;
        foreach (var fp in doc.Footprints)
        {
            var pads = fp.Pads;
            foreach (var p in pads)
                if (p.W > 0 && p.H > 0) minPad = Math.Min(minPad, Math.Min(p.W, p.H));
            for (int i = 0; i < pads.Count; i++)
            {
                double nn = double.MaxValue;
                for (int j = 0; j < pads.Count; j++)
                {
                    if (i == j) continue;
                    double dx = pads[i].X - pads[j].X, dy = pads[i].Y - pads[j].Y;
                    double d = Math.Sqrt(dx * dx + dy * dy);
                    if (d > 1e-6) nn = Math.Min(nn, d);
                }
                if (nn < double.MaxValue) minPitch = Math.Min(minPitch, nn);
            }
        }
        if (minPad == double.MaxValue || minPitch == double.MaxValue)
            return new HdiParams(0.10, 0.10, 0.25, 0.10);

        double viaLand  = Clamp(minPad - 0.02, 0.10, 0.60);
        double viaDrill = Math.Max(0.10, viaLand - 0.12);
        double channel  = Math.Max(minPitch - viaLand, 0.05);
        double stroke   = Clamp(channel / 3.0, 0.06, 0.30);
        return new HdiParams(stroke, stroke, viaLand, viaDrill);
    }

    private static double Clamp(double v, double lo, double hi) => Math.Max(lo, Math.Min(hi, v));
}
