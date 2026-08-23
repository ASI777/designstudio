using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S7 — layer-count scale + multi-plane stackup (E10) and reference-plane
// integrity / return path at scale (E12).
// Exit criterion: a 24-layer stackup where every controlled net has a
// continuous reference.
// ============================================================================

public class StackupScaleTests
{
    [Fact]
    public void LayerCountScalesPastSixteen()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(50);
        Assert.Equal(50, doc.CopperLayers);
        Assert.Equal(50, doc.Stackup.Count);
    }

    [Fact]
    public void LayerCountClampsAtSixtyFour()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(200);
        Assert.Equal(64, doc.CopperLayers);
    }

    [Fact]
    public void SmallStackupsKeepClassicTwoPlaneLayout()
    {
        // unchanged behaviour for the old ≤16 range (no regression)
        using var doc = new BoardDocument();
        doc.SetCopperLayers(6);
        Assert.Equal(LayerRole.Plane, doc.Stackup[1].Role);
        Assert.Equal(LayerRole.Plane, doc.Stackup[4].Role);
        Assert.Equal(LayerRole.Signal, doc.Stackup[2].Role);
    }

    [Fact]
    public void ThickStackupGivesEverySignalAnAdjacentReference()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(24);
        for (int i = 0; i < 24; i++)
        {
            if (doc.Stackup[i].Role != LayerRole.Signal) continue;
            var (above, below) = ImpedanceEngine.ReferencePlanes(doc, i);
            int da = above >= 0 ? i - above : 99;
            int db = below >= 0 ? below - i : 99;
            Assert.True(Math.Min(da, db) <= 1, $"signal L{i} has no reference plane within one layer");
        }
    }

    [Fact]
    public void ThickStackupPlanesAreTwoOunce()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(24);
        var planes = doc.Stackup.Where(l => l.Role == LayerRole.Plane).ToList();
        Assert.True(planes.Count >= 7, $"expected many planes, got {planes.Count}");
        Assert.All(planes, p => Assert.Equal(2.0, p.CopperWeightOz, 3));
    }

    [Fact]
    public void OuterLayersStaySignalOnThickStackup()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(32);
        Assert.Equal(LayerRole.Signal, doc.Stackup[0].Role);
        Assert.Equal(LayerRole.Signal, doc.Stackup[31].Role);
    }
}

public class ReferenceIntegrityTests
{
    private static BoardDocument Board(int layers, out int sig, out int gnd)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        doc.SetCopperLayers(layers);
        var cls = doc.AddNetClass("hs");
        cls.TargetImpedanceOhm = 50;                      // makes its nets "controlled"
        sig = doc.AddNet("SIG", cls.Id);
        gnd = doc.AddNet("GND");
        return doc;
    }

    [Fact]
    public void CleanTraceOverSolidPlanePasses()
    {
        using var doc = Board(4, out int sig, out int gnd);
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15, Bx = 35, By = 15, Width = 0.2, NetId = sig, Layer = 0 });
        doc.AddPlane(gnd, 1, 0.25);                       // solid reference plane under L0
        PlaneGenerator.RegenerateAll(doc);
        Assert.DoesNotContain(ReferenceIntegrity.Check(doc), h => h.Rule == 18);
        Assert.True(ReferenceIntegrity.Audit(doc).Single(a => a.Name == "SIG").Continuous);
    }

    [Fact]
    public void TraceCrossingAVoidIsFlagged()
    {
        using var doc = Board(4, out int sig, out int gnd);
        int other = doc.AddNet("OTHER");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15, Bx = 35, By = 15, Width = 0.2, NetId = sig, Layer = 0 });
        doc.AddPlane(gnd, 1, 0.25);
        // a fat foreign via punches a hole in the reference plane right under the trace
        doc.Vias.Add(new ViaItem { X = 20, Y = 15, NetId = other, FromLayer = 0, ToLayer = 3, DiameterMm = 4.0 });
        PlaneGenerator.RegenerateAll(doc);

        Assert.Contains(ReferenceIntegrity.Check(doc), h => h.Rule == 18);
        Assert.False(ReferenceIntegrity.Audit(doc).Single(a => a.Name == "SIG").Continuous);
    }

    [Fact]
    public void ReferenceChangeWithoutStitchIsFlagged()
    {
        using var doc = Board(24, out int sig, out int gnd);
        doc.Vias.Add(new ViaItem { X = 10, Y = 10, NetId = sig, FromLayer = 2, ToLayer = 8, DiameterMm = 0.5 });
        Assert.Contains(ReferenceIntegrity.Check(doc), h => h.Rule == 19);
    }

    [Fact]
    public void StitchingViaSatisfiesReferenceChange()
    {
        using var doc = Board(24, out int sig, out int gnd);
        doc.Vias.Add(new ViaItem { X = 10, Y = 10, NetId = sig, FromLayer = 2, ToLayer = 8, DiameterMm = 0.5 });
        doc.Vias.Add(new ViaItem { X = 10.4, Y = 10, NetId = gnd, FromLayer = 0, ToLayer = 23, DiameterMm = 0.4 });
        Assert.DoesNotContain(ReferenceIntegrity.Check(doc), h => h.Rule == 19);
    }

    [Fact]
    public void TwentyFourLayerControlledNetHasContinuousReference()
    {
        // the S7 exit criterion
        using var doc = Board(24, out int sig, out int gnd);
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15, Bx = 35, By = 15, Width = 0.13, NetId = sig, Layer = 2 });
        var audit = ReferenceIntegrity.Audit(doc);
        Assert.All(audit, a => Assert.True(a.Continuous, $"{a.Name}: {a.Detail}"));
        Assert.Contains(audit, a => a.Name == "SIG");
    }
}
