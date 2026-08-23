using System.Numerics;
using System.Text;

namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E4 step 2 + orchestration — link simulator.
//
// Ties the pieces together: ChannelExtractor gives S21(f) (from the S2 MoM RLGC
// + S3 via models) on a linear grid; a driver (IBIS-derived or a simple swing +
// edge) shapes the launched bit; an inverse FFT turns the band into a causal
// single-bit (pulse) response; and StatisticalEye reads the BER eye height/width.
//
// "Simulate link…" on a net runs this end-to-end. The LTI assumption (the eye is
// built statistically from the pulse response, not by bit-by-bit simulation) is
// exactly how Gen4/DDR5 compliance is checked; vendor AMI equalisers are E4 step
// 4 (Phase S6).
// ============================================================================

public static class LinkSimulator
{
    public sealed record Options(
        double DataRateGbps,
        double SwingV = 1.0,
        double RiseTimeS = 0,                 // 0 = ideal step (no driver pole)
        IbisModel? Driver = null,             // overrides SwingV/RiseTimeS when given
        double Ber = 1e-12,
        double NoiseSigmaV = 0,               // receiver rms voltage noise
        double RjRmsUI = 0, double DjUI = 0,  // jitter (rms / peak, in UI)
        double MaskHeightV = 0, double MaskWidthUI = 0,
        Equalizer? Eq = null,                 // Tx FFE / Rx CTLE / Rx DFE
        int N = 8192, double FmaxHz = 0);     // FmaxHz 0 ⇒ 8× Nyquist

    public sealed record Result(
        StatisticalEye.Eye Eye, double[] PulseResponse, double DtS, double UiS,
        double InsertionLossDb, bool MaskPass, string Summary);

    public static Result Simulate(BoardDocument doc, int netId, Options opt)
    {
        double dataRate = opt.DataRateGbps * 1e9;
        double ui = 1.0 / dataRate;
        double fmax = opt.FmaxHz > 0 ? opt.FmaxHz : 4 * dataRate;     // 8× Nyquist
        int n = opt.N;
        int nh = n / 2;
        double df = fmax / nh;
        double dt = 1.0 / (2 * fmax);

        // driver: IBIS-derived or simple swing + edge
        double swing = opt.SwingV, rise = opt.RiseTimeS;
        if (opt.Driver != null)
        {
            var d = opt.Driver.ToLinearDriver();
            swing = d.SwingV;
            rise = d.RiseTimeS > 0 ? d.RiseTimeS : rise;
        }
        double fPole = rise > 0 ? 0.35 / rise : double.PositiveInfinity;
        Complex Hdrv(double f) => double.IsInfinity(fPole)
            ? Complex.One : 1.0 / (1.0 + new Complex(0, f / fPole));

        // linear frequency grid 0..fmax and the channel S21 on it
        var freqs = new double[nh + 1];
        for (int k = 0; k <= nh; k++) freqs[k] = k * df;
        var fp = ChannelExtractor.ExtractAt(doc, netId, freqs, useMoM: true);

        // single-sided system spectrum (channel × driver), with a gentle upper-band
        // taper to suppress the brick-wall (Gibbs) ripple on near-lossless channels
        var H = new Complex[nh + 1];
        for (int k = 1; k <= nh; k++)
        {
            Complex s21 = fp[k].S21;
            double w = UpperTaper(freqs[k], fmax);
            Complex ctle = opt.Eq?.CtleH(freqs[k]) ?? Complex.One;   // Rx CTLE peaking
            H[k] = s21 * Hdrv(freqs[k]) * ctle * w;
        }
        H[0] = new Complex(H[1].Magnitude, 0);                       // clean real DC bin

        double[] h = Fft.RealIfft(H, n);

        // single-bit (pulse) response = channel impulse ⊛ a UI-wide rect of height swing
        int nUI = Math.Max(1, (int)Math.Round(ui / dt));
        var rect = new double[n];
        for (int i = 0; i < nUI && i < n; i++) rect[i] = swing;
        double[] p = Fft.Convolve(h, rect);
        if (opt.Eq != null) p = opt.Eq.ApplyFfe(p, dt, ui);          // Tx FFE pre-emphasis

        int dfe = opt.Eq?.DfeTaps ?? 0;                              // Rx DFE post-cursor cancel
        var eye = StatisticalEye.Analyze(p, dt, ui, opt.Ber, opt.NoiseSigmaV, opt.RjRmsUI, opt.DjUI, dfeTaps: dfe);

        // insertion loss at Nyquist
        int kNyq = Math.Clamp((int)Math.Round((dataRate / 2) / df), 1, nh);
        double ilDb = 20 * Math.Log10(Math.Max(fp[kNyq].S21.Magnitude, 1e-9));

        bool maskPass = eye.HeightV > opt.MaskHeightV && eye.WidthUI > opt.MaskWidthUI;

        var sb = new StringBuilder();
        sb.AppendLine($"Link eye — net {doc.NetName(netId)} @ {opt.DataRateGbps:F1} Gbps (UI {ui * 1e12:F1} ps)");
        sb.AppendLine($"  insertion loss @ Nyquist: {ilDb:F2} dB");
        sb.AppendLine($"  eye height: {eye.HeightV * 1e3:F1} mV (worst-case {eye.WorstCaseHeightV * 1e3:F1} mV) @ BER {opt.Ber:0.0e+0}");
        sb.AppendLine($"  eye width:  {eye.WidthPs:F1} ps ({eye.WidthUI:F3} UI)");
        if (opt.MaskHeightV > 0 || opt.MaskWidthUI > 0)
            sb.AppendLine($"  mask: {(maskPass ? "PASS" : "FAIL")} (need ≥{opt.MaskHeightV * 1e3:F0} mV, ≥{opt.MaskWidthUI:F2} UI)");

        return new Result(eye, p, dt, ui, ilDb, maskPass, sb.ToString());
    }

