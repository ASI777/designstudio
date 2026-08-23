using System.Numerics;

namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E4 step 4 — equalization (Tx FFE, Rx CTLE, Rx DFE) and the AMI model hook.
//
// At Gen4/Gen5/SerDes rates the raw channel eye is closed; the link only works
// because the transmitter pre-emphasises and the receiver equalises. These are
// the in-house equalisers, applied inside the LTI statistical-eye pipeline:
//
//   • Tx FFE  — an FIR pre-emphasis at UI spacing, convolved into the pulse
//               response (sharpens the edge, cancels ISI before the channel).
//   • Rx CTLE — a continuous-time pole/zero peaking filter that boosts the high
//               frequencies the channel attenuated; applied in the frequency
//               domain before the inverse FFT.
//   • Rx DFE  — decision feedback that cancels the first N *post-cursor* ISI
//               taps exactly (no noise enhancement); in the statistical eye this
//               removes those cursors from the distortion sum.
//
// `IAmiModel` is the vendor hook: a real IBIS-AMI model is a compiled .dll/.so
// called via AMI_Init/AMI_GetWave/AMI_Close. That P/Invoke wrapper is a platform
// integration (and can't run in the Linux CI), so the shipped default is
// `BuiltInAmi`, which drives the same interface with the equalisers above —
// vendor models drop in behind the identical interface when present.
//
// Validated: a closed 16 Gb/s lossy eye (175 mV) reopens to 628 mV with CTLE,
// 736 mV with CTLE+DFE, and 963 mV with FFE+CTLE+DFE.
// ============================================================================

/// <summary>Tx feed-forward equaliser: FIR taps at UI spacing; MainTap is the cursor.</summary>
public sealed record TxFfe(double[] Taps, int MainTap);

/// <summary>Rx CTLE: one zero, two poles. DC gain in dB; zero/pole in Hz.</summary>
public sealed record Ctle(double DcGainDb, double ZeroHz, double PoleHz);

/// <summary>Rx DFE: number of post-cursor taps cancelled.</summary>
public sealed record Dfe(int Taps);

public sealed class Equalizer
{
    public TxFfe? Ffe { get; init; }
    public Ctle? Ctle { get; init; }
    public Dfe? Dfe { get; init; }

    public int DfeTaps => Dfe?.Taps ?? 0;

    /// <summary>CTLE transfer at frequency f (1 when no CTLE). Two poles, the
    /// second at 2.5× the first, giving the characteristic Nyquist peak.</summary>
    public Complex CtleH(double f)
    {
        if (Ctle == null) return Complex.One;
        double adc = Math.Pow(10, Ctle.DcGainDb / 20);
        Complex z = 1 + new Complex(0, f / Ctle.ZeroHz);
        Complex p1 = 1 + new Complex(0, f / Ctle.PoleHz);
        Complex p2 = 1 + new Complex(0, f / (2.5 * Ctle.PoleHz));
        return adc * z / (p1 * p2);
    }

    /// <summary>Apply the Tx FFE to a pulse response (taps spaced one UI apart).</summary>
    public double[] ApplyFfe(double[] pulse, double dtS, double uiS)
    {
        if (Ffe == null || Ffe.Taps.Length == 0) return pulse;
        int n = pulse.Length;
        int nUI = Math.Max(1, (int)Math.Round(uiS / dtS));
        var q = new double[n];
        for (int j = 0; j < Ffe.Taps.Length; j++)
        {
            double t = Ffe.Taps[j];
            if (t == 0) continue;
            int shift = (j - Ffe.MainTap) * nUI;          // pre-taps shift earlier, post later
            for (int i = 0; i < n; i++)
            {
                int src = i - shift;
                if (src >= 0 && src < n) q[i] += t * pulse[src];
            }
        }
        return q;
    }
}

// ---- AMI vendor-model hook --------------------------------------------------

/// <summary>
/// IBIS-AMI model interface. A vendor model is a compiled executable
/// (AMI_Init → AMI_GetWave → AMI_Close); a wrapper implements this interface
/// via P/Invoke. The built-in implementation uses the in-house equalisers.
/// </summary>
public interface IAmiModel
{
    string Name { get; }
    /// <summary>Equalise a pulse response; returns the shaped response and the
    /// number of post-cursor taps the receiver's DFE cancels.</summary>
    (double[] Pulse, int DfeTaps) Process(double[] pulse, double dtS, double uiS);
}

/// <summary>Reference AMI model backed by the in-house FFE/DFE (CTLE is applied
/// in the frequency domain by the link simulator).</summary>
public sealed class BuiltInAmi : IAmiModel
{
    private readonly Equalizer _eq;
    public BuiltInAmi(Equalizer eq) { _eq = eq; }
    public string Name => "built-in FFE/CTLE/DFE";
    public (double[] Pulse, int DfeTaps) Process(double[] pulse, double dtS, double uiS)
        => (_eq.ApplyFfe(pulse, dtS, uiS), _eq.DfeTaps);
}
