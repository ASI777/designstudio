using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using DesignStudio.Model;

namespace DesignStudio.Controls;

public enum EditorTool { Select, PlaceFootprint, RouteInteractive, AutoRoute, Measure }

/// <summary>
/// Interactive PCB editor canvas, built for mouse-first multi-layer workflows:
///   - wheel zoom at cursor, middle/right-drag pan, F zoom-to-fit
///   - hover highlighting of parts, traces and vias
///   - click select, Shift+click multi-select, marquee (drag on empty space)
///   - drag moves the whole selection live (snap-to-grid), R rotates, Del deletes
///   - placement ghost preview under the cursor (R rotates before placing)
///   - interactive routing on the active layer; V drops a via and switches layer
///     mid-route; double-click or Esc ends the chain
///   - active copper layer rendered bright, other layers dimmed
///   - PgUp/PgDn (or 1..9) change the active layer
///   - ratsnest overlay (unrouted connections) and detailed DRC markers
/// All edits are undoable via the document's UndoStack.
/// </summary>
public class PcbCanvas : FrameworkElement
{
    public BoardDocument? Document { get; set; }

    private EditorTool _tool = EditorTool.Select;
    public EditorTool Tool
    {
        get => _tool;
        set { _tool = value; _routeStart = null; _routeNetId = -1; _placeRotation = 0; InvalidateVisual(); }
    }

    public bool ShowRatsnest { get; set; } = true;

    private int _activeLayer;
    public int ActiveLayer
    {
        get => _activeLayer;
        set
        {
            int clamped = Math.Clamp(value, 0, (Document?.CopperLayers ?? 2) - 1);
            if (clamped == _activeLayer) return;
            _activeLayer = clamped;
            ActiveLayerChanged?.Invoke(_activeLayer);
            InvalidateVisual();
        }
    }

    public event Action<int>? ActiveLayerChanged;
    public event Action<string>? StatusChanged;
    public event Action<object?>? SelectionChanged;   // FootprintItem, TraceItem, ViaItem or null

    // copper layer palette (top, inner..., bottom get distinct hues)
    private static readonly Color[] LayerColors =
    {
        Color.FromRgb(0xE5, 0x4B, 0x4B),   // L1 red
        Color.FromRgb(0x4B, 0x7B, 0xE5),   // L2 blue
        Color.FromRgb(0x4B, 0xE5, 0x7B),   // green
        Color.FromRgb(0xE5, 0xA8, 0x3C),   // orange
        Color.FromRgb(0xC8, 0x5B, 0xE0),   // violet
        Color.FromRgb(0x3C, 0xD8, 0xD8),   // cyan
        Color.FromRgb(0xE0, 0xD0, 0x50),   // yellow
        Color.FromRgb(0xB0, 0xB0, 0xB0),   // grey
    };

    public static Color ColorOfLayer(int layer) => LayerColors[layer % LayerColors.Length];

    // view transform: screen = (world - offset) * scale  (world in mm, y-down)
    private double _scale = 8.0;            // pixels per mm
    private Point _offset = new(-10, -10);  // mm
    private Point _lastMouse;
    private bool _panning;
    private bool _maybeContextMenu;         // right button pressed, not yet dragged
    private Point _rightDownScreen;

    // dragging selection
    private bool _draggingSelection;
    private Point _dragAnchorWorld;
    private List<(FootprintItem fp, double oldX, double oldY, double oldRot)>? _dragStart;
    private double _validDx, _validDy;     // last collision-free drag delta

    // marquee
    private bool _marquee;
    private Point _marqueeStartWorld;

    // routing / placement
    private Point? _routeStart;
    private int _routeNetId = -1;          // net adopted from the start pad
    private double _placeRotation;
    private Point _mouseWorld;

    private List<DrcResultItem> _drcResults = new();

    // pending placement (set by the library panel)
    public Func<(string refDes, string lib, List<PadItem> pads, double heightMm)>? PendingFootprintFactory { get; set; }

    public PcbCanvas()
    {
        Focusable = true;
        ClipToBounds = true;
    }

    public void SetDrcResults(List<DrcResultItem> results) { _drcResults = results; InvalidateVisual(); }
    public void ClearDrcResults() { _drcResults.Clear(); InvalidateVisual(); }

    // ---- coordinate transforms ----
    private Point WorldToScreen(double xMm, double yMm) => new((xMm - _offset.X) * _scale, (yMm - _offset.Y) * _scale);
    private Point ScreenToWorld(Point s) => new(s.X / _scale + _offset.X, s.Y / _scale + _offset.Y);
    private double SnapMm(double v) => Document is null ? v : Math.Round(v / Document.GridMm) * Document.GridMm;

    public void ZoomToFit()
    {
        if (Document is null || ActualWidth < 10 || ActualHeight < 10) return;
        double margin = 6;
        double sx = ActualWidth / (Document.BoardWidthMm + margin * 2);
        double sy = ActualHeight / (Document.BoardHeightMm + margin * 2);
        _scale = Math.Clamp(Math.Min(sx, sy), 0.5, 400);
        _offset = new Point(-(ActualWidth / _scale - Document.BoardWidthMm) / 2,
                            -(ActualHeight / _scale - Document.BoardHeightMm) / 2);
        InvalidateVisual();
    }

    public void CenterOn(double xMm, double yMm, double? scale = null)
    {
        if (scale is double s) _scale = Math.Clamp(s, 0.5, 400);
        _offset = new Point(xMm - ActualWidth / (2 * _scale), yMm - ActualHeight / (2 * _scale));
        InvalidateVisual();
    }

    // ---- selection helpers ----
    private IEnumerable<FootprintItem> SelectedFootprints =>
        Document?.Footprints.Where(f => f.Selected) ?? Enumerable.Empty<FootprintItem>();
    private IEnumerable<TraceItem> SelectedTraces =>
        Document?.Traces.Where(t => t.Selected) ?? Enumerable.Empty<TraceItem>();
    private IEnumerable<ViaItem> SelectedVias =>
        Document?.Vias.Where(v => v.Selected) ?? Enumerable.Empty<ViaItem>();

