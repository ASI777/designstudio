using System.Text.Json;
using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class BgaGridTests
{
    [Theory]
    [InlineData(0, "A")]
    [InlineData(7, "H")]
    [InlineData(8, "J")]     // I skipped (JEDEC)
    [InlineData(13, "P")]    // O skipped
    [InlineData(19, "Y")]
    [InlineData(20, "AA")]   // double letters after Y
    [InlineData(21, "AB")]
    public void RowLettersFollowJedec(int index, string expected)
        => Assert.Equal(expected, BgaGrid.RowLabel(index));

    [Fact]
    public void ExpansionHonoursDepopulationAndGeometry()
    {
        var bga = new DsBga { Rows = 4, Cols = 4, PitchMm = 0.8, BallDiameterMm = 0.4,
                              Depopulated = new List<string> { "B2", "C3" } };
        var pins = new Dictionary<string, DsPin>();
        var pads = BgaGrid.Expand(bga, pins);
        Assert.Equal(14, pads.Count);                       // 16 − 2 depopulated
        Assert.DoesNotContain(pads, p => p.Name == "B2");
        var a1 = pads.First(p => p.Name == "A1");
        Assert.Equal(-1.2, a1.XMm, 6);                      // (0 − 1.5) · 0.8
        Assert.Equal(-1.2, a1.YMm, 6);
        Assert.Equal(0.32, a1.WMm, 6);                      // NSMD land = 0.8 · ball
        var d4 = pads.First(p => p.Name == "D4");
        Assert.Equal(1.2, d4.XMm, 6);
        Assert.Equal(1.2, d4.YMm, 6);
    }
}

public class ComponentV2Tests
{
    private const string BgaJson = """
    {
      "schema": "design-studio.component/2",
      "component": { "manufacturer": "Qualcomm", "mpn": "APQ-MINI",
        "description": "test SoC", "category": "mcu",
        "function": "application processor", "interfaces": ["USB2", "MIPI_DSI"] },
      "symbol": { "ref_des_prefix": "U", "pins": [
        { "number": "A1", "name": "DNC",      "electrical_type": "nc" },
        { "number": "A2", "name": "GPIO_0",   "electrical_type": "bidirectional" },
        { "number": "B1", "name": "VDD_CORE", "electrical_type": "power_in" },
        { "number": "B2", "name": "GND",      "electrical_type": "power_in" }
      ] },
      "electrical": {
        "parameters": [ { "name": "CIN", "min": null, "typ": 1.2, "max": null, "unit": "pF", "condition": "" } ],
        "thermal": { "theta_ja_c_w": 24.0, "max_power_w": 4.0 },
        "power_domains": [ { "name": "VDD_CORE", "vnom_v": 1.05, "max_current_a": 3.0, "pins": ["B1"] } ]
      },
      "footprint": {
        "name": "nFBGA-4", "mount": "smd",
        "body": { "length_mm": 23, "width_mm": 23, "height_mm": 1.0 },
        "bga": { "rows": 2, "cols": 2, "pitch_mm": 0.8, "ball_diameter_mm": 0.45 }
      },
      "package_3d": { "height_mm": 1.0, "standoff_mm": 0.25, "shape": "box" }
    }
    """;

    [Fact]
    public void BgaComponentExpandsAndImports()
    {
        var r = DatasheetImporter.Import(BgaJson);
        Assert.True(r.Ok, string.Join("; ", r.Log));
        var fp = r.Footprint!;
        Assert.Equal(4, fp.Pads.Count);
        Assert.Equal(1.0, fp.HeightMm, 6);
        Assert.Equal(0.25, fp.StandoffMm, 6);
        Assert.Equal("application processor", fp.Function);
        Assert.Contains("USB2", fp.Interfaces);
        Assert.Equal("nc", fp.Pads.First(p => p.Name == "A1").ElectricalType);
        Assert.Equal("power_in", fp.Pads.First(p => p.Name == "B1").ElectricalType);
        Assert.Contains("VDD_CORE", fp.ElectricalJson);     // full payload preserved
        Assert.Contains(r.Log, l => l.Contains("BGA grid expanded"));
        Assert.Contains(r.Log, l => l.Contains("power domain: VDD_CORE"));
    }

    [Fact]
    public void UnlistedBallIsRejected()
    {
        string bad = BgaJson.Replace("""
        { "number": "B2", "name": "GND",      "electrical_type": "power_in" }
        """.Trim(), """
        { "number": "B2X", "name": "GND",      "electrical_type": "power_in" }
        """.Trim());
        var r = DatasheetImporter.Import(bad);
        Assert.False(r.Ok);
        Assert.Contains(r.Log, l => l.Contains("no symbol pin") || l.Contains("not a valid ball"));
    }

