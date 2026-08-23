using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DesignStudio.Model;

// JSON project format (.dsproj). Human-readable, git-friendly, versioned.
// v2: N-layer stackup, stable net ids, net classes, vias, pour flags.
// v1 files (string net list, 2 layers) are migrated on load.

public class ProjectFile
{
    [JsonPropertyName("version")] public int Version { get; set; } = 2;
    [JsonPropertyName("board_width_mm")] public double BoardWidthMm { get; set; }
    [JsonPropertyName("board_height_mm")] public double BoardHeightMm { get; set; }
    [JsonPropertyName("grid_mm")] public double GridMm { get; set; }
    [JsonPropertyName("copper_layers")] public int CopperLayers { get; set; } = 2;
    [JsonPropertyName("dielectric_er")] public double DielectricEr { get; set; } = 4.4;
    [JsonPropertyName("dielectric_h_mm")] public double DielectricHeightMm { get; set; } = 0.2;
    [JsonPropertyName("loss_tangent")] public double DielectricLossTangent { get; set; } = 0.02;
    [JsonPropertyName("copper_t_mm")] public double CopperThicknessMm { get; set; } = 0.035;
    // v1 legacy: net names by index
    [JsonPropertyName("nets")] public List<string>? NetsV1 { get; set; }
    // v2
    [JsonPropertyName("net_table")] public List<ProjNet>? NetTable { get; set; }
    [JsonPropertyName("net_classes")] public List<ProjNetClass>? NetClasses { get; set; }
    [JsonPropertyName("footprints")] public List<ProjFootprint> Footprints { get; set; } = new();
    [JsonPropertyName("traces")] public List<ProjTrace> Traces { get; set; } = new();
    [JsonPropertyName("vias")] public List<ProjVia>? Vias { get; set; }
    // v3: per-layer stackup and timing match groups
    [JsonPropertyName("stackup")] public List<StackupLayer>? Stackup { get; set; }
    [JsonPropertyName("match_groups")] public List<MatchGroup>? MatchGroups { get; set; }
    [JsonPropertyName("planes")] public List<PlaneShape>? Planes { get; set; }
    [JsonPropertyName("class_pair_rules")] public List<ClassPairRule>? ClassPairRules { get; set; }
    [JsonPropertyName("net_rules")] public List<NetRule>? NetRules { get; set; }
    [JsonPropertyName("channel_blocks")] public List<ChannelBlock>? ChannelBlocks { get; set; }
    // custom board outline (null/0 = plain rectangle)
    [JsonPropertyName("outline_shape")] public int OutlineShape { get; set; }
    [JsonPropertyName("outline_polygon")] public List<double[]>? OutlinePolygon { get; set; }
}

public class ProjNet
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("class")] public int ClassId { get; set; }
}

public class ProjNetClass
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("clearance_mm")] public double ClearanceMm { get; set; } = 0.2;
    [JsonPropertyName("trace_width_mm")] public double TraceWidthMm { get; set; } = 0.25;
    [JsonPropertyName("via_diameter_mm")] public double ViaDiameterMm { get; set; } = 0.6;
    [JsonPropertyName("via_drill_mm")] public double ViaDrillMm { get; set; } = 0.3;
    [JsonPropertyName("diff_pair_gap_mm")] public double DiffPairGapMm { get; set; }
    [JsonPropertyName("max_skew_mm")] public double MaxSkewMm { get; set; }
    [JsonPropertyName("microvia")] public bool AllowMicrovia { get; set; }
    [JsonPropertyName("z0_ohm")] public double TargetImpedanceOhm { get; set; }
    [JsonPropertyName("zdiff_ohm")] public double TargetDiffImpedanceOhm { get; set; }
}

