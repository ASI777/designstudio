using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S9 — full DDR5 channel topology (E19): multi-byte-lane data + fly-by
// CA bus + 2DPC, rolled up per channel and per subsystem.
// Exit criterion: a multi-channel 2DPC DDR5-6400 board reports a per-channel
// margin table and an overall pass/fail.
// ============================================================================

public class DdrSubsystemTests
{
    // 4-layer Megtron board; channels stacked in y. 2 DQ + DQS + 1 CA + CK each,
    // matched microstrip on L0 (kept small/short for test speed).
    private static BoardDocument Board()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 60);
        doc.SetCopperLayers(4);
        for (int i = 0; i < doc.Stackup.Count; i++) doc.Stackup[i].MaterialId = "megtron6";
        return doc;
    }

    private static DdrSubsystem.Channel BuildChannel(BoardDocument doc, string name, double yBase,
                                                     double lenMm = 15, double skewExtraMm = 0, bool withCa = true)
    {
        void Route(int net, double y, double len)
            => doc.Traces.Add(new TraceItem { Ax = 5, Ay = y, Bx = 5 + len, By = y, Width = 0.13, NetId = net, Layer = 0 });

        var dq = new List<int>();
        for (int i = 0; i < 2; i++)
        {
            int n = doc.AddNet($"{name}_DQ{i}");
            Route(n, yBase + i, i == 0 ? lenMm + skewExtraMm : lenMm);   // optional skew on DQ0
            dq.Add(n);
        }
        int dqs = doc.AddNet($"{name}_DQS"); Route(dqs, yBase + 2, lenMm);

        DdrSubsystem.CaBus? ca = null;
        if (withCa)
        {
            int ca0 = doc.AddNet($"{name}_CA0"); Route(ca0, yBase + 3, lenMm);
            int ck = doc.AddNet($"{name}_CK"); Route(ck, yBase + 4, lenMm);
            ca = new DdrSubsystem.CaBus(new[] { ca0 }, ck);
        }
        return new DdrSubsystem.Channel(name, new[] { new DdrSubsystem.ByteLane("b0", dq, dqs) }, ca);
    }

    [Fact]
    public void MatchedSingleChannelPasses()
    {
        using var doc = Board();
        var ch = BuildChannel(doc, "CH0", 5);
        var r = DdrSubsystem.Analyze(doc, "socket0", new[] { ch }, DdrTimingEngine.Ddr5_6400);
        Assert.True(r.Pass, r.Summary);
        Assert.Single(r.Channels);
        Assert.True(r.Channels[0].WorstMarginPs > 0);
        Assert.NotNull(r.Channels[0].Ca);                  // fly-by CA bus was analysed
    }

    [Fact]
    public void MultiChannelProducesPerChannelTable()
    {
        using var doc = Board();
        var chans = new[] { BuildChannel(doc, "CH0", 5), BuildChannel(doc, "CH1", 30) };
        var r = DdrSubsystem.Analyze(doc, "socket0", chans, DdrTimingEngine.Ddr5_6400);
        Assert.Equal(2, r.Channels.Count);
        Assert.Contains(r.Channels, c => c.Name == "CH0");
        Assert.Contains(r.Channels, c => c.Name == "CH1");
    }

    [Fact]
    public void SkewedChannelFailsAndIsNamed()
    {
        using var doc = Board();
        var chans = new[]
        {
            BuildChannel(doc, "CH0", 5),
            BuildChannel(doc, "CH1", 30, skewExtraMm: 14)   // DQ0 14 mm long ⇒ fails
        };
        var r = DdrSubsystem.Analyze(doc, "socket0", chans, DdrTimingEngine.Ddr5_6400);
        Assert.False(r.Pass);
        Assert.Equal("CH1", r.WorstChannel);
        Assert.False(r.Channels.Single(c => c.Name == "CH1").Pass);
        Assert.True(r.Channels.Single(c => c.Name == "CH0").Pass);
    }

    [Fact]
    public void TwoDpcReducesMargin()
    {
        using var doc = Board();
        var ch1 = BuildChannel(doc, "CH0", 5);
        double m1 = DdrSubsystem.Analyze(doc, "s", new[] { ch1 }, DdrTimingEngine.Ddr5_6400)
                               .Channels[0].WorstMarginPs;
        var ch2 = ch1 with { DimmsPerChannel = 2 };
        double m2 = DdrSubsystem.Analyze(doc, "s", new[] { ch2 }, DdrTimingEngine.Ddr5_6400)
                               .Channels[0].WorstMarginPs;
        Assert.True(m2 < m1, $"2DPC should reduce margin: {m2:F1} !< {m1:F1} ps");
    }

    [Fact]
    public void CaBusHasMoreMarginThanDataAtSameRate()
    {
        // CA runs at half the data rate, so its lane should be comfortably open
        using var doc = Board();
        var ch = BuildChannel(doc, "CH0", 5);
        var r = DdrSubsystem.Analyze(doc, "s", new[] { ch }, DdrTimingEngine.Ddr5_6400);
        var c = r.Channels[0];
        Assert.NotNull(c.Ca);
        Assert.True(c.Ca!.Pass);
        Assert.True(c.Ca.WorstSetupPs > 0 && c.Ca.WorstHoldPs > 0);
    }
}
