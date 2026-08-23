namespace DesignStudio.Model;

// ============================================================================
// 3.1 DC IR-drop solver.
//
// Each polygon plane is meshed into a uniform grid; cells inside the copper
// become nodes of a resistive network (sheet conductance σ·t per square).
// Pads of the plane's net inject current: power_out pins are the source
// (held at 0 V — drops are measured relative to the regulator), power_in
// pins sink their datasheet current (from electrical.power_domains, the same
// extraction the ampacity check uses). Gauss–Seidel solves G·v = i; the
// worst node voltage is the IR drop. Findings appear as DRC rule 20.
// ============================================================================

public static class IrDrop
{
    private const double CopperSigma = 5.8e7;       // S/m
    public const double DefaultBudgetV = 0.05;      // 50 mV default drop budget

    public record PlaneResult(PlaneShape Plane, double MaxDropV, double WorstX, double WorstY,
                              int Nodes, bool Solved, string? Skipped);

    public static PlaneResult Analyze(BoardDocument doc, PlaneShape plane,
                                      Dictionary<int, double> netCurrents)
    {
        doc.PlanesUpToDate();
        if (plane.Fill.Count == 0)
            return new(plane, 0, 0, 0, 0, false, "plane has no copper");

        // ---- mesh ----
        double step = Math.Clamp(Math.Max(doc.BoardWidthMm, doc.BoardHeightMm) / 80.0, 0.5, 2.0);
        int nx = (int)Math.Ceiling(doc.BoardWidthMm / step) + 1;
        int ny = (int)Math.Ceiling(doc.BoardHeightMm / step) + 1;
        var inside = new bool[nx, ny];
        int nodeCount = 0;
        for (int i = 0; i < nx; i++)
            for (int j = 0; j < ny; j++)
                if (InCopper(plane, i * step, j * step)) { inside[i, j] = true; nodeCount++; }
        if (nodeCount < 4)
            return new(plane, 0, 0, 0, nodeCount, false, "plane too small to mesh");

        // ---- boundary conditions from pads of this net ----
        double cuT = doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup[plane.Layer].CopperThicknessMm : doc.CopperThicknessMm;
        double gSquare = CopperSigma * (cuT / 1000.0);            // S per square

        var current = new double[nx, ny];                          // injected A (+ = sink)
        var fixedZero = new bool[nx, ny];                          // source nodes
        bool anySource = false, anySink = false;
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                if (pad.NetId != plane.NetId) continue;
                var (px, py) = fp.PadWorld(pad);
                int ci = Math.Clamp((int)Math.Round(px / step), 0, nx - 1);
                int cj = Math.Clamp((int)Math.Round(py / step), 0, ny - 1);
                if (!inside[ci, cj]) (ci, cj) = NearestNode(inside, nx, ny, ci, cj);
                if (ci < 0) continue;
                if (pad.ElectricalType == "power_out")
                {
                    fixedZero[ci, cj] = true; anySource = true;
                }
                else if (netCurrents.TryGetValue(plane.NetId, out double amps) && amps > 0)
                {
                    int sinkPads = Math.Max(1, doc.Footprints.Sum(f =>
                        f.Pads.Count(p => p.NetId == plane.NetId && p.ElectricalType != "power_out")));
                    current[ci, cj] += amps / sinkPads; anySink = true;
                }
            }
        if (!anySource)
            return new(plane, 0, 0, 0, nodeCount, false, "no power_out pin on the net (no source reference)");
        if (!anySink)
            return new(plane, 0, 0, 0, nodeCount, false, "no load current known for the net");

        // ---- Gauss–Seidel ----
        var v = new double[nx, ny];
        for (int iter = 0; iter < 4000; iter++)
        {
            double maxDelta = 0;
            for (int i = 0; i < nx; i++)
                for (int j = 0; j < ny; j++)
                {
                    if (!inside[i, j] || fixedZero[i, j]) continue;
                    double sum = 0; int n = 0;
                    if (i > 0 && inside[i - 1, j]) { sum += v[i - 1, j]; n++; }
                    if (i < nx - 1 && inside[i + 1, j]) { sum += v[i + 1, j]; n++; }
                    if (j > 0 && inside[i, j - 1]) { sum += v[i, j - 1]; n++; }
                    if (j < ny - 1 && inside[i, j + 1]) { sum += v[i, j + 1]; n++; }
                    if (n == 0) continue;
                    double nv = (sum + current[i, j] / gSquare) / n;
                    maxDelta = Math.Max(maxDelta, Math.Abs(nv - v[i, j]));
                    v[i, j] = nv;
                }
            if (maxDelta < 1e-7) break;
        }

