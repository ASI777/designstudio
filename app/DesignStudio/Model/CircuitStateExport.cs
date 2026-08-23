using System.Text.Json;
using System.Text.Json.Serialization;
using DesignStudio.Interop;
using DesignStudio.Model.LinkSim;

namespace DesignStudio.Model;

// The outbound half of the AI loop: serializes everything an LLM advisor
// (Gemini / Claude / any structured-output model) needs to recommend the next
// component, removals, or replacements — placed components with their full
// datasheet electrical payloads, every net with its members and connectivity
// state, unconnected pins (the strongest "what's missing" signal), DRC/ERC
// findings, and a physics snapshot of the routing rules on this stackup.
// The response contract (design-studio.advice/1) is embedded in the file so
// the model knows exactly how to answer.

public class CircuitState
{
    [JsonPropertyName("schema")] public string Schema { get; set; } = "design-studio.circuit-state/1";
    [JsonPropertyName("generated_utc")] public string GeneratedUtc { get; set; } = "";
    [JsonPropertyName("design_goal")] public string DesignGoal { get; set; } = "";
    [JsonPropertyName("board")] public CsBoard Board { get; set; } = new();
    [JsonPropertyName("net_classes")] public List<CsNetClass> NetClasses { get; set; } = new();
    [JsonPropertyName("components")] public List<CsComponent> Components { get; set; } = new();
    [JsonPropertyName("nets")] public List<CsNet> Nets { get; set; } = new();
    [JsonPropertyName("unconnected_pins")] public List<CsLoosePin> UnconnectedPins { get; set; } = new();
    [JsonPropertyName("drc")] public CsDrc Drc { get; set; } = new();
    [JsonPropertyName("erc")] public List<string> Erc { get; set; } = new();
    [JsonPropertyName("physics")] public List<CsClassPhysics> Physics { get; set; } = new();
    // S2–S6 sign-off results (populated when an AnalysisResults bundle is supplied)
    [JsonPropertyName("signal_integrity")] public List<CsChannel> SignalIntegrity { get; set; } = new();
    [JsonPropertyName("power_integrity")] public List<CsPdnRail> PowerIntegrity { get; set; } = new();
    [JsonPropertyName("ddr_lanes")] public List<CsDdrLane> DdrLanes { get; set; } = new();
    [JsonPropertyName("assistant_instructions")] public string AssistantInstructions { get; set; } = "";
    /// <summary>Set when ContextScope trims the state for a large board.</summary>
    [JsonPropertyName("context_note")] public string? ContextNote { get; set; }
}

public class CsBoard
{
    [JsonPropertyName("width_mm")] public double WidthMm { get; set; }
    [JsonPropertyName("height_mm")] public double HeightMm { get; set; }
    [JsonPropertyName("copper_layers")] public int CopperLayers { get; set; }
    [JsonPropertyName("dielectric_er")] public double DielectricEr { get; set; }
    [JsonPropertyName("dielectric_h_mm")] public double DielectricHeightMm { get; set; }
    [JsonPropertyName("loss_tangent")] public double LossTangent { get; set; }
    [JsonPropertyName("copper_t_mm")] public double CopperThicknessMm { get; set; }
    /// <summary>Per-layer stackup (empty when no live stackup) — the advisor needs
    /// the real plane/signal arrangement, not just a layer count.</summary>
    [JsonPropertyName("stackup")] public List<CsStackupLayer> Stackup { get; set; } = new();
}

public class CsStackupLayer
{
    [JsonPropertyName("index")] public int Index { get; set; }
    [JsonPropertyName("role")] public string Role { get; set; } = "signal";
    [JsonPropertyName("copper_t_mm")] public double CopperThicknessMm { get; set; }
    [JsonPropertyName("diel_h_mm")] public double DielectricHeightMm { get; set; }
    [JsonPropertyName("er")] public double Er { get; set; }
    [JsonPropertyName("df")] public double Df { get; set; }
    [JsonPropertyName("material")] public string Material { get; set; } = "";
    [JsonPropertyName("plane_net")] public string? PlaneNet { get; set; }
}

