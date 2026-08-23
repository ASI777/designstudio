using System.Numerics;
using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S3 — physics-based via model (E3 step 1).
// Exit criterion: via stub resonance within 10 % of measurement to 20 GHz.
//
// We validate against the analytic quarter-wave open-stub resonance
// f = c / (4·ℓ_stub·√εr), which is what measured suckouts track, and check
// that backdrilling removes it.
// ============================================================================

public class ViaModelTests
{
    private const double C0 = 2.99792458e8;

    // 12-layer board, 0.2 mm dielectric + 1 oz copper per layer, εr 4.4 (default).
    // Net routes on L0 and L2 (via transition); a through via to L11 leaves a stub.
    private static BoardDocument Board12(out int net, out ViaItem via, int viaTo = 11)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(80, 40);
        doc.SetCopperLayers(12);
        net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 20, By = 20, Width = 0.2, NetId = net, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 20, Ay = 20, Bx = 60, By = 20, Width = 0.2, NetId = net, Layer = 2 });
        via = new ViaItem { X = 20, Y = 20, NetId = net, FromLayer = 0, ToLayer = viaTo, DiameterMm = 0.6, DrillMm = 0.3 };
        doc.Vias.Add(via);
        return doc;
    }

    private static double ViaS21(ViaModel vm, double f)
    {
        var (A, B, C, D) = vm.Abcd(f);
        const double zr = 50;
        return (2 / (A + B / zr + C * zr + D)).Magnitude;
    }

    private static (double fHz, double mag) NotchInBand(ViaModel vm, double f0, double f1, int n = 1500)
    {
        double bestF = f0, bestM = double.MaxValue;
        for (int i = 0; i < n; i++)
        {
            double f = f0 + (f1 - f0) * i / (n - 1);
            double m = ViaS21(vm, f);
            if (m < bestM) { bestM = m; bestF = f; }
        }
        return (bestF, bestM);
    }

    [Fact]
    public void StubSuckoutFallsAtQuarterWaveResonance()
    {
        using var doc = Board12(out _, out var via);
        var vm = ViaModel.ForVia(doc, via, deepestUsedLayer: 2)!;
        Assert.False(double.IsNaN(vm.StubResonanceHz));
        Assert.InRange(vm.StubResonanceHz, 10e9, 20e9);          // 9-layer stub ⇒ in band

        var (fNotch, mag) = NotchInBand(vm, 3e9, 30e9);
        // the extracted insertion-loss notch must sit within 10 % of the
        // analytic open-stub quarter-wave frequency (the S3 exit criterion)
        Assert.InRange(fNotch, vm.StubResonanceHz * 0.90, vm.StubResonanceHz * 1.10);
        Assert.True(20 * Math.Log10(mag) < -6, $"suckout only {20 * Math.Log10(mag):F1} dB deep");
    }

    [Fact]
    public void ResonanceMatchesClosedFormQuarterWave()
    {
        using var doc = Board12(out _, out var via);
        var vm = ViaModel.ForVia(doc, via, 2)!;
        double er = 4.4;                                          // default dielectric
        double analytic = C0 / (4 * vm.StubLengthMm * 1e-3 * Math.Sqrt(er));
        Assert.InRange(vm.StubResonanceHz, analytic * 0.97, analytic * 1.03);
    }

    [Fact]
    public void BackdrillRemovesTheSuckout()
    {
        using var doc = Board12(out _, out var via);
        var full = ViaModel.ForVia(doc, via, 2)!;
        via.BackdrillToLayer = 3;                                 // remove barrel below L3
        var drilled = ViaModel.ForVia(doc, via, 2)!;

        double fullDepth = -20 * Math.Log10(NotchInBand(full, 3e9, 20e9).mag);
        double drilledDepth = -20 * Math.Log10(NotchInBand(drilled, 3e9, 20e9).mag);
        Assert.True(fullDepth > 10, $"full stub should suck out hard ({fullDepth:F1} dB)");
        Assert.True(drilledDepth < 3, $"backdrilled via should be flat in band ({drilledDepth:F1} dB)");
    }

    [Fact]
    public void DeeperStubResonatesLower()
    {
        using var doc = Board12(out _, out var via);
        double shallowExit = ViaModel.ForVia(doc, via, 4)!.StubResonanceHz;   // 7-layer stub
        double deepExit = ViaModel.ForVia(doc, via, 1)!.StubResonanceHz;      // 10-layer stub
        Assert.True(deepExit < shallowExit,
            $"longer stub must resonate lower: {deepExit / 1e9:F1} !< {shallowExit / 1e9:F1} GHz");
    }

    [Fact]
    public void FullyUsedThroughViaHasNoStubAndStaysFlat()
    {
        using var doc = Board12(out _, out var via);
        var vm = ViaModel.ForVia(doc, via, deepestUsedLayer: 11)!;   // routed all the way down
        Assert.True(double.IsNaN(vm.StubResonanceHz));
        Assert.True(vm.StubLengthMm < 1e-6);
        // no resonance ⇒ no deep notch anywhere in band
        var (_, mag) = NotchInBand(vm, 1e9, 20e9);
        Assert.True(mag > 0.6, $"through via should pass: |S21| min {mag:F3}");
    }

    [Fact]
    public void AntipadSetsViaImpedance()
    {
        using var doc = Board12(out _, out var via);
        double zAuto = ViaModel.ForVia(doc, via, 2)!.Z0ViaOhm;
        via.AntipadMm = 1.6;                                     // wider clearance ⇒ higher Z
        double zWide = ViaModel.ForVia(doc, via, 2)!.Z0ViaOhm;
        Assert.True(zWide > zAuto, $"wider antipad should raise Z ({zWide:F1} !> {zAuto:F1} Ω)");
    }
}

