using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class ProjectIOTests
{
    private static BoardDocument SampleDoc()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(120, 90);
        doc.SetCopperLayers(4);
        doc.DielectricEr = 3.66;            // e.g. Rogers-class laminate
        doc.DielectricHeightMm = 0.101;
        doc.DielectricLossTangent = 0.004;
        doc.CopperThicknessMm = 0.018;      // half-oz
        var hs = doc.AddNetClass("HS");
        hs.ClearanceMm = 0.1;
        hs.MaxSkewMm = 0.5;
        hs.AllowMicrovia = true;
        int gnd = doc.AddNet("GND");
        int dp = doc.AddNet("D_P", hs.Id);
        doc.RemoveNet(doc.AddNet("TEMP"));            // leave a gap in the id space
        int dn = doc.AddNet("D_N", hs.Id);

        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", LibName = "QFN-16", X = 30, Y = 20, RotationDeg = 45, Side = 0,
            Pads = new List<PadItem>
            {
                new() { Name = "1", X = -1, W = 0.8, H = 0.3, NetId = dp },
                new() { Name = "2", X = 1, W = 0.8, H = 0.3, NetId = gnd, ThroughHole = true, DrillMm = 0.3 }
            }
        });
        doc.Traces.Add(new TraceItem { Ax = 1, Ay = 2, Bx = 3, By = 4, Width = 0.15, NetId = dp, Layer = 2 });
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 9, By = 0, Width = 0.3, NetId = gnd, Layer = 1, IsPour = true });
        doc.Vias.Add(new ViaItem { X = 5, Y = 5, DiameterMm = 0.45, DrillMm = 0.2, NetId = dn, FromLayer = 0, ToLayer = 1 });
        _ = dn;
        return doc;
    }

    [Fact]
    public void V2RoundTripPreservesEverything()
    {
        using var doc = SampleDoc();
        string json = ProjectIO.Serialize(doc);
        using var copy = ProjectIO.Deserialize(json);

        Assert.Equal(doc.BoardWidthMm, copy.BoardWidthMm);
        Assert.Equal(4, copy.CopperLayers);
        Assert.Equal(3.66, copy.DielectricEr, 6);
        Assert.Equal(0.101, copy.DielectricHeightMm, 6);
        Assert.Equal(0.004, copy.DielectricLossTangent, 6);
        Assert.Equal(0.018, copy.CopperThicknessMm, 6);
        Assert.Equal(doc.Nets.Count, copy.Nets.Count);
        foreach (var n in doc.Nets)
        {
            var match = copy.FindNet(n.Id);
            Assert.NotNull(match);
            Assert.Equal(n.Name, match!.Name);
            Assert.Equal(n.ClassId, match.ClassId);
        }
        var hs = copy.NetClasses.Single(c => c.Name == "HS");
        Assert.Equal(0.1, hs.ClearanceMm, 6);
        Assert.Equal(0.5, hs.MaxSkewMm, 6);
        Assert.True(hs.AllowMicrovia);

        Assert.Single(copy.Footprints);
        Assert.Equal(45, copy.Footprints[0].RotationDeg);
        Assert.Equal(2, copy.Footprints[0].Pads.Count);
        Assert.True(copy.Footprints[0].Pads[1].ThroughHole);

        Assert.Equal(2, copy.Traces.Count);
        Assert.Contains(copy.Traces, t => t.IsPour && t.Layer == 1);
        Assert.Contains(copy.Traces, t => !t.IsPour && t.Layer == 2);

        var via = Assert.Single(copy.Vias);
        Assert.Equal(0.45, via.DiameterMm, 6);
        Assert.Equal((0, 1), (via.FromLayer, via.ToLayer));
    }

    [Fact]
    public void NewNetAfterLoadDoesNotCollide()
    {
        using var doc = SampleDoc();
        using var copy = ProjectIO.Deserialize(ProjectIO.Serialize(doc));
        int before = copy.Nets.Count;
        int id = copy.AddNet("ANOTHER");
        Assert.Equal(before + 1, copy.Nets.Count);
        Assert.Equal(1, copy.Nets.Count(n => n.Id == id));   // unique id even with gaps
    }

    [Fact]
    public void V1LegacyFilesMigrate()
    {
        const string v1 = """
        {
          "version": 1,
          "board_width_mm": 100,
          "board_height_mm": 80,
          "grid_mm": 1.27,
          "nets": ["GND", "VCC"],
          "footprints": [
            { "ref": "R1", "lib": "R_0603", "x_mm": 10, "y_mm": 10, "rot_deg": 0,
              "pads": [ { "name": "1", "x_mm": -0.8, "y_mm": 0, "w_mm": 0.8, "h_mm": 1.0, "net": 0 },
                        { "name": "2", "x_mm":  0.8, "y_mm": 0, "w_mm": 0.8, "h_mm": 1.0, "net": 1 } ] }
          ],
          "traces": [
            { "ax_mm": 1, "ay_mm": 1, "bx_mm": 2, "by_mm": 1, "w_mm": 0.25, "net": 0, "layer": 1 }
          ]
        }
        """;
        using var doc = ProjectIO.Deserialize(v1);
        Assert.Equal(2, doc.CopperLayers);
        Assert.Equal("GND", doc.NetName(0));
        Assert.Equal("VCC", doc.NetName(1));
        Assert.Single(doc.Footprints);
        Assert.Single(doc.Traces);
        Assert.Equal(1, doc.Traces[0].Layer);
        Assert.NotEmpty(doc.NetClasses);                      // default class exists
    }
}
