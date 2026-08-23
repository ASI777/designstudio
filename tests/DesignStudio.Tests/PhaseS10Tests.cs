using System.Text.Json;
using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S10 — HDI: blind / buried / stacked microvias (E11).
// Exit criterion: a stacked-microvia transition extracts S-parameters and the
// fab output lists the correct laser/mechanical drill spans.
// ============================================================================

public class HdiClassificationTests
{
    private static BoardDocument Board()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 20);
        doc.SetCopperLayers(4);
        return doc;
    }

    [Fact]
    public void ClassifiesViaFabricationTypes()
    {
        using var doc = Board();
        int n = doc.AddNet("N");
        var through = new ViaItem { X = 5, Y = 10, NetId = n, FromLayer = 0, ToLayer = 3, DrillMm = 0.3 };
        var blind = new ViaItem { X = 10, Y = 10, NetId = n, FromLayer = 0, ToLayer = 1, DrillMm = 0.3 };
        var buried = new ViaItem { X = 15, Y = 10, NetId = n, FromLayer = 1, ToLayer = 2, DrillMm = 0.3 };
        var micro = new ViaItem { X = 20, Y = 10, NetId = n, FromLayer = 0, ToLayer = 1, DrillMm = 0.1 };

        Assert.Equal(ViaType.Through, HdiVias.Resolve(doc, through));
        Assert.Equal(ViaType.Blind, HdiVias.Resolve(doc, blind));
        Assert.Equal(ViaType.Buried, HdiVias.Resolve(doc, buried));
        Assert.Equal(ViaType.Microvia, HdiVias.Resolve(doc, micro));   // small drill, 1 layer
    }

    [Fact]
    public void DetectsStackedMicrovias()
    {
        using var doc = Board();
        int n = doc.AddNet("N");
        for (int i = 0; i < 3; i++)
            doc.Vias.Add(new ViaItem { X = 20, Y = 10, NetId = n, FromLayer = i, ToLayer = i + 1, DrillMm = 0.1 });
        var stacks = HdiVias.Stacks(doc);
        Assert.Single(stacks);
        Assert.Equal(3, stacks[0].Count);
    }

    [Fact]
    public void BadMicroviaAspectIsFlagged()
    {
        using var doc = Board();                      // default 0.2 mm dielectric ⇒ aspect ~2.4
        int n = doc.AddNet("N");
        doc.Vias.Add(new ViaItem { X = 20, Y = 10, NetId = n, FromLayer = 0, ToLayer = 1, DrillMm = 0.1 });
        Assert.Contains(HdiVias.Check(doc), h => h.Rule == 25 && h.Message.Contains("aspect"));
    }

    [Fact]
    public void CleanHdiStackPasses()
    {
        using var doc = Board();
        for (int i = 0; i < doc.Stackup.Count; i++) doc.Stackup[i].DielectricHeightMm = 0.05;  // thin build-up
        int n = doc.AddNet("N");
        for (int i = 0; i < 3; i++)
            doc.Vias.Add(new ViaItem { X = 20, Y = 10, NetId = n, FromLayer = i, ToLayer = i + 1,
                                       DrillMm = 0.1, DiameterMm = 0.25 });
        Assert.Empty(HdiVias.Check(doc));             // aspect 0.85, annular 75 µm — all in spec
    }

    [Fact]
    public void ViaTypeRoundTripsThroughProjectIo()
    {
        using var doc = Board();
        int n = doc.AddNet("N");
        doc.Vias.Add(new ViaItem { X = 5, Y = 5, NetId = n, FromLayer = 0, ToLayer = 1,
                                   DrillMm = 0.1, Type = ViaType.Microvia });
        using var copy = ProjectIO.Deserialize(ProjectIO.Serialize(doc));
        Assert.Equal(ViaType.Microvia, copy.Vias[0].Type);
    }
}

public class HdiDrillAndExtractionTests
{
    [Fact]
    public void DrillSpanReportListsLaserAndMechanical()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(40, 20);
        doc.SetCopperLayers(4);
        int n = doc.AddNet("N");
        doc.Vias.Add(new ViaItem { X = 5, Y = 10, NetId = n, FromLayer = 0, ToLayer = 3, DrillMm = 0.3 });   // through
        doc.Vias.Add(new ViaItem { X = 20, Y = 10, NetId = n, FromLayer = 0, ToLayer = 1, DrillMm = 0.1 });  // microvia

        string report = DesignStudio.Export.FabExporter.DrillSpanReport(doc);
        Assert.Contains("LASER", report);
        Assert.Contains("L1-L2", report);             // microvia span (1-based)
        Assert.Contains("MECH", report);
        Assert.Contains("L1-L4", report);             // through span
    }

    [Fact]
    public void StackedMicroviaTransitionExtracts()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(50, 20);
        doc.SetCopperLayers(4);
        for (int i = 0; i < doc.Stackup.Count; i++)
        {
            doc.Stackup[i].MaterialId = "megtron6";
            doc.Stackup[i].DielectricHeightMm = 0.06;  // HDI build-up
        }
        int net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10, Bx = 20, By = 10, Width = 0.1, NetId = net, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 20, Ay = 10, Bx = 45, By = 10, Width = 0.1, NetId = net, Layer = 3 });
        for (int i = 0; i < 3; i++)                    // stacked microvias L0→L1→L2→L3
            doc.Vias.Add(new ViaItem { X = 20, Y = 10, NetId = net, FromLayer = i, ToLayer = i + 1, DrillMm = 0.1 });

        var sweep = ChannelExtractor.Extract(doc, net, 1e8, 2e10, 41, useMoM: true);
        Assert.Equal(41, sweep.Count);
        Assert.All(sweep, p => Assert.True(p.S21.Magnitude <= 1.001, "passive transition cannot have gain"));
        Assert.True(sweep[0].S21.Magnitude > 0.85, $"short HDI stack should be low-loss: {sweep[0].S21.Magnitude:F3}");
    }
}