public class CsNetClass
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("clearance_mm")] public double ClearanceMm { get; set; }
    [JsonPropertyName("trace_width_mm")] public double TraceWidthMm { get; set; }
    [JsonPropertyName("via_dia_mm")] public double ViaDiaMm { get; set; }
    [JsonPropertyName("via_drill_mm")] public double ViaDrillMm { get; set; }
    [JsonPropertyName("max_skew_mm")] public double MaxSkewMm { get; set; }
    [JsonPropertyName("microvia")] public bool Microvia { get; set; }
}

public class CsComponent
{
    [JsonPropertyName("ref")] public string Ref { get; set; } = "";
    [JsonPropertyName("footprint")] public string Footprint { get; set; } = "";
    [JsonPropertyName("manufacturer")] public string Manufacturer { get; set; } = "";
    [JsonPropertyName("mpn")] public string Mpn { get; set; } = "";
    [JsonPropertyName("function")] public string Function { get; set; } = "";
    [JsonPropertyName("interfaces")] public List<string> Interfaces { get; set; } = new();
    [JsonPropertyName("x_mm")] public double XMm { get; set; }
    [JsonPropertyName("y_mm")] public double YMm { get; set; }
    [JsonPropertyName("rot_deg")] public double RotDeg { get; set; }
    [JsonPropertyName("side")] public string Side { get; set; } = "top";
    [JsonPropertyName("height_mm")] public double HeightMm { get; set; }
    [JsonPropertyName("pins")] public List<CsPin> Pins { get; set; } = new();
    /// <summary>Full `electrical` clause from the component's datasheet JSON —
    /// parameters, thermal, power domains, required externals, high speed.</summary>
    [JsonPropertyName("electrical")] public JsonElement? Electrical { get; set; }
}

public class CsPin
{
    [JsonPropertyName("pin")] public string Pin { get; set; } = "";
    [JsonPropertyName("net")] public string? Net { get; set; }
    [JsonPropertyName("electrical_type")] public string ElectricalType { get; set; } = "passive";
}

public class CsNet
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("class")] public string Class { get; set; } = "";
    [JsonPropertyName("members")] public List<string> Members { get; set; } = new();
    [JsonPropertyName("routed_length_mm")] public double RoutedLengthMm { get; set; }
    [JsonPropertyName("islands")] public int Islands { get; set; }
    [JsonPropertyName("fully_routed")] public bool FullyRouted { get; set; }
}

public class CsLoosePin
{
    [JsonPropertyName("ref")] public string Ref { get; set; } = "";
    [JsonPropertyName("pin")] public string Pin { get; set; } = "";
    [JsonPropertyName("electrical_type")] public string ElectricalType { get; set; } = "";
}

public class CsDrc
{
    [JsonPropertyName("violations")] public int Violations { get; set; }
    [JsonPropertyName("by_rule")] public Dictionary<string, int> ByRule { get; set; } = new();
    [JsonPropertyName("top_messages")] public List<string> TopMessages { get; set; } = new();
}

public class CsClassPhysics
{
    [JsonPropertyName("class")] public string Class { get; set; } = "";
    [JsonPropertyName("z0_ohm_at_width")] public double? Z0Ohm { get; set; }
    [JsonPropertyName("ipc2221_current_a_dt10")] public double? CurrentA { get; set; }
}

// ---- S2–S6 sign-off summaries (the analysis the advisor was blind to) -------

/// <summary>Link/channel result for one net (S4 + S6 over the S2/S3 channel).</summary>
public class CsChannel
{
    [JsonPropertyName("net")] public string Net { get; set; } = "";
    [JsonPropertyName("data_rate_gbps")] public double DataRateGbps { get; set; }
    [JsonPropertyName("insertion_loss_db")] public double InsertionLossDb { get; set; }
    [JsonPropertyName("eye_height_mv")] public double EyeHeightMv { get; set; }
    [JsonPropertyName("eye_width_ui")] public double EyeWidthUi { get; set; }
    [JsonPropertyName("ber")] public double Ber { get; set; }
    [JsonPropertyName("mask_pass")] public bool MaskPass { get; set; }
    [JsonPropertyName("has_via_stub")] public bool HasViaStub { get; set; }

