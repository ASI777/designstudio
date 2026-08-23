using System.Numerics;

namespace DesignStudio.Model.LinkSim;

// ============================================================================
// Radix-2 iterative FFT. The link simulator works on power-of-two records, so
// this is all the transform machinery E4 needs — no external dependency, and
// it validates in the Linux CI test project alongside the rest of the model.
// (The roadmap's core/convolve.cpp lives here in managed code for now.)
// ============================================================================

public static class Fft
{
    /// <summary>In-place radix-2 FFT (inverse = true applies 1/N and conj twiddles).</summary>
    public static void Transform(Complex[] a, bool inverse)
    {
        int n = a.Length;
        if (n == 0 || (n & (n - 1)) != 0)
            throw new ArgumentException("FFT length must be a power of two.");

        // bit-reversal permutation
        for (int i = 1, j = 0; i < n; i++)
        {
            int bit = n >> 1;
            for (; (j & bit) != 0; bit >>= 1) j ^= bit;
            j ^= bit;
            if (i < j) (a[i], a[j]) = (a[j], a[i]);
        }

        for (int len = 2; len <= n; len <<= 1)
        {
            double ang = 2 * Math.PI / len * (inverse ? 1 : -1);
            Complex wlen = new(Math.Cos(ang), Math.Sin(ang));
            for (int i = 0; i < n; i += len)
            {
                Complex w = Complex.One;
                for (int k = 0; k < len / 2; k++)
                {
                    Complex u = a[i + k];
                    Complex v = a[i + k + len / 2] * w;
                    a[i + k] = u + v;
                    a[i + k + len / 2] = u - v;
                    w *= wlen;
                }
            }
        }

        if (inverse)
            for (int i = 0; i < n; i++) a[i] /= n;
    }

    /// <summary>
    /// Inverse transform of a single-sided spectrum (DC..Nyquist, length N/2+1)
    /// into a real time-domain record of length N, enforcing Hermitian symmetry
    /// so the result is real. spectrum[0] (DC) and spectrum[N/2] (Nyquist) are
    /// forced real.
    /// </summary>
    public static double[] RealIfft(Complex[] singleSided, int n)
    {
        if (singleSided.Length != n / 2 + 1)
            throw new ArgumentException("single-sided spectrum must have N/2+1 points.");
        var full = new Complex[n];
        full[0] = new Complex(singleSided[0].Real, 0);
        full[n / 2] = new Complex(singleSided[n / 2].Real, 0);
        for (int k = 1; k < n / 2; k++)
        {
            full[k] = singleSided[k];
            full[n - k] = Complex.Conjugate(singleSided[k]);
        }
        Transform(full, inverse: true);
        var re = new double[n];
        for (int i = 0; i < n; i++) re[i] = full[i].Real;
        return re;
    }

    /// <summary>Circular convolution of two real records via FFT (lengths must match, power of two).</summary>
    public static double[] Convolve(double[] x, double[] y)
    {
        int n = x.Length;
        var X = new Complex[n];
        var Y = new Complex[n];
        for (int i = 0; i < n; i++) { X[i] = x[i]; Y[i] = y[i]; }
        Transform(X, false);
        Transform(Y, false);
        for (int i = 0; i < n; i++) X[i] *= Y[i];
        Transform(X, true);
        var r = new double[n];
        for (int i = 0; i < n; i++) r[i] = X[i].Real;
        return r;
    }
}
