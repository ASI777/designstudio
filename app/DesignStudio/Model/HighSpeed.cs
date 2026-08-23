namespace DesignStudio.Model;

// ============================================================================
// High-speed routing subsystems:
//   4. DiffPairRouter   — coupled-pair routing on top of the A* engine
//   5. SerpentineTuner  — accordion meanders to close a delay mismatch
//   6. HighSpeedDrc     — SI rules beyond geometric clearance
//   7. SiEstimator      — first-order transmission-line verification report
// ============================================================================

// ----------------------------------------------------------------------------
// 4. Differential pair router.
//
// Strategy: route the P net with the existing A* (which already avoids
// obstacles), then construct N as a parallel offset path at the class's
// pair gap, with symmetric via placement and bend phase compensation:
// at every corner the inner line is shorter, so the offset path gets a
// 45°-style miter that keeps intra-pair skew below ~0.1 mm per bend.
// ----------------------------------------------------------------------------

public static class DiffPairRouter
{
    public record Result(bool Ok, string Message, double SkewMm);

    /// <summary>
    /// Route P from (pStart→pGoal) and N from (nStart→nGoal) as a coupled pair.
    /// Width/gap come from the net class of <paramref name="pNetId"/> — set
    /// TargetDiffImpedanceOhm on the class to have the solver size them first.
    /// </summary>
    public static Result Route(BoardDocument doc,
                               (double x, double y) pStart, (double x, double y) pGoal, int pNetId,
                               (double x, double y) nStart, (double x, double y) nGoal, int nNetId,
                               int layer)
    {
        var cls = doc.ClassFor(pNetId);
        double gap = cls.DiffPairGapMm > 0 ? cls.DiffPairGapMm : 0.2;
        double width = cls.TraceWidthMm;

        // impedance-driven geometry when a diff target is set
        if (cls.TargetDiffImpedanceOhm > 0)
        {
            var (h, er, ms) = ImpedanceEngine.Reference(doc, layer);
            double cu = doc.Stackup.Count == doc.CopperLayers
                ? doc.Stackup[layer].CopperThicknessMm : doc.CopperThicknessMm;
            var (w, g) = ImpedanceEngine.SolveDiffPair(cls.TargetDiffImpedanceOhm, ms, cu, h, er);
            if (!double.IsNaN(w)) { width = w; gap = g; }
        }
        double pitch = width + gap;   // centre-to-centre

        // 1. route P with the native A*
        int tracesBefore = doc.Traces.Count, viasBefore = doc.Vias.Count;
        if (!doc.AutoRoute(pStart.x, pStart.y, pGoal.x, pGoal.y, pNetId, layer, layer))
            return new(false, $"P side failed: {doc.LastRouteError}", 0);
        var pTraces = doc.Traces.Skip(tracesBefore).Where(t => t.NetId == pNetId).ToList();
        var pVias = doc.Vias.Skip(viasBefore).Where(v => v.NetId == pNetId).ToList();
        if (pTraces.Count == 0) return new(false, "P side produced no copper.", 0);

        // 2. polyline from P segments, offset by pitch to make the N path
        var pts = Polyline(pTraces);
        var nPts = OffsetPolyline(pts, pitch, towards: (nStart.x, nStart.y));

        // splice exact pad entries: pair splits apart only at the pads
        nPts[0] = (nStart.x, nStart.y);
        nPts[^1] = (nGoal.x, nGoal.y);

        // 3. emit N copper (+ symmetric vias, offset like the traces)
        var nTraces = new List<TraceItem>();
        for (int i = 0; i + 1 < nPts.Count; i++)
        {
            if (Dist(nPts[i], nPts[i + 1]) < 1e-6) continue;
            var t = new TraceItem
            {
                Ax = nPts[i].x, Ay = nPts[i].y, Bx = nPts[i + 1].x, By = nPts[i + 1].y,
                Width = width, NetId = nNetId, Layer = pTraces[Math.Min(i, pTraces.Count - 1)].Layer
            };
            doc.InternalAddTrace(t);
            nTraces.Add(t);
        }
        var nVias = new List<ViaItem>();
        foreach (var v in pVias)
        {
            var nv = new ViaItem
            {
                X = v.X, Y = v.Y + pitch,   // symmetric via pair; refined below by nearest N point
                NetId = nNetId, FromLayer = v.FromLayer, ToLayer = v.ToLayer,
                DiameterMm = v.DiameterMm, DrillMm = v.DrillMm
            };
            var near = nPts.MinBy(p => (p.x - v.X) * (p.x - v.X) + (p.y - v.Y) * (p.y - v.Y));
            nv.X = near.x; nv.Y = near.y;
            doc.InternalAddVia(nv);
            nVias.Add(nv);
        }

        // 4. phase compensation: if the offset made N shorter/longer than P
        //    beyond 0.1 mm, serpentine the shorter side near its start
        double pLen = pTraces.Sum(t => t.LengthMm);
        double nLen = nTraces.Sum(t => t.LengthMm);
        double skew = nLen - pLen;
        if (Math.Abs(skew) > 0.1)
        {
            var shorter = skew < 0 ? nNetId : pNetId;
            SerpentineTuner.AddLength(doc, shorter, Math.Abs(skew));
            skew = doc.Traces.Where(t => !t.IsPour && t.NetId == nNetId).Sum(t => t.LengthMm)
                 - doc.Traces.Where(t => !t.IsPour && t.NetId == pNetId).Sum(t => t.LengthMm);
        }

        doc.Undo.Push("Route diff pair",
            undo: () => { foreach (var t in nTraces) doc.InternalRemoveTrace(t); foreach (var v in nVias) doc.InternalRemoveVia(v); },
            redo: () => { foreach (var t in nTraces) doc.InternalAddTrace(t); foreach (var v in nVias) doc.InternalAddVia(v); });
        return new(true, $"Pair routed: w={width:F3} mm, gap={gap:F3} mm, residual skew {Math.Abs(skew):F3} mm", Math.Abs(skew));
    }

