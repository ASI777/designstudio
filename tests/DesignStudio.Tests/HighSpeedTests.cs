using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class SerpentineTunerTests
{
    [Fact]
    public void AddsRequestedLengthWithinTolerance()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        int n = doc.AddNet("DQ0");
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 40, By = 0, Width = 0.25, NetId = n, Layer = 0 });
        double before = doc.Traces.Where(t => t.NetId == n).Sum(t => t.LengthMm);

        var res = SerpentineTuner.AddLength(doc, n, 3.0);
        Assert.True(res.Ok, res.Message);
        double after = doc.Traces.Where(t => t.NetId == n).Sum(t => t.LengthMm);
        Assert.True(after - before >= 3.0 * 0.8, $"added only {after - before:F2} mm");
    }

    [Fact]
    public void RefusesWhenSegmentTooShort()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        int n = doc.AddNet("X");
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 0.5, By = 0, Width = 0.25, NetId = n, Layer = 0 });
        Assert.False(SerpentineTuner.AddLength(doc, n, 50).Ok);
    }

    [Fact]
    public void MeanderRespectsThreeWidthSpacing()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        int n = doc.AddNet("DQ1");
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 40, By = 0, Width = 0.25, NetId = n, Layer = 0 });
        var res = SerpentineTuner.AddLength(doc, n, 2.0);
        Assert.Contains("spacing 0.75", res.Message);   // 3 × 0.25 mm
    }
}

public class HighSpeedDrcTests
{
    private static BoardDocument ControlledBoard(out int sig, out int gnd)
    {
        var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        var cls = doc.AddNetClass("hs");
        cls.TargetImpedanceOhm = 50;
        sig = doc.AddNet("SIG", cls.Id);
        gnd = doc.AddNet("GND");
        return doc;
    }

    [Fact]
    public void LayerChangeWithoutReturnViaIsFlagged()
    {
        using var doc = ControlledBoard(out int sig, out _);
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 5, By = 0, NetId = sig, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 0, Bx = 10, By = 0, NetId = sig, Layer = 3 });
        doc.Vias.Add(new ViaItem { X = 5, Y = 0, NetId = sig, FromLayer = 0, ToLayer = 3 });
        Assert.Contains(HighSpeedDrc.Run(doc), r => r.Rule == 17);
    }

    [Fact]
    public void NearbyGroundViaSatisfiesReturnPath()
    {
        using var doc = ControlledBoard(out int sig, out int gnd);
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 5, By = 0, NetId = sig, Layer = 0 });
        doc.Vias.Add(new ViaItem { X = 5, Y = 0, NetId = sig, FromLayer = 0, ToLayer = 3 });
        doc.Vias.Add(new ViaItem { X = 5.5, Y = 0, NetId = gnd, FromLayer = 0, ToLayer = 3 });
        Assert.DoesNotContain(HighSpeedDrc.Run(doc), r => r.Rule == 17);
    }

    [Fact]
    public void UnusedViaBarrelSuggestsBackdrill()
    {
        using var doc = ControlledBoard(out int sig, out int gnd);
        // copper only on L1, but the via spans the whole 4-layer board
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 5, By = 0, NetId = sig, Layer = 0 });
        doc.Vias.Add(new ViaItem { X = 5, Y = 0, NetId = sig, FromLayer = 0, ToLayer = 3 });
        doc.Vias.Add(new ViaItem { X = 5.3, Y = 0, NetId = gnd, FromLayer = 0, ToLayer = 3 });
        Assert.Contains(HighSpeedDrc.Run(doc), r => r.Rule == 15 && r.Message.Contains("backdrill"));
    }
}

public class SiEstimatorTests
{
    [Fact]
    public void CleanShortNetHasWideEye()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        int n = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 20, By = 0, Width = 0.3, NetId = n, Layer = 0 });
        var rep = SiEstimator.Analyze(doc, n);
        Assert.True(rep.EyeOpeningPct > 70, $"eye {rep.EyeOpeningPct:F0} %");
        Assert.True(rep.TotalDelayPs > 0);
    }

    [Fact]
    public void WidthDiscontinuityRaisesReflection()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        int n = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 10, By = 0, Width = 0.15, NetId = n, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 10, Ay = 0, Bx = 20, By = 0, Width = 0.6, NetId = n, Layer = 0 });
        var rep = SiEstimator.Analyze(doc, n);
        Assert.True(rep.WorstReflection > 0.05, $"Γ={rep.WorstReflection:F3}");
    }
}
