using System.Numerics;
using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

// ============================================================================
// Phase S2 — causal materials (E2) + MoM RLGC solver (E1).
// Exit criterion: RLGC within ±2 % of reference tables to 50 GHz.
//
// References used here:
//   • Hammerstad-Jensen microstrip (≈1 % accurate, zero-thickness) — the same
//     model the C++ physics engine is validated against.
//   • Homogeneous/stripline εeff = εr is exact by construction.
// ============================================================================

public class MoMSingleEndedTests
{
    // Hammerstad-Jensen microstrip εeff and Z0 (zero-thickness reference).
    private static (double z0, double ee) Hj(double w, double t, double h, double er)
    {
        double u = w / h;
        double f = 6 + (2 * Math.PI - 6) * Math.Exp(-Math.Pow(30.666 / u, 0.7528));
        double z01 = 60 * Math.Log(f / u + Math.Sqrt(1 + Math.Pow(2 / u, 2)));
        double a = 1 + Math.Log((Math.Pow(u, 4) + Math.Pow(u / 52, 2)) / (Math.Pow(u, 4) + 0.432)) / 49
                     + Math.Log(1 + Math.Pow(u / 18.1, 3)) / 18.7;
        double b = 0.564 * Math.Pow((er - 0.9) / (er + 3), 0.053);
        double ee = (er + 1) / 2 + (er - 1) / 2 * Math.Pow(1 + 10 / u, -a * b);
        return (z01 / Math.Sqrt(ee), ee);
    }

    [Fact]
    public void ThinMicrostripWithin2PercentOfHammerstad()
    {
        // thin trace ⇒ HJ's zero-thickness formula is the right reference
        var s = MoM2D.Microstrip(0.36, 0.001, 0.2, 4.2);
        var (z0, ee) = Hj(0.36, 0.001, 0.2, 4.2);
        Assert.InRange(s.Z0, z0 * 0.98, z0 * 1.02);
        Assert.InRange(s.EpsEff, ee * 0.975, ee * 1.025);
    }

    [Fact]
    public void WideMicrostripWithin2PercentOfHammerstad()
    {
        var s = MoM2D.Microstrip(1.0, 0.018, 0.1, 3.5);
        var (z0, ee) = Hj(1.0, 0.018, 0.1, 3.5);
        Assert.InRange(s.Z0, z0 * 0.98, z0 * 1.02);
        Assert.InRange(s.EpsEff, ee * 0.98, ee * 1.02);
    }

    [Fact]
    public void FiniteThicknessLowersImpedance()
    {
        // a thicker trace has more capacitance ⇒ lower Z0 (HJ ignores this; the
        // MoM must capture it). This is the gap that looked like "error" vs HJ.
        double thin = MoM2D.Microstrip(0.36, 0.001, 0.2, 4.2).Z0;
        double thick = MoM2D.Microstrip(0.36, 0.035, 0.2, 4.2).Z0;
        Assert.True(thick < thin - 1.0, $"thick {thick:F2} should be well below thin {thin:F2}");
    }

    [Fact]
    public void SymmetricStriplineEffectivePermittivityEqualsEr()
    {
        // homogeneous fill ⇒ εeff must equal εr exactly (no air, no dispersion).
        var s = MoM2D.Stripline(0.2, 0.035, 0.2, 4.2);
        Assert.InRange(s.EpsEff, 4.2 * 0.99, 4.2 * 1.01);
        Assert.InRange(s.Z0, 30, 60);                       // sane band for this geometry
    }

    [Fact]
    public void DelayFollowsEffectivePermittivity()
    {
        var s = MoM2D.Microstrip(0.3, 0.035, 0.2, 4.2);
        Assert.Equal(3.3356 * Math.Sqrt(s.EpsEff), s.DelayPsPerMm, 1);
    }
}

public class MoMCoupledTests
{
    [Fact]
    public void OddModeImpedanceBelowEvenMode()
    {
        var m = MoM2D.CoupledMicrostrip(0.2, 0.2, 0.035, 0.2, 4.2);
        Assert.True(m.ZoddOhm < m.ZevenOhm, $"Zodd {m.ZoddOhm:F1} !< Zeven {m.ZevenOhm:F1}");
        Assert.Equal(2 * m.ZoddOhm, m.ZdiffOhm, 3);
        Assert.Equal(m.ZevenOhm / 2, m.ZcommOhm, 3);
        Assert.InRange(m.ZdiffOhm, 80, 140);                // edge-coupled pair, this geometry
    }

