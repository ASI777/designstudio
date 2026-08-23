using System.Text.Json;

namespace DesignStudio.Model.Advisor;

// ============================================================================
// E21 step 2 — the advice contract.
//
// The LLM advisor answers with a `design-studio.advice/1` JSON object. This
// parses and *validates* that response into the typed AdviceReport (rejecting
// malformed JSON, the wrong schema, or unknown ops) and serialises the
// deterministic SiPiAdvisor report into the identical shape — so the in-house
// recommender and a model response are interchangeable downstream, and a bad
// model reply fails loudly instead of silently dropping actions.
// ============================================================================

public sealed class AdviceFormatException : Exception
{
    public AdviceFormatException(string message) : base(message) { }
}

public static class AdviceContract
{
    private static readonly JsonSerializerOptions Opts = new() { WriteIndented = true };

    public static string OpToString(AdviceOp op) => op switch
    {
        AdviceOp.Add => "add",
        AdviceOp.Remove => "remove",
        AdviceOp.Replace => "replace",
        AdviceOp.Backdrill => "backdrill",
        AdviceOp.MoveDecap => "move_decap",
        AdviceOp.AddDecap => "add_decap",
        AdviceOp.SetEq => "set_eq",
        AdviceOp.Stackup => "stackup",
        AdviceOp.LengthTune => "length_tune",
        AdviceOp.Reference => "reference",
        AdviceOp.SetLayers => "set_layers",
        _ => "add"
    };

    public static AdviceOp ParseOp(string s) => s.Trim().ToLowerInvariant() switch
    {
        "add" => AdviceOp.Add,
        "remove" => AdviceOp.Remove,
        "replace" => AdviceOp.Replace,
        "backdrill" => AdviceOp.Backdrill,
        "move_decap" => AdviceOp.MoveDecap,
        "add_decap" => AdviceOp.AddDecap,
        "set_eq" => AdviceOp.SetEq,
        "stackup" => AdviceOp.Stackup,
        "length_tune" => AdviceOp.LengthTune,
        "reference" => AdviceOp.Reference,
        "set_layers" => AdviceOp.SetLayers,
        _ => throw new AdviceFormatException($"unknown advice op '{s}'")
    };

    public static string Serialize(AdviceReport report)
    {
        var payload = new
        {
            schema = AdviceReport.Schema,
            summary = report.Summary,
            actions = report.Actions.Select(a => new
            {
                op = OpToString(a.Op),
                mpn = a.Mpn,
                manufacturer = a.Manufacturer,
                for_ref = a.ForRef,
                net = a.Net,
                value = a.Value,
                reason = a.Reason,
                connections = a.Connections?.Select(c => new { pin = c.Pin, to_net = c.ToNet })
            }),
            risks = report.Risks
        };
        return JsonSerializer.Serialize(payload, Opts);
    }

    /// <summary>Parse and validate a model's advice JSON. Throws
    /// <see cref="AdviceFormatException"/> on malformed input.</summary>
    public static AdviceReport Parse(string json)
    {
        JsonDocument doc;
        try { doc = JsonDocument.Parse(json); }
        catch (JsonException e) { throw new AdviceFormatException("response is not valid JSON: " + e.Message); }

        using (doc)
        {
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                throw new AdviceFormatException("advice root must be a JSON object");

            string schema = Str(root, "schema") ?? "";
            if (schema != AdviceReport.Schema)
                throw new AdviceFormatException("schema must be " + AdviceReport.Schema + ", got " + schema);

            string summary = Str(root, "summary") ?? "";

            var actions = new List<AdviceAction>();
            if (root.TryGetProperty("actions", out var acts) && acts.ValueKind == JsonValueKind.Array)
            {
                foreach (var a in acts.EnumerateArray())
                {
                    string? opStr = Str(a, "op");
                    if (opStr == null) throw new AdviceFormatException("each action needs an \"op\"");

                    List<AdviceConnection>? conns = null;
                    if (a.TryGetProperty("connections", out var cs) && cs.ValueKind == JsonValueKind.Array)
                    {
                        conns = new List<AdviceConnection>();
                        foreach (var c in cs.EnumerateArray())
                        {
                            string? pin = Str(c, "pin");
                            string? toNet = Str(c, "to_net");
                            if (pin != null && toNet != null) conns.Add(new AdviceConnection(pin, toNet));
                        }
                        if (conns.Count == 0) conns = null;
                    }

                    actions.Add(new AdviceAction(
                        ParseOp(opStr),
                        Str(a, "reason") ?? "",
                        Net: Str(a, "net"),
                        ForRef: Str(a, "for_ref"),
                        Mpn: Str(a, "mpn"),
                        Manufacturer: Str(a, "manufacturer"),
                        Value: Str(a, "value"),
                        Connections: conns));
                }
            }

            var risks = new List<string>();
            if (root.TryGetProperty("risks", out var rk) && rk.ValueKind == JsonValueKind.Array)
                foreach (var x in rk.EnumerateArray())
                    if (x.ValueKind == JsonValueKind.String) risks.Add(x.GetString()!);

            return new AdviceReport(summary, actions, risks);
        }
    }

    private static string? Str(JsonElement e, string key)
        => e.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
}
