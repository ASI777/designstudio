namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E4 step 3 — statistical eye engine (LTI / peak-distortion analysis).
//
// Given the channel's single-bit (pulse) response, the received voltage at the
// sampler is  v = c0·a0 + Σ_{k≠0} ck·a_{-k}  for random bits a∈{±1}. Rather than
// simulate billions of bits, we build the *distribution* of the ISI term by
// convolving each cursor's two-point pmf {½@+ck, ½@−ck}, shift by the main
// cursor, fold in Gaussian noise, and read the BER tails. This is exactly how
// Gen4/DDR5 eyes are checked, and it gives BER contours, eye height and eye
// width directly. (NRZ here; PAM4 is a later extension.)
//
// Validated against analytic cases: a lossless channel opens to the full ±swing
// eye, a known cursor set reproduces the worst-case opening 2·(c0−Σ|ck|), added
// noise closes the eye by Q·σ, and the eye shrinks monotonically with loss/rate.
// ============================================================================

public static class StatisticalEye
{
    public sealed record Eye(double HeightV, double WidthUI, double WidthPs,
                             double CenterPhaseUI, double Ber, double WorstCaseHeightV,
                             double MainCursorV);

    /// <summary>
    /// Cursor taps c_k = p(t_peak + (k+phase)·UI). The main cursor is the global
    /// peak of the pulse response; phase offsets the sampling instant in UI.
    /// </summary>
    public static Dictionary<int, double> Cursors(double[] p, double dtS, double uiS,
                                                  int kSpan, double phaseUI)
    {
        double spui = uiS / dtS;
        int peak = 0; double pmax = double.MinValue;
        for (int i = 0; i < p.Length; i++) if (p[i] > pmax) { pmax = p[i]; peak = i; }
        double ts = peak + phaseUI * spui;
        var cs = new Dictionary<int, double>();
        for (int k = -kSpan; k <= kSpan; k++)
        {
            double idx = ts + k * spui;
            int lo = (int)Math.Floor(idx);
            if (lo < 0 || lo >= p.Length - 1) continue;
            double fr = idx - lo;
            cs[k] = p[lo] * (1 - fr) + p[lo + 1] * fr;
        }
        return cs;
    }

