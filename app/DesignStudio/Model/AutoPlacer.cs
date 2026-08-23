namespace DesignStudio.Model;

/// <summary>
/// Force-directed auto-placement (Phase 5-lite of the autonomous engine):
/// attraction along shared nets, repulsion between overlapping courtyards,
/// hard wall at the board outline. One undoable group move.
/// </summary>
public static class AutoPlacer
{
    private const double CourtyardMm = 0.5;     // clearance ring around each part
    private const int Iterations = 400;
    private const double AttractK = 0.02;       // spring constant per shared net
    private const double RepelStep = 0.6;       // push distance per overlap resolution

    public static int Place(BoardDocument doc)
    {
        var parts = doc.Footprints;
        if (parts.Count < 2) return 0;

        var before = parts.Select(fp => (fp, fp.X, fp.Y, fp.RotationDeg)).ToList();

        // Net → list of (part index, pad offset) for attraction forces
        var netPins = new Dictionary<int, List<(int part, double dx, double dy)>>();
        for (int i = 0; i < parts.Count; i++)
            foreach (var pad in parts[i].Pads)
            {
                if (pad.NetId < 0) continue;
                if (!netPins.TryGetValue(pad.NetId, out var list))
                    netPins[pad.NetId] = list = new List<(int, double, double)>();
                list.Add((i, pad.X, pad.Y));
            }

        var x = parts.Select(p => p.X).ToArray();
        var y = parts.Select(p => p.Y).ToArray();
        var half = parts.Select(p =>
        {
            var (minX, minY, maxX, maxY) = p.Bounds();
            return ((maxX - minX) / 2 + CourtyardMm, (maxY - minY) / 2 + CourtyardMm);
        }).ToArray();

        var rng = new Random(42);

        for (int iter = 0; iter < Iterations; iter++)
        {
            var fx = new double[parts.Count];
            var fy = new double[parts.Count];

            // Attraction: every pair of parts sharing a net pulls together.
            foreach (var pins in netPins.Values)
            {
                for (int a = 0; a < pins.Count; a++)
                    for (int b = a + 1; b < pins.Count; b++)
                    {
                        int i = pins[a].part, j = pins[b].part;
                        if (i == j) continue;
                        double dx = x[j] - x[i], dy = y[j] - y[i];
                        fx[i] += AttractK * dx; fy[i] += AttractK * dy;
                        fx[j] -= AttractK * dx; fy[j] -= AttractK * dy;
                    }
            }

            // Apply forces (damped)
            double damp = 1.0 - (double)iter / Iterations;
            for (int i = 0; i < parts.Count; i++)
            {
                x[i] += Math.Clamp(fx[i], -2, 2) * damp;
                y[i] += Math.Clamp(fy[i], -2, 2) * damp;
            }

            // Repulsion: separate overlapping courtyards directly.
            for (int i = 0; i < parts.Count; i++)
                for (int j = i + 1; j < parts.Count; j++)
                {
                    double ox = half[i].Item1 + half[j].Item1 - Math.Abs(x[i] - x[j]);
                    double oy = half[i].Item2 + half[j].Item2 - Math.Abs(y[i] - y[j]);
                    if (ox <= 0 || oy <= 0) continue;   // no overlap
                    // push apart along the axis of least penetration
                    double push = RepelStep + rng.NextDouble() * 0.1;
                    if (ox < oy)
                    {
                        double dir = x[i] < x[j] ? -1 : 1;
                        x[i] += dir * push; x[j] -= dir * push;
                    }
                    else
                    {
                        double dir = y[i] < y[j] ? -1 : 1;
                        y[i] += dir * push; y[j] -= dir * push;
                    }
                }

            // Keep parts inside the board (courtyard fully inside).
            for (int i = 0; i < parts.Count; i++)
            {
                x[i] = Math.Clamp(x[i], half[i].Item1, doc.BoardWidthMm - half[i].Item1);
                y[i] = Math.Clamp(y[i], half[i].Item2, doc.BoardHeightMm - half[i].Item2);
            }
        }

        // Snap to grid and commit.
        int moved = 0;
        for (int i = 0; i < parts.Count; i++)
        {
            double sx = Math.Round(x[i] / doc.GridMm) * doc.GridMm;
            double sy = Math.Round(y[i] / doc.GridMm) * doc.GridMm;
            if (Math.Abs(sx - parts[i].X) > 1e-9 || Math.Abs(sy - parts[i].Y) > 1e-9)
            {
                // Use the real outline legalizer for the final commit. The
                // force loop uses a fast rectangular clamp, but that is not
                // sufficient for circles, rounded corners, or imported polygons.
                doc.MoveFootprint(parts[i], sx, sy, parts[i].RotationDeg);
                moved++;
            }
        }
        doc.PushGroupMoveUndo("Auto-place", before);
        return moved;
    }
}
