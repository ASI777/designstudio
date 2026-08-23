using System.Text.Json;

namespace DesignStudio.Model.Advisor;

// ============================================================================
// Closes the advisor loop: design-studio.advice/1  ->  board connectivity.
//
// Until now the AI loop ended at "advice"; the advisor's connections and every
// placed part's power rails were re-entered by hand (a 100+-pin job for a large
// BGA). AdviceApplier turns that intent into nets deterministically:
//
//   1. Power-rail bundling — for each placed component whose library footprint
//      carries electrical.power_domains, create one net per rail (VSS -> GND)
//      and assign every member ball's pad to it.
//   2. Net classes — each component's high_speed.diff_pairs become impedance-
//      targeted net classes (the field solver derives width/gap later).
//   3. Connections — every action.connections[pin -> to_net] becomes a pad-net
//      assignment on the referenced placed part; if the part is an unplaced
//      `add`, the net is still created so it exists for when the part lands.
//
// Pure managed mutation of the document (no native call), so it is unit-testable
// without the C++ engine and mirrors the standalone tools/apply_advice.py.
// ============================================================================

public sealed record AdviceApplyResult(
    int RailsBundled,
    int NetsCreated,
    int PadsAssigned,
    int NetClassesCreated,
    IReadOnlyList<string> Log);

public static class AdviceApplier
{
    public static AdviceApplyResult Apply(BoardDocument doc, AdviceReport report, FootprintLibrary library)
    {
        var log = new List<string>();
        int nets0 = doc.Nets.Count;
        int classes0 = doc.NetClasses.Count;
        int rails = 0, padsAssigned = 0;

        // ---- 1 & 2: rails + net classes from each placed component ----------
        foreach (var fp in doc.Footprints)
        {
            var def = library.Items.FirstOrDefault(f => f.Name == fp.LibName);
            if (def is null || string.IsNullOrWhiteSpace(def.ElectricalJson)) continue;

            DsElectrical? elec;
            try { elec = JsonSerializer.Deserialize<DsElectrical>(def.ElectricalJson); }
            catch { log.Add($"{fp.RefDes}: electrical payload could not be parsed — skipped."); continue; }
            if (elec is null) continue;

            // index this part's pads by ball/pad name (a name can repeat? keep all)
            var padsByName = fp.Pads
                .GroupBy(p => p.Name)
                .ToDictionary(g => g.Key, g => g.ToList(), StringComparer.OrdinalIgnoreCase);

            // power rails
            var domains = elec.PowerDomains;
            if (domains is null || domains.Count == 0)
            {
                log.Add($"{fp.RefDes}: no power_domains in the component — rails not bundled " +
                        "(regenerate the component JSON with power_domains).");
            }
            else
            {
                foreach (var d in domains)
                {
                    if (d.Pins is null || d.Pins.Count == 0) continue;
                    string netName = string.Equals(d.Name, "VSS", StringComparison.OrdinalIgnoreCase)
                        ? "GND" : d.Name;
                    int netId = doc.AddNet(netName);
                    rails++;
                    foreach (var ball in d.Pins)
                        if (padsByName.TryGetValue(ball, out var pads))
                            foreach (var pad in pads) { pad.NetId = netId; padsAssigned++; }
                }
            }

            // differential net classes
            foreach (var dp in elec.HighSpeed?.DiffPairs ?? new List<DsDiffPair>())
            {
                string cname = $"{dp.Positive}/{dp.Negative}";
                if (doc.NetClasses.Any(c => c.Name == cname)) continue;
                var nc = doc.AddNetClass(cname);
                nc.TargetDiffImpedanceOhm = dp.ImpedanceOhm;
                if (dp.MaxSkewMm is double sk) nc.MaxSkewMm = sk;
            }
        }

        // ---- 3: apply advice connections ------------------------------------
        foreach (var act in report.Actions)
        {
            if (act.Connections is null || act.Connections.Count == 0) continue;

            // remove/replace/move target an existing ref; an `add` part is not
            // placed yet, so only its nets can be created (membership deferred).
            var target = act.ForRef is null
                ? null
                : doc.Footprints.FirstOrDefault(f => f.RefDes == act.ForRef);

            foreach (var c in act.Connections)
            {
                if (string.IsNullOrWhiteSpace(c.ToNet) || string.IsNullOrWhiteSpace(c.Pin)) continue;
                int netId = doc.AddNet(c.ToNet);   // ensure the net exists regardless
                if (target is null)
                {
                    log.Add($"net '{c.ToNet}' created; pin {c.Pin} awaits its part " +
                            $"({act.Mpn ?? "to-add"}) being placed.");
                    continue;
                }
                var pad = target.Pads.FirstOrDefault(p =>
                    string.Equals(p.Name, c.Pin, StringComparison.OrdinalIgnoreCase));
                if (pad is null) { log.Add($"{target.RefDes}: pad '{c.Pin}' not found."); continue; }
                pad.NetId = netId;
                padsAssigned++;
            }
        }

        return new AdviceApplyResult(
            RailsBundled: rails,
            NetsCreated: doc.Nets.Count - nets0,
            PadsAssigned: padsAssigned,
            NetClassesCreated: doc.NetClasses.Count - classes0,
            Log: log);
    }
}