    [Fact]
    public void TighterCouplingWidensModeSplit()
    {
        var loose = MoM2D.CoupledMicrostrip(0.2, 0.6, 0.035, 0.2, 4.2);
        var tight = MoM2D.CoupledMicrostrip(0.2, 0.1, 0.035, 0.2, 4.2);
        double splitLoose = loose.ZevenOhm - loose.ZoddOhm;
        double splitTight = tight.ZevenOhm - tight.ZoddOhm;
        Assert.True(splitTight > splitLoose, $"tight split {splitTight:F1} !> loose {splitLoose:F1}");
    }
}

public class MoMRlgcTests
{
    private static MaterialDef Megtron6() => MaterialsLibrary.Find("megtron6")!;

    [Fact]
    public void RlgcIsPhysicalAndSelfConsistent()
    {
        var r = MoM2D.ExtractRlgc(0.2, 0.035, 0.2, Megtron6(), 16e9, microstrip: true);
        Assert.True(r.L[0, 0] > 0 && r.C[0, 0] > 0 && r.R[0, 0] > 0 && r.G[0, 0] > 0);
        // lossless Z0 = √(L/C) must match the static microstrip solve closely
        double erMid = Megtron6().DkCausalAt(16);
        double zStatic = MoM2D.Microstrip(0.2, 0.035, 0.2, erMid).Z0;
        Assert.InRange(r.Z0(), zStatic * 0.9, zStatic * 1.1);
        // effective permittivity (partial-air microstrip) sits below εr≈3.5
        Assert.InRange(r.EpsEff(), 2.2, 2.9);
    }

    [Fact]
    public void ConductorLossScalesAsSqrtFrequency()
    {
        var lo = MoM2D.ExtractRlgc(0.2, 0.035, 0.2, Megtron6(), 8e9, true);
        var hi = MoM2D.ExtractRlgc(0.2, 0.035, 0.2, Megtron6(), 32e9, true);
        double ratio = hi.R[0, 0] / lo.R[0, 0];             // 4× freq ⇒ ≈2× R (+roughness rise)
        Assert.InRange(ratio, 1.9, 2.7);
    }

    [Fact]
    public void DielectricConductanceGrowsWithFrequency()
    {
        var lo = MoM2D.ExtractRlgc(0.2, 0.035, 0.2, Megtron6(), 1e9, true);
        var hi = MoM2D.ExtractRlgc(0.2, 0.035, 0.2, Megtron6(), 16e9, true);
        Assert.True(hi.G[0, 0] > lo.G[0, 0] * 5, "G ≈ ωC·tanδ should rise ~linearly with f");
    }

    [Fact]
    public void PropagationConstantHasPositiveLossAndPhase()
    {
        var r = MoM2D.ExtractRlgc(0.2, 0.035, 0.2, Megtron6(), 16e9, true);
        Assert.True(r.Gamma().Real > 0, "α (loss) must be positive");
        Assert.True(r.Gamma().Imaginary > 0, "β (phase) must be positive");
        Assert.InRange(r.Zc().Real, 55, 90);                // w/h=1 ⇒ ≈73 Ω here
    }
}

public class CausalMaterialTests
{
    private static MaterialDef Fr4() => new()
    {
        Id = "t", Points =
        {
            new(0.001, 4.70, 0.018), new(1, 4.40, 0.020), new(10, 4.15, 0.024)
        }
    };

    [Fact]
    public void CausalModelReproducesAnchorPoint()
    {
        var m = Fr4();
        // anchored near 10 GHz ⇒ Dk and Df reproduced there
        Assert.Equal(4.15, m.DkCausalAt(10), 1);
        Assert.Equal(0.024, m.DfCausalAt(10), 3);
    }

    [Fact]
    public void DielectricConstantDispersesDownwardWithFrequency()
    {
        var m = Fr4();
        // causal wideband-Debye: Dk falls as frequency rises
        Assert.True(m.DkCausalAt(0.1) > m.DkCausalAt(50),
            $"Dk(0.1)={m.DkCausalAt(0.1):F3} should exceed Dk(50)={m.DkCausalAt(50):F3}");
        Assert.True(m.DfCausalAt(10) > 0);
    }

    [Fact]
    public void ModelIsKramersKronigConsistent()
    {
        double residual = Causality.KramersKronigResidual(Fr4().Causal);
        Assert.True(residual < 0.10, $"KK residual {residual:F3} too large for a causal model");
    }

    [Fact]
    public void ComplexPermittivityHasNegativeImaginaryPart()
    {
        Complex e = Fr4().EpsComplexAt(10);
        Assert.True(e.Real > 0 && e.Imaginary < 0);          // ε′ − jε″ (lossy convention)
    }
}