    private static List<(double x, double y)> Polyline(List<TraceItem> traces)
    {
        var pts = new List<(double x, double y)> { (traces[0].Ax, traces[0].Ay) };
        foreach (var t in traces) pts.Add((t.Bx, t.By));
        return pts;
    }

    /// <summary>Offset a polyline by d to the side closer to `towards`, with mitered corners.</summary>
    private static List<(double x, double y)> OffsetPolyline(List<(double x, double y)> pts, double d,
                                                             (double x, double y) towards)
    {
        // pick offset sign from the first segment's normal
        var (nx, ny) = Normal(pts[0], pts[1]);
        double side = Math.Sign((towards.x - pts[0].x) * nx + (towards.y - pts[0].y) * ny);
        if (side == 0) side = 1;

        var result = new List<(double x, double y)>();
        for (int i = 0; i < pts.Count; i++)
        {
            (double x, double y) n1 = i > 0 ? Normal(pts[i - 1], pts[i]) : Normal(pts[i], pts[i + 1]);
            (double x, double y) n2 = i + 1 < pts.Count ? Normal(pts[i], pts[i + 1]) : n1;
            double mx = n1.x + n2.x, my = n1.y + n2.y;
            double mlen = Math.Sqrt(mx * mx + my * my);
            if (mlen < 1e-9) { mx = n1.x; my = n1.y; mlen = 1; }
            // miter normal, length-corrected so the gap stays constant through
            // the bend; clamped to avoid spikes at very sharp corners
            double k = d * side / Math.Max(0.5, mlen / 2);
            result.Add((pts[i].x + mx / mlen * Math.Min(Math.Abs(k), d * 1.5) * Math.Sign(k),
                        pts[i].y + my / mlen * Math.Min(Math.Abs(k), d * 1.5) * Math.Sign(k)));
        }
        return result;
    }

    private static (double x, double y) Normal((double x, double y) a, (double x, double y) b)
    {
        double dx = b.x - a.x, dy = b.y - a.y, len = Math.Sqrt(dx * dx + dy * dy);
        return len < 1e-9 ? (0, 0) : (-dy / len, dx / len);
    }

    private static double Dist((double x, double y) a, (double x, double y) b)
        => Math.Sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y));
}

// ----------------------------------------------------------------------------
// 5. Serpentine / tuning generator.
//
// Finds the longest straight segment of the net and replaces its middle with
// an accordion: amplitude and spacing follow the 3×width self-coupling rule.
// Each full meander period adds 2×amplitude of length.
// ----------------------------------------------------------------------------

public static class SerpentineTuner
{
    public record Result(bool Ok, string Message, double AddedMm);

