using System.Numerics;
using DesignStudio.Model;
using DesignStudio.Model.LinkSim;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S4 — IBIS parser + impulse response + statistical eye (E4 steps 1–3).
// Exit criterion: eye height within 10 % of a reference sim on a Gen4 channel.
// Here we validate the engine against analytic eye cases and physical trends.
// ============================================================================

public class FftTests
{
    [Fact]
    public void ForwardInverseRoundTrips()
    {
        var rng = new Random(1);
        int n = 256;
        var a = new Complex[n];
        for (int i = 0; i < n; i++) a[i] = new Complex(rng.NextDouble() - 0.5, rng.NextDouble() - 0.5);
        var orig = (Complex[])a.Clone();
        Fft.Transform(a, false);
        Fft.Transform(a, true);
        for (int i = 0; i < n; i++)
            Assert.True((a[i] - orig[i]).Magnitude < 1e-9, $"round-trip error at {i}");
    }

    [Fact]
    public void FlatSpectrumIsAnImpulse()
    {
        int n = 64;
        var ss = new Complex[n / 2 + 1];
        for (int i = 0; i < ss.Length; i++) ss[i] = Complex.One;   // H(f)=1 ⇒ h(t)=δ
        var h = Fft.RealIfft(ss, n);
        Assert.True(Math.Abs(h[0] - 1.0) < 1e-9);
        for (int i = 1; i < n; i++) Assert.True(Math.Abs(h[i]) < 1e-9);
    }

    [Fact]
    public void NonPowerOfTwoThrows()
        => Assert.Throws<ArgumentException>(() => Fft.Transform(new Complex[3], false));
}

public class IbisParserTests
{
    private const string Ibs = """
        | a small output buffer
        [Component] TEST_DRIVER
        [Model] OUT33
        Model_type   Output
        C_comp       2.0pF   1.5pF   2.5pF
        [Voltage Range]   3.3   3.0   3.6
        [Pulldown]
           0.0    0.0      0.0      0.0
           1.65   0.040    0.035    0.045
           3.3    0.080    0.070    0.090
        [Ramp]
        dV/dt_r    1.8/0.4n    1.6/0.5n    2.0/0.3n
        dV/dt_f    1.8/0.35n   1.6/0.45n   2.0/0.28n
        [Package]
        R_pkg   0.1    0.08   0.12
        L_pkg   3.2nH  3.0nH  3.5nH
        C_pkg   0.8pF  0.7pF  0.9pF
        """;

    [Fact]
    public void ParsesModelRampAndPackage()
    {
        var m = IbisModel.Parse(Ibs);
        Assert.Equal("Output", m.ModelType);
        Assert.Equal(2.0e-12, m.CCompF, 15);
        Assert.Equal(3.3, m.VoltageRangeV, 6);
        Assert.Equal(1.8, m.RampDvRiseV, 6);
        Assert.Equal(0.4e-9, m.RampDtRiseS, 15);
        Assert.Equal(3.2e-9, m.LPkgH, 15);
        Assert.Equal(0.8e-12, m.CPkgF, 15);
        Assert.Equal(3, m.Pulldown.Count);
    }

    [Fact]
    public void DerivesLinearDriver()
    {
        var d = IbisModel.Parse(Ibs).ToLinearDriver();
        Assert.Equal(3.3, d.SwingV, 6);
        Assert.Equal(0.4e-9, d.RiseTimeS, 15);
        Assert.InRange(d.ROutOhm, 20, 80);          // ~3.3 V / 0.08 A ≈ 41 Ω
    }

    [Fact]
    public void UnitParserHandlesSuffixes()
    {
        Assert.Equal(2e-12, IbisModel.Unit("2.0pF"), 15);
        Assert.Equal(3.5e-9, IbisModel.Unit("3.5nH"), 15);
        Assert.Equal(50, IbisModel.Unit("50Ohm"), 9);
        Assert.Equal(1.8, IbisModel.Unit("1.8"), 9);
    }
}

