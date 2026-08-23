using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using DesignStudio.Model;

namespace DesignStudio.Controls;

/// <summary>
/// Renders a single footprint definition, auto-scaled to fit, with pad names —
/// on a millimetre grid with a measuring cursor: hover shows the position in
/// mm, click-drag measures distance (Δx, Δy, length) between any two points.
/// The cursor snaps to pad centres/edges so pad pitch and spans read exactly.
/// </summary>
public class FootprintPreview : FrameworkElement
{
    private FootprintDef? _def;
    public FootprintDef? Footprint
    {
        get => _def;
        set { _def = value; _measureA = null; InvalidateVisual(); }
    }

    private double _scale = 1;
    private double _cx, _cy;
    private Point? _mouseWorld;          // mm
    private Point? _measureA;            // mm, set on mouse-down
    private bool _measuring;

    private Point T(double x, double y) => new(x * _scale + _cx, y * _scale + _cy);
    private Point ToWorld(Point screen) => new((screen.X - _cx) / _scale, (screen.Y - _cy) / _scale);

    /// <summary>Snap to a pad centre or pad edge within 6 px, else 0.05 mm grid.</summary>
    private Point Snap(Point world)
    {
        if (_def != null)
        {
            double tol = 6 / _scale;
            foreach (var p in _def.Pads)
            {
                foreach (var (sx, sy) in new[] { (p.XMm, p.YMm),
                    (p.XMm - p.WMm / 2, p.YMm), (p.XMm + p.WMm / 2, p.YMm),
                    (p.XMm, p.YMm - p.HMm / 2), (p.XMm, p.YMm + p.HMm / 2) })
                    if (Math.Abs(world.X - sx) < tol && Math.Abs(world.Y - sy) < tol)
                        return new Point(sx, sy);
            }
        }
        return new Point(Math.Round(world.X * 20) / 20, Math.Round(world.Y * 20) / 20);
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        _mouseWorld = Snap(ToWorld(e.GetPosition(this)));
        InvalidateVisual();
    }

    protected override void OnMouseLeave(MouseEventArgs e)
    {
        _mouseWorld = null;
        InvalidateVisual();
    }

    protected override void OnMouseDown(MouseButtonEventArgs e)
    {
        if (e.ChangedButton == MouseButton.Right) { _measureA = null; _measuring = false; }
        else { _measureA = Snap(ToWorld(e.GetPosition(this))); _measuring = true; CaptureMouse(); }
        InvalidateVisual();
    }

    protected override void OnMouseUp(MouseButtonEventArgs e)
    {
        _measuring = false;            // measurement stays on screen until right-click / reselect
        ReleaseMouseCapture();
    }

