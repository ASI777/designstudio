namespace DesignStudio.Model;

// ============================================================================
// Derives HDI fanout/pour parameters from the board's own geometry, so the
// power automation adapts to ANY part — 1.0 mm FCBGA, 0.5 mm CSP, 0.4 mm WLCSP —
// instead of carrying constants tuned for one footprint.
//
// Everything is a function of the finest pad pitch on the board and the size of
// the pads being bonded:
//   via land  = min(pitch/2, smallest pad)         — fits in the pad, leaves a channel
//   via drill = via land * 0.4, clamped to laser minimum, annular ring kept ≥ 0.05
//   clearance = 0.8 · (pitch − via land)/2, clamped 0.05‥0.2 mm
//   fill stroke = clearance                         — thin enough to thread the channel
//
// For the 0.5 mm part this reproduces the values we tuned by hand (via 0.25 /
// drill 0.10 / clearance 0.10 / stroke 0.10); for a 1.0 mm part it scales up to
// roughly via 0.5 / drill 0.20 / clearance 0.20.
// ============================================================================

public readonly record struct HdiParams(
    double PitchMm, double ViaDiameterMm, double ViaDrillMm,
    double ClearanceMm, double FillStrokeMm)
{
    public string Describe() =>
        $"pitch {PitchMm:0.###} mm → via {ViaDiameterMm:0.###}/{ViaDrillMm:0.###} mm, " +
        $"clearance {ClearanceMm:0.###} mm, fill stroke {FillStrokeMm:0.###} mm";
}

public static class HdiPlanner
{
    public const double LaserDrillMinMm = 0.10;   // smallest reliable laser microvia
    public const double MinAnnularMm    = 0.05;   // capture-ring floor

    /// <summary>Compute parameters from the board. If <paramref name="bondNets"/>
    /// is given, pad-size is measured only on those nets (the power balls).</summary>
    public static HdiParams Derive(BoardDocument doc, ISet<int>? bondNets = null)
    {
        // finest centre-to-centre pad spacing on any footprint = the board's pitch
        double pitch = double.MaxValue;
        foreach (var fp in doc.Footprints)
        {
            var pads = fp.Pads;
            for (int i = 0; i < pads.Count; i++)
                for (int j = i + 1; j < pads.Count; j++)
                {
                    double dx = pads[i].X - pads[j].X, dy = pads[i].Y - pads[j].Y;
                    double d = System.Math.Sqrt(dx * dx + dy * dy);
                    if (d > 0.01 && d < pitch) pitch = d;
                }
        }
        if (pitch == double.MaxValue) pitch = 1.0;     // no multi-pad parts: assume 1 mm

        // smallest pad we must drop a via into
        double padMin = double.MaxValue;
        foreach (var fp in doc.Footprints)
            foreach (var p in fp.Pads)
                if (bondNets == null || bondNets.Contains(p.NetId))
                    padMin = System.Math.Min(padMin, System.Math.Min(p.W, p.H));
        if (padMin == double.MaxValue) padMin = pitch * 0.55;

        double viaDia = System.Math.Min(pitch * 0.5, padMin);
        double drill  = System.Math.Max(LaserDrillMinMm, Round(viaDia * 0.4));
        if ((viaDia - drill) / 2 < MinAnnularMm)        // protect the annular ring
            drill = System.Math.Max(LaserDrillMinMm, viaDia - 2 * MinAnnularMm);
        double clearance = Round(System.Math.Clamp((pitch - viaDia) / 2 * 0.8, 0.05, 0.2));
        double stroke    = clearance;

        return new HdiParams(Round(pitch), Round(viaDia), Round(drill), clearance, stroke);
    }

    private static double Round(double v) => System.Math.Round(v, 3);
}
