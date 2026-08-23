namespace DesignStudio.Model;

// ============================================================================
// Power-via fanout: bond every power/ground ball to its plane.
//
// After GND/VDD copper planes are poured, the BGA balls still sit on the outer
// layer and the DRC reports one "unconnected net island" per ball — the plane
// exists but nothing ties the balls down to it. On a 0.5 mm-pitch BGA the fix is
// a via-in-pad under each ball. Doing 60+ of those by hand is exactly the kind of
// deterministic, error-prone step worth automating.
//
// Run() drops a via under every placed pad whose net has a plane, spanning the
// pad's outer layer to the nearest plane carrying that net. Nets without a plane
// are skipped (they wait for their regulator/route). Idempotent: a second run
// adds nothing where a via already exists.
// ============================================================================

public sealed record PowerFanoutResult(
    int ViasPlaced,
    int BallsAlreadyConnected,
    int NetsFannedOut,
    int DeepSpanVias,
    IReadOnlyList<string> Log);

public static class PowerFanout
{
    /// <param name="viaDiameterMm">via-in-pad land Ø (must fit inside the ball pad).</param>
    /// <param name="viaDrillMm">finished hole Ø (HDI microvia ≈ 0.1 mm).</param>
    /// <param name="viaDiameterMm">0 (default) = derive via-in-pad geometry from the
    /// board's finest-pitch part, so this works on any board, not one footprint.</param>
    public static PowerFanoutResult Run(BoardDocument doc,
                                        double viaDiameterMm = 0,
                                        double viaDrillMm = 0,
                                        double dedupTolMm = 0.05)
    {
        var log = new List<string>();

        if (viaDiameterMm <= 0 || viaDrillMm <= 0)
        {
            var auto = PowerOptimizer.DeriveHdiParams(doc);     // adapt to this board's geometry
            if (viaDiameterMm <= 0) viaDiameterMm = auto.ViaDiameterMm;
            if (viaDrillMm <= 0) viaDrillMm = auto.ViaDrillMm;
        }

        // net -> the plane layers carrying it
        var planeLayers = new Dictionary<int, List<int>>();
        foreach (var pl in doc.Planes)
        {
            if (pl.NetId < 0) continue;
            if (!planeLayers.TryGetValue(pl.NetId, out var list)) planeLayers[pl.NetId] = list = new List<int>();
            if (!list.Contains(pl.Layer)) list.Add(pl.Layer);
        }
        if (planeLayers.Count == 0)
            return new PowerFanoutResult(0, 0, 0, 0,
                new List<string> { "No copper planes found — create GND/power planes first (Board → Generate Copper Pour…)." });

        int placed = 0, already = 0, deep = 0;
        var netsDone = new HashSet<int>();

        foreach (var fp in doc.Footprints)
        {
            int padLayer = fp.Side <= 0 ? 0 : doc.CopperLayers - 1;   // SMD pads sit on the outer layer
            foreach (var pad in fp.Pads)
            {
                if (pad.NetId < 0) continue;
                if (!planeLayers.TryGetValue(pad.NetId, out var layers)) continue;     // net has no plane
                int planeLayer = layers.OrderBy(L => Math.Abs(L - padLayer)).First();  // nearest plane
                if (planeLayer == padLayer) continue;                                  // already on the plane layer

                var (wx, wy) = fp.PadWorld(pad);
                bool exists = doc.Vias.Any(v => v.NetId == pad.NetId &&
                                                Math.Abs(v.X - wx) < dedupTolMm &&
                                                Math.Abs(v.Y - wy) < dedupTolMm);
                if (exists) { already++; continue; }

                int from = Math.Min(padLayer, planeLayer);
                int to   = Math.Max(padLayer, planeLayer);
                var via = doc.AddViaDirect(wx, wy, pad.NetId, from, to, viaDiameterMm, viaDrillMm);
                via.Type = (to - from == 1) ? ViaType.Microvia : ViaType.Blind;
                if (to - from > 1) deep++;
                placed++;
                netsDone.Add(pad.NetId);
            }
        }

        foreach (var kv in planeLayers)
            log.Add($"{doc.NetName(kv.Key)}: plane on {string.Join(", ", kv.Value.Select(doc.LayerName))}");
        if (deep > 0)
            log.Add($"{deep} via(s) span more than one layer (deep blind vias). For cheaper HDI fab, " +
                    "move that plane nearer the outer layer or stack microvias layer-by-layer.");

        return new PowerFanoutResult(placed, already, netsDone.Count, deep, log);
    }
}
