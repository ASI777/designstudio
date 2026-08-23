using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DesignStudio.Model;

// ============================================================================
// 6.1 Schematic capture (foundation).
//
// A schematic is components (symbols derived from library footprints — the
// datasheet JSON already carries pin names/types), wires, and net labels.
// Connectivity is extracted by union-find over coincident endpoints; labels
// name the nets; ERC checks pin-type conflicts before anything reaches
// copper. Forward annotation pushes the result to the board: nets created
// by name, footprints placed for new components, pad↔net binding updated —
// and reports exactly what it changed (the netlist diff).
// ============================================================================

public class SchComponent
{
    [JsonPropertyName("ref")] public string RefDes { get; set; } = "";
    [JsonPropertyName("lib")] public string LibName { get; set; } = "";
    [JsonPropertyName("x")] public double X { get; set; }
    [JsonPropertyName("y")] public double Y { get; set; }
    /// <summary>Pin positions are generated: pins spread on the left/right symbol edges.</summary>
    [JsonIgnore] public List<SchPin> Pins { get; } = new();
    public double Width => 12;
    public double Height => Math.Max(8, (int)Math.Ceiling(Pins.Count / 2.0) * 2.5 + 2);
}

public class SchPin
{
    public string Name = "", Number = "", ElectricalType = "passive";
    public double DX, DY;                 // offset from component origin
}

public class SchWire
{
    [JsonPropertyName("ax")] public double Ax { get; set; }
    [JsonPropertyName("ay")] public double Ay { get; set; }
    [JsonPropertyName("bx")] public double Bx { get; set; }
    [JsonPropertyName("by")] public double By { get; set; }
}

public class SchLabel
{
    [JsonPropertyName("x")] public double X { get; set; }
    [JsonPropertyName("y")] public double Y { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
}

public class SchematicDocument
{
    [JsonPropertyName("components")] public List<SchComponent> Components { get; set; } = new();
    [JsonPropertyName("wires")] public List<SchWire> Wires { get; set; } = new();
    [JsonPropertyName("labels")] public List<SchLabel> Labels { get; set; } = new();

    public event Action? Changed;
    public void NotifyChanged() => Changed?.Invoke();

    /// <summary>Build pin geometry for a component from its library definition.</summary>
    public static SchComponent FromLibrary(FootprintDef def, string refDes, double x, double y)
    {
        var c = new SchComponent { RefDes = refDes, LibName = def.Name, X = x, Y = y };
        var pads = def.Pads.Where(p => !string.IsNullOrEmpty(p.Name)).ToList();
        int half = (pads.Count + 1) / 2;
        for (int i = 0; i < pads.Count; i++)
        {
            bool left = i < half;
            int row = left ? i : i - half;
            c.Pins.Add(new SchPin
            {
                Name = pads[i].Name, Number = pads[i].Name,
                ElectricalType = string.IsNullOrEmpty(pads[i].ElectricalType) ? "passive" : pads[i].ElectricalType,
                DX = left ? -c.Width / 2 : c.Width / 2,
                DY = -((half - 1) * 2.5) / 2 + row * 2.5
            });
        }
        return c;
    }

    // ---- connectivity: union-find over snapped endpoints ----

    public record ExtractedNet(string Name, List<(SchComponent c, SchPin p)> Pins);

    public List<ExtractedNet> ExtractNets()
    {
        var parent = new Dictionary<(long, long), (long, long)>();
        (long, long) Key(double x, double y) => ((long)Math.Round(x * 20), (long)Math.Round(y * 20));
        (long, long) Find((long, long) k)
        {
            if (!parent.TryGetValue(k, out var p)) { parent[k] = k; return k; }
            if (p == k) return k;
            var r = Find(p); parent[k] = r; return r;
        }
        void Union((long, long) a, (long, long) b)
        {
            var ra = Find(a); var rb = Find(b);
            if (ra != rb) parent[ra] = rb;
        }

        foreach (var w in Wires) Union(Key(w.Ax, w.Ay), Key(w.Bx, w.By));
        // wire endpoints touching the middle of other wires (T junctions)
        foreach (var w in Wires)
            foreach (var v in Wires)
            {
                if (ReferenceEquals(w, v)) continue;
                foreach (var (ex, ey) in new[] { (w.Ax, w.Ay), (w.Bx, w.By) })
                    if (OnSegment(ex, ey, v)) Union(Key(ex, ey), Key(v.Ax, v.Ay));
            }

        var pinAt = new Dictionary<(long, long), List<(SchComponent, SchPin)>>();
        foreach (var c in Components)
            foreach (var p in c.Pins)
            {
                var k = Key(c.X + p.DX, c.Y + p.DY);
                Find(k);
                if (!pinAt.TryGetValue(k, out var l)) pinAt[k] = l = new();
                l.Add((c, p));
            }

        var labelAt = new Dictionary<(long, long), string>();
        foreach (var l in Labels) labelAt[Key(l.X, l.Y)] = l.Name;
        // labels sitting on a wire body bind to that wire's group
        foreach (var l in Labels)
            foreach (var v in Wires)
                if (OnSegment(l.X, l.Y, v)) Union(Key(l.X, l.Y), Key(v.Ax, v.Ay));

        var groups = new Dictionary<(long, long), ExtractedNet>();
        int anon = 1;
        foreach (var (k, pins) in pinAt)
        {
            var root = Find(k);
            if (!groups.TryGetValue(root, out var net))
                groups[root] = net = new ExtractedNet($"N${anon++}", new());
            net.Pins.AddRange(pins);
        }
        // apply label names
        foreach (var (k, name) in labelAt)
        {
            var root = Find(k);
            if (groups.TryGetValue(root, out var net))
                groups[root] = net with { Name = name };
        }
        // drop single-pin groups — nothing to connect
        return groups.Values.Where(g => g.Pins.Count >= 2).ToList();
    }