public class ChannelViaIntegrationTests
{
    [Fact]
    public void ChannelInsertionLossShowsViaSuckout()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(80, 40);
        doc.SetCopperLayers(12);
        doc.Stackup[0].MaterialId = "megtron6";
        int net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 20, By = 20, Width = 0.2, NetId = net, Layer = 0 });
        doc.Traces.Add(new TraceItem { Ax = 20, Ay = 20, Bx = 70, By = 20, Width = 0.2, NetId = net, Layer = 2 });
        doc.Vias.Add(new ViaItem { X = 20, Y = 20, NetId = net, FromLayer = 0, ToLayer = 11, DiameterMm = 0.6, DrillMm = 0.3 });

        var vm = ViaModel.ForVia(doc, doc.Vias[0], 2)!;
        double fres = vm.StubResonanceHz;
        var sweep = ChannelExtractor.Extract(doc, net, 1e9, 30e9, 121, useMoM: true);

        // find the deepest dip and confirm it sits near the via stub resonance
        var dip = sweep.OrderBy(p => p.S21.Magnitude).First();
        Assert.InRange(dip.FreqHz, fres * 0.85, fres * 1.15);
        Assert.True(20 * Math.Log10(dip.S21.Magnitude) < -4,
            $"channel should show the via suckout near {fres / 1e9:F1} GHz");
    }

    [Fact]
    public void BackdrilledChannelIsFlatter()
    {
        BoardDocument Make(bool backdrill)
        {
            var doc = new BoardDocument();
            doc.SetBoardSize(80, 40);
            doc.SetCopperLayers(12);
            doc.Stackup[0].MaterialId = "megtron6";
            int net = doc.AddNet("SIG");
            doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 20, By = 20, Width = 0.2, NetId = net, Layer = 0 });
            doc.Traces.Add(new TraceItem { Ax = 20, Ay = 20, Bx = 70, By = 20, Width = 0.2, NetId = net, Layer = 2 });
            doc.Vias.Add(new ViaItem
            {
                X = 20, Y = 20, NetId = net, FromLayer = 0, ToLayer = 11, DiameterMm = 0.6, DrillMm = 0.3,
                BackdrillToLayer = backdrill ? 3 : -1
            });
            return doc;
        }
        using var full = Make(false);
        using var drilled = Make(true);
        int n = full.Nets[0].Id;
        double fullMin = ChannelExtractor.Extract(full, n, 1e9, 25e9, 101, useMoM: true).Min(p => p.S21.Magnitude);
        double drilledMin = ChannelExtractor.Extract(drilled, n, 1e9, 25e9, 101, useMoM: true).Min(p => p.S21.Magnitude);
        Assert.True(drilledMin > fullMin, "backdrilling should lift the worst-case insertion loss");
    }
}

public class ViaAntipadIoTests
{
    [Fact]
    public void AntipadRoundTripsThroughProjectIo()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(4);
        int net = doc.AddNet("SIG");
        doc.Vias.Add(new ViaItem
        {
            X = 10, Y = 10, NetId = net, FromLayer = 0, ToLayer = 3,
            DiameterMm = 0.5, DrillMm = 0.25, AntipadMm = 0.95
        });
        using var copy = ProjectIO.Deserialize(ProjectIO.Serialize(doc));
        Assert.Single(copy.Vias);
        Assert.Equal(0.95, copy.Vias[0].AntipadMm, 6);
    }
}
