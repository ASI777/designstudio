using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class DatasheetImportTests
{
    // Mirrors docs/datasheet-extractor/example-ams1117-3v3.json (abridged).
    private const string ValidJson = """
    {
      "schema": "design-studio.component/1",
      "component": {
        "manufacturer": "Advanced Monolithic Systems", "mpn": "AMS1117-3.3",
        "description": "1A LDO, 3.3 V fixed", "category": "regulator",
        "datasheet": { "url": "http://example.com/ams1117.pdf" }
      },
      "symbol": {
        "ref_des_prefix": "U",
        "pins": [
          { "number": "1", "name": "GND",  "electrical_type": "power_in" },
          { "number": "2", "name": "VOUT", "electrical_type": "power_out" },
          { "number": "3", "name": "VIN",  "electrical_type": "power_in" },
          { "number": "TAB", "name": "VOUT", "electrical_type": "power_out" }
        ]
      },
      "electrical": {
        "required_externals": [
          { "purpose": "output capacitor", "value": "22uF", "constraints": "tantalum, low ESR" }
        ],
        "high_speed": {
          "diff_pairs": [ { "positive": "D+", "negative": "D-", "impedance_ohm": 90, "max_skew_mm": 0.15 } ],
          "single_ended": [ { "signal": "CLK", "impedance_ohm": 50 } ]
        }
      },
      "footprint": {
        "name": "SOT-223", "ipc_name": "SOT223P700X180-4N", "mount": "smd",
        "body": { "length_mm": 6.5, "width_mm": 3.5, "height_mm": 1.6 },
        "pitch_mm": 2.3,
        "pads": [
          { "number": "1",   "x_mm": -2.3, "y_mm": 3.05,  "width_mm": 1.2, "height_mm": 2.2, "shape": "rect" },
          { "number": "2",   "x_mm": 0.0,  "y_mm": 3.05,  "width_mm": 1.2, "height_mm": 2.2, "shape": "rect" },
          { "number": "3",   "x_mm": 2.3,  "y_mm": 3.05,  "width_mm": 1.2, "height_mm": 2.2, "shape": "rect" },
          { "number": "TAB", "x_mm": 0.0,  "y_mm": -3.05, "width_mm": 3.8, "height_mm": 2.2, "shape": "rect" }
        ]
      },
      "orientation": { "pin1_marker": "none", "pin1_position": "leftmost lead" },
      "extraction": { "warnings": ["illustrative"] }
    }
    """;

    [Fact]
    public void ValidFileImports()
    {
        var r = DatasheetImporter.Import(ValidJson);
        Assert.True(r.Ok, string.Join("; ", r.Log));
        Assert.NotNull(r.Footprint);
        var fp = r.Footprint!;
        Assert.Equal("SOT223P700X180-4N", fp.Name);
        Assert.Equal(4, fp.Pads.Count);
        Assert.Equal("datasheet-json", fp.Source);
        Assert.Equal("AMS1117-3.3", fp.Mpn);
        Assert.Equal("U", fp.RefDesPrefix);
    }

    [Fact]
    public void ElectricalTypesFlowOntoPads()
    {
        var r = DatasheetImporter.Import(ValidJson);
        var pads = r.Footprint!.ToPadItems();
        Assert.Equal("power_in", pads.First(p => p.Name == "1").ElectricalType);
        Assert.Equal("power_out", pads.First(p => p.Name == "2").ElectricalType);
        Assert.Equal("power_out", pads.First(p => p.Name == "TAB").ElectricalType);
    }

    [Fact]
    public void HighSpeedEntriesBecomeSuggestedClasses()
    {
        var r = DatasheetImporter.Import(ValidJson);
        Assert.Equal(2, r.SuggestedClasses.Count);
        var dp = r.SuggestedClasses.First(c => c.Differential);
        Assert.Equal(90, dp.ImpedanceOhm);
        Assert.Equal(0.15, dp.MaxSkewMm!.Value, 6);
        Assert.Contains(r.Log, l => l.Contains("90"));
    }

    [Fact]
    public void RequiredExternalsAppearInLog()
    {
        var r = DatasheetImporter.Import(ValidJson);
        Assert.Contains(r.Log, l => l.Contains("22uF") && l.Contains("low ESR"));
    }

    [Fact]
    public void RejectsOverlappingPads()
    {
        string bad = ValidJson.Replace("\"x_mm\": 2.3,", "\"x_mm\": -2.2,");   // pad 3 onto pad 1
        var r = DatasheetImporter.Import(bad);
        Assert.False(r.Ok);
        Assert.Contains(r.Log, l => l.Contains("overlap"));
    }

    [Fact]
    public void RejectsPadWithoutSymbolPin()
    {
        string bad = ValidJson.Replace("\"number\": \"TAB\", \"name\": \"VOUT\", \"electrical_type\": \"power_out\" }",
                                       "\"number\": \"TAB2\", \"name\": \"VOUT\", \"electrical_type\": \"power_out\" }");
        var r = DatasheetImporter.Import(bad);
        Assert.False(r.Ok);
        Assert.Contains(r.Log, l => l.Contains("no matching symbol pin"));
    }

    [Fact]
    public void RejectsWrongSchemaAndBadJson()
    {
        Assert.False(DatasheetImporter.Import(ValidJson.Replace("design-studio.component/1", "v999")).Ok);
        Assert.False(DatasheetImporter.Import("{ not json").Ok);
    }

    [Fact]
    public void RejectsImplausibleUnits()
    {
        // inches pasted as mm: 65 mm wide pad on a SOT-223
        string bad = ValidJson.Replace("\"width_mm\": 3.8", "\"width_mm\": 65");
        var r = DatasheetImporter.Import(bad);
        Assert.False(r.Ok);
        Assert.Contains(r.Log, l => l.Contains("millimetres"));
    }

    [Fact]
    public void RejectsDrillWithoutAnnularRing()
    {
        string bad = ValidJson.Replace("\"number\": \"1\",   \"x_mm\": -2.3, \"y_mm\": 3.05,  \"width_mm\": 1.2, \"height_mm\": 2.2, \"shape\": \"rect\"",
            "\"number\": \"1\",   \"x_mm\": -2.3, \"y_mm\": 3.05,  \"width_mm\": 1.2, \"height_mm\": 2.2, \"shape\": \"rect\", \"drill_mm\": 1.15");
        var r = DatasheetImporter.Import(bad);
        Assert.False(r.Ok);
        Assert.Contains(r.Log, l => l.Contains("annular"));
    }
}

