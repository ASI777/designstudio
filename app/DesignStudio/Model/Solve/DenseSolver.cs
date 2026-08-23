using DesignStudio.Interop;

namespace DesignStudio.Model.Solve;

// ============================================================================
// E20. Dense linear-algebra hot path.
//
// The Method-of-Moments RLGC extraction (MoM2D) and the FDM PDN solver spend
// almost all of their time in a dense LU factor + solve of an N×N system
// (N≈400 for a meshed cross-section), repeated per frequency × per net. This is
// the single place that work funnels through, so it is the natural C++ migration
// target named in the roadmap.
//
// The managed LU here is the always-correct reference and the default; when the
// native core library is present, `SolveColumns` routes the factor+solve through
// it (`dc_dense_lu_solve`) for the throughput win, and the two paths agree to
// numerical precision (same pivoting, same singular-pivot clamp). `UseNative`
// lets callers and tests force the managed path.
// ============================================================================

public static class DenseSolver
{
    /// <summary>When true and the native core is available, the solve runs in C++.</summary>
    public static bool UseNative { get; set; } = true;

    /// <summary>True if the last <see cref="SolveColumns"/> call used the native path.</summary>
    public static bool LastUsedNative { get; private set; }

    /// <summary>
    /// Solve A·xₖ = bₖ for every right-hand side column. A is factored once;
    /// results preserve column order. Tries the native LU, falls back to managed.
    /// </summary>
    public static double[][] SolveColumns(double[,] a, double[][] rhsColumns)
    {
        int n = a.GetLength(0);
        if (a.GetLength(1) != n) throw new ArgumentException("matrix must be square");

        if (UseNative)
        {
            var flat = ToRowMajor(a, n);
            var native = NativeMath.TrySolveColumns(flat, n, rhsColumns);
            if (native != null) { LastUsedNative = true; return native; }
        }

        LastUsedNative = false;
        var lu = Factor(a);
        var outCols = new double[rhsColumns.Length][];
        for (int c = 0; c < rhsColumns.Length; c++) outCols[c] = lu.Solve(rhsColumns[c]);
        return outCols;
    }

    private static double[] ToRowMajor(double[,] a, int n)
    {
        var flat = new double[n * n];
        for (int i = 0; i < n; i++)
            for (int j = 0; j < n; j++) flat[i * n + j] = a[i, j];
        return flat;
    }

    // ---- managed LU with partial pivoting (the reference) --------------------

    public sealed class Lu
    {
        public double[,] A = null!;
        public int[] P = null!;
        public int N;

        public double[] Solve(double[] b)
        {
            int n = N; var a = A; var y = new double[n];
            for (int i = 0; i < n; i++)
            {
                double s = b[P[i]];
                for (int j = 0; j < i; j++) s -= a[i, j] * y[j];
                y[i] = s;
            }
            var x = new double[n];
            for (int i = n - 1; i >= 0; i--)
            {
                double s = y[i];
                for (int j = i + 1; j < n; j++) s -= a[i, j] * x[j];
                x[i] = s / (Math.Abs(a[i, i]) < 1e-300 ? 1e-300 : a[i, i]);
            }
            return x;
        }
    }

    public static Lu Factor(double[,] src)
    {
        int n = src.GetLength(0);
        var a = (double[,])src.Clone();
        var p = new int[n];
        for (int i = 0; i < n; i++) p[i] = i;
        for (int k = 0; k < n; k++)
        {
            int piv = k; double max = Math.Abs(a[k, k]);
            for (int i = k + 1; i < n; i++)
                if (Math.Abs(a[i, k]) > max) { max = Math.Abs(a[i, k]); piv = i; }
            if (piv != k)
            {
                for (int j = 0; j < n; j++) (a[k, j], a[piv, j]) = (a[piv, j], a[k, j]);
                (p[k], p[piv]) = (p[piv], p[k]);
            }
            double akk = a[k, k];
            if (Math.Abs(akk) < 1e-300) akk = 1e-300;
            for (int i = k + 1; i < n; i++)
            {
                double f = a[i, k] / akk; a[i, k] = f;
                for (int j = k + 1; j < n; j++) a[i, j] -= f * a[k, j];
            }
        }
        return new Lu { A = a, P = p, N = n };
    }
}