    private void ClearSelection()
    {
        if (Document is null) return;
        foreach (var fp in Document.Footprints) fp.Selected = false;
        foreach (var t in Document.Traces) t.Selected = false;
        foreach (var v in Document.Vias) v.Selected = false;
    }

    public void SelectAll()
    {
        if (Document is null) return;
        foreach (var fp in Document.Footprints) fp.Selected = true;
        foreach (var t in Document.Traces.Where(t => !t.IsPour)) t.Selected = true;
        foreach (var v in Document.Vias) v.Selected = true;
        RaiseSelection();
        InvalidateVisual();
    }

    private void RaiseSelection()
    {
        object? primary = SelectedFootprints.FirstOrDefault();
        primary ??= SelectedTraces.FirstOrDefault();
        primary ??= SelectedVias.FirstOrDefault();
        SelectionChanged?.Invoke(primary);
    }

    // ---- input ----

    protected override void OnMouseWheel(MouseWheelEventArgs e)
    {
        var before = ScreenToWorld(e.GetPosition(this));
        _scale *= e.Delta > 0 ? 1.15 : 1 / 1.15;
        _scale = Math.Clamp(_scale, 0.5, 400);
        var after = ScreenToWorld(e.GetPosition(this));
        _offset = new Point(_offset.X + (before.X - after.X), _offset.Y + (before.Y - after.Y));
        InvalidateVisual();
    }

    protected override void OnMouseDown(MouseButtonEventArgs e)
    {
        Focus();
        _lastMouse = e.GetPosition(this);

        if (e.ChangedButton == MouseButton.Middle)
        {
            _panning = true; CaptureMouse(); return;
        }
        if (e.ChangedButton == MouseButton.Right)
        {
            // Right button: pan if dragged, context menu if released in place.
            _maybeContextMenu = true;
            _rightDownScreen = _lastMouse;
            _panning = true; CaptureMouse(); return;
        }
        if (Document is null || e.ChangedButton != MouseButton.Left) return;

        var w = ScreenToWorld(_lastMouse);

        if (e.ClickCount == 2 && Tool == EditorTool.RouteInteractive)
        {
            _routeStart = null;   // double-click ends the chain
            _routeNetId = -1;
            InvalidateVisual();
            return;
        }

        switch (Tool)
        {
            case EditorTool.Select:
                HandleSelectMouseDown(w);
                break;

            case EditorTool.PlaceFootprint:
                if (PendingFootprintFactory != null)
                {
                    var (refDes, lib, pads, heightMm) = PendingFootprintFactory();
                    Document.AddFootprint(refDes, lib, SnapMm(w.X), SnapMm(w.Y), pads, _placeRotation,
                                          0, heightMm);
                    StatusChanged?.Invoke($"Placed {refDes} at ({SnapMm(w.X):F2}, {SnapMm(w.Y):F2}) mm — R rotates, Esc to stop");
                }
                break;

            case EditorTool.RouteInteractive:
            {
                // snap to the pad under the cursor: trace endpoints must land on
                // pin centres and carry the pin's net to count as connected
                var padHit = Document.SnapToPad(w.X, w.Y, Math.Max(0.3, 4 / _scale));
                double ex = padHit?.X ?? SnapMm(w.X);
                double ey = padHit?.Y ?? SnapMm(w.Y);
                int padNet = padHit?.NetId ?? -1;

                if (_routeStart is null)
                {
                    _routeStart = new Point(ex, ey);
                    _routeNetId = padNet;
                    StatusChanged?.Invoke(padNet >= 0
                        ? $"Routing net {Document.NetName(padNet)} on {Document.LayerName(ActiveLayer)} — V drops a via"
                        : "Routing (no net yet) — start or end on a pad to bind its net");
                }
                else if (padNet >= 0 && _routeNetId >= 0 && padNet != _routeNetId)
                {
                    StatusChanged?.Invoke(
                        $"Refused: {Document.NetName(_routeNetId)} cannot join {Document.NetName(padNet)} (different nets)");
                }
                else
                {
                    if (_routeNetId < 0) _routeNetId = padNet;   // adopt net on landing
                    double width = RuleResolver.Width(Document, _routeNetId);
                    // push-and-shove: blockers bend out of the way when possible
                    var shove = ShoveRouter.Place(Document, _routeStart.Value.X, _routeStart.Value.Y,
                        ex, ey, width, _routeNetId, ActiveLayer);
                    if (!shove.Placed)
                    {
                        StatusChanged?.Invoke($"Blocked: {shove.Message} — route around or change layer (V)");
                        break;
                    }
                    if (shove.Shoved) StatusChanged?.Invoke($"Shove: {shove.Message}");
                    _routeStart = new Point(ex, ey); // continue chain
                }
                InvalidateVisual();
                break;
            }

            case EditorTool.AutoRoute:
            {
                var padHit = Document.SnapToPad(w.X, w.Y, Math.Max(0.3, 4 / _scale));
                var pt = padHit is { } ph ? new Point(ph.X, ph.Y) : w;
                int padNet = padHit?.NetId ?? -1;

                if (_routeStart is null)
                {
                    _routeStart = pt;
                    _routeNetId = padNet;
                    StatusChanged?.Invoke("Auto-route: click target point" +
                        (padNet >= 0 ? $" (net {Document.NetName(padNet)})" : ""));
                }
                else
                {
                    int net = _routeNetId >= 0 ? _routeNetId : padNet;
                    if (_routeNetId >= 0 && padNet >= 0 && padNet != _routeNetId)
                    {
                        StatusChanged?.Invoke(
                            $"Refused: {Document.NetName(_routeNetId)} cannot auto-route to {Document.NetName(padNet)} (different nets)");
                    }
                    else if (net >= 0 && TryRouteDiffPair(net, _routeStart.Value, pt)) { }
                    else
                    {
                        bool ok = Document.AutoRoute(_routeStart.Value.X, _routeStart.Value.Y, pt.X, pt.Y, net,
                                                     ActiveLayer, ActiveLayer);
                        StatusChanged?.Invoke(ok
                            ? $"Auto-route complete{(net >= 0 ? $" — net {Document.NetName(net)}" : "")}"
                            : $"Auto-route failed: {Document.LastRouteError}");
                    }
                    _routeStart = null;
                    _routeNetId = -1;
                }
                break;
            }
        }
    }

