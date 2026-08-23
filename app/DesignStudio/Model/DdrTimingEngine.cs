using System.Text;
using DesignStudio.Model.LinkSim;

namespace DesignStudio.Model;

// ============================================================================
// E7. DDR timing engine — per-byte-lane setup/hold margins.
//
// The number a DDR5 board is actually signed off on: does every DQ bit have
// positive setup and hold margin against the strobe at the target rate? This
// engine combines everything the stack now produces:
//
//   • the data-valid window  — half the (equalised) statistical-eye width × UI
//                              (S4 link sim + S6 equalisers, over the S2/S3
//                              channel);
//   • DQ-to-DQS skew         — the per-bit arrival difference (DelayEngine);
//   • crosstalk-induced jitter — NEXT from the other lane bits (Crosstalk) ÷ slew;
//   • SSN/SSO                — PDN impedance at the buffer (PlaneCavity, E6) ×
//                              the simultaneous-switching current ÷ slew;
//   • Rj/Dj                  — the standard's jitter budget.
//
//   setup = ½·eye − tDS − skew − jitter
//   hold  = ½·eye − tDH + skew − jitter
//
// Output: a per-bit margin table and a lane pass/fail — "DQ3 fails setup by
// 4 ps at 6400 MT/s." Validated to discriminate a matched low-loss lane (pass)
// from a long/lossy/skewed one (fail).
// ============================================================================

public static class DdrTimingEngine
{
    /// <param name="DataRateMtps">e.g. 6400 for DDR5-6400</param>
    /// <param name="EdgeRiseFracUI">data edge rise time as a fraction of UI (slew)</param>
    public sealed record Spec(double DataRateMtps, double TdsPs, double TdhPs, double SwingV,
                              double EdgeRiseFracUI, double RjRmsPs, double DjPs,
                              double IPerBitA, double Ber);

    public static Spec Ddr5_6400 => new(6400, 18, 18, 1.1, 0.15, 0.8, 1.5, 0.008, 1e-12);
    public static Spec Ddr5_4800 => new(4800, 22, 22, 1.1, 0.15, 1.0, 2.0, 0.008, 1e-12);

    public sealed record BitMargin(string Net, double SkewPs, double HalfEyePs, double XtalkPs,
                                   double SsnPs, double JitterPs, double SetupPs, double HoldPs, bool Pass);

    public sealed record LaneReport(string LaneName, double DataRateMtps, double UiPs,
                                    List<BitMargin> Bits, double WorstSetupPs, double WorstHoldPs,
                                    string WorstBit, bool Pass, string Summary);

    /// <summary>
    /// Analyse one byte lane (DQ bits + DQS strobe). <paramref name="pdn"/> (E6)
    /// supplies the SSN impedance; null ⇒ SSN ignored. <paramref name="eq"/> is
    /// the receiver/transmitter equalisation applied to every bit's eye.
    /// </summary>
    public static LaneReport AnalyzeByteLane(BoardDocument doc, IList<int> dqNets, int dqsNet,
                                             Spec spec, PlaneCavity? pdn = null, Equalizer? eq = null,
                                             string laneName = "byte")
    {
        double uiPs = 1e6 / spec.DataRateMtps;                 // MT/s → UI (ps)
        double uiS = uiPs * 1e-12;
        double riseS = spec.EdgeRiseFracUI * uiS;
        double slewMvPerPs = spec.SwingV / riseS * 1e-9;       // (V/s)·1e-9 = mV/ps
        double q = StatisticalEye.QFromBer(spec.Ber);
        double dqsDelay = DelayEngine.NetDelayPs(doc, dqsNet);
        double fData = spec.DataRateMtps * 1e6 / 2;            // fundamental ≈ rate/2

        // SSN: PDN Z at the buffer × the simultaneous-switching current of the lane
        double ssnV = 0;
        if (pdn != null)
            ssnV = pdn.Zin(fData).Magnitude * (dqNets.Count * spec.IPerBitA);
        double ssnPs = ssnV * 1e3 / slewMvPerPs;

        var couplings = Crosstalk.FindCouplings(doc);
        var bits = new List<BitMargin>();
        foreach (int dq in dqNets)
        {
            // (equalised) eye width → data valid window
            var link = LinkSimulator.Simulate(doc, dq, new LinkSimulator.Options(
                spec.DataRateMtps / 1000.0, spec.SwingV, riseS, Eq: eq, N: 2048));
            double halfEye = link.Eye.WidthUI * uiPs / 2;

            double skew = DelayEngine.NetDelayPs(doc, dq) - dqsDelay;

            // crosstalk: NEXT from the other lane bits onto this DQ
            double nextFrac = couplings
                .Where(c => c.Victim.NetId == dq && dqNets.Contains(c.Aggressor.NetId))
                .Sum(c => c.NextFraction);
            double xtalkPs = nextFrac * spec.SwingV * 1e3 / slewMvPerPs;

            double jitter = xtalkPs + ssnPs + spec.DjPs + q * spec.RjRmsPs;
            double setup = halfEye - spec.TdsPs - skew - jitter;
            double hold = halfEye - spec.TdhPs + skew - jitter;
            bool pass = setup > 0 && hold > 0;
            bits.Add(new BitMargin(doc.NetName(dq), skew, halfEye, xtalkPs, ssnPs, jitter, setup, hold, pass));
        }

        var worstSetup = bits.MinBy(b => b.SetupPs)!;
        var worstHold = bits.MinBy(b => b.HoldPs)!;
        bool lanePass = bits.All(b => b.Pass);
        string worstBit = worstSetup.SetupPs <= worstHold.HoldPs ? worstSetup.Net : worstHold.Net;

        var sb = new StringBuilder();
        sb.AppendLine($"DDR timing — lane {laneName} @ {spec.DataRateMtps:F0} MT/s (UI {uiPs:F1} ps): {(lanePass ? "PASS" : "FAIL")}");
        sb.AppendLine($"  worst setup {worstSetup.SetupPs:+0.0;-0.0} ps ({worstSetup.Net}), " +
                      $"worst hold {worstHold.HoldPs:+0.0;-0.0} ps ({worstHold.Net})");
        if (ssnPs > 0) sb.AppendLine($"  SSN {ssnV * 1e3:F1} mV → {ssnPs:F1} ps over {dqNets.Count} bits");
        foreach (var b in bits.Where(b => !b.Pass))
            sb.AppendLine($"  {b.Net}: FAIL — setup {b.SetupPs:+0.0;-0.0} ps, hold {b.HoldPs:+0.0;-0.0} ps " +
                          $"(eye ½{b.HalfEyePs:F1} ps, skew {b.SkewPs:+0.0;-0.0} ps, jitter {b.JitterPs:F1} ps)");

        return new LaneReport(laneName, spec.DataRateMtps, uiPs, bits,
                              worstSetup.SetupPs, worstHold.HoldPs, worstBit, lanePass, sb.ToString());
    }
}
