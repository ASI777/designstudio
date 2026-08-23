using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using DesignStudio.Model;

namespace DesignStudio;

/// <summary>
/// Schematic editor (foundation): place symbols from the library, draw wires
/// (pins snap), drop net labels, run ERC, and forward-annotate to the board.
/// Deliberately spartan — the value is the netlist flow, not the chrome.
/// </summary>
public class SchematicWindow : Window
{
    private readonly SchematicDocument _sch;
    private readonly FootprintLibrary _library;
    private readonly BoardDocument _board;
    private readonly SchCanvas _canvas;
    private readonly ComboBox _partCombo = new() { Width = 220, Margin = new Thickness(4) };
    private readonly TextBlock _status = new() { Margin = new Thickness(8, 4, 8, 4) };

    public SchematicWindow(SchematicDocument sch, FootprintLibrary library, BoardDocument board)
    {
        _sch = sch; _library = library; _board = board;
        Title = "Schematic — Design Studio";
        Width = 1000; Height = 700;
        Background = new SolidColorBrush(Color.FromRgb(0x14, 0x18, 0x1C));

        _canvas = new SchCanvas(sch) { Status = s => _status.Text = s };
        _partCombo.ItemsSource = library.Items;
        _partCombo.DisplayMemberPath = "Name";
        if (library.Items.Count > 0) _partCombo.SelectedIndex = 0;

        var place = Btn("Place part", () =>
        {
            if (_partCombo.SelectedItem is not FootprintDef def) return;
            int n = _sch.Components.Count(c => c.LibName == def.Name) + 1 + _sch.Components.Count;
            _canvas.BeginPlace(def, $"{def.RefDesPrefix}{n}");
        });
        var wire = Btn("Wire (W)", () => _canvas.Mode = SchMode.Wire);
        var label = Btn("Net label (L)", () => _canvas.Mode = SchMode.Label);
        var select = Btn("Select (Esc)", () => _canvas.Mode = SchMode.Select);
        var erc = Btn("Run ERC", () =>
        {
            var issues = _sch.RunErc();
            MessageBox.Show(issues.Count == 0 ? "ERC clean." : string.Join("\n", issues),
                "ERC", MessageBoxButton.OK,
                issues.Count == 0 ? MessageBoxImage.Information : MessageBoxImage.Warning);
        });
        var sync = Btn("Sync to Board →", () =>
        {
            var rep = _sch.SyncToBoard(_board, _library);
            MessageBox.Show(
                $"Added: {(rep.Added.Count == 0 ? "—" : string.Join(", ", rep.Added))}\n" +
                $"Updated: {rep.Updated.Count} pad binding(s)\n" +
                (rep.Warnings.Count > 0 ? "Warnings:\n" + string.Join("\n", rep.Warnings) : "No warnings."),
                "Forward annotation", MessageBoxButton.OK, MessageBoxImage.Information);
        });

        var bar = new StackPanel { Orientation = Orientation.Horizontal };
        bar.Children.Add(_partCombo);
        foreach (var b in new[] { place, wire, label, select, erc, sync }) bar.Children.Add(b);

        var root = new DockPanel();
        DockPanel.SetDock(bar, Dock.Top);
        DockPanel.SetDock(_status, Dock.Bottom);
        root.Children.Add(bar);
        root.Children.Add(_status);
        root.Children.Add(_canvas);
        Content = root;

        KeyDown += (_, e) =>
        {
            if (e.Key == Key.W) _canvas.Mode = SchMode.Wire;
            else if (e.Key == Key.L) _canvas.Mode = SchMode.Label;
            else if (e.Key == Key.Escape) _canvas.CancelToSelect();
            else if (e.Key == Key.Delete) _canvas.DeleteHovered();
        };
    }

    private static Button Btn(string text, Action click)
    {
        var b = new Button { Content = text, Margin = new Thickness(4), Padding = new Thickness(8, 2, 8, 2) };
        b.Click += (_, _) => click();
        return b;
    }
}

public enum SchMode { Select, Place, Wire, Label }

public class SchCanvas : FrameworkElement
{
    private readonly SchematicDocument _sch;
    public SchMode Mode { get; set; } = SchMode.Select;
    public Action<string>? Status;

    private const double Scale = 8;          // px per schematic unit
    private FootprintDef? _placingDef;
    private string _placingRef = "";
    private Point? _wireStart;
    private Point _mouse;
    private SchComponent? _dragging;
    private Point _dragOffset;

