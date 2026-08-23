using System.Globalization;
using System.IO;
using System.Text;
using DesignStudio.Model;

namespace DesignStudio.Export;

/// <summary>
/// Fabrication handoff generator for N-layer boards:
///   - Gerber RS-274X copper for every layer (traces, pours, pads, via lands)
///   - solder mask + paste for both outer sides
///   - board profile (edge)
///   - Excellon drill files: plated through, one file per blind/buried via span
///   - BOM CSV and pick-and-place centroid CSV (side-aware)
/// Pads are emitted as G36/G37 polygon regions so rotation is exact; traces
/// and pours use circular apertures; via lands flash circle apertures.
/// Format: 4.6 metric (nm resolution).
/// </summary>
public static class FabExporter
{
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    private const double MaskExpansionMm = 0.05;

    public record Result(List<string> FilesWritten);

    public static Result ExportAll(BoardDocument doc, string folder, string baseName)
    {
        Directory.CreateDirectory(folder);
        var written = new List<string>();

        for (int layer = 0; layer < doc.CopperLayers; layer++)
        {
            string file = CopperFileName(doc, baseName, layer);
            written.Add(WriteFile(Path.Combine(folder, file), GerberCopper(doc, layer)));
        }
        written.Add(WriteFile(Path.Combine(folder, baseName + "-F_Mask.gts"), GerberMask(doc, top: true)));
        written.Add(WriteFile(Path.Combine(folder, baseName + "-B_Mask.gbs"), GerberMask(doc, top: false)));
        written.Add(WriteFile(Path.Combine(folder, baseName + "-F_Paste.gtp"), GerberPaste(doc, top: true)));
        written.Add(WriteFile(Path.Combine(folder, baseName + "-B_Paste.gbp"), GerberPaste(doc, top: false)));
        written.Add(WriteFile(Path.Combine(folder, baseName + "-Edge_Cuts.gm1"), GerberEdge(doc)));

        foreach (var (span, content) in ExcellonFiles(doc))
        {
            string suffix = span == (0, doc.CopperLayers - 1)
                ? ".drl"
                : $"-L{span.from + 1}-L{span.to + 1}.drl";
            written.Add(WriteFile(Path.Combine(folder, baseName + suffix), content));
        }

        // backdrill files: one NPTH file per (side, depth) span, oversized bit
        foreach (var (span, content) in BackdrillFiles(doc))
            written.Add(WriteFile(Path.Combine(folder, baseName + $"-backdrill-L{span.from + 1}-L{span.to + 1}.drl"), content));

        written.Add(WriteFile(Path.Combine(folder, baseName + "-BOM.csv"), Bom(doc)));
        written.Add(WriteFile(Path.Combine(folder, baseName + "-PnP.csv"), Centroid(doc)));
        written.Add(WriteFile(Path.Combine(folder, baseName + "-Stackup.txt"), StackupDrawing(doc)));
        string coupons = CouponReadme(doc);
        if (coupons.Length > 0)
            written.Add(WriteFile(Path.Combine(folder, baseName + "-Coupons.txt"), coupons));

        return new Result(written);
    }

