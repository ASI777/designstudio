using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class AmpacityTests
{
    [Fact]
    public void WiderTraceCarriesMoreCurrent()
    {
        double narrow = Ampacity.TraceCapacityA(0.2, 0.035, external: true);
        double wide = Ampacity.TraceCapacityA(2.0, 0.035, external: true);
        Assert.True(wide > narrow * 3);
    }

    [Fact]
    public void InternalLayersDerate()
    {
        Assert.True(Ampacity.TraceCapacityA(0.5, 0.035, external: false) <
                    Ampacity.TraceCapacityA(0.5, 0.035, external: true));
    }

    [Fact]
    public void IpcBallparkIsSane()
    {
        // classic reference point: ~0.5 mm / 1 oz external @ ΔT 10 °C ≈ 1–2 A
        double cap = Ampacity.TraceCapacityA(0.5, 0.035, external: true);
        Assert.InRange(cap, 0.8, 2.5);
    }

    [Fact]
    public void RequiredWidthRoundTrips()
    {
        double w = Ampacity.RequiredWidthMm(3.0, 0.035, external: true);
        Assert.Equal(3.0, Ampacity.TraceCapacityA(w, 0.035, external: true), 1);
    }

    [Fact]
    public void ViaCapacityScalesWithDrill()
    {
        Assert.True(Ampacity.ViaCapacityA(0.6) > Ampacity.ViaCapacityA(0.2));
    }
}

public class RuleResolverTests
{
    [Fact]
    public void PerNetOverrideBeatsClass()
    {
        using var doc = new BoardDocument();
        int n = doc.AddNet("X");
        doc.NetRules.Add(new NetRule { NetId = n, TraceWidthMm = 1.5 });
        Assert.Equal(1.5, RuleResolver.Width(doc, n), 6);
    }

    [Fact]
    public void ClassPairRuleRaisesClearance()
    {
        using var doc = new BoardDocument();
        var hv = doc.AddNetClass("HV");
        int a = doc.AddNet("MAINS", hv.Id);
        int b = doc.AddNet("LOGIC");
        double before = RuleResolver.Clearance(doc, a, b);
        doc.ClassPairRules.Add(new ClassPairRule { ClassA = hv.Id, ClassB = 0, ClearanceMm = 2.5 });
        Assert.Equal(2.5, RuleResolver.Clearance(doc, a, b), 6);
        Assert.True(RuleResolver.Clearance(doc, a, b) > before);
    }

    [Fact]
    public void PairClearanceViolationIsFlagged()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(50, 50);
        var hv = doc.AddNetClass("HV");
        int a = doc.AddNet("MAINS", hv.Id);
        int b = doc.AddNet("LOGIC");
        doc.ClassPairRules.Add(new ClassPairRule { ClassA = hv.Id, ClassB = 0, ClearanceMm = 2.0 });
        // two parallel traces 1 mm apart: fine per class (0.2), violates the pair rule (2.0)
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10, Bx = 30, By = 10, Width = 0.25, NetId = a, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 11, Bx = 30, By = 11, Width = 0.25, NetId = b, Layer = 0 });
        doc.NotifyChanged();
        Assert.Contains(RuleResolver.CheckPairClearances(doc), r => r.Rule == 19);
    }
}

public class BackdrillTests
{
    private static BoardDocument StubBoard(out ViaItem via)
    {
        var doc = new BoardDocument();
        doc.SetCopperLayers(6);
        var cls = doc.AddNetClass("hs");
        cls.TargetImpedanceOhm = 50;
        int sig = doc.AddNet("SIG", cls.Id);
        doc.Traces.Add(new TraceItem { Ax = 0, Ay = 0, Bx = 5, By = 0, NetId = sig, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 0, Bx = 10, By = 0, NetId = sig, Layer = 2 });
        via = new ViaItem { X = 5, Y = 0, NetId = sig, FromLayer = 0, ToLayer = 5, DrillMm = 0.3 };
        doc.Vias.Add(via);
        return doc;
    }

    [Fact]
    public void ApplyAllMarksStubVias()
    {
        using var doc = StubBoard(out var via);
        Assert.Equal(1, Backdrill.ApplyAll(doc));
        Assert.Equal(2, via.BackdrillToLayer);          // deepest used layer
        Assert.Equal(0, Backdrill.ApplyAll(doc));       // idempotent
    }

    [Fact]
    public void MarkedViaNoLongerFlagsRule15()
    {
        using var doc = StubBoard(out _);
        Assert.Contains(HighSpeedDrc.Run(doc), r => r.Rule == 15);
        Backdrill.ApplyAll(doc);
        Assert.DoesNotContain(HighSpeedDrc.Run(doc), r => r.Rule == 15);
    }

    [Fact]
    public void BackdrillFileHasOversizedBit()
    {
        using var doc = StubBoard(out var via);
        Backdrill.ApplyAll(doc);
        var files = DesignStudio.Export.FabExporter.BackdrillFiles(doc);
        Assert.Single(files);
        Assert.Contains((via.DrillMm + Backdrill.OversizeMm).ToString("F3",
            System.Globalization.CultureInfo.InvariantCulture), files[0].content);
    }
}

public class FabDocsTests
{
    [Fact]
    public void StackupDrawingListsMaterialsAndImpedance()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = "megtron6";
        var cls = doc.AddNetClass("pcie");
        cls.TargetDiffImpedanceOhm = 85;
        string s = DesignStudio.Export.FabExporter.StackupDrawing(doc);
        Assert.Contains("Megtron 6", s);
        Assert.Contains("85", s);
        Assert.Contains("L4", s);
    }

    [Fact]
    public void CouponsOnlyForControlledClasses()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        Assert.Equal("", DesignStudio.Export.FabExporter.CouponReadme(doc));
        var cls = doc.AddNetClass("usb");
        cls.TargetDiffImpedanceOhm = 90;
        cls.DiffPairGapMm = 0.15;
        string s = DesignStudio.Export.FabExporter.CouponReadme(doc);
        Assert.Contains("usb-DIFF", s);
        Assert.Contains("90", s);
    }
}
