using System.Text.Json;

namespace DesignStudio.Model;

// ============================================================================
// 5.1 2.5D steady-state thermal solver.
//
// The board becomes a 2D conduction grid whose in-plane conductivity blends
// FR-4 (0.3 W/mK) with copper (385 W/mK) by local copper cross-section —
// traces, planes and pours all count, summed over every layer. Components
// inject their dissipation (electrical.thermal.max_power_w from the
// datasheet JSON); both faces lose heat by natural convection
// (h ≈ 10 W/m²K). Gauss–Seidel solves the balance; the result is a board
// temperature field. Each part's junction estimate Tj = Tboard + P·θjc
// (falling back to θja against ambient) is checked against Tj(max) and
// raises DRC rule 23.
// ============================================================================

public static class Thermal
{
    public const double AmbientC = 25;
    private const double KFr4 = 0.3, KCu = 385;          // W/mK
    private const double HConv = 10;                     // W/m²K, natural convection, each face

    public record PartTemp(FootprintItem Fp, double PowerW, double BoardC, double JunctionC, double? TjMaxC);
    public record Report(double MaxBoardC, double HotX, double HotY, List<PartTemp> Parts, string? Skipped);

    public static Report Analyze(BoardDocument doc, FootprintLibrary? library)
    {
        var sources = HeatSources(doc, library);
        if (sources.Count == 0)
            return new(AmbientC, 0, 0, new(), "no component declares dissipation (electrical.thermal.max_power_w)");

        // ---- grid ----
        double step = Math.Clamp(Math.Max(doc.BoardWidthMm, doc.BoardHeightMm) / 60.0, 0.5, 2.5);
        int nx = (int)Math.Ceiling(doc.BoardWidthMm / step) + 1;
        int ny = (int)Math.Ceiling(doc.BoardHeightMm / step) + 1;

        // effective conductance·thickness map (W/K per square) from copper coverage
        double boardT = BoardThicknessM(doc);
        var kt = new double[nx, ny];
        for (int i = 0; i < nx; i++)
            for (int j = 0; j < ny; j++)
                kt[i, j] = KFr4 * boardT;                                     // substrate baseline
        doc.PlanesUpToDate();
        double cuT = (doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup.Average(l => l.CopperThicknessMm) : doc.CopperThicknessMm) * 1e-3;
        foreach (var t in doc.Traces)
            StampSegment(kt, nx, ny, step, t.Ax, t.Ay, t.Bx, t.By, KCu * cuT * Math.Min(1, t.Width / step));
        foreach (var plane in doc.Planes)
            for (int i = 0; i < nx; i++)
                for (int j = 0; j < ny; j++)
                    if (kt[i, j] < KFr4 * boardT + KCu * cuT && InPlane(plane, i * step, j * step))
                        kt[i, j] += KCu * cuT;

        // heat input per cell
        var q = new double[nx, ny];
        foreach (var (fp, p) in sources)
        {
            int ci = Math.Clamp((int)Math.Round(fp.X / step), 0, nx - 1);
            int cj = Math.Clamp((int)Math.Round(fp.Y / step), 0, ny - 1);
            q[ci, cj] += p;
        }

        // ---- Gauss–Seidel: conduction + convection both faces ----
        double cellAreaM2 = step * step * 1e-6;
        double conv = 2 * HConv * cellAreaM2;                                // W/K per cell
        var temp = new double[nx, ny];
        for (int i = 0; i < nx; i++) for (int j = 0; j < ny; j++) temp[i, j] = AmbientC;
        for (int iter = 0; iter < 3000; iter++)
        {
            double maxDelta = 0;
            for (int i = 0; i < nx; i++)
                for (int j = 0; j < ny; j++)
                {
                    double gSum = conv, tSum = conv * AmbientC;
                    void N(int ii, int jj)
                    {
                        if (ii < 0 || ii >= nx || jj < 0 || jj >= ny) return;
                        double g = (kt[i, j] + kt[ii, jj]) / 2;              // W/K per square ≈ per cell pair
                        gSum += g; tSum += g * temp[ii, jj];
                    }
                    N(i - 1, j); N(i + 1, j); N(i, j - 1); N(i, j + 1);
                    double nt = (tSum + q[i, j]) / gSum;
                    maxDelta = Math.Max(maxDelta, Math.Abs(nt - temp[i, j]));
                    temp[i, j] = nt;
                }
            if (maxDelta < 1e-4) break;
        }

        double hot = AmbientC, hx = 0, hy = 0;
        for (int i = 0; i < nx; i++)
            for (int j = 0; j < ny; j++)
                if (temp[i, j] > hot) { hot = temp[i, j]; hx = i * step; hy = j * step; }

        // junction estimates
        var parts = new List<PartTemp>();
        foreach (var (fp, p) in sources)
        {
            int ci = Math.Clamp((int)Math.Round(fp.X / step), 0, nx - 1);
            int cj = Math.Clamp((int)Math.Round(fp.Y / step), 0, ny - 1);
            var th = ThermalData(doc, fp, library);
            double tj = th?.ThetaJcCw is double jc
                ? temp[ci, cj] + p * jc
                : th?.ThetaJaCw is double ja ? AmbientC + p * ja : temp[ci, cj] + p * 20;
            parts.Add(new PartTemp(fp, p, temp[ci, cj], tj, th?.TjMaxC));
        }
        return new(hot, hx, hy, parts, null);
    }