    public SchCanvas(SchematicDocument sch)
    {
        _sch = sch;
        _sch.Changed += InvalidateVisual;
        ClipToBounds = true;
    }

    public void BeginPlace(FootprintDef def, string refDes)
    {
        _placingDef = def; _placingRef = refDes; Mode = SchMode.Place;
        Status?.Invoke($"Click to place {refDes} ({def.Name})");
    }

    public void CancelToSelect() { Mode = SchMode.Select; _wireStart = null; _placingDef = null; InvalidateVisual(); }

    public void DeleteHovered()
    {
        var w = ToWorld(_mouse);
        var c = HitComponent(w);
        if (c != null) { _sch.Components.Remove(c); _sch.NotifyChanged(); return; }
        var wire = _sch.Wires.FirstOrDefault(wi => DistToWire(w, wi) < 0.6);
        if (wire != null) { _sch.Wires.Remove(wire); _sch.NotifyChanged(); }
    }

    private Point ToWorld(Point screen) => new(screen.X / Scale, screen.Y / Scale);
    private Point ToScreen(double x, double y) => new(x * Scale, y * Scale);
    private static double Snap(double v) => Math.Round(v / 1.25) * 1.25;

    /// <summary>Snap to a pin within reach, else to the grid.</summary>
    private Point SnapPoint(Point w)
    {
        foreach (var c in _sch.Components)
            foreach (var p in c.Pins)
                if (Math.Abs(c.X + p.DX - w.X) < 1 && Math.Abs(c.Y + p.DY - w.Y) < 1)
                    return new Point(c.X + p.DX, c.Y + p.DY);
        return new Point(Snap(w.X), Snap(w.Y));
    }

    private SchComponent? HitComponent(Point w) =>
        _sch.Components.FirstOrDefault(c =>
            Math.Abs(w.X - c.X) < c.Width / 2 && Math.Abs(w.Y - c.Y) < c.Height / 2);

    private static double DistToWire(Point p, SchWire w)
    {
        double dx = w.Bx - w.Ax, dy = w.By - w.Ay, l2 = dx * dx + dy * dy;
        double u = l2 < 1e-12 ? 0 : Math.Clamp(((p.X - w.Ax) * dx + (p.Y - w.Ay) * dy) / l2, 0, 1);
        double qx = w.Ax + u * dx - p.X, qy = w.Ay + u * dy - p.Y;
        return Math.Sqrt(qx * qx + qy * qy);
    }

    protected override void OnMouseDown(MouseButtonEventArgs e)
    {
        var w = ToWorld(e.GetPosition(this));
        switch (Mode)
        {
            case SchMode.Place when _placingDef != null:
                var comp = SchematicDocument.FromLibrary(_placingDef, _placingRef, Snap(w.X), Snap(w.Y));
                _sch.Components.Add(comp);
                _sch.NotifyChanged();
                Mode = SchMode.Select; _placingDef = null;
                Status?.Invoke($"Placed {comp.RefDes} — W wires pins, L names nets");
                break;

            case SchMode.Wire:
                var sp = SnapPoint(w);
                if (_wireStart is null) _wireStart = sp;
                else
                {
                    _sch.Wires.Add(new SchWire { Ax = _wireStart.Value.X, Ay = _wireStart.Value.Y, Bx = sp.X, By = sp.Y });
                    _sch.NotifyChanged();
                    _wireStart = sp;     // chain
                }
                break;

            case SchMode.Label:
                var lp = SnapPoint(w);
                var name = PromptName();
                if (!string.IsNullOrWhiteSpace(name))
                {
                    _sch.Labels.Add(new SchLabel { X = lp.X, Y = lp.Y, Name = name.Trim() });
                    _sch.NotifyChanged();
                }
                Mode = SchMode.Select;
                break;

            default:
                _dragging = HitComponent(w);
                if (_dragging != null)
                {
                    _dragOffset = new Point(w.X - _dragging.X, w.Y - _dragging.Y);
                    CaptureMouse();
                }
                break;
        }
        InvalidateVisual();
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        _mouse = e.GetPosition(this);
        if (_dragging != null)
        {
            var w = ToWorld(_mouse);
            _dragging.X = Snap(w.X - _dragOffset.X);
            _dragging.Y = Snap(w.Y - _dragOffset.Y);
            _sch.NotifyChanged();
        }
        if (Mode is SchMode.Wire or SchMode.Place || _dragging != null) InvalidateVisual();
    }

