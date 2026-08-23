using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class PowerFanoutTests
{
    // A part with two GND balls and one ball on a net that has no plane, plus a
    // GND plane on inner layer 1.
    private static (BoardDocument doc, int gnd) Scene()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        doc.SetCopperLayers(4);
        int gnd = doc.AddNet("GND");
        int sig = doc.AddNet("SIG");        // deliberately no plane
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", LibName = "BGA", X = 10, Y = 10, Side = 0,
            Pads = new List<PadItem>
            {
                new() { Name = "A1", NetId = gnd, X = 0.0, Y = 0.0, W = 0.3, H = 0.3 },
                new() { Name = "A2", NetId = gnd, X = 0.5, Y = 0.0, W = 0.3, H = 0.3 },
                new() { Name = "A3", NetId = sig, X = 1.0, Y = 0.0, W = 0.3, H = 0.3 }
            }
        });
        doc.AddPlane(gnd, layer: 1, clearanceMm: 0.2);
        doc.NotifyChanged();
        return (doc, gnd);
    }

    [Fact]
    public void DropsViaInPadForEveryPlanedBall()
    {
        var (doc, gnd) = Scene();

        var r = PowerFanout.Run(doc);

        Assert.Equal(2, r.ViasPlaced);                 // the two GND balls
        Assert.Equal(1, r.NetsFannedOut);
        Assert.Equal(2, doc.Vias.Count);
        foreach (var v in doc.Vias)
        {
            Assert.Equal(gnd, v.NetId);
            Assert.Equal(0, v.FromLayer);              // outer
            Assert.Equal(1, v.ToLayer);               // plane
            Assert.Equal(ViaType.Microvia, v.Type);   // adjacent-layer span
        }
        // vias landed on the GND ball world positions (fp at 10,10)
        Assert.Contains(doc.Vias, v => System.Math.Abs(v.X - 10.0) < 1e-6);
        Assert.Contains(doc.Vias, v => System.Math.Abs(v.X - 10.5) < 1e-6);
    }

    [Fact]
    public void SkipsNetsWithoutAPlane()
    {
        var (doc, _) = Scene();
        PowerFanout.Run(doc);
        // the SIG ball at x=11 never gets a via (no plane on that net)
        Assert.DoesNotContain(doc.Vias, v => System.Math.Abs(v.X - 11.0) < 1e-6);
    }

    [Fact]
    public void IsIdempotent()
    {
        var (doc, _) = Scene();
        PowerFanout.Run(doc);
        var second = PowerFanout.Run(doc);

        Assert.Equal(0, second.ViasPlaced);
        Assert.Equal(2, second.BallsAlreadyConnected);
        Assert.Equal(2, doc.Vias.Count);              // no duplicates
    }

    [Fact]
    public void ReportsWhenNoPlanesExist()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        var r = PowerFanout.Run(doc);
        Assert.Equal(0, r.ViasPlaced);
        Assert.Contains(r.Log, m => m.Contains("No copper planes"));
    }
}