    /// <summary>Insert meanders on netId adding ~addMm of copper. One undo entry.</summary>
    public static Result AddLength(BoardDocument doc, int netId, double addMm)
    {
        var segs = doc.Traces.Where(t => !t.IsPour && t.NetId == netId).ToList();
        if (segs.Count == 0) return new(false, "Net has no routed copper.", 0);

        var host = segs.MaxBy(t => t.LengthMm)!;
        double w = host.Width;
        double spacing = 3 * w;                          // anti-self-coupling rule
        double amp = Math.Max(3 * w, 0.5);               // amplitude (one side)
        int periods = Math.Max(1, (int)Math.Ceiling(addMm / (2 * amp)));
        double runNeeded = periods * 2 * (spacing + w);  // along-track room

        if (host.LengthMm < runNeeded + 2 * w)
        {
            periods = (int)((host.LengthMm - 2 * w) / (2 * (spacing + w)));   // how many actually fit
            if (periods < 1) return new(false,
                $"Longest segment ({host.LengthMm:F2} mm) too short for a meander at 3×width spacing.", 0);
            runNeeded = periods * 2 * (spacing + w);
        }

        // unit vectors along and across the host segment
        double dx = host.Bx - host.Ax, dy = host.By - host.Ay, len = host.LengthMm;
        double ux = dx / len, uy = dy / len, px = -uy, py = ux;

        double start = (len - runNeeded) / 2;            // centre the accordion
        var pts = new List<(double x, double y)> { (host.Ax, host.Ay),
                                                   (host.Ax + ux * start, host.Ay + uy * start) };
        double s = start;
        for (int i = 0; i < periods; i++)
        {
            double half = spacing + w;
            pts.Add((host.Ax + ux * s + px * amp, host.Ay + uy * s + py * amp));
            pts.Add((host.Ax + ux * (s + half) + px * amp, host.Ay + uy * (s + half) + py * amp));
            pts.Add((host.Ax + ux * (s + half), host.Ay + uy * (s + half)));
            s += half;
            pts.Add((host.Ax + ux * s - px * amp, host.Ay + uy * s - py * amp));
            pts.Add((host.Ax + ux * (s + half) - px * amp, host.Ay + uy * (s + half) - py * amp));
            pts.Add((host.Ax + ux * (s + half), host.Ay + uy * (s + half)));
            s += half;
        }
        pts.Add((host.Bx, host.By));

        var added = new List<TraceItem>();
        for (int i = 0; i + 1 < pts.Count; i++)
        {
            if (Math.Abs(pts[i].x - pts[i + 1].x) < 1e-9 && Math.Abs(pts[i].y - pts[i + 1].y) < 1e-9) continue;
            var t = new TraceItem
            {
                Ax = pts[i].x, Ay = pts[i].y, Bx = pts[i + 1].x, By = pts[i + 1].y,
                Width = w, NetId = netId, Layer = host.Layer
            };
            doc.InternalAddTrace(t);
            added.Add(t);
        }
        doc.InternalRemoveTrace(host);

        double addedLen = added.Sum(t => t.LengthMm) - len;
        doc.Undo.Push($"Serpentine {doc.NetName(netId)} (+{addedLen:F2} mm)",
            undo: () => { foreach (var t in added) doc.InternalRemoveTrace(t); doc.InternalAddTrace(host); },
            redo: () => { doc.InternalRemoveTrace(host); foreach (var t in added) doc.InternalAddTrace(t); });
        return new(true, $"Added {addedLen:F2} mm with {periods} meander period(s) " +
                         $"(amplitude {amp:F2} mm, spacing {spacing:F2} mm).", addedLen);
    }
}

// ----------------------------------------------------------------------------
// 6. High-speed DRC extensions (managed checks appended to the native run).
// ----------------------------------------------------------------------------