    protected override void OnMouseUp(MouseButtonEventArgs e)
    {
        if (e.ChangedButton == MouseButton.Right) { CancelToSelect(); return; }
        _dragging = null;
        ReleaseMouseCapture();
    }

    private static string? PromptName()
    {
        var box = new TextBox { Margin = new Thickness(8, 4, 8, 8) };
        var ok = new Button { Content = "OK", Width = 70, IsDefault = true, Margin = new Thickness(8) };
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = "Net name:", Margin = new Thickness(8, 8, 8, 0) });
        panel.Children.Add(box); panel.Children.Add(ok);
        var win = new Window
        {
            Title = "Net label", Content = panel, Width = 240, SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = Application.Current.MainWindow, ResizeMode = ResizeMode.NoResize
        };
        ok.Click += (_, _) => win.DialogResult = true;
        box.Focus();
        return win.ShowDialog() == true ? box.Text : null;
    }

    protected override void OnRender(DrawingContext dc)
    {
        dc.DrawRectangle(new SolidColorBrush(Color.FromRgb(0x14, 0x18, 0x1C)), null, new Rect(RenderSize));

        // dot grid
        var gridBrush = new SolidColorBrush(Color.FromArgb(60, 255, 255, 255));
        for (double x = 0; x < ActualWidth; x += 1.25 * Scale)
            for (double y = 0; y < ActualHeight; y += 1.25 * Scale)
                dc.DrawRectangle(gridBrush, null, new Rect(x, y, 1, 1));

        var wirePen = new Pen(Brushes.MediumSpringGreen, 1.5);
        foreach (var w in _sch.Wires)
            dc.DrawLine(wirePen, ToScreen(w.Ax, w.Ay), ToScreen(w.Bx, w.By));

        var bodyPen = new Pen(Brushes.Goldenrod, 1.2);
        var pinPen = new Pen(Brushes.LightSkyBlue, 1);
        foreach (var c in _sch.Components)
        {
            var tl = ToScreen(c.X - c.Width / 2, c.Y - c.Height / 2);
            var br = ToScreen(c.X + c.Width / 2, c.Y + c.Height / 2);
            dc.DrawRectangle(null, bodyPen, new Rect(tl, br));
            var refText = new FormattedText(c.RefDes, System.Globalization.CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface("Consolas"), 12, Brushes.Goldenrod, 1.25);
            dc.DrawText(refText, new Point(tl.X, tl.Y - 16));
            foreach (var p in c.Pins)
            {
                var pinEnd = ToScreen(c.X + p.DX, c.Y + p.DY);
                var pinRoot = ToScreen(c.X + p.DX * 0.7, c.Y + p.DY);
                dc.DrawLine(pinPen, pinRoot, pinEnd);
                dc.DrawEllipse(Brushes.LightSkyBlue, null, pinEnd, 2, 2);
                var nameText = new FormattedText(p.Name, System.Globalization.CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight, new Typeface("Consolas"), 9, Brushes.Gray, 1.25);
                dc.DrawText(nameText, new Point(
                    p.DX < 0 ? pinEnd.X + 4 : pinEnd.X - nameText.Width - 4, pinEnd.Y - 11));
            }
        }

        var labelBrush = Brushes.Orange;
        foreach (var l in _sch.Labels)
        {
            var pt = ToScreen(l.X, l.Y);
            var t = new FormattedText(l.Name, System.Globalization.CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface("Consolas"), 11, labelBrush, 1.25);
            dc.DrawText(t, new Point(pt.X + 3, pt.Y - 14));
            dc.DrawEllipse(labelBrush, null, pt, 2, 2);
        }

        if (Mode == SchMode.Wire && _wireStart is Point ws)
        {
            var rubber = new Pen(new SolidColorBrush(Color.FromArgb(150, 0, 255, 160)), 1)
            { DashStyle = DashStyles.Dash };
            var snap = SnapPoint(ToWorld(_mouse));
            dc.DrawLine(rubber, ToScreen(ws.X, ws.Y), ToScreen(snap.X, snap.Y));
        }
        if (Mode == SchMode.Place && _placingDef != null)
        {
            var w = ToWorld(_mouse);
            var ghost = new Pen(new SolidColorBrush(Color.FromArgb(120, 218, 165, 32)), 1);
            dc.DrawRectangle(null, ghost, new Rect(ToScreen(Snap(w.X) - 6, Snap(w.Y) - 4),
                                                   ToScreen(Snap(w.X) + 6, Snap(w.Y) + 4)));
        }
    }
}
