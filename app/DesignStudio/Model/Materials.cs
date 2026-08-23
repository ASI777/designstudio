using System.IO;
using System.Numerics;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DesignStudio.Model;

// ============================================================================
// 4.3 Laminate materials library with frequency-dependent Dk/Df.
//
// A material is a set of (frequency, Dk, Df) measurement points exactly as
// published in laminate datasheets; the library interpolates log-linearly in
// frequency between them. Stackup layers reference a material id; raw
// DielectricEr / LossTangent stay as the fallback so old projects and quick
// edits keep working.
//
// Built-ins cover the common ladder from cheap FR-4 to 100G laminates.
// Users add their own in %APPDATA%\DesignStudio\materials.json (same schema).
//
// Phase S2 (E2) adds, on top of the raw datapoints:
//   • a causal wideband-Debye (Djordjevic-Sarkar) dispersion model fitted to
//     the points, so Dk(f)/Df(f) used by the field/channel solvers obey
//     Kramers-Kronig (the log-linear interpolation above is *not* causal and
//     gives the wrong phase at >10 GHz — "wrong phase = wrong eye");
//   • copper surface-roughness loss models (Hammerstad and Huray/cannonball)
//     parameterised by an RMS roughness on the material.
// Both are consumed by MoM2D (E1) and ChannelExtractor.
// ============================================================================

public record MaterialPoint(
    [property: JsonPropertyName("f_ghz")] double FreqGHz,
    [property: JsonPropertyName("dk")] double Dk,
    [property: JsonPropertyName("df")] double Df);

public class MaterialDef
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("vendor")] public string Vendor { get; set; } = "";
    /// <summary>Measurement points, ascending frequency. At least one.</summary>
    [JsonPropertyName("points")] public List<MaterialPoint> Points { get; set; } = new();
    [JsonPropertyName("max_speed_gbps")] public double MaxSpeedGbps { get; set; }   // guidance only

    /// <summary>RMS copper-foil roughness (µm). 0 = treat as smooth. Typical:
    /// 0.3–0.5 (VLP/HVLP), 1.5–2.5 (standard reverse-treat), 5+ (electrodeposited).</summary>
    [JsonPropertyName("roughness_rq_um")] public double RoughnessRqUm { get; set; }
    /// <summary>"hammerstad" (default) or "huray". Selects the roughness loss model.</summary>
    [JsonPropertyName("roughness_model")] public string RoughnessModel { get; set; } = "hammerstad";

    public double DkAt(double fGHz) => Interp(fGHz, p => p.Dk);
    public double DfAt(double fGHz) => Interp(fGHz, p => p.Df);

    private DjordjevicSarkar? _causal;
    /// <summary>Causal wideband-Debye model fitted to the points (cached).</summary>
    public DjordjevicSarkar Causal => _causal ??= DjordjevicSarkar.Fit(Points);

    /// <summary>Causal Dk(f): real relative permittivity from the fitted model.</summary>
    public double DkCausalAt(double fGHz) => Causal.Dk(fGHz * 1e9);
    /// <summary>Causal Df(f) = ε″/ε′ from the fitted model.</summary>
    public double DfCausalAt(double fGHz) => Causal.LossTangent(fGHz * 1e9);
    /// <summary>Complex relative permittivity ε′ − jε″ at frequency.</summary>
    public Complex EpsComplexAt(double fGHz) => Causal.Eps(fGHz * 1e9);

    /// <summary>Surface-roughness loss correction factor Ksr(f) ≥ 1 for this
    /// material's foil; multiplies the smooth-copper conductor loss.</summary>
    public double RoughnessFactor(double fHz)
    {
        if (RoughnessRqUm <= 0) return 1.0;
        return RoughnessModel.Equals("huray", StringComparison.OrdinalIgnoreCase)
            ? SurfaceRoughness.HurayCannonball(fHz, RoughnessRqUm)
            : SurfaceRoughness.Hammerstad(fHz, RoughnessRqUm);
    }

    private double Interp(double fGHz, Func<MaterialPoint, double> sel)
    {
        if (Points.Count == 0) return double.NaN;
        if (Points.Count == 1 || fGHz <= Points[0].FreqGHz) return sel(Points[0]);
        if (fGHz >= Points[^1].FreqGHz) return sel(Points[^1]);
        for (int i = 0; i + 1 < Points.Count; i++)
        {
            if (fGHz > Points[i + 1].FreqGHz) continue;
            // log-linear in frequency: dielectric dispersion is ~log(f)
            double f0 = Math.Log(Math.Max(Points[i].FreqGHz, 1e-3));
            double f1 = Math.Log(Points[i + 1].FreqGHz);
            double u = (Math.Log(fGHz) - f0) / Math.Max(f1 - f0, 1e-9);
            return sel(Points[i]) + u * (sel(Points[i + 1]) - sel(Points[i]));
        }
        return sel(Points[^1]);
    }

    public override string ToString() => $"{Name} (Dk {DkAt(1):F2} @ 1 GHz)";
}

