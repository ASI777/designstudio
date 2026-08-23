using DesignStudio.Interop;

namespace DesignStudio.Model;

// UI-side board document — the single source of truth. The native core is
// rebuilt from this document on demand (before routing, DRC, pours, length
// queries), which makes model drift between the two sides impossible by
// construction. Every mutation is undoable.

public class PadItem
{
    public double X, Y, W, H;          // mm, relative to footprint origin
    public int NetId = -1;
    public string Name = "";
    public bool ThroughHole;
    public double DrillMm;
    /// <summary>input | output | bidirectional | power_in | power_out | passive | nc — drives ERC.</summary>
    public string ElectricalType = "passive";
    /// <summary>Routed length inside the package (BGA substrate), mm — from the datasheet JSON. Feeds the delay engine.</summary>
    public double PackageDelayMm;
}

public class FootprintItem
{
    public ulong NativeId;
    public string RefDes = "";
    public string LibName = "";
    public double X, Y;                // mm
    public double RotationDeg;
    public int Side;                   // copper layer of SMD pads (0 = top)
    public double Height3DMm;          // package height from the datasheet (0 = use heuristic)
    public List<PadItem> Pads = new();
    public bool Selected;

    public (double minX, double minY, double maxX, double maxY) Bounds()
        => BoundsAt(X, Y, RotationDeg);

    public (double minX, double minY, double maxX, double maxY) BoundsAt(
        double x, double y, double rotationDeg)
    {
        if (Pads.Count == 0) return (x - 1, y - 1, x + 1, y + 1);
        double r = rotationDeg * Math.PI / 180, c = Math.Cos(r), s = Math.Sin(r);
        double minX = double.MaxValue, minY = double.MaxValue, maxX = double.MinValue, maxY = double.MinValue;
        foreach (var p in Pads)
        {
            double px = x + p.X * c - p.Y * s, py = y + p.X * s + p.Y * c;
            // pad rectangle rotates with the part: rotated half-extents
            double hw = (Math.Abs(p.W * c) + Math.Abs(p.H * s)) / 2;
            double hh = (Math.Abs(p.W * s) + Math.Abs(p.H * c)) / 2;
            minX = Math.Min(minX, px - hw); maxX = Math.Max(maxX, px + hw);
            minY = Math.Min(minY, py - hh); maxY = Math.Max(maxY, py + hh);
        }
        return (minX, minY, maxX, maxY);
    }

    public (double x, double y) PadWorld(PadItem p)
    {
        double r = RotationDeg * Math.PI / 180, c = Math.Cos(r), s = Math.Sin(r);
        return (X + p.X * c - p.Y * s, Y + p.X * s + p.Y * c);
    }
}

public class TraceItem
{
    public ulong NativeId;
    public double Ax, Ay, Bx, By;      // mm
    public double Width = 0.25;
    public int NetId = -1;
    public int Layer;                  // copper layer index
    public bool IsPour;                // generated pour fill stroke
    public bool Selected;

    public double LengthMm => Math.Sqrt((Bx - Ax) * (Bx - Ax) + (By - Ay) * (By - Ay));
}

/// <summary>Fabrication class of a via. Through/Blind/Buried are mechanically
/// drilled; Microvia is laser-drilled (one build-up layer, small, capture pad).</summary>
public enum ViaType { Through, Blind, Buried, Microvia }

public class ViaItem
{
    public ulong NativeId;
    public double X, Y;                // mm
    public double DiameterMm = 0.6;
    public double DrillMm = 0.3;
    /// <summary>Fabrication class. Through is the default; set Microvia for laser
    /// build-up vias. Blind/Buried are otherwise inferred from the layer span.</summary>
    public ViaType Type = ViaType.Through;
    /// <summary>Plane antipad (clearance hole) diameter, mm. 0 = auto-derive from
    /// pad + net-class clearance. Sets the coaxial via impedance in the S2/S3 field model.</summary>
    public double AntipadMm = 0;
    public int NetId = -1;
    public int FromLayer, ToLayer = 1; // inclusive copper span
    /// <summary>Backdrill: barrel removed below this layer (-1 = none). Set by Backdrill.ApplyAll.</summary>
    public int BackdrillToLayer = -1;
    public bool Selected;
}

public class NetInfo
{
    public int Id = -1;
    public string Name = "";
    public int ClassId;
    public override string ToString() => Name;
}

public class NetClassInfo
{
    public int Id;
    public string Name { get; set; } = "Default";
    public double ClearanceMm { get; set; } = 0.2;
    public double TraceWidthMm { get; set; } = 0.25;
    public double ViaDiameterMm { get; set; } = 0.6;
    public double ViaDrillMm { get; set; } = 0.3;
    public double DiffPairGapMm { get; set; }
    public double MaxSkewMm { get; set; }
    public bool AllowMicrovia { get; set; }
    /// <summary>Single-ended impedance target, Ω (0 = uncontrolled). The solver derives width per layer.</summary>
    public double TargetImpedanceOhm { get; set; }
    /// <summary>Differential impedance target, Ω (0 = uncontrolled). The solver derives width + gap.</summary>
    public double TargetDiffImpedanceOhm { get; set; }
    public override string ToString() => Name;
}

public record DrcResultItem(int Rule, string RuleName, string Message, double X, double Y)
{
    public override string ToString() => $"[{RuleName}] {Message}  @ ({X:F2}, {Y:F2})";
}

