using DesignStudio.Interop;
using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

/// <summary>
/// End-to-end tests across the P/Invoke boundary: the same designcore library
/// the app ships with, driven through BoardDocument. Requires the native core
/// to be built (CMake) and copied next to the test binaries — CI does this;
/// locally the tests no-op when the library is absent.
/// </summary>
public class NativeIntegrationTests
{
    private static readonly bool NativeOk = ProbeNative();

    private static bool ProbeNative()
    {
        try
        {
            var h = NativeCore.dc_board_create();
            NativeCore.dc_board_destroy(h);
            return true;
        }
        catch (DllNotFoundException) { return false; }
        catch (EntryPointNotFoundException) { return false; }
    }

    [Fact]
    public void AutoRouteInsertsViasAroundAWall()
    {
        if (!NativeOk) return;
        using var doc = new BoardDocument();
        doc.SetBoardSize(50, 50);
        doc.SetCopperLayers(2);
        int wall = doc.AddNet("WALL");
        int sig = doc.AddNet("SIG");
        // wall across layer 0 only
        doc.AddTraceDirect(25, 0, 25, 50, 2.0, wall, 0);

        bool ok = doc.AutoRoute(5, 25, 45, 25, sig, 0, 0);
        Assert.True(ok, doc.LastRouteError);
        Assert.True(doc.Vias.Count >= 2, "route should drop to L2 and come back");
        Assert.Contains(doc.Traces, t => t.NetId == sig && t.Layer == 1);
    }

    [Fact]
    public void RouteFailureCarriesAReason()
    {
        if (!NativeOk) return;
        using var doc = new BoardDocument();
        doc.SetBoardSize(50, 50);
        int wall = doc.AddNet("WALL");
        int sig = doc.AddNet("SIG");
        // both layers blocked
        doc.AddTraceDirect(25, 0, 25, 50, 2.0, wall, 0);
        doc.AddTraceDirect(25, 0, 25, 50, 2.0, wall, 1);

        bool ok = doc.AutoRoute(5, 25, 45, 25, sig, 0, 0);
        Assert.False(ok);
        Assert.False(string.IsNullOrWhiteSpace(doc.LastRouteError));
    }

    [Fact]
    public void DrcReportsRuleAndMessage()
    {
        if (!NativeOk) return;
        using var doc = new BoardDocument();
        int a = doc.AddNet("A");
        int b = doc.AddNet("B");
        doc.AddTraceDirect(10, 10, 20, 10, 0.25, a, 0);
        doc.AddTraceDirect(10, 10.1, 20, 10.1, 0.25, b, 0);

        var violations = doc.RunDrcDetailed();
        var clash = violations.FirstOrDefault(v => v.Rule == 1);
        Assert.NotNull(clash);
        Assert.Contains("gap", clash!.Message);
        Assert.Equal("Trace-trace clearance", clash.RuleName);
    }