public static class MaterialsLibrary
{
    public static string UserFile => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "DesignStudio", "materials.json");

    private static List<MaterialDef>? _all;

    /// <summary>Built-ins + user file, cached. Call Reload() after editing the file.</summary>
    public static IReadOnlyList<MaterialDef> All => _all ??= Load();

    public static MaterialDef? Find(string? id)
        => string.IsNullOrEmpty(id) ? null : All.FirstOrDefault(m => m.Id == id);

    public static void Reload() => _all = null;

    private static List<MaterialDef> Load()
    {
        var list = Builtins();
        try
        {
            if (File.Exists(UserFile))
            {
                var user = JsonSerializer.Deserialize<List<MaterialDef>>(File.ReadAllText(UserFile));
                if (user != null)
                    foreach (var m in user)
                    {
                        list.RemoveAll(b => b.Id == m.Id);   // user overrides built-in
                        list.Add(m);
                    }
            }
        }
        catch { /* malformed user file must not kill the app; built-ins remain */ }
        return list;
    }

    // Published typical values from vendor datasheets (Dk/Df vs frequency).
    private static List<MaterialDef> Builtins() => new()
    {
        new MaterialDef
        {
            Id = "fr4-std", Name = "FR-4 standard Tg135", Vendor = "generic", MaxSpeedGbps = 3,
            RoughnessRqUm = 2.0, RoughnessModel = "hammerstad",
            Points =
            {
                new(0.001, 4.70, 0.018), new(0.1, 4.55, 0.019), new(1, 4.40, 0.020),
                new(5, 4.25, 0.022), new(10, 4.15, 0.024)
            }
        },
        new MaterialDef
        {
            Id = "fr4-370hr", Name = "Isola 370HR (low-loss FR-4)", Vendor = "Isola", MaxSpeedGbps = 8,
            RoughnessRqUm = 1.5, RoughnessModel = "hammerstad",
            Points =
            {
                new(0.001, 4.35, 0.021), new(1, 4.17, 0.021), new(5, 4.04, 0.021), new(10, 3.92, 0.025)
            }
        },
        new MaterialDef
        {
            Id = "megtron6", Name = "Panasonic Megtron 6", Vendor = "Panasonic", MaxSpeedGbps = 28,
            RoughnessRqUm = 0.5, RoughnessModel = "huray",
            Points =
            {
                new(1, 3.61, 0.004), new(5, 3.56, 0.004), new(10, 3.52, 0.005),
                new(25, 3.45, 0.006), new(50, 3.40, 0.007)
            }
        },
        new MaterialDef
        {
            Id = "tachyon100g", Name = "Isola Tachyon 100G", Vendor = "Isola", MaxSpeedGbps = 56,
            RoughnessRqUm = 0.3, RoughnessModel = "huray",
            Points =
            {
                new(1, 3.04, 0.0021), new(10, 3.02, 0.0021), new(25, 3.00, 0.0022), new(50, 2.98, 0.0024)
            }
        },
        new MaterialDef
        {
            Id = "ro4350b", Name = "Rogers RO4350B", Vendor = "Rogers", MaxSpeedGbps = 40,
            RoughnessRqUm = 0.4, RoughnessModel = "huray",
            Points =
            {
                new(1, 3.48, 0.0037), new(10, 3.48, 0.0037), new(24, 3.47, 0.0040), new(50, 3.46, 0.0044)
            }
        }
    };
}

// ============================================================================
// E2.1 Causal wideband-Debye dispersion (Djordjevic-Sarkar / Svensson model).
//
//   ε*(ω) = ε∞ + Δε/(ln10·(m2−m1)) · ln( (10^m2 + jf) / (10^m1 + jf) )
//
// A single such term has an almost-flat loss tangent across [10^m1, 10^m2]
// and the corresponding causal (Kramers-Kronig consistent) real-part rise
// toward low frequency — exactly the behaviour laminate datasheets show. We
// fix the decade bounds well outside the band (1 kHz … 1 THz) and fit ε∞ and
// Δε to one datasheet anchor so Dk and Df are reproduced exactly there, while
// the rest of the curve is physically causal.
//
// Reference: A.R. Djordjevic et al., "Wideband frequency-domain
// characterization of FR-4 and time-domain causality", IEEE Trans. EMC, 2001.
// ============================================================================

