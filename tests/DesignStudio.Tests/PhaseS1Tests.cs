using System.Numerics;
using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class TouchstoneTests
{
    [Fact]
    public void ParsesRiFormatWithHz()
    {
        var blk = SParameterBlock.Parse("""
            ! test file
            # Hz S RI R 50
            1e9 0.1 0.0  0.9 -0.1  0.9 -0.1  0.1 0.0
            2e9 0.2 0.0  0.8 -0.2  0.8 -0.2  0.2 0.0
            """);
        Assert.Equal(2, blk.FreqHz.Length);
        Assert.Equal(1e9, blk.FreqHz[0]);
        Assert.Equal(0.1, blk.S11[0].Real, 6);
        Assert.Equal(new Complex(0.9, -0.1), blk.S21[0]);
        Assert.Equal(50, blk.RefOhm);
    }

    [Fact]
    public void ParsesDbFormatWithGhzDefault()
    {
        var blk = SParameterBlock.Parse("""
            # GHZ S DB R 50
            1 -20 0  -1 -45  -1 -45  -20 0
            """);
        Assert.Equal(1e9, blk.FreqHz[0]);
        Assert.Equal(0.1, blk.S11[0].Magnitude, 3);          // -20 dB
        Assert.Equal(Math.Pow(10, -1.0 / 20), blk.S21[0].Magnitude, 3);
        Assert.Equal(-45 * Math.PI / 180, blk.S21[0].Phase, 3);
    }

    [Fact]
    public void InterpolatesBetweenPointsAndClampsOutside()
    {
        var blk = SParameterBlock.Parse("""
            # MHZ S RI R 50
            100 0 0  1.0 0  1.0 0  0 0
            300 0 0  0.5 0  0.5 0  0 0
            """);
        Assert.Equal(0.75, blk.At(200e6).s21.Real, 6);       // midpoint
        Assert.Equal(1.0, blk.At(1e6).s21.Real, 6);          // clamp low
        Assert.Equal(0.5, blk.At(10e9).s21.Real, 6);         // clamp high (no gain by extrapolation)
    }

    [Fact]
    public void IdealThroughHasIdentityAbcd()
    {
        var blk = SParameterBlock.Parse("""
            # GHZ S RI R 50
            1 0 0  1 0  1 0  0 0
            """);
        var (a, b, c, d) = blk.Abcd(1e9);
        Assert.Equal(1, a.Real, 4); Assert.Equal(0, b.Magnitude, 4);
        Assert.Equal(0, c.Magnitude, 4); Assert.Equal(1, d.Real, 4);
    }

    [Fact]
    public void AttachedLossyBlockReducesChannelS21()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        int net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 45, By = 20, Width = 0.3, NetId = net, Layer = 0 });

        double cleanS21 = ChannelExtractor.Extract(doc, net, 1e9, 1e9 + 1, 2)[0].S21.Magnitude;

        string tmp = Path.Combine(Path.GetTempPath(), "ds_conn.s2p");
        File.WriteAllText(tmp, """
            # GHZ S DB R 50
            0.1 -30 0  -3 -10  -3 -10  -30 0
            20  -30 0  -3 -80  -3 -80  -30 0
            """);
        doc.ChannelBlocks.Add(new ChannelBlock { NetId = net, FilePath = tmp });
        double withBlock = ChannelExtractor.Extract(doc, net, 1e9, 1e9 + 1, 2)[0].S21.Magnitude;
        File.Delete(tmp);

        Assert.True(withBlock < cleanS21 * 0.85,
            $"3 dB connector model had no effect: {cleanS21:F3} → {withBlock:F3}");
    }

    [Fact]
    public void MissingFileIsReportedNotFatal()
    {
        var blk = new ChannelBlock { NetId = 0, FilePath = "Z:\\does\\not\\exist.s2p" };
        Assert.Null(blk.Parsed());
        Assert.NotNull(blk.LoadError);
    }
}

public class OpenEmsExporterTests
{
    private static BoardDocument NetBoard(out int net)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 30, By = 20, Width = 0.3, NetId = net, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 30, Ay = 20, Bx = 45, By = 30, Width = 0.3, NetId = net, Layer = 2 });
        doc.Vias.Add(new ViaItem { X = 30, Y = 20, NetId = net, FromLayer = 0, ToLayer = 3, DiameterMm = 0.5 });
        return doc;
    }

    [Fact]
    public void ScriptContainsAllGeometryClasses()
    {
        using var doc = NetBoard(out int net);
        string py = DesignStudio.Export.OpenEmsExporter.Build(doc, net);
        Assert.Contains("openEMS", py);
        Assert.Contains("AddMaterial", py);          // dielectrics
        Assert.Contains("AddBox", py);               // axis-aligned copper
        Assert.Contains("AddLinPoly", py);           // the angled segment
        Assert.Contains("AddCylinder", py);          // via barrel
        Assert.Contains("AddLumpedPort(1", py);
        Assert.Contains("AddLumpedPort(2", py);
        Assert.Contains("plane L2", py);             // stackup planes exported
    }

    [Fact]
    public void DielectricConstantsComeFromMaterials()
    {
        using var doc = NetBoard(out int net);
        doc.Stackup[0].MaterialId = "megtron6";      // Dk ≈ 3.52 @ 10 GHz
        string py = DesignStudio.Export.OpenEmsExporter.Build(doc, net);
        Assert.Contains("epsilon=3.5", py);
    }

    [Fact]
    public void UnroutedNetThrows()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        int n = doc.AddNet("EMPTY");
        Assert.Throws<InvalidOperationException>(() => DesignStudio.Export.OpenEmsExporter.Build(doc, n));
    }

    [Fact]
    public void InvariantCultureNumbers()
    {
        using var doc = NetBoard(out int net);
        string py = DesignStudio.Export.OpenEmsExporter.Build(doc, net);
        Assert.DoesNotContain(",5", py.Split('\n').First(l => l.Contains("AddBox")));
    }
}
