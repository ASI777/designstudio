using DesignStudio.Model;
using DesignStudio.Model.Advisor;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Layer-count recommendation (LayerPlanner + advisor set_layers) and custom
// board shapes (BoardOutline + BoardDocument + ProjectIO round-trip).
// ============================================================================

public class LayerPlannerTests
{
    private static CsComponent Part(string refDes, int pins)
    {
        var c = new CsComponent { Ref = refDes };
        for (int i = 0; i < pins; i++) c.Pins.Add(new CsPin { Pin = (i + 1).ToString() });
        return c;
    }

    [Fact]
    public void SmallTwoLayerJobStaysLow()
    {
        var s = new CircuitState();
        s.Board.CopperLayers = 2;
        s.Components.Add(Part("U1", 14));        // a small QFN
        for (int i = 0; i < 8; i++) s.Nets.Add(new CsNet { Name = $"SIG{i}" });

        var est = LayerPlanner.Estimate(s);
        Assert.True(est.RecommendedLayers <= 4, $"expected <=4, got {est.RecommendedLayers}");
        Assert.Equal(0, est.RecommendedLayers % 2);   // even count
    }

    [Fact]
    public void DenseBgaWithDdrAndManyRailsWantsManyLayers()
    {
        var s = new CircuitState();
        s.Board.CopperLayers = 4;
        s.Components.Add(Part("U1", 1156));      // big SoC BGA
        for (int i = 0; i < 600; i++) s.Nets.Add(new CsNet { Name = $"SIG{i}" });
        foreach (var v in new[] { "VDD_CORE", "VDDQ", "VPP", "V1P8", "V3V3", "GND" })
            s.Nets.Add(new CsNet { Name = v, Class = "power" });
        s.DdrLanes.Add(new CsDdrLane { Lane = "DQ0", Pass = true });
        s.SignalIntegrity.Add(new CsChannel { Net = "PCIE", MaskPass = true });

        var est = LayerPlanner.Estimate(s);
        Assert.True(est.RecommendedLayers >= 12, $"a dense DDR/BGA board should want many layers, got {est.RecommendedLayers}");
        Assert.Equal(0, est.RecommendedLayers % 2);
        Assert.True(est.PlaneLayers >= 2 && est.SignalLayers >= 4);
    }

    [Fact]
    public void AdvisorRecommendsMoreLayersWhenUnderProvisioned()
    {
        var s = new CircuitState();
        s.Board.CopperLayers = 2;                // far too few
        s.Components.Add(Part("U1", 900));
        for (int i = 0; i < 400; i++) s.Nets.Add(new CsNet { Name = $"S{i}" });
        s.DdrLanes.Add(new CsDdrLane { Lane = "DQ0", Pass = true });

        var rep = SiPiAdvisor.Recommend(s);
        var set = rep.Actions.FirstOrDefault(a => a.Op == AdviceOp.SetLayers);
        Assert.NotNull(set);
        Assert.True(int.Parse(set!.Value!) > 2);
    }

    [Fact]
    public void AdvisorDoesNotNagWhenLayersAreSufficient()
    {
        var s = new CircuitState();
        s.Board.CopperLayers = 50;               // plenty
        s.Components.Add(Part("U1", 256));
        for (int i = 0; i < 50; i++) s.Nets.Add(new CsNet { Name = $"S{i}" });

        var rep = SiPiAdvisor.Recommend(s);
        Assert.DoesNotContain(rep.Actions, a => a.Op == AdviceOp.SetLayers);
    }

    [Fact]
    public void SetLayersOpRoundTripsThroughTheContract()
    {
        var report = new AdviceReport("x",
            new[] { new AdviceAction(AdviceOp.SetLayers, "need more", Value: "24") }, System.Array.Empty<string>());
        var back = AdviceContract.Parse(AdviceContract.Serialize(report));
        Assert.Equal(AdviceOp.SetLayers, back.Actions[0].Op);
        Assert.Equal("24", back.Actions[0].Value);
    }
}

public class BoardOutlineTests
{
    [Fact]
    public void CircleBoundingBoxEqualsDiameter()
    {
        var poly = BoardOutline.Circle(40);
        var (_, _, w, h) = BoardOutline.BoundingBox(poly);
        Assert.Equal(40, w, 1);
        Assert.Equal(40, h, 1);
        // centre is inside, a far corner is outside
        Assert.True(BoardOutline.Contains(poly, 20, 20));
        Assert.False(BoardOutline.Contains(poly, 1, 1));
    }

