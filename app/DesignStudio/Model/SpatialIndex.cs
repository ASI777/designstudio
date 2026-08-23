namespace DesignStudio.Model;

// ============================================================================
// 1.1 Spatial index — a loose quadtree over axis-aligned bounding boxes.
//
// Purpose: O(log n) "what is near this point/rect" for hit-testing, DRC
// proximity rules, crosstalk pair discovery and (later) shove routing.
// Managed implementation: the consumers today are managed; the C++ core can
// grow its own mirror when batch routing lands. Build is O(n log n); the
// document rebuilds it lazily keyed on BoardDocument.Version, so callers
// never see a stale index.
// ============================================================================

public readonly record struct BBox(double MinX, double MinY, double MaxX, double MaxY)
{
    public bool Intersects(in BBox o)
        => MinX <= o.MaxX && MaxX >= o.MinX && MinY <= o.MaxY && MaxY >= o.MinY;
    public bool Contains(double x, double y)
        => x >= MinX && x <= MaxX && y >= MinY && y <= MaxY;
    public double CentreDist2(double x, double y)
    {
        double cx = (MinX + MaxX) / 2 - x, cy = (MinY + MaxY) / 2 - y;
        return cx * cx + cy * cy;
    }
    public BBox Inflate(double d) => new(MinX - d, MinY - d, MaxX + d, MaxY + d);
}

public class SpatialIndex<T>
{
    private const int SplitThreshold = 8;
    private const int MaxDepth = 10;

    private sealed class Node
    {
        public BBox Bounds;
        public List<(BBox box, T item)> Items = new();
        public Node?[]? Children;       // 4 quadrants, created on split
        public int Depth;
    }

    private readonly Node _root;
    public int Count { get; private set; }

    public SpatialIndex(double minX, double minY, double maxX, double maxY)
        => _root = new Node { Bounds = new BBox(minX, minY, maxX, maxY) };

    public void Insert(in BBox box, T item) { Insert(_root, box, item); Count++; }

    private static void Insert(Node n, in BBox box, T item)
    {
        if (n.Children != null)
        {
            int q = Quadrant(n, box);
            if (q >= 0) { Insert(Child(n, q), box, item); return; }
        }
        n.Items.Add((box, item));
        if (n.Children == null && n.Items.Count > SplitThreshold && n.Depth < MaxDepth)
            Split(n);
    }

    private static void Split(Node n)
    {
        n.Children = new Node?[4];
        var keep = new List<(BBox, T)>();
        foreach (var (box, item) in n.Items)
        {
            int q = Quadrant(n, box);
            if (q >= 0) Insert(Child(n, q), box, item);
            else keep.Add((box, item));
        }
        n.Items = keep;
    }

    private static int Quadrant(Node n, in BBox box)
    {
        double mx = (n.Bounds.MinX + n.Bounds.MaxX) / 2, my = (n.Bounds.MinY + n.Bounds.MaxY) / 2;
        bool left = box.MaxX <= mx, right = box.MinX >= mx;
        bool top = box.MaxY <= my, bottom = box.MinY >= my;
        if (left && top) return 0;
        if (right && top) return 1;
        if (left && bottom) return 2;
        if (right && bottom) return 3;
        return -1;                       // straddles a midline → stays at this node
    }

    private static Node Child(Node n, int q)
    {
        if (n.Children![q] is { } c) return c;
        double mx = (n.Bounds.MinX + n.Bounds.MaxX) / 2, my = (n.Bounds.MinY + n.Bounds.MaxY) / 2;
        var b = q switch
        {
            0 => new BBox(n.Bounds.MinX, n.Bounds.MinY, mx, my),
            1 => new BBox(mx, n.Bounds.MinY, n.Bounds.MaxX, my),
            2 => new BBox(n.Bounds.MinX, my, mx, n.Bounds.MaxY),
            _ => new BBox(mx, my, n.Bounds.MaxX, n.Bounds.MaxY)
        };
        return n.Children[q] = new Node { Bounds = b, Depth = n.Depth + 1 };
    }

    /// <summary>All items whose bbox intersects the query rect.</summary>
    public void Query(in BBox rect, List<T> results) => Query(_root, rect, results);

    private static void Query(Node n, in BBox rect, List<T> results)
    {
        foreach (var (box, item) in n.Items)
            if (box.Intersects(rect)) results.Add(item);
        if (n.Children == null) return;
        foreach (var c in n.Children)
            if (c != null && c.Bounds.Intersects(rect)) Query(c, rect, results);
    }

    public List<T> Query(double x, double y, double radius)
    {
        var r = new List<T>();
        Query(new BBox(x - radius, y - radius, x + radius, y + radius), r);
        return r;
    }
}

// ----------------------------------------------------------------------------
// Document-level cached indexes, invalidated by Version.
// ----------------------------------------------------------------------------

public static class BoardIndex
{
    private static long _padVersion = -1;
    private static SpatialIndex<(FootprintItem fp, PadItem pad)>? _pads;
    private static BoardDocument? _padDoc;

    /// <summary>Quadtree over pad bounding boxes (world coords), rebuilt lazily.</summary>
    public static SpatialIndex<(FootprintItem fp, PadItem pad)> Pads(BoardDocument doc)
    {
        if (_pads != null && ReferenceEquals(_padDoc, doc) && _padVersion == doc.Version)
            return _pads;
        var idx = new SpatialIndex<(FootprintItem, PadItem)>(
            -doc.BoardWidthMm, -doc.BoardHeightMm, doc.BoardWidthMm * 2, doc.BoardHeightMm * 2);
        foreach (var fp in doc.Footprints)
            foreach (var pad in fp.Pads)
            {
                var (px, py) = fp.PadWorld(pad);
                double r = Math.Max(pad.W, pad.H) / 2;
                idx.Insert(new BBox(px - r, py - r, px + r, py + r), (fp, pad));
            }
        _pads = idx; _padDoc = doc; _padVersion = doc.Version;
        return idx;
    }

    private static long _traceVersion = -1;
    private static SpatialIndex<TraceItem>? _traces;
    private static BoardDocument? _traceDoc;

    /// <summary>Quadtree over non-pour trace segments.</summary>
    public static SpatialIndex<TraceItem> Traces(BoardDocument doc)
    {
        if (_traces != null && ReferenceEquals(_traceDoc, doc) && _traceVersion == doc.Version)
            return _traces;
        var idx = new SpatialIndex<TraceItem>(
            -doc.BoardWidthMm, -doc.BoardHeightMm, doc.BoardWidthMm * 2, doc.BoardHeightMm * 2);
        foreach (var t in doc.Traces)
        {
            if (t.IsPour) continue;
            idx.Insert(new BBox(Math.Min(t.Ax, t.Bx) - t.Width, Math.Min(t.Ay, t.By) - t.Width,
                                Math.Max(t.Ax, t.Bx) + t.Width, Math.Max(t.Ay, t.By) + t.Width), t);
        }
        _traces = idx; _traceDoc = doc; _traceVersion = doc.Version;
        return idx;
    }
}