public class BoardDocument : IDisposable
{
    public CoreBoard Core { get; private set; } = new();
    public List<FootprintItem> Footprints { get; } = new();
    public List<TraceItem> Traces { get; } = new();
    public List<ViaItem> Vias { get; } = new();
    public List<NetInfo> Nets { get; } = new();
    public List<NetClassInfo> NetClasses { get; } = new() { new NetClassInfo { Id = 0 } };
    /// <summary>Per-copper-layer electrical stackup; kept in sync with CopperLayers by EnsureStackup().</summary>
    public List<StackupLayer> Stackup { get; } = new();
    /// <summary>Timing-matched net groups (byte lanes, clock pairs) checked by DRC rule 13.</summary>
    public List<MatchGroup> MatchGroups { get; } = new();
    /// <summary>Polygon copper planes (3.0). Fill regenerates lazily via PlanesUpToDate().</summary>
    public List<PlaneShape> Planes { get; } = new();
    /// <summary>Rule tree (6.2): clearances between specific class pairs.</summary>
    public List<ClassPairRule> ClassPairRules { get; } = new();
    /// <summary>Rule tree (6.2): per-net width/clearance overrides.</summary>
    public List<NetRule> NetRules { get; } = new();
    /// <summary>Vendor S-parameter models (connectors, cables) attached to nets (S1/E8).</summary>
    public List<ChannelBlock> ChannelBlocks { get; } = new();

    private long _planesVersion = -1;

    /// <summary>Regenerates plane fills if the board changed since the last call. Cheap when clean.</summary>
    public void PlanesUpToDate()
    {
        if (_planesVersion == Version || Planes.Count == 0) return;
        PlaneGenerator.RegenerateAll(this);
        _planesVersion = Version;
    }

    public PlaneShape AddPlane(int netId, int layer, double clearanceMm)
    {
        var plane = new PlaneShape { NetId = netId, Layer = layer, ClearanceMm = clearanceMm };
        Planes.Add(plane);
        NotifyChanged();
        Undo.Push($"Plane {NetName(netId)} on {LayerName(layer)}",
            undo: () => { Planes.Remove(plane); NotifyChanged(); },
            redo: () => { Planes.Add(plane); NotifyChanged(); });
        return plane;
    }

    public void RemovePlane(PlaneShape plane)
    {
        if (!Planes.Remove(plane)) return;
        NotifyChanged();
        Undo.Push("Delete plane",
            undo: () => { Planes.Add(plane); NotifyChanged(); },
            redo: () => { Planes.Remove(plane); NotifyChanged(); });
    }

    public double BoardWidthMm = 100, BoardHeightMm = 80;
    /// <summary>Custom outline polygon (mm, CCW). Null = a plain BoardWidthMm x BoardHeightMm rectangle.</summary>
    public List<double[]>? OutlinePolygon;
    /// <summary>Outline shape kind (UI + round-trip; the geometry lives in OutlinePolygon).</summary>
    public BoardShape Shape = BoardShape.Rectangle;
    public double GridMm = 1.27;
    public int CopperLayers { get; private set; } = 2;

    // Stackup electrical properties (defaults: standard FR-4, 1 oz copper).
    // Used by the physics engine for impedance, delay, loss and current math.
    public double DielectricEr = 4.4;          // relative permittivity
    public double DielectricHeightMm = 0.2;    // trace-to-plane dielectric
    public double DielectricLossTangent = 0.02;
    public double CopperThicknessMm = 0.035;   // 1 oz

    public UndoStack Undo { get; } = new();
    public bool Dirty { get; private set; }
    public string? FilePath { get; set; }

    public event Action? Changed;
    /// <summary>Monotonic edit counter — cheap cache invalidation key (ratsnest, 3D).</summary>
    public long Version { get; private set; }

    private bool _nativeStale = true;
    private int _nextNetId;

    public void NotifyChanged() { Dirty = true; Version++; _nativeStale = true; Changed?.Invoke(); }
    public void MarkClean() { Dirty = false; }

    private static long Nm(double mm) => (long)Math.Round(mm * NativeCore.NmPerMm);
    private static double Mm(long nm) => nm / (double)NativeCore.NmPerMm;

    // ---- layers ----

    /// <summary>
    /// Grows/shrinks the per-layer stackup to match CopperLayers, preserving
    /// edits. New boards get a sensible default: on 4+ layers, L2 is a GND
    /// plane and L(n-1) a PWR plane — the classic SI-friendly arrangement.
    /// </summary>
    public void EnsureStackup()
    {
        while (Stackup.Count > CopperLayers) Stackup.RemoveAt(Stackup.Count - 1);
        bool wasEmpty = Stackup.Count == 0;
        while (Stackup.Count < CopperLayers)
            Stackup.Add(new StackupLayer
            {
                CopperThicknessMm = CopperThicknessMm,
                DielectricHeightMm = DielectricHeightMm,
                DielectricEr = DielectricEr,
                LossTangent = DielectricLossTangent
            });
        if (wasEmpty && CopperLayers >= 4 && CopperLayers <= 16)
        {
            Stackup[1].Role = LayerRole.Plane;                    // L2 = GND
            Stackup[CopperLayers - 2].Role = LayerRole.Plane;     // L(n-1) = PWR
        }
        else if (wasEmpty && CopperLayers > 16)
        {
            // High-layer-count boards (server / AI / backplane class) need a
            // reference plane adjacent to every signal layer, not just two
            // planes. Place planes on a 3-layer cadence with signal outer
            // layers, so no signal is more than one layer from a plane, and
            // make the planes 2 oz (thick power/ground copper).
            foreach (int i in PlaneCadence(CopperLayers))
            {
                Stackup[i].Role = LayerRole.Plane;
                Stackup[i].CopperWeightOz = 2.0;
                Stackup[i].CopperThicknessMm = 0.070;             // 2 oz
            }
        }
    }

