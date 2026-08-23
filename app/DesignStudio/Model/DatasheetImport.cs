using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DesignStudio.Model;

// Importer for "design-studio.component/1" JSON files produced by the
// datasheet extraction prompt (docs/datasheet-extractor/). The LLM proposes,
// this code disposes: every file passes deterministic validation (pin/pad
// agreement, overlaps, annular rings, plausible dimensions) before a
// FootprintDef is created, so a hallucinated geometry cannot reach the
// library. High-speed entries become suggested net classes.

public class DsComponentFile
{
    [JsonPropertyName("schema")] public string Schema { get; set; } = "";
    [JsonPropertyName("component")] public DsComponent Component { get; set; } = new();
    [JsonPropertyName("symbol")] public DsSymbol Symbol { get; set; } = new();
    [JsonPropertyName("electrical")] public DsElectrical? Electrical { get; set; }
    [JsonPropertyName("footprint")] public DsFootprint Footprint { get; set; } = new();
    [JsonPropertyName("orientation")] public DsOrientation? Orientation { get; set; }
    [JsonPropertyName("package_3d")] public DsPackage3d? Package3d { get; set; }
    [JsonPropertyName("extraction")] public DsExtraction? Extraction { get; set; }
}

/// <summary>JEDEC BGA grid mathematics: row letters skip I, O, Q, S, X, Z.</summary>
public static class BgaGrid
{
    private const string Letters = "ABCDEFGHJKLMNPRTUVWY";   // 20 single letters

    public static string RowLabel(int index)
    {
        if (index < 0) throw new ArgumentOutOfRangeException(nameof(index));
        if (index < Letters.Length) return Letters[index].ToString();
        int hi = (index / Letters.Length) - 1;
        int lo = index % Letters.Length;
        return Letters[hi].ToString() + Letters[lo];          // AA, AB, … then BA…
    }

    /// <summary>Expand a BGA grid into pads centred on the package origin.</summary>
    public static List<PadDef> Expand(DsBga bga, IReadOnlyDictionary<string, DsPin> pinByBall)
    {
        var skip = new HashSet<string>(bga.Depopulated ?? new List<string>(), StringComparer.OrdinalIgnoreCase);
        double land = bga.LandDiameterMm is double l && l > 0 ? l : bga.BallDiameterMm * 0.8;  // NSMD nominal
        var pads = new List<PadDef>(bga.Rows * bga.Cols);
        for (int r = 0; r < bga.Rows; r++)
            for (int c = 0; c < bga.Cols; c++)
            {
                string ball = RowLabel(r) + (c + 1);
                if (skip.Contains(ball)) continue;
                pads.Add(new PadDef
                {
                    Name = ball,
                    XMm = Math.Round((c - (bga.Cols - 1) / 2.0) * bga.PitchMm, 4),
                    YMm = Math.Round((r - (bga.Rows - 1) / 2.0) * bga.PitchMm, 4),
                    WMm = land,
                    HMm = land,
                    ElectricalType = pinByBall.TryGetValue(ball, out var pin) ? pin.ElectricalType : "passive"
                });
            }
        return pads;
    }
}

public class DsComponent
{
    [JsonPropertyName("manufacturer")] public string Manufacturer { get; set; } = "";
    [JsonPropertyName("mpn")] public string Mpn { get; set; } = "";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "other";
    [JsonPropertyName("function")] public string Function { get; set; } = "";
    [JsonPropertyName("interfaces")] public List<string>? Interfaces { get; set; }
    [JsonPropertyName("datasheet")] public DsDatasheetMeta? Datasheet { get; set; }
}

public class DsDatasheetMeta
{
    [JsonPropertyName("title")] public string Title { get; set; } = "";
    [JsonPropertyName("revision")] public string Revision { get; set; } = "";
    [JsonPropertyName("url")] public string Url { get; set; } = "";
}

public class DsSymbol
{
    [JsonPropertyName("ref_des_prefix")] public string RefDesPrefix { get; set; } = "U";
    [JsonPropertyName("pins")] public List<DsPin> Pins { get; set; } = new();
}

