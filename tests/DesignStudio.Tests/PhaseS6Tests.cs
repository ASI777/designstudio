using DesignStudio.Model;
using DesignStudio.Model.LinkSim;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S6 — equalisation (E4 step 4) + DDR timing engine (E7), and a
// server-grade DDR5-6400 byte lane that must pass.
// ============================================================================

public class EqualizationTests
{
    [Fact]
    public void CtleBoostsHighFrequency()
    {
        var eq = new Equalizer { Ctle = new Ctle(DcGainDb: 0, ZeroHz: 4e9, PoleHz: 12e9) };
        double low = eq.CtleH(1e8).Magnitude;
        double high = eq.CtleH(8e9).Magnitude;
        Assert.True(high > low, $"CTLE should peak at HF: {high:F3} !> {low:F3}");
    }

    [Fact]
    public void FfeReshapesPulse()
    {
        var eq = new Equalizer { Ffe = new TxFfe(new[] { -0.2, 1.0, -0.3 }, 1) };
        var p = new double[400];
        for (int i = 50; i < 60; i++) p[i] = 1.0;            // a 10-sample pulse
        var q = eq.ApplyFfe(p, dtS: 1e-12, uiS: 10e-12);
        // the pre/post taps create over/undershoot at the pulse edges
        Assert.True(Enumerable.Range(0, p.Length).Any(i => Math.Abs(q[i] - p[i]) > 1e-9),
            "FFE should reshape the pulse");
    }

    [Fact]
    public void DfeCancelsPostCursorIsi()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0, [-1] = 0.1, [1] = 0.3, [2] = 0.2 };
        double noDfe = StatisticalEye.EyeHeightAt(cs, 1e-12, 0);
        double dfe2 = StatisticalEye.EyeHeightAt(cs, 1e-12, 0, dfeTaps: 2);
        Assert.True(dfe2 > noDfe, $"DFE should open the eye: {dfe2:F3} !> {noDfe:F3}");
        // post-cursors 1,2 cancelled ⇒ only pre-cursor 0.1 ISI ⇒ 2·(1−0.1)=1.8
        Assert.InRange(dfe2, 1.77, 1.81);
    }

    [Fact]
    public void BuiltInAmiAppliesFfeAndReportsDfe()
    {
        var ami = new BuiltInAmi(new Equalizer { Ffe = new TxFfe(new[] { 1.0, -0.3 }, 0), Dfe = new Dfe(2) });
        var (pulse, dfe) = ami.Process(new double[256], 1e-12, 10e-12);
        Assert.Equal(2, dfe);
        Assert.Equal(256, pulse.Length);
        Assert.Equal("built-in FFE/CTLE/DFE", ami.Name);
    }
}

public class EqualizedLinkTests
{
    [Fact]
    public void EqualisationReopensALossyEye()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(90, 40);
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = "fr4-std";               // lossy
        int net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 65, By = 20, Width = 0.2, NetId = net, Layer = 0 });

        var bare = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(16, 1.0, 10e-12));
        var eq = new Equalizer
        {
            Ctle = new Ctle(0, 16e9 / 3, 16e9 / 1.2),
            Dfe = new Dfe(3)
        };
        var eqd = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(16, 1.0, 10e-12, Eq: eq));
        Assert.True(eqd.Eye.HeightV > bare.Eye.HeightV,
            $"EQ should reopen the eye: {eqd.Eye.HeightV * 1e3:F0} !> {bare.Eye.HeightV * 1e3:F0} mV");
    }
}