public sealed class DjordjevicSarkar
{
    public double EpsInf { get; }
    public double DeltaEps { get; }
    public double F1 { get; }   // lower bound, Hz
    public double F2 { get; }   // upper bound, Hz

    public DjordjevicSarkar(double epsInf, double deltaEps, double f1, double f2)
    { EpsInf = epsInf; DeltaEps = deltaEps; F1 = f1; F2 = f2; }

    private double Ln10 => Math.Log(10);
    private double Decades => Math.Log10(F2 / F1);

    /// <summary>Complex relative permittivity ε′ − jε″ at frequency f (Hz).</summary>
    public Complex Eps(double fHz)
    {
        // ε* = ε∞ + Δε/(ln10·decades) · ln((F2 + jf)/(F1 + jf))
        var num = new Complex(F2, fHz);
        var den = new Complex(F1, fHz);
        Complex term = Complex.Log(num / den) * (DeltaEps / (Ln10 * Decades));
        Complex eps = new Complex(EpsInf, 0) + term;
        // engineering convention ε′ − jε″ (loss is the negative imaginary part)
        return new Complex(eps.Real, -Math.Abs(eps.Imaginary));
    }

    public double Dk(double fHz) => Eps(fHz).Real;
    public double EpsImag(double fHz) => -Eps(fHz).Imaginary;      // ε″ ≥ 0
    public double LossTangent(double fHz)
    {
        var e = Eps(fHz);
        return e.Real > 0 ? -e.Imaginary / e.Real : 0.0;
    }

    /// <summary>
    /// Fit to datasheet points. Anchors at the point nearest 10 GHz (the band
    /// that matters for Gen4/DDR5), reproducing its Dk and Df exactly; decade
    /// bounds fixed at 1 kHz … 1 THz. Falls back gracefully for sparse data.
    /// </summary>
    public static DjordjevicSarkar Fit(IReadOnlyList<MaterialPoint> pts)
    {
        double f1 = 1e3, f2 = 1e12;
        if (pts == null || pts.Count == 0)
            return new DjordjevicSarkar(4.0, 0.0, f1, f2);

        // anchor: closest to 10 GHz, else the highest-frequency point
        MaterialPoint a = pts[0];
        double best = double.MaxValue;
        foreach (var p in pts)
        {
            double d = Math.Abs(Math.Log(Math.Max(p.FreqGHz, 1e-6)) - Math.Log(10.0));
            if (d < best) { best = d; a = p; }
        }
        double f0 = Math.Max(a.FreqGHz, 1e-6) * 1e9;
        double dk0 = a.Dk, df0 = a.Df;
        double epsImag0 = dk0 * df0;                       // ε″ = ε′·tanδ at anchor

        double decades = Math.Log10(f2 / f1);
        // ε″(f0) = Δε/(ln10·decades)·(atan(f0/f1) − atan(f0/f2))
        double k = (Math.Atan(f0 / f1) - Math.Atan(f0 / f2)) / (Math.Log(10) * decades);
        double deltaEps = k > 1e-12 ? epsImag0 / k : 0.0;
        // ε′(f0) = ε∞ + Δε/(2 ln10 decades)·ln((f2²+f0²)/(f1²+f0²))
        double rePart = deltaEps / (2 * Math.Log(10) * decades)
                        * Math.Log((f2 * f2 + f0 * f0) / (f1 * f1 + f0 * f0));
        double epsInf = dk0 - rePart;
        return new DjordjevicSarkar(epsInf, deltaEps, f1, f2);
    }
}

// ============================================================================
// E2.2 Copper surface-roughness loss models.
//
// At 32 GT/s the skin depth (≈0.4 µm at 16 GHz) is comparable to the foil
// tooth height, so the current crowds over a rougher-than-flat surface and
// the conductor loss is multiplied by Ksr(f) ≥ 1. Two standard models:
//   • Hammerstad-Jensen: a 2-parameter arctangent that saturates at 2× —
//     adequate to ~20 GHz, the classic default.
//   • Huray "snowball" (here the Simonovich cannonball parameterisation from
//     a single RMS roughness): physically grounded, does not artificially cap
//     at 2× and tracks measured loss past 40 GHz.
// ============================================================================