    public static CsChannel From(string net, double dataRateGbps, LinkSimulator.Result r) => new()
    {
        Net = net, DataRateGbps = dataRateGbps,
        InsertionLossDb = Math.Round(r.InsertionLossDb, 2),
        EyeHeightMv = Math.Round(r.Eye.HeightV * 1e3, 1),
        EyeWidthUi = Math.Round(r.Eye.WidthUI, 3),
        Ber = r.Eye.Ber, MaskPass = r.MaskPass
    };
}

/// <summary>Spatial PDN result for one power net (S5/E6).</summary>
public class CsPdnRail
{
    [JsonPropertyName("net")] public string Net { get; set; } = "";
    [JsonPropertyName("target_mohm")] public double TargetMohm { get; set; }
    [JsonPropertyName("worst_z_mohm")] public double WorstZMohm { get; set; }
    [JsonPropertyName("worst_freq_mhz")] public double WorstFreqMhz { get; set; }
    [JsonPropertyName("over_target")] public bool OverTarget { get; set; }
    [JsonPropertyName("resonances_mhz")] public List<double> ResonancesMhz { get; set; } = new();
    [JsonPropertyName("decap_ranking")] public List<string> DecapRanking { get; set; } = new();
    [JsonPropertyName("placement_hint")] public string? PlacementHint { get; set; }

    public static CsPdnRail From(string net, PlaneCavity.Result r) => new()
    {
        Net = net,
        TargetMohm = Math.Round(r.TargetZOhm * 1e3, 1),
        WorstZMohm = Math.Round(r.WorstZOhm * 1e3, 1),
        WorstFreqMhz = Math.Round(r.WorstFreqHz / 1e6, 1),
        OverTarget = r.WorstZOhm > r.TargetZOhm,
        ResonancesMhz = r.ResonancesHz.Select(f => Math.Round(f / 1e6, 1)).ToList(),
        DecapRanking = r.DecapRanking
            .Select(d => $"{d.RefDes}: {d.WorstFreqContribOhm * 1e3:+0.0;-0.0} mΩ").ToList(),
        PlacementHint = r.Hint?.Reason
    };
}

/// <summary>DDR byte-lane timing result (S6/E7).</summary>
public class CsDdrLane
{
    [JsonPropertyName("lane")] public string Lane { get; set; } = "";
    [JsonPropertyName("data_rate_mtps")] public double DataRateMtps { get; set; }
    [JsonPropertyName("worst_setup_ps")] public double WorstSetupPs { get; set; }
    [JsonPropertyName("worst_hold_ps")] public double WorstHoldPs { get; set; }
    [JsonPropertyName("worst_bit")] public string WorstBit { get; set; } = "";
    [JsonPropertyName("pass")] public bool Pass { get; set; }

    public static CsDdrLane From(DdrTimingEngine.LaneReport r) => new()
    {
        Lane = r.LaneName, DataRateMtps = r.DataRateMtps,
        WorstSetupPs = Math.Round(r.WorstSetupPs, 1), WorstHoldPs = Math.Round(r.WorstHoldPs, 1),
        WorstBit = r.WorstBit, Pass = r.Pass
    };
}

/// <summary>
/// Optional sign-off results the caller has already computed (link sims, PDN,
/// DDR margins). Passed to CircuitStateExport.Build so the advisor sees them;
/// the board geometry/netlist export works with or without it.
/// </summary>
public class AnalysisResults
{
    public List<CsChannel> Channels { get; } = new();
    public List<CsPdnRail> Pdn { get; } = new();
    public List<CsDdrLane> Ddr { get; } = new();
}

public static class CircuitStateExport
{
    private static readonly JsonSerializerOptions Opts = new() { WriteIndented = true };

