using DesignStudio.Model;
using DesignStudio.Model.LinkSim;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S5 — plane-cavity PDN solver (E6) + compliance masks & jitter (E5).
// Exit criterion: a DDR5 board passes/fails with spatial decap analysis + a
// mask report.
// ============================================================================

public class PlaneCavityTests
{
    private const double C0 = 2.99792458e8;
    // 50 × 40 mm power/ground plane pair, 0.1 mm apart, εr 4.2.
    private static PlaneCavity Cavity(IEnumerable<PlaneCavity.Port> ports,
                                      IEnumerable<PlaneCavity.Decap> decaps)
        => new(50, 40, 0.1, 4.2, 0.02, ports.ToList(), decaps.ToList());

    [Fact]
    public void ZeroOrderModeIsThePlaneCapacitance()
    {
        var cav = Cavity(new[] { new PlaneCavity.Port(25, 20, "L") }, Array.Empty<PlaneCavity.Decap>());
        double cPlane = 4.2 * 8.8541878128e-12 * (0.05 * 0.04) / (0.1e-3);
        double zCap = 1.0 / (2 * Math.PI * 1e8 * cPlane);          // ≈ 2.14 Ω
        double z00 = cav.ZMatrix(1e8)[0, 0].Magnitude;
        Assert.InRange(z00, zCap * 0.9, zCap * 1.1);
    }

    [Fact]
    public void SelfImpedancePeaksAtCavityResonance()
    {
        // off-centre port so the (1,0) mode is excited (centre is a node)
        var cav = Cavity(new[] { new PlaneCavity.Port(5, 20, "L") }, Array.Empty<PlaneCavity.Decap>());
        double f10 = C0 / (2 * Math.Sqrt(4.2)) * (1 / 0.05);       // ≈ 1.463 GHz
        double peakF = 0, peakZ = 0;
        for (int i = 0; i <= 200; i++)
        {
            double f = 1.1e9 + (1.8e9 - 1.1e9) * i / 200.0;
            double z = cav.ZMatrix(f)[0, 0].Magnitude;
            if (z > peakZ) { peakZ = z; peakF = f; }
        }
        Assert.InRange(peakF, f10 * 0.92, f10 * 1.08);
    }

    [Fact]
    public void DecapCrushesImpedanceNearItsResonance()
    {
        const double c = 100e-9, esl = 0.5e-9, esr = 0.01;
        double srf = 1.0 / (2 * Math.PI * Math.Sqrt(esl * c));     // ≈ 22.5 MHz
        var noDecap = Cavity(new[] { new PlaneCavity.Port(25, 20, "L") }, Array.Empty<PlaneCavity.Decap>());
        var withDecap = Cavity(
            new[] { new PlaneCavity.Port(25, 20, "L"), new PlaneCavity.Port(15, 20, "C1") },
            new[] { new PlaneCavity.Decap(1, c, esl, esr, "C1") });

        double z0 = noDecap.Zin(srf).Magnitude;
        double z1 = withDecap.Zin(srf).Magnitude;
        Assert.True(z1 < 0.1, $"decap should pull Z below 100 mΩ at SRF, got {z1 * 1e3:F1} mΩ");
        Assert.True(z1 < 0.2 * z0, $"decap should dominate: {z1 * 1e3:F1} !< {0.2 * z0 * 1e3:F1} mΩ");
    }

    [Fact]
    public void EffectivenessRankingIsSortedAndComplete()
    {
        var ports = new[]
        {
            new PlaneCavity.Port(25, 20, "L"),
            new PlaneCavity.Port(10, 20, "C1"),
            new PlaneCavity.Port(40, 20, "C2"),
        };
        var decaps = new[]
        {
            new PlaneCavity.Decap(1, 1e-6, 0.8e-9, 0.008, "C1"),
            new PlaneCavity.Decap(2, 10e-9, 0.4e-9, 0.012, "C2"),
        };
        var r = Cavity(ports, decaps).Analyze(0.05);
        Assert.Equal(2, r.DecapRanking.Count);
        Assert.True(r.DecapRanking[0].WorstFreqContribOhm >= r.DecapRanking[1].WorstFreqContribOhm);
    }

    [Fact]
    public void PlacementHintAppearsWhenOverTarget()
    {
        var cav = Cavity(new[] { new PlaneCavity.Port(25, 20, "L") }, Array.Empty<PlaneCavity.Decap>());
        var r = cav.Analyze(targetZOhm: 1e-4);                     // unreachably low ⇒ violation
        Assert.NotNull(r.Hint);
        Assert.InRange(r.Hint!.XMm, 0, 50);
        Assert.InRange(r.Hint.YMm, 0, 40);
        Assert.NotEmpty(r.ResonancesHz);
    }
}

