namespace DesignStudio.Model.Advisor;

// ============================================================================
// E21 (extension). Layer-count planning.
//
// "How many copper layers does this board need?" is one of the first stackup
// decisions, and it follows from the components and the routing they demand, not
// from a guess. LayerPlanner estimates a recommended copper-layer count from the
// exported CircuitState: signal-routing demand (the densest component's escape
// plus overall net count), whether the design carries high-speed channels or
// DDR (which need stripline reference planes, so routing moves to inner layers),
// and the number of power domains (each plane pair). It returns an even,
// fab-realistic count with a transparent rationale, so the advisor can recommend
// raising (or notes the comfort margin on) the current layer count.
//
// This is a first-order planner — a starting stackup, not a routing guarantee;
// the real proof is a completed route + the SI/PI sign-off the other engines run.
// ============================================================================

public sealed record LayerEstimate(
    int RecommendedLayers,
    int SignalLayers,
    int PlaneLayers,
    string Rationale);

public static class LayerPlanner
{
    /// <summary>Estimate the copper-layer count this design calls for.</summary>
    public static LayerEstimate Estimate(CircuitState s)
    {
        // --- signal-routing demand -------------------------------------------
        // power/ground nets don't consume signal-routing channels
        int signalNets = s.Nets.Count(n => !IsPowerNet(n.Name, n.Class));
        int densestPins = s.Components.Count == 0 ? 0 : s.Components.Max(c => c.Pins.Count);

        // A fine-pitch BGA escapes ~2 pin-rows per signal layer; rows ≈ half the
        // grid side. This is the dominant signal-layer driver on dense parts.
        int bgaSignalLayers = 1;
        if (densestPins >= 200)
        {
            int gridSide = (int)Math.Ceiling(Math.Sqrt(densestPins));
            int rows = Math.Max(0, (gridSide - 2 + 1) / 2);     // inner rows needing escape
            bgaSignalLayers = Math.Max(1, (int)Math.Ceiling(rows / 2.0));
        }

        // Overall density: a signal layer carries on the order of ~120 routed
        // nets on a typical board before congestion forces another layer.
        const int netsPerLayer = 120;
        int densitySignalLayers = (int)Math.Ceiling(signalNets / (double)netsPerLayer);

        int signalLayers = Math.Max(2, Math.Max(bgaSignalLayers, densitySignalLayers));

        // High-speed (SerDes channels or DDR) wants stripline: route between
        // planes, so at least two inner signal layers plus their references.
        bool highSpeed = s.SignalIntegrity.Count > 0 || s.DdrLanes.Count > 0;
        if (highSpeed) signalLayers = Math.Max(signalLayers, 4);

        // --- plane demand ----------------------------------------------------
        int powerDomains = CountPowerDomains(s);
        // GND + the main rail = 2 planes; each pair of extra rails adds a split plane;
        // high-speed adds a dedicated reference so signals are never plane-starved.
        int planeLayers = 2 + (int)Math.Ceiling(Math.Max(0, powerDomains - 1) / 2.0);
        if (highSpeed) planeLayers += 1;

        int total = signalLayers + planeLayers;
        if (total % 2 != 0) total += 1;                 // PCBs are built in even counts
        total = Math.Clamp(total, 2, 64);

        var why = $"{signalLayers} signal layer(s) " +
                  $"(densest part {densestPins} pins, {signalNets} signal nets" +
                  (highSpeed ? ", high-speed → stripline" : "") + ") + " +
                  $"{planeLayers} plane layer(s) ({powerDomains} power domain(s)" +
                  (highSpeed ? " + a high-speed reference" : "") + "), " +
                  $"rounded to {total} (even).";

        return new LayerEstimate(total, signalLayers, planeLayers, why);
    }

    private static int CountPowerDomains(CircuitState s)
    {
        // distinct power/ground nets are the rough domain count; floor at 2 (a
        // ground and at least one supply) for anything non-trivial
        var domains = s.Nets.Where(n => IsPowerNet(n.Name, n.Class))
                            .Select(n => n.Name).Distinct(StringComparer.OrdinalIgnoreCase).Count();
        return Math.Max(s.Components.Count > 1 ? 2 : 1, domains);
    }

    private static bool IsPowerNet(string name, string netClass)
    {
        string n = (name ?? "").ToUpperInvariant();
        string c = (netClass ?? "").ToUpperInvariant();
        return c.Contains("POWER") || c.Contains("PWR") ||
               n is "GND" or "GROUND" or "VSS" or "AGND" or "DGND" ||
               n.StartsWith("VDD") || n.StartsWith("VCC") || n.StartsWith("VSS") ||
               n.StartsWith("V") && n.Length <= 6 && n.Any(char.IsDigit) ||   // V1P8, V3V3, 1V2…
               n.Contains("VCORE") || n.Contains("VDDQ") || n.Contains("PWR") || n.Contains("RAIL");
    }
}