    public const string ResponseContract =
        "You are an electronics design advisor. Study this circuit state against the design_goal. " +
        "Check (1) component/netlist completeness: power budgets (every power_in pin needs a source able " +
        "to supply it), required external components from each part's electrical.required_externals, " +
        "interface completeness, unconnected_pins; and (2) signal/power integrity sign-off when present: " +
        "signal_integrity (eye height/width, mask_pass, has_via_stub — a closed eye or failed mask needs a fix: " +
        "back-drill a via stub (backdrill) when has_via_stub, else add equalisation (set_eq)), " +
        "power_integrity (worst_z_mohm vs target_mohm, resonances, decap_ranking, placement_hint — a rail " +
        "over target needs decaps added/moved; a negative decap_ranking entry is a decap making an " +
        "anti-resonance worse), ddr_lanes (worst_setup_ps/worst_hold_ps — a negative margin fails, see " +
        "worst_bit — a negative hold margin wants length_tune, a negative setup margin wants set_eq), and the board.stackup (reference planes, layer count — if board.copper_layers is too few for the parts, recommend set_layers with the count). " +
        "Respond with ONLY a JSON object: " +
        "{\"schema\":\"design-studio.advice/1\",\"summary\":\"...\",\"actions\":[{\"op\":\"add|remove|replace|" +
        "backdrill|move_decap|add_decap|set_eq|stackup|length_tune|reference|set_layers\",\"mpn\":\"...\",\"manufacturer\":\"...\"," +
        "\"for_ref\":\"<existing ref>\",\"net\":\"<net for SI/PI ops>\",\"reason\":\"...\"," +
        "\"connections\":[{\"pin\":\"...\",\"to_net\":\"...\"}]}],\"risks\":[\"...\"]} . " +
        "For every `add`, also attach (or offer to generate) a design-studio.component/2 JSON extracted " +
        "from that part's datasheet so it can be imported directly.";

    public static string Build(BoardDocument doc, string designGoal, FootprintLibrary? library = null)
        => Build(doc, designGoal, library, null);

