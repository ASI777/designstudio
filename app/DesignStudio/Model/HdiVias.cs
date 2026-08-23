namespace DesignStudio.Model;

// ============================================================================
// E11. HDI — blind / buried / stacked microvias.
//
// Until now a via was one mechanical barrel spanning FromLayer..ToLayer. Dense
// boards (Apple-M-class laptops, AI baseboards) are HDI: laser-drilled microvias
// that span a single build-up layer, stacked on top of each other or staggered,
// plus blind and buried mechanical vias. This module classifies vias by
// fabrication type, finds stacked microvia groups, and checks the HDI design
// rules (microvia aspect ratio, annular ring / capture pad, stack depth).
//
//   • Through  — spans the whole board (mechanical).
//   • Blind    — one end on an outer layer, the other inner (mechanical).
//   • Buried   — inner-to-inner (mechanical).
//   • Microvia — laser, one build-up layer, small drill, small capture pad.
//
// The SI side is handled in ViaModel: a microvia is too short to resonate, so it
// is a capacitance-dominated transition, and a stack is the cascade of those
// short transitions (which ChannelExtractor already multiplies along the path).
// The fab side is FabExporter.DrillSpanReport (laser vs mechanical drill spans).
// ============================================================================

public static class HdiVias
{
    public const double MaxMicroviaAspect = 1.0;     // laser microvia depth : drill
    public const double MaxMechanicalAspect = 12.0;  // plated mechanical barrel : drill
    public const double MinAnnularMm = 0.05;         // capture-pad ring (pad−drill)/2
    public const double MaxStackDepth = 3;           // stacked microvias before special process
    private const double CoLocateTolMm = 0.08;

    /// <summary>Fabrication class from the explicit flag, else inferred geometry.</summary>
    public static ViaType Resolve(BoardDocument doc, ViaItem v)
    {
        int lo = Math.Min(v.FromLayer, v.ToLayer), hi = Math.Max(v.FromLayer, v.ToLayer);
        int n = doc.CopperLayers;
        if (v.Type == ViaType.Microvia || (hi - lo <= 1 && v.DrillMm <= 0.15)) return ViaType.Microvia;
        if (lo == 0 && hi == n - 1) return ViaType.Through;
        if (lo == 0 || hi == n - 1) return ViaType.Blind;
        return ViaType.Buried;
    }

    public static bool IsLaser(ViaType t) => t == ViaType.Microvia;

    /// <summary>Barrel length (mm) over the via's layer span.</summary>
    public static double BarrelLengthMm(BoardDocument doc, ViaItem v)
    {
        int lo = Math.Min(v.FromLayer, v.ToLayer), hi = Math.Max(v.FromLayer, v.ToLayer);
        if (doc.Stackup.Count != doc.CopperLayers)
            return (hi - lo) * (doc.DielectricHeightMm + doc.CopperThicknessMm);
        double len = 0;
        for (int i = lo; i < hi && i < doc.Stackup.Count; i++)
            len += doc.Stackup[i].DielectricHeightMm + doc.Stackup[i].CopperThicknessMm;
        return len;
    }

    public static double AspectRatio(BoardDocument doc, ViaItem v)
        => v.DrillMm > 0 ? BarrelLengthMm(doc, v) / v.DrillMm : 0;

    /// <summary>Groups of co-located microvias on consecutive spans (a stack).</summary>
    public static List<List<ViaItem>> Stacks(BoardDocument doc)
    {
        var micros = doc.Vias.Where(v => Resolve(doc, v) == ViaType.Microvia).ToList();
        var used = new HashSet<ViaItem>();
        var stacks = new List<List<ViaItem>>();
        foreach (var v in micros)
        {
            if (used.Contains(v)) continue;
            var group = doc.Vias.Where(u => Resolve(doc, u) == ViaType.Microvia &&
                                            Math.Abs(u.X - v.X) < CoLocateTolMm &&
                                            Math.Abs(u.Y - v.Y) < CoLocateTolMm)
                                .OrderBy(u => Math.Min(u.FromLayer, u.ToLayer)).ToList();
            foreach (var g in group) used.Add(g);
            if (group.Count >= 2) stacks.Add(group);
        }
        return stacks;
    }

    /// <summary>HDI design-rule checks — rule 25.</summary>
    public static List<DrcResultItem> Check(BoardDocument doc)
    {
        var r = new List<DrcResultItem>();
        foreach (var v in doc.Vias)
        {
            var type = Resolve(doc, v);
            double aspect = AspectRatio(doc, v);
            double annular = (v.DiameterMm - v.DrillMm) / 2;

            if (type == ViaType.Microvia)
            {
                int lo = Math.Min(v.FromLayer, v.ToLayer), hi = Math.Max(v.FromLayer, v.ToLayer);
                if (hi - lo > 1)
                    r.Add(new DrcResultItem(25, "Microvia span",
                        $"microvia at ({v.X:F2}, {v.Y:F2}) spans {hi - lo} layers — a laser microvia is a single " +
                        "build-up layer; use stacked microvias or a mechanical via.", v.X, v.Y));
                if (aspect > MaxMicroviaAspect)
                    r.Add(new DrcResultItem(25, "Microvia aspect",
                        $"microvia at ({v.X:F2}, {v.Y:F2}) aspect {aspect:F2} > {MaxMicroviaAspect:F1} — laser drilling " +
                        "won't reliably plate; thin the build-up dielectric or widen the drill.", v.X, v.Y));
            }
            else if (aspect > MaxMechanicalAspect)
                r.Add(new DrcResultItem(25, "Via aspect",
                    $"{type} via at ({v.X:F2}, {v.Y:F2}) aspect {aspect:F1} > {MaxMechanicalAspect:F0} — hard to plate; " +
                    "split into stacked/staggered vias or use a thicker drill.", v.X, v.Y));

            if (annular < MinAnnularMm)
                r.Add(new DrcResultItem(25, "Annular ring",
                    $"{type} via at ({v.X:F2}, {v.Y:F2}) has a {annular * 1000:F0} µm capture ring " +
                    $"(< {MinAnnularMm * 1000:F0} µm) — enlarge the pad or shrink the drill.", v.X, v.Y));
        }

        foreach (var stack in Stacks(doc))
            if (stack.Count > MaxStackDepth)
                r.Add(new DrcResultItem(25, "Microvia stack",
                    $"stack of {stack.Count} microvias at ({stack[0].X:F2}, {stack[0].Y:F2}) exceeds {MaxStackDepth} high — " +
                    "stagger some or limit stack depth (copper-fill is required for any stacked microvia).",
                    stack[0].X, stack[0].Y));
        return r;
    }
}