    /// <summary>
    /// Backdrill drill files: vias with BackdrillToLayer set get an unplated
    /// pass from the far side down to (but not through) the last used layer,
    /// with the standard +0.2 mm bit oversize. Grouped by removal span.
    /// </summary>
    public static List<((int from, int to) span, string content)> BackdrillFiles(BoardDocument doc)
    {
        var bySpan = doc.Vias
            .Where(v => v.BackdrillToLayer >= 0 && v.BackdrillToLayer < Math.Max(v.FromLayer, v.ToLayer))
            .GroupBy(v => (from: v.BackdrillToLayer + 1, to: Math.Max(v.FromLayer, v.ToLayer)));
        var result = new List<((int, int), string)>();
        foreach (var grp in bySpan)
        {
            var sb = new StringBuilder();
            sb.AppendLine("M48");
            sb.AppendLine("METRIC,TZ");
            sb.AppendLine("; NPTH backdrill pass — remove plated barrel between the listed layers");
            var tools = grp.Select(v => v.DrillMm + Backdrill.OversizeMm).Distinct().OrderBy(d => d).ToList();
            for (int i = 0; i < tools.Count; i++)
                sb.AppendLine($"T{i + 1:D2}C{tools[i].ToString("F3", Inv)}");
            sb.AppendLine("%");
            sb.AppendLine("G90");
            sb.AppendLine("G05");
            for (int i = 0; i < tools.Count; i++)
            {
                sb.AppendLine($"T{i + 1:D2}");
                foreach (var v in grp.Where(v => Math.Abs(v.DrillMm + Backdrill.OversizeMm - tools[i]) < 1e-9))
                    sb.AppendLine($"X{v.X.ToString("F3", Inv)}Y{v.Y.ToString("F3", Inv)}");
            }
            sb.AppendLine("T0");
            sb.AppendLine("M30");
            result.Add((grp.Key, sb.ToString()));
        }
        return result;
    }

    /// <summary>Human-readable stackup table for the fab drawing.</summary>
    public static string StackupDrawing(BoardDocument doc)
    {
        var sb = new StringBuilder();
        sb.AppendLine($"STACKUP — {doc.CopperLayers} copper layers, board {doc.BoardWidthMm:F1} × {doc.BoardHeightMm:F1} mm");
        sb.AppendLine(new string('-', 78));
        doc.EnsureStackup();
        double total = 0;
        for (int i = 0; i < doc.Stackup.Count; i++)
        {
            var l = doc.Stackup[i];
            var mat = MaterialsLibrary.Find(l.MaterialId);
            sb.AppendLine($"L{i + 1,-3} {l.Role,-7} copper {l.CopperThicknessMm * 1000:F0} µm" +
                          (l.Role == LayerRole.Plane && l.PlaneNetId >= 0 ? $"  (net {doc.NetName(l.PlaneNetId)})" : ""));
            total += l.CopperThicknessMm;
            if (i + 1 < doc.Stackup.Count)
            {
                sb.AppendLine($"     dielectric {l.DielectricHeightMm:F3} mm — " +
                              (mat != null
                                  ? $"{mat.Name}: Dk {mat.DkAt(1):F2}@1GHz / {mat.DkAt(10):F2}@10GHz, Df {mat.DfAt(1):F4}"
                                  : $"Er {l.DielectricEr:F2}, tanδ {l.LossTangent:F4}"));
                total += l.DielectricHeightMm;
            }
        }
        sb.AppendLine(new string('-', 78));
        sb.AppendLine($"Nominal finished thickness ≈ {total:F2} mm (excl. mask/finish)");
        var controlled = doc.NetClasses.Where(c => c.TargetImpedanceOhm > 0 || c.TargetDiffImpedanceOhm > 0).ToList();
        if (controlled.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine("CONTROLLED IMPEDANCE:");
            foreach (var c in controlled)
                sb.AppendLine($"  class '{c.Name}': " +
                    (c.TargetImpedanceOhm > 0 ? $"{c.TargetImpedanceOhm:F0} Ω single-ended " : "") +
                    (c.TargetDiffImpedanceOhm > 0 ? $"{c.TargetDiffImpedanceOhm:F0} Ω differential " : "") +
                    $"@ width {c.TraceWidthMm:F3} mm" +
                    (c.DiffPairGapMm > 0 ? $", gap {c.DiffPairGapMm:F3} mm" : "") +
                    " — fab to verify with coupons; ±10 % unless quoted otherwise.");
        }
        return sb.ToString();
    }