    /// <summary>
    /// Eye opening (V) at one sampling phase: the BER-tail edge of the "1" level
    /// distribution, doubled by symmetry. Positive ⇒ eye open at that BER.
    /// </summary>
    public static double EyeHeightAt(IReadOnlyDictionary<int, double> cs, double ber,
                                     double sigmaNoise, int vbins = 4001, int dfeTaps = 0,
                                     IReadOnlyList<IReadOnlyList<double>>? aggressors = null)
    {
        if (!cs.TryGetValue(0, out double c0)) return 0;
        // a DFE cancels the first dfeTaps post-cursors (k = 1..dfeTaps) exactly
        var taps = cs.Where(kv => kv.Key != 0 && !(kv.Key >= 1 && kv.Key <= dfeTaps)
                                  && Math.Abs(kv.Value) > 1e-12)
                     .Select(kv => kv.Value).ToList();
        // crosstalk: every aggressor cursor is an extra random-data tap (E16)
        double aggSum = 0;
        if (aggressors != null)
            foreach (var ag in aggressors) foreach (var c in ag) aggSum += Math.Abs(c);
        // span must reach past the BER tail of the noise (Q≈7 at 1e-12), so the
        // Gaussian is not truncated before the quantile we read.
        double span = Math.Abs(c0) + taps.Sum(Math.Abs) + aggSum + 9 * sigmaNoise + 1e-9;
        double dv = 2 * span / (vbins - 1);
        var pdf = new double[vbins];
        pdf[vbins / 2] = 1.0;                                  // delta at 0

        void Fold(double t)
        {
            int s = (int)Math.Round(t / dv);
            if (s == 0) return;
            var np = new double[vbins];
            int a = Math.Abs(s);
            for (int i = 0; i < vbins; i++)
            {
                double half = 0.5 * pdf[i];
                if (half == 0) continue;
                int up = i + a, dn = i - a;
                if (up < vbins) np[up] += half;
                if (dn >= 0) np[dn] += half;
            }
            pdf = np;
        }
        foreach (double t in taps) Fold(t);
        if (aggressors != null)
            foreach (var ag in aggressors) foreach (var c in ag)
                if (Math.Abs(c) > 1e-12) Fold(c);

        // shift to the "1" level (+c0)
        int sh = (int)Math.Round(c0 / dv);
        var one = new double[vbins];
        for (int i = 0; i < vbins; i++)
        {
            int j = i + sh;
            if (j >= 0 && j < vbins) one[j] += pdf[i];
        }

        if (sigmaNoise > 0)
        {
            // kernel out to ±8.5σ so the BER tail (~7σ at 1e-12) is represented
            int gr = Math.Max(1, (int)Math.Ceiling(8.5 * sigmaNoise / dv));
            var g = new double[2 * gr + 1]; double gsum = 0;
            for (int i = -gr; i <= gr; i++) { double x = i * dv / sigmaNoise; g[i + gr] = Math.Exp(-0.5 * x * x); gsum += g[i + gr]; }
            var conv = new double[vbins];
            for (int i = 0; i < vbins; i++)
            {
                if (one[i] == 0) continue;
                for (int k = -gr; k <= gr; k++) { int j = i + k; if (j >= 0 && j < vbins) conv[j] += one[i] * g[k + gr] / gsum; }
            }
            one = conv;
        }

        // lower-tail quantile at the target BER
        double total = one.Sum(); if (total <= 0) return 0;
        double acc = 0, thresh = ber * total;
        for (int i = 0; i < vbins; i++)
        {
            acc += one[i];
            if (acc >= thresh) { double vlo = -span + i * dv; return 2 * vlo; }
        }
        return 0;
    }

    /// <summary>
    /// Full eye: sweep the sampling phase, take the widest vertical opening, and
    /// measure the horizontal span where the eye stays open at the target BER.
    /// Random/deterministic jitter (UI rms / peak) close the width.
    /// </summary>
    public static Eye Analyze(double[] p, double dtS, double uiS, double ber = 1e-12,
                              double sigmaNoise = 0, double rjRmsUI = 0, double djUI = 0,
                              int kSpan = 80, int phaseSteps = 81, int dfeTaps = 0,
                              IReadOnlyList<IReadOnlyList<double>>? aggressors = null)
    {
        double bestH = double.MinValue, bestPhase = 0, mainV = 0, worst = 0;
        var open = new bool[phaseSteps];
        var phases = new double[phaseSteps];
        for (int s = 0; s < phaseSteps; s++)
        {
            double phase = -0.5 + s / (double)(phaseSteps - 1);
            phases[s] = phase;
            var cs = Cursors(p, dtS, uiS, kSpan, phase);
            double h = EyeHeightAt(cs, ber, sigmaNoise, dfeTaps: dfeTaps, aggressors: aggressors);
            open[s] = h > 0;
            if (h > bestH)
            {
                bestH = h; bestPhase = phase; cs.TryGetValue(0, out mainV);
                worst = 2 * (Math.Abs(mainV) - cs.Where(kv => kv.Key != 0
                            && !(kv.Key >= 1 && kv.Key <= dfeTaps)).Sum(kv => Math.Abs(kv.Value)));
            }
        }

        // contiguous open span around the best phase
        int center = (int)Math.Round((bestPhase + 0.5) * (phaseSteps - 1));
        center = Math.Clamp(center, 0, phaseSteps - 1);
        int lo = center, hi = center;
        while (lo > 0 && open[lo - 1]) lo--;
        while (hi < phaseSteps - 1 && open[hi + 1]) hi++;
        double widthUI = bestH > 0 ? phases[hi] - phases[lo] : 0;

        // jitter budget at the target BER (dual-Dirac)
        double q = QFromBer(ber);
        widthUI = Math.Max(0, widthUI - (djUI + 2 * q * rjRmsUI));

        return new Eye(Math.Max(0, bestH), widthUI, widthUI * uiS * 1e12,
                       bestPhase, ber, Math.Max(0, worst), mainV);
    }