    /// <summary>
    /// Simulate over an arbitrary channel transfer S21(f) — e.g. a multi-board
    /// backplane assembled from vendor connector S-parameters (E18). Same driver,
    /// equalisation and eye machinery as the net-based path.
    /// </summary>
    public static Result SimulateChannel(Func<double, Complex> channelS21, Options opt, string label = "channel")
    {
        double dataRate = opt.DataRateGbps * 1e9;
        double ui = 1.0 / dataRate;
        double fmax = opt.FmaxHz > 0 ? opt.FmaxHz : 4 * dataRate;
        int n = opt.N, nh = n / 2;
        double df = fmax / nh, dt = 1.0 / (2 * fmax);

        double swing = opt.SwingV, rise = opt.RiseTimeS;
        if (opt.Driver != null)
        {
            var d = opt.Driver.ToLinearDriver();
            swing = d.SwingV; rise = d.RiseTimeS > 0 ? d.RiseTimeS : rise;
        }
        double fPole = rise > 0 ? 0.35 / rise : double.PositiveInfinity;
        Complex Hdrv(double f) => double.IsInfinity(fPole) ? Complex.One : 1.0 / (1.0 + new Complex(0, f / fPole));

        var H = new Complex[nh + 1];
        for (int k = 1; k <= nh; k++)
        {
            double f = k * df;
            Complex s21 = channelS21(f);
            if (!Complex.IsFinite(s21)) s21 = Complex.Zero;
            Complex ctle = opt.Eq?.CtleH(f) ?? Complex.One;
            H[k] = s21 * Hdrv(f) * ctle * UpperTaper(f, fmax);
        }
        H[0] = new Complex(H[1].Magnitude, 0);

        double[] h = Fft.RealIfft(H, n);
        int nUI = Math.Max(1, (int)Math.Round(ui / dt));
        var rect = new double[n];
        for (int i = 0; i < nUI && i < n; i++) rect[i] = swing;
        double[] p = Fft.Convolve(h, rect);
        if (opt.Eq != null) p = opt.Eq.ApplyFfe(p, dt, ui);

        int dfe = opt.Eq?.DfeTaps ?? 0;
        var eye = StatisticalEye.Analyze(p, dt, ui, opt.Ber, opt.NoiseSigmaV, opt.RjRmsUI, opt.DjUI, dfeTaps: dfe);
        double ilDb = 20 * Math.Log10(Math.Max(channelS21(dataRate / 2).Magnitude, 1e-9));
        bool maskPass = eye.HeightV > opt.MaskHeightV && eye.WidthUI > opt.MaskWidthUI;

        var sb = new StringBuilder();
        sb.AppendLine($"Link eye — {label} @ {opt.DataRateGbps:F1} Gbps (UI {ui * 1e12:F1} ps)");
        sb.AppendLine($"  insertion loss @ Nyquist: {ilDb:F2} dB");
        sb.AppendLine($"  eye: {eye.HeightV * 1e3:F1} mV × {eye.WidthUI:F3} UI @ BER {opt.Ber:0.0e+0}");
        return new Result(eye, p, dt, ui, ilDb, maskPass, sb.ToString());
    }

    public sealed record Pam4Result(StatisticalEye.Pam4Eye Eye, double[] PulseResponse, double DtS, double UiS,
                                    double InsertionLossDb, double MinSubEyeV, double Rlm, string Summary);

