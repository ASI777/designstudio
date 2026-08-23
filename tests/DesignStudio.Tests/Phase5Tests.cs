using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class CrosstalkTests
{
    [Fact]
    public void CloserGapMeansMoreCoupling()
    {
        double tight = Crosstalk.NextFraction(0.15, 0.2, 30);
        double loose = Crosstalk.NextFraction(0.60, 0.2, 30);
        Assert.True(tight > loose * 2);
    }

    [Fact]
    public void ShortRunsAreDiscounted()
    {
        Assert.True(Crosstalk.NextFraction(0.15, 0.2, 2) <
                    Crosstalk.NextFraction(0.15, 0.2, 40));
    }

    [Fact]
    public void LongTightParallelRunIsFlagged()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        int a = doc.AddNet("AGG");
        int b = doc.AddNet("VIC");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20.0, Bx = 55, By = 20.0, Width = 0.2, NetId = a, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20.3, Bx = 55, By = 20.3, Width = 0.2, NetId = b, Layer = 0 });
        doc.NotifyChanged();
        Assert.Contains(Crosstalk.Check(doc), r => r.Rule == 22);
    }

    [Fact]
    public void WellSeparatedTracesPass()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        int a = doc.AddNet("AGG");
        int b = doc.AddNet("VIC");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 10, Bx = 55, By = 10, Width = 0.2, NetId = a, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 14, Bx = 55, By = 14, Width = 0.2, NetId = b, Layer = 0 });
        doc.NotifyChanged();
        Assert.Empty(Crosstalk.Check(doc));
    }

    [Fact]
    public void PerpendicularTracesDoNotCouple()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(2);
        int a = doc.AddNet("A");
        int b = doc.AddNet("B");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 55, By = 20, Width = 0.2, NetId = a, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 30, Ay = 5, Bx = 30, By = 35, Width = 0.2, NetId = b, Layer = 0 });
        doc.NotifyChanged();
        Assert.Empty(Crosstalk.FindCouplings(doc));
    }
}

public class PdnTests
{
    [Fact]
    public void DecapResonanceLowersImpedanceAboveVrmRange()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(30, 30);
        doc.SetCopperLayers(4);
        int vdd = doc.AddNet("VDD");
        int gnd = doc.AddNet("GND");

        // bare net (no decaps) vs same net with one 100 nF MLCC
        var bare = PdnSweep(doc, vdd);
        var cap = new FootprintItem { RefDes = "C1", X = 15, Y = 15, LibName = "C_0402" };
        cap.Pads.Add(new PadItem { Name = "1", NetId = vdd, W = 0.5, H = 0.5 });
        cap.Pads.Add(new PadItem { Name = "2", NetId = gnd, W = 0.5, H = 0.5 });
        doc.Footprints.Add(cap);
        doc.NotifyChanged();
        var withCap = PdnSweep(doc, vdd);

        // around the MLCC series resonance (~10–30 MHz) impedance must drop
        double bareZ = bare.Where(p => p.FreqHz > 1e7 && p.FreqHz < 3e7).Min(p => p.ZOhm);
        double capZ = withCap.Where(p => p.FreqHz > 1e7 && p.FreqHz < 3e7).Min(p => p.ZOhm);
        Assert.True(capZ < bareZ / 2, $"decap had no effect: {bareZ:G3} → {capZ:G3} Ω");

        static List<PdnAnalyzer.Point> PdnSweep(BoardDocument d, int net)
        {
            // Analyze needs a current; bypass NetCurrents by sweeping manually via decaps+VRM:
            var decaps = PdnAnalyzer.FindDecaps(d, net, null);
            var pts = new List<PdnAnalyzer.Point>();
            for (int k = 0; k <= 40; k++)
            {
                double f = 1e6 * Math.Pow(1e3, k / 40.0);
                double w = 2 * Math.PI * f;
                System.Numerics.Complex y = 1 / new System.Numerics.Complex(0.001, w * 10e-9);
                foreach (var dc in decaps)
                    y += 1 / new System.Numerics.Complex(dc.EsrOhm, w * dc.EslH - 1 / (w * dc.C));
                pts.Add(new PdnAnalyzer.Point(f, (1 / y).Magnitude));
            }
            return pts;
        }
    }

    [Fact]
    public void FindDecapsRequiresBridgingPowerAndGround()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        int vdd = doc.AddNet("VDD");
        int gnd = doc.AddNet("GND");
        int sig = doc.AddNet("SIG");
        var c1 = new FootprintItem { RefDes = "C1" };          // proper decap
        c1.Pads.Add(new PadItem { Name = "1", NetId = vdd });
        c1.Pads.Add(new PadItem { Name = "2", NetId = gnd });
        var c2 = new FootprintItem { RefDes = "C2" };          // AC coupling cap, not a decap
        c2.Pads.Add(new PadItem { Name = "1", NetId = vdd });
        c2.Pads.Add(new PadItem { Name = "2", NetId = sig });
        var r1 = new FootprintItem { RefDes = "R1" };          // not a capacitor
        r1.Pads.Add(new PadItem { Name = "1", NetId = vdd });
        r1.Pads.Add(new PadItem { Name = "2", NetId = gnd });
        doc.Footprints.Add(c1); doc.Footprints.Add(c2); doc.Footprints.Add(r1);
        var decaps = PdnAnalyzer.FindDecaps(doc, vdd, null);
        Assert.Single(decaps);
        Assert.Equal("C1", decaps[0].RefDes);
    }
}

public class ThermalTests
{
    private static FootprintLibrary LibraryWith(string libName, double powerW, double? tjMax = null,
                                                double? thetaJa = 40)
    {
        var lib = new FootprintLibrary();
        lib.Items.Add(new FootprintDef
        {
            Name = libName,
            ElectricalJson = System.Text.Json.JsonSerializer.Serialize(new DsElectrical
            {
                Thermal = new DsThermal { MaxPowerW = powerW, ThetaJaCw = thetaJa, TjMaxC = tjMax }
            })
        });
        return lib;
    }

    [Fact]
    public void HotPartRaisesBoardTemperature()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        doc.SetCopperLayers(2);
        doc.Footprints.Add(new FootprintItem { RefDes = "U1", LibName = "SOC", X = 20, Y = 15 });
        doc.NotifyChanged();
        var r = Thermal.Analyze(doc, LibraryWith("SOC", 3.0));
        Assert.Null(r.Skipped);
        Assert.True(r.MaxBoardC > Thermal.AmbientC + 5, $"only {r.MaxBoardC:F1} °C for 3 W");
        Assert.InRange(r.HotX, 10, 30);   // hotspot near the part
    }

    [Fact]
    public void OverTjPartIsFlagged()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(30, 30);
        doc.SetCopperLayers(2);
        doc.Footprints.Add(new FootprintItem { RefDes = "U1", LibName = "HOT", X = 15, Y = 15 });
        doc.NotifyChanged();
        // 5 W at θja 60 °C/W → Tj ≈ 325 °C ≫ 125 °C limit
        var hits = Thermal.Check(doc, LibraryWith("HOT", 5.0, tjMax: 125, thetaJa: 60));
        Assert.Contains(hits, h => h.Rule == 23 && h.Message.Contains("U1"));
    }

    [Fact]
    public void NoDeclaredPowerMeansSkipped()
    {
        using var doc = new BoardDocument();
        doc.SetCopperLayers(2);
        Assert.NotNull(Thermal.Analyze(doc, null).Skipped);
    }
}