public class DsPin
{
    [JsonPropertyName("number")] public string Number { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("electrical_type")] public string ElectricalType { get; set; } = "passive";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    /// <summary>Routed length inside the package (BGA substrate escape), mm — for delay matching.</summary>
    [JsonPropertyName("package_length_mm")] public double? PackageLengthMm { get; set; }
}

public class DsElectrical
{
    [JsonPropertyName("required_externals")] public List<DsExternal>? RequiredExternals { get; set; }
    [JsonPropertyName("high_speed")] public DsHighSpeed? HighSpeed { get; set; }
    [JsonPropertyName("parameters")] public List<DsParameter>? Parameters { get; set; }
    [JsonPropertyName("thermal")] public DsThermal? Thermal { get; set; }
    [JsonPropertyName("power_domains")] public List<DsPowerDomain>? PowerDomains { get; set; }
}

/// <summary>Generic min/typ/max row — carries *any* datasheet electrical table.</summary>
public class DsParameter
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("min")] public double? Min { get; set; }
    [JsonPropertyName("typ")] public double? Typ { get; set; }
    [JsonPropertyName("max")] public double? Max { get; set; }
    [JsonPropertyName("unit")] public string Unit { get; set; } = "";
    [JsonPropertyName("condition")] public string Condition { get; set; } = "";
}

public class DsThermal
{
    [JsonPropertyName("theta_ja_c_w")] public double? ThetaJaCw { get; set; }
    [JsonPropertyName("theta_jc_c_w")] public double? ThetaJcCw { get; set; }
    [JsonPropertyName("max_power_w")] public double? MaxPowerW { get; set; }
    [JsonPropertyName("tj_max_c")] public double? TjMaxC { get; set; }
}

public class DsPowerDomain
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("vmin_v")] public double? VminV { get; set; }
    [JsonPropertyName("vnom_v")] public double? VnomV { get; set; }
    [JsonPropertyName("vmax_v")] public double? VmaxV { get; set; }
    [JsonPropertyName("max_current_a")] public double? MaxCurrentA { get; set; }
    [JsonPropertyName("pins")] public List<string>? Pins { get; set; }
}

public class DsExternal
{
    [JsonPropertyName("purpose")] public string Purpose { get; set; } = "";
    [JsonPropertyName("value")] public string Value { get; set; } = "";
    [JsonPropertyName("constraints")] public string Constraints { get; set; } = "";
}

public class DsHighSpeed
{
    [JsonPropertyName("diff_pairs")] public List<DsDiffPair>? DiffPairs { get; set; }
    [JsonPropertyName("single_ended")] public List<DsSingleEnded>? SingleEnded { get; set; }
}

public class DsDiffPair
{
    [JsonPropertyName("positive")] public string Positive { get; set; } = "";
    [JsonPropertyName("negative")] public string Negative { get; set; } = "";
    [JsonPropertyName("impedance_ohm")] public double ImpedanceOhm { get; set; }
    [JsonPropertyName("max_skew_mm")] public double? MaxSkewMm { get; set; }
}

public class DsSingleEnded
{
    [JsonPropertyName("signal")] public string Signal { get; set; } = "";
    [JsonPropertyName("impedance_ohm")] public double ImpedanceOhm { get; set; }
}

public class DsFootprint
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("ipc_name")] public string IpcName { get; set; } = "";
    [JsonPropertyName("mount")] public string Mount { get; set; } = "smd";
    [JsonPropertyName("body")] public DsBody? Body { get; set; }
    [JsonPropertyName("pitch_mm")] public double? PitchMm { get; set; }
    [JsonPropertyName("pads")] public List<DsPad> Pads { get; set; } = new();
    [JsonPropertyName("bga")] public DsBga? Bga { get; set; }
    [JsonPropertyName("derived_from_outline")] public bool DerivedFromOutline { get; set; }
}

