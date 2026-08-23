using System.Runtime.InteropServices;

namespace DesignStudio.Interop;

/// <summary>
/// P/Invoke surface for the C++ core engine (designcore.dll).
/// Coordinates are nanometres (long) — same convention as the C++ side.
/// Every call returns an explicit status or count; large results are stored on
/// the native handle and fetched in a second call, so nothing truncates.
/// </summary>
public static class NativeCore
{
    private const string Dll = "designcore";
    public const long NmPerMm = 1_000_000;

    // Mirrors DcStatus in c_api.h.
    public const int Ok = 0;
    public const int ErrInvalidHandle = -1;
    public const int ErrInvalidArg = -2;
    public const int ErrNotFound = -3;
    public const int ErrRouteNoPath = -10;
    public const int ErrRouteIterLimit = -11;
    public const int ErrRouteStartBlocked = -12;
    public const int ErrRouteGoalBlocked = -13;

    public static string RouteStatusText(int status) => status switch
    {
        Ok => "ok",
        ErrRouteNoPath => "no path exists at this width/clearance",
        ErrRouteIterLimit => "search limit reached — board too dense for the grid step",
        ErrRouteStartBlocked => "start point is inside another net's keepout",
        ErrRouteGoalBlocked => "target point is inside another net's keepout",
        _ => $"router error {status}"
    };

