using System.Numerics;
using System.Text.Json;

namespace DesignStudio.Model;

// ============================================================================
// 3.2 PDN AC impedance analysis.
//
// The impedance a load sees on a power net, 100 kHz – 1 GHz, as the parallel
// combination of admittance branches:
//   * VRM:        R + jωL behavioral model (low-f anchor)
//   * plane pair: parallel-plate C from overlapping plane area / dielectric
//   * each decap: series R-L-C (ESR/ESL from the part's electrical
//                 parameters where the datasheet provides them, sensible
//                 MLCC defaults otherwise)
// Compared against the classic target Z = Vnom·ripple% / Itransient.
// Exceeding it — usually an L-C anti-resonance between decap values — raises
// DRC rule 21 with the offending frequency.
// ============================================================================

public static class PdnAnalyzer
{
    public const double RippleFraction = 0.05;          // 5 % allowed ripple
    private const double VrmR = 0.001, VrmLnH = 10;     // behavioral VRM
    private const double DefaultCapF = 100e-9, DefaultEslH = 0.8e-9, DefaultEsrOhm = 0.02;

    public record Decap(double C, double EslH, double EsrOhm, string RefDes);
    public record Point(double FreqHz, double ZOhm);
    public record Report(int NetId, string NetName, double TargetZOhm, List<Point> Sweep,
                         double WorstZOhm, double WorstFreqHz, List<Decap> Decaps, string? Skipped);

    public static Report Analyze(BoardDocument doc, int netId, FootprintLibrary? library)
    {
        string name = doc.NetName(netId);
        var currents = Ampacity.NetCurrents(doc, library);
        if (!currents.TryGetValue(netId, out double amps) || amps <= 0)
            return new(netId, name, 0, new(), 0, 0, new(), "no load current known for the net");
        double vnom = NominalVoltage(doc, netId, library) ?? 3.3;
        double target = vnom * RippleFraction / Math.Max(amps / 2, 1e-3);   // transient ≈ I/2

        // plane-pair capacitance: this net's plane area against the nearest plane spacing
        double planeC = 0;
        foreach (var plane in doc.Planes.Where(p => p.NetId == netId))
        {
            doc.PlanesUpToDate();
            double areaMm2 = plane.Fill.Sum(PlaneGenerator.SignedArea);
            if (areaMm2 <= 0) continue;
            double dMm = doc.Stackup.Count == doc.CopperLayers
                ? doc.Stackup[Math.Min(plane.Layer, doc.Stackup.Count - 1)].DielectricHeightMm
                : doc.DielectricHeightMm;
            double er = doc.Stackup.Count == doc.CopperLayers
                ? doc.Stackup[Math.Min(plane.Layer, doc.Stackup.Count - 1)].ErAt()
                : doc.DielectricEr;
            planeC += 8.854e-12 * er * (areaMm2 * 1e-6) / (dMm * 1e-3);
        }

        var decaps = FindDecaps(doc, netId, library);
        var sweep = new List<Point>(121);
        double worstZ = 0, worstF = 0;
        for (int k = 0; k <= 120; k++)
        {
            double f = 1e5 * Math.Pow(1e9 / 1e5, k / 120.0);
            double w = 2 * Math.PI * f;
            Complex y = 1 / new Complex(VrmR, w * VrmLnH * 1e-9);            // VRM branch
            if (planeC > 0) y += new Complex(0, w * planeC);                  // plane pair
            foreach (var d in decaps)
                y += 1 / new Complex(d.EsrOhm, w * d.EslH - 1 / (w * d.C));   // series RLC
            double z = (1 / y).Magnitude;
            sweep.Add(new Point(f, z));
            if (z > worstZ) { worstZ = z; worstF = f; }
        }
        return new(netId, name, target, sweep, worstZ, worstF, decaps, null);
    }