    private static bool OnSegment(double px, double py, SchWire w, double tol = 0.05)
    {
        double dx = w.Bx - w.Ax, dy = w.By - w.Ay, l2 = dx * dx + dy * dy;
        if (l2 < 1e-12) return Math.Abs(px - w.Ax) < tol && Math.Abs(py - w.Ay) < tol;
        double u = ((px - w.Ax) * dx + (py - w.Ay) * dy) / l2;
        if (u < -0.01 || u > 1.01) return false;
        double qx = w.Ax + u * dx - px, qy = w.Ay + u * dy - py;
        return qx * qx + qy * qy < tol * tol;
    }

    // ---- ERC at the schematic level ----

    public List<string> RunErc()
    {
        var issues = new List<string>();
        var nets = ExtractNets();
        foreach (var net in nets)
        {
            var outputs = net.Pins.Where(p => p.p.ElectricalType is "output" or "power_out").ToList();
            if (outputs.Count > 1)
                issues.Add($"net {net.Name}: {outputs.Count} driving pins " +
                           $"({string.Join(", ", outputs.Select(o => $"{o.c.RefDes}.{o.p.Name}"))}) — conflict.");
            if (net.Pins.Any(p => p.p.ElectricalType == "nc"))
                issues.Add($"net {net.Name}: a no-connect pin is wired " +
                           $"({string.Join(", ", net.Pins.Where(p => p.p.ElectricalType == "nc").Select(o => $"{o.c.RefDes}.{o.p.Name}"))}).");
        }
        var connected = nets.SelectMany(n => n.Pins.Select(p => (p.c, p.p))).ToHashSet();
        foreach (var c in Components)
            foreach (var p in c.Pins.Where(p => p.ElectricalType == "power_in"))
                if (!connected.Contains((c, p)))
                    issues.Add($"{c.RefDes}.{p.Name}: power input is unconnected.");
        return issues;
    }

    // ---- forward annotation: schematic → board ----

    public record SyncReport(List<string> Added, List<string> Updated, List<string> Warnings);

    public SyncReport SyncToBoard(BoardDocument board, FootprintLibrary library)
    {
        var added = new List<string>();
        var updated = new List<string>();
        var warnings = new List<string>();
        var nets = ExtractNets();

        // nets by name
        var netId = new Dictionary<string, int>();
        foreach (var n in nets) netId[n.Name] = board.AddNet(n.Name);

        // components: place missing footprints in a staging row below the board
        double stageX = 5, stageY = board.BoardHeightMm + 10;
        foreach (var sc in Components)
        {
            var fp = board.Footprints.FirstOrDefault(f => f.RefDes == sc.RefDes);
            if (fp is null)
            {
                var def = library.Items.FirstOrDefault(d => d.Name == sc.LibName);
                if (def is null) { warnings.Add($"{sc.RefDes}: footprint '{sc.LibName}' not in the library."); continue; }
                fp = board.AddFootprint(sc.RefDes, def.Name, stageX, stageY, def.ToPadItems(),
                                        0, 0, def.HeightMm);
                stageX += Math.Max(def.BodyWidthMm, 4) + 4;
                added.Add(sc.RefDes);
            }

            // pad ↔ net binding from extracted connectivity
            foreach (var net in nets)
                foreach (var (c, p) in net.Pins.Where(np => np.c == sc))
                {
                    var pad = fp.Pads.FirstOrDefault(pd => pd.Name == p.Number);
                    if (pad is null) { warnings.Add($"{sc.RefDes}: pad '{p.Number}' missing on the footprint."); continue; }
                    if (pad.NetId != netId[net.Name])
                    {
                        pad.NetId = netId[net.Name];
                        if (!added.Contains(sc.RefDes)) updated.Add($"{sc.RefDes}.{p.Number} → {net.Name}");
                    }
                }
        }
        board.NotifyChanged();
        return new SyncReport(added, updated, warnings);
    }

    // ---- persistence ----

    public string Serialize()
    {
        // pins are regenerated from the library on load; only placement persists
        return JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
    }

    public static SchematicDocument Deserialize(string json, FootprintLibrary library)
    {
        var doc = JsonSerializer.Deserialize<SchematicDocument>(json) ?? new SchematicDocument();
        foreach (var c in doc.Components)
        {
            var def = library.Items.FirstOrDefault(d => d.Name == c.LibName);
            if (def is null) continue;
            var rebuilt = FromLibrary(def, c.RefDes, c.X, c.Y);
            c.Pins.AddRange(rebuilt.Pins);
        }
        return doc;
    }

    public static string PathFor(string projectPath) =>
        Path.ChangeExtension(projectPath, ".dsschem");
}
