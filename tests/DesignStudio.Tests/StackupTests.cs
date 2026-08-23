using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class ImpedanceEngineTests
{
    // 0.3 mm trace, 1 oz copper, 0.2 mm FR-4 (Er 4.4) → ~53 Ω microstrip
    [Fact]
    public void MicrostripMatchesIpc2141()
    {
        double z = ImpedanceEngine.MicrostripZ0(0.3, 0.035, 0.2, 4.4);
        Assert.InRange(z, 48, 58);
    }

    [Fact]
    public void StriplineIsLowerThanMicrostripForSameGeometry()
    {
        double ms = ImpedanceEngine.MicrostripZ0(0.2, 0.035, 0.2, 4.4);
        double sl = ImpedanceEngine.StriplineZ0(0.2, 0.035, 0.2, 4.4);
        Assert.True(sl < ms);   // fully embedded field → lower Z for same w/h
    }

    [Fact]
    public void WidthSolverRoundTrips()
    {
        double w = ImpedanceEngine.SolveWidth(50, microstrip: true, tMm: 0.035, hMm: 0.2, er: 4.4);
        Assert.False(double.IsNaN(w));
        Assert.Equal(50, ImpedanceEngine.MicrostripZ0(w, 0.035, 0.2, 4.4), 1);
    }

    [Fact]
    public void DiffPairSolverHitsTarget()
    {
        var (w, g) = ImpedanceEngine.SolveDiffPair(90, microstrip: true, tMm: 0.035, hMm: 0.15, er: 4.4);
        Assert.False(double.IsNaN(w));
        Assert.False(double.IsNaN(g));
        Assert.Equal(90, ImpedanceEngine.MicrostripZdiff(w, g, 0.035, 0.15, 4.4), 0);
    }

    [Fact]
    public void StriplineIsSlowerThanMicrostrip()
    {
        Assert.True(ImpedanceEngine.StriplineDelayPsPerMm(4.4) >
                    ImpedanceEngine.MicrostripDelayPsPerMm(4.4));
    }

    [Fact]
    public void StackupReferenceFindsNearestPlane()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(4);            // EnsureStackup → L2 GND, L3 PWR planes
        var (h, _, msTop) = ImpedanceEngine.Reference(doc, 0);
        Assert.True(msTop);                // outer layer over plane = microstrip
        Assert.Equal(doc.Stackup[0].DielectricHeightMm, h, 6);
        var (_, _, msBottom) = ImpedanceEngine.Reference(doc, 3);
        Assert.True(msBottom);
    }
}

public class DelayEngineTests
{
    [Fact]
    public void MatchGroupSpreadIsFlagged()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        int a = doc.AddNet("DQ0"); int b = doc.AddNet("DQ1");
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 10, By = 0, NetId = a, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 30, By = 0, NetId = b, Layer = 0 });
        doc.MatchGroups.Add(new MatchGroup { Name = "lane0", NetIds = { a, b }, TolerancePs = 10 });
        var hits = DelayEngine.CheckMatchGroups(doc);
        Assert.Contains(hits, h => h.Rule == 13 && h.Message.Contains("lane0"));
    }

    [Fact]
    public void MatchedNetsPass()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        int a = doc.AddNet("CLK_P"); int b = doc.AddNet("CLK_N");
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 20, By = 0, NetId = a, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 1, Bx = 20, By = 1, NetId = b, Layer = 0 });
        doc.MatchGroups.Add(new MatchGroup { Name = "clk", NetIds = { a, b }, TolerancePs = 10 });
        Assert.Empty(DelayEngine.CheckMatchGroups(doc));
    }

    [Fact]
    public void PinPackageDelayCountsTowardNetDelay()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        int n = doc.AddNet("DQ2");
        double bare = DelayEngine.NetDelayPs(doc, n);
        doc.Footprints.Add(new FootprintItem
        {
            Pads = { new PadItem { Name = "A1", NetId = n, PackageDelayMm = 5 } }
        });
        Assert.True(DelayEngine.NetDelayPs(doc, n) > bare);
    }

    [Fact]
    public void ViaAddsDelay()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        int n = doc.AddNet("SIG");
        doc.Vias.Add(new ViaItem { NetId = n, FromLayer = 0, ToLayer = 3 });
        Assert.True(DelayEngine.NetDelayPs(doc, n) > 0);
    }
}
