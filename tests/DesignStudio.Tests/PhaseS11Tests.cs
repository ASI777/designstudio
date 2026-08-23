using System.Numerics;
using DesignStudio.Model;
using DesignStudio.Model.LinkSim;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S11 — vector-fit macromodel + multi-board channel assembly (E18) and
// vendor IBIS-AMI support (E17).
// Exit criterion: a backplane channel assembled from vendor connectors produces
// a causal pulse response and an AMI-equalised eye.
// ============================================================================

public class VectorFitTests
{
    // skin + dielectric loss with a transport delay
    private static Complex H(double f, double lenM, double er)
    {
        double c = 2.99792458e8;
        double a = 1.2e-5 * Math.Sqrt(Math.Max(f, 1)) + 8e-12 * f;
        return Complex.FromPolarCoordinates(Math.Exp(-a * lenM), -2 * Math.PI * f * Math.Sqrt(er) / c * lenM);
    }

    private static (double[] f, Complex[] h) Sample(double lenM, double er, int m = 200)
    {
        var f = new double[m]; var h = new Complex[m];
        for (int i = 0; i < m; i++) { f[i] = 1e7 + (4e10 - 1e7) * i / (m - 1); h[i] = H(f[i], lenM, er); }
        return (f, h);
    }

    [Fact]
    public void RecoversTheBulkDelay()
    {
        var (f, h) = Sample(0.3, 4.0);
        var vf = VectorFit.Fit(f, h, 16);
        double analytic = 0.3 * Math.Sqrt(4.0) / 2.99792458e8;     // 2.0 ns
        Assert.InRange(vf.DelayS, analytic * 0.9, analytic * 1.1);
    }

    [Fact]
    public void ImpulseIsCausalByConstruction()
    {
        var (f, h) = Sample(0.3, 4.0);
        var vf = VectorFit.Fit(f, h, 16);
        Assert.Equal(0.0, vf.Impulse(vf.DelayS - 1e-12));          // nothing before the delay
        Assert.Equal(0.0, vf.Impulse(0));
        Assert.True(Math.Abs(vf.Impulse(vf.DelayS + 5e-12)) > 0);  // response after it
    }

    [Fact]
    public void TracksTheFrequencyResponse()
    {
        var (f, h) = Sample(0.3, 4.0);
        var vf = VectorFit.Fit(f, h, 20);
        var (max, mean) = vf.FitError(f, h);
        Assert.True(mean < 0.15, $"mean fit error {mean:F3} too large");
    }
}

public class ChannelAssemblyTests
{
    private static SParameterBlock Connector(double midDb)
        => SParameterBlock.Parse($"""
            # GHZ S DB R 50
            0.1 -25 0  {-0.5} -5   {-0.5} -5   -25 0
            20  -20 0  {midDb} -120  {midDb} -120  -20 0
            """);

    private static BoardDocument SegmentBoard(out int net, double lenMm)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(lenMm + 20, 30);
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = "megtron6";
        net = doc.AddNet("SEG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15, Bx = 5 + lenMm, By = 15, Width = 0.15, NetId = net, Layer = 0 });
        return doc;
    }

    [Fact]
    public void BackplaneAssemblyProducesACausalEye()
    {
        using var doc = SegmentBoard(out int net, 40);
        var seg = ChannelAssembly.SegmentFromNet(doc, net, 1e7, 4e10, 101);
        var conn = Connector(-3);
        // daughtercard → connector → backplane → connector → daughtercard
        var backplane = new ChannelAssembly().Add(seg).Add(conn).Add(seg).Add(conn).Add(seg);
        Assert.Equal(5, backplane.SegmentCount);

        var r = backplane.Simulate(new LinkSimulator.Options(16, 1.0, 10e-12));
        Assert.True(r.Eye.HeightV >= 0);
        double pre = LinkSimulator.PrecursorEnergyFraction(r.PulseResponse, r.DtS, r.UiS);
        Assert.True(pre < 0.3, $"pulse response should be causal: pre-cursor energy {pre:P0}");
    }

    [Fact]
    public void MoreConnectorsCloseTheEye()
    {
        using var doc = SegmentBoard(out int net, 30);
        var seg = ChannelAssembly.SegmentFromNet(doc, net, 1e7, 4e10, 101);
        var conn = Connector(-4);
        var opt = new LinkSimulator.Options(16, 1.0, 10e-12);

        double oneBoard = new ChannelAssembly().Add(seg).Simulate(opt).Eye.HeightV;
        double backplane = new ChannelAssembly().Add(seg).Add(conn).Add(seg).Add(conn).Add(seg).Simulate(opt).Eye.HeightV;
        Assert.True(backplane < oneBoard,
            $"connectors + extra boards should close the eye: {backplane * 1e3:F0} !< {oneBoard * 1e3:F0} mV");
    }
}

public class AmiModelTests
{
    private const string Ami = """
        (vendor_serdes
          (Reserved_Parameters (AMI_Version (Usage Info)(Type String)(Value "5.1")))
          (Model_Specific
            (CTLE (dc_gain (Usage In)(Type Float)(Value -2.0))
                  (zero_ghz (Value 5))
                  (pole_ghz (Value 14)))
            (FFE (taps (Usage In)(Type Tap)(Value -0.1 0.7 -0.2)))
            (DFE (taps (Usage In)(Type Int)(Value 3)))))
        """;

    [Fact]
    public void ParsesAmiTreeIntoEqualiser()
    {
        var root = AmiModel.Parse(Ami);
        var eq = AmiModel.ToEqualizer(root);

        Assert.NotNull(eq.Ctle);
        Assert.Equal(-2.0, eq.Ctle!.DcGainDb, 3);
        Assert.Equal(5e9, eq.Ctle.ZeroHz, 0);
        Assert.Equal(14e9, eq.Ctle.PoleHz, 0);

        Assert.NotNull(eq.Ffe);
        Assert.Equal(3, eq.Ffe!.Taps.Length);
        Assert.Equal(1, eq.Ffe.MainTap);                  // 0.7 is the cursor

        Assert.NotNull(eq.Dfe);
        Assert.Equal(3, eq.Dfe!.Taps);
    }

    [Fact]
    public void ParametricAmiExposesEqualiser()
    {
        var ami = new ParametricAmi(AmiModel.Parse(Ami), "vendor");
        Assert.Equal("vendor", ami.Name);
        var (pulse, dfe) = ami.Process(new double[256], 1e-12, 10e-12);
        Assert.Equal(3, dfe);
        Assert.Equal(256, pulse.Length);
    }

    [Fact]
    public void AmiEqualiserReopensALossyEye()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(90, 40);
        doc.SetCopperLayers(4);
        doc.Stackup[0].MaterialId = "fr4-std";
        int net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 65, By = 20, Width = 0.2, NetId = net, Layer = 0 });

        var bare = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(16, 1.0, 10e-12));
        var eq = AmiModel.ToEqualizer(AmiModel.Parse("""
            (m (Model_Specific
               (CTLE (dc_gain (Value 0))(zero_ghz (Value 5.3))(pole_ghz (Value 13)))
               (DFE (taps (Value 4)))))
            """));
        var eqd = LinkSimulator.Simulate(doc, net, new LinkSimulator.Options(16, 1.0, 10e-12, Eq: eq));
        Assert.True(eqd.Eye.HeightV > bare.Eye.HeightV,
            $"AMI EQ should reopen the eye: {eqd.Eye.HeightV * 1e3:F0} !> {bare.Eye.HeightV * 1e3:F0} mV");
    }
}