    /// <summary>
    /// Impedance coupon specification: one 25 mm test strip per controlled
    /// class, placed in the panel rail (coordinates below the board origin).
    /// Documented as a build note so the fab adds them to the panel.
    /// </summary>
    public static string CouponReadme(BoardDocument doc)
    {
        var controlled = doc.NetClasses.Where(c => c.TargetImpedanceOhm > 0 || c.TargetDiffImpedanceOhm > 0).ToList();
        if (controlled.Count == 0) return "";
        var sb = new StringBuilder();
        sb.AppendLine("IMPEDANCE TEST COUPONS — add to panel rail, same stackup as the board");
        sb.AppendLine(new string('-', 70));
        double y = doc.BoardHeightMm + 5;
        foreach (var c in controlled)
        {
            if (c.TargetImpedanceOhm > 0)
                sb.AppendLine($"Coupon '{c.Name}-SE': 25 mm straight trace, width {c.TraceWidthMm:F3} mm, " +
                              $"L1 over first plane — target {c.TargetImpedanceOhm:F0} Ω ±10 %. Suggested origin (0, {y:F0}).");
            if (c.TargetDiffImpedanceOhm > 0)
                sb.AppendLine($"Coupon '{c.Name}-DIFF': 25 mm coupled pair, width {c.TraceWidthMm:F3} mm, " +
                              $"gap {c.DiffPairGapMm:F3} mm — target {c.TargetDiffImpedanceOhm:F0} Ω ±10 %. Suggested origin (0, {y + 3:F0}).");
            y += 6;
        }
        sb.AppendLine();
        sb.AppendLine("TDR-measure each coupon; reject panel if outside tolerance.");
        return sb.ToString();
    }

    public static string CopperFileName(BoardDocument doc, string baseName, int layer)
    {
        if (layer == 0) return baseName + "-F_Cu.gtl";
        if (layer == doc.CopperLayers - 1) return baseName + "-B_Cu.gbl";
        return baseName + $"-In{layer}_Cu.g{layer + 1}";
    }

    private static string WriteFile(string path, string content)
    {
        File.WriteAllText(path, content);
        return path;
    }

    // ---- coordinate formatting: X4.6 metric ----
    private static string Coord(double mm) => ((long)Math.Round(mm * 1_000_000)).ToString(Inv);

    private static void Header(StringBuilder sb, string fileFunction)
    {
        sb.AppendLine("%TF.GenerationSoftware,DesignStudio,DesignStudio,1.0*%");
        sb.AppendLine($"%TF.FileFunction,{fileFunction}*%");
        sb.AppendLine("%FSLAX46Y46*%");
        sb.AppendLine("%MOMM*%");
        sb.AppendLine("%LPD*%");
        sb.AppendLine("G01*");
    }

    private static string LayerFunction(BoardDocument doc, int layer) =>
        layer == 0 ? "Copper,L1,Top"
        : layer == doc.CopperLayers - 1 ? $"Copper,L{doc.CopperLayers},Bot"
        : $"Copper,L{layer + 1},Inr";

    // Pads present on a copper layer: SMD pads on the footprint's side,
    // through-hole pads everywhere.
    private static bool PadOnLayer(BoardDocument doc, FootprintItem fp, PadItem pad, int layer)
        => pad.ThroughHole || NormalizedSide(doc, fp) == layer;

    private static int NormalizedSide(BoardDocument doc, FootprintItem fp)
        => fp.Side <= 0 ? 0 : doc.CopperLayers - 1;

