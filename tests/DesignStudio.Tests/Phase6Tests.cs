using DesignStudio.Model;
using Xunit;

namespace DesignStudio.Tests;

public class BatchRouterTests
{
    private static BoardDocument TwoPartBoard(out int n1, out int n2)
    {
        var doc = new BoardDocument();
        doc.SetBoardSize(50, 40);
        doc.SetCopperLayers(2);
        n1 = doc.AddNet("SIG1");
        n2 = doc.AddNet("SIG2");
        var u1 = new FootprintItem { RefDes = "U1", X = 10, Y = 20 };
        u1.Pads.Add(new PadItem { Name = "1", X = 0, Y = -2, W = 1, H = 1, NetId = n1 });
        u1.Pads.Add(new PadItem { Name = "2", X = 0, Y = 2, W = 1, H = 1, NetId = n2 });
        var u2 = new FootprintItem { RefDes = "U2", X = 40, Y = 20 };
        u2.Pads.Add(new PadItem { Name = "1", X = 0, Y = -2, W = 1, H = 1, NetId = n1 });
        u2.Pads.Add(new PadItem { Name = "2", X = 0, Y = 2, W = 1, H = 1, NetId = n2 });
        doc.Footprints.Add(u1);
        doc.Footprints.Add(u2);
        doc.NotifyChanged();
        return doc;
    }

    [Fact]
    public void FindsMstConnectionsForUnroutedNets()
    {
        using var doc = TwoPartBoard(out _, out _);
        var conns = BatchRouter.UnroutedConnections(doc);
        Assert.Equal(2, conns.Count);          // one edge per 2-pad net
    }

    [Fact]
    public void RoutesEverythingOnAnEmptyBoard()
    {
        using var doc = TwoPartBoard(out int n1, out int n2);
        var r = BatchRouter.RouteAll(doc);
        Assert.Equal(0, r.Failed);
        Assert.Equal(2, r.Routed);
        Assert.Equal(1, doc.NetIslands(n1));   // fully connected
        Assert.Equal(1, doc.NetIslands(n2));
    }

    [Fact]
    public void RoutedNetsProduceNoNewConnections()
    {
        using var doc = TwoPartBoard(out _, out _);
        BatchRouter.RouteAll(doc);
        Assert.Empty(BatchRouter.UnroutedConnections(doc));
    }

    [Fact]
    public void ThreePadNetGetsTwoMstEdges()
    {
        using var doc = new BoardDocument();
        doc.SetBoardSize(60, 40);
        doc.SetCopperLayers(2);
        int n = doc.AddNet("BUS");
        for (int i = 0; i < 3; i++)
        {
            var fp = new FootprintItem { RefDes = $"R{i + 1}", X = 10 + i * 15, Y = 20 };
            fp.Pads.Add(new PadItem { Name = "1", W = 1, H = 1, NetId = n });
            doc.Footprints.Add(fp);
        }
        doc.NotifyChanged();
        Assert.Equal(2, BatchRouter.UnroutedConnections(doc).Count);
    }
}

public class SchematicTests
{
    private static FootprintDef Reg() => new()
    {
        Name = "REG", RefDesPrefix = "U",
        Pads =
        {
            new PadDef { Name = "VIN", ElectricalType = "power_in" },
            new PadDef { Name = "VOUT", ElectricalType = "power_out" },
            new PadDef { Name = "GND", ElectricalType = "power_in" }
        }
    };

    private static FootprintDef Cap() => new()
    {
        Name = "C_0402", RefDesPrefix = "C",
        Pads =
        {
            new PadDef { Name = "1", ElectricalType = "passive" },
            new PadDef { Name = "2", ElectricalType = "passive" }
        }
    };

    [Fact]
    public void WiredPinsLandOnTheSameNet()
    {
        var sch = new SchematicDocument();
        var u1 = SchematicDocument.FromLibrary(Reg(), "U1", 20, 20);
        var c1 = SchematicDocument.FromLibrary(Cap(), "C1", 50, 20);
        sch.Components.Add(u1);
        sch.Components.Add(c1);
        var from = u1.Pins.First(p => p.Name == "VOUT");
        var to = c1.Pins.First(p => p.Name == "1");
        sch.Wires.Add(new SchWire
        {
            Ax = u1.X + from.DX, Ay = u1.Y + from.DY,
            Bx = c1.X + to.DX, By = c1.Y + to.DY
        });
        var nets = sch.ExtractNets();
        Assert.Single(nets);
        Assert.Equal(2, nets[0].Pins.Count);
    }

