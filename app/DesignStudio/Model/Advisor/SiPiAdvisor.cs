namespace DesignStudio.Model.Advisor;

// ============================================================================
// E21. SI/PI-aware design advisor.
//
// The AI loop used to reason only about netlist/component completeness — it was
// blind to the S2–S13 analysis, so it could not recommend the decisions that
// actually matter on a 24–50-layer board. S7–S13 exposed that analysis into
// `circuit-state`; this is the design-space reasoning that turns a failing
// result into a concrete fix.
//
// SiPiAdvisor is a deterministic recommender over the exported CircuitState: it
// maps each sign-off failure to the physically correct action (back-drill a via
// stub, add/move a decap at a PDN antinode, add equalisation, length-tune a DDR
// bit, fix a reference/stackup). It is the ground truth the LLM advisor is held
// to — the model may phrase and prioritise, but a board that fails a DDR5 lane
// or a PDN target produces the right SI/PI action here without any model call,
// which is exactly the E21 exit criterion and what the tests pin down.
// ============================================================================

public enum AdviceOp
{
    Add, Remove, Replace,        // component-level (the original contract)
    Backdrill, MoveDecap, AddDecap, SetEq, Stackup, LengthTune, Reference,   // SI/PI
    SetLayers                    // stackup: recommend a copper-layer count
}

/// <summary>One recommended action. Fields are op-specific; <see cref="Value"/>
/// carries a free-form parameter (a decap value, an EQ tap spec, a backdrill
/// target).</summary>
public sealed record AdviceAction(
    AdviceOp Op,
    string Reason,
    string? Net = null,
    string? ForRef = null,
    string? Mpn = null,
    string? Manufacturer = null,
    string? Value = null,
    IReadOnlyList<AdviceConnection>? Connections = null);

/// <summary>A single pin → net intent from an advice action (e.g. a regulator's
/// VOUT to the VDD_CORE rail, or a gate-driver INHA to a fabric PWM net). The
/// AdviceApplier turns these into pad-net assignments.</summary>
public sealed record AdviceConnection(string Pin, string ToNet);

public sealed record AdviceReport(
    string Summary,
    IReadOnlyList<AdviceAction> Actions,
    IReadOnlyList<string> Risks)
{
    public const string Schema = "design-studio.advice/1";
}