    /// <summary>
    /// If the net belongs to a 2-net class with a diff-pair gap, route both
    /// halves as a coupled pair: the partner's endpoints are the partner-net
    /// pads nearest to the clicked start/goal.
    /// </summary>
    private bool TryRouteDiffPair(int netId, Point start, Point goal)
    {
        if (Document is null) return false;
        var cls = Document.ClassFor(netId);
        if (cls.DiffPairGapMm <= 0 && cls.TargetDiffImpedanceOhm <= 0) return false;
        var members = Document.Nets.Where(n => n.ClassId == cls.Id).Select(n => n.Id).ToList();
        if (members.Count != 2) return false;
        int partner = members.First(id => id != netId);

        var partnerPads = Document.Footprints
            .SelectMany(fp => fp.Pads.Where(p => p.NetId == partner)
                                     .Select(p => fp.PadWorld(p)))
            .ToList();
        if (partnerPads.Count < 2) return false;
        var nStart = partnerPads.MinBy(p => (p.x - start.X) * (p.x - start.X) + (p.y - start.Y) * (p.y - start.Y));
        var nGoal = partnerPads.Where(p => p != nStart)
            .MinBy(p => (p.x - goal.X) * (p.x - goal.X) + (p.y - goal.Y) * (p.y - goal.Y));

        var res = DiffPairRouter.Route(Document,
            (start.X, start.Y), (goal.X, goal.Y), netId,
            nStart, nGoal, partner, ActiveLayer);
        StatusChanged?.Invoke(res.Ok ? $"Diff pair: {res.Message}" : $"Diff pair failed: {res.Message}");
        InvalidateVisual();
        return true;
    }

    private void HandleSelectMouseDown(Point w)
    {
        bool shift = Keyboard.Modifiers.HasFlag(ModifierKeys.Shift);
        var hitFp = HitTestFootprint(w);
        var hitVia = hitFp is null ? HitTestVia(w) : null;
        var hitTrace = hitFp is null && hitVia is null ? HitTestTrace(w) : null;

        if (hitFp != null)
        {
            if (shift) hitFp.Selected = !hitFp.Selected;
            else if (!hitFp.Selected) { ClearSelection(); hitFp.Selected = true; }
            if (hitFp.Selected)
            {
                _draggingSelection = true;
                _dragAnchorWorld = w;
                _dragStart = SelectedFootprints.Select(fp => (fp, fp.X, fp.Y, fp.RotationDeg)).ToList();
                _validDx = _validDy = 0;
                CaptureMouse();
            }
        }
        else if (hitVia != null)
        {
            if (shift) hitVia.Selected = !hitVia.Selected;
            else { ClearSelection(); hitVia.Selected = true; }
        }
        else if (hitTrace != null)
        {
            if (shift) hitTrace.Selected = !hitTrace.Selected;
            else { ClearSelection(); hitTrace.Selected = true; }
        }
        else
        {
            if (!shift) ClearSelection();
            _marquee = true;
            _marqueeStartWorld = w;
            CaptureMouse();
        }
        RaiseSelection();
        InvalidateVisual();
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        var pos = e.GetPosition(this);
        _mouseWorld = ScreenToWorld(pos);

        if (_panning)
        {
            if (_maybeContextMenu && (pos - _rightDownScreen).Length > 4) _maybeContextMenu = false;
            if (!_maybeContextMenu)
            {
                _offset = new Point(_offset.X - (pos.X - _lastMouse.X) / _scale,
                                    _offset.Y - (pos.Y - _lastMouse.Y) / _scale);
            }
            _lastMouse = pos;
            InvalidateVisual();
            return;
        }
        _lastMouse = pos;

        if (_draggingSelection && Document != null && _dragStart != null)
        {
            double dx = SnapMm(_mouseWorld.X - _dragAnchorWorld.X);
            double dy = SnapMm(_mouseWorld.Y - _dragAnchorWorld.Y);
            var dragSet = _dragStart.Select(d => d.fp).ToHashSet();
            foreach (var (fp, oldX, oldY, _) in _dragStart)
                Document.MoveFootprint(fp, SnapMm(oldX + dx), SnapMm(oldY + dy), fp.RotationDeg);
            if (Document.FootprintsCollide(dragSet))
            {
                // would overshadow a neighbour — stay at the last clear spot
                foreach (var (fp, oldX, oldY, _) in _dragStart)
                    Document.MoveFootprint(fp, SnapMm(oldX + _validDx), SnapMm(oldY + _validDy), fp.RotationDeg);
            }
            else { _validDx = dx; _validDy = dy; }
        }

        string layerName = Document?.LayerName(ActiveLayer) ?? "";
        StatusChanged?.Invoke($"X: {_mouseWorld.X:F2} mm   Y: {_mouseWorld.Y:F2} mm   Layer: {layerName}   Zoom: {_scale:F1}px/mm");

        // hover, ghost preview, rubber band and marquee all need repaints
        if (_marquee || _routeStart != null || Tool is EditorTool.PlaceFootprint or EditorTool.Select
            or EditorTool.RouteInteractive or EditorTool.AutoRoute)
            InvalidateVisual();
    }

