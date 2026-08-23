namespace DesignStudio.Model;

// Automatic stackup sizing. The layer count a board needs is driven by its
// hardest-to-escape component plus signal-integrity demands:
//
//  * BGA escape routing — the outer two ball rings of an area array escape on
//    a single signal layer (one trace between adjacent lands at typical
//    pitches); every additional pair of inner rings needs another signal
//    layer reached through vias. ring depth = ceil(min(rows, cols) / 2).
//  * Plane layers — once a design has real power distribution (power_in pins
//    on multiple nets) or any controlled-impedance / diff-pair net class, the
//    stackup gets dedicated GND and PWR planes: they give return paths,
//    suppress crosstalk/EMI, and make impedance predictable.
//  * Fabrication reality — copper counts are even (paired lamination) and the
//    app supports 2..16.
public static class StackupPlanner
{
    public record Plan(int Layers, List<string> Rationale);

    public static Plan Recommend(BoardDocument doc)
    {
        var why = new List<string>();

        // ---- worst-case escape depth over all parts ----
        int worstRings = 1;
        string worstRef = "";
        foreach (var fp in doc.Footprints)
        {
            int rings = EscapeRingDepth(fp);
            if (rings > worstRings) { worstRings = rings; worstRef = fp.RefDes; }
        }
        int signalLayers = Math.Max(1, (int)Math.Ceiling(worstRings / 2.0));
        if (worstRings > 2)
            why.Add($"{worstRef}: area-array part {worstRings} ball rings deep → {signalLayers} signal layer(s) to escape every pin.");

        // both outer faces route by default; inner signal layers only beyond that
        int innerSignals = Math.Max(0, signalLayers - 2);

        // ---- plane layers ----
        bool hasPower = doc.Footprints.SelectMany(f => f.Pads)
                           .Count(p => p.ElectricalType is "power_in" or "power_out") >= 4;
        bool highSpeed = doc.NetClasses.Any(c => c.Id != 0 && (c.DiffPairGapMm > 0 || c.MaxSkewMm > 0));
        bool dense = doc.Footprints.Sum(f => f.Pads.Count) > 80;
        int planes = 0;
        if (hasPower || highSpeed || dense || innerSignals > 0)
        {
            planes = 2;
            if (highSpeed) why.Add("Controlled-impedance / diff-pair net classes → dedicated GND plane for return paths and crosstalk/EMI isolation, plus a PWR plane.");
            else if (hasPower) why.Add("Multiple power pins → dedicated GND + PWR planes for clean power distribution.");
            else why.Add("High pin density → GND + PWR planes recommended.");
        }

        int total = 2 + innerSignals + planes;
        if (total % 2 != 0) total++;                 // fabs laminate in pairs
        total = Math.Clamp(total, 2, 16);
        why.Add($"Recommended stackup: {total} copper layers.");
        return new Plan(total, why);
    }

    /// <summary>
    /// How many concentric pad rings deep a footprint is. Perimeter packages
    /// (QFN/SOIC/QFP) are 1; an N×M BGA is ceil(min(N,M)/2).
    /// </summary>
    private static int EscapeRingDepth(FootprintItem fp)
    {
        if (fp.Pads.Count < 16) return 1;
        var xs = fp.Pads.Select(p => Math.Round(p.X, 2)).Distinct().Count();
        var ys = fp.Pads.Select(p => Math.Round(p.Y, 2)).Distinct().Count();
        // area array if the pads substantially fill the grid (perimeter parts don't)
        if (xs < 3 || ys < 3 || fp.Pads.Count < 0.6 * xs * ys) return 1;
        return (int)Math.Ceiling(Math.Min(xs, ys) / 2.0);
    }
}