public static class SurfaceRoughness
{
    public const double SigmaCu = 5.8e7;                 // S/m
    private const double Mu0 = 4e-7 * Math.PI;

    /// <summary>Skin depth in copper (m).</summary>
    public static double SkinDepth(double fHz) => Math.Sqrt(1.0 / (Math.PI * fHz * Mu0 * SigmaCu));

    /// <summary>Hammerstad-Jensen roughness factor. Δ = RMS roughness (µm).</summary>
    public static double Hammerstad(double fHz, double rqUm)
    {
        if (rqUm <= 0 || fHz <= 0) return 1.0;
        double delta = SkinDepth(fHz);
        double ratio = rqUm * 1e-6 / delta;
        return 1.0 + (2.0 / Math.PI) * Math.Atan(1.4 * ratio * ratio);
    }

    /// <summary>
    /// Cannonball-Huray factor from a single RMS roughness. A 14-sphere
    /// (9-4-1) cannonball stack of radius r over a hexagonal-close-packed tile
    /// gives a relative-area coefficient Cr = 14·π·r²/Atile; here r ≈ Rq and
    /// Atile = (6r)², so Cr = 14π/36 ≈ 1.22 (Ksr saturates near 2.2×).
    ///   Ksr(f) = 1 + Cr / (1 + δ/r + δ²/(2r²))
    /// Reference: Huray et al., DesignCon 2010; L. Simonovich, "Cannonball-Huray".
    /// </summary>
    public static double HurayCannonball(double fHz, double rqUm)
    {
        if (rqUm <= 0 || fHz <= 0) return 1.0;
        double r = rqUm * 1e-6;                           // sphere radius ≈ Rq
        double cr = 14.0 * Math.PI / 36.0;                // relative-area coefficient
        double delta = SkinDepth(fHz);
        double x = delta / r;
        return 1.0 + cr / (1.0 + x + 0.5 * x * x);
    }
}

// ============================================================================
// E2.3 Kramers-Kronig causality check.
//
// A model whose ε″ is the Hilbert transform of (ε′ − ε∞) is causal. We verify
// the fitted Djordjevic-Sarkar model numerically: reconstruct ε′(ω₀) from ε″
// via the subtractive KK relation and compare to the model's own ε′. The
// residual should be a few percent (limited only by the quadrature grid),
// which is the test the roadmap calls for.
// ============================================================================

public static class Causality
{
    /// <summary>
    /// Max relative error between the model's ε′ and the KK-reconstruction of
    /// ε′ from ε″, sampled across [fLo, fHi] (Hz). Small ⇒ causal.
    /// ε′(ω₀) − ε∞ = (2/π) P∫₀^∞ ω·ε″(ω)/(ω²−ω₀²) dω
    /// </summary>
    public static double KramersKronigResidual(DjordjevicSarkar m, double fLo = 1e8, double fHi = 5e10,
                                               int grid = 4000, int probes = 12)
    {
        // dense log grid in angular frequency for the PV integral
        double wLoG = Math.Log(2 * Math.PI * m.F1 * 1e-1);
        double wHiG = Math.Log(2 * Math.PI * m.F2 * 1e1);
        var w = new double[grid];
        var epp = new double[grid];
        for (int i = 0; i < grid; i++)
        {
            w[i] = Math.Exp(wLoG + (wHiG - wLoG) * i / (grid - 1));
            epp[i] = m.EpsImag(w[i] / (2 * Math.PI));
        }

        double worst = 0;
        for (int p = 0; p < probes; p++)
        {
            double f0 = fLo * Math.Pow(fHi / fLo, p / (double)(probes - 1));
            double w0 = 2 * Math.PI * f0;
            // trapezoidal PV integral, skipping the singular cell at w≈w0
            double integ = 0;
            for (int i = 0; i + 1 < grid; i++)
            {
                double wa = w[i], wb = w[i + 1];
                if ((wa - w0) * (wb - w0) <= 0) continue;          // straddles pole: skip
                double fa = wa * epp[i] / (wa * wa - w0 * w0);
                double fb = wb * epp[i + 1] / (wb * wb - w0 * w0);
                integ += 0.5 * (fa + fb) * (wb - wa);
            }
            double reconstructed = m.EpsInf + (2.0 / Math.PI) * integ;
            double actual = m.Dk(f0);
            worst = Math.Max(worst, Math.Abs(reconstructed - actual) / Math.Max(actual, 1e-6));
        }
        return worst;
    }
}
