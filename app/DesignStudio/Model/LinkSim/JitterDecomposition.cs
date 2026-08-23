namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E5. Jitter decomposition from the statistical eye.
//
// Separates the horizontal eye closure into:
//   • DDJ  — data-dependent (ISI) jitter, the channel's own contribution,
//            taken from the ISI-limited eye width (no injected jitter);
//   • Dj   — injected deterministic jitter (e.g. Tx duty-cycle / periodic);
//   • Rj   — random (Gaussian) jitter, rms.
// and combines them into the dual-Dirac total jitter at the target BER:
//   TJ(BER) = DDJ + Dj + 2·Q(BER)·Rj ,  leaving eye width 1 − TJ (UI).
// This is the jitter half of the per-standard compliance report.
// ============================================================================

public static class JitterDecomposition
{
    public sealed record JitterReport(double RjRmsUI, double DjUI, double DdjUI,
                                      double TotalJitterUI, double EyeWidthUI, double QBer);

    /// <summary>
    /// Decompose the jitter of a link from its pulse response. DDJ is read from
    /// the ISI-limited eye (no injected jitter); Rj/Dj are the injected Tx terms.
    /// </summary>
    public static JitterReport Decompose(double[] pulse, double dtS, double uiS, double ber,
                                         double rjRmsUI, double djUI)
    {
        // ISI-only eye (no injected jitter) → the data-dependent jitter is the
        // horizontal closure of the unit interval that ISI alone causes.
        var isiEye = StatisticalEye.Analyze(pulse, dtS, uiS, ber, 0, 0, 0);
        double ddj = Math.Max(0, 1.0 - isiEye.WidthUI);

        double q = StatisticalEye.QFromBer(ber);
        double tj = ddj + djUI + 2 * q * rjRmsUI;
        double eyeWidth = Math.Max(0, 1.0 - tj);
        return new JitterReport(rjRmsUI, djUI, ddj, tj, eyeWidth, q);
    }
}
