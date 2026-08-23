namespace DesignStudio.Model;

// ============================================================================
// 1.4 BGA escape planner.
//
// Generates the fanout that makes a dense BGA routable, as ordinary copper:
//   * ring 0–1 (outer two ball rings): straight escape stubs outward on the
//     component's own layer — these always fit at sane pitch/width.
//   * deeper rings: dog-bone vias placed diagonally in the ball-grid cell
//     (the only spot that clears all four neighbouring lands), then a stub
//     escape outward on an inner SIGNAL layer assigned by ring depth — two
//     rings per layer, skipping plane layers from the stackup.
//
// Everything emitted is plain traces + vias on the pad's net, so routing,
// DRC, delay and S-parameter extraction all see it with zero special cases.
// ============================================================================

public static class EscapePlanner
{
    public record Result(bool Ok, string Message, int Stubs, int DogBones);

    public static Result Fanout(BoardDocument doc, FootprintItem fp)
    {
        // ---- recognise the grid ----
        if (fp.Pads.Count < 16) return new(false, "not an area-array part (fewer than 16 pads).", 0, 0);
        var xs = fp.Pads.Select(p => Math.Round(p.X, 2)).Distinct().OrderBy(v => v).ToList();
        var ys = fp.Pads.Select(p => Math.Round(p.Y, 2)).Distinct().OrderBy(v => v).ToList();
        if (xs.Count < 3 || ys.Count < 3 || fp.Pads.Count < 0.6 * xs.Count * ys.Count)
            return new(false, "pad field is not a grid (perimeter package?).", 0, 0);
        double pitchX = (xs[^1] - xs[0]) / Math.Max(xs.Count - 1, 1);
        double pitchY = (ys[^1] - ys[0]) / Math.Max(ys.Count - 1, 1);
        double pitch = Math.Min(pitchX, pitchY);
        if (pitch < 0.3) return new(false, $"pitch {pitch:F2} mm needs HDI microvias — not supported yet.", 0, 0);

        // inner signal layers available for escapes (skip planes)
        doc.EnsureStackup();
        var signalLayers = Enumerable.Range(1, doc.CopperLayers - 1)
            .Where(l => doc.Stackup[l].Role == LayerRole.Signal)
            .ToList();

        var addedTraces = new List<TraceItem>();
        var addedVias = new List<ViaItem>();
        int stubs = 0, dogBones = 0, skipped = 0;

        double cosR = Math.Cos(fp.RotationDeg * Math.PI / 180), sinR = Math.Sin(fp.RotationDeg * Math.PI / 180);

        foreach (var pad in fp.Pads)
        {
            if (pad.NetId < 0) { skipped++; continue; }     // unassigned balls have nowhere to go
            int ix = xs.IndexOf(Math.Round(pad.X, 2));
            int iy = ys.IndexOf(Math.Round(pad.Y, 2));
            int ring = Math.Min(Math.Min(ix, xs.Count - 1 - ix), Math.Min(iy, ys.Count - 1 - iy));

            var cls = doc.ClassFor(pad.NetId);
            double w = Math.Min(cls.TraceWidthMm, (pitch - cls.ClearanceMm * 2) * 0.5);
            var (px, py) = fp.PadWorld(pad);

            // outward direction: toward the nearest edge of the part, in world space
            double dirLx = ix <= xs.Count - 1 - ix ? -1 : 1;
            double dirLy = iy <= ys.Count - 1 - iy ? -1 : 1;
            bool horizontal = Math.Min(ix, xs.Count - 1 - ix) <= Math.Min(iy, ys.Count - 1 - iy);
            (double ex, double ey) dirLocal = horizontal ? (dirLx, 0) : (0, dirLy);
            double dwx = dirLocal.ex * cosR - dirLocal.ey * sinR;
            double dwy = dirLocal.ex * sinR + dirLocal.ey * cosR;

            if (ring <= 1)
            {
                // straight stub to just outside the courtyard, top layer
                double run = ring * pitch + pitch + 0.5;
                addedTraces.Add(new TraceItem
                {
                    Ax = px, Ay = py, Bx = px + dwx * run, By = py + dwy * run,
                    Width = w, NetId = pad.NetId, Layer = fp.Side <= 0 ? 0 : doc.CopperLayers - 1
                });
                stubs++;
            }
            else
            {
                if (signalLayers.Count == 0) { skipped++; continue; }
                // dog-bone via at the diagonal cell centre (the only spot that
                // clears all four neighbouring lands), pointing away from the
                // package centre on both axes, rotated into world space
                double dvx = (dirLx * cosR - dirLy * sinR) * pitch / 2;
                double dvy = (dirLx * sinR + dirLy * cosR) * pitch / 2;
                double vx = px + dvx, vy = py + dvy;

                int escapeLayer = signalLayers[Math.Min((ring - 2) / 2, signalLayers.Count - 1)];
                addedTraces.Add(new TraceItem
                {
                    Ax = px, Ay = py, Bx = vx, By = vy, Width = w, NetId = pad.NetId,
                    Layer = fp.Side <= 0 ? 0 : doc.CopperLayers - 1
                });
                addedVias.Add(new ViaItem
                {
                    X = vx, Y = vy, NetId = pad.NetId,
                    FromLayer = 0, ToLayer = doc.CopperLayers - 1,
                    DiameterMm = Math.Min(cls.ViaDiameterMm, pitch * 0.55),
                    DrillMm = Math.Min(cls.ViaDrillMm, pitch * 0.25)
                });
                // escape stub outward on the assigned inner layer, clear of the ball field
                double run = (ring + 1.5) * pitch;
                addedTraces.Add(new TraceItem
                {
                    Ax = vx, Ay = vy, Bx = vx + dwx * run, By = vy + dwy * run,
                    Width = w, NetId = pad.NetId, Layer = escapeLayer
                });
                dogBones++;
            }
        }

        if (addedTraces.Count == 0)
            return new(false, $"nothing to fan out ({skipped} pads without nets).", 0, 0);

        foreach (var t in addedTraces) doc.InternalAddTrace(t);
        foreach (var v in addedVias) doc.InternalAddVia(v);
        doc.Undo.Push($"BGA fanout {fp.RefDes}",
            undo: () => { foreach (var t in addedTraces) doc.InternalRemoveTrace(t); foreach (var v in addedVias) doc.InternalRemoveVia(v); },
            redo: () => { foreach (var t in addedTraces) doc.InternalAddTrace(t); foreach (var v in addedVias) doc.InternalAddVia(v); });

        return new(true,
            $"{fp.RefDes}: {stubs} edge escapes, {dogBones} dog-bones over {signalLayers.Count} signal layer(s)" +
            (skipped > 0 ? $", {skipped} pad(s) skipped (no net)" : "") + ".",
            stubs, dogBones);
    }
}
