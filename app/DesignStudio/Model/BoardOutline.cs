namespace DesignStudio.Model;

// ============================================================================
// Board outline / shape.
//
// The board was always a width x height rectangle. Real boards are not: round
// coin-cell and motor boards, rounded-corner phone/laptop boards, and free-form
// outlines that hug a mechanical enclosure. BoardOutline turns the outline into
// a polygon (mm, counter-clockwise, implicitly closed) with builders for the
// common shapes and the geometry the rest of the app needs: a bounding box (so
// width/height and the router keep-out stay meaningful) and point-in-polygon
// containment (for "is this part on the board?" checks and edge clearance).
//
// The polygon flows straight into the Clipper2-based plane generator (planes
// fill the real outline), the Gerber edge layer, and the canvas; the native
// router keep-out stays the bounding rectangle, with the true edge enforced by
// the outline DRC.
// ============================================================================

public enum BoardShape { Rectangle, RoundedRectangle, Circle, Polygon }

public static class BoardOutline
{
    /// <summary>Rectangle outline (CCW from origin).</summary>
    public static List<double[]> Rectangle(double wMm, double hMm) => new()
    {
        new[] { 0.0, 0.0 }, new[] { wMm, 0.0 }, new[] { wMm, hMm }, new[] { 0.0, hMm }
    };

    /// <summary>Rounded-rectangle outline; each corner arc uses <paramref name="seg"/> segments.</summary>
    public static List<double[]> RoundedRectangle(double wMm, double hMm, double radiusMm, int seg = 8)
    {
        double r = Math.Max(0, Math.Min(radiusMm, Math.Min(wMm, hMm) / 2));
        if (r <= 1e-9) return Rectangle(wMm, hMm);
        var pts = new List<double[]>();
        // corner centres, CCW starting bottom-right
        (double cx, double cy, double a0)[] corners =
        {
            (wMm - r, r, -Math.PI / 2),       // bottom-right
            (wMm - r, hMm - r, 0),            // top-right
            (r, hMm - r, Math.PI / 2),        // top-left
            (r, r, Math.PI)                   // bottom-left
        };
        foreach (var (cx, cy, a0) in corners)
            for (int i = 0; i <= seg; i++)
            {
                double a = a0 + (Math.PI / 2) * i / seg;
                pts.Add(new[] { cx + r * Math.Cos(a), cy + r * Math.Sin(a) });
            }
        return pts;
    }

    /// <summary>Circle outline of the given diameter, polygonised into <paramref name="seg"/> sides.</summary>
    public static List<double[]> Circle(double diameterMm, int seg = 64)
    {
        double r = diameterMm / 2;
        int n = Math.Max(12, seg);
        var pts = new List<double[]>(n);
        for (int i = 0; i < n; i++)
        {
            double a = 2 * Math.PI * i / n;
            pts.Add(new[] { r + r * Math.Cos(a), r + r * Math.Sin(a) });   // centred so bbox starts at 0,0
        }
        return pts;
    }

    /// <summary>Axis-aligned bounding box (minX, minY, width, height) of a polygon.</summary>
    public static (double minX, double minY, double width, double height) BoundingBox(IReadOnlyList<double[]> poly)
    {
        if (poly == null || poly.Count == 0) return (0, 0, 0, 0);
        double minX = double.MaxValue, minY = double.MaxValue, maxX = double.MinValue, maxY = double.MinValue;
        foreach (var p in poly)
        {
            minX = Math.Min(minX, p[0]); maxX = Math.Max(maxX, p[0]);
            minY = Math.Min(minY, p[1]); maxY = Math.Max(maxY, p[1]);
        }
        return (minX, minY, maxX - minX, maxY - minY);
    }

    /// <summary>Even-odd point-in-polygon test (mm).</summary>
    public static bool Contains(IReadOnlyList<double[]> poly, double x, double y)
    {
        if (poly == null || poly.Count < 3) return false;
        bool inside = false;
        for (int i = 0, j = poly.Count - 1; i < poly.Count; j = i++)
        {
            double xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
            bool crosses = (yi > y) != (yj > y) &&
                           x < (xj - xi) * (y - yi) / (yj - yi + double.Epsilon) + xi;
            if (crosses) inside = !inside;
        }
        return inside;
    }

    public static bool ContainsOrOnBoundary(IReadOnlyList<double[]> poly, double x, double y,
                                            double toleranceMm = 1e-7)
    {
        if (poly == null || poly.Count < 3) return false;
        for (int i = 0; i < poly.Count; i++)
        {
            var a = poly[i];
            var b = poly[(i + 1) % poly.Count];
            double dx = b[0] - a[0], dy = b[1] - a[1];
            double lengthSquared = dx * dx + dy * dy;
            if (lengthSquared <= toleranceMm * toleranceMm) continue;
            double t = Math.Clamp(((x - a[0]) * dx + (y - a[1]) * dy) / lengthSquared, 0, 1);
            double px = a[0] + t * dx, py = a[1] + t * dy;
            if ((x - px) * (x - px) + (y - py) * (y - py) <= toleranceMm * toleranceMm)
                return true;
        }
        return Contains(poly, x, y);
    }

    /// <summary>
    /// Items whose centre falls outside the board outline (a board-edge sanity
    /// check). Returns human-readable identifiers. Empty when the board is a plain
    /// rectangle or everything is inside.
    /// </summary>
    public static List<string> OutsideOutline(BoardDocument doc)
    {
        var outline = doc.EffectiveOutline();
        var bad = new List<string>();
        // a rectangle outline can't exclude anything inside width x height, skip
        if (doc.Shape == BoardShape.Rectangle) return bad;

        foreach (var fp in doc.Footprints)
            if (fp.Pads.Count == 0
                ? !ContainsOrOnBoundary(outline, fp.X, fp.Y)
                : !doc.IsFootprintInsideBoard(fp, fp.X, fp.Y, fp.RotationDeg))
                bad.Add($"{fp.RefDes} at ({fp.X:F1},{fp.Y:F1}) mm is outside the board outline");
        foreach (var v in doc.Vias)
            if (!Contains(outline, v.X, v.Y))
                bad.Add($"via at ({v.X:F1},{v.Y:F1}) mm is outside the board outline");
        return bad;
    }
}