    /// <summary>Plane-layer indices for a thick stackup: every signal layer ends
    /// up adjacent to a plane (≤2 signal layers between consecutive planes).</summary>
    public static IEnumerable<int> PlaneCadence(int layers)
    {
        var planes = new SortedSet<int>();
        for (int i = 1; i <= layers - 2; i++)
            if ((i - 1) % 3 == 0) planes.Add(i);                  // 1, 4, 7, …
        planes.Add(layers - 2);                                   // guarantee a bottom reference
        return planes;
    }

    public void SetCopperLayers(int count)
    {
        CopperLayers = Math.Clamp(count, 2, 64);
        EnsureStackup();
        foreach (var t in Traces) t.Layer = Math.Min(t.Layer, CopperLayers - 1);
        foreach (var v in Vias)
        {
            v.FromLayer = Math.Min(v.FromLayer, CopperLayers - 1);
            v.ToLayer = Math.Min(v.ToLayer, CopperLayers - 1);
            if (v.FromLayer == v.ToLayer) { v.FromLayer = 0; v.ToLayer = CopperLayers - 1; }
        }
        NotifyChanged();
    }

    public string LayerName(int layer) =>
        layer == 0 ? "L1 (Top)" :
        layer == CopperLayers - 1 ? $"L{CopperLayers} (Bottom)" :
        $"L{layer + 1} (Inner)";

    public void SetBoardSize(double wMm, double hMm)
    {
        BoardWidthMm = wMm; BoardHeightMm = hMm;
        Shape = BoardShape.Rectangle; OutlinePolygon = null;
        NotifyChanged();
    }

    /// <summary>The board outline as a polygon — the custom OutlinePolygon when set,
    /// otherwise the rectangle of BoardWidthMm x BoardHeightMm.</summary>
    public IReadOnlyList<double[]> EffectiveOutline()
        => OutlinePolygon is { Count: >= 3 } ? OutlinePolygon
                                             : BoardOutline.Rectangle(BoardWidthMm, BoardHeightMm);

    /// <summary>Set a custom board outline; BoardWidthMm/HeightMm follow its bounding box
    /// so the router keep-out and view scaling stay correct.</summary>
    public void SetBoardOutline(BoardShape shape, IReadOnlyList<double[]> polygon)
    {
        Shape = shape;
        OutlinePolygon = (shape == BoardShape.Rectangle || polygon is not { Count: >= 3 })
            ? null
            : polygon.Select(pt => new[] { pt[0], pt[1] }).ToList();
        var (_, _, w, h) = BoardOutline.BoundingBox(EffectiveOutline());
        if (w > 0 && h > 0) { BoardWidthMm = Math.Round(w, 4); BoardHeightMm = Math.Round(h, 4); }
        NotifyChanged();
    }

    /// <summary>Convenience: set a standard shape from primary dimensions
    /// (rectangle: w,h; square: w; circle: diameter; rounded: w,h,radius).</summary>
    public void SetBoardShape(BoardShape shape, double primaryMm, double secondaryMm = 0, double radiusMm = 0)
    {
        switch (shape)
        {
            case BoardShape.Circle:
                SetBoardOutline(shape, BoardOutline.Circle(primaryMm)); break;
            case BoardShape.RoundedRectangle:
                SetBoardOutline(shape, BoardOutline.RoundedRectangle(primaryMm, secondaryMm, radiusMm)); break;
            default:
                SetBoardSize(primaryMm, secondaryMm > 0 ? secondaryMm : primaryMm); break;
        }
    }

    // ---- nets & classes ----

    public NetInfo? FindNet(int id) => Nets.FirstOrDefault(n => n.Id == id);
    public string NetName(int id) => FindNet(id)?.Name ?? "—";

    public int AddNet(string name, int classId = 0)
    {
        var existing = Nets.FirstOrDefault(n => n.Name == name);
        if (existing != null) return existing.Id;
        if (_nextNetId == 0 && Nets.Count > 0) _nextNetId = Nets.Max(n => n.Id) + 1;
        var net = new NetInfo { Id = _nextNetId++, Name = name, ClassId = classId };
        Nets.Add(net);
        NotifyChanged();
        return net.Id;
    }

    public void RemoveNet(int id)
    {
        if (Nets.RemoveAll(n => n.Id == id) == 0) return;
        foreach (var fp in Footprints)
            foreach (var p in fp.Pads)
                if (p.NetId == id) p.NetId = -1;
        foreach (var t in Traces) if (t.NetId == id) t.NetId = -1;
        foreach (var v in Vias) if (v.NetId == id) v.NetId = -1;
        NotifyChanged();
    }

    /// <summary>Restores a net with a specific id (project load, undo).</summary>
    public void RestoreNet(NetInfo net)
    {
        Nets.RemoveAll(n => n.Id == net.Id);
        Nets.Add(net);
        _nextNetId = Math.Max(_nextNetId, net.Id + 1);
        NotifyChanged();
    }