    [Fact]
    public void V1FilesStillImport()
    {
        string v1 = BgaJson
            .Replace("design-studio.component/2", "design-studio.component/1")
            .Replace(""""bga": { "rows": 2, "cols": 2, "pitch_mm": 0.8, "ball_diameter_mm": 0.45 }"""",
                     """"
                     pads": [
                        { "number": "A1", "x_mm": -0.4, "y_mm": -0.4, "width_mm": 0.36, "height_mm": 0.36 },
                        { "number": "A2", "x_mm":  0.4, "y_mm": -0.4, "width_mm": 0.36, "height_mm": 0.36 },
                        { "number": "B1", "x_mm": -0.4, "y_mm":  0.4, "width_mm": 0.36, "height_mm": 0.36 },
                        { "number": "B2", "x_mm":  0.4, "y_mm":  0.4, "width_mm": 0.36, "height_mm": 0.36 } ]
                     """".Trim());
        var r = DatasheetImporter.Import(v1);
        Assert.True(r.Ok, string.Join("; ", r.Log));
        Assert.Equal(4, r.Footprint!.Pads.Count);
    }
}

public class CircuitStateExportTests
{
    [Fact]
    public void ExportCarriesEverythingTheAdvisorNeeds()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        int v5 = doc.AddNet("5V0");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", LibName = "APQ-MINI_nFBGA-4", X = 30, Y = 20, Height3DMm = 1.0,
            Pads = new List<PadItem>
            {
                new() { Name = "B1", NetId = v5, W = 0.4, H = 0.4, ElectricalType = "power_in" },
                new() { Name = "A2", NetId = -1, W = 0.4, H = 0.4, ElectricalType = "bidirectional" },
                new() { Name = "A1", NetId = -1, W = 0.4, H = 0.4, ElectricalType = "nc" }
            }
        });
        doc.NotifyChanged();

        var lib = new FootprintLibrary();
        lib.Items.Add(new FootprintDef
        {
            Name = "APQ-MINI_nFBGA-4", Mpn = "APQ-MINI", Manufacturer = "Qualcomm",
            Function = "application processor", Interfaces = new List<string> { "USB2" },
            ElectricalJson = """{"power_domains":[{"name":"VDD_CORE","vnom_v":1.05}]}"""
        });

        string json = CircuitStateExport.Build(doc, "battery-powered dev board", lib);
        using var state = JsonDocument.Parse(json);
        var root = state.RootElement;

        Assert.Equal("design-studio.circuit-state/1", root.GetProperty("schema").GetString());
        Assert.Equal("battery-powered dev board", root.GetProperty("design_goal").GetString());

        var comp = root.GetProperty("components")[0];
        Assert.Equal("APQ-MINI", comp.GetProperty("mpn").GetString());
        Assert.Equal(1.0, comp.GetProperty("height_mm").GetDouble(), 6);
        Assert.Equal("VDD_CORE",
            comp.GetProperty("electrical").GetProperty("power_domains")[0].GetProperty("name").GetString());

        var net = root.GetProperty("nets").EnumerateArray().First();
        Assert.Equal("5V0", net.GetProperty("name").GetString());
        Assert.Equal("U1.B1", net.GetProperty("members")[0].GetString());

        // the bidirectional pin is flagged; the NC pin is not
        var loose = root.GetProperty("unconnected_pins").EnumerateArray().ToList();
        Assert.Single(loose);
        Assert.Equal("A2", loose[0].GetProperty("pin").GetString());

        Assert.Contains("design-studio.advice/1",
            root.GetProperty("assistant_instructions").GetString());
    }

    [Fact]
    public void ExportCarriesStackupAndSignOffResults()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(6);                              // live stackup ⇒ planes at L1, L4
        doc.AddNet("DQ0");

        var results = new AnalysisResults();
        results.Channels.Add(new CsChannel
        {
            Net = "DQ0", DataRateGbps = 6.4, EyeHeightMv = 420, EyeWidthUi = 0.6,
            Ber = 1e-12, MaskPass = true
        });
        var cav = new PlaneCavity(20, 20, 0.1, 3.5, 0.005,
                                  new[] { new PlaneCavity.Port(10, 10, "L") },
                                  Array.Empty<PlaneCavity.Decap>());
        results.Pdn.Add(CsPdnRail.From("VDDQ", cav.Analyze(0.05, fStop: 5e8, points: 24)));
        results.Ddr.Add(new CsDdrLane
        {
            Lane = "byte0", DataRateMtps = 6400, WorstSetupPs = 15, WorstHoldPs = 20,
            WorstBit = "DQ3", Pass = true
        });

        string json = CircuitStateExport.Build(doc, "DDR5 server board", null, results);
        using var state = JsonDocument.Parse(json);
        var root = state.RootElement;

        // the real per-layer stackup is now exported (was just a layer count)
        var stack = root.GetProperty("board").GetProperty("stackup");
        Assert.Equal(6, stack.GetArrayLength());
        Assert.Contains(stack.EnumerateArray(), l => l.GetProperty("role").GetString() == "plane");

        // the S2–S6 sign-off the advisor used to be blind to
        Assert.Equal("DQ0", root.GetProperty("signal_integrity")[0].GetProperty("net").GetString());
        Assert.Equal("VDDQ", root.GetProperty("power_integrity")[0].GetProperty("net").GetString());
        Assert.Equal("DQ3", root.GetProperty("ddr_lanes")[0].GetProperty("worst_bit").GetString());

        // the advice contract now covers SI/PI actions
        string instr = root.GetProperty("assistant_instructions").GetString()!;
        Assert.Contains("ddr_lanes", instr);
        Assert.Contains("backdrill", instr);
    }
}