    protected override void OnMouseUp(MouseButtonEventArgs e)
    {
        if (e.ChangedButton == MouseButton.Right && _maybeContextMenu)
        {
            _maybeContextMenu = false;
            _panning = false;
            ReleaseMouseCapture();
            OpenContextMenu();
            return;
        }
        if (e.ChangedButton is MouseButton.Middle or MouseButton.Right)
        {
            _maybeContextMenu = false;
            _panning = false;
            ReleaseMouseCapture();
            return;
        }

        if (_draggingSelection && Document != null && _dragStart != null)
        {
            if (_dragStart.Count == 1)
                Document.PushMoveUndo(_dragStart[0].fp, _dragStart[0].oldX, _dragStart[0].oldY, _dragStart[0].oldRot);
            else
                Document.PushGroupMoveUndo($"Move {_dragStart.Count} parts", _dragStart);

            // copper follows the pins: rip up and re-route the moved nets
            bool actuallyMoved = _dragStart.Any(d =>
                Math.Abs(d.fp.X - d.oldX) > 1e-9 || Math.Abs(d.fp.Y - d.oldY) > 1e-9);
            if (actuallyMoved)
                Document.RerouteFootprintNets(_dragStart.Select(d => d.fp).ToList());
        }
        _draggingSelection = false;
        _dragStart = null;

        if (_marquee && Document != null)
        {
            _marquee = false;
            var a = _marqueeStartWorld; var b = _mouseWorld;
            double minX = Math.Min(a.X, b.X), maxX = Math.Max(a.X, b.X);
            double minY = Math.Min(a.Y, b.Y), maxY = Math.Max(a.Y, b.Y);
            if (maxX - minX > 0.05 || maxY - minY > 0.05)
            {
                foreach (var fp in Document.Footprints)
                {
                    var (fminX, fminY, fmaxX, fmaxY) = fp.Bounds();
                    if (fminX >= minX && fmaxX <= maxX && fminY >= minY && fmaxY <= maxY) fp.Selected = true;
                }
                foreach (var t in Document.Traces)
                {
                    if (t.IsPour) continue;
                    if (Math.Min(t.Ax, t.Bx) >= minX && Math.Max(t.Ax, t.Bx) <= maxX &&
                        Math.Min(t.Ay, t.By) >= minY && Math.Max(t.Ay, t.By) <= maxY) t.Selected = true;
                }
                foreach (var v in Document.Vias)
                {
                    if (v.X >= minX && v.X <= maxX && v.Y >= minY && v.Y <= maxY) v.Selected = true;
                }
                RaiseSelection();
            }
            InvalidateVisual();
        }

        ReleaseMouseCapture();
    }

    /// <summary>Minimal modal text prompt (no external dependencies).</summary>
    private static string? PromptText(string title, string label, string initial)
    {
        var box = new TextBox { Text = initial, Margin = new Thickness(8, 4, 8, 8) };
        var ok = new Button { Content = "OK", Width = 70, IsDefault = true, Margin = new Thickness(8) };
        var panel = new StackPanel();
        panel.Children.Add(new TextBlock { Text = label, Margin = new Thickness(8, 8, 8, 0) });
        panel.Children.Add(box);
        panel.Children.Add(ok);
        var win = new Window
        {
            Title = title, Content = panel, Width = 280, SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = Application.Current.MainWindow, ResizeMode = ResizeMode.NoResize
        };
        ok.Click += (_, _) => win.DialogResult = true;
        box.Focus(); box.SelectAll();
        return win.ShowDialog() == true ? box.Text : null;
    }

    private void OpenContextMenu()
    {
        if (Document is null) return;
        var menu = new ContextMenu();

        bool hasSelection = SelectedFootprints.Any() || SelectedTraces.Any() || SelectedVias.Any();
        if (!hasSelection)
        {
            // Select what's under the cursor first.
            var hitFp = HitTestFootprint(_mouseWorld);
            var hitVia = hitFp is null ? HitTestVia(_mouseWorld) : null;
            var hitTr = hitFp is null && hitVia is null ? HitTestTrace(_mouseWorld) : null;
            if (hitFp != null) { hitFp.Selected = true; hasSelection = true; }
            else if (hitVia != null) { hitVia.Selected = true; hasSelection = true; }
            else if (hitTr != null) { hitTr.Selected = true; hasSelection = true; }
            if (hasSelection) { RaiseSelection(); InvalidateVisual(); }
        }

        if (hasSelection)
        {
            var rotate = new MenuItem { Header = "Rotate 90°  (R)" };
            rotate.Click += (_, _) => RotateSelection();
            menu.Items.Add(rotate);

            var del = new MenuItem { Header = "Delete  (Del)" };
            del.Click += (_, _) => DeleteSelection();
            menu.Items.Add(del);

            menu.Items.Add(new Separator());
        }

        // BGA fanout for a selected area-array footprint
        if (SelectedFootprints.FirstOrDefault(f => f.Pads.Count >= 16) is { } bga)
        {
            var fan = new MenuItem { Header = $"Generate BGA fanout for {bga.RefDes}" };
            fan.Click += (_, _) =>
            {
                var res = EscapePlanner.Fanout(Document, bga);
                StatusChanged?.Invoke(res.Ok ? $"Fanout: {res.Message}" : $"Fanout failed: {res.Message}");
                InvalidateVisual();
            };
            menu.Items.Add(fan);
            menu.Items.Add(new Separator());
        }

        // high-speed tools for the selected trace's net
        if (SelectedTraces.FirstOrDefault(t => t.NetId >= 0) is { } selTrace)
        {
            int netId = selTrace.NetId;
            var tune = new MenuItem { Header = $"Add serpentine to {Document.NetName(netId)}…" };
            tune.Click += (_, _) =>
            {
                string? input = PromptText("Serpentine tuning", "Length to add (mm):", "1.0");
                if (input != null && double.TryParse(input, out double mm) && mm > 0)
                {
                    var res = SerpentineTuner.AddLength(Document, netId, mm);
                    StatusChanged?.Invoke(res.Message);
                    InvalidateVisual();
                }
            };
            menu.Items.Add(tune);

            var si = new MenuItem { Header = $"SI report for {Document.NetName(netId)}" };
            si.Click += (_, _) =>
            {
                var rep = SiEstimator.Analyze(Document, netId);
                MessageBox.Show(SiEstimator.Format(rep), "Signal integrity estimate",
                    MessageBoxButton.OK, MessageBoxImage.Information);
            };
            menu.Items.Add(si);

            var sp = new MenuItem { Header = $"Export S-parameters (.s2p) for {Document.NetName(netId)}…" };
            sp.Click += (_, _) =>
            {
                var dlg = new Microsoft.Win32.SaveFileDialog
                {
                    Filter = "Touchstone 2-port (*.s2p)|*.s2p",
                    FileName = $"{Document.NetName(netId)}.s2p"
                };
                if (dlg.ShowDialog() != true) return;
                var data = ChannelExtractor.Extract(Document, netId);
                ChannelExtractor.WriteTouchstone(dlg.FileName, data, Document.NetName(netId));
                StatusChanged?.Invoke($"Wrote {data.Count} frequency points to {System.IO.Path.GetFileName(dlg.FileName)}");
            };
            menu.Items.Add(sp);

            var attach = new MenuItem { Header = $"Attach vendor S-parameter model to {Document.NetName(netId)}…" };
            attach.Click += (_, _) =>
            {
                var dlg = new Microsoft.Win32.OpenFileDialog
                { Filter = "Touchstone (*.s2p)|*.s2p|All files (*.*)|*.*", Title = "Attach S-parameter model" };
                if (dlg.ShowDialog() != true) return;
                var blk = new ChannelBlock { NetId = netId, FilePath = dlg.FileName };
                var parsed = blk.Parsed();
                if (parsed is null)
                { StatusChanged?.Invoke($"Could not read model: {blk.LoadError}"); return; }
                Document.ChannelBlocks.Add(blk);
                Document.NotifyChanged();
                StatusChanged?.Invoke($"Attached {parsed.Name} ({parsed.FreqHz.Length} points, " +
                    $"{parsed.FreqHz[0] / 1e6:F0}–{parsed.FreqHz[^1] / 1e9:F1} GHz) — included in SI report and .s2p export");
            };
            menu.Items.Add(attach);

            var ems = new MenuItem { Header = $"Export openEMS full-wave model for {Document.NetName(netId)}…" };
            ems.Click += (_, _) =>
            {
                var dlg = new Microsoft.Win32.SaveFileDialog
                { Filter = "openEMS Python script (*.py)|*.py", FileName = $"{Document.NetName(netId)}_openems.py" };
                if (dlg.ShowDialog() != true) return;
                try
                {
                    Export.OpenEmsExporter.Write(Document, netId, dlg.FileName);
                    StatusChanged?.Invoke($"Wrote {System.IO.Path.GetFileName(dlg.FileName)} — run with python-openems for full-wave S-parameters");
                }
                catch (Exception ex) { StatusChanged?.Invoke($"Export failed: {ex.Message}"); }
            };
            menu.Items.Add(ems);
            menu.Items.Add(new Separator());
        }

        if (_routeStart != null)
        {
            var via = new MenuItem { Header = "Drop via + switch layer  (V)" };
            via.Click += (_, _) => DropRoutingVia();
            menu.Items.Add(via);
            var cancel = new MenuItem { Header = "Cancel route  (Esc)" };
            cancel.Click += (_, _) => { _routeStart = null; InvalidateVisual(); };
            menu.Items.Add(cancel);
        }

        var fit = new MenuItem { Header = "Zoom to fit  (F)" };
        fit.Click += (_, _) => ZoomToFit();
        menu.Items.Add(fit);

        menu.PlacementTarget = this;
        menu.IsOpen = true;
    }

