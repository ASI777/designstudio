using DesignStudio.Model;
using DesignStudio.Model.Advisor;
using Xunit;

namespace DesignStudio.Tests;

public class AdviceApplierTests
{
    // A placed part whose footprint carries power_domains + a diff pair, and an
    // advice file with a connection on it and a deferred connection for an add.
    private static (BoardDocument doc, FootprintLibrary lib) Scene()
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(40, 30);
        doc.Footprints.Add(new FootprintItem
        {
            RefDes = "U1", LibName = "MPFS025TC-FCSG325", X = 20, Y = 15,
            Pads = new List<PadItem>
            {
                new() { Name = "A1", NetId = -1, W = 0.3, H = 0.3, ElectricalType = "power_in" },     // VDD
                new() { Name = "B1", NetId = -1, W = 0.3, H = 0.3, ElectricalType = "power_in" },     // VSS
                new() { Name = "A2", NetId = -1, W = 0.3, H = 0.3, ElectricalType = "bidirectional" } // wired by advice
            }
        });
        doc.NotifyChanged();

        var lib = new FootprintLibrary();
        lib.Items.Add(new FootprintDef
        {
            Name = "MPFS025TC-FCSG325", Mpn = "MPFS025TC-FCSG325", Manufacturer = "Microchip",
            ElectricalJson = """
            {
              "power_domains": [
                { "name": "VDD", "pins": ["A1"] },
                { "name": "VSS", "pins": ["B1"] }
              ],
              "high_speed": { "diff_pairs": [
                { "positive": "TX_P", "negative": "TX_N", "impedance_ohm": 85 }
              ]}
            }
            """
        });
        return (doc, lib);
    }

    private const string AdviceJson = """
    {
      "schema": "design-studio.advice/1",
      "summary": "bring-up",
      "actions": [
        { "op": "replace", "for_ref": "U1", "reason": "wire a fabric output",
          "connections": [ { "pin": "A2", "to_net": "GPIO_OUT" } ] },
        { "op": "add", "mpn": "REG1", "reason": "core regulator",
          "connections": [ { "pin": "VOUT", "to_net": "VDD" } ] }
      ],
      "risks": []
    }
    """;

    [Fact]
    public void BundlesPowerRailsAndAssignsPads()
    {
        var (doc, lib) = Scene();
        var report = AdviceContract.Parse(AdviceJson);

        var r = AdviceApplier.Apply(doc, report, lib);

        Assert.Equal(2, r.RailsBundled);                                   // VDD + VSS
        var vdd = doc.Nets.Single(n => n.Name == "VDD");
        var gnd = doc.Nets.Single(n => n.Name == "GND");                   // VSS renamed to GND
        Assert.Equal(vdd.Id, doc.Footprints[0].Pads.Single(p => p.Name == "A1").NetId);
        Assert.Equal(gnd.Id, doc.Footprints[0].Pads.Single(p => p.Name == "B1").NetId);
    }

    [Fact]
    public void CreatesDiffPairNetClassWithImpedance()
    {
        var (doc, lib) = Scene();
        var r = AdviceApplier.Apply(doc, AdviceContract.Parse(AdviceJson), lib);

        Assert.Equal(1, r.NetClassesCreated);
        var nc = doc.NetClasses.Single(c => c.Name == "TX_P/TX_N");
        Assert.Equal(85, nc.TargetDiffImpedanceOhm, 6);
    }

    [Fact]
    public void AppliesConnectionOnPlacedPartAndDefersUnplacedAdd()
    {
        var (doc, lib) = Scene();
        var r = AdviceApplier.Apply(doc, AdviceContract.Parse(AdviceJson), lib);

        // connection on the placed U1 wires A2 -> GPIO_OUT
        var gpio = doc.Nets.Single(n => n.Name == "GPIO_OUT");
        Assert.Equal(gpio.Id, doc.Footprints[0].Pads.Single(p => p.Name == "A2").NetId);

        // the add's VOUT->VDD reuses the existing VDD net and is logged as deferred
        Assert.Single(doc.Nets, n => n.Name == "VDD");
        Assert.Contains(r.Log, m => m.Contains("awaits its part"));
    }

    [Fact]
    public void NoPowerDomainsIsReportedNotCrashed()
    {
        var (doc, lib) = Scene();
        lib.Items[0].ElectricalJson = "{}";                                // strip power_domains
        var r = AdviceApplier.Apply(doc, AdviceContract.Parse(AdviceJson), lib);

        Assert.Equal(0, r.RailsBundled);
        Assert.Contains(r.Log, m => m.Contains("no power_domains"));
    }
}