    /// <summary>
    /// PAM4 link simulation (E15) over a channel transfer. <c>DataRateGbps</c> is
    /// the *symbol* rate in GBaud (e.g. 56 for 112G PAM4). Returns the three
    /// sub-eye openings, their min and RLM. <paramref name="aggressors"/> are
    /// per-aggressor coupled cursor sets folded into the eye (E16).
    /// </summary>
    public static Pam4Result SimulatePam4(Func<double, Complex> channelS21, Options opt,
                                          IReadOnlyList<IReadOnlyList<double>>? aggressors = null,
                                          string label = "PAM4")
    {
        double baud = opt.DataRateGbps * 1e9;
        double ui = 1.0 / baud;
        double fmax = opt.FmaxHz > 0 ? opt.FmaxHz : 4 * baud;
        int n = opt.N, nh = n / 2;
        double df = fmax / nh, dt = 1.0 / (2 * fmax);

        double swing = opt.SwingV, rise = opt.RiseTimeS;
        if (opt.Driver != null) { var d = opt.Driver.ToLinearDriver(); swing = d.SwingV; rise = d.RiseTimeS > 0 ? d.RiseTimeS : rise; }
        double fPole = rise > 0 ? 0.35 / rise : double.PositiveInfinity;
        Complex Hdrv(double f) => double.IsInfinity(fPole) ? Complex.One : 1.0 / (1.0 + new Complex(0, f / fPole));

        var H = new Complex[nh + 1];
        for (int k = 1; k <= nh; k++)
        {
            double f = k * df;
            Complex s21 = channelS21(f);
            if (!Complex.IsFinite(s21)) s21 = Complex.Zero;
            Complex ctle = opt.Eq?.CtleH(f) ?? Complex.One;
            H[k] = s21 * Hdrv(f) * ctle * UpperTaper(f, fmax);
        }
        H[0] = new Complex(H[1].Magnitude, 0);

        double[] h = Fft.RealIfft(H, n);
        int nUI = Math.Max(1, (int)Math.Round(ui / dt));
        var rect = new double[n];
        for (int i = 0; i < nUI && i < n; i++) rect[i] = swing;
        double[] p = Fft.Convolve(h, rect);
        if (opt.Eq != null) p = opt.Eq.ApplyFfe(p, dt, ui);

        int dfe = opt.Eq?.DfeTaps ?? 0;
        var eye = StatisticalEye.Pam4Analyze(p, dt, ui, opt.Ber, opt.NoiseSigmaV, dfe, aggressors);
        double ilDb = 20 * Math.Log10(Math.Max(channelS21(baud / 2).Magnitude, 1e-9));

        var sb = new StringBuilder();
        sb.AppendLine($"PAM4 link — {label} @ {opt.DataRateGbps:F0} GBaud ({opt.DataRateGbps * 2:F0} Gb/s, UI {ui * 1e12:F1} ps)");
        sb.AppendLine($"  insertion loss @ Nyquist: {ilDb:F2} dB");
        sb.AppendLine($"  sub-eyes: {eye.SubEyeHeightsV[0] * 1e3:F1} / {eye.SubEyeHeightsV[1] * 1e3:F1} / {eye.SubEyeHeightsV[2] * 1e3:F1} mV " +
                      $"(min {eye.MinSubEyeV * 1e3:F1} mV, RLM {eye.Rlm:F2})");
        return new Pam4Result(eye, p, dt, ui, ilDb, eye.MinSubEyeV, eye.Rlm, sb.ToString());
    }

    /// <summary>Pre-cursor energy fraction of a pulse response — a causality metric
    /// (small ⇒ the response is causal; energy well before the main cursor is not).</summary>
    public static double PrecursorEnergyFraction(double[] pulse, double dtS, double uiS)
    {
        int peak = 0; double pmax = double.MinValue;
        for (int i = 0; i < pulse.Length; i++) if (Math.Abs(pulse[i]) > pmax) { pmax = Math.Abs(pulse[i]); peak = i; }
        int guard = peak - (int)(2 * uiS / dtS);            // 2 UI before the main cursor
        double pre = 0, tot = 0;
        for (int i = 0; i < pulse.Length; i++)
        {
            double e = pulse[i] * pulse[i];
            tot += e;
            if (i < guard) pre += e;
        }
        return tot > 0 ? pre / tot : 0;
    }

    public sealed record Compliance(string Standard, MaskResult Mask,
                                    JitterDecomposition.JitterReport Jitter, Result Link);

    /// <summary>
    /// Simulate at a named standard's data rate/BER and check the eye against its
    /// mask, with a jitter decomposition — the per-standard pass/fail report (E5).
    /// </summary>
    public static Compliance? CheckCompliance(BoardDocument doc, int netId, string standard,
                                              double swingV = 1.0, double riseTimeS = 0,
                                              double rjRmsUI = 0, double djUI = 0)
    {
        var mask = ComplianceMasks.Find(standard);
        if (mask == null) return null;
        var link = Simulate(doc, netId, new Options(
            mask.DataRateGbps, swingV, riseTimeS, Ber: mask.BerTarget,
            RjRmsUI: rjRmsUI, DjUI: djUI,
            MaskHeightV: mask.MinEyeHeightV, MaskWidthUI: mask.MinEyeWidthUI));
        var mr = ComplianceMasks.Check(mask, link.Eye);
        var jr = JitterDecomposition.Decompose(link.PulseResponse, link.DtS, link.UiS,
                                               mask.BerTarget, rjRmsUI, djUI);
        return new Compliance(standard, mr, jr, link);
    }

    // raised-cosine taper over the top 12 % of the band
    private static double UpperTaper(double f, double fmax)
    {
        double f0 = 0.88 * fmax;
        if (f <= f0) return 1.0;
        double x = (f - f0) / (fmax - f0);
        return 0.5 * (1 + Math.Cos(Math.PI * x));
    }
}