public static class HighSpeedDrc
{
    public static List<DrcResultItem> Run(BoardDocument doc)
    {
        var r = new List<DrcResultItem>();
        bool stackupLive = doc.Stackup.Count == doc.CopperLayers;
        var gndNets = doc.Nets.Where(n => n.Name.Contains("GND", StringComparison.OrdinalIgnoreCase))
                              .Select(n => n.Id).ToHashSet();

        // controlled nets = nets in a class with an impedance target or skew limit
        var controlled = doc.Nets.Where(n =>
        {
            var c = doc.ClassFor(n.Id);
            return c.TargetImpedanceOhm > 0 || c.TargetDiffImpedanceOhm > 0 || c.MaxSkewMm > 0;
        }).Select(n => n.Id).ToHashSet();

        // 14: reference-plane continuity — controlled trace on a layer with no
        // adjacent plane in the stackup (the field has no return path)
        if (stackupLive)
            foreach (var t in doc.Traces.Where(t => !t.IsPour && controlled.Contains(t.NetId)))
            {
                var (h, _, _) = ImpedanceEngine.Reference(doc, t.Layer);
                bool anyPlane = doc.Stackup.Any(l => l.Role == LayerRole.Plane);
                if (!anyPlane)
                {
                    r.Add(new DrcResultItem(14, "Reference plane",
                        $"{doc.NetName(t.NetId)}: controlled-impedance trace on {doc.LayerName(t.Layer)} has no reference plane in the stackup.",
                        (t.Ax + t.Bx) / 2, (t.Ay + t.By) / 2));
                    break;   // one report is enough when there are no planes at all
                }
            }

        // 15: via stubs — through-via on a controlled net whose copper only
        // uses the upper layers leaves a resonant stub; flag for backdrill
        foreach (var v in doc.Vias.Where(v => controlled.Contains(v.NetId)))
        {
            if (v.BackdrillToLayer >= 0) continue;   // stub already scheduled for removal
            var layersUsed = doc.Traces.Where(t => !t.IsPour && t.NetId == v.NetId)
                                       .Select(t => t.Layer).Distinct().ToList();
            if (layersUsed.Count == 0) continue;
            int deepest = layersUsed.Max();
            if (v.ToLayer > deepest && stackupLive)
            {
                double stub = 0;
                for (int i = deepest; i < v.ToLayer && i < doc.Stackup.Count; i++)
                    stub += doc.Stackup[i].DielectricHeightMm + doc.Stackup[i].CopperThicknessMm;
                if (stub > 0.3)
                    r.Add(new DrcResultItem(15, "Via stub",
                        $"{doc.NetName(v.NetId)}: via at ({v.X:F2}, {v.Y:F2}) has a {stub:F2} mm unused barrel below {doc.LayerName(deepest)} — consider backdrilling.",
                        v.X, v.Y));
            }
        }

        // 16: pair-gap deviation — diff-class nets whose parallel segments
        // wander off the class gap by >20 %
        foreach (var cls in doc.NetClasses.Where(c => c.DiffPairGapMm > 0))
        {
            var members = doc.Nets.Where(n => n.ClassId == cls.Id).Select(n => n.Id).ToList();
            if (members.Count != 2) continue;
            var a = doc.Traces.Where(t => !t.IsPour && t.NetId == members[0]).ToList();
            var b = doc.Traces.Where(t => !t.IsPour && t.NetId == members[1]).ToList();
            var bSet = b.ToHashSet();
            foreach (var ta in a)
            {
                double cx = (ta.Ax + ta.Bx) / 2, cy = (ta.Ay + ta.By) / 2;
                // spatial index narrows candidates to the local neighbourhood
                var nearest = BoardIndex.Traces(doc).Query(cx, cy, 5)
                               .Where(tb => bSet.Contains(tb) && tb.Layer == ta.Layer)
                               .Select(tb => DistPointSeg(cx, cy, tb))
                               .DefaultIfEmpty(double.MaxValue).Min();
                if (nearest == double.MaxValue) continue;
                double target = cls.DiffPairGapMm + (cls.TraceWidthMm);   // centre-to-centre
                if (Math.Abs(nearest - target) > 0.2 * target)
                    r.Add(new DrcResultItem(16, "Pair gap",
                        $"class '{cls.Name}': gap {nearest:F2} mm at ({cx:F2}, {cy:F2}) deviates >20 % from target {target:F2} mm — coupling/impedance shifts.",
                        cx, cy));
            }
        }

        // 17: return-path vias — every controlled-net layer change needs a
        // ground via within 1 mm so the return current can follow
        foreach (var v in doc.Vias.Where(v => controlled.Contains(v.NetId)))
        {
            bool hasReturn = doc.Vias.Any(g => gndNets.Contains(g.NetId) &&
                (g.X - v.X) * (g.X - v.X) + (g.Y - v.Y) * (g.Y - v.Y) <= 1.0);
            if (!hasReturn)
                r.Add(new DrcResultItem(17, "Return path",
                    $"{doc.NetName(v.NetId)}: layer change at ({v.X:F2}, {v.Y:F2}) has no GND via within 1 mm — return current must detour.",
                    v.X, v.Y));
        }
        return r;
    }

    private static double DistPointSeg(double px, double py, TraceItem t)
    {
        double dx = t.Bx - t.Ax, dy = t.By - t.Ay;
        double l2 = dx * dx + dy * dy;
        double u = l2 < 1e-12 ? 0 : Math.Clamp(((px - t.Ax) * dx + (py - t.Ay) * dy) / l2, 0, 1);
        double qx = t.Ax + u * dx - px, qy = t.Ay + u * dy - py;
        return Math.Sqrt(qx * qx + qy * qy);
    }
}