public class ProjFootprint
{
    [JsonPropertyName("ref")] public string RefDes { get; set; } = "";
    [JsonPropertyName("lib")] public string LibName { get; set; } = "";
    [JsonPropertyName("x_mm")] public double X { get; set; }
    [JsonPropertyName("y_mm")] public double Y { get; set; }
    [JsonPropertyName("rot_deg")] public double RotationDeg { get; set; }
    [JsonPropertyName("side")] public int Side { get; set; }
    [JsonPropertyName("h3d_mm")] public double Height3DMm { get; set; }
    [JsonPropertyName("pads")] public List<ProjPad> Pads { get; set; } = new();
}

public class ProjPad
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("x_mm")] public double X { get; set; }
    [JsonPropertyName("y_mm")] public double Y { get; set; }
    [JsonPropertyName("w_mm")] public double W { get; set; }
    [JsonPropertyName("h_mm")] public double H { get; set; }
    [JsonPropertyName("net")] public int NetId { get; set; } = -1;
    [JsonPropertyName("th")] public bool ThroughHole { get; set; }
    [JsonPropertyName("drill_mm")] public double DrillMm { get; set; }
    [JsonPropertyName("pkg_delay_mm")] public double PackageDelayMm { get; set; }
}

public class ProjTrace
{
    [JsonPropertyName("ax_mm")] public double Ax { get; set; }
    [JsonPropertyName("ay_mm")] public double Ay { get; set; }
    [JsonPropertyName("bx_mm")] public double Bx { get; set; }
    [JsonPropertyName("by_mm")] public double By { get; set; }
    [JsonPropertyName("w_mm")] public double Width { get; set; }
    [JsonPropertyName("net")] public int NetId { get; set; } = -1;
    [JsonPropertyName("layer")] public int Layer { get; set; }
    [JsonPropertyName("pour")] public bool IsPour { get; set; }
}

public class ProjVia
{
    [JsonPropertyName("x_mm")] public double X { get; set; }
    [JsonPropertyName("y_mm")] public double Y { get; set; }
    [JsonPropertyName("dia_mm")] public double DiameterMm { get; set; } = 0.6;
    [JsonPropertyName("drill_mm")] public double DrillMm { get; set; } = 0.3;
    [JsonPropertyName("antipad_mm")] public double AntipadMm { get; set; } = 0;
    [JsonPropertyName("net")] public int NetId { get; set; } = -1;
    [JsonPropertyName("from")] public int FromLayer { get; set; }
    [JsonPropertyName("to")] public int ToLayer { get; set; } = 1;
    [JsonPropertyName("backdrill_to")] public int BackdrillToLayer { get; set; } = -1;
    [JsonPropertyName("via_type")] public ViaType Type { get; set; } = ViaType.Through;
}