public class ComplianceMaskTests
{
    private static StatisticalEye.Eye Eye(double h, double w)
        => new(h, w, w * 1e3, 0, 1e-12, h, h);

    [Fact]
    public void OpenEyePassesGen4Mask()
    {
        var mask = ComplianceMasks.Find("PCIe Gen4 (16 GT/s)")!;
        var res = ComplianceMasks.Check(mask, Eye(0.5, 0.5));
        Assert.True(res.Pass);
        Assert.Contains("PASS", res.Summary);
    }

    [Fact]
    public void ClosedEyeFailsGen4Mask()
    {
        var mask = ComplianceMasks.Find("PCIe Gen4 (16 GT/s)")!;
        var res = ComplianceMasks.Check(mask, Eye(0.005, 0.10));
        Assert.False(res.Pass);
    }

    [Fact]
    public void Ddr5PresetExists()
    {
        var mask = ComplianceMasks.Find("DDR5-6400");
        Assert.NotNull(mask);
        Assert.Equal(6.4, mask!.DataRateGbps, 3);
    }
}

public class JitterDecompositionTests
{
    // build a trivial pulse response with a clean main cursor for the decomposition
    private static double[] CleanPulse(out double dt, out double ui)
    {
        int n = 4096; dt = 1e-12; ui = 50e-12;
        int spui = (int)(ui / dt);
        var p = new double[n];
        for (int i = 0; i < spui; i++) p[1000 + i] = 1.0;          // one UI-wide pulse
        return p;
    }

    [Fact]
    public void TotalJitterIsDdjWhenNoRjDjInjected()
    {
        var p = CleanPulse(out double dt, out double ui);
        var j = JitterDecomposition.Decompose(p, dt, ui, 1e-12, rjRmsUI: 0, djUI: 0);
        Assert.Equal(j.DdjUI, j.TotalJitterUI, 6);
        Assert.InRange(j.EyeWidthUI, 0, 1);
    }

    [Fact]
    public void InjectedJitterClosesTheWidth()
    {
        var p = CleanPulse(out double dt, out double ui);
        double clean = JitterDecomposition.Decompose(p, dt, ui, 1e-12, 0, 0).EyeWidthUI;
        double jittered = JitterDecomposition.Decompose(p, dt, ui, 1e-12, rjRmsUI: 0.02, djUI: 0.05).EyeWidthUI;
        Assert.True(jittered < clean, $"jitter should close width: {jittered:F3} !< {clean:F3}");
        Assert.InRange(StatisticalEye.QFromBer(1e-12), 6.9, 7.1);
    }
}

public class PdnSpatialIntegrationTests
{
    [Fact]
    public void BuildsCavityFromBoardGeometry()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        int vdd = doc.AddNet("VDD");
        int gnd = doc.AddNet("GND");
        doc.Planes.Add(new PlaneShape { NetId = vdd, Layer = 1 });
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", X = 30, Y = 20,
            Pads = new List<PadItem> { new() { Name = "1", NetId = vdd, W = 0.3, H = 0.3, ThroughHole = true } }
        });
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "C1", LibName = "0402", X = 15, Y = 20,
            Pads = new List<PadItem>
            {
                new() { Name = "1", X = -0.5, NetId = vdd, W = 0.3, H = 0.3, ThroughHole = true },
                new() { Name = "2", X = 0.5, NetId = gnd, W = 0.3, H = 0.3, ThroughHole = true }
            }
        });
        PlaneGenerator.RegenerateAll(doc);

        var cav = PdnAnalyzer.BuildCavity(doc, vdd, null);
        Assert.NotNull(cav);
        var r = cav!.Analyze(0.05, fStop: 1e9, points: 60);
        Assert.NotEmpty(r.Curve);
        Assert.Single(r.DecapRanking);                            // the one decap C1
    }

    [Fact]
    public void CheckComplianceRunsEndToEnd()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(40, 40);
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = "megtron6";
        int net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 15, By = 20, Width = 0.2, NetId = net, Layer = 0 });

        var comp = LinkSimulator.CheckCompliance(doc, net, "Generic 10 Gb/s", swingV: 1.0, riseTimeS: 8e-12);
        Assert.NotNull(comp);
        Assert.True(comp!.Link.Eye.HeightV > 0);
        Assert.InRange(comp.Jitter.DdjUI, 0, 1);
        Assert.True(comp.Mask.Pass, $"short low-loss channel should clear the generic mask: {comp.Mask.Summary}");
    }
}
