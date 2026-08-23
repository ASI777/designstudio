using System.Windows.Media;
using System.Windows.Media.Media3D;
using DesignStudio.Model;

namespace DesignStudio.Controls;

/// <summary>
/// 2D→3D evolution: generates the board-level 3D scene from the PCB document —
/// substrate slab, outer-layer copper, vias, extruded component bodies with
/// per-package height heuristics. No imported models required.
/// </summary>
public static class Board3DBuilder
{
    private const double BoardThickness = 1.6;   // mm

    public static Model3DGroup Build(BoardDocument doc)
    {
        var group = new Model3DGroup();
        group.Children.Add(new AmbientLight(Color.FromRgb(70, 70, 70)));
        group.Children.Add(new DirectionalLight(Colors.White, new Vector3D(-0.4, -0.6, -0.8)));
        group.Children.Add(new DirectionalLight(Color.FromRgb(90, 90, 110), new Vector3D(0.6, 0.4, 0.5)));

        var fr4 = MatteMaterial(Color.FromRgb(0x1B, 0x5E, 0x20));
        var copper = MatteMaterial(Color.FromRgb(0xC8, 0x96, 0x32));
        var bodyIc = MatteMaterial(Color.FromRgb(0x30, 0x30, 0x34));
        var bodyPassive = MatteMaterial(Color.FromRgb(0x8D, 0x6E, 0x63));
        var bodyConn = MatteMaterial(Color.FromRgb(0x10, 0x10, 0x10));

        // Board substrate: top face at Z=0, body extruded downward.
        group.Children.Add(Box(doc.BoardWidthMm / 2, -doc.BoardHeightMm / 2, -BoardThickness / 2,
                               doc.BoardWidthMm, doc.BoardHeightMm, BoardThickness, fr4));

        // Copper: outer-layer traces as thin slabs (top above, bottom below the
        // substrate). Pour strokes are skipped — thousands of fill lines would
        // swamp the 3D scene without adding shape information.
        foreach (var t in doc.Traces.Where(t => !t.IsPour && (t.Layer == 0 || t.Layer == doc.CopperLayers - 1)))
        {
            bool top = t.Layer == 0;
            double dx = t.Bx - t.Ax, dy = t.By - t.Ay;
            double len = Math.Sqrt(dx * dx + dy * dy);
            if (len < 1e-6) continue;
            double angle = Math.Atan2(dy, dx) * 180 / Math.PI;
            double z = top ? 0.02 : -BoardThickness - 0.02;
            var slab = Box(0, 0, z, len, t.Width, 0.035, copper);
            var tg = new Transform3DGroup();
            tg.Children.Add(new TranslateTransform3D(len / 2, 0, 0));
            tg.Children.Add(new RotateTransform3D(new AxisAngleRotation3D(new Vector3D(0, 0, 1), -angle)));
            tg.Children.Add(new TranslateTransform3D(t.Ax, -t.Ay, 0));
            slab.Transform = tg;
            group.Children.Add(slab);
        }

        // Vias: small copper barrels through the substrate.
        foreach (var v in doc.Vias)
        {
            group.Children.Add(Box(v.X, -v.Y, -BoardThickness / 2,
                                   v.DiameterMm, v.DiameterMm, BoardThickness + 0.07, copper));
        }

        foreach (var fp in doc.Footprints)
        {
            // Pads
            foreach (var pad in fp.Pads)
            {
                var (px, py) = fp.PadWorld(pad);
                var padBox = Box(0, 0, 0.025, pad.W, pad.H, 0.05, copper);
                var tg = new Transform3DGroup();
                tg.Children.Add(new RotateTransform3D(new AxisAngleRotation3D(new Vector3D(0, 0, 1), -fp.RotationDeg)));
                tg.Children.Add(new TranslateTransform3D(px, -py, 0));
                padBox.Transform = tg;
                group.Children.Add(padBox);
            }

            // Body: extruded bounding box with package height heuristic, slightly
            // inset from the pad extents so pads stay visible.
            var (minX, minY, maxX, maxY) = fp.Bounds();
            double w = Math.Max(0.4, (maxX - minX) * 0.75);
            double h = Math.Max(0.4, (maxY - minY) * 0.75);
            double height = fp.Height3DMm > 0 ? fp.Height3DMm : PackageHeight(fp.LibName);
            var mat = PackageMaterial(fp.LibName, bodyIc, bodyPassive, bodyConn);
            group.Children.Add(Box((minX + maxX) / 2, -(minY + maxY) / 2, 0.05 + height / 2, w, h, height, mat));
        }

        return group;
    }

    private static double PackageHeight(string lib)
    {
        string s = lib.ToUpperInvariant();
        if (s.Contains("SOIC") || s.Contains("SOT")) return 1.6;
        if (s.Contains("QFN") || s.Contains("QFP") || s.Contains("BGA")) return 0.9;
        if (s.Contains("HEADER") || s.StartsWith("J") || s.Contains("CONN")) return 8.5;
        if (s.StartsWith("R_") || s.StartsWith("C_") || s.StartsWith("L_") || s.Contains("LED")) return 0.6;
        return 1.2;
    }

    private static Material PackageMaterial(string lib, Material ic, Material passive, Material conn)
    {
        string s = lib.ToUpperInvariant();
        if (s.Contains("HEADER") || s.Contains("CONN")) return conn;
        if (s.StartsWith("R_") || s.StartsWith("C_") || s.StartsWith("L_") || s.Contains("LED")) return passive;
        return ic;
    }

    private static Material MatteMaterial(Color c)
    {
        var mg = new MaterialGroup();
        mg.Children.Add(new DiffuseMaterial(new SolidColorBrush(c)));
        mg.Children.Add(new SpecularMaterial(new SolidColorBrush(Color.FromArgb(60, 255, 255, 255)), 24));
        return mg;
    }

    /// <summary>Axis-aligned box centred at (cx, cy, cz). Y axis: world -y (screen up).</summary>
    private static GeometryModel3D Box(double cx, double cy, double cz,
                                       double sx, double sy, double sz, Material mat)
    {
        double hx = sx / 2, hy = sy / 2, hz = sz / 2;
        var p = new Point3D[]
        {
            new(cx - hx, cy - hy, cz - hz), new(cx + hx, cy - hy, cz - hz),
            new(cx + hx, cy + hy, cz - hz), new(cx - hx, cy + hy, cz - hz),
            new(cx - hx, cy - hy, cz + hz), new(cx + hx, cy - hy, cz + hz),
            new(cx + hx, cy + hy, cz + hz), new(cx - hx, cy + hy, cz + hz),
        };
        var mesh = new MeshGeometry3D();
        foreach (var pt in p) mesh.Positions.Add(pt);
        int[] idx =
        {
            0,1,2, 0,2,3,   // bottom (z-)
            4,6,5, 4,7,6,   // top (z+)
            0,4,5, 0,5,1,   // front (y-)
            1,5,6, 1,6,2,   // right (x+)
            2,6,7, 2,7,3,   // back (y+)
            3,7,4, 3,4,0,   // left (x-)
        };
        foreach (var i in idx) mesh.TriangleIndices.Add(i);
        var model = new GeometryModel3D(mesh, mat) { BackMaterial = mat };
        return model;
    }
}