    private void RotateSelection()
    {
        if (Document is null) return;
        if (Tool == EditorTool.PlaceFootprint)
        {
            _placeRotation = (_placeRotation + 90) % 360;
            InvalidateVisual();
            return;
        }
        var sel = SelectedFootprints.ToList();
        if (sel.Count == 0) return;
        var before = sel.Select(fp => (fp, fp.X, fp.Y, fp.RotationDeg)).ToList();
        foreach (var fp in sel)
            Document.InternalMoveFootprint(fp, fp.X, fp.Y, (fp.RotationDeg + 90) % 360);
        Document.PushGroupMoveUndo(sel.Count == 1 ? $"Rotate {sel[0].RefDes}" : $"Rotate {sel.Count} parts", before);
        Document.RerouteFootprintNets(sel);   // pads moved — copper follows
    }

    private void DeleteSelection()
    {
        if (Document is null) return;
        var fps = SelectedFootprints.ToList();
        var trs = SelectedTraces.ToList();
        var vis = SelectedVias.ToList();
        if (fps.Count == 0 && trs.Count == 0 && vis.Count == 0) return;
        Document.RemoveItems(fps, trs, vis);
        SelectionChanged?.Invoke(null);
        StatusChanged?.Invoke($"Deleted {fps.Count + trs.Count + vis.Count} item(s) — Ctrl+Z to undo");
    }

    /// <summary>Mid-route via: drop a via at the current chain point, switch active layer.</summary>
    private void DropRoutingVia()
    {
        if (Document is null || _routeStart is null || Document.CopperLayers < 2) return;
        var cls = Document.ClassFor(_routeNetId);   // via geometry from the routed net's class
        int next = (ActiveLayer + 1) % Document.CopperLayers;
        int from, to;
        if (cls.AllowMicrovia && Math.Abs(next - ActiveLayer) == 1) { from = ActiveLayer; to = next; }
        else { from = 0; to = Document.CopperLayers - 1; }
        Document.AddViaDirect(_routeStart.Value.X, _routeStart.Value.Y, _routeNetId, from, to,
                              cls.ViaDiameterMm, cls.ViaDrillMm);
        ActiveLayer = next;
        StatusChanged?.Invoke($"Via placed — now routing on {Document.LayerName(ActiveLayer)}");
        InvalidateVisual();
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        if (Document is null) return;
        switch (e.Key)
        {
            case Key.Escape:
                _routeStart = null;
                _routeNetId = -1;
                if (Tool == EditorTool.PlaceFootprint) Tool = EditorTool.Select;
                InvalidateVisual();
                break;
            case Key.R:
                RotateSelection();
                break;
            case Key.V:
                if (Tool == EditorTool.RouteInteractive && _routeStart != null) DropRoutingVia();
                break;
            case Key.Delete:
                DeleteSelection();
                break;
            case Key.F:
                ZoomToFit();
                break;
            case Key.PageUp:
                ActiveLayer = ActiveLayer - 1;
                StatusChanged?.Invoke($"Active layer: {Document.LayerName(ActiveLayer)}");
                break;
            case Key.PageDown:
                ActiveLayer = ActiveLayer + 1;
                StatusChanged?.Invoke($"Active layer: {Document.LayerName(ActiveLayer)}");
                break;
            case >= Key.D1 and <= Key.D9 when Keyboard.Modifiers == ModifierKeys.None:
                ActiveLayer = e.Key - Key.D1;
                StatusChanged?.Invoke($"Active layer: {Document.LayerName(ActiveLayer)}");
                break;
            case Key.A when Keyboard.Modifiers.HasFlag(ModifierKeys.Control):
                SelectAll();
                break;
        }
    }

