using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class PowerOptimizerTests
{
    private static BoardDocument Scene()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        doc.SetCopperLayers(4);
        int gnd = doc.AddNet("GND");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", LibName = "BGA", X = 10, Y = 10, Side = 0,
            Pads = new List<PadItem>
            {
                new() { Name = "A1", NetId = gnd, X = 0.0, Y = 0.0, W = 0.3, H = 0.3 },
                new() { Name = "A2", NetId = gnd, X = 0.5, Y = 0.0, W = 0.3, H = 0.3 },
                new() { Name = "A3", NetId = gnd, X = 1.0, Y = 0.0, W = 0.3, H = 0.3 }
            }
        });
        doc.AddPlane(gnd, layer: 1, clearanceMm: 0.2);
        doc.NotifyChanged();
        return doc;
    }

    [Fact]
    public void AppliesDerivedHdiClearanceAndFansOut()
    {
        var doc = Scene();
        Assert.Equal(0.2, doc.NetClasses[0].ClearanceMm, 6);   // starts coarse

        var r = PowerOptimizer.Run(doc);   // auto-derives from the 0.5 mm-spaced 0.3 mm pads

        Assert.InRange(r.ClearanceMm, 0.05, 0.2);              // derived HDI value, tighter than 0.2
        Assert.Equal(r.ClearanceMm, r.FillStrokeMm, 6);       // stroke tracks clearance
        Assert.Equal(r.ClearanceMm, doc.NetClasses[0].ClearanceMm, 6);
        Assert.True(doc.NetClasses[0].ClearanceMm < 0.2);     // class actually tightened
        Assert.True(r.ViasPlaced >= 3);                        // a via under each GND ball
        Assert.True(r.PlanesPoured >= 1);
    }

    [Fact]
    public void DerivesTighterParamsForFinerPitch()
    {
        var coarse = PowerOptimizer.DeriveHdiParams(MakeBga(pitch: 1.0, pad: 0.5));
        var fine   = PowerOptimizer.DeriveHdiParams(MakeBga(pitch: 0.5, pad: 0.275));
        Assert.True(fine.ClearanceMm < coarse.ClearanceMm);    // finer pitch => tighter clearance
        Assert.True(fine.ViaDiameterMm < coarse.ViaDiameterMm);
    }

    [Fact]
    public void ReportsWhenNothingToPour()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        var r = PowerOptimizer.Run(doc);
        Assert.Equal(0, r.ViasPlaced);
        Assert.Contains(r.Log, m => m.Contains("No planes or pours"));
    }

    private static BoardDocument MakeBga(double pitch, double pad)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        var pads = new List<PadItem>();
        for (int i = 0; i < 3; i++)
            pads.Add(new PadItem { Name = $"A{i + 1}", X = i * pitch, Y = 0, W = pad, H = pad });
        doc.Footprints.Add(new FootprintItem { RefDes = "U1", LibName = "BGA", X = 10, Y = 10, Pads = pads });
        return doc;
    }
}