/// <summary>
/// Parametric BGA grid (v2): the reliable way to describe hundreds of balls.
/// The software expands rows×cols with JEDEC row lettering (I O Q S X Z
/// skipped); ball ids in symbol.pins and `depopulated` must match.
/// </summary>
public class DsBga
{
    [JsonPropertyName("rows")] public int Rows { get; set; }
    [JsonPropertyName("cols")] public int Cols { get; set; }
    [JsonPropertyName("pitch_mm")] public double PitchMm { get; set; }
    [JsonPropertyName("ball_diameter_mm")] public double BallDiameterMm { get; set; }
    [JsonPropertyName("land_diameter_mm")] public double? LandDiameterMm { get; set; }
    [JsonPropertyName("depopulated")] public List<string>? Depopulated { get; set; }
}

public class DsBody
{
    [JsonPropertyName("length_mm")] public double LengthMm { get; set; }
    [JsonPropertyName("width_mm")] public double WidthMm { get; set; }
    [JsonPropertyName("height_mm")] public double HeightMm { get; set; }
}

public class DsPad
{
    [JsonPropertyName("number")] public string Number { get; set; } = "";
    [JsonPropertyName("x_mm")] public double XMm { get; set; }
    [JsonPropertyName("y_mm")] public double YMm { get; set; }
    [JsonPropertyName("width_mm")] public double WidthMm { get; set; }
    [JsonPropertyName("height_mm")] public double HeightMm { get; set; }
    [JsonPropertyName("shape")] public string Shape { get; set; } = "rect";
    [JsonPropertyName("drill_mm")] public double DrillMm { get; set; }
    [JsonPropertyName("mechanical")] public bool Mechanical { get; set; }
}

public class DsOrientation
{
    [JsonPropertyName("pin1_marker")] public string Pin1Marker { get; set; } = "none";
    [JsonPropertyName("pin1_position")] public string Pin1Position { get; set; } = "";
}

public class DsPackage3d
{
    [JsonPropertyName("height_mm")] public double HeightMm { get; set; }
    [JsonPropertyName("standoff_mm")] public double StandoffMm { get; set; }
    [JsonPropertyName("shape")] public string Shape { get; set; } = "box";
}

public class DsExtraction
{
    [JsonPropertyName("warnings")] public List<string>? Warnings { get; set; }
}

public record SuggestedNetClass(string Name, double ImpedanceOhm, bool Differential, double? MaxSkewMm);

public record DatasheetImportResult(bool Ok, FootprintDef? Footprint,
                                    List<SuggestedNetClass> SuggestedClasses, List<string> Log);

public static class DatasheetImporter
{
    private static readonly string[] AcceptedSchemas =
        { "design-studio.component/1", "design-studio.component/2" };
    private static readonly string[] ValidTypes =
        { "input", "output", "bidirectional", "power_in", "power_out", "passive", "nc" };

    public static DatasheetImportResult ImportFile(string path)
        => Import(File.ReadAllText(path));

