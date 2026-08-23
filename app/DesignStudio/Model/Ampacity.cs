using System.Text.Json;

namespace DesignStudio.Model;

// ============================================================================
// 5.2 Current-capacity (ampacity) checking — IPC-2221 chart equations.
//
// Required current per net comes from the datasheet payloads already in the
// library: each component's electrical.power_domains lists max_current_a and
// member pins; placed pads map pins to nets. The capacity of copper follows
// IPC-2221: I = k · ΔT^0.44 · A^0.725  (A in mil², k = 0.048 external /
// 0.024 internal). Vias are checked as a copper barrel annulus.
//
// Findings surface as DRC rule 18 ("Current capacity").
// ============================================================================

public static class Ampacity
{
    public const double DefaultTempRiseC = 10;
    private const double PlatingThicknessMm = 0.025;   // typical 1 mil via plating
    private const double MilPerMm = 39.3701;

    /// <summary>Max current (A) of a trace cross-section per IPC-2221.</summary>
    public static double TraceCapacityA(double widthMm, double thicknessMm, bool external,
                                        double tempRiseC = DefaultTempRiseC)
    {
        double areaMil2 = widthMm * MilPerMm * (thicknessMm * MilPerMm);
        double k = external ? 0.048 : 0.024;
        return k * Math.Pow(tempRiseC, 0.44) * Math.Pow(areaMil2, 0.725);
    }

    /// <summary>Max current of a plated via barrel (annular cross-section, treated as internal copper).</summary>
    public static double ViaCapacityA(double drillMm, double tempRiseC = DefaultTempRiseC)
    {
        double areaMm2 = Math.PI * drillMm * PlatingThicknessMm;        // circumference × plating
        double areaMil2 = areaMm2 * MilPerMm * MilPerMm;
        return 0.024 * Math.Pow(tempRiseC, 0.44) * Math.Pow(areaMil2, 0.725);
    }

    /// <summary>Required current per net, extracted from placed components' power domains.</summary>
    public static Dictionary<int, double> NetCurrents(BoardDocument doc, FootprintLibrary? library)
    {
        var currents = new Dictionary<int, double>();
        if (library is null) return currents;
        foreach (var fp in doc.Footprints)
        {
            var def = library.Items.FirstOrDefault(d => d.Name == fp.LibName);
            if (string.IsNullOrWhiteSpace(def?.ElectricalJson)) continue;
            DsElectrical? el;
            try { el = JsonSerializer.Deserialize<DsElectrical>(def!.ElectricalJson); }
            catch (JsonException) { continue; }
            foreach (var pd in el?.PowerDomains ?? new List<DsPowerDomain>())
            {
                if (pd.MaxCurrentA is not double amps || amps <= 0 || pd.Pins is null) continue;
                foreach (var pinName in pd.Pins)
                {
                    var pad = fp.Pads.FirstOrDefault(p => p.Name == pinName);
                    if (pad is null || pad.NetId < 0) continue;
                    // a domain's current may split over several pins; assume even split
                    double perPin = amps / pd.Pins.Count;
                    currents[pad.NetId] = Math.Max(
                        currents.GetValueOrDefault(pad.NetId), perPin * pd.Pins.Count);
                }
            }
        }
        return currents;
    }

    /// <summary>Rule 18: every trace/via on a current-carrying net must have the capacity.</summary>
    public static List<DrcResultItem> Check(BoardDocument doc, FootprintLibrary? library,
                                            double tempRiseC = DefaultTempRiseC)
    {
        var results = new List<DrcResultItem>();
        var currents = NetCurrents(doc, library);
        if (currents.Count == 0) return results;

        foreach (var (netId, amps) in currents)
        {
            foreach (var t in doc.Traces.Where(t => !t.IsPour && t.NetId == netId))
            {
                bool external = t.Layer == 0 || t.Layer == doc.CopperLayers - 1;
                double cu = doc.Stackup.Count == doc.CopperLayers
                    ? doc.Stackup[t.Layer].CopperThicknessMm : doc.CopperThicknessMm;
                double cap = TraceCapacityA(t.Width, cu, external, tempRiseC);
                if (cap < amps)
                    results.Add(new DrcResultItem(18, "Current capacity",
                        $"{doc.NetName(netId)}: {t.Width:F2} mm trace carries {cap:F2} A max but the net needs {amps:F2} A " +
                        $"(ΔT {tempRiseC:F0} °C) — widen to ≥ {RequiredWidthMm(amps, cu, external, tempRiseC):F2} mm.",
                        (t.Ax + t.Bx) / 2, (t.Ay + t.By) / 2));
            }
            // vias: count parallel vias at the same (x,y) cluster as sharing current is
            // out of scope v1 — each via is checked alone, which is conservative
            foreach (var v in doc.Vias.Where(v => v.NetId == netId))
            {
                double cap = ViaCapacityA(v.DrillMm, tempRiseC);
                if (cap < amps)
                    results.Add(new DrcResultItem(18, "Current capacity",
                        $"{doc.NetName(netId)}: via (drill {v.DrillMm:F2} mm) carries {cap:F2} A max but the net needs {amps:F2} A — " +
                        $"use a larger drill or stitch multiple vias.",
                        v.X, v.Y));
            }
        }
        return results;
    }

    /// <summary>Inverse of TraceCapacityA: minimum width for a target current.</summary>
    public static double RequiredWidthMm(double amps, double thicknessMm, bool external,
                                         double tempRiseC = DefaultTempRiseC)
    {
        double k = external ? 0.048 : 0.024;
        double areaMil2 = Math.Pow(amps / (k * Math.Pow(tempRiseC, 0.44)), 1 / 0.725);
        return areaMil2 / (thicknessMm * MilPerMm) / MilPerMm;
    }
}