    /// <summary>Rule 23: junction temperature vs the datasheet limit (or 125 °C default).</summary>
    public static List<DrcResultItem> Check(BoardDocument doc, FootprintLibrary? library)
    {
        var results = new List<DrcResultItem>();
        var r = Analyze(doc, library);
        if (r.Skipped != null) return results;
        foreach (var p in r.Parts)
        {
            double limit = p.TjMaxC ?? 125;
            if (p.JunctionC > limit)
                results.Add(new DrcResultItem(23, "Thermal",
                    $"{p.Fp.RefDes}: junction estimate {p.JunctionC:F0} °C exceeds Tj(max) {limit:F0} °C " +
                    $"(dissipating {p.PowerW:F1} W, board {p.BoardC:F0} °C locally) — " +
                    "add copper area/thermal vias under the part or spread the heat sources.",
                    p.Fp.X, p.Fp.Y));
        }
        return results;
    }

    private static List<(FootprintItem fp, double powerW)> HeatSources(BoardDocument doc, FootprintLibrary? library)
    {
        var list = new List<(FootprintItem, double)>();
        foreach (var fp in doc.Footprints)
        {
            var th = ThermalData(doc, fp, library);
            if (th?.MaxPowerW is double p && p > 0.05) list.Add((fp, p));
        }
        return list;
    }

    private static DsThermal? ThermalData(BoardDocument doc, FootprintItem fp, FootprintLibrary? library)
    {
        var def = library?.Items.FirstOrDefault(d => d.Name == fp.LibName);
        if (string.IsNullOrWhiteSpace(def?.ElectricalJson)) return null;
        try { return JsonSerializer.Deserialize<DsElectrical>(def!.ElectricalJson)?.Thermal; }
        catch (JsonException) { return null; }
    }

    private static double BoardThicknessM(BoardDocument doc)
    {
        if (doc.Stackup.Count != doc.CopperLayers)
            return Math.Max((doc.CopperLayers - 1) * (doc.DielectricHeightMm + doc.CopperThicknessMm), 0.8) * 1e-3;
        double t = 0;
        for (int i = 0; i < doc.Stackup.Count; i++)
        {
            t += doc.Stackup[i].CopperThicknessMm;
            if (i + 1 < doc.Stackup.Count) t += doc.Stackup[i].DielectricHeightMm;
        }
        return Math.Max(t, 0.4) * 1e-3;
    }

    private static void StampSegment(double[,] kt, int nx, int ny, double step,
                                     double ax, double ay, double bx, double by, double add)
    {
        double len = Math.Sqrt((bx - ax) * (bx - ax) + (by - ay) * (by - ay));
        int n = Math.Max(1, (int)(len / step));
        for (int k = 0; k <= n; k++)
        {
            double x = ax + (bx - ax) * k / n, y = ay + (by - ay) * k / n;
            int i = (int)Math.Round(x / step), j = (int)Math.Round(y / step);
            if (i >= 0 && i < nx && j >= 0 && j < ny) kt[i, j] += add / (k == 0 || k == n ? 2 : 1) / n;
        }
    }

    private static bool InPlane(PlaneShape plane, double x, double y)
    {
        int crossings = 0;
        foreach (var poly in plane.Fill)
            for (int i = 0; i < poly.Count; i++)
            {
                var (x1, y1) = poly[i];
                var (x2, y2) = poly[(i + 1) % poly.Count];
                if ((y1 > y) != (y2 > y) && x < x1 + (y - y1) / (y2 - y1) * (x2 - x1))
                    crossings++;
            }
        return (crossings & 1) == 1;
    }
}