    /// <summary>Rule 21 over every power net that has known current draw.</summary>
    public static List<DrcResultItem> Check(BoardDocument doc, FootprintLibrary? library)
    {
        var results = new List<DrcResultItem>();
        foreach (var netId in Ampacity.NetCurrents(doc, library).Keys)
        {
            var r = Analyze(doc, netId, library);
            if (r.Skipped != null || r.TargetZOhm <= 0) continue;
            if (r.WorstZOhm > r.TargetZOhm)
                results.Add(new DrcResultItem(21, "PDN impedance",
                    $"{r.NetName}: Z = {r.WorstZOhm * 1000:F1} mΩ at {r.WorstFreqHz / 1e6:F1} MHz exceeds the " +
                    $"{r.TargetZOhm * 1000:F1} mΩ target ({r.Decaps.Count} decap(s) seen) — " +
                    $"add a capacitor resonant near {r.WorstFreqHz / 1e6:F0} MHz close to the load.",
                    0, 0));
        }
        return results;
    }

    /// <summary>
    /// Decaps = 2-pad C-prefix parts bridging this net and a GND net. ESR/ESL
    /// from the part's electrical parameters (rows named ESR / ESL) when the
    /// datasheet JSON provides them, MLCC defaults otherwise.
    /// </summary>
    public static List<Decap> FindDecaps(BoardDocument doc, int netId, FootprintLibrary? library)
    {
        var gnd = doc.Nets.Where(n => n.Name.Contains("GND", StringComparison.OrdinalIgnoreCase))
                          .Select(n => n.Id).ToHashSet();
        var found = new List<Decap>();
        foreach (var fp in doc.Footprints)
        {
            if (!fp.RefDes.StartsWith('C') || fp.Pads.Count != 2) continue;
            bool onNet = fp.Pads.Any(p => p.NetId == netId);
            bool onGnd = fp.Pads.Any(p => gnd.Contains(p.NetId));
            if (!onNet || !onGnd) continue;

            double c = DefaultCapF, esl = DefaultEslH, esr = DefaultEsrOhm;
            var def = library?.Items.FirstOrDefault(d => d.Name == fp.LibName);
            if (!string.IsNullOrWhiteSpace(def?.ElectricalJson))
                try
                {
                    var el = JsonSerializer.Deserialize<DsElectrical>(def!.ElectricalJson);
                    foreach (var p in el?.Parameters ?? new List<DsParameter>())
                    {
                        double? val = p.Typ ?? p.Max ?? p.Min;
                        if (val is null) continue;
                        if (p.Name.Contains("capacitance", StringComparison.OrdinalIgnoreCase) &&
                            p.Unit.Contains('F')) c = Scale(val.Value, p.Unit, "F");
                        else if (p.Name.Contains("ESL", StringComparison.OrdinalIgnoreCase)) esl = Scale(val.Value, p.Unit, "H");
                        else if (p.Name.Contains("ESR", StringComparison.OrdinalIgnoreCase)) esr = Scale(val.Value, p.Unit, "Ω");
                    }
                }
                catch (JsonException) { }
            found.Add(new Decap(c, esl, esr, fp.RefDes));
        }
        return found;
    }

    private static double Scale(double v, string unit, string baseUnit)
        => unit.TrimEnd(baseUnit[0], 'Ω', 'F', 'H') switch
        {
            "p" => v * 1e-12, "n" => v * 1e-9, "u" or "µ" => v * 1e-6,
            "m" => v * 1e-3, _ => v
        };

    // ------------------------------------------------------------------------
    // E6 — spatial PDN: build a plane-cavity model from the board and analyse
    // Z(f) at the load with decaps at their real coordinates.
    // ------------------------------------------------------------------------

    public record SpatialReport(int NetId, string NetName, PlaneCavity.Result? Cavity, string? Skipped);

