using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class ShoveRouterTests
{
    private static BoardDocument Board(out int netA, out int netB)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(50, 50);
        doc.SetCopperLayers(2);
        netA = doc.AddNet("A");
        netB = doc.AddNet("B");
        return doc;
    }

    [Fact]
    public void CleanPlacementDoesNotShove()
    {
        using var doc = Board(out int a, out _);
        var r = ShoveRouter.Place(doc, 5, 5, 30, 5, 0.25, a, 0);
        Assert.True(r.Placed);
        Assert.False(r.Shoved);
        Assert.Single(doc.Traces);
    }

    [Fact]
    public void CrossingBlockerGetsBumpedClear()
    {
        using var doc = Board(out int a, out int b);
        // existing horizontal trace of net B
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10, Bx = 45, By = 10, Width = 0.25, NetId = b, Layer = 0 });
        doc.NotifyChanged();
        // route net A right along its middle section (collision)
        var r = ShoveRouter.Place(doc, 10, 10.1, 40, 10.1, 0.25, a, 0);
        Assert.True(r.Placed, r.Message);
        Assert.True(r.Shoved);
        // blocker endpoints preserved (connectivity)
        var bTraces = doc.Traces.Where(t => t.NetId == b).ToList();
        Assert.True(bTraces.Count > 1, "blocker should be split into a bump");
        Assert.Contains(bTraces, t => (t.Ax == 5 && t.Ay == 10) || (t.Bx == 5 && t.By == 10));
        Assert.Contains(bTraces, t => (t.Ax == 45 && t.Ay == 10) || (t.Bx == 45 && t.By == 10));
        // and the bump must now clear the new trace
        var newT = doc.Traces.First(t => t.NetId == a);
        foreach (var t in bTraces)
            Assert.True(ShoveRouter.SegSegDistance(newT, t) >= 0.2 + 0.25 - 1e-6,
                "shoved copper still violates clearance");
    }

    [Fact]
    public void SameNetIsNeverShoved()
    {
        using var doc = Board(out int a, out _);
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10, Bx = 45, By = 10, Width = 0.25, NetId = a, Layer = 0 });
        doc.NotifyChanged();
        var r = ShoveRouter.Place(doc, 5, 10.1, 45, 10.1, 0.25, a, 0);
        Assert.True(r.Placed);
        Assert.False(r.Shoved);          // same net may touch
    }

    [Fact]
    public void UnresolvableShoveRevertsCleanly()
    {
        using var doc = Board(out int a, out int b);
        int c = doc.AddNet("C");
        // sandwich: B between the new route and a C trace right behind it
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10, Bx = 45, By = 10, Width = 0.25, NetId = b, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10.6, Bx = 45, By = 10.6, Width = 0.25, NetId = c, Layer = 0 });
        doc.NotifyChanged();
        int tracesBefore = doc.Traces.Count;
        var r = ShoveRouter.Place(doc, 5, 9.6, 45, 9.6, 0.25, a, 0);
        if (!r.Placed)
            Assert.Equal(tracesBefore, doc.Traces.Count);   // full revert, no half-shoves
    }
}

public class EscapePlannerTests
{
    private static FootprintItem Bga(int n, double pitch, int firstNet)
    {
        var fp = new FootprintItem { RefDes = "U1", X = 25, Y = 25 };
        for (int r = 0; r < n; r++)
            for (int c = 0; c < n; c++)
                fp.Pads.Add(new PadItem
                {
                    Name = $"{(char)('A' + r)}{c + 1}",
                    X = (c - (n - 1) / 2.0) * pitch,
                    Y = (r - (n - 1) / 2.0) * pitch,
                    W = pitch * 0.5, H = pitch * 0.5,
                    NetId = firstNet + r * n + c
                });
        return fp;
    }

    private static BoardDocument BgaBoard(int n, double pitch, out FootprintItem fp)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(50, 50);
        doc.SetCopperLayers(6);
        int first = -1;
        for (int i = 0; i < n * n; i++)
        {
            int id = doc.AddNet($"N{i}");
            if (first < 0) first = id;
        }
        fp = Bga(n, pitch, first);
        doc.Footprints.Add(fp);
        doc.NotifyChanged();
        return doc;
    }

    [Fact]
    public void EveryBallGetsCopper()
    {
        using var doc = BgaBoard(6, 0.8, out var fp);
        var r = EscapePlanner.Fanout(doc, fp);
        Assert.True(r.Ok, r.Message);
        // 6×6: rings 0–1 = perimeter 2 deep = 32 balls; ring 2 = 4 balls
        Assert.Equal(32, r.Stubs);
        Assert.Equal(4, r.DogBones);
        Assert.Equal(4, doc.Vias.Count);
    }

    [Fact]
    public void DogBoneViasClearNeighbouringBalls()
    {
        using var doc = BgaBoard(6, 0.8, out var fp);
        EscapePlanner.Fanout(doc, fp);
        foreach (var v in doc.Vias)
            foreach (var pad in fp.Pads)
            {
                var (px, py) = fp.PadWorld(pad);
                double d = Math.Sqrt((px - v.X) * (px - v.X) + (py - v.Y) * (py - v.Y));
                if (pad.NetId == v.NetId) continue;
                Assert.True(d > v.DiameterMm / 2 + pad.W / 2 - 1e-6,
                    $"via at ({v.X:F2},{v.Y:F2}) collides with ball {pad.Name}");
            }
    }

    [Fact]
    public void InnerEscapesAvoidPlaneLayers()
    {
        using var doc = BgaBoard(6, 0.8, out var fp);
        EscapePlanner.Fanout(doc, fp);
        var planeLayers = Enumerable.Range(0, doc.CopperLayers)
            .Where(l => doc.Stackup[l].Role == LayerRole.Plane).ToHashSet();
        foreach (var t in doc.Traces)
            Assert.DoesNotContain(t.Layer, planeLayers);
    }

    [Fact]
    public void RefusesPerimeterPackages()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        var qfn = new FootprintItem { RefDes = "U2" };
        for (int i = 0; i < 8; i++)
        {
            qfn.Pads.Add(new PadItem { Name = $"{i + 1}", X = -1.5, Y = i * 0.5, W = 0.3, H = 0.3, NetId = 0 });
            qfn.Pads.Add(new PadItem { Name = $"{i + 9}", X = 1.5, Y = i * 0.5, W = 0.3, H = 0.3, NetId = 0 });
        }
        Assert.False(EscapePlanner.Fanout(doc, qfn).Ok);
    }

    [Fact]
    public void FanoutIsUndoable()
    {
        using var doc = BgaBoard(6, 0.8, out var fp);
        EscapePlanner.Fanout(doc, fp);
        Assert.True(doc.Traces.Count > 0);
        doc.Undo.Undo();
        Assert.Empty(doc.Traces);
        Assert.Empty(doc.Vias);
    }
}
