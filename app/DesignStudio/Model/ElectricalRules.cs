namespace DesignStudio.Model;

/// <summary>
/// ERC-lite: electrical rule checks driven by per-pad electrical types
/// (input/output/bidirectional/power_in/power_out/passive/nc), which arrive
/// from datasheet JSON imports. Runs alongside the geometric DRC and reports
/// into the same results panel. Rule codes 101+ keep clear of the C++ DRC
/// codes (1–13).
/// </summary>
public static class ElectricalRules
{
    public const int RuleOutputConflict = 101;
    public const int RuleNcConnected = 102;
    public const int RuleUnpoweredInput = 103;

    private static readonly string[] PowerNetHints =
        { "GND", "VCC", "VDD", "VBUS", "VIN", "VOUT", "3V3", "3.3V", "5V", "12V", "PWR", "VBAT", "AVDD", "DVDD" };

    public static List<DrcResultItem> Check(BoardDocument doc)
    {
        var results = new List<DrcResultItem>();

        // gather pins per net
        var byNet = new Dictionary<int, List<(FootprintItem fp, PadItem pad)>>();
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                if (pad.NetId < 0)
                {
                    // NC pins are *supposed* to be unconnected — nothing to do.
                    continue;
                }
                if (!byNet.TryGetValue(pad.NetId, out var list))
                    byNet[pad.NetId] = list = new List<(FootprintItem, PadItem)>();
                list.Add((fp, pad));
            }

        foreach (var (netId, pinList) in byNet)
        {
            string netName = doc.NetName(netId);

            // 1. multiple push-pull outputs shorted together
            var outputs = pinList.Where(p => p.pad.ElectricalType == "output").ToList();
            if (outputs.Count >= 2)
            {
                var (fp, pad) = outputs[0];
                var (x, y) = fp.PadWorld(pad);
                string who = string.Join(", ", outputs.Select(o => $"{o.fp.RefDes}.{o.pad.Name}"));
                results.Add(new DrcResultItem(RuleOutputConflict, "ERC: output conflict",
                    $"Net {netName}: {outputs.Count} outputs driven together ({who})", x, y));
            }

            // 2. no-connect pins that are connected
            foreach (var (fp, pad) in pinList.Where(p => p.pad.ElectricalType == "nc"))
            {
                var (x, y) = fp.PadWorld(pad);
                results.Add(new DrcResultItem(RuleNcConnected, "ERC: NC pin connected",
                    $"{fp.RefDes}.{pad.Name} is a no-connect pin but is on net {netName}", x, y));
            }

            // 3. power inputs on a net with no power source and no power-ish name
            var powerIns = pinList.Where(p => p.pad.ElectricalType == "power_in").ToList();
            if (powerIns.Count > 0)
            {
                bool hasSource = pinList.Any(p => p.pad.ElectricalType == "power_out");
                bool looksLikePower = PowerNetHints.Any(h =>
                    netName.Contains(h, StringComparison.OrdinalIgnoreCase));
                if (!hasSource && !looksLikePower)
                {
                    var (fp, pad) = powerIns[0];
                    var (x, y) = fp.PadWorld(pad);
                    results.Add(new DrcResultItem(RuleUnpoweredInput, "ERC: unpowered power input",
                        $"Net {netName}: {powerIns.Count} power input pin(s) with no power source on the net", x, y));
                }
            }
        }

        return results;
    }
}