    public NetClassInfo ClassFor(int netId)
    {
        var net = FindNet(netId);
        var cls = net is null ? null : NetClasses.FirstOrDefault(c => c.Id == net.ClassId);
        return cls ?? NetClasses[0];
    }

    public NetClassInfo AddNetClass(string name)
    {
        int id = NetClasses.Max(c => c.Id) + 1;
        var nc = new NetClassInfo { Id = id, Name = name };
        NetClasses.Add(nc);
        NotifyChanged();
        return nc;
    }

    // ---- native sync: rebuild the C++ board from this document on demand ----

    /// <summary>
    /// Rebuilds the native board if any mutation happened since the last sync.
    /// All native operations (route, DRC, pour, length) call this first, so the
    /// engine always sees exactly what is on screen.
    /// </summary>
    public void SyncNative()
    {
        if (!_nativeStale) return;
        var h = Core.Handle;
        NativeCore.dc_board_clear(h);
        NativeCore.dc_board_set_outline(h, 0, 0, Nm(BoardWidthMm), Nm(BoardHeightMm));
        NativeCore.dc_board_set_copper_layers(h, CopperLayers);
        foreach (var c in NetClasses)
            NativeCore.dc_class_set(h, c.Id, c.Name, Nm(c.ClearanceMm), Nm(c.TraceWidthMm),
                Nm(c.ViaDiameterMm), Nm(c.ViaDrillMm), Nm(c.DiffPairGapMm), Nm(c.MaxSkewMm),
                c.AllowMicrovia ? 1 : 0);
        foreach (var n in Nets)
            NativeCore.dc_net_set(h, n.Id, n.Name, n.ClassId);
        foreach (var fp in Footprints)
        {
            var padDefs = fp.Pads.Select(p => new NativeCore.DcPadDef
            {
                X = Nm(p.X), Y = Nm(p.Y), W = Nm(p.W), H = Nm(p.H),
                NetId = p.NetId, ThroughHole = p.ThroughHole ? 1 : 0, Drill = Nm(p.DrillMm),
                Name = p.Name
            }).ToArray();
            fp.NativeId = NativeCore.dc_footprint_add(h, fp.RefDes, fp.LibName,
                Nm(fp.X), Nm(fp.Y), fp.RotationDeg, fp.Side, padDefs, padDefs.Length);
        }
        foreach (var t in Traces)
            t.NativeId = NativeCore.dc_trace_add(h,
                Nm(t.Ax), Nm(t.Ay), Nm(t.Bx), Nm(t.By), Nm(t.Width), t.Layer, t.NetId, t.IsPour ? 1 : 0);
        foreach (var v in Vias)
            v.NativeId = NativeCore.dc_via_add(h, Nm(v.X), Nm(v.Y), Nm(v.DiameterMm), Nm(v.DrillMm),
                v.NetId, v.FromLayer, v.ToLayer);
        _nativeStale = false;
    }

    // ---- low-level (no undo entry) ----

    public void InternalAddFootprint(FootprintItem fp) { Footprints.Add(fp); NotifyChanged(); }
    public void InternalRemoveFootprint(FootprintItem fp) { Footprints.Remove(fp); NotifyChanged(); }
    public void InternalAddTrace(TraceItem t) { Traces.Add(t); NotifyChanged(); }
    public void InternalRemoveTrace(TraceItem t) { Traces.Remove(t); NotifyChanged(); }
    public void InternalAddVia(ViaItem v) { Vias.Add(v); NotifyChanged(); }
    public void InternalRemoveVia(ViaItem v) { Vias.Remove(v); NotifyChanged(); }

    public void InternalMoveFootprint(FootprintItem fp, double xMm, double yMm, double rotationDeg)
    {
        fp.X = xMm; fp.Y = yMm; fp.RotationDeg = rotationDeg;
        NotifyChanged();
    }

    // ---- undoable operations ----

    public FootprintItem AddFootprint(string refDes, string lib, double xMm, double yMm, List<PadItem> pads,
                                      double rotationDeg = 0, int side = 0, double height3DMm = 0)
    {
        var fp = new FootprintItem { RefDes = refDes, LibName = lib, X = xMm, Y = yMm,
                                     RotationDeg = rotationDeg, Side = side, Pads = pads,
                                     Height3DMm = height3DMm };
        (fp.X, fp.Y) = LegalizeFootprintPosition(fp, xMm, yMm, rotationDeg);
        InternalAddFootprint(fp);
        Undo.Push($"Place {refDes}",
            undo: () => InternalRemoveFootprint(fp),
            redo: () => InternalAddFootprint(fp));
        return fp;
    }

    public void RemoveFootprint(FootprintItem fp)
    {
        InternalRemoveFootprint(fp);
        Undo.Push($"Delete {fp.RefDes}",
            undo: () => InternalAddFootprint(fp),
            redo: () => InternalRemoveFootprint(fp));
    }

    /// <summary>Live move during drag — no undo entry. Call PushMoveUndo at drag end.</summary>
    public void MoveFootprint(FootprintItem fp, double xMm, double yMm, double rotationDeg)
    {
        (double legalX, double legalY) = LegalizeFootprintPosition(fp, xMm, yMm, rotationDeg);
        InternalMoveFootprint(fp, legalX, legalY, rotationDeg);
    }