    [Fact]
    public void LabelNamesTheNet()
    {
        var sch = new SchematicDocument();
        var u1 = SchematicDocument.FromLibrary(Reg(), "U1", 20, 20);
        var c1 = SchematicDocument.FromLibrary(Cap(), "C1", 50, 20);
        sch.Components.Add(u1); sch.Components.Add(c1);
        var from = u1.Pins.First(p => p.Name == "VOUT");
        var to = c1.Pins.First(p => p.Name == "1");
        sch.Wires.Add(new SchWire { Ax = u1.X + from.DX, Ay = u1.Y + from.DY, Bx = c1.X + to.DX, By = c1.Y + to.DY });
        sch.Labels.Add(new SchLabel { X = u1.X + from.DX, Y = u1.Y + from.DY, Name = "3V3" });   // on the wire endpoint
        Assert.Equal("3V3", sch.ExtractNets().Single().Name);
    }

    [Fact]
    public void ErcCatchesDoubleDriversAndOpenPower()
    {
        var sch = new SchematicDocument();
        var u1 = SchematicDocument.FromLibrary(Reg(), "U1", 20, 20);
        var u2 = SchematicDocument.FromLibrary(Reg(), "U2", 60, 20);
        sch.Components.Add(u1); sch.Components.Add(u2);
        // tie the two regulator outputs together: conflict
        var o1 = u1.Pins.First(p => p.Name == "VOUT");
        var o2 = u2.Pins.First(p => p.Name == "VOUT");
        sch.Wires.Add(new SchWire { Ax = u1.X + o1.DX, Ay = u1.Y + o1.DY, Bx = u2.X + o2.DX, By = u2.Y + o2.DY });
        var issues = sch.RunErc();
        Assert.Contains(issues, i => i.Contains("driving pins"));
        Assert.Contains(issues, i => i.Contains("power input is unconnected"));
    }

    [Fact]
    public void SyncCreatesFootprintsNetsAndBindings()
    {
        var lib = new FootprintLibrary();
        var reg = Reg();
        reg.Pads.ForEach(p => { p.WMm = 0.5; p.HMm = 0.5; });
        lib.Items.Add(reg);
        var cap = Cap();
        cap.Pads.ForEach(p => { p.WMm = 0.5; p.HMm = 0.5; });
        lib.Items.Add(cap);

        var sch = new SchematicDocument();
        var u1 = SchematicDocument.FromLibrary(reg, "U1", 20, 20);
        var c1 = SchematicDocument.FromLibrary(cap, "C1", 50, 20);
        sch.Components.Add(u1); sch.Components.Add(c1);
        var from = u1.Pins.First(p => p.Name == "VOUT");
        var to = c1.Pins.First(p => p.Name == "1");
        sch.Wires.Add(new SchWire { Ax = u1.X + from.DX, Ay = u1.Y + from.DY, Bx = c1.X + to.DX, By = c1.Y + to.DY });
        sch.Labels.Add(new SchLabel { X = u1.X + from.DX, Y = u1.Y + from.DY, Name = "3V3" });

        using var board = new BoardDocument();
        board.SetBoardSize(50, 40);
        var rep = sch.SyncToBoard(board, lib);

        Assert.Contains("U1", rep.Added);
        Assert.Contains("C1", rep.Added);
        Assert.Contains(board.Nets, n => n.Name == "3V3");
        int netId = board.Nets.First(n => n.Name == "3V3").Id;
        var fpU1 = board.Footprints.First(f => f.RefDes == "U1");
        var fpC1 = board.Footprints.First(f => f.RefDes == "C1");
        Assert.Equal(netId, fpU1.Pads.First(p => p.Name == "VOUT").NetId);
        Assert.Equal(netId, fpC1.Pads.First(p => p.Name == "1").NetId);
    }

    [Fact]
    public void SyncIsIdempotent()
    {
        var lib = new FootprintLibrary();
        lib.Items.Add(Reg());
        var sch = new SchematicDocument();
        sch.Components.Add(SchematicDocument.FromLibrary(lib.Items[0], "U1", 20, 20));
        using var board = new BoardDocument();
        sch.SyncToBoard(board, lib);
        var second = sch.SyncToBoard(board, lib);
        Assert.Empty(second.Added);
        Assert.Single(board.Footprints);
    }

    [Fact]
    public void SerializationRoundTripsPlacement()
    {
        var lib = new FootprintLibrary();
        lib.Items.Add(Reg());
        var sch = new SchematicDocument();
        sch.Components.Add(SchematicDocument.FromLibrary(lib.Items[0], "U1", 31.25, 20));
        sch.Labels.Add(new SchLabel { X = 5, Y = 5, Name = "3V3" });
        var copy = SchematicDocument.Deserialize(sch.Serialize(), lib);
        Assert.Single(copy.Components);
        Assert.Equal("U1", copy.Components[0].RefDes);
        Assert.Equal(31.25, copy.Components[0].X, 6);
        Assert.Equal(3, copy.Components[0].Pins.Count);   // pins rebuilt from the library
        Assert.Single(copy.Labels);
    }
}
