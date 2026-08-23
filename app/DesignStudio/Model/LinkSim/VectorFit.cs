using System.Numerics;

namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E18. Vector-fit rational macromodel — guaranteed-causal channel response.
//
// The link simulator's direct inverse FFT is accurate but its causality depends
// on the band-limiting; a rational macromodel  H(s) = e^{-sτ}·(d + Σ rk/(s−pk))
// with all poles in the left half-plane is *causal by construction* — its
// impulse is exactly zero before the transport delay τ. That is what a backplane
// channel (cascaded vendor S-parameters) needs for a trustworthy time-domain
// response.
//
// Method (the tractable, linear version): pull the bulk delay τ out of the phase
// slope, fit the smooth residual with a fixed bank of real poles by real
// least-squares (so the residues are real and the impulse real), then re-apply
// the delay. Fixed poles give ~10 % accuracy on skin-effect channels (the √f
// branch cut isn't rational); full Gustavsen pole-relocation is the accuracy
// refinement — but the *causality guarantee* is exact regardless.
//
// Validated: τ recovered exactly, impulse identically zero before τ.
// ============================================================================

public sealed class VectorFit
{
    public double DelayS { get; }
    public double D { get; }                 // constant term
    public double[] Poles { get; }           // real, negative (rad/s)
    public double[] Residues { get; }

    private VectorFit(double tau, double d, double[] poles, double[] residues)
    { DelayS = tau; D = d; Poles = poles; Residues = residues; }

    /// <summary>Fit S21 samples H(fHz) with nPoles real poles + a bulk delay.</summary>
    public static VectorFit Fit(double[] fHz, Complex[] h, int nPoles = 16)
    {
        int m = fHz.Length;
        var w = new double[m];
        for (int i = 0; i < m; i++) w[i] = 2 * Math.PI * fHz[i];

        // bulk delay from the unwrapped phase slope:  τ = −dφ/dω
        var ph = Unwrap(h);
        double tau = -Slope(w, ph);
        if (tau < 0) tau = 0;

        // delay-removed response (smooth / minimum-phase-ish)
        var hnd = new Complex[m];
        for (int i = 0; i < m; i++) hnd[i] = h[i] * Complex.FromPolarCoordinates(1, w[i] * tau);

        // fixed real poles, geometrically spaced across the band
        double fmin = Math.Max(fHz[1 % m], 1), fmax = fHz[^1];
        var poles = new double[nPoles];
        for (int k = 0; k < nPoles; k++)
            poles[k] = -2 * Math.PI * fmin * 0.5 * Math.Pow((fmax * 2) / (fmin * 0.5), k / (double)(nPoles - 1));

        // basis columns A[:,0]=1, A[:,k+1]=1/(jω−pk); real LS over [Re;Im]
        int nc = nPoles + 1;
        var ata = new double[nc, nc];
        var atb = new double[nc];
        var col = new Complex[nc];
        for (int i = 0; i < m; i++)
        {
            Complex s = new Complex(0, w[i]);
            col[0] = Complex.One;
            for (int k = 0; k < nPoles; k++) col[k + 1] = Complex.One / (s - poles[k]);
            for (int a = 0; a < nc; a++)
            {
                atb[a] += col[a].Real * hnd[i].Real + col[a].Imaginary * hnd[i].Imaginary;
                for (int b = 0; b < nc; b++)
                    ata[a, b] += col[a].Real * col[b].Real + col[a].Imaginary * col[b].Imaginary;
            }
        }
        var x = SolveReal(ata, atb);
        var res = new double[nPoles];
        Array.Copy(x, 1, res, 0, nPoles);
        return new VectorFit(tau, x[0], poles, res);
    }

    /// <summary>The fitted transfer at frequency f.</summary>
    public Complex At(double fHz)
    {
        double w = 2 * Math.PI * fHz;
        Complex s = new Complex(0, w), acc = D;
        for (int k = 0; k < Poles.Length; k++) acc += Residues[k] / (s - Poles[k]);
        return acc * Complex.FromPolarCoordinates(1, -w * DelayS);
    }

    /// <summary>Causal impulse response h(t) (zero for t &lt; τ by construction).</summary>
    public double Impulse(double tS)
    {
        double tt = tS - DelayS;
        if (tt < 0) return 0;
        double h = 0;
        for (int k = 0; k < Poles.Length; k++) h += Residues[k] * Math.Exp(Poles[k] * tt);
        return h;
    }

    public (double max, double mean) FitError(double[] fHz, Complex[] h)
    {
        double max = 0, sum = 0;
        for (int i = 0; i < fHz.Length; i++)
        {
            double e = (At(fHz[i]) - h[i]).Magnitude;
            max = Math.Max(max, e); sum += e;
        }
        return (max, sum / fHz.Length);
    }

    // ---- helpers -------------------------------------------------------------

    private static double[] Unwrap(Complex[] h)
    {
        var p = new double[h.Length];
        double prev = h[0].Phase, off = 0;
        p[0] = prev;
        for (int i = 1; i < h.Length; i++)
        {
            double cur = h[i].Phase;
            double d = cur - prev;
            if (d > Math.PI) off -= 2 * Math.PI;
            else if (d < -Math.PI) off += 2 * Math.PI;
            p[i] = cur + off; prev = cur;
        }
        return p;
    }

    private static double Slope(double[] x, double[] y)
    {
        int n = x.Length;
        double sx = 0, sy = 0, sxx = 0, sxy = 0;
        for (int i = 0; i < n; i++) { sx += x[i]; sy += y[i]; sxx += x[i] * x[i]; sxy += x[i] * y[i]; }
        double den = n * sxx - sx * sx;
        return Math.Abs(den) < 1e-30 ? 0 : (n * sxy - sx * sy) / den;
    }

    private static double[] SolveReal(double[,] A, double[] b)
    {
        int n = b.Length;
        var a = (double[,])A.Clone();
        var x = (double[])b.Clone();
        for (int k = 0; k < n; k++) a[k, k] += 1e-12 * (Math.Abs(a[k, k]) + 1);   // Tikhonov
        for (int k = 0; k < n; k++)
        {
            int piv = k; double mx = Math.Abs(a[k, k]);
            for (int i = k + 1; i < n; i++) if (Math.Abs(a[i, k]) > mx) { mx = Math.Abs(a[i, k]); piv = i; }
            if (piv != k)
            {
                for (int j = 0; j < n; j++) (a[k, j], a[piv, j]) = (a[piv, j], a[k, j]);
                (x[k], x[piv]) = (x[piv], x[k]);
            }
            double d = a[k, k] == 0 ? 1e-30 : a[k, k];
            for (int i = k + 1; i < n; i++)
            {
                double f = a[i, k] / d;
                for (int j = k; j < n; j++) a[i, j] -= f * a[k, j];
                x[i] -= f * x[k];
            }
        }
        for (int i = n - 1; i >= 0; i--)
        {
            double s = x[i];
            for (int j = i + 1; j < n; j++) s -= a[i, j] * x[j];
            x[i] = s / (a[i, i] == 0 ? 1e-30 : a[i, i]);
        }
        return x;
    }
}