public class ElectricalRulesTests
{
    private static FootprintItem Part(string refDes, double x, params (string name, string type, int net)[] pins)
    {
        var fp = new FootprintItem { RefDes = refDes, X = x, Y = 10 };
        double px = 0;
        foreach (var (name, type, net) in pins)
        {
            fp.Pads.Add(new PadItem { Name = name, X = px, W = 1, H = 1, NetId = net, ElectricalType = type });
            px += 2;
        }
        return fp;
    }

    [Fact]
    public void TwoOutputsOnOneNetConflict()
    {
        using var doc = new BoardDocument();
        int n = doc.AddNet("SIG");
        doc.Footprints.Add(Part("U1", 10, ("OUT", "output", n)));
        doc.Footprints.Add(Part("U2", 30, ("OUT", "output", n)));
        var r = ElectricalRules.Check(doc);
        Assert.Contains(r, v => v.Rule == ElectricalRules.RuleOutputConflict && v.Message.Contains("U1") && v.Message.Contains("U2"));
    }

    [Fact]
    public void OutputDrivingInputsIsFine()
    {
        using var doc = new BoardDocument();
        int n = doc.AddNet("SIG");
        doc.Footprints.Add(Part("U1", 10, ("OUT", "output", n)));
        doc.Footprints.Add(Part("U2", 30, ("IN", "input", n), ("IN2", "input", n)));
        Assert.DoesNotContain(ElectricalRules.Check(doc), v => v.Rule == ElectricalRules.RuleOutputConflict);
    }

    [Fact]
    public void ConnectedNcPinFlagged()
    {
        using var doc = new BoardDocument();
        int n = doc.AddNet("SIG");
        doc.Footprints.Add(Part("U1", 10, ("NC", "nc", n)));
        var r = ElectricalRules.Check(doc);
        Assert.Contains(r, v => v.Rule == ElectricalRules.RuleNcConnected);

        // unconnected NC is fine
        using var doc2 = new BoardDocument();
        doc2.Footprints.Add(Part("U1", 10, ("NC", "nc", -1)));
        Assert.Empty(ElectricalRules.Check(doc2));
    }

    [Fact]
    public void UnpoweredPowerInputFlagged_ButNamedRailsPass()
    {
        using var doc = new BoardDocument();
        int mystery = doc.AddNet("N$17");
        doc.Footprints.Add(Part("U1", 10, ("VDDX", "power_in", mystery)));
        Assert.Contains(ElectricalRules.Check(doc), v => v.Rule == ElectricalRules.RuleUnpoweredInput);

        using var doc2 = new BoardDocument();
        int rail = doc2.AddNet("3V3");
        doc2.Footprints.Add(Part("U1", 10, ("VDD", "power_in", rail)));
        Assert.Empty(ElectricalRules.Check(doc2));

        using var doc3 = new BoardDocument();
        int fed = doc3.AddNet("N$9");
        doc3.Footprints.Add(Part("U1", 10, ("VDD", "power_in", fed)));
        doc3.Footprints.Add(Part("U2", 30, ("VOUT", "power_out", fed)));
        Assert.Empty(ElectricalRules.Check(doc3));
    }
}
