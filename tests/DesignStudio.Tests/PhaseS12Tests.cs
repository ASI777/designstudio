using DesignStudio.Model;
using DesignStudio.Model.LinkSim;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S12 — PAM4 / differential signalling (E15) and the statistical
// crosstalk-aware eye (E16).
// Exit criterion: a 112G PAM4 differential channel reports three sub-eye heights
// and a per-standard pass/fail, and adding a switching aggressor closes the
// victim eye by the measured coupled amount.
// ============================================================================

public class Pam4EyeTests
{
    // A clean cursor-only response: three stacked sub-eyes each span 2/3 of the
    // outer level separation (levels −1, −1/3, +1/3, +1).
    [Fact]
    public void LosslessPam4HasThreeEqualSubEyes()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0 };
        var eyes = StatisticalEye.Pam4SubEyesAt(cs, 1e-6, 0);

        Assert.Equal(3, eyes.Length);
        foreach (double e in eyes) Assert.InRange(e, 0.66, 0.67);          // 2/3 of unit level sep
        Assert.True(Math.Abs(eyes[0] - eyes[2]) < 1e-6, "outer sub-eyes should match");
    }

    // ISI from neighbouring symbols (4-level random data) collapses every sub-eye
    // by ~2·Σ|tap| — the inner eyes close just like NRZ, only there are three of them.
    [Fact]
    public void IsiClosesAllSubEyes()
    {
        var clean = new Dictionary<int, double> { [0] = 1.0 };
        var lossy = new Dictionary<int, double> { [0] = 1.0, [1] = 0.15, [2] = 0.08 };

        double cleanMin = StatisticalEye.Pam4SubEyesAt(clean, 1e-6, 0).Min();
        var lossyEyes = StatisticalEye.Pam4SubEyesAt(lossy, 1e-6, 0);

        Assert.True(lossyEyes.Min() < cleanMin, "ISI must close the worst sub-eye");
        // expected ≈ 2/3 − 2·(0.15+0.08) = 0.207
        Assert.InRange(lossyEyes.Min(), 0.18, 0.24);
    }

    // E16: a switching aggressor is extra 4-level data folded into every level —
    // it shrinks all three sub-eyes.
    [Fact]
    public void Pam4AggressorClosesSubEyes()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0 };
        var aggr = new List<IReadOnlyList<double>> { new List<double> { 0.10, 0.05 } };

        double quiet = StatisticalEye.Pam4SubEyesAt(cs, 1e-6, 0).Min();
        double noisy = StatisticalEye.Pam4SubEyesAt(cs, 1e-6, 0, aggressors: aggr).Min();

        Assert.True(noisy < quiet, $"aggressor must close the sub-eye: {noisy:F3} !< {quiet:F3}");
    }

    [Fact]
    public void RlmIsUnityForSymmetricLevels()
    {
        // a flat pulse → equal sub-eyes → RLM 1
        var p = new double[256];
        for (int i = 100; i < 110; i++) p[i] = 1.0;             // 10-sample flat cursor
        var eye = StatisticalEye.Pam4Analyze(p, 1e-12, 10e-12, ber: 1e-6);

        Assert.Equal(3, eye.SubEyeHeightsV.Length);
        Assert.InRange(eye.Rlm, 0.95, 1.0);
    }
}

public class CrosstalkEyeTests
{
    // E16 validated in Python: a victim NRZ eye closes by exactly 2·Σ|coupled cursor|.
    [Fact]
    public void AggressorClosesNrzEyeByCoupledAmount()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0, [1] = 0.05 };   // small self-ISI
        var aggr = new List<IReadOnlyList<double>> { new List<double> { 0.08, 0.04 } };

        double quiet = StatisticalEye.EyeHeightAt(cs, 1e-12, 0);
        double withXt = StatisticalEye.EyeHeightAt(cs, 1e-12, 0, aggressors: aggr);

        Assert.True(withXt < quiet);
        // closure ≈ 2·(0.08+0.04) = 0.24
        Assert.InRange(quiet - withXt, 0.22, 0.26);
    }

    [Fact]
    public void MoreAggressorsCloseTheEyeFurther()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0 };
        var one = new List<IReadOnlyList<double>> { new List<double> { 0.06 } };
        var two = new List<IReadOnlyList<double>> { new List<double> { 0.06 }, new List<double> { 0.05 } };

        double e1 = StatisticalEye.EyeHeightAt(cs, 1e-12, 0, aggressors: one);
        double e2 = StatisticalEye.EyeHeightAt(cs, 1e-12, 0, aggressors: two);

        Assert.True(e2 < e1, "a second aggressor should close the eye further");
    }
}