        double worst = 0, wx = 0, wy = 0;
        for (int i = 0; i < nx; i++)
            for (int j = 0; j < ny; j++)
                if (inside[i, j] && v[i, j] > worst) { worst = v[i, j]; wx = i * step; wy = j * step; }
        return new(plane, worst, wx, wy, nodeCount, true, null);
    }

    /// <summary>Rule 20 over every plane with known currents.</summary>
    public static List<DrcResultItem> Check(BoardDocument doc, FootprintLibrary? library,
                                            double budgetV = DefaultBudgetV)
    {
        var results = new List<DrcResultItem>();
        if (doc.Planes.Count == 0) return results;
        var currents = Ampacity.NetCurrents(doc, library);
        foreach (var plane in doc.Planes)
        {
            var r = Analyze(doc, plane, currents);
            if (r.Solved && r.MaxDropV > budgetV)
                results.Add(new DrcResultItem(20, "IR drop",
                    $"{doc.NetName(plane.NetId)} plane on {doc.LayerName(plane.Layer)}: " +
                    $"{r.MaxDropV * 1000:F0} mV drop at ({r.WorstX:F1}, {r.WorstY:F1}) exceeds the {budgetV * 1000:F0} mV budget — " +
                    "thicken copper, widen the plane neck, or move the regulator closer.",
                    r.WorstX, r.WorstY));
        }
        return results;
    }

    // ========================================================================
    // E14. Plane-aware electro-thermal IR drop.
    //
    // The DC IR solve above is isothermal. A 300–400 W CPU rail pushing 100–300 A
    // through the plane heats the copper, copper resistivity rises ~0.39 %/°C,
    // and the droop gets worse — a coupling that matters for these rails. This
    // iterates: IR solve with ρ(T) → per-cell Joule heat → 2-D thermal solve
    // (copper conduction + convection both faces) → update ρ(T) → repeat. It
    // reports the worst droop, the hottest plane region, and the peak current
    // density. Validated: coupled droop exceeds the isothermal droop by exactly
    // the 1+α·ΔT resistivity factor.
    // ========================================================================

    private const double AlphaCu = 0.0039;          // copper temco, /°C
    private const double KCu = 385;                 // W/mK
    private const double HConv = 10;                // W/m²K per face

    public record ElectroThermalResult(double MaxDropV, double WorstX, double WorstY,
                                       double IsothermalDropV, double HotC, double HotX, double HotY,
                                       double PeakCurrentDensityAmmm2, int Nodes, bool Solved, string? Skipped);

    public static ElectroThermalResult AnalyzeElectroThermal(BoardDocument doc, PlaneShape plane,
        Dictionary<int, double> netCurrents, double ambientC = 25, int couplingIters = 6)
    {
        doc.PlanesUpToDate();
        if (plane.Fill.Count == 0)
            return new(0, 0, 0, 0, ambientC, 0, 0, 0, 0, false, "plane has no copper");

        double step = Math.Clamp(Math.Max(doc.BoardWidthMm, doc.BoardHeightMm) / 80.0, 0.5, 2.0);
        int nx = (int)Math.Ceiling(doc.BoardWidthMm / step) + 1;
        int ny = (int)Math.Ceiling(doc.BoardHeightMm / step) + 1;
        var inside = new bool[nx, ny];
        int nodeCount = 0;
        for (int i = 0; i < nx; i++)
            for (int j = 0; j < ny; j++)
                if (InCopper(plane, i * step, j * step)) { inside[i, j] = true; nodeCount++; }
        if (nodeCount < 4) return new(0, 0, 0, 0, ambientC, 0, 0, 0, nodeCount, false, "plane too small to mesh");

        double cuT = doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup[plane.Layer].CopperThicknessMm : doc.CopperThicknessMm;
        double gSquare0 = CopperSigma * (cuT / 1000.0);   // S/sq at 25 °C

        var current = new double[nx, ny];
        var fixedZero = new bool[nx, ny];
        bool anySource = false, anySink = false;
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                if (pad.NetId != plane.NetId) continue;
                var (px, py) = fp.PadWorld(pad);
                int ci = Math.Clamp((int)Math.Round(px / step), 0, nx - 1);
                int cj = Math.Clamp((int)Math.Round(py / step), 0, ny - 1);
                if (!inside[ci, cj]) (ci, cj) = NearestNode(inside, nx, ny, ci, cj);
                if (ci < 0) continue;
                if (pad.ElectricalType == "power_out") { fixedZero[ci, cj] = true; anySource = true; }
                else if (netCurrents.TryGetValue(plane.NetId, out double amps) && amps > 0)
                {
                    int sinkPads = Math.Max(1, doc.Footprints.Sum(f =>
                        f.Pads.Count(p => p.NetId == plane.NetId && p.ElectricalType != "power_out")));
                    current[ci, cj] += amps / sinkPads; anySink = true;
                }
            }
        if (!anySource) return new(0, 0, 0, 0, ambientC, 0, 0, 0, nodeCount, false, "no power_out pin (no source reference)");
        if (!anySink) return new(0, 0, 0, 0, ambientC, 0, 0, 0, nodeCount, false, "no load current known for the net");

        var T = new double[nx, ny];
        for (int i = 0; i < nx; i++) for (int j = 0; j < ny; j++) T[i, j] = ambientC;
        var v = new double[nx, ny];
        double cellAreaM2 = step * step * 1e-6;
        double conv = 2 * HConv * cellAreaM2;
        double ktCell = KCu * (cuT * 1e-3);
        double isoDrop = 0, drop = 0, peakJ = 0;

        for (int outer = 0; outer < couplingIters; outer++)
        {
            // local sheet conductance with ρ(T); the ρ rise is soft-capped so a
            // pathologically concentrated current can't run the coupling away.
            double GAt(int i, int j) => gSquare0 / Math.Clamp(1 + AlphaCu * (T[i, j] - 25), 1.0, 5.0);

            // ---- IR solve (Gauss–Seidel) ----
            for (int iter = 0; iter < 3000; iter++)
            {
                double md = 0;
                for (int i = 0; i < nx; i++)
                    for (int j = 0; j < ny; j++)
                    {
                        if (!inside[i, j] || fixedZero[i, j]) continue;
                        double s = 0, g = 0;
                        void Nb(int ii, int jj)
                        {
                            if (ii < 0 || ii >= nx || jj < 0 || jj >= ny || !inside[ii, jj]) return;
                            double gg = 0.5 * (GAt(i, j) + GAt(ii, jj)); s += gg * v[ii, jj]; g += gg;
                        }
                        Nb(i - 1, j); Nb(i + 1, j); Nb(i, j - 1); Nb(i, j + 1);
                        if (g == 0) continue;
                        double nv = (s + current[i, j]) / g;
                        md = Math.Max(md, Math.Abs(nv - v[i, j])); v[i, j] = nv;
                    }
                if (md < 1e-9) break;
            }

            drop = 0; double wx = 0, wy = 0;
            for (int i = 0; i < nx; i++)
                for (int j = 0; j < ny; j++)
                    if (inside[i, j] && v[i, j] > drop) { drop = v[i, j]; wx = i * step; wy = j * step; }
            if (outer == 0) isoDrop = drop;

            // ---- Joule heat per cell + peak current density ----
            var p = new double[nx, ny];
            peakJ = 0;
            for (int i = 0; i < nx; i++)
                for (int j = 0; j < ny; j++)
                {
                    if (!inside[i, j]) continue;
                    void Branch(int ii, int jj)
                    {
                        if (ii < 0 || ii >= nx || jj < 0 || jj >= ny || !inside[ii, jj]) return;
                        double gg = 0.5 * (GAt(i, j) + GAt(ii, jj));
                        double ib = gg * (v[i, j] - v[ii, jj]);
                        p[i, j] += 0.5 * ib * ib / gg;
                        double jdens = Math.Abs(ib) / (step * cuT);     // A/mm²
                        if (jdens > peakJ) peakJ = jdens;
                    }
                    Branch(i + 1, j); Branch(i - 1, j); Branch(i, j + 1); Branch(i, j - 1);
                }

            // ---- thermal solve (conduction + convection) ----
            for (int iter = 0; iter < 3000; iter++)
            {
                double md = 0;
                for (int i = 0; i < nx; i++)
                    for (int j = 0; j < ny; j++)
                    {
                        if (!inside[i, j]) continue;
                        double gs = conv, ts = conv * ambientC;
                        void Nb(int ii, int jj)
                        {
                            if (ii < 0 || ii >= nx || jj < 0 || jj >= ny || !inside[ii, jj]) return;
                            gs += ktCell; ts += ktCell * T[ii, jj];
                        }
                        Nb(i - 1, j); Nb(i + 1, j); Nb(i, j - 1); Nb(i, j + 1);
                        double nt = (ts + p[i, j]) / gs;
                        md = Math.Max(md, Math.Abs(nt - T[i, j])); T[i, j] = nt;
                    }
                if (md < 1e-4) break;
            }

            if (outer == couplingIters - 1)
            {
                double hot = ambientC, hx = 0, hy = 0;
                for (int i = 0; i < nx; i++)
                    for (int j = 0; j < ny; j++)
                        if (inside[i, j] && T[i, j] > hot) { hot = T[i, j]; hx = i * step; hy = j * step; }
                return new(drop, wx, wy, isoDrop, hot, hx, hy, peakJ, nodeCount, true, null);
            }
        }
        return new(drop, 0, 0, isoDrop, ambientC, 0, 0, peakJ, nodeCount, true, null);
    }

    /// <summary>Electro-thermal DRC: droop over budget or a hot plane region.</summary>
    public static List<DrcResultItem> CheckElectroThermal(BoardDocument doc, FootprintLibrary? library,
        double budgetV = DefaultBudgetV, double maxPlaneC = 105)
    {
        var results = new List<DrcResultItem>();
        var currents = Ampacity.NetCurrents(doc, library);
        foreach (var plane in doc.Planes)
        {
            var r = AnalyzeElectroThermal(doc, plane, currents);
            if (!r.Solved) continue;
            if (r.MaxDropV > budgetV)
                results.Add(new DrcResultItem(24, "IR drop (thermal)",
                    $"{doc.NetName(plane.NetId)}: {r.MaxDropV * 1000:F0} mV droop at ({r.WorstX:F1}, {r.WorstY:F1}) " +
                    $"(isothermal {r.IsothermalDropV * 1000:F0} mV; hot {r.HotC:F0} °C) exceeds {budgetV * 1000:F0} mV — " +
                    "thicken/widen the plane, add power vias, move the regulator closer.", r.WorstX, r.WorstY));
            if (r.HotC > maxPlaneC)
                results.Add(new DrcResultItem(24, "Plane heating",
                    $"{doc.NetName(plane.NetId)}: plane reaches {r.HotC:F0} °C at ({r.HotX:F1}, {r.HotY:F1}) " +
                    $"(peak {r.PeakCurrentDensityAmmm2:F0} A/mm²) — spread the current over more copper/vias.",
                    r.HotX, r.HotY));
        }
        return results;
    }

    private static bool InCopper(PlaneShape plane, double x, double y)
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
        return (crossings & 1) == 1;     // even-odd: holes excluded naturally
    }

    private static (int, int) NearestNode(bool[,] inside, int nx, int ny, int ci, int cj)
    {
        for (int r = 1; r < Math.Max(nx, ny); r++)
            for (int di = -r; di <= r; di++)
                for (int dj = -r; dj <= r; dj++)
                {
                    int i = ci + di, j = cj + dj;
                    if (i >= 0 && i < nx && j >= 0 && j < ny && inside[i, j]) return (i, j);
                }
        return (-1, -1);
    }
}