    /// <summary>
    /// True when the complete rotated footprint bounds are on the actual board
    /// substrate. Checking the footprint bounds, rather than only its origin,
    /// prevents edge parts from hanging off the board.
    /// </summary>
    public bool IsFootprintInsideBoard(FootprintItem fp, double xMm, double yMm,
                                       double rotationDeg, double clearanceMm = 0)
    {
        var (minX, minY, maxX, maxY) = fp.BoundsAt(xMm, yMm, rotationDeg);
        minX -= clearanceMm; minY -= clearanceMm;
        maxX += clearanceMm; maxY += clearanceMm;
        var outline = EffectiveOutline();
        if (Shape == BoardShape.Rectangle || OutlinePolygon is not { Count: >= 3 })
            return minX >= -1e-9 && minY >= -1e-9
                && maxX <= BoardWidthMm + 1e-9 && maxY <= BoardHeightMm + 1e-9;

        // The board families currently supported by the editor are convex
        // rounded/circular outlines. Requiring every corner of the conservative
        // axis-aligned footprint box to be inside also keeps arbitrary polygon
        // imports safely on-substrate.
        return BoardOutline.ContainsOrOnBoundary(outline, minX, minY)
            && BoardOutline.ContainsOrOnBoundary(outline, minX, maxY)
            && BoardOutline.ContainsOrOnBoundary(outline, maxX, minY)
            && BoardOutline.ContainsOrOnBoundary(outline, maxX, maxY);
    }

    /// <summary>Finds the nearest legal center for a footprint pose.</summary>
    public (double x, double y) LegalizeFootprintPosition(FootprintItem fp,
                                                           double xMm, double yMm,
                                                           double rotationDeg,
                                                           double clearanceMm = 0)
    {
        if (IsFootprintInsideBoard(fp, xMm, yMm, rotationDeg, clearanceMm))
            return (xMm, yMm);

        var (minX, minY, maxX, maxY) = fp.BoundsAt(xMm, yMm, rotationDeg);
        double halfW = (maxX - minX) / 2 + clearanceMm;
        double halfH = (maxY - minY) / 2 + clearanceMm;
        if (Shape == BoardShape.Rectangle || OutlinePolygon is not { Count: >= 3 })
            return (
                Math.Clamp(xMm, halfW, Math.Max(halfW, BoardWidthMm - halfW)),
                Math.Clamp(yMm, halfH, Math.Max(halfH, BoardHeightMm - halfH)));

        var (outlineMinX, outlineMinY, outlineW, outlineH) = BoardOutline.BoundingBox(EffectiveOutline());
        double outlineMaxX = outlineMinX + outlineW, outlineMaxY = outlineMinY + outlineH;
        double step = Math.Max(GridMm > 0 ? GridMm : 0.5, 0.5);
        (double x, double y)? best = null;
        double bestDistance = double.MaxValue;
        for (double y = outlineMinY; y <= outlineMaxY + 1e-9; y += step)
            for (double x = outlineMinX; x <= outlineMaxX + 1e-9; x += step)
            {
                if (!IsFootprintInsideBoard(fp, x, y, rotationDeg, clearanceMm)) continue;
                double distance = (x - xMm) * (x - xMm) + (y - yMm) * (y - yMm);
                if (distance < bestDistance) { bestDistance = distance; best = (x, y); }
            }
        if (best.HasValue) return best.Value;
        double legalMinX = outlineMinX + halfW, legalMaxX = outlineMaxX - halfW;
        double legalMinY = outlineMinY + halfH, legalMaxY = outlineMaxY - halfH;
        // An oversized footprint cannot be made legal on this board. Return
        // the outline centre without throwing; DRC can report the impossible
        // fit while the placement remains deterministic.
        return (
            legalMinX <= legalMaxX ? Math.Clamp(xMm, legalMinX, legalMaxX) : outlineMinX + outlineW / 2,
            legalMinY <= legalMaxY ? Math.Clamp(yMm, legalMinY, legalMaxY) : outlineMinY + outlineH / 2);
    }

    public void PushMoveUndo(FootprintItem fp, double oldX, double oldY, double oldRot)
    {
        double newX = fp.X, newY = fp.Y, newRot = fp.RotationDeg;
        if (Math.Abs(newX - oldX) < 1e-9 && Math.Abs(newY - oldY) < 1e-9 && Math.Abs(newRot - oldRot) < 1e-9) return;
        Undo.Push($"Move {fp.RefDes}",
            undo: () => InternalMoveFootprint(fp, oldX, oldY, oldRot),
            redo: () => InternalMoveFootprint(fp, newX, newY, newRot));
    }

    /// <summary>Group move undo for multi-select drags and auto-place.</summary>
    public void PushGroupMoveUndo(string name, List<(FootprintItem fp, double oldX, double oldY, double oldRot)> before)
    {
        var after = before.Select(b => (b.fp, newX: b.fp.X, newY: b.fp.Y, newRot: b.fp.RotationDeg)).ToList();
        bool any = false;
        for (int i = 0; i < before.Count; i++)
            if (Math.Abs(after[i].newX - before[i].oldX) > 1e-9 ||
                Math.Abs(after[i].newY - before[i].oldY) > 1e-9 ||
                Math.Abs(after[i].newRot - before[i].oldRot) > 1e-9) { any = true; break; }
        if (!any) return;
        Undo.Push(name,
            undo: () => { foreach (var b in before) InternalMoveFootprint(b.fp, b.oldX, b.oldY, b.oldRot); },
            redo: () => { foreach (var a in after) InternalMoveFootprint(a.fp, a.newX, a.newY, a.newRot); });
    }