    // ---- hit testing ----

    private FootprintItem? HitTestFootprint(Point w)
    {
        if (Document is null) return null;
        for (int i = Document.Footprints.Count - 1; i >= 0; i--)
        {
            var fp = Document.Footprints[i];
            var (minX, minY, maxX, maxY) = fp.Bounds();
            if (w.X >= minX && w.X <= maxX && w.Y >= minY && w.Y <= maxY) return fp;
        }
        return null;
    }

    private TraceItem? HitTestTrace(Point w)
    {
        if (Document is null) return null;
        double tol = Math.Max(0.3, 4 / _scale);   // 4 px or 0.3 mm
        for (int i = Document.Traces.Count - 1; i >= 0; i--)
        {
            var t = Document.Traces[i];
            if (t.IsPour) continue;               // pours are regenerated, not hand-edited
            if (DistancePointSegment(w.X, w.Y, t.Ax, t.Ay, t.Bx, t.By) <= t.Width / 2 + tol) return t;
        }
        return null;
    }

    private ViaItem? HitTestVia(Point w)
    {
        if (Document is null) return null;
        double tol = Math.Max(0.2, 3 / _scale);
        for (int i = Document.Vias.Count - 1; i >= 0; i--)
        {
            var v = Document.Vias[i];
            double dx = w.X - v.X, dy = w.Y - v.Y;
            if (Math.Sqrt(dx * dx + dy * dy) <= v.DiameterMm / 2 + tol) return v;
        }
        return null;
    }

    private static double DistancePointSegment(double px, double py, double ax, double ay, double bx, double by)
    {
        double abx = bx - ax, aby = by - ay;
        double len2 = abx * abx + aby * aby;
        double t = len2 > 0 ? Math.Clamp(((px - ax) * abx + (py - ay) * aby) / len2, 0, 1) : 0;
        double cx = ax + t * abx - px, cy = ay + t * aby - py;
        return Math.Sqrt(cx * cx + cy * cy);
    }

    // ---- rendering ----

    protected override void OnRender(DrawingContext dc)
    {
        dc.DrawRectangle(new SolidColorBrush(Color.FromRgb(0x10, 0x14, 0x18)), null, new Rect(RenderSize));
        if (Document is null) return;

        DrawGrid(dc);
        DrawBoardOutline(dc);
        DrawPlanes(dc);
        if (ShowRatsnest) DrawRatsnest(dc);
        DrawTraces(dc);
        DrawVias(dc);
        DrawFootprints(dc);
        DrawGhost(dc);
        DrawRubberBand(dc);
        DrawMarquee(dc);
        DrawDrcMarkers(dc);
    }

    /// <summary>Polygon copper planes: filled with EvenOdd so Clipper holes render as voids.</summary>
    private void DrawPlanes(DrawingContext dc)
    {
        if (Document!.Planes.Count == 0) return;
        Document.PlanesUpToDate();
        foreach (var plane in Document.Planes)
        {
            bool active = plane.Layer == ActiveLayer;
            byte alpha = (byte)(active ? 90 : 30);
            var color = ColorOfLayer(plane.Layer);
            var brush = new SolidColorBrush(Color.FromArgb(alpha, color.R, color.G, color.B));
            var geo = new StreamGeometry { FillRule = FillRule.EvenOdd };
            using (var ctx = geo.Open())
            {
                foreach (var poly in plane.Fill)
                {
                    if (poly.Count < 3) continue;
                    ctx.BeginFigure(WorldToScreen(poly[0].x, poly[0].y), true, true);
                    for (int i = 1; i < poly.Count; i++)
                        ctx.LineTo(WorldToScreen(poly[i].x, poly[i].y), true, false);
                }
            }
            geo.Freeze();
            dc.DrawGeometry(brush, null, geo);
        }
    }

    private void DrawGrid(DrawingContext dc)
    {
        if (_scale * Document!.GridMm < 5) return; // too dense to draw
        var pen = new Pen(new SolidColorBrush(Color.FromArgb(40, 255, 255, 255)), 0.5);
        pen.Freeze();
        var tl = ScreenToWorld(new Point(0, 0));
        var br = ScreenToWorld(new Point(ActualWidth, ActualHeight));
        double g = Document.GridMm;
        for (double x = Math.Floor(tl.X / g) * g; x <= br.X; x += g)
            dc.DrawLine(pen, WorldToScreen(x, tl.Y), WorldToScreen(x, br.Y));
        for (double y = Math.Floor(tl.Y / g) * g; y <= br.Y; y += g)
            dc.DrawLine(pen, WorldToScreen(tl.X, y), WorldToScreen(br.X, y));
    }

    private void DrawBoardOutline(DrawingContext dc)
    {
        var pen = new Pen(Brushes.Goldenrod, 1.5);
        var fill = new SolidColorBrush(Color.FromArgb(18, 0, 120, 0));
        if (Document!.OutlinePolygon is { Count: >= 3 } poly)
        {
            var geo = new StreamGeometry();
            using (var ctx = geo.Open())
            {
                ctx.BeginFigure(WorldToScreen(poly[0][0], poly[0][1]), true, true);
                for (int i = 1; i < poly.Count; i++)
                    ctx.LineTo(WorldToScreen(poly[i][0], poly[i][1]), true, false);
            }
            geo.Freeze();
            dc.DrawGeometry(fill, pen, geo);     // rectangle, circle, or any custom outline
        }
        else
        {
            var p0 = WorldToScreen(0, 0);
            var p1 = WorldToScreen(Document.BoardWidthMm, Document.BoardHeightMm);
            dc.DrawRectangle(fill, pen, new Rect(p0, p1));
        }
    }

