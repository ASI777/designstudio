using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class UndoStackTests
{
    [Fact]
    public void UndoRedoRoundTrip()
    {
        int value = 0;
        var stack = new UndoStack();
        value = 1;
        stack.Push("set 1", undo: () => value = 0, redo: () => value = 1);
        Assert.True(stack.CanUndo);
        Assert.False(stack.CanRedo);

        stack.Undo();
        Assert.Equal(0, value);
        Assert.True(stack.CanRedo);

        stack.Redo();
        Assert.Equal(1, value);
        Assert.Equal("set 1", stack.NextUndoName);
    }

    [Fact]
    public void PushClearsRedo()
    {
        int value = 0;
        var stack = new UndoStack();
        stack.Push("a", () => value = 0, () => value = 1);
        stack.Undo();
        Assert.Equal(0, value);
        stack.Push("b", () => value = 0, () => value = 2);
        Assert.False(stack.CanRedo);
    }
}

public class NetTableTests
{
    [Fact]
    public void NetIdsAreStableAcrossRemoval()
    {
        using var doc = new BoardDocument();
        int gnd = doc.AddNet("GND");
        int vcc = doc.AddNet("VCC");
        int sig = doc.AddNet("SIG");
        Assert.NotEqual(gnd, vcc);

        doc.RemoveNet(vcc);
        Assert.Equal("GND", doc.NetName(gnd));
        Assert.Equal("SIG", doc.NetName(sig));        // id survives the deletion
        Assert.Null(doc.FindNet(vcc));

        int next = doc.AddNet("NEW");
        Assert.NotEqual(sig, next);                   // no id reuse collision with live nets
    }

    [Fact]
    public void RemoveNetDetachesCopper()
    {
        using var doc = new BoardDocument();
        int n = doc.AddNet("X");
        var t = doc.AddTraceDirect(0, 0, 5, 0, 0.25, n, 0);
        doc.RemoveNet(n);
        Assert.Equal(-1, t.NetId);
    }

    [Fact]
    public void ClassResolutionFallsBackToDefault()
    {
        using var doc = new BoardDocument();
        var ddr = doc.AddNetClass("DDR");
        ddr.MaxSkewMm = 0.5;
        int dq = doc.AddNet("DQ0", ddr.Id);
        int misc = doc.AddNet("MISC");
        Assert.Equal("DDR", doc.ClassFor(dq).Name);
        Assert.Equal("Default", doc.ClassFor(misc).Name);
        Assert.Equal("Default", doc.ClassFor(12345).Name);
    }

    [Fact]
    public void SetCopperLayersClampsExistingItems()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(6);
        var t = doc.AddTraceDirect(0, 0, 5, 0, 0.25, -1, 5);
        doc.Vias.Add(new ViaItem { FromLayer = 0, ToLayer = 5 });
        doc.SetCopperLayers(2);
        Assert.Equal(1, t.Layer);
        Assert.All(doc.Vias, v => Assert.True(v.ToLayer <= 1));
    }
}

public class FootprintGeometryTests
{
    [Fact]
    public void BoundsRotateWithThePart()
    {
        var fp = new FootprintItem
        {
            X = 10, Y = 10, RotationDeg = 90,
            Pads = new List<PadItem> { new() { X = 0, Y = 0, W = 10, H = 0.5 } }
        };
        var (minX, minY, maxX, maxY) = fp.Bounds();
        Assert.True(maxY - minY > maxX - minX, "rotated wide pad must become tall");
    }
}

public class PadSnapTests
{
    [Fact]
    public void SnapFindsRotatedPadCentreAndNet()
    {
        using var doc = new BoardDocument();
        int n = doc.AddNet("SIG");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", X = 10, Y = 10, RotationDeg = 90,
            Pads = new List<PadItem> { new() { Name = "1", X = 2, Y = 0, W = 1, H = 1, NetId = n } }
        });
        // pad local (2,0) rotated 90° → world (10, 12)
        var hit = doc.SnapToPad(10.1, 11.9);
        Assert.NotNull(hit);
        Assert.Equal(10.0, hit!.Value.X, 9);
        Assert.Equal(12.0, hit.Value.Y, 9);
        Assert.Equal(n, hit.Value.NetId);
    }

    [Fact]
    public void SnapMissesFarPoints_AndPrefersTopmost()
    {
        using var doc = new BoardDocument();
        int a = doc.AddNet("A");
        int b = doc.AddNet("B");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", X = 10, Y = 10,
            Pads = new List<PadItem> { new() { Name = "1", W = 2, H = 2, NetId = a } }
        });
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U2", X = 10, Y = 10,   // stacked on top (placed later)
            Pads = new List<PadItem> { new() { Name = "1", W = 2, H = 2, NetId = b } }
        });
        Assert.Null(doc.SnapToPad(20, 20));
        Assert.Equal(b, doc.SnapToPad(10, 10)!.Value.NetId);   // topmost wins
    }
}

public class RatsnestTests
{
    private static BoardDocument TwoPadDoc(out int net)
    {
        var doc = new BoardDocument();
        net = doc.AddNet("N1");
        int n = net;
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "R1", X = 10, Y = 10,
            Pads = new List<PadItem> { new() { NetId = n, W = 1, H = 1 } }
        });
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "R2", X = 30, Y = 10,
            Pads = new List<PadItem> { new() { NetId = n, W = 1, H = 1 } }
        });
        return doc;
    }

    [Fact]
    public void UnroutedPadsProduceOneLine()
    {
        using var doc = TwoPadDoc(out _);
        Assert.Single(Ratsnest.Compute(doc));
    }

    [Fact]
    public void ConnectingTraceRemovesTheLine()
    {
        using var doc = TwoPadDoc(out int net);
        doc.AddTraceDirect(10, 10, 30, 10, 0.25, net, 0);
        Assert.Empty(Ratsnest.Compute(doc));
    }

    [Fact]
    public void PourStrokesDoNotCountAsRouting()
    {
        using var doc = TwoPadDoc(out int net);
        doc.Traces.Add(new TraceItem { Ax = 10, Ay = 10, Bx = 30, By = 10, NetId = net, IsPour = true });
        Assert.Single(Ratsnest.Compute(doc));
    }
}

public class ParametricFootprintTests
{
    [Fact]
    public void QfnHasFourSidesOfPads()
    {
        var qfn = ParametricFootprints.Qfn("QFN-16", "t", 16, 3.0, 0.5);
        Assert.Equal(16, qfn.Pads.Count);
        Assert.Equal(16, qfn.Pads.Select(p => p.Name).Distinct().Count());
    }

    [Fact]
    public void PinHeaderFollowsIpc2221DrillRules()
    {
        var hdr = ParametricFootprints.PinHeader("H", "t", 4);
        Assert.All(hdr.Pads, p =>
        {
            Assert.True(p.ThroughHole);
            Assert.True(p.DrillMm >= 0.64 + 0.25 - 1e-9);          // lead + 0.25
            Assert.True(p.WMm >= p.DrillMm + 0.5 - 1e-9);          // annular ring
        });
    }
}