    /// <summary>
    /// Build a rectangular plane-cavity model for a power net: the plane's
    /// bounding box is the cavity, the load (footprint with the most pads on the
    /// net) is port 0, and every decap is a port at its footprint location.
    /// Returns null when the net has no plane to resolve geometry from.
    /// </summary>
    public static PlaneCavity? BuildCavity(BoardDocument doc, int netId, FootprintLibrary? library,
                                           double fMaxHz = 2e9)
    {
        doc.PlanesUpToDate();
        var plane = doc.Planes.Where(p => p.NetId == netId)
                              .OrderByDescending(p => p.Fill.Sum(f => Math.Abs(PlaneGenerator.SignedArea(f))))
                              .FirstOrDefault();
        if (plane == null || plane.Fill.Count == 0) return null;

        // bounding box of the plane copper
        double x0 = double.MaxValue, y0 = double.MaxValue, x1 = double.MinValue, y1 = double.MinValue;
        foreach (var ring in plane.Fill)
            foreach (var (x, y) in ring)
            { x0 = Math.Min(x0, x); y0 = Math.Min(y0, y); x1 = Math.Max(x1, x); y1 = Math.Max(y1, y); }
        double a = x1 - x0, b = y1 - y0;
        if (a < 1 || b < 1) return null;

        double dMm = doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup[Math.Min(plane.Layer, doc.Stackup.Count - 1)].DielectricHeightMm
            : doc.DielectricHeightMm;
        double er = doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup[Math.Min(plane.Layer, doc.Stackup.Count - 1)].ErAt()
            : doc.DielectricEr;
        double tanD = doc.Stackup.Count == doc.CopperLayers
            ? doc.Stackup[Math.Min(plane.Layer, doc.Stackup.Count - 1)].TanAt()
            : doc.DielectricLossTangent;

        // port 0 = the load (most pads on the net); fall back to the plane centre
        var load = doc.Footprints
            .Select(fp => (fp, n: fp.Pads.Count(p => p.NetId == netId)))
            .Where(t => t.n > 0).OrderByDescending(t => t.n).Select(t => t.fp).FirstOrDefault();
        var ports = new List<PlaneCavity.Port>
        {
            load != null
                ? new PlaneCavity.Port(Clamp(load.X - x0, a), Clamp(load.Y - y0, b), $"LOAD:{load.RefDes}")
                : new PlaneCavity.Port(a / 2, b / 2, "LOAD")
        };

        // decaps: reuse the RLC extraction, place each at its footprint
        var rlc = FindDecaps(doc, netId, library);
        var decaps = new List<PlaneCavity.Decap>();
        foreach (var d in rlc)
        {
            var fp = doc.Footprints.FirstOrDefault(f => f.RefDes == d.RefDes);
            if (fp == null) continue;
            ports.Add(new PlaneCavity.Port(Clamp(fp.X - x0, a), Clamp(fp.Y - y0, b), d.RefDes));
            decaps.Add(new PlaneCavity.Decap(ports.Count - 1, d.C, d.EslH, d.EsrOhm, d.RefDes));
        }

        return new PlaneCavity(a, b, dMm, er, tanD, ports, decaps, fMaxHz: fMaxHz);

        static double Clamp(double v, double hi) => Math.Clamp(v, 0, hi);
    }

    /// <summary>Spatial PDN analysis (E6): Z(f) at the load with decap placement,
    /// resonances, per-decap effectiveness and a placement hint.</summary>
    public static SpatialReport AnalyzeSpatial(BoardDocument doc, int netId, FootprintLibrary? library)
    {
        string name = doc.NetName(netId);
        var lumped = Analyze(doc, netId, library);
        if (lumped.TargetZOhm <= 0) return new(netId, name, null, lumped.Skipped ?? "no PDN target");
        var cavity = BuildCavity(doc, netId, library);
        if (cavity == null) return new(netId, name, null, "no plane on this net for a spatial model");
        return new(netId, name, cavity.Analyze(lumped.TargetZOhm), null);
    }