    public static string GerberCopper(BoardDocument doc, int layer)
    {
        var sb = new StringBuilder();
        Header(sb, LayerFunction(doc, layer));

        // Apertures: one circle per distinct trace width on this layer + via lands.
        var widths = doc.Traces.Where(t => t.Layer == layer)
                               .Select(t => t.Width).Distinct().OrderBy(w => w).ToList();
        var viaDias = doc.Vias.Where(v => ViaOnLayer(v, layer))
                              .Select(v => v.DiameterMm).Distinct().OrderBy(d => d).ToList();
        var apOf = new Dictionary<double, int>();
        var viaApOf = new Dictionary<double, int>();
        int code = 10;
        foreach (var w in widths)
        {
            apOf[w] = code;
            sb.AppendLine($"%ADD{code}C,{w.ToString("F4", Inv)}*%");
            code++;
        }
        foreach (var d in viaDias)
        {
            viaApOf[d] = code;
            sb.AppendLine($"%ADD{code}C,{d.ToString("F4", Inv)}*%");
            code++;
        }

        EmitPlanes(sb, doc, layer);   // planes first: LPC voids must not erase later copper

        // Traces and pour strokes
        foreach (var grp in doc.Traces.Where(t => t.Layer == layer).GroupBy(t => t.Width))
        {
            sb.AppendLine($"D{apOf[grp.Key]}*");
            foreach (var t in grp)
            {
                sb.AppendLine($"X{Coord(t.Ax)}Y{Coord(t.Ay)}D02*");
                sb.AppendLine($"X{Coord(t.Bx)}Y{Coord(t.By)}D01*");
            }
        }

        // Via lands (flash)
        foreach (var grp in doc.Vias.Where(v => ViaOnLayer(v, layer)).GroupBy(v => v.DiameterMm))
        {
            sb.AppendLine($"D{viaApOf[grp.Key]}*");
            foreach (var v in grp)
                sb.AppendLine($"X{Coord(v.X)}Y{Coord(v.Y)}D03*");
        }

        // Pads as filled polygon regions (rotation-exact)
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                if (!PadOnLayer(doc, fp, pad, layer)) continue;
                EmitPadRegion(sb, fp, pad, 0);
            }

