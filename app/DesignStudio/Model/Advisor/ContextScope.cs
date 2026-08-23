namespace DesignStudio.Model.Advisor;

// ============================================================================
// E21 step 3 — scale the context.
//
// A thousand-net, 50-layer board is too large to hand an LLM whole. But the
// decisions live in a small "hot" set: nets whose channel eye or PDN target
// fails, lanes that miss timing, nets that aren't fully routed, and parts with
// unconnected pins. ContextScope keeps that set in full and caps the healthy
// remainder to a budget, recording what was omitted so the model knows the view
// is scoped. This is the cheap, deterministic retrieval layer that keeps the
// advisor tractable; the full state is still available for a net-scoped
// drill-down when the model asks for one.
// ============================================================================

public sealed record ContextBudget(int MaxNets = 200, int MaxComponents = 200);

public static class ContextScope
{
    /// <summary>
    /// Trim a CircuitState to its failing/at-risk nets plus a budgeted slice of
    /// the healthy ones (and the components that touch them). Mutates and returns
    /// the same state. A board already within budget is returned unchanged.
    /// </summary>
    public static CircuitState Apply(CircuitState s, ContextBudget budget)
    {
        if (s.Nets.Count <= budget.MaxNets && s.Components.Count <= budget.MaxComponents)
            return s;

        // hot nets: the ones a designer would actually look at
        var hot = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var ch in s.SignalIntegrity)
            if (!ch.MaskPass || ch.EyeHeightMv <= 0.0 || ch.EyeWidthUi < 0.1) hot.Add(ch.Net);
        foreach (var r in s.PowerIntegrity)
            if (r.OverTarget) hot.Add(r.Net);
        foreach (var n in s.Nets)
            if (!n.FullyRouted && n.Members.Count > 0) hot.Add(n.Name);

        // keep all hot nets, then fill the remaining budget with healthy nets
        var kept = new List<CsNet>();
        foreach (var n in s.Nets) if (hot.Contains(n.Name)) kept.Add(n);
        foreach (var n in s.Nets)
        {
            if (kept.Count >= budget.MaxNets) break;
            if (!hot.Contains(n.Name)) kept.Add(n);
        }
        int omittedNets = s.Nets.Count - kept.Count;

        var keptNames = new HashSet<string>(kept.Select(n => n.Name), StringComparer.OrdinalIgnoreCase);
        var loose = new HashSet<string>(s.UnconnectedPins.Select(p => p.Ref), StringComparer.OrdinalIgnoreCase);

        // keep components that touch a kept net or carry an unconnected pin
        var keptComps = s.Components
            .Where(c => loose.Contains(c.Ref)
                        || c.Pins.Any(p => p.Net != null && keptNames.Contains(p.Net)))
            .ToList();
        int omittedComps = s.Components.Count - keptComps.Count;

        s.Nets = kept;
        s.Components = keptComps;
        s.ContextNote =
            $"Context scoped to {kept.Count} nets ({hot.Count} failing/at-risk kept in full) and " +
            $"{keptComps.Count} components; {omittedNets} healthy nets and {omittedComps} components " +
            "omitted to fit the context budget. Ask for a net-scoped drill-down to see an omitted net.";
        return s;
    }
}