    [Fact]
    public void PourFillsAndUndoes()
    {
        if (!NativeOk) return;
        using var doc = new BoardDocument();
        doc.SetBoardSize(30, 30);
        int gnd = doc.AddNet("GND");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "C1", X = 15, Y = 15,
            Pads = new List<PadItem> { new() { NetId = gnd, W = 2, H = 2 } }
        });
        doc.NotifyChanged();

        int strokes = doc.GeneratePour(gnd, 0);
        Assert.True(strokes > 10, $"expected a real fill, got {strokes}");
        Assert.Equal(strokes, doc.Traces.Count(t => t.IsPour));

        doc.Undo.Undo();
        Assert.Equal(0, doc.Traces.Count(t => t.IsPour));
        doc.Undo.Redo();
        Assert.Equal(strokes, doc.Traces.Count(t => t.IsPour));
    }

    [Fact]
    public void NetLengthAndIslandsComeFromTheEngine()
    {
        if (!NativeOk) return;
        using var doc = new BoardDocument();
        int n = doc.AddNet("N");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "A", X = 10, Y = 10,
            Pads = new List<PadItem> { new() { NetId = n, W = 1, H = 1 } }
        });
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "B", X = 20, Y = 10,
            Pads = new List<PadItem> { new() { NetId = n, W = 1, H = 1 } }
        });
        doc.NotifyChanged();
        Assert.Equal(2, doc.NetIslands(n));

        doc.AddTraceDirect(10, 10, 20, 10, 0.25, n, 0);
        Assert.Equal(1, doc.NetIslands(n));
        Assert.Equal(10.0, doc.NetLengthMm(n), 3);
    }

    [Fact]
    public void AutoRouteConnectsOffGridPinsExactly()
    {
        if (!NativeOk) return;
        using var doc = new BoardDocument();
        doc.SetBoardSize(50, 50);
        int n = doc.AddNet("SIG");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "A", X = 10.05, Y = 10.05,   // deliberately off the 0.1 mm routing grid
            Pads = new List<PadItem> { new() { Name = "1", W = 1, H = 1, NetId = n } }
        });
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "B", X = 40.07, Y = 25.03,
            Pads = new List<PadItem> { new() { Name = "1", W = 1, H = 1, NetId = n } }
        });
        doc.NotifyChanged();
        Assert.Equal(2, doc.NetIslands(n));

        // route pad-centre to pad-centre with the pad's net (what the canvas now does)
        Assert.True(doc.AutoRoute(10.05, 10.05, 40.07, 25.03, n, 0, 0), doc.LastRouteError);

        // trace endpoints land exactly on the pin centres…
        Assert.Contains(doc.Traces, t =>
            (Math.Abs(t.Ax - 10.05) < 1e-6 && Math.Abs(t.Ay - 10.05) < 1e-6) ||
            (Math.Abs(t.Bx - 10.05) < 1e-6 && Math.Abs(t.By - 10.05) < 1e-6));
        Assert.Contains(doc.Traces, t =>
            (Math.Abs(t.Ax - 40.07) < 1e-6 && Math.Abs(t.Ay - 25.03) < 1e-6) ||
            (Math.Abs(t.Bx - 40.07) < 1e-6 && Math.Abs(t.By - 25.03) < 1e-6));

        // …and the engine confirms the pins are electrically one island
        Assert.Equal(1, doc.NetIslands(n));
        Assert.Empty(Ratsnest.Compute(doc));
    }

    [Fact]
    public void PhysicsEngineAnswersThroughInterop()
    {
        if (!NativeOk) return;
        // 50-ohm synthesis round-trip on a standard FR-4 stack
        double w = NativeCore.dc_phys_microstrip_width(50, 0.2, 0.035, 4.4);
        Assert.False(double.IsNaN(w));
        Assert.Equal(0, NativeCore.dc_phys_microstrip(w, 0.2, 0.035, 4.4,
            out double z0, out double eEff, out double delay));
        Assert.Equal(50.0, z0, 2);
        Assert.InRange(eEff, 2.5, 4.4);
        Assert.InRange(delay, 5.0, 6.5);
        // textbook skin depth
        Assert.Equal(2.09e-3, NativeCore.dc_phys_skin_depth_mm(1e9), 4);
        // 90-ohm differential synthesis
        double wd = NativeCore.dc_phys_diff_microstrip_width(90, 0.15, 0.15, 0.035, 4.4);
        Assert.False(double.IsNaN(wd));
        Assert.Equal(90.0, NativeCore.dc_phys_diff_microstrip(wd, 0.15, 0.15, 0.035, 4.4), 1);
        // invalid input -> NaN, not a crash
        Assert.True(double.IsNaN(NativeCore.dc_phys_microstrip_width(-5, 0.2, 0.035, 4.4)));
    }

    [Fact]
    public void NativeRebuildSurvivesHeavyEditing()
    {
        if (!NativeOk) return;
        using var doc = new BoardDocument();
        int n = doc.AddNet("N");
        for (int i = 0; i < 50; i++)
        {
            var t = doc.AddTraceDirect(i, 0, i + 0.5, 0, 0.2, n, 0);
            if (i % 3 == 0) doc.RemoveTrace(t);
            if (i % 7 == 0) doc.Undo.Undo();
        }
        // any sequence of edits must leave a consistent native rebuild
        var violations = doc.RunDrcDetailed();
        Assert.NotNull(violations);
        Assert.True(doc.NetLengthMm(n) >= 0);
    }
}