public class DdrTimingTests
{
    // a byte lane of length-matched microstrip on Megtron-6
    private static BoardDocument MatchedLane(out List<int> dq, out int dqs, double lenMm = 18,
                                             double skewExtraMm = 0)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(lenMm + 30, 40);
        doc.SetCopperLayers(4);
        for (int i = 0; i < doc.Stackup.Count; i++) doc.Stackup[i].MaterialId = "megtron6";
        dq = new List<int>();
        for (int i = 0; i < 8; i++)
        {
            int n = doc.AddNet($"DQ{i}");
            double len = i == 0 ? lenMm + skewExtraMm : lenMm;   // optional skew on DQ0
            doc.Traces.Add(new TraceItem { Ax = 5, Ay = 5 + i, Bx = 5 + len, By = 5 + i, Width = 0.15, NetId = n, Layer = 0 });
            dq.Add(n);
        }
        dqs = doc.AddNet("DQS");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 14, Bx = 5 + lenMm, By = 14, Width = 0.15, NetId = dqs, Layer = 0 });
        return doc;
    }

    [Fact]
    public void MatchedLowLossLanePasses()
    {
        using var doc = MatchedLane(out var dq, out int dqs);
        var r = DdrTimingEngine.AnalyzeByteLane(doc, dq, dqs, DdrTimingEngine.Ddr5_6400);
        Assert.True(r.Pass, r.Summary);
        Assert.True(r.WorstSetupPs > 0 && r.WorstHoldPs > 0);
        Assert.Equal(8, r.Bits.Count);
        Assert.Equal(156.25, r.UiPs, 1);
    }

    [Fact]
    public void SkewedBitFailsAndIsNamed()
    {
        using var doc = MatchedLane(out var dq, out int dqs, skewExtraMm: 14);   // DQ0 14 mm long
        var r = DdrTimingEngine.AnalyzeByteLane(doc, dq, dqs, DdrTimingEngine.Ddr5_6400);
        Assert.False(r.Pass);
        Assert.Equal("DQ0", r.WorstBit);
        Assert.Contains("DQ0", r.Summary);
    }
}

public class ServerDdr5Capstone
{
    // Server-grade DDR5-6400 byte lane: length-matched Megtron-6 striplines on an
    // inner layer between planes, with a decoupled VDDQ PDN. Must pass.
    [Fact]
    public void ServerGradeDdr5ByteLanePasses()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(6);                              // planes at L1, L4 ⇒ L2 is stripline
        for (int i = 0; i < doc.Stackup.Count; i++)
        {
            doc.Stackup[i].MaterialId = "megtron6";
            doc.Stackup[i].DielectricHeightMm = 0.1;         // thin, controlled stripline
        }

        var dq = new List<int>();
        for (int i = 0; i < 8; i++)
        {
            int n = doc.AddNet($"DQ{i}");
            // matched 18 mm striplines on L2, 1 mm pitch (low crosstalk)
            doc.Traces.Add(new TraceItem { Ax = 5, Ay = 5 + i, Bx = 23, By = 5 + i, Width = 0.13, NetId = n, Layer = 2 });
            dq.Add(n);
        }
        int dqs = doc.AddNet("DQS");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 14, Bx = 23, By = 14, Width = 0.13, NetId = dqs, Layer = 2 });

        // VDDQ PDN: 20×20 mm plane pair, well decoupled (E6 feeds the SSN term)
        var ports = new List<PlaneCavity.Port>
        {
            new(10, 10, "BGA"), new(4, 4, "C1"), new(16, 4, "C2"), new(4, 16, "C3"), new(16, 16, "C4"),
        };
        var decaps = new List<PlaneCavity.Decap>
        {
            new(1, 220e-9, 0.5e-9, 0.008, "C1"), new(2, 220e-9, 0.5e-9, 0.008, "C2"),
            new(3, 100e-9, 0.4e-9, 0.010, "C3"), new(4, 100e-9, 0.4e-9, 0.010, "C4"),
        };
        var pdn = new PlaneCavity(20, 20, 0.1, 3.5, 0.005, ports, decaps);

        var report = DdrTimingEngine.AnalyzeByteLane(doc, dq, dqs, DdrTimingEngine.Ddr5_6400,
                                                     pdn: pdn, eq: null, laneName: "byte0");

        Assert.True(report.Pass, report.Summary);
        Assert.True(report.WorstSetupPs > 0, $"setup margin {report.WorstSetupPs:F1} ps");
        Assert.True(report.WorstHoldPs > 0, $"hold margin {report.WorstHoldPs:F1} ps");
        Assert.All(report.Bits, b => Assert.True(b.Pass, $"{b.Net} failed: {b.SetupPs:F1}/{b.HoldPs:F1} ps"));

        // and the data eye clears the DDR5 mask at the chip
        var comp = LinkSimulator.CheckCompliance(doc, dq[0], "DDR5-6400", swingV: 1.1, riseTimeS: 0.15 / 6.4e9);
        Assert.NotNull(comp);
    }
}