    public TraceItem AddTraceDirect(double ax, double ay, double bx, double by, double widthMm, int netId, int layer)
    {
        var t = new TraceItem { Ax = ax, Ay = ay, Bx = bx, By = by, Width = widthMm, NetId = netId, Layer = layer };
        InternalAddTrace(t);
        Undo.Push("Route trace",
            undo: () => InternalRemoveTrace(t),
            redo: () => InternalAddTrace(t));
        return t;
    }

    public void RemoveTrace(TraceItem t)
    {
        InternalRemoveTrace(t);
        Undo.Push("Delete trace",
            undo: () => InternalAddTrace(t),
            redo: () => InternalRemoveTrace(t));
    }

    public ViaItem AddViaDirect(double xMm, double yMm, int netId, int fromLayer, int toLayer,
                                double diameterMm, double drillMm)
    {
        var v = new ViaItem { X = xMm, Y = yMm, NetId = netId, FromLayer = fromLayer, ToLayer = toLayer,
                              DiameterMm = diameterMm, DrillMm = drillMm };
        InternalAddVia(v);
        Undo.Push("Place via",
            undo: () => InternalRemoveVia(v),
            redo: () => InternalAddVia(v));
        return v;
    }

    public void RemoveItems(List<FootprintItem> fps, List<TraceItem> traces, List<ViaItem>? vias = null)
    {
        vias ??= new List<ViaItem>();
        if (fps.Count == 0 && traces.Count == 0 && vias.Count == 0) return;
        foreach (var fp in fps) InternalRemoveFootprint(fp);
        foreach (var t in traces) InternalRemoveTrace(t);
        foreach (var v in vias) InternalRemoveVia(v);
        var viasCopy = vias;
        Undo.Push($"Delete {fps.Count + traces.Count + viasCopy.Count} item(s)",
            undo: () => { foreach (var fp in fps) InternalAddFootprint(fp); foreach (var t in traces) InternalAddTrace(t); foreach (var v in viasCopy) InternalAddVia(v); },
            redo: () => { foreach (var fp in fps) InternalRemoveFootprint(fp); foreach (var t in traces) InternalRemoveTrace(t); foreach (var v in viasCopy) InternalRemoveVia(v); });
    }

    // ---- routing ----

    public string? LastRouteError { get; private set; }

    /// <summary>
    /// Multi-layer auto-route between two points using the C++ A* engine.
    /// Inserts vias on layer changes. One undo entry for the whole route.
    /// Width/clearance/via geometry come from the net's class.
    /// </summary>
    public bool AutoRoute(double sxMm, double syMm, double gxMm, double gyMm, int netId,
                          int startLayer = 0, int goalLayer = -1)
    {
        SyncNative();
        var cls = ClassFor(netId);
        if (goalLayer < 0) goalLayer = startLayer;
        var req = new NativeCore.DcRouteRequest
        {
            NetId = netId,
            Sx = Nm(sxMm), Sy = Nm(syMm), StartLayer = startLayer,
            Gx = Nm(gxMm), Gy = Nm(gyMm), GoalLayer = goalLayer,
            TraceWidth = Nm(cls.TraceWidthMm), Clearance = Nm(cls.ClearanceMm),
            GridStep = Nm(0.1),
            AllowVias = CopperLayers > 1 ? 1 : 0,
            AllowMicrovia = cls.AllowMicrovia ? 1 : 0,
            ViaDiameter = Nm(cls.ViaDiameterMm), ViaDrill = Nm(cls.ViaDrillMm),
            ViaCostMm = 3.0,
            MaxExpansions = 4_000_000,
            MinDrillToDrill = Nm(0.5)
        };
        int status = NativeCore.dc_route_run(Core.Handle, ref req);
        if (status != NativeCore.Ok)
        {
            LastRouteError = NativeCore.RouteStatusText(status);
            return false;
        }
        LastRouteError = null;

        int nPts = NativeCore.dc_route_point_count(Core.Handle);
        var pts = new NativeCore.DcRoutePoint[Math.Max(nPts, 1)];
        nPts = NativeCore.dc_route_get_points(Core.Handle, pts, pts.Length);
        int nVias = NativeCore.dc_route_via_count(Core.Handle);
        var rvias = new NativeCore.DcRouteVia[Math.Max(nVias, 1)];
        nVias = NativeCore.dc_route_get_vias(Core.Handle, rvias, rvias.Length);
        if (nPts < 2) return false;

        var addedTraces = new List<TraceItem>();
        var addedVias = new List<ViaItem>();
        for (int i = 0; i + 1 < nPts; i++)
        {
            if (pts[i].Layer != pts[i + 1].Layer) continue;   // via hop, no copper
            if (pts[i].X == pts[i + 1].X && pts[i].Y == pts[i + 1].Y) continue;
            var t = new TraceItem
            {
                Ax = Mm(pts[i].X), Ay = Mm(pts[i].Y),
                Bx = Mm(pts[i + 1].X), By = Mm(pts[i + 1].Y),
                Width = cls.TraceWidthMm, NetId = netId, Layer = pts[i].Layer
            };
            InternalAddTrace(t);
            addedTraces.Add(t);
        }
        for (int i = 0; i < nVias; i++)
        {
            var v = new ViaItem
            {
                X = Mm(rvias[i].X), Y = Mm(rvias[i].Y),
                NetId = netId, FromLayer = rvias[i].FromLayer, ToLayer = rvias[i].ToLayer,
                DiameterMm = cls.ViaDiameterMm, DrillMm = cls.ViaDrillMm
            };
            InternalAddVia(v);
            addedVias.Add(v);
        }
        Undo.Push("Auto-route",
            undo: () => { foreach (var t in addedTraces) InternalRemoveTrace(t); foreach (var v in addedVias) InternalRemoveVia(v); },
            redo: () => { foreach (var t in addedTraces) InternalAddTrace(t); foreach (var v in addedVias) InternalAddVia(v); });
        return true;
    }

