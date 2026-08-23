using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Media3D;
using System.Windows.Threading;
using DesignStudio.Controls;
using DesignStudio.Export;
using DesignStudio.Model;
using Microsoft.Win32;

namespace DesignStudio;

public partial class MainWindow : Window
{
    private BoardDocument _doc = new();
    private FootprintLibrary _library = FootprintLibrary.Load();
    private readonly DispatcherTimer _autosaveTimer;

    // 3D orbit camera state
    private double _orbitAz = -35, _orbitEl = 55, _orbitDist = 160;
    private bool _orbiting;
    private Point _orbitLast;

    public MainWindow()
    {
        InitializeComponent();
        WireUp();
        RefreshFootprintCombo();

        _autosaveTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(120) };
        _autosaveTimer.Tick += (_, _) => Autosave();
        _autosaveTimer.Start();

        Loaded += (_, _) => Pcb.ZoomToFit();
    }

    private void WireUp()
    {
        Pcb.Document = _doc;
        Pcb.StatusChanged -= OnPcbStatus;
        Pcb.StatusChanged += OnPcbStatus;
        Pcb.SelectionChanged -= OnCanvasSelection;
        Pcb.SelectionChanged += OnCanvasSelection;
        Pcb.ActiveLayerChanged -= OnActiveLayerChanged;
        Pcb.ActiveLayerChanged += OnActiveLayerChanged;
        Pcb.PendingFootprintFactory = MakeSelectedFootprint;
        _doc.Changed += () => { Pcb.InvalidateVisual(); UpdateTitle(); AutoConfigureStackup(); };
        _doc.Undo.Changed += UpdateUndoMenu;
        RefreshLayerCombo();
        UpdateTitle();
        UpdateUndoMenu();
    }

    private void OnPcbStatus(string s) => Dispatcher.Invoke(() => StatusText.Text = s);

    private void OnCanvasSelection(object? item)
    {
        PropText.Text = item switch
        {
            FootprintItem fp =>
                $"{fp.RefDes}  ({fp.LibName})\nX: {fp.X:F2} mm  Y: {fp.Y:F2} mm\nRotation: {fp.RotationDeg}°\nSide: {(fp.Side == 0 ? "Top" : "Bottom")}\nPads: {fp.Pads.Count}\n\n" +
                "Drag to move · R rotate · Del delete\nShift+click multi-select · drag empty area for marquee",
            TraceItem t =>
                $"Trace on {_doc.LayerName(t.Layer)}\n({t.Ax:F2}, {t.Ay:F2}) → ({t.Bx:F2}, {t.By:F2})\nWidth: {t.Width:F2} mm · Segment: {t.LengthMm:F2} mm\nNet: {_doc.NetName(t.NetId)}" +
                (t.NetId >= 0 ? $" · routed {_doc.NetLengthMm(t.NetId):F2} mm" : "") +
                $"\nZ₀ ≈ {ImpedanceEngine.TraceZ0(_doc, t):F1} Ω ({(ImpedanceEngine.Reference(_doc, t.Layer).microstrip ? "microstrip" : "stripline")})" +
                $"\nSegment delay: {DelayEngine.SegmentDelayPs(_doc, t):F1} ps" +
                (t.NetId >= 0 ? $" · net total {DelayEngine.NetDelayPs(_doc, t.NetId):F1} ps" : "") +
                $"\nClass: {_doc.ClassFor(t.NetId).Name}\n\nDel deletes",
            ViaItem v =>
                $"Via {(v.FromLayer == 0 && v.ToLayer == _doc.CopperLayers - 1 ? "(through)" : v.ToLayer - v.FromLayer == 1 ? "(micro)" : "(blind/buried)")}\n" +
                $"{_doc.LayerName(v.FromLayer)} → {_doc.LayerName(v.ToLayer)}\nØ {v.DiameterMm:F2} mm, drill {v.DrillMm:F2} mm\nNet: {_doc.NetName(v.NetId)}\n\nDel deletes",
            _ => "Nothing selected"
        };
    }

    private void UpdateTitle()
    {
        string name = _doc.FilePath is null ? "untitled" : Path.GetFileNameWithoutExtension(_doc.FilePath);
        Title = $"Design Studio — {name}{(_doc.Dirty ? " *" : "")} · {_doc.CopperLayers} copper layers";
    }

    private void UpdateUndoMenu()
    {
        MenuUndo.Header = _doc.Undo.CanUndo ? $"Undo {_doc.Undo.NextUndoName}" : "Undo";
        MenuUndo.IsEnabled = _doc.Undo.CanUndo;
        MenuRedo.Header = _doc.Undo.CanRedo ? $"Redo {_doc.Undo.NextRedoName}" : "Redo";
        MenuRedo.IsEnabled = _doc.Undo.CanRedo;
    }

    // ---- layers ----

    private bool _layerComboUpdating;
    private bool _stackupAutoRunning;
    private int _lastFootprintCount;

    /// <summary>
    /// Self-configuring stackup: whenever parts are added, recompute the layer
    /// count the densest component + SI requirements demand and grow the
    /// stackup if needed. Never shrinks automatically (existing copper could
    /// reference inner layers the user wants to keep).
    /// </summary>
    private void AutoConfigureStackup()
    {
        if (_stackupAutoRunning || _doc.Footprints.Count == _lastFootprintCount) return;
        _lastFootprintCount = _doc.Footprints.Count;
        var plan = StackupPlanner.Recommend(_doc);
        if (plan.Layers <= _doc.CopperLayers) return;
        _stackupAutoRunning = true;
        try
        {
            _doc.SetCopperLayers(plan.Layers);
            RefreshLayerCombo();
            StatusText.Text = $"Stackup auto-configured to {plan.Layers} copper layers — " +
                              string.Join(" ", plan.Rationale.Take(plan.Rationale.Count - 1));
        }
        finally { _stackupAutoRunning = false; }
    }

    private void RefreshLayerCombo()
    {
        _layerComboUpdating = true;
        LayerCombo.Items.Clear();
        for (int i = 0; i < _doc.CopperLayers; i++)
            LayerCombo.Items.Add(_doc.LayerName(i));
        LayerCombo.SelectedIndex = Math.Clamp(Pcb.ActiveLayer, 0, _doc.CopperLayers - 1);
        _layerComboUpdating = false;
    }

    private void OnLayerSelected(object sender, SelectionChangedEventArgs e)
    {
        if (_layerComboUpdating || LayerCombo.SelectedIndex < 0) return;
        Pcb.ActiveLayer = LayerCombo.SelectedIndex;
        Pcb.InvalidateVisual();
    }

    private void OnActiveLayerChanged(int layer)
    {
        _layerComboUpdating = true;
        if (layer < LayerCombo.Items.Count) LayerCombo.SelectedIndex = layer;
        _layerComboUpdating = false;
    }

    private void OnBoardSetup(object sender, RoutedEventArgs e)
    {
        var dlg = new BoardSetupDialog(_doc) { Owner = this };
        if (dlg.ShowDialog() != true) return;
        if (dlg.Shape == BoardShape.Circle)
            _doc.SetBoardShape(BoardShape.Circle, dlg.BoardWidthMm);
        else if (dlg.Shape == BoardShape.RoundedRectangle)
            _doc.SetBoardShape(BoardShape.RoundedRectangle, dlg.BoardWidthMm, dlg.BoardHeightMm, dlg.CornerRadiusMm);
        else
            _doc.SetBoardSize(dlg.BoardWidthMm, dlg.BoardHeightMm);
        _doc.GridMm = dlg.GridMm;
        _doc.DielectricEr = dlg.DielectricEr;
        _doc.DielectricHeightMm = dlg.DielectricHeightMm;
        _doc.DielectricLossTangent = dlg.DielectricLossTangent;
        _doc.CopperThicknessMm = dlg.CopperThicknessMm;
        _doc.SetCopperLayers(dlg.CopperLayers);
        RefreshLayerCombo();
        Pcb.ZoomToFit();
        StatusText.Text = $"Board {dlg.BoardWidthMm:F0}×{dlg.BoardHeightMm:F0} mm, {dlg.CopperLayers} copper layers";
    }

    private void OnPhysicsCalculator(object sender, RoutedEventArgs e)
        => new PhysicsCalculatorDialog(_doc) { Owner = this }.ShowDialog();

    private void OnRouteAll(object sender, RoutedEventArgs e)
    {
        var pending = BatchRouter.UnroutedConnections(_doc);
        if (pending.Count == 0) { StatusText.Text = "Nothing to route — all nets are connected."; return; }
        StatusText.Text = $"Batch routing {pending.Count} connection(s)…";
        var result = BatchRouter.RouteAll(_doc, progress: s => StatusText.Text = $"Route All: {s}");
        Pcb.InvalidateVisual();
        StatusText.Text = result.Failed == 0
            ? $"Route All: {result.Routed} connection(s) routed in {result.Iterations} pass(es)"
            : $"Route All: {result.Routed} routed, {result.Failed} open after {result.Iterations} pass(es) — " +
              "ratsnest shows the rest (try more layers or wider spacing)";
    }

    private SchematicDocument? _schematic;

    private void OnOpenSchematic(object sender, RoutedEventArgs e)
    {
        if (_schematic is null)
        {
            string? schPath = _doc.FilePath is null ? null : SchematicDocument.PathFor(_doc.FilePath);
            _schematic = schPath != null && File.Exists(schPath)
                ? SchematicDocument.Deserialize(File.ReadAllText(schPath), _library)
                : new SchematicDocument();
            _schematic.Changed += () =>
            {
                if (_doc.FilePath is string p)
                    try { File.WriteAllText(SchematicDocument.PathFor(p), _schematic!.Serialize()); }
                    catch { /* autosave best-effort */ }
            };
        }
        new SchematicWindow(_schematic, _library, _doc) { Owner = this }.Show();
    }

    private void OnNetClasses(object sender, RoutedEventArgs e)
    {
        new NetClassesDialog(_doc) { Owner = this }.ShowDialog();
        Pcb.InvalidateVisual();
    }

    private void OnGeneratePour(object sender, RoutedEventArgs e)
    {
        if (_doc.Nets.Count == 0)
        {
            StatusText.Text = "Pour needs a net — assign pads to a net (e.g. GND) first";
            return;
        }
        var dlg = new PourDialog(_doc, Pcb.ActiveLayer) { Owner = this };
        if (dlg.ShowDialog() != true) return;
        // Native scanline pour: real copper that lives in the connectivity model,
        // so same-net pads and (power-fanout) vias bond to it and the
        // "unconnected net island" DRC clears. (A draw-only polygon plane is not
        // seen by the native island/connectivity check.)
        int segs = _doc.GeneratePour(dlg.NetId, dlg.Layer, dlg.LineWidthMm, dlg.ClearanceMm);
        Pcb.InvalidateVisual();
        StatusText.Text = segs > 0
            ? $"Poured {_doc.NetName(dlg.NetId)} on {_doc.LayerName(dlg.Layer)} ({segs} fill segments) — Ctrl+Z to undo"
            : "Pour produced no copper (no same-net pads on this layer, or the region is blocked).";
    }

    // ---- placement from the library ----
    private (string refDes, string lib, List<PadItem> pads, double heightMm) MakeSelectedFootprint()
    {
        var def = FootprintCombo.SelectedItem as FootprintDef ?? _library.Items.First();
        int n = _doc.Footprints.Count(f => f.RefDes.StartsWith(def.RefDesPrefix)) + 1;
        return ($"{def.RefDesPrefix}{n}", def.Name, def.ToPadItems(), def.HeightMm);
    }

    private void RefreshFootprintCombo()
    {
        var prev = (FootprintCombo.SelectedItem as FootprintDef)?.Name;
        FootprintCombo.ItemsSource = _library.Items.ToList();
        FootprintCombo.SelectedIndex = Math.Max(0,
            _library.Items.FindIndex(f => f.Name == prev));
    }

    private void OnOpenLibrary(object sender, RoutedEventArgs e)
    {
        var win = new LibraryWindow(_library) { Owner = this };
        bool? placed = win.ShowDialog();
        RefreshFootprintCombo();
        if (placed == true && win.SelectedForPlacement is FootprintDef def)
        {
            FootprintCombo.SelectedItem = _library.Items.FirstOrDefault(f => f.Name == def.Name);
            Pcb.Tool = EditorTool.PlaceFootprint;
            StatusText.Text = $"Click on the board to place {def.Name} — R rotates the ghost";
        }
    }

    private void OnSeedLibrary(object sender, RoutedEventArgs e)
    {
        int added = ParametricFootprints.SeedInto(_library);
        RefreshFootprintCombo();
        StatusText.Text = added > 0
            ? $"Added {added} parametric footprints (IPC-7351-style) to the library"
            : "Standard footprint set already present";
    }

    // ---- file ----

    private bool ConfirmDiscard()
    {
        if (!_doc.Dirty) return true;
        var r = MessageBox.Show("The board has unsaved changes. Save first?",
            "Design Studio", MessageBoxButton.YesNoCancel, MessageBoxImage.Warning);
        if (r == MessageBoxResult.Cancel) return false;
        if (r == MessageBoxResult.Yes) { OnSaveProject(this, new RoutedEventArgs()); return !_doc.Dirty; }
        return true;
    }

    private void ReplaceDocument(BoardDocument newDoc)
    {
        var old = _doc;
        _doc = newDoc;
        WireUp();
        old.Dispose();
        Pcb.ClearDrcResults();
        DrcList.ItemsSource = null;
        NetList.ItemsSource = null;
        Pcb.InvalidateVisual();
        Pcb.ZoomToFit();
    }

    private void OnNewBoard(object sender, RoutedEventArgs e)
    {
        if (!ConfirmDiscard()) return;
        ReplaceDocument(new BoardDocument());
        StatusText.Text = "New board";
    }

    private void OnOpenProject(object sender, RoutedEventArgs e)
    {
        if (!ConfirmDiscard()) return;
        var dlg = new OpenFileDialog { Filter = ProjectIO.Filter, DefaultExt = ProjectIO.Extension };
        if (dlg.ShowDialog(this) != true) return;
        try
        {
            string path = dlg.FileName;
            string auto = ProjectIO.AutosavePath(path);
            if (File.Exists(auto) && File.GetLastWriteTimeUtc(auto) > File.GetLastWriteTimeUtc(path))
            {
                var r = MessageBox.Show("A newer autosave exists for this project. Recover it?",
                    "Design Studio", MessageBoxButton.YesNo, MessageBoxImage.Question);
                if (r == MessageBoxResult.Yes) path = auto;
            }
            var doc = ProjectIO.Load(path);
            doc.FilePath = dlg.FileName;             // even when recovering, save back to the project
            ReplaceDocument(doc);
            StatusText.Text = $"Opened {Path.GetFileName(dlg.FileName)}: {_doc.Footprints.Count} parts, " +
                              $"{_doc.Traces.Count(t => !t.IsPour)} traces, {_doc.Vias.Count} vias, {_doc.CopperLayers} layers";
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Could not open project:\n{ex.Message}", "Design Studio",
                MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void OnSaveProject(object sender, RoutedEventArgs e)
    {
        if (_doc.FilePath is null) { OnSaveProjectAs(sender, e); return; }
        try
        {
            ProjectIO.Save(_doc, _doc.FilePath);
            UpdateTitle();
            StatusText.Text = $"Saved {Path.GetFileName(_doc.FilePath)}";
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Save failed:\n{ex.Message}", "Design Studio", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private void OnSaveProjectAs(object sender, RoutedEventArgs e)
    {
        var dlg = new SaveFileDialog { Filter = ProjectIO.Filter, DefaultExt = ProjectIO.Extension, FileName = "board" };
        if (dlg.ShowDialog(this) != true) return;
        _doc.FilePath = dlg.FileName;
        OnSaveProject(sender, e);
    }

    private void Autosave()
    {
        if (!_doc.Dirty || _doc.FilePath is null) return;
        try { ProjectIO.Save(_doc, ProjectIO.AutosavePath(_doc.FilePath), setAsProjectPath: false); }
        catch { /* autosave must never interrupt the user */ }
    }

    private void OnExportFab(object sender, RoutedEventArgs e)
    {
        var dlg = new OpenFolderDialog { Title = "Choose a folder for the fabrication output package" };
        if (dlg.ShowDialog(this) != true) return;
        try
        {
            string baseName = _doc.FilePath is null ? "board" : Path.GetFileNameWithoutExtension(_doc.FilePath);
            var result = FabExporter.ExportAll(_doc, dlg.FolderName, baseName);
            StatusText.Text = $"Exported {result.FilesWritten.Count} fabrication files to {dlg.FolderName}";
            MessageBox.Show("Fabrication package written:\n\n" +
                string.Join("\n", result.FilesWritten.Select(Path.GetFileName)) +
                $"\n\nGerber RS-274X ({_doc.CopperLayers} copper layers, mask, paste, edge), " +
                "Excellon drill files (per via span), BOM, pick-and-place.",
                "Export complete", MessageBoxButton.OK, MessageBoxImage.Information);
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Export failed:\n{ex.Message}", "Design Studio", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    /// <summary>
    /// Writes the design-studio.circuit-state/1 file: the full electrical
    /// picture of the placed circuit (components with datasheet payloads, net
    /// membership, connectivity, DRC/ERC, unconnected pins, physics) plus the
    /// embedded response contract — attach it to Gemini/Claude together with
    /// docs/circuit-advisor/ADVISOR_PROMPT.md to get add/remove/replace advice.
    /// </summary>
    private void OnExportCircuitState(object sender, RoutedEventArgs e)
    {
        var goalDlg = new DesignGoalDialog { Owner = this };
        if (goalDlg.ShowDialog() != true) return;
        var dlg = new SaveFileDialog
        {
            Filter = "Circuit state JSON (*.json)|*.json",
            FileName = (_doc.FilePath is null ? "board" : Path.GetFileNameWithoutExtension(_doc.FilePath)) + "-circuit-state"
        };
        if (dlg.ShowDialog(this) != true) return;
        try
        {
            File.WriteAllText(dlg.FileName, CircuitStateExport.Build(_doc, goalDlg.Goal, _library));
            StatusText.Text = $"Circuit state exported: {_doc.Footprints.Count} components, " +
                              $"{_doc.Nets.Count} nets → {Path.GetFileName(dlg.FileName)} (attach to your LLM with the advisor prompt)";
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Export failed:\n{ex.Message}", "Design Studio", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    // Closes the AI loop: load a design-studio.advice/1 file and apply it —
    // bundle each placed part's power rails into nets, create the diff-pair net
    // classes, and wire the advisor's pin→net connections. (See AdviceApplier.)
    private void OnApplyAdvice(object sender, RoutedEventArgs e)
    {
        var dlg = new OpenFileDialog { Filter = "Advice JSON (*.json)|*.json" };
        if (dlg.ShowDialog(this) != true) return;
        try
        {
            var report = Model.Advisor.AdviceContract.Parse(File.ReadAllText(dlg.FileName));
            var r = Model.Advisor.AdviceApplier.Apply(_doc, report, _library);
            Pcb.InvalidateVisual();
            StatusText.Text = $"Applied advice: {r.RailsBundled} power rails bundled, " +
                              $"{r.PadsAssigned} pads assigned, {r.NetsCreated} nets and " +
                              $"{r.NetClassesCreated} net classes created.";
            if (r.Log.Count > 0)
                MessageBox.Show(string.Join("\n", r.Log), "Apply Advice — notes",
                                MessageBoxButton.OK, MessageBoxImage.Information);
        }
        catch (Model.Advisor.AdviceFormatException ex)
        {
            MessageBox.Show($"Not a valid advice file:\n{ex.Message}", "Design Studio",
                            MessageBoxButton.OK, MessageBoxImage.Warning);
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Apply Advice failed:\n{ex.Message}", "Design Studio",
                            MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    // Bond every power/ground ball to its plane with a via-in-pad, clearing the
    // "unconnected net island" DRC items the planes otherwise leave. (PowerFanout.)
    private void OnFanoutPowerVias(object sender, RoutedEventArgs e)
    {
        if (_doc.Planes.Count == 0)
        {
            StatusText.Text = "Fanout needs planes — create GND/power planes first (Board → Generate Copper Pour…)";
            return;
        }
        var r = PowerFanout.Run(_doc);
        Pcb.InvalidateVisual();
        StatusText.Text = $"Power fanout: {r.ViasPlaced} vias placed across {r.NetsFannedOut} planed net(s)" +
                          (r.BallsAlreadyConnected > 0 ? $", {r.BallsAlreadyConnected} already bonded" : "") +
                          " — run DRC (Ctrl+D) to confirm islands cleared.";
        if (r.Log.Count > 0)
            MessageBox.Show(string.Join("\n", r.Log), "Power fanout — notes",
                            MessageBoxButton.OK, MessageBoxImage.Information);
    }

    // One-click HDI power optimization: tighten clearance, fan out, re-pour with
    // a thin fill stroke, and report the irreducible center-ball islands left for
    // detailed routing. (PowerOptimizer.)
    private void OnOptimizePower(object sender, RoutedEventArgs e)
    {
        var r = PowerOptimizer.Run(_doc);
        Pcb.InvalidateVisual();
        string left = r.RemainingIslands.Count == 0
            ? "all power nets fully connected"
            : string.Join(", ", r.RemainingIslands.Select(x => $"{x.Net} {x.Islands}"));
        StatusText.Text = $"Power optimized: {r.ViasPlaced} vias, {r.PlanesPoured} planes re-poured " +
                          $"@ {r.ClearanceMm:0.##}/{r.FillStrokeMm:0.##} mm — left for routing: {left}. Ctrl+D to re-check.";
        MessageBox.Show(string.Join("\n", r.Log), "Optimize Power Planes",
                        MessageBoxButton.OK, MessageBoxImage.Information);
    }

    private void OnWindowClosing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        if (!ConfirmDiscard()) e.Cancel = true;
    }

    private void OnExit(object sender, RoutedEventArgs e) => Close();

    // ---- edit ----

    private void OnUndo(object sender, RoutedEventArgs e) { _doc.Undo.Undo(); Pcb.InvalidateVisual(); }
    private void OnRedo(object sender, RoutedEventArgs e) { _doc.Undo.Redo(); Pcb.InvalidateVisual(); }
    private void OnSelectAll(object sender, RoutedEventArgs e) => Pcb.SelectAll();

    private void OnWindowKeyDown(object sender, KeyEventArgs e)
    {
        if (Keyboard.Modifiers.HasFlag(ModifierKeys.Control))
        {
            switch (e.Key)
            {
                case Key.Z: OnUndo(sender, new RoutedEventArgs()); e.Handled = true; break;
                case Key.Y: OnRedo(sender, new RoutedEventArgs()); e.Handled = true; break;
                case Key.S: OnSaveProject(sender, new RoutedEventArgs()); e.Handled = true; break;
                case Key.O: OnOpenProject(sender, new RoutedEventArgs()); e.Handled = true; break;
                case Key.N: OnNewBoard(sender, new RoutedEventArgs()); e.Handled = true; break;
                case Key.D: OnRunDrc(sender, new RoutedEventArgs()); e.Handled = true; break;
            }
        }
    }

    // ---- toolbar / tools ----

    private void OnToolChanged(object sender, RoutedEventArgs e)
    {
        if (Pcb is null) return;
        var tag = (sender as RadioButton)?.Tag?.ToString() ?? "Select";
        Pcb.Tool = Enum.Parse<EditorTool>(tag);
        StatusText.Text = $"Tool: {tag}";
    }

    private void OnZoomFit(object sender, RoutedEventArgs e) => Pcb.ZoomToFit();

    private void OnToggleRatsnest(object sender, RoutedEventArgs e)
    {
        Pcb.ShowRatsnest = MenuRatsnest.IsChecked;
        Pcb.InvalidateVisual();
    }

    private void OnRunDrc(object sender, RoutedEventArgs e)
    {
        // auto-mark backdrills before the run so cleared stubs don't re-flag rule 15
        int backdrilled = Backdrill.ApplyAll(_doc);
        var violations = _doc.RunDrcDetailed();
        violations.AddRange(ElectricalRules.Check(_doc));   // ERC from pin electrical types
        violations.AddRange(Ampacity.Check(_doc, _library)); // IPC-2221 current capacity
        violations.AddRange(IrDrop.Check(_doc, _library));   // DC IR-drop on planes
        violations.AddRange(PdnAnalyzer.Check(_doc, _library)); // PDN AC impedance
        violations.AddRange(Crosstalk.Check(_doc));          // parallel-run coupling
        violations.AddRange(Thermal.Check(_doc, _library));  // junction temperatures
        if (backdrilled > 0)
            StatusText.Text = $"Marked {backdrilled} via(s) for backdrilling (exported with the fab package)";
        Pcb.SetDrcResults(violations);
        DrcList.ItemsSource = violations;
        DrcExpander.IsExpanded = violations.Count > 0;
        StatusText.Text = violations.Count == 0
            ? "DRC passed — no violations"
            : $"DRC: {violations.Count} violation(s) — click an entry to zoom to it";
    }

    private void OnDrcItemSelected(object sender, SelectionChangedEventArgs e)
    {
        if (DrcList.SelectedItem is DrcResultItem v)
        {
            Pcb.CenterOn(v.X, v.Y, scale: 20);
            StatusText.Text = $"{v.RuleName}: {v.Message}";
        }
    }

    private void OnNetsExpanded(object sender, RoutedEventArgs e)
    {
        NetList.ItemsSource = _doc.Nets
            .OrderBy(n => n.Name, StringComparer.OrdinalIgnoreCase)
            .Select(n =>
            {
                int islands = _doc.NetIslands(n.Id);
                string state = islands <= 1 ? "✓ routed" : $"{islands} islands";
                return $"{n.Name}  ({_doc.ClassFor(n.Id).Name})  {_doc.NetLengthMm(n.Id):F1} mm  ·  {state}";
            })
            .ToList();
    }

    private void OnAutoPlace(object sender, RoutedEventArgs e)
    {
        int moved = AutoPlacer.Place(_doc);
        Pcb.InvalidateVisual();
        StatusText.Text = moved > 0
            ? $"Auto-place moved {moved} component(s) — Ctrl+Z to undo"
            : "Auto-place: nothing to move (need at least 2 parts)";
    }

    // ---- 3D view ----

    private void OnEditorTabChanged(object sender, SelectionChangedEventArgs e)
    {
        // SelectionChanged is a bubbling routed event — make sure it came from the TabControl.
        if (!ReferenceEquals(e.OriginalSource, EditorTabs)) return;
        if (EditorTabs?.SelectedIndex == 1) Rebuild3D();
    }

    private void Rebuild3D()
    {
        View3D.Children.Clear();
        var visual = new ModelVisual3D { Content = Board3DBuilder.Build(_doc) };
        View3D.Children.Add(visual);
        _orbitDist = Math.Max(_doc.BoardWidthMm, _doc.BoardHeightMm) * 1.6;
        Update3DCamera();
    }

    private void Update3DCamera()
    {
        double az = _orbitAz * Math.PI / 180, el = _orbitEl * Math.PI / 180;
        double cx = _doc.BoardWidthMm / 2, cy = -_doc.BoardHeightMm / 2;
        var eye = new Point3D(
            cx + _orbitDist * Math.Cos(el) * Math.Sin(az),
            cy - _orbitDist * Math.Cos(el) * Math.Cos(az),
            _orbitDist * Math.Sin(el));
        var look = new Vector3D(cx - eye.X, cy - eye.Y, -eye.Z);
        View3D.Camera = new PerspectiveCamera(eye, look, new Vector3D(0, 0, 1), 45);
    }

    private void On3DMouseDown(object sender, MouseButtonEventArgs e)
    {
        _orbiting = true;
        _orbitLast = e.GetPosition(View3DHost);
        View3DHost.CaptureMouse();
    }

    private void On3DMouseMove(object sender, MouseEventArgs e)
    {
        if (!_orbiting) return;
        var p = e.GetPosition(View3DHost);
        _orbitAz += (p.X - _orbitLast.X) * 0.4;
        _orbitEl = Math.Clamp(_orbitEl + (p.Y - _orbitLast.Y) * 0.3, 5, 89);
        _orbitLast = p;
        Update3DCamera();
    }

    private void On3DMouseUp(object sender, MouseButtonEventArgs e)
    {
        _orbiting = false;
        View3DHost.ReleaseMouseCapture();
    }

    private void On3DMouseWheel(object sender, MouseWheelEventArgs e)
    {
        _orbitDist = Math.Clamp(_orbitDist * (e.Delta > 0 ? 1 / 1.15 : 1.15), 20, 1000);
        Update3DCamera();
    }
}
