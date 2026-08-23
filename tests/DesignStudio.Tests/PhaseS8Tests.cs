using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S8 — split-plane FDM PDN (E13) + electro-thermal IR drop (E14).
// Exit criterion: a split VDDQ Z(f) the rectangular model can't produce, and a
// high-current rail's droop + thermal.
// ============================================================================

public class PlanePdnFdmTests
{
    [Fact]
    public void FdmMatchesModalOnARectangle()
    {
        var fdm = PlanePdnFdm.Rectangle(40, 40, 0.1, 3.5, 0.005, (20, 20));
        var modal = new PlaneCavity(40, 40, 0.1, 3.5, 0.005,
                                    new[] { new PlaneCavity.Port(20, 20, "L") },
                                    Array.Empty<PlaneCavity.Decap>());
        foreach (double f in new[] { 1e7, 1e8 })
        {
            double zf = fdm.Zin(f).Magnitude;
            double zm = modal.ZMatrix(f)[0, 0].Magnitude;     // bare plane (no VRM either side)
            Assert.InRange(zf, zm * 0.95, zm * 1.05);
        }
    }

    [Fact]
    public void SplitPlaneRaisesImpedanceVersusSolid()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(40, 20);
        doc.SetCopperLayers(2);
        int gnd = doc.AddNet("GND");

        var solid = new PlaneShape { NetId = gnd, Layer = 0, Fill = { Rect(0, 0, 40, 20) } };
        var split = new PlaneShape { NetId = gnd, Layer = 0,
                                     Fill = { Rect(0, 0, 18, 20), Rect(22, 0, 40, 20) } };
        // bare plane (no VRM) so the comparison reflects the lost copper/capacitance,
        // not where a VRM heuristic happens to land
        var fSolid = PlanePdnFdm.FromPlane(doc, solid, (9, 10), withVrm: false)!;
        var fSplit = PlanePdnFdm.FromPlane(doc, split, (9, 10), withVrm: false)!;   // port on left island

        double zSolid = fSolid.Zin(1e7).Magnitude;
        double zSplit = fSplit.Zin(1e7).Magnitude;
        Assert.True(zSplit > 1.5 * zSolid,
            $"a split island should show higher Z (less copper): {zSplit * 1e3:F0} vs {zSolid * 1e3:F0} mΩ");
    }

    [Fact]
    public void DecapLowersFdmImpedanceNearSrf()
    {
        const double c = 100e-9, esl = 0.5e-9, esr = 0.01;
        double srf = 1.0 / (2 * Math.PI * Math.Sqrt(esl * c));
        var bare = PlanePdnFdm.Rectangle(40, 40, 0.1, 3.5, 0.005, (20, 20));
        var withDecap = PlanePdnFdm.Rectangle(40, 40, 0.1, 3.5, 0.005, (20, 20),
            new[] { (10.0, 20.0, c, esl, esr, "C1") });
        Assert.True(withDecap.Zin(srf).Magnitude < 0.2 * bare.Zin(srf).Magnitude);
    }

    [Fact]
    public void HandlesAnLShapedPlane()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(40, 40);
        doc.SetCopperLayers(2);
        int gnd = doc.AddNet("GND");
        // L-shape: full bottom band + left column
        var lshape = new PlaneShape { NetId = gnd, Layer = 0,
                                      Fill = { Rect(0, 0, 40, 12), Rect(0, 12, 12, 40) } };
        var fdm = PlanePdnFdm.FromPlane(doc, lshape, (6, 6));
        Assert.NotNull(fdm);
        Assert.True(fdm!.Zin(1e7).Magnitude > 0);
    }

    private static List<(double x, double y)> Rect(double x0, double y0, double x1, double y1)
        => new() { (x0, y0), (x1, y0), (x1, y1), (x0, y1) };
}

public class ElectroThermalTests
{
    private static BoardDocument RailBoard(out PlaneShape plane, out int vdd)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        doc.SetCopperLayers(2);
        doc.Stackup[0].CopperThicknessMm = 0.070;             // 2 oz power plane
        vdd = doc.AddNet("VDD");
        var src = new FootprintItem { RefDes = "VR1", X = 3, Y = 15 };
        src.Pads.Add(new PadItem { Name = "VOUT", NetId = vdd, ElectricalType = "power_out", W = 1, H = 1 });
        var load = new FootprintItem { RefDes = "U1", X = 37, Y = 15 };
        load.Pads.Add(new PadItem { Name = "VDD", NetId = vdd, ElectricalType = "power_in", W = 1, H = 1 });
        doc.Footprints.Add(src);
        doc.Footprints.Add(load);
        doc.NotifyChanged();
        plane = doc.AddPlane(vdd, 0, 0.3);
        return doc;
    }

    [Fact]
    public void HighCurrentRailHeatsAndDroopsMoreThanIsothermal()
    {
        using var doc = RailBoard(out var plane, out int vdd);
        var r = IrDrop.AnalyzeElectroThermal(doc, plane, new Dictionary<int, double> { [vdd] = 60.0 });
        Assert.True(r.Solved, r.Skipped);
        Assert.True(r.MaxDropV > 0);
        Assert.True(r.HotC > 25, $"plane should heat above ambient, got {r.HotC:F1} °C");
        Assert.True(r.MaxDropV > r.IsothermalDropV,
            $"ρ(T) should worsen droop: coupled {r.MaxDropV * 1e3:F1} !> isothermal {r.IsothermalDropV * 1e3:F1} mV");
        Assert.True(r.PeakCurrentDensityAmmm2 > 0);
    }

    [Fact]
    public void LowCurrentBarelyHeats()
    {
        using var doc = RailBoard(out var plane, out int vdd);
        var r = IrDrop.AnalyzeElectroThermal(doc, plane, new Dictionary<int, double> { [vdd] = 1.0 });
        Assert.True(r.Solved);
        Assert.InRange(r.HotC, 25, 40);                       // negligible self-heating
    }
}