    [Fact]
    public void SetBoardShapeCircleUpdatesDocumentAndBoundingBox()
    {
        using var doc = new BoardDocument();
        doc.SetBoardShape(BoardShape.Circle, 60);

        Assert.Equal(BoardShape.Circle, doc.Shape);
        Assert.NotNull(doc.OutlinePolygon);
        Assert.Equal(60, doc.BoardWidthMm, 1);
        Assert.Equal(60, doc.BoardHeightMm, 1);
        Assert.True(doc.EffectiveOutline().Count >= 3);   // polygon populated
    }

    [Fact]
    public void RectangleHasNoCustomOutline()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(100, 80);
        Assert.Equal(BoardShape.Rectangle, doc.Shape);
        Assert.Null(doc.OutlinePolygon);
        Assert.Equal(4, doc.EffectiveOutline().Count);   // synthesised rectangle
    }

    [Fact]
    public void PartOutsideACircleIsFlagged()
    {
        using var doc = new BoardDocument();
        doc.SetBoardShape(BoardShape.Circle, 40);
        doc.Footprints.Add(new FootprintItem { RefDes = "U1", X = 20, Y = 20 });   // centre: inside
        doc.Footprints.Add(new FootprintItem { RefDes = "U2", X = 1, Y = 1 });     // corner: outside the circle

        var outside = BoardOutline.OutsideOutline(doc);
        Assert.Contains(outside, m => m.Contains("U2"));
        Assert.DoesNotContain(outside, m => m.Contains("U1"));
    }

    [Fact]
    public void ComponentBodyCannotHangOffCircleEvenWhenItsOriginIsInside()
    {
        using var doc = new BoardDocument();
        doc.SetBoardShape(BoardShape.Circle, 40);
        var fp = new FootprintItem
        {
            RefDes = "U1", X = 20, Y = 20,
            Pads = new List<PadItem> { new() { W = 8, H = 8 } }
        };
        doc.Footprints.Add(fp);

        Assert.False(doc.IsFootprintInsideBoard(fp, fp.X, fp.Y, fp.RotationDeg));
        Assert.Contains(doc.OutsideOutline(), message => message.Contains("U1"));
    }

    [Fact]
    public void MoveFootprintLegalizesRotatedEdgePlacement()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(20, 20);
        var fp = new FootprintItem
        {
            RefDes = "J1",
            Pads = new List<PadItem> { new() { W = 8, H = 2 } }
        };
        doc.Footprints.Add(fp);

        doc.MoveFootprint(fp, 1, 1, 45);

        Assert.True(doc.IsFootprintInsideBoard(fp, fp.X, fp.Y, fp.RotationDeg));
        Assert.True(fp.X > 1 && fp.Y > 1, "the center should be moved inward by the rotated bounds");
    }

    [Fact]
    public void AutoPlaceUsesTheRealOutlineForFinalPositions()
    {
        using var doc = new BoardDocument();
        doc.SetBoardShape(BoardShape.Circle, 40);
        int net = doc.AddNet("N");
        for (int i = 0; i < 2; i++)
            doc.Footprints.Add(new FootprintItem
            {
                RefDes = $"U{i + 1}", X = i == 0 ? 1 : 39, Y = 20, RotationDeg = 30,
                Pads = new List<PadItem> { new() { W = 5, H = 5, NetId = net } }
            });

        AutoPlacer.Place(doc);

        Assert.All(doc.Footprints, fp =>
            Assert.True(doc.IsFootprintInsideBoard(fp, fp.X, fp.Y, fp.RotationDeg), fp.RefDes));
    }

    [Fact]
    public void OutlineSurvivesAProjectRoundTrip()
    {
        using var doc = new BoardDocument();
        doc.SetBoardShape(BoardShape.Circle, 50);
        int n = doc.OutlinePolygon!.Count;

        string json = ProjectIO.Serialize(doc);
        using var back = ProjectIO.Deserialize(json);

        Assert.Equal(BoardShape.Circle, back.Shape);
        Assert.NotNull(back.OutlinePolygon);
        Assert.Equal(n, back.OutlinePolygon!.Count);
        Assert.Equal(50, back.BoardWidthMm, 1);
    }
}