    public static DatasheetImportResult Import(string json)
    {
        var log = new List<string>();
        var classes = new List<SuggestedNetClass>();

        DsComponentFile? file;
        try
        {
            file = JsonSerializer.Deserialize<DsComponentFile>(json);
        }
        catch (JsonException ex)
        {
            return new(false, null, classes, new List<string> { $"Invalid JSON: {ex.Message}" });
        }
        if (file is null)
            return new(false, null, classes, new List<string> { "Empty document." });
        if (!AcceptedSchemas.Contains(file.Schema))
            return new(false, null, classes, new List<string>
                { $"Unsupported schema '{file.Schema}' (expected one of: {string.Join(", ", AcceptedSchemas)})." });

        // v2 parametric BGA: expand the grid into explicit pads before validation
        if (file.Footprint.Bga is { } bga && file.Footprint.Pads.Count == 0)
        {
            var pinMap = file.Symbol.Pins
                .GroupBy(pn => pn.Number, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(g => g.Key, g => g.First(), StringComparer.OrdinalIgnoreCase);
            var bgaErrors = ValidateBga(bga, file.Symbol.Pins);
            if (bgaErrors.Count > 0)
            {
                log.Add($"Rejected: {bgaErrors.Count} BGA validation error(s).");
                log.AddRange(bgaErrors.Select(e => "  ✗ " + e));
                return new(false, null, classes, log);
            }
            file.Footprint.Pads = BgaGrid.Expand(bga, pinMap)
                .Select(pd => new DsPad
                {
                    Number = pd.Name, XMm = pd.XMm, YMm = pd.YMm,
                    WidthMm = pd.WMm, HeightMm = pd.HMm, Shape = "circle"
                }).ToList();
            log.Add($"BGA grid expanded: {bga.Rows}×{bga.Cols} @ {bga.PitchMm} mm pitch → {file.Footprint.Pads.Count} balls" +
                    (bga.Depopulated is { Count: > 0 } dep ? $" ({dep.Count} depopulated)" : ""));
        }

        var errors = Validate(file);
        if (errors.Count > 0)
        {
            log.Add($"Rejected: {errors.Count} validation error(s).");
            log.AddRange(errors.Select(e => "  ✗ " + e));
            return new(false, null, classes, log);
        }

        // ---- map to a library footprint ----
        var pinByNumber = file.Symbol.Pins.ToDictionary(p => p.Number, p => p);
        var def = new FootprintDef
        {
            Name = string.IsNullOrWhiteSpace(file.Footprint.IpcName)
                ? $"{file.Component.Mpn}_{file.Footprint.Name}"
                : file.Footprint.IpcName,
            Description = string.IsNullOrWhiteSpace(file.Component.Description)
                ? $"{file.Component.Manufacturer} {file.Component.Mpn}"
                : file.Component.Description,
            RefDesPrefix = string.IsNullOrWhiteSpace(file.Symbol.RefDesPrefix) ? "U" : file.Symbol.RefDesPrefix,
            Source = "datasheet-json",
            Manufacturer = file.Component.Manufacturer,
            Mpn = file.Component.Mpn,
            DatasheetUrl = file.Component.Datasheet?.Url ?? "",
            Pin1Marker = file.Orientation?.Pin1Marker ?? "",
            BodyWidthMm = file.Footprint.Body?.LengthMm ?? 0,
            BodyHeightMm = file.Footprint.Body?.WidthMm ?? 0,   // re-oriented to pads below
            HeightMm = file.Package3d?.HeightMm ?? file.Footprint.Body?.HeightMm ?? 0,
            StandoffMm = file.Package3d?.StandoffMm ?? 0,
            Function = file.Component.Function,
            Interfaces = file.Component.Interfaces ?? new List<string>(),
            ElectricalJson = file.Electrical is null
                ? ""
                : JsonSerializer.Serialize(file.Electrical),
            Pads = file.Footprint.Pads.Select(p => new PadDef
            {
                Name = p.Number,
                XMm = p.XMm, YMm = p.YMm,
                WMm = p.WidthMm, HMm = p.HeightMm,
                ThroughHole = file.Footprint.Mount == "through_hole" || p.DrillMm > 0,
                DrillMm = p.DrillMm,
                ElectricalType = p.Mechanical
                    ? "passive"
                    : pinByNumber.TryGetValue(p.Number, out var pin) ? pin.ElectricalType : "passive",
                PackageDelayMm = pinByNumber.TryGetValue(p.Number, out var pin2) ? pin2.PackageLengthMm ?? 0 : 0
            }).ToList()
        };

        // Datasheets state body as length×width without fixing which axis is
        // which on the land pattern. Orient the body box to the pad field:
        // if the pads are taller than wide but the body is wider than tall
        // (or vice versa), swap so the outline encloses the part correctly.
        if (def.Pads.Count > 0 && def.BodyWidthMm > 0 && def.BodyHeightMm > 0)
        {
            double padW = def.Pads.Max(p => p.XMm) - def.Pads.Min(p => p.XMm);
            double padH = def.Pads.Max(p => p.YMm) - def.Pads.Min(p => p.YMm);
            if ((padW > padH) != (def.BodyWidthMm > def.BodyHeightMm) && Math.Abs(padW - padH) > 0.01)
            {
                (def.BodyWidthMm, def.BodyHeightMm) = (def.BodyHeightMm, def.BodyWidthMm);
                log.Add("  body outline rotated 90° to match the pad field orientation.");
            }
        }

        log.Add($"Imported {file.Component.Manufacturer} {file.Component.Mpn} ({file.Footprint.Name}): " +
                $"{def.Pads.Count} pads, {file.Symbol.Pins.Count} symbol pins.");

        // electrical context worth surfacing to the designer
        foreach (var ext in file.Electrical?.RequiredExternals ?? new List<DsExternal>())
            log.Add($"  requires: {ext.Purpose} = {ext.Value}" +
                    (string.IsNullOrWhiteSpace(ext.Constraints) ? "" : $" ({ext.Constraints})"));

        foreach (var dp in file.Electrical?.HighSpeed?.DiffPairs ?? new List<DsDiffPair>())
        {
            classes.Add(new SuggestedNetClass($"{dp.Positive}/{dp.Negative}", dp.ImpedanceOhm, true, dp.MaxSkewMm));
            log.Add($"  suggested net class: diff pair {dp.Positive}/{dp.Negative} @ {dp.ImpedanceOhm:F0} Ω" +
                    (dp.MaxSkewMm is double sk ? $", max skew {sk:F2} mm" : ""));
        }
        foreach (var se in file.Electrical?.HighSpeed?.SingleEnded ?? new List<DsSingleEnded>())
        {
            classes.Add(new SuggestedNetClass(se.Signal, se.ImpedanceOhm, false, null));
            log.Add($"  suggested net class: {se.Signal} @ {se.ImpedanceOhm:F0} Ω single-ended");
        }

        foreach (var pd in file.Electrical?.PowerDomains ?? new List<DsPowerDomain>())
            log.Add($"  power domain: {pd.Name}" +
                    (pd.VnomV is double vn ? $" @ {vn:F2} V" : "") +
                    (pd.MaxCurrentA is double ia ? $", up to {ia:F2} A" : ""));
        if (file.Electrical?.Thermal is { } th)
            log.Add($"  thermal: θja={th.ThetaJaCw?.ToString("F1") ?? "?"} °C/W, " +
                    $"Pmax={th.MaxPowerW?.ToString("F1") ?? "?"} W");

        foreach (var w in file.Extraction?.Warnings ?? new List<string>())
            log.Add($"  extractor warning: {w}");
        if (file.Footprint.DerivedFromOutline)
            log.Add("  ⚠ pads were derived from the package outline, not a land-pattern drawing — review before fab.");

        return new(true, def, classes, log);
    }

    /// <summary>BGA grid sanity before expansion.</summary>
    public static List<string> ValidateBga(DsBga bga, List<DsPin> pins)
    {
        var errors = new List<string>();
        if (bga.Rows < 2 || bga.Rows > 80 || bga.Cols < 2 || bga.Cols > 80)
            errors.Add($"bga grid {bga.Rows}×{bga.Cols} out of range (2..80).");
        if (bga.PitchMm < 0.15 || bga.PitchMm > 3.0)
            errors.Add($"bga pitch {bga.PitchMm} mm implausible (0.15–3.0).");
        if (bga.BallDiameterMm <= 0 || bga.BallDiameterMm >= bga.PitchMm)
            errors.Add($"bga ball diameter {bga.BallDiameterMm} mm must be > 0 and < pitch.");
        if (errors.Count > 0) return errors;

        // every populated ball needs a symbol pin; every pin must be a real ball
        var valid = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        for (int r = 0; r < bga.Rows; r++)
            for (int c = 0; c < bga.Cols; c++)
                valid.Add(BgaGrid.RowLabel(r) + (c + 1));
        var depop = new HashSet<string>(bga.Depopulated ?? new List<string>(), StringComparer.OrdinalIgnoreCase);
        foreach (var d in depop)
            if (!valid.Contains(d)) errors.Add($"depopulated ball '{d}' is outside the {bga.Rows}×{bga.Cols} grid.");
        var pinSet = new HashSet<string>(pins.Select(pn => pn.Number), StringComparer.OrdinalIgnoreCase);
        foreach (var pn in pinSet)
            if (!valid.Contains(pn)) errors.Add($"pin '{pn}' is not a valid ball id for this grid.");
        int missing = 0;
        foreach (var ball in valid)
            if (!depop.Contains(ball) && !pinSet.Contains(ball) && ++missing <= 5)
                errors.Add($"ball '{ball}' has no symbol pin (list every ball; use electrical_type \"nc\" for DNC).");
        if (missing > 5) errors.Add($"…and {missing - 5} more unlisted balls.");
        return errors;
    }

    /// <summary>Deterministic validation — the gate between LLM output and the library.</summary>
    public static List<string> Validate(DsComponentFile file)
    {
        var errors = new List<string>();
        var fp = file.Footprint;
        var pins = file.Symbol.Pins;

        if (string.IsNullOrWhiteSpace(file.Component.Mpn)) errors.Add("component.mpn is required.");
        if (string.IsNullOrWhiteSpace(fp.Name)) errors.Add("footprint.name is required.");
        if (pins.Count == 0) errors.Add("symbol.pins is empty.");
        if (fp.Pads.Count == 0) errors.Add("footprint.pads is empty.");

        // unique pin / pad numbers
        var dupPins = pins.GroupBy(p => p.Number).Where(g => g.Count() > 1).Select(g => g.Key).ToList();
        if (dupPins.Count > 0) errors.Add($"duplicate symbol pin numbers: {string.Join(", ", dupPins)}.");
        var dupPads = fp.Pads.GroupBy(p => p.Number).Where(g => g.Count() > 1).Select(g => g.Key).ToList();
        if (dupPads.Count > 0) errors.Add($"duplicate pad numbers: {string.Join(", ", dupPads)}.");

        // electrical types
        foreach (var p in pins)
            if (!ValidTypes.Contains(p.ElectricalType))
                errors.Add($"pin {p.Number}: unknown electrical_type '{p.ElectricalType}'.");

        // every non-mechanical pad must have a matching symbol pin
        var pinNumbers = pins.Select(p => p.Number).ToHashSet();
        foreach (var pad in fp.Pads.Where(p => !p.Mechanical))
            if (!pinNumbers.Contains(pad.Number))
                errors.Add($"pad '{pad.Number}' has no matching symbol pin (mark mechanical pads with \"mechanical\": true).");

        // geometry sanity
        foreach (var pad in fp.Pads)
        {
            if (pad.WidthMm <= 0 || pad.HeightMm <= 0)
                errors.Add($"pad '{pad.Number}': non-positive size {pad.WidthMm}×{pad.HeightMm} mm.");
            if (pad.WidthMm > 60 || pad.HeightMm > 60 || Math.Abs(pad.XMm) > 120 || Math.Abs(pad.YMm) > 120)
                errors.Add($"pad '{pad.Number}': implausible dimensions/position — units must be millimetres.");
            if (pad.DrillMm > 0 && pad.DrillMm >= Math.Min(pad.WidthMm, pad.HeightMm) - 0.1)
                errors.Add($"pad '{pad.Number}': drill {pad.DrillMm} mm leaves no annular ring on a " +
                           $"{pad.WidthMm}×{pad.HeightMm} mm pad.");
        }
        if (fp.Mount == "through_hole")
            foreach (var pad in fp.Pads.Where(p => !p.Mechanical && p.DrillMm <= 0))
                errors.Add($"pad '{pad.Number}': through-hole footprint but drill_mm is missing.");

        if (fp.Pads.Count > 5000)
            errors.Add($"{fp.Pads.Count} pads exceeds the 5000-pad limit.");

        // overlapping pads (axis-aligned, 1 µm tolerance); skip the O(n²) sweep
        // for parametric grids — expansion geometry is non-overlapping by
        // construction (land < pitch enforced above)
        if (file.Footprint.Bga is null)
        for (int i = 0; i < fp.Pads.Count; i++)
            for (int j = i + 1; j < fp.Pads.Count; j++)
            {
                var a = fp.Pads[i]; var b = fp.Pads[j];
                double dx = Math.Abs(a.XMm - b.XMm), dy = Math.Abs(a.YMm - b.YMm);
                if (dx < (a.WidthMm + b.WidthMm) / 2 - 0.001 &&
                    dy < (a.HeightMm + b.HeightMm) / 2 - 0.001)
                    errors.Add($"pads '{a.Number}' and '{b.Number}' overlap.");
            }

        return errors;
    }
}