    private List<Ratsnest.Line>? _ratsnestCache;
    private long _ratsnestVersion = -1;

    private void DrawRatsnest(DrawingContext dc)
    {
        if (Document is null) return;
        if (_ratsnestCache is null || _ratsnestVersion != Document.Version)
        {
            _ratsnestCache = Ratsnest.Compute(Document);
            _ratsnestVersion = Document.Version;
        }
        var lines = _ratsnestCache;
        if (lines.Count == 0) return;
        var pen = new Pen(new SolidColorBrush(Color.FromArgb(150, 0, 220, 220)), 1) { DashStyle = DashStyles.Dash };
        foreach (var l in lines)
            dc.DrawLine(pen, WorldToScreen(l.Ax, l.Ay), WorldToScreen(l.Bx, l.By));
    }

    private Brush LayerBrush(int layer, bool isPour, bool selected, bool hovered)
    {
        if (selected) return Brushes.White;
        if (hovered) return Brushes.LightSalmon;
        var c = ColorOfLayer(layer);
        byte alpha = layer == ActiveLayer ? (byte)255 : (byte)80;
        if (isPour) alpha = layer == ActiveLayer ? (byte)110 : (byte)40;
        return new SolidColorBrush(Color.FromArgb(alpha, c.R, c.G, c.B));
    }

