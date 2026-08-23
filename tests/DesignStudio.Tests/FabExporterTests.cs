using DesignStudio.Export;
using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class FabExporterTests
{
    private static BoardDocument FourLayerDoc()
    {
        var doc = new BoardDocument();
        doc.SetCopperLayers(4);
        int gnd = doc.AddNet("GND");
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", LibName = "QFN-16", X = 20, Y = 20, Side = 0,
            Pads = new List<PadItem>
            {
                new() { Name = "1", X = -1, W = 0.8, H = 0.3, NetId = gnd },
            }
        });
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "J1", LibName = "PinHeader_1x02_P2.54", X = 50, Y = 20,
            Pads = new List<PadItem>
            {
                new() { Name = "1", W = 1.4, H = 1.4, ThroughHole = true, DrillMm = 0.9, NetId = gnd },
            }
        });
        doc.Traces.Add(new TraceItem { Ax = 10, Ay = 10, Bx = 20, By = 10, Width = 0.2, Layer = 0, NetId = gnd });
        doc.Traces.Add(new TraceItem { Ax = 10, Ay = 12, Bx = 20, By = 12, Width = 0.15, Layer = 2, NetId = gnd });
        // through via and a microvia between L1-L2
        doc.Vias.Add(new ViaItem { X = 30, Y = 10, DiameterMm = 0.6, DrillMm = 0.3, NetId = gnd, FromLayer = 0, ToLayer = 3 });
        doc.Vias.Add(new ViaItem { X = 32, Y = 10, DiameterMm = 0.3, DrillMm = 0.15, NetId = gnd, FromLayer = 0, ToLayer = 1 });
        return doc;
    }

    [Fact]
    public void CopperFileNamesCoverTheStackup()
    {
        using var doc = FourLayerDoc();
        Assert.Equal("b-F_Cu.gtl", FabExporter.CopperFileName(doc, "b", 0));
        Assert.Equal("b-In1_Cu.g2", FabExporter.CopperFileName(doc, "b", 1));
        Assert.Equal("b-In2_Cu.g3", FabExporter.CopperFileName(doc, "b", 2));
        Assert.Equal("b-B_Cu.gbl", FabExporter.CopperFileName(doc, "b", 3));
    }

    [Fact]
    public void CopperLayersContainTheRightObjects()
    {
        using var doc = FourLayerDoc();

        string top = FabExporter.GerberCopper(doc, 0);
        Assert.Contains("Copper,L1,Top", top);
        Assert.Contains("D01*", top);                 // trace draw
        Assert.Contains("G36*", top);                 // pad region
        Assert.Contains("D03*", top);                 // via flash

        string in1 = FabExporter.GerberCopper(doc, 1);
        Assert.Contains("Copper,L2,Inr", in1);
        // SMD pad (centre x=19, w=0.8 → vertex at 18.6) exists on top only;
        // the through-hole pad region appears on every copper layer.
        Assert.Contains("X18600000", top);
        Assert.DoesNotContain("X18600000", in1);
        Assert.Contains("G36*", in1);

        string in2 = FabExporter.GerberCopper(doc, 2);
        Assert.Contains("D01*", in2);                 // the L3 trace

        // the L1-L2 microvia must not flash on L3
        int microFlashes = CountOccurrences(in2, "X32000000Y10000000D03*");
        Assert.Equal(0, microFlashes);
        // ...but the through via does
        Assert.Contains("X30000000Y10000000D03*", in2);
    }

    [Fact]
    public void MaskOpensPadsButTentsVias()
    {
        using var doc = FourLayerDoc();
        string mask = FabExporter.GerberMask(doc, top: true);
        Assert.Contains("Soldermask,Top", mask);
        Assert.Contains("G36*", mask);                // pad openings
        Assert.DoesNotContain("D03*", mask);          // vias tented
    }

    [Fact]
    public void PasteOnlyForSmdPads()
    {
        using var doc = FourLayerDoc();
        string paste = FabExporter.GerberPaste(doc, top: true);
        // SMD pad region present; through-hole pad must not get paste
        Assert.Contains("G36*", paste);
        // TH pad sits at x=50: no region vertex near it
        Assert.DoesNotContain("X50700000", paste);
    }

    [Fact]
    public void DrillFilesSplitByViaSpan()
    {
        using var doc = FourLayerDoc();
        var files = FabExporter.ExcellonFiles(doc);

        var through = files.Single(f => f.span == (0, 3));
        Assert.Contains("X50.000Y20.000", through.content);   // TH pad hole
        Assert.Contains("X30.000Y10.000", through.content);   // through via
        Assert.DoesNotContain("X32.000", through.content);    // microvia not here

        var micro = files.Single(f => f.span == (0, 1));
        Assert.Contains("X32.000Y10.000", micro.content);
        Assert.Contains("T01C0.150", micro.content);
    }

    [Fact]
    public void ExportAllWritesTheFullPackage()
    {
        using var doc = FourLayerDoc();
        string dir = Path.Combine(Path.GetTempPath(), "ds_fab_" + Guid.NewGuid().ToString("N"));
        try
        {
            var result = FabExporter.ExportAll(doc, dir, "board");
            // 4 copper + 2 mask + 2 paste + edge + 2 drill + BOM + PnP + stackup drawing = 14
            // (coupons and backdrill files appear only when the board needs them)
            Assert.Equal(14, result.FilesWritten.Count);
            Assert.Contains(result.FilesWritten, f => f.EndsWith("-Stackup.txt"));
            Assert.All(result.FilesWritten, f => Assert.True(File.Exists(f), $"missing {f}"));

            string pnp = File.ReadAllText(result.FilesWritten.Single(f => f.EndsWith("-PnP.csv")));
            Assert.Contains("U1,20.000,20.000,0.0,Top", pnp);
        }
        finally
        {
            if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true);
        }
    }

    [Fact]
    public void BomGroupsByFootprint()
    {
        using var doc = FourLayerDoc();
        doc.Footprints.Add(new FootprintItem { RefDes = "U2", LibName = "QFN-16" });
        string bom = FabExporter.Bom(doc);
        Assert.Contains("2,\"U1 U2\",\"QFN-16\"", bom);
    }

    private static int CountOccurrences(string text, string needle)
    {
        int count = 0, idx = 0;
        while ((idx = text.IndexOf(needle, idx, StringComparison.Ordinal)) >= 0) { count++; idx += needle.Length; }
        return count;
    }
}