    /// <summary>
    /// Rips up and re-routes every already-routed net touching the given
    /// footprints. Called after a part is moved so copper follows the pins
    /// instead of being left disconnected. Nets that were never routed are
    /// left to the ratsnest. One undo entry per net rip-up; AutoRoute adds
    /// its own entries.
    /// </summary>
    public void RerouteFootprintNets(IEnumerable<FootprintItem> moved)
    {
        var netIds = moved.SelectMany(fp => fp.Pads)
                          .Select(p => p.NetId)
                          .Where(id => id >= 0)
                          .Distinct()
                          .ToList();
        foreach (var netId in netIds)
        {
            var oldTraces = Traces.Where(t => !t.IsPour && t.NetId == netId).ToList();
            var oldVias = Vias.Where(v => v.NetId == netId).ToList();
            if (oldTraces.Count == 0) continue;          // never routed — ratsnest only

            foreach (var t in oldTraces) InternalRemoveTrace(t);
            foreach (var v in oldVias) InternalRemoveVia(v);
            Undo.Push($"Rip up {NetName(netId)}",
                undo: () => { foreach (var t in oldTraces) InternalAddTrace(t); foreach (var v in oldVias) InternalAddVia(v); },
                redo: () => { foreach (var t in oldTraces) InternalRemoveTrace(t); foreach (var v in oldVias) InternalRemoveVia(v); });

            // all pad positions on this net, connected as a greedy nearest-
            // neighbour chain — short, direct copper after every move
            var pts = Footprints
                .SelectMany(fp => fp.Pads.Where(p => p.NetId == netId)
                                         .Select(p => fp.PadWorld(p)))
                .ToList();
            if (pts.Count < 2) continue;
            var remaining = new List<(double x, double y)>(pts.Skip(1));
            var cur = pts[0];
            while (remaining.Count > 0)
            {
                int best = 0; double bestD = double.MaxValue;
                for (int i = 0; i < remaining.Count; i++)
                {
                    double d = (remaining[i].x - cur.x) * (remaining[i].x - cur.x) +
                               (remaining[i].y - cur.y) * (remaining[i].y - cur.y);
                    if (d < bestD) { bestD = d; best = i; }
                }
                var next = remaining[best];
                remaining.RemoveAt(best);
                AutoRoute(cur.x, cur.y, next.x, next.y, netId);
                cur = next;
            }
        }
    }

    /// <summary>
    /// True if any footprint in <paramref name="set"/> would overlap another
    /// footprint (outside the set) closer than <paramref name="marginMm"/>.
    /// Used to keep dragged parts from overshadowing their neighbours.
    /// </summary>
    public bool FootprintsCollide(IReadOnlyCollection<FootprintItem> set, double marginMm = 0.5)
    {
        foreach (var fp in set)
        {
            var (aMinX, aMinY, aMaxX, aMaxY) = fp.Bounds();
            foreach (var other in Footprints)
            {
                if (other == fp || set.Contains(other) || other.Side != fp.Side) continue;
                var (bMinX, bMinY, bMaxX, bMaxY) = other.Bounds();
                if (aMinX < bMaxX + marginMm && aMaxX > bMinX - marginMm &&
                    aMinY < bMaxY + marginMm && aMaxY > bMinY - marginMm)
                    return true;
            }
        }
        return false;
    }

    // ---- DRC ----

    public static string RuleName(int rule) => rule switch
    {
        14 => "Reference plane",
        18 => "Current capacity",
        19 => "Class-pair clearance",
        20 => "IR drop",
        21 => "PDN impedance",
        22 => "Crosstalk",
        23 => "Thermal",
        15 => "Via stub",
        16 => "Pair gap",
        17 => "Return path",
        1 => "Trace-trace clearance",
        2 => "Pad-trace clearance",
        3 => "Pad-pad clearance",
        4 => "Board edge",
        5 => "Trace width",
        6 => "Drill size",
        7 => "Via-trace clearance",
        8 => "Via-pad clearance",
        9 => "Via-via clearance",
        10 => "Annular ring",
        11 => "Hole-to-hole",
        12 => "Unconnected net",
        13 => "Length matching",
        _ => $"Rule {rule}"
    };

    public List<DrcResultItem> RunDrcDetailed()
    {
        SyncNative();
        var opt = new NativeCore.DcDrcOptions
        {
            // HDI-class limits: laser microvias go to 0.10 mm drill / 0.05 mm
            // annular ring (matches HdiVias.MinAnnularMm and the drc.h note).
            // A 0.5 mm-pitch BGA can't be escaped with 0.15 mm mechanical drills.
            DefaultClearance = Nm(0.2), MinTraceWidth = Nm(0.1), MinDrill = Nm(0.10),
            MinAnnularRing = Nm(0.05), MinDrillToDrill = Nm(0.25),
            CheckConnectivity = 1, CheckSkew = 1
        };
        int total = NativeCore.dc_drc_run(Core.Handle, ref opt);
        var result = new List<DrcResultItem>(Math.Max(total, 0));
        for (int i = 0; i < total; i++)
        {
            if (NativeCore.dc_drc_get(Core.Handle, i, out var v) != NativeCore.Ok) break;
            result.Add(new DrcResultItem(v.Rule, RuleName(v.Rule), v.Message ?? "", Mm(v.X), Mm(v.Y)));
        }
        result.AddRange(DelayEngine.CheckMatchGroups(this));   // delay-aware timing rule 13
        result.AddRange(HighSpeedDrc.Run(this));               // SI rules 14–17
        result.AddRange(RuleResolver.CheckPairClearances(this)); // rule 19
        return result;
    }