    /// <summary>Gaussian Q corresponding to a one-sided BER (0.5·erfc(Q/√2)=BER).</summary>
    public static double QFromBer(double ber)
    {
        if (ber <= 0) return 8;
        if (ber >= 0.5) return 0;
        double lo = 0, hi = 12;
        for (int i = 0; i < 60; i++)
        {
            double mid = (lo + hi) / 2;
            if (0.5 * Erfc(mid / Math.Sqrt(2)) > ber) lo = mid; else hi = mid;
        }
        return (lo + hi) / 2;
    }

    /// <summary>erfc via Abramowitz &amp; Stegun 7.1.26 (≈1e-7).</summary>
    public static double Erfc(double x)
    {
        double z = Math.Abs(x);
        double t = 1.0 / (1.0 + 0.3275911 * z);
        double y = (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t;
        double erf = 1 - y * Math.Exp(-z * z);
        erf = x >= 0 ? erf : -erf;
        return 1 - erf;
    }

    // ---- PAM4 (E15): four levels, three sub-eyes -----------------------------

    /// <summary>Normalised PAM4 levels (−1, −⅓, +⅓, +1).</summary>
    public static readonly double[] Pam4Levels = { -1, -1.0 / 3, 1.0 / 3, 1 };

    public sealed record Pam4Eye(double[] SubEyeHeightsV, double MinSubEyeV, double Rlm, double CenterPhaseUI);

    /// <summary>
    /// The three PAM4 sub-eye openings (V) at one sampling phase. ISI taps and any
    /// crosstalk aggressors are 4-level random data; the opening of each sub-eye is
    /// the gap between the BER tails of the two adjacent level distributions.
    /// </summary>
    public static double[] Pam4SubEyesAt(IReadOnlyDictionary<int, double> cs, double ber, double sigmaNoise,
                                         int dfeTaps = 0, IReadOnlyList<IReadOnlyList<double>>? aggressors = null,
                                         int vbins = 6001)
    {
        if (!cs.TryGetValue(0, out double c0)) return new[] { 0.0, 0.0, 0.0 };
        var taps = cs.Where(kv => kv.Key != 0 && !(kv.Key >= 1 && kv.Key <= dfeTaps)
                                  && Math.Abs(kv.Value) > 1e-12).Select(kv => kv.Value).ToList();
        double aggSum = 0;
        if (aggressors != null) foreach (var ag in aggressors) foreach (var c in ag) aggSum += Math.Abs(c);
        double span = Math.Abs(c0) + taps.Sum(Math.Abs) + aggSum + 9 * sigmaNoise + 1e-9;
        double dv = 2 * span / (vbins - 1);

        var pdf = new double[vbins]; pdf[vbins / 2] = 1.0;
        foreach (double t in taps) pdf = FoldLevels(pdf, t, Pam4Levels, dv, vbins);
        if (aggressors != null)
            foreach (var ag in aggressors) foreach (var c in ag)
                if (Math.Abs(c) > 1e-12) pdf = FoldLevels(pdf, c, Pam4Levels, dv, vbins);
        if (sigmaNoise > 0) pdf = AddNoise(pdf, sigmaNoise, dv, vbins);

        var eyes = new double[3];
        for (int i = 0; i < 3; i++)
        {
            var up = Shift(pdf, c0 * Pam4Levels[i + 1], dv, vbins);
            var lo = Shift(pdf, c0 * Pam4Levels[i], dv, vbins);
            eyes[i] = LowerTailV(up, ber, span, dv) - UpperTailV(lo, ber, span, dv);
        }
        return eyes;
    }

    /// <summary>Sweep the sampling phase; report the three sub-eyes at the phase
    /// whose worst sub-eye is widest, plus the RLM (min/max sub-eye uniformity).</summary>
    public static Pam4Eye Pam4Analyze(double[] p, double dtS, double uiS, double ber = 1e-6,
                                      double sigmaNoise = 0, int dfeTaps = 0,
                                      IReadOnlyList<IReadOnlyList<double>>? aggressors = null,
                                      int kSpan = 80, int phaseSteps = 41)
    {
        double[] best = { 0, 0, 0 }; double bestMin = double.MinValue, bestPhase = 0;
        for (int s = 0; s < phaseSteps; s++)
        {
            double phase = -0.5 + s / (double)(phaseSteps - 1);
            var cs = Cursors(p, dtS, uiS, kSpan, phase);
            var eyes = Pam4SubEyesAt(cs, ber, sigmaNoise, dfeTaps, aggressors);
            double mn = Math.Min(eyes[0], Math.Min(eyes[1], eyes[2]));
            if (mn > bestMin) { bestMin = mn; best = eyes; bestPhase = phase; }
        }
        double max = Math.Max(best[0], Math.Max(best[1], best[2]));
        double min = Math.Min(best[0], Math.Min(best[1], best[2]));
        double rlm = max > 0 ? Math.Max(0, min) / max : 0;
        return new Pam4Eye(best, Math.Max(0, bestMin), rlm, bestPhase);
    }

    // ---- shared distribution helpers -----------------------------------------

    private static double[] FoldLevels(double[] pdf, double c, double[] levels, double dv, int vbins)
    {
        var np = new double[vbins];
        double w = 1.0 / levels.Length;
        foreach (double L in levels)
        {
            int s = (int)Math.Round(c * L / dv);
            for (int i = 0; i < vbins; i++)
            {
                if (pdf[i] == 0) continue;
                int j = i + s;
                if (j >= 0 && j < vbins) np[j] += pdf[i] * w;
            }
        }
        return np;
    }

    private static double[] Shift(double[] pdf, double v, double dv, int vbins)
    {
        int sh = (int)Math.Round(v / dv);
        var o = new double[vbins];
        for (int i = 0; i < vbins; i++) { int j = i + sh; if (j >= 0 && j < vbins) o[j] += pdf[i]; }
        return o;
    }

    private static double[] AddNoise(double[] pdf, double sigma, double dv, int vbins)
    {
        int gr = Math.Max(1, (int)Math.Ceiling(8.5 * sigma / dv));
        var g = new double[2 * gr + 1]; double gsum = 0;
        for (int i = -gr; i <= gr; i++) { double x = i * dv / sigma; g[i + gr] = Math.Exp(-0.5 * x * x); gsum += g[i + gr]; }
        var o = new double[vbins];
        for (int i = 0; i < vbins; i++)
        {
            if (pdf[i] == 0) continue;
            for (int k = -gr; k <= gr; k++) { int j = i + k; if (j >= 0 && j < vbins) o[j] += pdf[i] * g[k + gr] / gsum; }
        }
        return o;
    }

    private static double LowerTailV(double[] dist, double ber, double span, double dv)
    {
        double total = dist.Sum(); if (total <= 0) return 0;
        double acc = 0, th = ber * total;
        for (int i = 0; i < dist.Length; i++) { acc += dist[i]; if (acc >= th) return -span + i * dv; }
        return span;
    }

    private static double UpperTailV(double[] dist, double ber, double span, double dv)
    {
        double total = dist.Sum(); if (total <= 0) return 0;
        double acc = 0, th = ber * total;
        for (int i = dist.Length - 1; i >= 0; i--) { acc += dist[i]; if (acc >= th) return -span + i * dv; }
        return -span;
    }
}
