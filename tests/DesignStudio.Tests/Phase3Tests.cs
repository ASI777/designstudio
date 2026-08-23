using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class FieldSolverTests
{
    [Fact]
    public void MicrostripAgreesWithClosedFormBallpark()
    {
        var fs = FieldSolver2D.Microstrip(0.3, 0.035, 0.2, 4.4);
        double ipc = ImpedanceEngine.MicrostripZ0(0.3, 0.035, 0.2, 4.4);
        Assert.InRange(fs.Z0, ipc * 0.75, ipc * 1.25);   // same ballpark, different method
        Assert.InRange(fs.EpsEff, 1.0, 4.4);              // field partly in air
    }

    [Fact]
    public void StriplineIsFullyEmbedded()
    {
        var sl = FieldSolver2D.Stripline(0.2, 0.035, 0.2, 4.4);
        Assert.InRange(sl.EpsEff, 3.9, 4.5);              // ~all field in dielectric
        var ms = FieldSolver2D.Microstrip(0.2, 0.035, 0.2, 4.4);
        Assert.True(sl.DelayPsPerMm > ms.DelayPsPerMm);   // stripline slower
    }

    [Fact]
    public void WiderTraceLowersImpedance()
    {
        double zNarrow = FieldSolver2D.Microstrip(0.15, 0.035, 0.2, 4.4).Z0;
        double zWide = FieldSolver2D.Microstrip(0.60, 0.035, 0.2, 4.4).Z0;
        Assert.True(zWide < zNarrow);
    }
}

public class IrDropTests
{
    private static BoardDocument PlaneBoard(out PlaneShape plane, out int vdd)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        doc.SetCopperLayers(2);
        vdd = doc.AddNet("VDD");
        var src = new FootprintItem { RefDes = "U1", X = 2, Y = 15 };
        src.Pads.Add(new PadItem { Name = "OUT", NetId = vdd, ElectricalType = "power_out", W = 1, H = 1 });
        var load = new FootprintItem { RefDes = "U2", X = 38, Y = 15 };
        load.Pads.Add(new PadItem { Name = "VIN", NetId = vdd, ElectricalType = "power_in", W = 1, H = 1 });
        doc.Footprints.Add(src);
        doc.Footprints.Add(load);
        doc.NotifyChanged();
        plane = doc.AddPlane(vdd, 0, 0.3);
        return doc;
    }

    [Fact]
    public void DropIsPositiveAndFinite()
    {
        using var doc = PlaneBoard(out var plane, out int vdd);
        var r = IrDrop.Analyze(doc, plane, new Dictionary<int, double> { [vdd] = 5.0 });
        Assert.True(r.Solved, r.Skipped);
        Assert.True(r.MaxDropV > 0, "current must produce a drop");
        Assert.True(r.MaxDropV < 1.0, $"drop {r.MaxDropV:F3} V implausibly large");
    }

    [Fact]
    public void MoreCurrentMoreDrop()
    {
        using var doc = PlaneBoard(out var plane, out int vdd);
        double d1 = IrDrop.Analyze(doc, plane, new() { [vdd] = 1.0 }).MaxDropV;
        double d5 = IrDrop.Analyze(doc, plane, new() { [vdd] = 5.0 }).MaxDropV;
        Assert.True(d5 > d1 * 3, $"expected ~linear scaling, got {d1:G3} → {d5:G3}");
    }

    [Fact]
    public void NoSourceMeansSkipped()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(20, 20);
        doc.SetCopperLayers(2);
        int gnd = doc.AddNet("GND");
        var plane = doc.AddPlane(gnd, 0, 0.3);
        var r = IrDrop.Analyze(doc, plane, new() { [gnd] = 2.0 });
        Assert.False(r.Solved);
    }
}

public class ChannelExtractorTests
{
    private static BoardDocument Channel(out int net)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 45, By = 20, Width = 0.3, NetId = net, Layer = 0 });
        return doc;
    }

    [Fact]
    public void PassiveAndNearLosslessAtLowFrequency()
    {
        using var doc = Channel(out int net);
        var data = ChannelExtractor.Extract(doc, net, 1e7, 2e10, 51);
        Assert.Equal(51, data.Count);
        var lo = data[0];
        Assert.True(lo.S21.Magnitude > 0.9, $"|S21|={lo.S21.Magnitude:F3} at 10 MHz");
        foreach (var p in data)
            Assert.True(p.S21.Magnitude <= 1.001, "passive network cannot have gain");
    }

    [Fact]
    public void LossGrowsWithFrequency()
    {
        using var doc = Channel(out int net);
        var data = ChannelExtractor.Extract(doc, net, 1e7, 2e10, 51);
        Assert.True(data[^1].S21.Magnitude < data[0].S21.Magnitude,
            "insertion loss must increase with frequency");
    }

    [Fact]
    public void ViaAddsDiscontinuity()
    {
        using var doc = Channel(out int net);
        double s11Clean = ChannelExtractor.Extract(doc, net, 1e9, 1e9 + 1, 2)[0].S11.Magnitude;
        doc.Vias.Add(new ViaItem { X = 25, Y = 20, NetId = net, FromLayer = 0, ToLayer = 3 });
        double s11Via = ChannelExtractor.Extract(doc, net, 1e9, 1e9 + 1, 2)[0].S11.Magnitude;
        Assert.True(s11Via > s11Clean, "via must raise reflection");
    }

    [Fact]
    public void TouchstoneFileIsWellFormed()
    {
        using var doc = Channel(out int net);
        var data = ChannelExtractor.Extract(doc, net, 1e7, 1e10, 21);
        string path = Path.Combine(Path.GetTempPath(), "ds_test.s2p");
        ChannelExtractor.WriteTouchstone(path, data, "SIG");
        string text = File.ReadAllText(path);
        Assert.Contains("# Hz S RI R 50", text);
        Assert.Equal(21, text.Split('\n').Count(l => l.Length > 0 && char.IsDigit(l[0])));
        File.Delete(path);
    }
}