public static class SiPiAdvisor
{
    /// <summary>
    /// Recommend SI/PI fixes from the analysed circuit state. Pure and
    /// deterministic: same state ⇒ same actions, no model call.
    /// </summary>
    public static AdviceReport Recommend(CircuitState s)
    {
        var actions = new List<AdviceAction>();
        var risks = new List<string>();

        int failChan = 0, failRail = 0, failLane = 0;

        // ---- Stackup: are there enough copper layers for these components? ----
        var layers = LayerPlanner.Estimate(s);
        if (s.Board.CopperLayers > 0 && s.Board.CopperLayers < layers.RecommendedLayers)
            actions.Add(new AdviceAction(AdviceOp.SetLayers,
                $"The board has {s.Board.CopperLayers} copper layers but the components call for about " +
                $"{layers.RecommendedLayers}: {layers.Rationale} Raise the stackup so signals have routing room " +
                "and a reference plane next to every signal layer.",
                Value: layers.RecommendedLayers.ToString()));

        // ---- Signal integrity: closed/failed channel eyes (S4/S6/S12) --------
        foreach (var ch in s.SignalIntegrity)
        {
            bool closed = ch.EyeHeightMv <= 0.0;
            bool fails = !ch.MaskPass || closed || ch.EyeWidthUi < 0.1;
            if (!fails) continue;
            failChan++;

            if (ch.HasViaStub)
                actions.Add(new AdviceAction(AdviceOp.Backdrill,
                    $"{ch.Net}: eye {ch.EyeHeightMv:F0} mV / {ch.EyeWidthUi:F2} UI fails at {ch.DataRateGbps:F0} Gb/s. " +
                    "An un-backdrilled through-via leaves a resonant stub — back-drill it to the last used layer to remove the suckout.",
                    Net: ch.Net, Value: "backdrill stub to deepest used layer"));

            if (ch.InsertionLossDb <= -10.0)
            {
                actions.Add(new AdviceAction(AdviceOp.SetEq,
                    $"{ch.Net}: {ch.InsertionLossDb:F1} dB insertion loss at Nyquist closes the eye. " +
                    "Add Tx FFE pre-emphasis + Rx CTLE peaking (and a DFE tap for the residual post-cursors).",
                    Net: ch.Net, Value: "FFE+CTLE(+DFE)"));
                risks.Add($"{ch.Net}: equalisation adds latency and power — confirm the SerDes supports the tap configuration.");
            }

            // closed without a stub and without dominant loss ⇒ a reference /
            // impedance problem on the layer, not something EQ or back-drill fixes
            if (closed && !ch.HasViaStub && ch.InsertionLossDb > -10.0)
                actions.Add(new AdviceAction(AdviceOp.Stackup,
                    $"{ch.Net}: eye is closed with neither a via stub nor high loss — check reference-plane continuity " +
                    "and the trace impedance on its layer (stackup / reference-plane assignment).",
                    Net: ch.Net));
        }

        // ---- Power integrity: rails over the impedance target (S5/S8) --------
        foreach (var r in s.PowerIntegrity)
        {
            if (!r.OverTarget) continue;
            failRail++;

            // a decap that worsens an anti-resonance (negative contribution) → move it
            foreach (var entry in r.DecapRanking)
            {
                if (!entry.Contains(": -")) continue;     // negative mΩ contribution
                string refDes = entry.Split(':')[0].Trim();
                actions.Add(new AdviceAction(AdviceOp.MoveDecap,
                    $"{r.Net}: {refDes} sits at a current null and worsens the {r.WorstFreqMhz:F0} MHz anti-resonance " +
                    $"({entry}); move it toward the {r.WorstFreqMhz:F0} MHz antinode or change its value.",
                    Net: r.Net, ForRef: refDes));
            }

            string where = !string.IsNullOrWhiteSpace(r.PlacementHint)
                ? r.PlacementHint!
                : $"at the {r.WorstFreqMhz:F0} MHz antinode";
            actions.Add(new AdviceAction(AdviceOp.AddDecap,
                $"{r.Net}: worst Z {r.WorstZMohm:F1} mΩ exceeds the {r.TargetMohm:F1} mΩ target at {r.WorstFreqMhz:F0} MHz. " +
                $"Add {DecapClassFor(r.WorstFreqMhz)} {where}.",
                Net: r.Net, Value: DecapClassFor(r.WorstFreqMhz)));
        }

        // ---- DDR byte-lane timing (S6/S9) ------------------------------------
        foreach (var d in s.DdrLanes)
        {
            if (d.Pass) continue;
            failLane++;

            if (d.WorstHoldPs < 0)
                actions.Add(new AdviceAction(AdviceOp.LengthTune,
                    $"DDR lane {d.Lane}: hold margin {d.WorstHoldPs:F0} ps is negative at bit {d.WorstBit}. " +
                    "Add intra-lane length to the early bit / re-match the byte-lane skew so data is stable through the hold window.",
                    Net: d.Lane, ForRef: d.WorstBit));

            if (d.WorstSetupPs < 0)
                actions.Add(new AdviceAction(AdviceOp.SetEq,
                    $"DDR lane {d.Lane}: setup margin {d.WorstSetupPs:F0} ps is negative at bit {d.WorstBit}. " +
                    "Cut ISI with write/read equalisation (or reduce flight time) so the bit settles before the strobe.",
                    Net: d.Lane, ForRef: d.WorstBit));

            if (d.WorstHoldPs >= 0 && d.WorstSetupPs >= 0)
                actions.Add(new AdviceAction(AdviceOp.Reference,
                    $"DDR lane {d.Lane} fails though setup/hold margins are positive — check reference continuity / SSN on this lane.",
                    Net: d.Lane));
        }

        string summary = (failChan + failRail + failLane) == 0
            ? "No SI/PI sign-off failures in the analysed results — the board meets the channel, PDN and DDR margins it was checked against."
            : $"{failChan} channel(s), {failRail} power rail(s) and {failLane} DDR lane(s) fail sign-off; " +
              $"{actions.Count} SI/PI action(s) proposed.";

        return new AdviceReport(summary, actions, risks);
    }

    // a first-order decap class for the resonance frequency: small high-frequency
    // caps for the GHz-ish band, bulk for the low end
    private static string DecapClassFor(double freqMhz) =>
        freqMhz >= 100 ? "a 100 nF 0201/0402 (high-frequency)" :
        freqMhz >= 10 ? "a 1 µF 0402 (mid-band)" :
                        "a 10–47 µF bulk (low-frequency)";
}