    protected override void OnRender(DrawingContext dc)
    {
        dc.DrawRectangle(new SolidColorBrush(Color.FromRgb(0x10, 0x14, 0x18)), null, new Rect(RenderSize));
        if (_def is null || _def.Pads.Count == 0) return;

        double minX = -_def.BodyWidthMm / 2, maxX = _def.BodyWidthMm / 2;
        double minY = -_def.BodyHeightMm / 2, maxY = _def.BodyHeightMm / 2;
        foreach (var p in _def.Pads)
        {
            minX = Math.Min(minX, p.XMm - p.WMm / 2); maxX = Math.Max(maxX, p.XMm + p.WMm / 2);
            minY = Math.Min(minY, p.YMm - p.HMm / 2); maxY = Math.Max(maxY, p.YMm + p.HMm / 2);
        }
        double w = maxX - minX, h = maxY - minY;
        if (w <= 0 || h <= 0) return;
        _scale = 0.85 * Math.Min(ActualWidth / w, ActualHeight / h);
        _cx = ActualWidth / 2 - (minX + w / 2) * _scale;
        _cy = ActualHeight / 2 - (minY + h / 2) * _scale;

        // ---- mm grid (pitch auto-chosen so lines stay ≥ 12 px apart) ----
        double pitch = 0.1;
        while (pitch * _scale < 12) pitch *= pitch.ToString().EndsWith("2") ? 2.5 : 2;  // 0.1 0.2 0.5 1 2 5…
        var gridPen = new Pen(new SolidColorBrush(Color.FromArgb(40, 255, 255, 255)), 0.5);
        var axisPen = new Pen(new SolidColorBrush(Color.FromArgb(90, 120, 200, 255)), 0.8);
        double gx0 = Math.Floor(ToWorld(new Point(0, 0)).X / pitch) * pitch;
        double gy0 = Math.Floor(ToWorld(new Point(0, 0)).Y / pitch) * pitch;
        for (double x = gx0; x * _scale + _cx < ActualWidth; x += pitch)
            dc.DrawLine(Math.Abs(x) < pitch / 2 ? axisPen : gridPen,
                        new Point(T(x, 0).X, 0), new Point(T(x, 0).X, ActualHeight));
        for (double y = gy0; y * _scale + _cy < ActualHeight; y += pitch)
            dc.DrawLine(Math.Abs(y) < pitch / 2 ? axisPen : gridPen,
                        new Point(0, T(0, y).Y), new Point(ActualWidth, T(0, y).Y));

        // body / silkscreen
        var bodyPen = new Pen(Brushes.LightYellow, 1);
        dc.DrawRectangle(null, bodyPen,
            new Rect(T(-_def.BodyWidthMm / 2, -_def.BodyHeightMm / 2), T(_def.BodyWidthMm / 2, _def.BodyHeightMm / 2)));

        var padBrush = new SolidColorBrush(Color.FromRgb(0xC8, 0x96, 0x32));
        var thBrush = new SolidColorBrush(Color.FromRgb(0x30, 0x30, 0x30));
        foreach (var p in _def.Pads)
        {
            dc.DrawRectangle(padBrush, null, new Rect(T(p.XMm - p.WMm / 2, p.YMm - p.HMm / 2), T(p.XMm + p.WMm / 2, p.YMm + p.HMm / 2)));
            if (p.ThroughHole && p.DrillMm > 0)
                dc.DrawEllipse(thBrush, null, T(p.XMm, p.YMm), p.DrillMm / 2 * _scale, p.DrillMm / 2 * _scale);
            if (_scale * Math.Min(p.WMm, p.HMm) > 9)
            {
                var label = new FormattedText(p.Name, System.Globalization.CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight, new Typeface("Consolas"), Math.Min(12, _scale * p.HMm * 0.5), Brushes.Black, 1.25);
                var c = T(p.XMm, p.YMm);
                dc.DrawText(label, new Point(c.X - label.Width / 2, c.Y - label.Height / 2));
            }
        }
        // pin-1 marker
        var p1 = _def.Pads.FirstOrDefault(p => p.Name == "1");
        if (p1 != null)
            dc.DrawEllipse(Brushes.White, null, T(p1.XMm - p1.WMm / 2 - 0.4, p1.YMm), 3, 3);

        // ---- measuring cursor ----
        if (_mouseWorld is Point mw)
        {
            var crossPen = new Pen(new SolidColorBrush(Color.FromArgb(150, 120, 200, 255)), 0.8);
            var sp = T(mw.X, mw.Y);
            dc.DrawLine(crossPen, new Point(sp.X, 0), new Point(sp.X, ActualHeight));
            dc.DrawLine(crossPen, new Point(0, sp.Y), new Point(ActualWidth, sp.Y));

            string text = $"{mw.X:F2}, {mw.Y:F2} mm";
            if (_measureA is Point a && (_measuring || (a - mw).Length > 1e-6))
            {
                var measurePen = new Pen(Brushes.Cyan, 1.5);
                dc.DrawLine(measurePen, T(a.X, a.Y), sp);
                dc.DrawEllipse(Brushes.Cyan, null, T(a.X, a.Y), 2.5, 2.5);
                double dx = mw.X - a.X, dy = mw.Y - a.Y;
                text = $"Δx {Math.Abs(dx):F3}  Δy {Math.Abs(dy):F3}  L {Math.Sqrt(dx * dx + dy * dy):F3} mm";
            }
            var readout = new FormattedText(text + $"   grid {pitch:0.##} mm",
                System.Globalization.CultureInfo.InvariantCulture, FlowDirection.LeftToRight,
                new Typeface("Consolas"), 12, Brushes.White, 1.25);
            dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(170, 0, 0, 0)), null,
                new Rect(4, ActualHeight - readout.Height - 8, readout.Width + 8, readout.Height + 4));
            dc.DrawText(readout, new Point(8, ActualHeight - readout.Height - 6));
        }
    }
}