    [StructLayout(LayoutKind.Sequential)]
    public struct DcPoint { public long X, Y; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    public struct DcPadDef
    {
        public long X, Y, W, H;
        public int NetId;
        public int ThroughHole;
        public long Drill;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 16)]
        public string Name;
    }

    // ---- board lifecycle ----
    [DllImport(Dll)] public static extern IntPtr dc_board_create();
    [DllImport(Dll)] public static extern void dc_board_destroy(IntPtr board);
    [DllImport(Dll)] public static extern int dc_board_clear(IntPtr board);
    [DllImport(Dll)] public static extern int dc_board_set_outline(IntPtr board, long x0, long y0, long x1, long y1);
    [DllImport(Dll)] public static extern int dc_board_set_copper_layers(IntPtr board, int count);

    // ---- nets & classes ----
    [DllImport(Dll, CharSet = CharSet.Ansi)]
    public static extern int dc_net_set(IntPtr board, int netId, string name, int classId);
    [DllImport(Dll)] public static extern int dc_net_remove(IntPtr board, int netId);
    [DllImport(Dll, CharSet = CharSet.Ansi)]
    public static extern int dc_class_set(IntPtr board, int classId, string name,
        long clearance, long traceWidth, long viaDiameter, long viaDrill,
        long diffPairGap, long maxSkew, int allowMicrovia);

    // ---- items ----
    [DllImport(Dll, CharSet = CharSet.Ansi)]
    public static extern ulong dc_footprint_add(IntPtr board, string refDes, string libName,
        long x, long y, double rotationDeg, int side, DcPadDef[] pads, int padCount);
    [DllImport(Dll)] public static extern int dc_footprint_move(IntPtr board, ulong id, long x, long y, double rotationDeg);
    [DllImport(Dll)] public static extern int dc_item_remove(IntPtr board, ulong id);
    [DllImport(Dll)]
    public static extern ulong dc_trace_add(IntPtr board, long ax, long ay, long bx, long by,
        long width, int layer, int netId, int isPour);
    [DllImport(Dll)]
    public static extern ulong dc_via_add(IntPtr board, long x, long y, long diameter, long drill,
        int netId, int fromLayer, int toLayer);

    // ---- routing ----
    [StructLayout(LayoutKind.Sequential)]
    public struct DcRouteRequest
    {
        public int NetId;
        public long Sx, Sy; public int StartLayer;
        public long Gx, Gy; public int GoalLayer;
        public long TraceWidth, Clearance, GridStep;
        public int AllowVias, AllowMicrovia;
        public long ViaDiameter, ViaDrill;
        public double ViaCostMm;
        public long MaxExpansions;
        public long MinDrillToDrill;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct DcRoutePoint { public long X, Y; public int Layer; public int _pad; }

    [StructLayout(LayoutKind.Sequential)]
    public struct DcRouteVia { public long X, Y; public int FromLayer, ToLayer; }

    [DllImport(Dll)] public static extern int dc_route_run(IntPtr board, ref DcRouteRequest req);
    [DllImport(Dll)] public static extern int dc_route_point_count(IntPtr board);
    [DllImport(Dll)] public static extern int dc_route_get_points(IntPtr board, [Out] DcRoutePoint[] outPoints, int cap);
    [DllImport(Dll)] public static extern int dc_route_via_count(IntPtr board);
    [DllImport(Dll)] public static extern int dc_route_get_vias(IntPtr board, [Out] DcRouteVia[] outVias, int cap);

    // ---- DRC ----
    [StructLayout(LayoutKind.Sequential)]
    public struct DcDrcOptions
    {
        public long DefaultClearance, MinTraceWidth, MinDrill, MinAnnularRing, MinDrillToDrill;
        public int CheckConnectivity, CheckSkew;
    }

    /// <summary>Mirrors DcDrcViolation in c_api.h. Rule codes 1..13 mirror dc::DrcRule.</summary>
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    public struct DcDrcViolation
    {
        public int Rule;
        public int _pad;
        public long X, Y;
        public ulong ItemA, ItemB;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 192)]
        public string Message;
    }

    [DllImport(Dll)] public static extern int dc_drc_run(IntPtr board, ref DcDrcOptions opt);
    [DllImport(Dll)] public static extern int dc_drc_get(IntPtr board, int index, out DcDrcViolation violation);

    // ---- pours ----
    [StructLayout(LayoutKind.Sequential)]
    public struct DcPourStroke { public long Ax, Ay, Bx, By, Width; }

    [DllImport(Dll)]
    public static extern int dc_pour_run(IntPtr board, int netId, int layer, long lineWidth, long clearance,
        long x0, long y0, long x1, long y1);
    [DllImport(Dll)] public static extern int dc_pour_get(IntPtr board, int index, out DcPourStroke stroke);

    // ---- queries ----
    [DllImport(Dll)] public static extern long dc_net_length(IntPtr board, int netId);
    [DllImport(Dll)] public static extern int dc_net_islands(IntPtr board, int netId);

    // ---- physics & mathematics engine (stateless; mm / Hz / °C / Ω) ----
    [DllImport(Dll)] public static extern int dc_phys_microstrip(double wMm, double hMm, double tMm, double er,
        out double z0, out double eEff, out double delayPsPerMm);
    [DllImport(Dll)] public static extern double dc_phys_microstrip_width(double targetZ0, double hMm, double tMm, double er);
    [DllImport(Dll)] public static extern int dc_phys_stripline(double wMm, double bMm, double tMm, double er,
        out double z0, out double delayPsPerMm);
    [DllImport(Dll)] public static extern double dc_phys_stripline_width(double targetZ0, double bMm, double tMm, double er);
    [DllImport(Dll)] public static extern double dc_phys_diff_microstrip(double wMm, double sMm, double hMm, double tMm, double er);
    [DllImport(Dll)] public static extern double dc_phys_diff_stripline(double wMm, double sMm, double bMm, double tMm, double er);
    [DllImport(Dll)] public static extern double dc_phys_diff_microstrip_width(double targetZdiff, double sMm, double hMm, double tMm, double er);
    [DllImport(Dll)] public static extern double dc_phys_skin_depth_mm(double fHz);
    [DllImport(Dll)] public static extern double dc_phys_microstrip_loss_db_per_m(double wMm, double hMm, double tMm, double er,
        double tanDelta, double fHz);
    [DllImport(Dll)] public static extern double dc_phys_trace_resistance(double wMm, double tMm, double lengthMm, double fHz, double tempC);
    [DllImport(Dll)] public static extern double dc_phys_ipc2221_current(double widthMm, double thickMm, double deltaTC, int external);
    [DllImport(Dll)] public static extern double dc_phys_onderdonk_current(double widthMm, double thickMm, double seconds, double ambientC);
    [DllImport(Dll)] public static extern double dc_phys_via_inductance_nh(double heightMm, double drillMm);
    [DllImport(Dll)] public static extern double dc_phys_via_capacitance_pf(double er, double boardThickMm, double padMm, double antipadMm);
    [DllImport(Dll)] public static extern double dc_phys_via_resistance(double heightMm, double drillMm, double platingMm, double tempC);
    [DllImport(Dll)] public static extern double dc_phys_via_thermal_kw(double heightMm, double drillMm, double platingMm);
    [DllImport(Dll)] public static extern double dc_phys_via_current(double drillMm, double platingMm, double deltaTC);
    [DllImport(Dll)] public static extern double dc_phys_crosstalk(double sMm, double hMm);
    [DllImport(Dll)] public static extern double dc_phys_plane_capacitance_pf(double areaMm2, double dielectricMm, double er);

    // ---- dense linear algebra (SI hot path, E20) ----
    // A: n*n row-major (overwritten with LU); B/X: n*rhs column-major blocks.
    [DllImport(Dll)] public static extern int dc_dense_lu_solve(
        [In, Out] double[] a, int n, [In] double[] b, int rhs, [Out] double[] x);
}

