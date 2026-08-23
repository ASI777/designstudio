using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class SpatialIndexTests
{
    [Fact]
    public void QueryFindsOnlyNearbyItems()
    {
        var idx = new SpatialIndex<int>(0, 0, 100, 100);
        for (int i = 0; i < 100; i++)
            idx.Insert(new BBox(i, i, i + 0.5, i + 0.5), i);
        var near = idx.Query(10, 10, 2);
        Assert.Contains(10, near);
        Assert.DoesNotContain(50, near);
        Assert.True(near.Count < 10);
    }

    [Fact]
    public void StraddlingItemsAreStillFound()
    {
        var idx = new SpatialIndex<string>(0, 0, 100, 100);
        idx.Insert(new BBox(49, 49, 51, 51), "centre");     // straddles all midlines
        for (int i = 0; i < 20; i++) idx.Insert(new BBox(i, 0, i + 0.4, 0.4), $"i{i}");
        Assert.Contains("centre", idx.Query(50, 50, 1));
    }

    [Fact]
    public void PadIndexResolvesNearestPin()
    {
        using var doc = new BoardDocument();
        var fp = new FootprintItem { X = 10, Y = 10 };
        fp.Pads.Add(new PadItem { Name = "1", X = 0, Y = 0, W = 0.5, H = 0.5 });
        fp.Pads.Add(new PadItem { Name = "2", X = 0, Y = 0.8, W = 0.5, H = 0.5 });
        doc.Footprints.Add(fp);
        doc.NotifyChanged();
        var hit = doc.FindPad(10, 10.75, 0.3);
        Assert.NotNull(hit);
        Assert.Equal("2", hit!.Value.pad.Name);   // nearest, not first
    }
}

public class MaterialsTests
{
    [Fact]
    public void BuiltinsLoadAndInterpolate()
    {
        var m6 = MaterialsLibrary.Find("megtron6");
        Assert.NotNull(m6);
        double dk1 = m6!.DkAt(1), dk25 = m6.DkAt(25);
        Assert.True(dk1 > dk25);                    // dispersion: Dk falls with f
        double dk7 = m6.DkAt(7);
        Assert.InRange(dk7, dk25, dk1);             // interpolated between points
    }

    [Fact]
    public void StackupLayerUsesMaterialCurve()
    {
        var layer = new StackupLayer { DielectricEr = 4.4, MaterialId = "tachyon100g" };
        Assert.InRange(layer.ErAt(10), 2.9, 3.1);   // material wins over raw Er
        layer.MaterialId = "";
        Assert.Equal(4.4, layer.ErAt(10), 3);       // fallback to raw value
    }

    [Fact]
    public void LowLossMaterialImprovesImpedanceVelocity()
    {
        // lower Dk → faster propagation
        Assert.True(ImpedanceEngine.StriplineDelayPsPerMm(3.0) <
                    ImpedanceEngine.StriplineDelayPsPerMm(4.4));
    }
}

public class PlaneShapeTests
{
    [Fact]
    public void PlaneFillsBoardAndAvoidsOtherNetCopper()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(50, 40);
        doc.SetCopperLayers(2);
        int gnd = doc.AddNet("GND");
        int sig = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 10, Ay = 20, Bx = 40, By = 20, Width = 0.3, NetId = sig, Layer = 0 });
        doc.NotifyChanged();

        var plane = doc.AddPlane(gnd, 0, 0.3);
        doc.PlanesUpToDate();

        Assert.True(plane.Fill.Count >= 1, "plane produced no polygons");
        double area = plane.Fill.Sum(PlaneGenerator.SignedArea);
        Assert.True(area > 50 * 40 * 0.8, $"net area {area:F0} mm² too small");
        Assert.True(area < 50 * 40, "plane cannot exceed the board");
        // the signal trace must have carved a void: either a hole polygon or a split outline
        Assert.True(plane.Fill.Any(p => PlaneGenerator.SignedArea(p) < 0) || plane.Fill.Count > 1,
            "no void around the other-net trace");
    }

    [Fact]
    public void SameNetCopperIsNotCarvedOut()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(30, 30);
        doc.SetCopperLayers(2);
        int gnd = doc.AddNet("GND");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15, Bx = 25, By = 15, Width = 0.3, NetId = gnd, Layer = 0 });
        doc.NotifyChanged();
        var plane = doc.AddPlane(gnd, 0, 0.3);
        doc.PlanesUpToDate();
        Assert.Single(plane.Fill);                  // solid sheet, no voids
        Assert.True(PlaneGenerator.SignedArea(plane.Fill[0]) > 0);
    }

    [Fact]
    public void PlaneRegeneratesWhenBoardChanges()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(30, 30);
        doc.SetCopperLayers(2);
        int gnd = doc.AddNet("GND");
        int sig = doc.AddNet("SIG");
        var plane = doc.AddPlane(gnd, 0, 0.3);
        doc.PlanesUpToDate();
        int polysBefore = plane.Fill.Count;

        Assert.Equal(1, polysBefore);               // empty board → solid sheet
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 15, Bx = 25, By = 15, Width = 0.5, NetId = sig, Layer = 0 });
        doc.NotifyChanged();
        doc.PlanesUpToDate();
        Assert.True(plane.Fill.Count > 1 || plane.Fill.Any(p => PlaneGenerator.SignedArea(p) < 0),
            "fill did not regenerate around the new trace");
    }

    [Fact]
    public void GerberContainsPlaneRegions()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(20, 20);
        doc.SetCopperLayers(2);
        int gnd = doc.AddNet("GND");
        doc.AddPlane(gnd, 0, 0.3);
        string gerber = DesignStudio.Export.FabExporter.GerberCopper(doc, 0);
        Assert.Contains("G36*", gerber);
        Assert.Contains("%LPD*%", gerber);
    }
}