// ----------------------------------------------------------------------------
// 7. First-order SI verification.
//
// Extracts a cascaded transmission-line model of a routed net (per-segment Z0
// and delay from the stackup) and produces analytic estimates: impedance
// profile, reflection magnitude at each discontinuity, dielectric+copper loss,
// and a resulting eye-opening figure. This is an estimator for catching
// gross problems early — not a substitute for IBIS/field-solver sign-off.
// ----------------------------------------------------------------------------

public static class SiEstimator
{
    public record SegmentModel(double LengthMm, double Z0, double DelayPs, int Layer);
    public record Report(int NetId, string NetName, List<SegmentModel> Segments,
                         double TotalDelayPs, double WorstReflection, double LossDbAt1GHz,
                         double EyeOpeningPct, List<string> Notes);

    public static Report Analyze(BoardDocument doc, int netId, double bitRateGbps = 1.0)
    {
        var notes = new List<string>();
        var segs = doc.Traces.Where(t => !t.IsPour && t.NetId == netId)
            .Select(t => new SegmentModel(t.LengthMm, ImpedanceEngine.TraceZ0(doc, t),
                                          DelayEngine.SegmentDelayPs(doc, t), t.Layer))
            .ToList();
        double totalPs = DelayEngine.NetDelayPs(doc, netId);

        // reflections at each Z0 discontinuity: Γ = (Z2−Z1)/(Z2+Z1)
        double worstGamma = 0;
        for (int i = 0; i + 1 < segs.Count; i++)
        {
            double g = Math.Abs((segs[i + 1].Z0 - segs[i].Z0) / (segs[i + 1].Z0 + segs[i].Z0));
            worstGamma = Math.Max(worstGamma, g);
        }
        int viaCount = doc.Vias.Count(v => v.NetId == netId);
        worstGamma += viaCount * 0.03;   // each via ≈ small capacitive discontinuity
        if (viaCount > 2) notes.Add($"{viaCount} vias on the net — each adds a discontinuity; minimize for >2.5 Gbps.");

        // loss at 1 GHz: dielectric ≈ 2.3·f·tanδ·√Er dB/m + copper (rough)
        double lenM = segs.Sum(s => s.LengthMm) / 1000.0;
        double er = doc.DielectricEr, tan = doc.DielectricLossTangent;
        double dielDbPerM = 2.3 * 1.0 * tan * Math.Sqrt(er) * 10;   // ~0.9 dB/m for FR-4
        double copperDbPerM = 3.0;                                  // ~3 dB/m at 1 GHz, 0.25 mm trace
        double lossDb = lenM * (dielDbPerM + copperDbPerM) * bitRateGbps / 2;

        // eye estimate: start at 100 %, subtract reflection and loss penalties
        double eye = 100.0;
        eye -= worstGamma * 2 * 100 * 0.5;            // double transit reflection penalty
        eye -= Math.Min(60, lossDb * 8);              // amplitude loss penalty
        var spread = doc.MatchGroups.FirstOrDefault(g => g.NetIds.Contains(netId));
        if (spread != null)
        {
            var delays = spread.NetIds.Select(id => DelayEngine.NetDelayPs(doc, id)).ToList();
            double skewPs = delays.Max() - delays.Min();
            double ui = 1000.0 / bitRateGbps;         // unit interval ps
            eye -= Math.Min(40, skewPs / ui * 100);
            if (skewPs > ui * 0.1) notes.Add($"Group skew {skewPs:F1} ps is >10 % of the {ui:F0} ps unit interval.");
        }
        eye = Math.Max(0, eye);

        if (worstGamma > 0.1) notes.Add($"Worst reflection Γ={worstGamma:F2} (>0.1): align trace widths/layers to one impedance.");
        if (segs.Count == 0) notes.Add("Net has no routed copper yet.");

        return new Report(netId, doc.NetName(netId), segs, totalPs, worstGamma, lossDb, eye, notes);
    }

    public static string Format(Report r)
    {
        var sb = new System.Text.StringBuilder();
        sb.AppendLine($"SI report — net {r.NetName}");
        sb.AppendLine($"  segments: {r.Segments.Count}, total delay {r.TotalDelayPs:F1} ps");
        if (r.Segments.Count > 0)
            sb.AppendLine($"  Z0 profile: {r.Segments.Min(s => s.Z0):F1}–{r.Segments.Max(s => s.Z0):F1} Ω");
        sb.AppendLine($"  worst reflection Γ: {r.WorstReflection:F3}");
        sb.AppendLine($"  est. loss: {r.LossDbAt1GHz:F2} dB");
        sb.AppendLine($"  eye opening estimate: {r.EyeOpeningPct:F0} %");
        foreach (var n in r.Notes) sb.AppendLine($"  ⚠ {n}");
        return sb.ToString();
    }
}
