using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DesignStudio.Model;

/// <summary>A reusable library footprint definition (not yet placed on a board).</summary>
public class FootprintDef
{
    public string Name { get; set; } = "";            // "SOIC-8_3.9x4.9mm_P1.27"
    public string Description { get; set; } = "";
    public string RefDesPrefix { get; set; } = "U";    // U, R, C, Q, J...
    public List<PadDef> Pads { get; set; } = new();
    public double BodyWidthMm { get; set; }            // courtyard / silkscreen body
    public double BodyHeightMm { get; set; }
    public string Source { get; set; } = "manual";     // "manual" | "parametric" | "datasheet-json"
    public DateTime Created { get; set; } = DateTime.UtcNow;

    // Component metadata (filled by the datasheet JSON importer; optional).
    public string Manufacturer { get; set; } = "";
    public string Mpn { get; set; } = "";
    public string DatasheetUrl { get; set; } = "";
    public string Pin1Marker { get; set; } = "";
    public double HeightMm { get; set; }          // 3D package height
    public double StandoffMm { get; set; }
    public string Function { get; set; } = "";    // e.g. "application processor"
    public List<string> Interfaces { get; set; } = new();
    /// <summary>Raw `electrical` clause from the datasheet JSON — full fidelity
    /// for the circuit-state export that the AI advisor consumes.</summary>
    public string ElectricalJson { get; set; } = "";

    public List<PadItem> ToPadItems() => Pads.Select(p => new PadItem
    { X = p.XMm, Y = p.YMm, W = p.WMm, H = p.HMm, Name = p.Name,
      ThroughHole = p.ThroughHole, DrillMm = p.DrillMm, PackageDelayMm = p.PackageDelayMm,
      ElectricalType = string.IsNullOrEmpty(p.ElectricalType) ? "passive" : p.ElectricalType }).ToList();
}

public class PadDef
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("x_mm")] public double XMm { get; set; }
    [JsonPropertyName("y_mm")] public double YMm { get; set; }
    [JsonPropertyName("w_mm")] public double WMm { get; set; }
    [JsonPropertyName("h_mm")] public double HMm { get; set; }
    [JsonPropertyName("through_hole")] public bool ThroughHole { get; set; }
    [JsonPropertyName("drill_mm")] public double DrillMm { get; set; }
    [JsonPropertyName("electrical")] public string ElectricalType { get; set; } = "passive";
    [JsonPropertyName("pkg_delay_mm")] public double PackageDelayMm { get; set; }
}

/// <summary>
/// JSON-file-backed footprint library stored in %APPDATA%\DesignStudio\footprints\.
/// One file per footprint so the library is git-friendly and hand-editable.
/// </summary>
public class FootprintLibrary
{
    public static string LibraryDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "DesignStudio", "footprints");

    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = true };

    public List<FootprintDef> Items { get; } = new();

    public static FootprintLibrary Load()
    {
        var lib = new FootprintLibrary();
        Directory.CreateDirectory(LibraryDir);
        foreach (var file in Directory.GetFiles(LibraryDir, "*.json"))
        {
            try
            {
                var def = JsonSerializer.Deserialize<FootprintDef>(File.ReadAllText(file));
                if (def != null) lib.Items.Add(def);
            }
            catch { /* skip corrupt files */ }
        }
        if (lib.Items.Count == 0) lib.SeedDefaults();
        lib.Items.Sort((a, b) => string.Compare(a.Name, b.Name, StringComparison.OrdinalIgnoreCase));
        return lib;
    }

    public void Save(FootprintDef def)
    {
        Directory.CreateDirectory(LibraryDir);
        var safe = string.Concat(def.Name.Select(c => char.IsLetterOrDigit(c) || c is '-' or '_' or '.' ? c : '_'));
        File.WriteAllText(Path.Combine(LibraryDir, safe + ".json"), JsonSerializer.Serialize(def, JsonOpts));
        Items.RemoveAll(f => f.Name == def.Name);
        Items.Add(def);
        Items.Sort((a, b) => string.Compare(a.Name, b.Name, StringComparison.OrdinalIgnoreCase));
    }

    public void Delete(FootprintDef def)
    {
        var safe = string.Concat(def.Name.Select(c => char.IsLetterOrDigit(c) || c is '-' or '_' or '.' ? c : '_'));
        var path = Path.Combine(LibraryDir, safe + ".json");
        if (File.Exists(path)) File.Delete(path);
        Items.Remove(def);
    }

    private void SeedDefaults()
    {
        Save(new FootprintDef
        {
            Name = "R_0603", Description = "Resistor 0603 (1608 metric)", RefDesPrefix = "R",
            BodyWidthMm = 1.6, BodyHeightMm = 0.8, Pads = TwoPad(1.6, 0.8)
        });
        Save(new FootprintDef
        {
            Name = "C_0603", Description = "Capacitor 0603", RefDesPrefix = "C",
            BodyWidthMm = 1.6, BodyHeightMm = 0.8, Pads = TwoPad(1.6, 0.8)
        });
        Save(new FootprintDef
        {
            Name = "SOIC-8", Description = "SOIC-8, 1.27mm pitch", RefDesPrefix = "U",
            BodyWidthMm = 3.9, BodyHeightMm = 4.9, Pads = Soic(8)
        });
    }

    private static List<PadDef> TwoPad(double l, double w) => new()
    {
        new PadDef { Name = "1", XMm = -l / 2, WMm = 0.8, HMm = w + 0.2 },
        new PadDef { Name = "2", XMm =  l / 2, WMm = 0.8, HMm = w + 0.2 },
    };

    private static List<PadDef> Soic(int pins)
    {
        var pads = new List<PadDef>();
        int perSide = pins / 2;
        for (int i = 0; i < perSide; i++)
        {
            double y = (i - (perSide - 1) / 2.0) * 1.27;
            pads.Add(new PadDef { Name = (i + 1).ToString(), XMm = -2.7, YMm = y, WMm = 1.5, HMm = 0.6 });
            pads.Add(new PadDef { Name = (pins - i).ToString(), XMm = 2.7, YMm = -y, WMm = 1.5, HMm = 0.6 });
        }
        return pads;
    }
}