    /// <summary>
    /// Spatial PDN via the 2-D FDM solver (E13) — for split / L-shaped / voided
    /// planes the rectangular modal model can't represent. Meshes the actual
    /// plane copper; falls back to a "no plane" report when none exists.
    /// </summary>
    public static SpatialReport AnalyzeSpatialFdm(BoardDocument doc, int netId, FootprintLibrary? library)
    {
        string name = doc.NetName(netId);
        var lumped = Analyze(doc, netId, library);
        double target = lumped.TargetZOhm > 0 ? lumped.TargetZOhm : DefaultBudgetVForFdm;
        doc.PlanesUpToDate();
        var plane = doc.Planes.Where(p => p.NetId == netId)
                              .OrderByDescending(p => p.Fill.Sum(f => Math.Abs(PlaneGenerator.SignedArea(f))))
                              .FirstOrDefault();
        if (plane == null || plane.Fill.Count == 0)
            return new(netId, name, null, "no plane on this net for an FDM model");

        var load = doc.Footprints
            .Select(fp => (fp, n: fp.Pads.Count(p => p.NetId == netId)))
            .Where(t => t.n > 0).OrderByDescending(t => t.n).Select(t => t.fp).FirstOrDefault();
        (double x, double y) loadXY = load != null ? (load.X, load.Y)
            : (doc.BoardWidthMm / 2, doc.BoardHeightMm / 2);

        var decaps = new List<(double, double, double, double, double, string)>();
        foreach (var d in FindDecaps(doc, netId, library))
        {
            var fp = doc.Footprints.FirstOrDefault(f => f.RefDes == d.RefDes);
            if (fp != null) decaps.Add((fp.X, fp.Y, d.C, d.EslH, d.EsrOhm, d.RefDes));
        }

        var fdm = PlanePdnFdm.FromPlane(doc, plane, loadXY, decaps);
        if (fdm == null) return new(netId, name, null, "plane too small to mesh");
        return new(netId, name, fdm.Analyze(target), null);
    }

    private const double DefaultBudgetVForFdm = 0.05;

    /// <summary>Spatial PDN DRC (rule 22): fires when the cavity Z exceeds target,
    /// with the placement hint. Kept separate from the lumped rule-21 run.</summary>
    public static List<DrcResultItem> CheckSpatial(BoardDocument doc, FootprintLibrary? library)
    {
        var results = new List<DrcResultItem>();
        foreach (var netId in Ampacity.NetCurrents(doc, library).Keys)
        {
            var r = AnalyzeSpatial(doc, netId, library);
            if (r.Cavity == null) continue;
            var c = r.Cavity;
            if (c.WorstZOhm > c.TargetZOhm && c.Hint != null)
                results.Add(new DrcResultItem(22, "PDN (spatial)",
                    $"{r.NetName}: Z = {c.WorstZOhm * 1000:F1} mΩ at {c.WorstFreqHz / 1e6:F1} MHz " +
                    $"exceeds the {c.TargetZOhm * 1000:F1} mΩ target — {c.Hint.Reason}.",
                    c.Hint.XMm, c.Hint.YMm));
        }
        return results;
    }

    private static double? NominalVoltage(BoardDocument doc, int netId, FootprintLibrary? library)
    {
        foreach (var fp in doc.Footprints)
        {
            var def = library?.Items.FirstOrDefault(d => d.Name == fp.LibName);
            if (string.IsNullOrWhiteSpace(def?.ElectricalJson)) continue;
            try
            {
                var el = JsonSerializer.Deserialize<DsElectrical>(def!.ElectricalJson);
                foreach (var pd in el?.PowerDomains ?? new List<DsPowerDomain>())
                    if (pd.VnomV is double v &&
                        pd.Pins?.Any(pin => fp.Pads.FirstOrDefault(p => p.Name == pin)?.NetId == netId) == true)
                        return v;
            }
            catch (JsonException) { }
        }
        return null;
    }
}