        sb.AppendLine("M02*");
        return sb.ToString();
    }

    /// <summary>
    /// Polygon plane copper: outers as dark (LPD) regions, Clipper holes as
    /// clear (LPC). LPC erases everything drawn before it, so planes are
    /// emitted FIRST in the file; traces/pads then draw dark on top and are
    /// never punched out by plane voids.
    /// </summary>
    private static void EmitPlanes(StringBuilder sb, BoardDocument doc, int layer)
    {
        doc.PlanesUpToDate();
        var planesHere = doc.Planes.Where(p => p.Layer == layer).ToList();
        if (planesHere.Count == 0) return;
        foreach (var plane in planesHere)
            foreach (var poly in plane.Fill.OrderByDescending(PlaneGenerator.SignedArea))
            {
                if (poly.Count < 3) continue;
                bool hole = PlaneGenerator.SignedArea(poly) < 0;
                sb.AppendLine(hole ? "%LPC*%" : "%LPD*%");
                sb.AppendLine("G36*");
                sb.AppendLine($"X{Coord(poly[0].x)}Y{Coord(poly[0].y)}D02*");
                for (int i = 1; i < poly.Count; i++)
                    sb.AppendLine($"X{Coord(poly[i].x)}Y{Coord(poly[i].y)}D01*");
                sb.AppendLine($"X{Coord(poly[0].x)}Y{Coord(poly[0].y)}D01*");
                sb.AppendLine("G37*");
            }
        sb.AppendLine("%LPD*%");   // restore dark polarity for the copper that follows
    }

    private static bool ViaOnLayer(ViaItem v, int layer)
        => layer >= Math.Min(v.FromLayer, v.ToLayer) && layer <= Math.Max(v.FromLayer, v.ToLayer);

    public static string GerberMask(BoardDocument doc, bool top)
    {
        // Mask files are negative: drawn areas are mask OPENINGS.
        var sb = new StringBuilder();
        Header(sb, top ? "Soldermask,Top" : "Soldermask,Bot");
        int outer = top ? 0 : doc.CopperLayers - 1;
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                if (!pad.ThroughHole && NormalizedSide(doc, fp) != outer) continue;
                EmitPadRegion(sb, fp, pad, MaskExpansionMm);
            }
        // vias stay tented (no openings) — standard default for HDI/dense boards
        sb.AppendLine("M02*");
        return sb.ToString();
    }

    public static string GerberPaste(BoardDocument doc, bool top)
    {
        var sb = new StringBuilder();
        Header(sb, top ? "Paste,Top" : "Paste,Bot");
        int outer = top ? 0 : doc.CopperLayers - 1;
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                if (pad.ThroughHole || NormalizedSide(doc, fp) != outer) continue;
                EmitPadRegion(sb, fp, pad, 0);
            }
        sb.AppendLine("M02*");
        return sb.ToString();
    }

    private static void EmitPadRegion(StringBuilder sb, FootprintItem fp, PadItem pad, double expandMm)
    {
        var quad = PadQuad(fp, pad, expandMm);
        sb.AppendLine("G36*");
        sb.AppendLine($"X{Coord(quad[0].x)}Y{Coord(quad[0].y)}D02*");
        for (int i = 1; i < 4; i++)
            sb.AppendLine($"X{Coord(quad[i].x)}Y{Coord(quad[i].y)}D01*");
        sb.AppendLine($"X{Coord(quad[0].x)}Y{Coord(quad[0].y)}D01*");
        sb.AppendLine("G37*");
    }

    public static string GerberEdge(BoardDocument doc)
    {
        var sb = new StringBuilder();
        Header(sb, "Profile,NP");
        sb.AppendLine("%ADD10C,0.1000*%");
        sb.AppendLine("D10*");
        // trace the real board outline (rectangle, circle, or any custom polygon)
        var poly = doc.EffectiveOutline();
        sb.AppendLine($"X{Coord(poly[0][0])}Y{Coord(poly[0][1])}D02*");
        for (int i = 1; i < poly.Count; i++)
            sb.AppendLine($"X{Coord(poly[i][0])}Y{Coord(poly[i][1])}D01*");
        sb.AppendLine($"X{Coord(poly[0][0])}Y{Coord(poly[0][1])}D01*");   // close the profile
        sb.AppendLine("M02*");
        return sb.ToString();
    }

    // ---- drill-span report: laser (microvia) vs mechanical, per span (E11/HDI) ----

    /// <summary>
    /// Human-readable drill program: every distinct (fabrication-class, layer-span,
    /// drill) grouped, labelled LASER for microvias and MECH for through/blind/
    /// buried. This is what the fab needs to set up the laser and mechanical drill
    /// passes for an HDI build.
    /// </summary>
    public static string DrillSpanReport(BoardDocument doc)
    {
        var sb = new StringBuilder();
        sb.AppendLine($"DRILL SPANS ({doc.CopperLayers}-layer board):");

        var pth = doc.Footprints.SelectMany(f => f.Pads).Count(p => p.ThroughHole && p.DrillMm > 0);
        if (pth > 0)
            sb.AppendLine($"  MECH  L1-L{doc.CopperLayers}  through-hole pads  ×{pth}");

        var groups = doc.Vias
            .Select(v => new
            {
                Type = HdiVias.Resolve(doc, v),
                From = Math.Min(v.FromLayer, v.ToLayer) + 1,
                To = Math.Max(v.FromLayer, v.ToLayer) + 1,
                Drill = v.DrillMm
            })
            .GroupBy(x => (x.Type, x.From, x.To, x.Drill))
            .OrderByDescending(g => HdiVias.IsLaser(g.Key.Type) ? 0 : 1)   // mechanical first
            .ThenBy(g => g.Key.From).ThenBy(g => g.Key.To);

        foreach (var g in groups)
            sb.AppendLine($"  {(HdiVias.IsLaser(g.Key.Type) ? "LASER" : "MECH ")}  " +
                          $"L{g.Key.From}-L{g.Key.To}  ⌀{g.Key.Drill.ToString("F3", Inv)} mm  " +
                          $"×{g.Count()}  ({g.Key.Type})");

        if (doc.Vias.Count == 0 && pth == 0) sb.AppendLine("  (no drilled features)");
        return sb.ToString();
    }

    // ---- drills: one file per plated span ----

    public static List<((int from, int to) span, string content)> ExcellonFiles(BoardDocument doc)
    {
        var holesBySpan = new Dictionary<(int, int), List<(double drill, double x, double y)>>();
        var throughSpan = (0, doc.CopperLayers - 1);

        void Add((int, int) span, double drill, double x, double y)
        {
            if (!holesBySpan.TryGetValue(span, out var list))
                holesBySpan[span] = list = new List<(double, double, double)>();
            list.Add((drill, x, y));
        }

        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
                if (pad.ThroughHole && pad.DrillMm > 0)
                {
                    var (x, y) = fp.PadWorld(pad);
                    Add(throughSpan, pad.DrillMm, x, y);
                }
        foreach (var v in doc.Vias)
        {
            var span = (Math.Min(v.FromLayer, v.ToLayer), Math.Max(v.FromLayer, v.ToLayer));
            Add(span, v.DrillMm, v.X, v.Y);
        }
        if (!holesBySpan.ContainsKey(throughSpan))
            holesBySpan[throughSpan] = new List<(double, double, double)>();

        var result = new List<((int, int), string)>();
        foreach (var (span, holes) in holesBySpan.OrderBy(kv => kv.Key))
        {
            var sb = new StringBuilder();
            sb.AppendLine("M48");
            sb.AppendLine("METRIC,TZ");
            var tools = holes.Select(hl => hl.drill).Distinct().OrderBy(d => d).ToList();
            for (int i = 0; i < tools.Count; i++)
                sb.AppendLine($"T{i + 1:D2}C{tools[i].ToString("F3", Inv)}");
            sb.AppendLine("%");
            sb.AppendLine("G90");
            sb.AppendLine("G05");
            for (int i = 0; i < tools.Count; i++)
            {
                sb.AppendLine($"T{i + 1:D2}");
                foreach (var hl in holes.Where(hh => hh.drill == tools[i]))
                    sb.AppendLine($"X{hl.x.ToString("F3", Inv)}Y{hl.y.ToString("F3", Inv)}");
            }
            sb.AppendLine("T0");
            sb.AppendLine("M30");
            result.Add((span, sb.ToString()));
        }
        return result;
    }

    public static string Bom(BoardDocument doc)
    {
        var sb = new StringBuilder();
        sb.AppendLine("Qty,Designators,Footprint");
        foreach (var grp in doc.Footprints.GroupBy(f => f.LibName).OrderBy(g => g.Key))
        {
            string refs = string.Join(" ", grp.Select(f => f.RefDes).OrderBy(r => r, StringComparer.OrdinalIgnoreCase));
            sb.AppendLine($"{grp.Count()},\"{refs}\",\"{grp.Key}\"");
        }
        return sb.ToString();
    }

    public static string Centroid(BoardDocument doc)
    {
        var sb = new StringBuilder();
        sb.AppendLine("RefDes,X_mm,Y_mm,Rotation_deg,Side,Footprint");
        foreach (var fp in doc.Footprints.OrderBy(f => f.RefDes, StringComparer.OrdinalIgnoreCase))
            sb.AppendLine(string.Join(",",
                fp.RefDes,
                fp.X.ToString("F3", Inv),
                fp.Y.ToString("F3", Inv),
                fp.RotationDeg.ToString("F1", Inv),
                NormalizedSide(doc, fp) == 0 ? "Top" : "Bottom",
                $"\"{fp.LibName}\""));
        return sb.ToString();
    }

    private static (double x, double y)[] PadQuad(FootprintItem fp, PadItem pad, double expandMm = 0)
    {
        double r = fp.RotationDeg * Math.PI / 180, c = Math.Cos(r), s = Math.Sin(r);
        double cx = fp.X + pad.X * c - pad.Y * s;
        double cy = fp.Y + pad.X * s + pad.Y * c;
        double hw = pad.W / 2 + expandMm, hh = pad.H / 2 + expandMm;
        var local = new (double x, double y)[] { (-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh) };
        var quad = new (double x, double y)[4];
        for (int i = 0; i < 4; i++)
            quad[i] = (cx + local[i].x * c - local[i].y * s,
                       cy + local[i].x * s + local[i].y * c);
        return quad;
    }
}
