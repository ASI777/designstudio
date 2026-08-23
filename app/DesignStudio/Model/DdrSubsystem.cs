using System.Text;
using DesignStudio.Model.LinkSim;

namespace DesignStudio.Model;

// ============================================================================
// E19. Full DDR5 channel topology — per-channel / per-socket sign-off.
//
// The DdrTimingEngine (E7) margins one byte lane. A real DDR5 subsystem is a
// hierarchy: socket → channel → { data byte lanes (DQ×8 + DQS) + a fly-by
// command/address bus }, often with 2 DIMMs per channel (2DPC). This builds
// that structure and rolls the margins up:
//
//   • each data byte lane → DdrTimingEngine.AnalyzeByteLane (S6, validated);
//   • the CA bus is the same setup/hold problem against the *clock* rather than
//     the strobe, at half the data rate (1 command per CK), with command
//     setup/hold (tIS/tIH) — so it reuses AnalyzeByteLane with a half-rate spec;
//   • 2DPC adds the idle rank's reflection/ISI as extra deterministic jitter,
//     which closes every lane in the channel;
//   • the worst margin over all lanes + CA is the channel verdict, and the worst
//     channel is the subsystem verdict.
//
// Output: a per-channel margin table and an overall pass/fail — the sign-off a
// dense multi-socket DDR5 board is judged by.
// ============================================================================

public static class DdrSubsystem
{
    public sealed record ByteLane(string Name, IReadOnlyList<int> DqNets, int DqsNet);
    public sealed record CaBus(IReadOnlyList<int> CaNets, int ClkNet);
    /// <summary>One channel: its data byte lanes, an optional fly-by CA bus, and
    /// the DIMMs populated on it (1 or 2 — 2DPC derates every lane).</summary>
    public sealed record Channel(string Name, IReadOnlyList<ByteLane> Lanes, CaBus? Ca, int DimmsPerChannel = 1);

    public sealed record ChannelResult(string Name, List<DdrTimingEngine.LaneReport> DataLanes,
                                        DdrTimingEngine.LaneReport? Ca, int Dpc,
                                        double WorstMarginPs, string WorstWhere, bool Pass);

    public sealed record Report(string Name, List<ChannelResult> Channels, double WorstMarginPs,
                                string WorstChannel, bool Pass, string Summary);

    /// <summary>
    /// Analyse a whole DDR5 subsystem (one socket, or several — just list every
    /// channel). <paramref name="pdn"/> supplies SSN (E6), <paramref name="eq"/>
    /// any equalisation. CA timing uses caTIs/caTIh against the clock.
    /// </summary>
    public static Report Analyze(BoardDocument doc, string name, IReadOnlyList<Channel> channels,
                                 DdrTimingEngine.Spec spec, PlaneCavity? pdn = null, Equalizer? eq = null,
                                 double caTIsPs = 30, double caTIhPs = 30)
    {
        double uiDq = 1e6 / spec.DataRateMtps;
        var results = new List<ChannelResult>();

        foreach (var ch in channels)
        {
            // 2DPC: the idle rank's stub reflects → extra data-dependent jitter on
            // every lane (≈10 % UI here; the board-level reflection is a later refinement).
            double dpcJitter = ch.DimmsPerChannel >= 2 ? 0.10 * uiDq : 0;
            var specDq = spec with { DjPs = spec.DjPs + dpcJitter };

            var lanes = ch.Lanes
                .Select(l => DdrTimingEngine.AnalyzeByteLane(doc, l.DqNets.ToList(), l.DqsNet, specDq,
                                                            pdn, eq, $"{ch.Name}/{l.Name}"))
                .ToList();

            DdrTimingEngine.LaneReport? ca = null;
            if (ch.Ca != null && ch.Ca.CaNets.Count > 0)
            {
                var specCa = spec with
                {
                    DataRateMtps = spec.DataRateMtps / 2,     // 1 command per CK
                    TdsPs = caTIsPs,
                    TdhPs = caTIhPs,
                    DjPs = spec.DjPs + dpcJitter
                };
                ca = DdrTimingEngine.AnalyzeByteLane(doc, ch.Ca.CaNets.ToList(), ch.Ca.ClkNet, specCa,
                                                     pdn, eq, $"{ch.Name}/CA");
            }

            double worst = double.MaxValue; string where = ch.Name;
            void Consider(DdrTimingEngine.LaneReport r)
            {
                double m = Math.Min(r.WorstSetupPs, r.WorstHoldPs);
                if (m < worst) { worst = m; where = $"{r.LaneName}:{r.WorstBit}"; }
            }
            foreach (var l in lanes) Consider(l);
            if (ca != null) Consider(ca);

            bool pass = lanes.All(l => l.Pass) && (ca?.Pass ?? true);
            results.Add(new ChannelResult(ch.Name, lanes, ca, ch.DimmsPerChannel, worst, where, pass));
        }

        var worstCh = results.MinBy(c => c.WorstMarginPs);
        bool allPass = results.All(c => c.Pass);

        var sb = new StringBuilder();
        sb.AppendLine($"DDR5 subsystem {name} @ {spec.DataRateMtps:F0} MT/s: {(allPass ? "PASS" : "FAIL")} " +
                      $"({results.Count} channels)");
        sb.AppendLine($"  worst margin {worstCh?.WorstMarginPs:+0.0;-0.0} ps in {worstCh?.Name} ({worstCh?.WorstWhere})");
        foreach (var c in results)
            sb.AppendLine($"  {c.Name} ({c.Dpc}DPC): {(c.Pass ? "PASS" : "FAIL")} — " +
                          $"worst {c.WorstMarginPs:+0.0;-0.0} ps ({c.WorstWhere}), " +
                          $"{c.DataLanes.Count} data lane(s){(c.Ca != null ? " + CA" : "")}");

        return new Report(name, results, worstCh?.WorstMarginPs ?? 0, worstCh?.Name ?? "", allPass, sb.ToString());
    }
}