    private void DrawTraces(DrawingContext dc)
    {
        TraceItem? hover = Tool == EditorTool.Select && !_draggingSelection && !_marquee
            ? HitTestTrace(_mouseWorld) : null;

        // draw order: pours under traces, non-active layers under the active layer
        foreach (var pass in new[] { 0, 1, 2, 3 })
        foreach (var t in Document!.Traces)
        {
            bool active = t.Layer == ActiveLayer;
            int order = (t.IsPour ? 0 : 2) + (active ? 1 : 0);
            if (order != pass) continue;
            var brush = LayerBrush(t.Layer, t.IsPour, t.Selected, ReferenceEquals(t, hover));
            var pen = new Pen(brush, Math.Max(1, t.Width * _scale)) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round };
            dc.DrawLine(pen, WorldToScreen(t.Ax, t.Ay), WorldToScreen(t.Bx, t.By));
        }
    }

    private void DrawVias(DrawingContext dc)
    {
        if (Document!.Vias.Count == 0) return;
        var holeBrush = new SolidColorBrush(Color.FromRgb(0x10, 0x14, 0x18));
        foreach (var v in Document.Vias)
        {
            var centre = WorldToScreen(v.X, v.Y);
            double rOut = v.DiameterMm / 2 * _scale;
            double rIn = v.DrillMm / 2 * _scale;
            bool onActive = ActiveLayer >= v.FromLayer && ActiveLayer <= v.ToLayer;
            Brush ring = v.Selected
                ? Brushes.White
                : new SolidColorBrush(Color.FromArgb(onActive ? (byte)230 : (byte)100, 0xC0, 0xC0, 0xC8));
            dc.DrawEllipse(ring, null, centre, rOut, rOut);
            dc.DrawEllipse(holeBrush, null, centre, rIn, rIn);
            // blind/buried indicator: tick marks coloured by span ends
            if (!(v.FromLayer == 0 && v.ToLayer == Document.CopperLayers - 1))
            {
                var pen = new Pen(new SolidColorBrush(ColorOfLayer(v.FromLayer)), 1.5);
                dc.DrawLine(pen, new Point(centre.X - rOut, centre.Y), new Point(centre.X - rOut * 0.3, centre.Y));
                var pen2 = new Pen(new SolidColorBrush(ColorOfLayer(v.ToLayer)), 1.5);
                dc.DrawLine(pen2, new Point(centre.X + rOut * 0.3, centre.Y), new Point(centre.X + rOut, centre.Y));
            }
        }
    }

    private void DrawFootprints(DrawingContext dc)
    {
        var padBrush = new SolidColorBrush(Color.FromRgb(0xC8, 0x96, 0x32));
        var thBrush = new SolidColorBrush(Color.FromRgb(0xB0, 0xB0, 0xB8));
        FootprintItem? hover = Tool == EditorTool.Select && !_draggingSelection && !_marquee
            ? HitTestFootprint(_mouseWorld) : null;

        foreach (var fp in Document!.Footprints)
        {
            DrawFootprintPads(dc, fp, fp.X, fp.Y, fp.RotationDeg, padBrush, thBrush);

            var (minX, minY, maxX, maxY) = fp.Bounds();
            if (fp.Selected)
            {
                var selPen = new Pen(Brushes.White, 1) { DashStyle = DashStyles.Dash };
                dc.DrawRectangle(null, selPen, new Rect(WorldToScreen(minX - 0.3, minY - 0.3), WorldToScreen(maxX + 0.3, maxY + 0.3)));
            }
            else if (ReferenceEquals(fp, hover))
            {
                var hovPen = new Pen(new SolidColorBrush(Color.FromArgb(140, 255, 255, 255)), 1);
                dc.DrawRectangle(null, hovPen, new Rect(WorldToScreen(minX - 0.3, minY - 0.3), WorldToScreen(maxX + 0.3, maxY + 0.3)));
            }
            var label = new FormattedText(fp.RefDes, System.Globalization.CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface("Consolas"), Math.Max(8, _scale * 0.9), Brushes.LightGray, 1.25);
            dc.DrawText(label, WorldToScreen(minX, minY - 1.4));
        }

        // pin under the cursor: highlight ring + name/net tag so the user
        // always knows exactly which pin a click will hit
        if (Tool is EditorTool.Select or EditorTool.RouteInteractive or EditorTool.AutoRoute &&
            Document!.FindPad(_mouseWorld.X, _mouseWorld.Y, Math.Max(0.3, 4 / _scale)) is { } hit)
        {
            var (hfp, hpad) = hit;
            var (px, py) = hfp.PadWorld(hpad);
            double rr = (Math.Max(hpad.W, hpad.H) / 2 + 0.15) * _scale;
            var ringPen = new Pen(Brushes.Cyan, 2);
            dc.DrawEllipse(null, ringPen, WorldToScreen(px, py), rr, rr);

            string tag = $"{hfp.RefDes}.{hpad.Name}" +
                         (hpad.NetId >= 0 ? $" · {Document.NetName(hpad.NetId)}" : "");
            var tip = new FormattedText(tag, System.Globalization.CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight, new Typeface("Consolas"), 12, Brushes.Cyan, 1.25);
            var anchor = WorldToScreen(px, py);
            dc.DrawRectangle(new SolidColorBrush(Color.FromArgb(180, 0, 0, 0)), null,
                new Rect(anchor.X + 10, anchor.Y - tip.Height - 6, tip.Width + 8, tip.Height + 4));
            dc.DrawText(tip, new Point(anchor.X + 14, anchor.Y - tip.Height - 4));
        }
    }

    private void DrawFootprintPads(DrawingContext dc, FootprintItem fp, double atX, double atY, double rotDeg,
                                   Brush padBrush, Brush thBrush, double opacity = 1.0)
    {
        double r = rotDeg * Math.PI / 180, c = Math.Cos(r), s = Math.Sin(r);
        foreach (var pad in fp.Pads)
        {
            double px = atX + pad.X * c - pad.Y * s, py = atY + pad.X * s + pad.Y * c;
            var brush = pad.ThroughHole ? thBrush : padBrush;
            if (opacity < 1.0)
            {
                brush = brush.Clone();
                brush.Opacity = opacity;
            }

            double rotMod = Math.Abs(rotDeg % 180);
            if (rotMod < 0.01 || rotMod > 179.99)
            {
                var tl = WorldToScreen(px - pad.W / 2, py - pad.H / 2);
                var br2 = WorldToScreen(px + pad.W / 2, py + pad.H / 2);
                dc.DrawRectangle(brush, null, new Rect(tl, br2));
            }
            else if (Math.Abs(rotMod - 90) < 0.01)
            {
                // 90/270: swap w/h
                var tl = WorldToScreen(px - pad.H / 2, py - pad.W / 2);
                var br2 = WorldToScreen(px + pad.H / 2, py + pad.W / 2);
                dc.DrawRectangle(brush, null, new Rect(tl, br2));
            }
            else
            {
                // arbitrary rotation: exact rotated polygon
                var geo = new StreamGeometry();
                using (var ctx = geo.Open())
                {
                    var corners = new (double lx, double ly)[]
                        { (-pad.W/2, -pad.H/2), (pad.W/2, -pad.H/2), (pad.W/2, pad.H/2), (-pad.W/2, pad.H/2) };
                    bool first = true;
                    foreach (var (lx, ly) in corners)
                    {
                        var pt = WorldToScreen(px + lx * c - ly * s, py + lx * s + ly * c);
                        if (first) { ctx.BeginFigure(pt, true, true); first = false; }
                        else ctx.LineTo(pt, true, false);
                    }
                }
                geo.Freeze();
                dc.DrawGeometry(brush, null, geo);
            }

            if (pad.ThroughHole && pad.DrillMm > 0)
            {
                var centre = WorldToScreen(px, py);
                dc.DrawEllipse(new SolidColorBrush(Color.FromRgb(0x10, 0x14, 0x18)), null,
                    centre, pad.DrillMm / 2 * _scale, pad.DrillMm / 2 * _scale);
            }
        }
    }

    private void DrawGhost(DrawingContext dc)
    {
        if (Tool != EditorTool.PlaceFootprint || PendingFootprintFactory is null || Document is null) return;
        var (refDes, _, pads, _) = PendingFootprintFactory();
        var ghost = new FootprintItem { RefDes = refDes, Pads = pads, RotationDeg = _placeRotation };
        double gx = SnapMm(_mouseWorld.X), gy = SnapMm(_mouseWorld.Y);
        DrawFootprintPads(dc, ghost, gx, gy, _placeRotation,
            new SolidColorBrush(Color.FromRgb(0xC8, 0x96, 0x32)),
            new SolidColorBrush(Color.FromRgb(0xB0, 0xB0, 0xB8)), opacity: 0.45);
        // crosshair at the snap point
        var pen = new Pen(new SolidColorBrush(Color.FromArgb(120, 255, 255, 255)), 1);
        var p = WorldToScreen(gx, gy);
        dc.DrawLine(pen, new Point(p.X - 8, p.Y), new Point(p.X + 8, p.Y));
        dc.DrawLine(pen, new Point(p.X, p.Y - 8), new Point(p.X, p.Y + 8));
    }

    private void DrawRubberBand(DrawingContext dc)
    {
        if (_routeStart is null) return;
        var pen = new Pen(new SolidColorBrush(ColorOfLayer(ActiveLayer)), 1) { DashStyle = DashStyles.Dot };
        dc.DrawLine(pen, WorldToScreen(_routeStart.Value.X, _routeStart.Value.Y), WorldToScreen(_mouseWorld.X, _mouseWorld.Y));
    }

    private void DrawMarquee(DrawingContext dc)
    {
        if (!_marquee) return;
        var pen = new Pen(new SolidColorBrush(Color.FromArgb(180, 100, 180, 255)), 1) { DashStyle = DashStyles.Dash };
        var fill = new SolidColorBrush(Color.FromArgb(25, 100, 180, 255));
        dc.DrawRectangle(fill, pen, new Rect(WorldToScreen(_marqueeStartWorld.X, _marqueeStartWorld.Y),
                                             WorldToScreen(_mouseWorld.X, _mouseWorld.Y)));
    }

    private void DrawDrcMarkers(DrawingContext dc)
    {
        var pen = new Pen(Brushes.Red, 2);
        foreach (var v in _drcResults)
        {
            var p = WorldToScreen(v.X, v.Y);
            dc.DrawEllipse(null, pen, p, 8, 8);
            dc.DrawLine(pen, new Point(p.X - 6, p.Y - 6), new Point(p.X + 6, p.Y + 6));
            dc.DrawLine(pen, new Point(p.X - 6, p.Y + 6), new Point(p.X + 6, p.Y - 6));
        }
    }
}