public static class ProjectIO
{
    public const string Extension = ".dsproj";
    public const string Filter = "Design Studio project (*.dsproj)|*.dsproj|All files (*.*)|*.*";

    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = true };

    public static string Serialize(BoardDocument doc)
    {
        var pf = new ProjectFile
        {
            BoardWidthMm = doc.BoardWidthMm,
            BoardHeightMm = doc.BoardHeightMm,
            GridMm = doc.GridMm,
            CopperLayers = doc.CopperLayers,
            DielectricEr = doc.DielectricEr,
            DielectricHeightMm = doc.DielectricHeightMm,
            DielectricLossTangent = doc.DielectricLossTangent,
            CopperThicknessMm = doc.CopperThicknessMm,
            OutlineShape = (int)doc.Shape,
            OutlinePolygon = doc.OutlinePolygon,
            NetTable = doc.Nets.Select(n => new ProjNet { Id = n.Id, Name = n.Name, ClassId = n.ClassId }).ToList(),
            NetClasses = doc.NetClasses.Select(c => new ProjNetClass
            {
                Id = c.Id, Name = c.Name, ClearanceMm = c.ClearanceMm, TraceWidthMm = c.TraceWidthMm,
                ViaDiameterMm = c.ViaDiameterMm, ViaDrillMm = c.ViaDrillMm,
                DiffPairGapMm = c.DiffPairGapMm, MaxSkewMm = c.MaxSkewMm, AllowMicrovia = c.AllowMicrovia,
                TargetImpedanceOhm = c.TargetImpedanceOhm, TargetDiffImpedanceOhm = c.TargetDiffImpedanceOhm
            }).ToList(),
            Stackup = doc.Stackup.Count > 0 ? doc.Stackup.ToList() : null,
            MatchGroups = doc.MatchGroups.Count > 0 ? doc.MatchGroups.ToList() : null,
            Planes = doc.Planes.Count > 0 ? doc.Planes.ToList() : null,
            Footprints = doc.Footprints.Select(fp => new ProjFootprint
            {
                RefDes = fp.RefDes, LibName = fp.LibName, X = fp.X, Y = fp.Y,
                RotationDeg = fp.RotationDeg, Side = fp.Side, Height3DMm = fp.Height3DMm,
                Pads = fp.Pads.Select(p => new ProjPad
                {
                    Name = p.Name, X = p.X, Y = p.Y, W = p.W, H = p.H,
                    NetId = p.NetId, ThroughHole = p.ThroughHole, DrillMm = p.DrillMm,
                    PackageDelayMm = p.PackageDelayMm
                }).ToList()
            }).ToList(),
            Traces = doc.Traces.Select(t => new ProjTrace
            {
                Ax = t.Ax, Ay = t.Ay, Bx = t.Bx, By = t.By,
                Width = t.Width, NetId = t.NetId, Layer = t.Layer, IsPour = t.IsPour
            }).ToList(),
            Vias = doc.Vias.Select(v => new ProjVia
            {
                X = v.X, Y = v.Y, DiameterMm = v.DiameterMm, DrillMm = v.DrillMm,
                AntipadMm = v.AntipadMm,
                NetId = v.NetId, FromLayer = v.FromLayer, ToLayer = v.ToLayer,
                BackdrillToLayer = v.BackdrillToLayer, Type = v.Type
            }).ToList(),
            ClassPairRules = doc.ClassPairRules.Count > 0 ? doc.ClassPairRules.ToList() : null,
            NetRules = doc.NetRules.Count > 0 ? doc.NetRules.ToList() : null,
            ChannelBlocks = doc.ChannelBlocks.Count > 0 ? doc.ChannelBlocks.ToList() : null
        };
        return JsonSerializer.Serialize(pf, JsonOpts);
    }

    public static void Save(BoardDocument doc, string path, bool setAsProjectPath = true)
    {
        // Atomic-ish save: write temp then replace, so a crash never corrupts the project.
        string tmp = path + ".tmp";
        File.WriteAllText(tmp, Serialize(doc));
        File.Move(tmp, path, overwrite: true);
        if (setAsProjectPath)
        {
            doc.FilePath = path;
            doc.MarkClean();
        }
    }

    public static BoardDocument Deserialize(string json, string? path = null)
    {
        var pf = JsonSerializer.Deserialize<ProjectFile>(json)
                 ?? throw new InvalidDataException("Not a valid Design Studio project file.");

        var doc = new BoardDocument
        {
            BoardWidthMm = pf.BoardWidthMm > 0 ? pf.BoardWidthMm : 100,
            BoardHeightMm = pf.BoardHeightMm > 0 ? pf.BoardHeightMm : 80,
            GridMm = pf.GridMm > 0 ? pf.GridMm : 1.27,
            FilePath = path
        };
        doc.SetCopperLayers(pf.CopperLayers >= 2 ? pf.CopperLayers : 2);
        if (pf.DielectricEr >= 1) doc.DielectricEr = pf.DielectricEr;
        if (pf.DielectricHeightMm > 0) doc.DielectricHeightMm = pf.DielectricHeightMm;
        if (pf.DielectricLossTangent >= 0) doc.DielectricLossTangent = pf.DielectricLossTangent;
        if (pf.CopperThicknessMm > 0) doc.CopperThicknessMm = pf.CopperThicknessMm;
        if (pf.OutlinePolygon is { Count: >= 3 })
            doc.SetBoardOutline((BoardShape)pf.OutlineShape, pf.OutlinePolygon);

        if (pf.NetClasses is { Count: > 0 })
        {
            doc.NetClasses.Clear();
            foreach (var c in pf.NetClasses)
                doc.NetClasses.Add(new NetClassInfo
                {
                    Id = c.Id, Name = c.Name, ClearanceMm = c.ClearanceMm, TraceWidthMm = c.TraceWidthMm,
                    ViaDiameterMm = c.ViaDiameterMm, ViaDrillMm = c.ViaDrillMm,
                    DiffPairGapMm = c.DiffPairGapMm, MaxSkewMm = c.MaxSkewMm, AllowMicrovia = c.AllowMicrovia,
                    TargetImpedanceOhm = c.TargetImpedanceOhm, TargetDiffImpedanceOhm = c.TargetDiffImpedanceOhm
                });
            if (doc.NetClasses.All(c => c.Id != 0))
                doc.NetClasses.Insert(0, new NetClassInfo { Id = 0 });
        }

        if (pf.NetTable is { Count: > 0 })
        {
            foreach (var n in pf.NetTable)
                doc.RestoreNet(new NetInfo { Id = n.Id, Name = n.Name, ClassId = n.ClassId });
        }
        else if (pf.NetsV1 is { Count: > 0 })
        {
            // v1 migration: index == id
            for (int i = 0; i < pf.NetsV1.Count; i++)
                doc.RestoreNet(new NetInfo { Id = i, Name = pf.NetsV1[i], ClassId = 0 });
        }

        foreach (var f in pf.Footprints)
        {
            doc.Footprints.Add(new FootprintItem
            {
                RefDes = f.RefDes, LibName = f.LibName, X = f.X, Y = f.Y,
                RotationDeg = f.RotationDeg, Side = f.Side, Height3DMm = f.Height3DMm,
                Pads = f.Pads.Select(p => new PadItem
                {
                    Name = p.Name, X = p.X, Y = p.Y, W = p.W, H = p.H,
                    NetId = p.NetId, ThroughHole = p.ThroughHole, DrillMm = p.DrillMm,
                    PackageDelayMm = p.PackageDelayMm
                }).ToList()
            });
        }
        foreach (var t in pf.Traces)
        {
            doc.Traces.Add(new TraceItem
            {
                Ax = t.Ax, Ay = t.Ay, Bx = t.Bx, By = t.By,
                Width = t.Width, NetId = t.NetId, Layer = t.Layer, IsPour = t.IsPour
            });
        }
        if (pf.Vias != null)
            foreach (var v in pf.Vias)
                doc.Vias.Add(new ViaItem
                {
                    X = v.X, Y = v.Y, DiameterMm = v.DiameterMm, DrillMm = v.DrillMm,
                    AntipadMm = v.AntipadMm,
                    NetId = v.NetId, FromLayer = v.FromLayer, ToLayer = v.ToLayer,
                    BackdrillToLayer = v.BackdrillToLayer, Type = v.Type
                });

        if (pf.Stackup is { Count: > 0 })
        {
            doc.Stackup.Clear();
            doc.Stackup.AddRange(pf.Stackup.Take(doc.CopperLayers));
            doc.EnsureStackup();
        }
        if (pf.MatchGroups is { Count: > 0 })
            doc.MatchGroups.AddRange(pf.MatchGroups);
        if (pf.Planes is { Count: > 0 })
            doc.Planes.AddRange(pf.Planes);   // fills regenerate lazily on first draw
        if (pf.ClassPairRules is { Count: > 0 }) doc.ClassPairRules.AddRange(pf.ClassPairRules);
        if (pf.NetRules is { Count: > 0 }) doc.NetRules.AddRange(pf.NetRules);
        if (pf.ChannelBlocks is { Count: > 0 }) doc.ChannelBlocks.AddRange(pf.ChannelBlocks);

        doc.NotifyChanged();
        doc.MarkClean();
        return doc;
    }

    public static BoardDocument Load(string path) => Deserialize(File.ReadAllText(path), path);

    public static string AutosavePath(string projectPath) => projectPath + ".autosave";
}