    /// <summary>As Build, with optional pre-computed S2–S6 sign-off results so the
    /// advisor sees the channel eyes, PDN and DDR margins, not just the netlist.</summary>
    public static CircuitState BuildState(BoardDocument doc, string designGoal, FootprintLibrary? library,
                               AnalysisResults? results)
    {
        var state = new CircuitState
        {
            GeneratedUtc = DateTime.UtcNow.ToString("u"),
            DesignGoal = designGoal,
            AssistantInstructions = ResponseContract,
            Board = new CsBoard
            {
                WidthMm = doc.BoardWidthMm, HeightMm = doc.BoardHeightMm,
                CopperLayers = doc.CopperLayers,
                DielectricEr = doc.DielectricEr, DielectricHeightMm = doc.DielectricHeightMm,
                LossTangent = doc.DielectricLossTangent, CopperThicknessMm = doc.CopperThicknessMm
            }
        };

        // per-layer stackup — the real plane/signal arrangement, not just a count
        if (doc.Stackup.Count == doc.CopperLayers)
            for (int i = 0; i < doc.Stackup.Count; i++)
            {
                var l = doc.Stackup[i];
                state.Board.Stackup.Add(new CsStackupLayer
                {
                    Index = i, Role = l.Role == LayerRole.Plane ? "plane" : "signal",
                    CopperThicknessMm = l.CopperThicknessMm, DielectricHeightMm = l.DielectricHeightMm,
                    Er = Math.Round(l.ErAt(), 3), Df = Math.Round(l.TanAt(), 5), Material = l.MaterialId,
                    PlaneNet = l.PlaneNetId >= 0 ? doc.NetName(l.PlaneNetId) : null
                });
            }

        foreach (var c in doc.NetClasses)
            state.NetClasses.Add(new CsNetClass
            {
                Name = c.Name, ClearanceMm = c.ClearanceMm, TraceWidthMm = c.TraceWidthMm,
                ViaDiaMm = c.ViaDiameterMm, ViaDrillMm = c.ViaDrillMm,
                MaxSkewMm = c.MaxSkewMm, Microvia = c.AllowMicrovia
            });

        // components, enriched from the library's datasheet payloads
        foreach (var fp in doc.Footprints)
        {
            var def = library?.Items.FirstOrDefault(d => d.Name == fp.LibName);
            var comp = new CsComponent
            {
                Ref = fp.RefDes, Footprint = fp.LibName,
                Manufacturer = def?.Manufacturer ?? "", Mpn = def?.Mpn ?? "",
                Function = def?.Function ?? "", Interfaces = def?.Interfaces ?? new List<string>(),
                XMm = Math.Round(fp.X, 3), YMm = Math.Round(fp.Y, 3), RotDeg = fp.RotationDeg,
                Side = fp.Side == 0 ? "top" : "bottom",
                HeightMm = fp.Height3DMm > 0 ? fp.Height3DMm : (def?.HeightMm ?? 0)
            };
            if (!string.IsNullOrWhiteSpace(def?.ElectricalJson))
            {
                try { comp.Electrical = JsonSerializer.Deserialize<JsonElement>(def!.ElectricalJson); }
                catch (JsonException) { /* stale payload — omit rather than fail the export */ }
            }
            foreach (var pad in fp.Pads)
            {
                comp.Pins.Add(new CsPin
                {
                    Pin = pad.Name,
                    Net = pad.NetId >= 0 ? doc.NetName(pad.NetId) : null,
                    ElectricalType = pad.ElectricalType
                });
                if (pad.NetId < 0 && pad.ElectricalType != "nc")
                    state.UnconnectedPins.Add(new CsLoosePin
                    { Ref = fp.RefDes, Pin = pad.Name, ElectricalType = pad.ElectricalType });
            }
            state.Components.Add(comp);
        }

        // nets with membership + engine connectivity (native calls guarded so
        // the export still works without the core library present)
        foreach (var net in doc.Nets.OrderBy(n => n.Name, StringComparer.OrdinalIgnoreCase))
        {
            var cn = new CsNet { Name = net.Name, Class = doc.ClassFor(net.Id).Name };
            foreach (var fp in doc.Footprints)
                foreach (var pad in fp.Pads)
                    if (pad.NetId == net.Id)
                        cn.Members.Add($"{fp.RefDes}.{pad.Name}");
            try
            {
                cn.RoutedLengthMm = Math.Round(doc.NetLengthMm(net.Id), 3);
                cn.Islands = doc.NetIslands(net.Id);
                cn.FullyRouted = cn.Islands <= 1 && cn.Members.Count > 0;
            }
            catch (DllNotFoundException) { cn.Islands = -1; }
            state.Nets.Add(cn);
        }

        // DRC + ERC snapshot
        try
        {
            var violations = doc.RunDrcDetailed();
            violations.AddRange(ElectricalRules.Check(doc));
            state.Drc.Violations = violations.Count;
            state.Drc.ByRule = violations.GroupBy(v => v.RuleName)
                                         .ToDictionary(g => g.Key, g => g.Count());
            state.Drc.TopMessages = violations.Take(25).Select(v => v.ToString()).ToList();
            state.Erc = violations.Where(v => v.Rule >= 100).Select(v => v.Message).ToList();
        }
        catch (DllNotFoundException)
        {
            state.Drc.Violations = -1;
            state.Drc.TopMessages.Add("native engine unavailable at export time");
        }

        // physics snapshot of each class's routing rules on this stackup
        foreach (var c in doc.NetClasses)
        {
            var ph = new CsClassPhysics { Class = c.Name };
            try
            {
                if (NativeCore.dc_phys_microstrip(c.TraceWidthMm, doc.DielectricHeightMm,
                        doc.CopperThicknessMm, doc.DielectricEr,
                        out double z0, out _, out _) == NativeCore.Ok)
                    ph.Z0Ohm = Math.Round(z0, 1);
                double i = NativeCore.dc_phys_ipc2221_current(c.TraceWidthMm, doc.CopperThicknessMm, 10, 1);
                if (!double.IsNaN(i)) ph.CurrentA = Math.Round(i, 2);
            }
            catch (DllNotFoundException) { /* physics omitted without the core */ }
            state.Physics.Add(ph);
        }

        // S2–S6 sign-off results, when the caller has run them
        if (results != null)
        {
            state.SignalIntegrity.AddRange(results.Channels);
            state.PowerIntegrity.AddRange(results.Pdn);
            state.DdrLanes.AddRange(results.Ddr);
        }

        return state;
    }

    /// <summary>Serialised circuit state for the advisor (geometry + netlist + S2–S13 sign-off).</summary>
    public static string Build(BoardDocument doc, string designGoal, FootprintLibrary? library,
                               AnalysisResults? results)
        => JsonSerializer.Serialize(BuildState(doc, designGoal, library, results), Opts);
}