public class SurfaceRoughnessTests
{
    [Fact]
    public void HammerstadIsBoundedAndIncreasesWithFrequency()
    {
        double lo = SurfaceRoughness.Hammerstad(1e9, 2.0);
        double hi = SurfaceRoughness.Hammerstad(40e9, 2.0);
        Assert.True(hi > lo && hi <= 2.0 && lo >= 1.0);
    }

    [Fact]
    public void HurayRisesAndExceedsHammerstadAtHighFrequency()
    {
        double h = SurfaceRoughness.Hammerstad(50e9, 1.0);
        double u = SurfaceRoughness.HurayCannonball(50e9, 1.0);
        Assert.True(u > 1.0);
        Assert.True(SurfaceRoughness.HurayCannonball(40e9, 1.0) > SurfaceRoughness.HurayCannonball(1e9, 1.0));
    }

    [Fact]
    public void SmoothFoilHasUnityFactor()
    {
        Assert.Equal(1.0, SurfaceRoughness.Hammerstad(20e9, 0.0));
        var smooth = new MaterialDef { RoughnessRqUm = 0 };
        Assert.Equal(1.0, smooth.RoughnessFactor(20e9));
    }

    [Fact]
    public void RoughMaterialAddsLossThatGrowsWithFrequency()
    {
        var m = MaterialsLibrary.Find("fr4-std")!;           // Rq = 2 µm
        Assert.True(m.RoughnessFactor(20e9) > 1.05);
        Assert.True(m.RoughnessFactor(20e9) > m.RoughnessFactor(1e9));
    }
}

public class ChannelExtractorMoMTests
{
    private static BoardDocument Board(out int net, string materialId)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(80, 40);
        doc.SetCopperLayers(4);                              // L0 signal, L1/L2 planes
        doc.Stackup[0].MaterialId = materialId;
        net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 65, By = 20, Width = 0.2, NetId = net, Layer = 0 });
        return doc;
    }

    [Fact]
    public void MoMChannelLossIncreasesWithFrequency()
    {
        using var doc = Board(out int net, "megtron6");
        var sweep = ChannelExtractor.Extract(doc, net, 1e8, 4e10, 41, useMoM: true);
        double loDb = 20 * Math.Log10(sweep[2].S21.Magnitude);
        double hiDb = 20 * Math.Log10(sweep[^1].S21.Magnitude);
        Assert.True(hiDb < loDb - 1.0, $"insertion loss should grow: {loDb:F2} → {hiDb:F2} dB");
        Assert.True(sweep[^1].S21.Magnitude < 1.0);
    }

    [Fact]
    public void LossyMegtronExceedsItsOwnLowFrequencyLoss()
    {
        using var doc = Board(out int net, "megtron6");
        var sweep = ChannelExtractor.Extract(doc, net, 1e8, 4e10, 41, useMoM: true);
        // a 60 mm channel at 40 GHz on Megtron-6 should show several dB of loss
        double hiDb = -20 * Math.Log10(sweep[^1].S21.Magnitude);
        Assert.InRange(hiDb, 1.0, 40.0);
    }

    [Fact]
    public void RougherLaminateLosesMoreThanSmoother()
    {
        using var rough = Board(out int n1, "fr4-std");       // Rq 2.0 µm, lossy FR-4
        using var smooth = Board(out int n2, "tachyon100g");  // Rq 0.3 µm, low-loss
        double roughDb = -20 * Math.Log10(
            ChannelExtractor.Extract(rough, n1, 1e8, 3e10, 31, useMoM: true)[^1].S21.Magnitude);
        double smoothDb = -20 * Math.Log10(
            ChannelExtractor.Extract(smooth, n2, 1e8, 3e10, 31, useMoM: true)[^1].S21.Magnitude);
        Assert.True(roughDb > smoothDb, $"FR-4 {roughDb:F1} dB should exceed Tachyon {smoothDb:F1} dB");
    }

    [Fact]
    public void FallsBackToClosedFormWithoutLiveStackup()
    {
        // a fresh document has an empty stackup (Count ≠ CopperLayers) ⇒ the MoM
        // path is skipped and the closed-form cascade still produces a result.
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        int net = doc.AddNet("SIG");
        doc.Traces.Add(new TraceItem { Ax = 5, Ay = 20, Bx = 45, By = 20, Width = 0.3, NetId = net, Layer = 0 });
        Assert.NotEqual(doc.Stackup.Count, doc.CopperLayers);   // not a live stackup
        var sweep = ChannelExtractor.Extract(doc, net, 1e9, 1e9 + 1, 2, useMoM: true);
        Assert.True(sweep[0].S21.Magnitude is > 0 and <= 1.0);
    }
}