/// <summary>
/// Optional native acceleration for the dense-solver hot path. Probes once for
/// the core library; if it (or the symbol) is absent, <see cref="Available"/> is
/// false and callers fall back to the managed solver. Nothing here is required
/// for correctness — the native and managed paths produce identical results.
/// </summary>
public static class NativeMath
{
    private static int _state;   // 0 = unprobed, 1 = available, -1 = unavailable

    public static bool Available
    {
        get
        {
            if (_state != 0) return _state == 1;
            try
            {
                // a trivial 1×1 solve confirms the symbol resolves and runs
                var a = new double[] { 2.0 };
                var b = new double[] { 4.0 };
                var x = new double[1];
                int rc = NativeCore.dc_dense_lu_solve(a, 1, b, 1, x);
                _state = (rc == NativeCore.Ok && Math.Abs(x[0] - 2.0) < 1e-9) ? 1 : -1;
            }
            catch (DllNotFoundException) { _state = -1; }
            catch (EntryPointNotFoundException) { _state = -1; }
            catch (BadImageFormatException) { _state = -1; }
            return _state == 1;
        }
    }

    /// <summary>
    /// Solve A·X = B for several right-hand sides via the native LU. Returns the
    /// solution columns, or null when the native path is unavailable (caller
    /// should fall back). <paramref name="a"/> is row-major n×n and is consumed.
    /// </summary>
    public static double[][]? TrySolveColumns(double[] aRowMajor, int n, double[][] rhsColumns)
    {
        if (!Available) return null;
        int m = rhsColumns.Length;
        var b = new double[n * m];
        for (int c = 0; c < m; c++)
            for (int i = 0; i < n; i++) b[c * n + i] = rhsColumns[c][i];
        var x = new double[n * m];
        int rc = NativeCore.dc_dense_lu_solve(aRowMajor, n, b, m, x);
        if (rc != NativeCore.Ok) return null;
        var outCols = new double[m][];
        for (int c = 0; c < m; c++)
        {
            var col = new double[n];
            Array.Copy(x, c * n, col, 0, n);
            outCols[c] = col;
        }
        return outCols;
    }
}

/// <summary>
/// Safe wrapper owning the native board handle. Creation is lazy: documents
/// that never run a native operation (pure model edits, serialization, export)
/// never load the native library at all.
/// </summary>
public sealed class CoreBoard : IDisposable
{
    private IntPtr _handle;
    public IntPtr Handle
    {
        get
        {
            if (_handle == IntPtr.Zero) _handle = NativeCore.dc_board_create();
            return _handle;
        }
    }
    public void Dispose()
    {
        if (_handle != IntPtr.Zero) { NativeCore.dc_board_destroy(_handle); _handle = IntPtr.Zero; }
        GC.SuppressFinalize(this);
    }
    ~CoreBoard() => Dispose();
}
