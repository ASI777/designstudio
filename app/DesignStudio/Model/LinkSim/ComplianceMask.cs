namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E5. Compliance masks & jitter — turn the statistical eye into a per-standard
// pass/fail, attachable to DRC.
//
// A mask here is the inner keep-out the eye must clear at the target BER:
// a minimum eye height (V) and width (UI). The presets are simplified
// receiver-eye targets for the common interfaces; because the link simulator
// is LTI/pre-equalisation (Tx FFE / Rx CTLE-DFE are E4 step 4 / S6), a lossy
// channel will correctly "fail" until equalisation is applied — which is the
// honest answer and the reason those engines exist.
// ============================================================================

public sealed record ComplianceMask(string Standard, double DataRateGbps, double BerTarget,
                                    double MinEyeHeightV, double MinEyeWidthUI);

public sealed record MaskResult(string Standard, bool Pass, double EyeHeightV, double MinEyeHeightV,
                                double EyeWidthUI, double MinEyeWidthUI, string Summary);

public static class ComplianceMasks
{
    // Simplified receiver-eye keep-outs (height V, width UI) at the target BER.
    public static readonly IReadOnlyDictionary<string, ComplianceMask> Presets =
        new Dictionary<string, ComplianceMask>
        {
            ["PCIe Gen3 (8 GT/s)"] = new("PCIe Gen3 (8 GT/s)", 8.0, 1e-12, 0.025, 0.30),
            ["PCIe Gen4 (16 GT/s)"] = new("PCIe Gen4 (16 GT/s)", 16.0, 1e-12, 0.015, 0.30),
            ["PCIe Gen5 (32 GT/s)"] = new("PCIe Gen5 (32 GT/s)", 32.0, 1e-12, 0.010, 0.25),
            ["DDR5-6400"] = new("DDR5-6400", 6.4, 1e-12, 0.040, 0.25),
            ["Generic 10 Gb/s"] = new("Generic 10 Gb/s", 10.0, 1e-12, 0.050, 0.40),
        };

    public static ComplianceMask? Find(string standard)
        => Presets.TryGetValue(standard, out var m) ? m : null;

    /// <summary>Check a simulated eye against a mask.</summary>
    public static MaskResult Check(ComplianceMask mask, StatisticalEye.Eye eye)
    {
        bool pass = eye.HeightV >= mask.MinEyeHeightV && eye.WidthUI >= mask.MinEyeWidthUI;
        string s = $"{mask.Standard}: {(pass ? "PASS" : "FAIL")} — eye {eye.HeightV * 1e3:F1} mV " +
                   $"× {eye.WidthUI:F3} UI vs mask {mask.MinEyeHeightV * 1e3:F0} mV × {mask.MinEyeWidthUI:F2} UI";
        return new MaskResult(mask.Standard, pass, eye.HeightV, mask.MinEyeHeightV,
                              eye.WidthUI, mask.MinEyeWidthUI, s);
    }
}

// ----------------------------------------------------------------------------
// E15. PAM4 compliance — a PAM4 link has three stacked sub-eyes, so the keep-out
// is the *minimum* sub-eye opening (the worst of the three) plus a level-linearity
// floor (RLM, ratio of level mismatch). Presets are simplified receiver targets
// for the common 100/112G-per-lane PAM4 interfaces (IEEE 802.3 / OIF-CEI). Baud is
// the symbol rate; the lane bit rate is 2× the baud.
// ----------------------------------------------------------------------------

public sealed record Pam4Mask(string Standard, double BaudGBd, double BerTarget,
                              double MinSubEyeV, double MinRlm);

public sealed record Pam4MaskResult(string Standard, bool Pass, double MinSubEyeV, double RequiredSubEyeV,
                                    double Rlm, double RequiredRlm, double[] SubEyeHeightsV, string Summary);

public static class Pam4Masks
{
    public static readonly IReadOnlyDictionary<string, Pam4Mask> Presets =
        new Dictionary<string, Pam4Mask>
        {
            // pre-FEC BER 1e-6; min worst sub-eye and RLM linearity floor
            ["112G PAM4 (56 GBd)"] = new("112G PAM4 (56 GBd)", 56.0, 1e-6, 0.0150, 0.92),
            ["100G PAM4 (53.125 GBd)"] = new("100G PAM4 (53.125 GBd)", 53.125, 1e-6, 0.0180, 0.92),
            ["56G PAM4 (28 GBd)"] = new("56G PAM4 (28 GBd)", 28.0, 1e-6, 0.0280, 0.95),
        };

    public static Pam4Mask? Find(string standard)
        => Presets.TryGetValue(standard, out var m) ? m : null;

    /// <summary>Check a PAM4 eye (three sub-eyes + RLM) against a mask.</summary>
    public static Pam4MaskResult Check(Pam4Mask mask, StatisticalEye.Pam4Eye eye)
    {
        bool pass = eye.MinSubEyeV >= mask.MinSubEyeV && eye.Rlm >= mask.MinRlm;
        string s = $"{mask.Standard}: {(pass ? "PASS" : "FAIL")} — min sub-eye {eye.MinSubEyeV * 1e3:F1} mV " +
                   $"(sub-eyes {eye.SubEyeHeightsV[0] * 1e3:F1}/{eye.SubEyeHeightsV[1] * 1e3:F1}/{eye.SubEyeHeightsV[2] * 1e3:F1} mV), " +
                   $"RLM {eye.Rlm:F3} vs mask {mask.MinSubEyeV * 1e3:F0} mV / RLM {mask.MinRlm:F2}";
        return new Pam4MaskResult(mask.Standard, pass, eye.MinSubEyeV, mask.MinSubEyeV,
                                  eye.Rlm, mask.MinRlm, eye.SubEyeHeightsV, s);
    }
}