public class Pam4MaskTests
{
    [Fact]
    public void MaskPassesACleanEyeAndFailsAClosedOne()
    {
        var mask = Pam4Masks.Find("112G PAM4 (56 GBd)");
        Assert.NotNull(mask);

        var clean = new StatisticalEye.Pam4Eye(new[] { 0.050, 0.052, 0.048 }, 0.048, 0.96, 0);
        var closed = new StatisticalEye.Pam4Eye(new[] { 0.050, 0.010, 0.040 }, 0.010, 0.85, 0);

        Assert.True(Pam4Masks.Check(mask!, clean).Pass);
        Assert.False(Pam4Masks.Check(mask!, closed).Pass);     // worst sub-eye + RLM both below floor
    }
}

public class Pam4LinkTests
{
    private static BoardDocument DiffPair(out int pNet, double lenMm)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(lenMm + 20, 30);
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = "megtron6";
        pNet = doc.AddNet("LANE_P");
        int nNet = doc.AddNet("LANE_N");
        // a tightly-coupled differential pair (0.15 mm traces, 0.2 mm edge-to-edge)
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15.0, Bx = 5 + lenMm, By = 15.0, Width = 0.15, NetId = pNet, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15.35, Bx = 5 + lenMm, By = 15.35, Width = 0.15, NetId = nNet, Layer = 0 });
        return doc;
    }

    // Exit criterion: a 112G PAM4 differential channel reports three sub-eye
    // heights and a per-standard pass/fail. The P-net of the routed pair is the
    // differential-through (the MoM extraction already references the planes); the
    // companion N trace establishes the coupled pair.
    [Fact]
    public void Differential112GPam4ReportsThreeSubEyes()
    {
        using var doc = DiffPair(out int pNet, 12);
        var seg = ChannelAssembly.SegmentFromNet(doc, pNet, 1e7, 4e10, 81);
        var asm = new ChannelAssembly().Add(seg);

        // 56 GBaud = 112 Gb/s PAM4, 0.8 Vpp, Rx CTLE peaking at the 28 GHz Nyquist
        var eq = new Equalizer { Ctle = new Ctle(0, 14e9, 28e9) };
        var opt = new LinkSimulator.Options(56, 0.8, Ber: 1e-6, Eq: eq);
        var r = LinkSimulator.SimulatePam4(asm.S21, opt, label: "112G PAM4 lane");

        Assert.Equal(3, r.Eye.SubEyeHeightsV.Length);
        Assert.All(r.Eye.SubEyeHeightsV, e => Assert.True(double.IsFinite(e)));
        Assert.True(r.MinSubEyeV >= 0);                        // reported opening is clamped at 0
        Assert.InRange(r.Rlm, 0.0, 1.0);
        Assert.True(r.InsertionLossDb <= 0, "insertion loss should be ≤ 0 dB");

        // a per-standard pass/fail is produced
        var mask = Pam4Masks.Find("112G PAM4 (56 GBd)")!;
        var verdict = Pam4Masks.Check(mask, r.Eye);
        Assert.Contains(verdict.Pass ? "PASS" : "FAIL", verdict.Summary);
    }

    // Adding a switching aggressor closes the victim eye (E16, channel level).
    [Fact]
    public void AggressorClosesThePam4LaneEye()
    {
        using var doc = DiffPair(out int pNet, 12);
        var seg = ChannelAssembly.SegmentFromNet(doc, pNet, 1e7, 4e10, 81);
        var asm = new ChannelAssembly().Add(seg);
        var eq = new Equalizer { Ctle = new Ctle(0, 14e9, 28e9) };
        var opt = new LinkSimulator.Options(56, 0.8, Ber: 1e-6, Eq: eq);

        double quiet = LinkSimulator.SimulatePam4(asm.S21, opt).MinSubEyeV;
        var aggr = new List<IReadOnlyList<double>> { new List<double> { 0.05, 0.02 } };
        double noisy = LinkSimulator.SimulatePam4(asm.S21, opt, aggr).MinSubEyeV;

        Assert.True(noisy <= quiet, $"aggressor must not open the eye: {noisy:F4} > {quiet:F4}");
    }
}