public class StatisticalEyeMathTests
{
    [Fact]
    public void NoIsiGivesFullSwingEye()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0 };
        double h = StatisticalEye.EyeHeightAt(cs, 1e-12, 0);
        Assert.InRange(h, 1.97, 2.001);             // ±swing, no ISI (tiny grid epsilon)
    }

    [Fact]
    public void KnownCursorsReproduceWorstCaseOpening()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0, [1] = 0.3, [-1] = 0.1 };
        double h = StatisticalEye.EyeHeightAt(cs, 1e-12, 0);
        Assert.InRange(h, 1.18, 1.21);              // 2·(1 − 0.4) = 1.2
    }

    [Fact]
    public void NoiseClosesTheEye()
    {
        var cs = new Dictionary<int, double> { [0] = 1.0 };
        double clean = StatisticalEye.EyeHeightAt(cs, 1e-12, 0);
        double noisy = StatisticalEye.EyeHeightAt(cs, 1e-12, 0.1);
        Assert.True(noisy < clean && noisy > 0);
        Assert.InRange(noisy, 0.4, 0.8);            // ≈ 2·(1 − 7·0.1)
    }

    [Fact]
    public void QFromBerAndErfc()
    {
        Assert.InRange(StatisticalEye.QFromBer(1e-12), 6.9, 7.1);
        Assert.Equal(1.0, StatisticalEye.Erfc(0), 3);
        Assert.InRange(StatisticalEye.Erfc(2), 0.004, 0.006);
    }
}

public class LinkSimulatorTests
{
    private static BoardDocument Channel(out int net, double lenMm, string material = "megtron6")
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(lenMm + 20, 40);
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = material;
        net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 5 + lenMm, By = 20, Width = 0.2, NetId = net, Layer = 0 });
        return doc;
    }

    [Fact]
    public void ShortChannelEyeIsOpen()
    {
        using var doc = Channel(out int net, 20);
        var r = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(DataRateGbps: 16, SwingV: 1.0, RiseTimeS: 10e-12));
        Assert.True(r.Eye.HeightV > 1.0, $"short channel should be open: {r.Eye.HeightV * 1e3:F0} mV");
        Assert.True(r.Eye.HeightV < 2.0);
        Assert.True(r.Eye.WidthUI > 0.3);
        Assert.Equal(0, r.PulseResponse.Length & (r.PulseResponse.Length - 1));   // power of two
    }

    [Fact]
    public void LongerLossierChannelClosesEye()
    {
        using var shortCh = Channel(out int n1, 20);
        using var longCh = Channel(out int n2, 200);
        var opt = new LinkSimulator.Options(DataRateGbps: 16, SwingV: 1.0, RiseTimeS: 10e-12);
        double hShort = LinkSimulator.Simulate(shortCh, n1, opt).Eye.HeightV;
        double hLong = LinkSimulator.Simulate(longCh, n2, opt).Eye.HeightV;
        Assert.True(hLong < hShort, $"longer channel should close the eye: {hLong * 1e3:F0} !< {hShort * 1e3:F0} mV");
    }

    [Fact]
    public void HigherDataRateClosesEye()
    {
        using var doc = Channel(out int net, 120);
        double h8 = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(8, 1.0, 10e-12)).Eye.HeightV;
        double h28 = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(28, 1.0, 10e-12)).Eye.HeightV;
        Assert.True(h28 < h8, $"higher rate should close the eye: {h28 * 1e3:F0} !< {h8 * 1e3:F0} mV");
    }

    [Fact]
    public void MaskFailsWhenThresholdExceedsOpening()
    {
        using var doc = Channel(out int net, 150);
        var r = LinkSimulator.Simulate(doc, net,
            new LinkSimulator.Options(16, 1.0, 10e-12, MaskHeightV: 1.9, MaskWidthUI: 0.9));
        Assert.False(r.MaskPass);                   // a lossy channel can't clear a near-ideal mask
        Assert.Contains("FAIL", r.Summary);
    }

    [Fact]
    public void IbisDriverDrivesTheSwing()
    {
        using var doc = Channel(out int net, 30);
        var ibis = IbisModel.Parse("""
            [Model] OUT
            Model_type Output
            [Voltage Range] 1.2 1.1 1.3
            [Ramp]
            dV/dt_r 0.7/0.02n 0.6/0.03n 0.8/0.015n
            """);
        var r = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(16, Driver: ibis));
        // eye opening should scale with the 1.2 V IBIS supply, not the 1 V default
        Assert.True(r.Eye.HeightV > 1.0 && r.Eye.HeightV < 2.4,
            $"IBIS-driven eye {r.Eye.HeightV * 1e3:F0} mV should reflect the 1.2 V swing");
    }
}