    // ---- copper pours ----

    /// <summary>
    /// Regenerates the pour for (net, layer): removes existing pour strokes of
    /// that net/layer and fills the board with clearance-respecting copper.
    /// One undo entry.
    /// </summary>
    public int GeneratePour(int netId, int layer, double lineWidthMm = 0.3, double clearanceMm = 0.25)
    {
        var old = Traces.Where(t => t.IsPour && t.NetId == netId && t.Layer == layer).ToList();
        foreach (var t in old) Traces.Remove(t);
        NotifyChanged();

        SyncNative();
        int n = NativeCore.dc_pour_run(Core.Handle, netId, layer, Nm(lineWidthMm), Nm(clearanceMm),
            0, 0, Nm(BoardWidthMm), Nm(BoardHeightMm));
        var added = new List<TraceItem>(Math.Max(n, 0));
        for (int i = 0; i < n; i++)
        {
            if (NativeCore.dc_pour_get(Core.Handle, i, out var s) != NativeCore.Ok) break;
            added.Add(new TraceItem
            {
                Ax = Mm(s.Ax), Ay = Mm(s.Ay), Bx = Mm(s.Bx), By = Mm(s.By),
                Width = Mm(s.Width), NetId = netId, Layer = layer, IsPour = true
            });
        }
        foreach (var t in added) Traces.Add(t);
        NotifyChanged();

        Undo.Push($"Pour {NetName(netId)} on {LayerName(layer)}",
            undo: () =>
            {
                foreach (var t in added) Traces.Remove(t);
                foreach (var t in old) Traces.Add(t);
                NotifyChanged();
            },
            redo: () =>
            {
                foreach (var t in old) Traces.Remove(t);
                foreach (var t in added) Traces.Add(t);
                NotifyChanged();
            });
        return added.Count;
    }

    public void ClearPour(int netId, int layer)
    {
        var old = Traces.Where(t => t.IsPour && t.NetId == netId && t.Layer == layer).ToList();
        if (old.Count == 0) return;
        foreach (var t in old) Traces.Remove(t);
        NotifyChanged();
        Undo.Push("Clear pour",
            undo: () => { foreach (var t in old) Traces.Add(t); NotifyChanged(); },
            redo: () => { foreach (var t in old) Traces.Remove(t); NotifyChanged(); });
    }

    // ---- queries ----

    /// <summary>
    /// Pad under (or near) a point, topmost first: returns its world centre and
    /// net. Route clicks snap here so traces land exactly on pin centres and
    /// adopt the pin's net — the difference between drawn copper and a
    /// connected pin.
    /// </summary>
    public (double X, double Y, int NetId)? SnapToPad(double xMm, double yMm, double tolMm = 0.3)
    {
        var hit = FindPad(xMm, yMm, tolMm);
        if (hit is null) return null;
        var (fp, pad) = hit.Value;
        var (px, py) = fp.PadWorld(pad);
        return (px, py, pad.NetId);
    }

    /// <summary>
    /// The pad nearest the point within reach — *nearest*, not first-found, so
    /// densely packed pins resolve to the one actually under the cursor
    /// instead of a neighbour earlier in iteration order.
    /// </summary>
    public (FootprintItem fp, PadItem pad)? FindPad(double xMm, double yMm, double tolMm = 0.3)
    {
        (FootprintItem fp, PadItem pad)? best = null;
        double bestD = double.MaxValue;
        int bestZ = -1;
        // quadtree query instead of scanning every pad on the board;
        // nearest wins, exact ties go to the topmost (later-placed) footprint
        foreach (var (fp, pad) in BoardIndex.Pads(this).Query(xMm, yMm, tolMm + 5))
        {
            var (px, py) = fp.PadWorld(pad);
            double reach = Math.Max(pad.W, pad.H) / 2 + tolMm;
            double dx = xMm - px, dy = yMm - py, d2 = dx * dx + dy * dy;
            if (d2 > reach * reach) continue;
            int z = Footprints.IndexOf(fp);
            if (d2 < bestD - 1e-12 || (Math.Abs(d2 - bestD) <= 1e-12 && z > bestZ))
            { bestD = d2; bestZ = z; best = (fp, pad); }
        }
        return best;
    }

    public double NetLengthMm(int netId)
    {
        SyncNative();
        long nm = NativeCore.dc_net_length(Core.Handle, netId);
        return nm < 0 ? 0 : Mm(nm);
    }

    /// <summary>1 = fully connected, larger = unrouted islands remain.</summary>
    public int NetIslands(int netId)
    {
        SyncNative();
        int n = NativeCore.dc_net_islands(Core.Handle, netId);
        return Math.Max(n, 0);
    }

    public void Dispose() => Core.Dispose();
}
