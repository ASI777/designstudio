using System.Windows;
using System.Windows.Controls;
using DesignStudio.Model;

namespace DesignStudio;

// Code-only dialogs (no XAML codegen): board setup, net classes, copper pour.

/// <summary>Board size, grid and copper layer count (2–16, the smartphone/accelerator/backplane range).</summary>
public class BoardSetupDialog : Window
{
    private readonly TextBox _width = new();
    private readonly TextBox _height = new();
    private readonly TextBox _grid = new();
    private readonly ComboBox _layers = new();
    private readonly TextBox _er = new();
    private readonly TextBox _dielH = new();
    private readonly TextBox _tanD = new();
    private readonly TextBox _copperT = new();
    private readonly ComboBox _shape = new();
    private readonly TextBox _radius = new();

    public double BoardWidthMm { get; private set; }
    public double BoardHeightMm { get; private set; }
    public double GridMm { get; private set; }
    public int CopperLayers { get; private set; }
    public double DielectricEr { get; private set; }
    public double DielectricHeightMm { get; private set; }
    public double DielectricLossTangent { get; private set; }
    public double CopperThicknessMm { get; private set; }
    public BoardShape Shape { get; private set; }
    public double CornerRadiusMm { get; private set; }

    public BoardSetupDialog(BoardDocument doc)
    {
        Title = "Board Setup";
        Width = 380; Height = 510;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ResizeMode = ResizeMode.NoResize;
        Background = (System.Windows.Media.Brush)Application.Current.Resources["BgDark"];

        _width.Text = doc.BoardWidthMm.ToString("F2");
        _height.Text = doc.BoardHeightMm.ToString("F2");
        _grid.Text = doc.GridMm.ToString("F2");
        _er.Text = doc.DielectricEr.ToString("F2");
        _dielH.Text = doc.DielectricHeightMm.ToString("F3");
        _tanD.Text = doc.DielectricLossTangent.ToString("F3");
        _copperT.Text = doc.CopperThicknessMm.ToString("F3");
        // Editable so the full engine range (2..64) is reachable, not just the
        // common low-layer presets. Above 16 layers EnsureStackup() switches to the
        // dense server/AI plane cadence (a reference plane next to every signal
        // layer); the count never changes on its own — it is whatever is set here.
        _layers.IsEditable = true;
        foreach (int n in new[] { 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 32, 40, 48, 50, 64 })
            _layers.Items.Add(n);
        _layers.Text = doc.CopperLayers.ToString();   // preserves any current value, incl. > 16
        foreach (var name in new[] { "Rectangle", "Square", "Circle", "Rounded rectangle" })
            _shape.Items.Add(name);
        _shape.SelectedIndex = doc.Shape switch { BoardShape.Circle => 2, BoardShape.RoundedRectangle => 3, _ => 0 };
        _radius.Text = "3.00";

        var grid = new Grid { Margin = new Thickness(12) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(160) });
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        string[] labels = { "Width / diameter (mm)", "Height (mm)", "Grid (mm)", "Shape", "Corner radius (mm)",
                            "Copper layers", "Dielectric εr", "Dielectric height (mm)", "Loss tangent", "Copper thickness (mm)" };
        UIElement[] inputs = { _width, _height, _grid, _shape, _radius, _layers, _er, _dielH, _tanD, _copperT };
        for (int i = 0; i < labels.Length; i++)
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var lbl = new TextBlock { Text = labels[i], Margin = new Thickness(0, 6, 8, 6), VerticalAlignment = VerticalAlignment.Center };
            Grid.SetRow(lbl, i); Grid.SetColumn(lbl, 0);
            var input = (FrameworkElement)inputs[i];
            input.Margin = new Thickness(0, 4, 0, 4);
            Grid.SetRow(input, i); Grid.SetColumn(input, 1);
            grid.Children.Add(lbl);
            grid.Children.Add(input);
        }

        var ok = new Button { Content = "OK", Width = 80, Margin = new Thickness(0, 12, 8, 0), IsDefault = true };
        var cancel = new Button { Content = "Cancel", Width = 80, Margin = new Thickness(0, 12, 0, 0), IsCancel = true };
        ok.Click += (_, _) =>
        {
            if (!double.TryParse(_width.Text, out double w) || w < 5 || w > 1000 ||
                !double.TryParse(_height.Text, out double h) || h < 5 || h > 1000 ||
                !double.TryParse(_grid.Text, out double g) || g <= 0)
            {
                MessageBox.Show("Enter a valid size (5–1000 mm) and grid.", "Board Setup");
                return;
            }
            if (!double.TryParse(_er.Text, out double er) || er < 1 ||
                !double.TryParse(_dielH.Text, out double dh) || dh <= 0 ||
                !double.TryParse(_tanD.Text, out double td) || td < 0 ||
                !double.TryParse(_copperT.Text, out double ct) || ct <= 0)
            {
                MessageBox.Show("Enter a valid stackup (εr ≥ 1, heights > 0).", "Board Setup");
                return;
            }
            if (!int.TryParse((_layers.Text ?? "").Trim(), out int layers) || layers < 2 || layers > 64)
            {
                MessageBox.Show("Copper layers must be a whole number from 2 to 64.", "Board Setup");
                return;
            }
            BoardWidthMm = w; BoardHeightMm = h; GridMm = g;
            CopperLayers = layers;
            DielectricEr = er; DielectricHeightMm = dh;
            DielectricLossTangent = td; CopperThicknessMm = ct;
            Shape = _shape.SelectedIndex switch { 2 => BoardShape.Circle, 3 => BoardShape.RoundedRectangle, _ => BoardShape.Rectangle };
            if (_shape.SelectedIndex == 1) BoardHeightMm = w;     // square: side = width
            double.TryParse(_radius.Text, out double rad);
            CornerRadiusMm = rad;
            DialogResult = true;
        };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(ok);
        buttons.Children.Add(cancel);
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(buttons, labels.Length); Grid.SetColumn(buttons, 1);
        grid.Children.Add(buttons);
        Content = grid;
    }
}

/// <summary>
/// Net class editor: per-class clearance, width, via geometry, length-matching
/// budget and microvia permission, plus net→class assignment.
/// </summary>
public class NetClassesDialog : Window
{
    private readonly BoardDocument _doc;
    private readonly DataGrid _classGrid = new();
    private readonly ListBox _netList = new() { SelectionMode = SelectionMode.Extended };
    private readonly ComboBox _assignCombo = new();

    public NetClassesDialog(BoardDocument doc)
    {
        _doc = doc;
        Title = "Net Classes";
        Width = 860; Height = 480;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (System.Windows.Media.Brush)Application.Current.Resources["BgDark"];

        var root = new Grid { Margin = new Thickness(10) };
        root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3, GridUnitType.Star) });
        root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star) });

        // left: classes
        var left = new DockPanel();
        var leftTitle = new TextBlock { Text = "Classes (clearance / width / via in mm)", FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 4) };
        DockPanel.SetDock(leftTitle, Dock.Top);
        var addBtn = new Button { Content = "Add Class", Width = 100, Margin = new Thickness(0, 6, 0, 0), HorizontalAlignment = HorizontalAlignment.Left };
        DockPanel.SetDock(addBtn, Dock.Bottom);
        addBtn.Click += (_, _) =>
        {
            _doc.AddNetClass($"Class{_doc.NetClasses.Count}");
            RefreshClasses();
        };
        var zBtn = new Button { Content = "Width from impedance…", Width = 170, Margin = new Thickness(8, 6, 0, 0), HorizontalAlignment = HorizontalAlignment.Left };
        DockPanel.SetDock(zBtn, Dock.Bottom);
        zBtn.Click += (_, _) => SolveWidthFromImpedance();
        _classGrid.AutoGenerateColumns = true;
        _classGrid.CanUserAddRows = false;
        _classGrid.CanUserDeleteRows = false;
        left.Children.Add(leftTitle);
        left.Children.Add(zBtn);
        left.Children.Add(addBtn);
        left.Children.Add(_classGrid);

        // right: net assignment
        var right = new DockPanel { Margin = new Thickness(10, 0, 0, 0) };
        var rightTitle = new TextBlock { Text = "Assign nets to a class", FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 4) };
        DockPanel.SetDock(rightTitle, Dock.Top);
        var assignRow = new DockPanel { Margin = new Thickness(0, 6, 0, 0) };
        DockPanel.SetDock(assignRow, Dock.Bottom);
        var assignBtn = new Button { Content = "Assign", Width = 80, Margin = new Thickness(8, 0, 0, 0) };
        DockPanel.SetDock(assignBtn, Dock.Right);
        assignBtn.Click += (_, _) => AssignSelected();
        assignRow.Children.Add(assignBtn);
        assignRow.Children.Add(_assignCombo);
        right.Children.Add(rightTitle);
        right.Children.Add(assignRow);
        right.Children.Add(_netList);

        Grid.SetColumn(left, 0);
        Grid.SetColumn(right, 1);
        root.Children.Add(left);
        root.Children.Add(right);
        Content = root;

        RefreshClasses();
        RefreshNets();
        Closed += (_, _) => _doc.NotifyChanged();
    }

    private void RefreshClasses()
    {
        _classGrid.ItemsSource = null;
        _classGrid.ItemsSource = _doc.NetClasses;
        _assignCombo.ItemsSource = null;
        _assignCombo.ItemsSource = _doc.NetClasses;
        if (_assignCombo.SelectedIndex < 0) _assignCombo.SelectedIndex = 0;
    }

    private void RefreshNets()
    {
        _netList.ItemsSource = null;
        _netList.ItemsSource = _doc.Nets
            .Select(n => new NetRow(n, _doc.NetClasses.FirstOrDefault(c => c.Id == n.ClassId)?.Name ?? "?"))
            .ToList();
    }

    private void AssignSelected()
    {
        if (_assignCombo.SelectedItem is not NetClassInfo cls) return;
        foreach (var item in _netList.SelectedItems.OfType<NetRow>())
            item.Net.ClassId = cls.Id;
        RefreshNets();
    }

    /// <summary>
    /// Physics-engine synthesis: sets the selected class's trace width (and
    /// diff gap solution) from a target impedance on the document's stackup
    /// (microstrip model — outer layers; see Tools → Physics Calculator for
    /// stripline numbers).
    /// </summary>
    private void SolveWidthFromImpedance()
    {
        if (_classGrid.SelectedItem is not NetClassInfo cls)
        {
            MessageBox.Show("Select a class row first.", "Net Classes");
            return;
        }
        var dlg = new ImpedanceTargetDialog(cls) { Owner = this };
        if (dlg.ShowDialog() != true) return;

        double h = _doc.DielectricHeightMm, t = _doc.CopperThicknessMm, er = _doc.DielectricEr;
        if (dlg.TargetZdiff > 0 && dlg.GapMm > 0)
        {
            double w = Interop.NativeCore.dc_phys_diff_microstrip_width(dlg.TargetZdiff, dlg.GapMm, h, t, er);
            if (double.IsNaN(w))
            {
                MessageBox.Show($"No width reaches {dlg.TargetZdiff:F0} Ω differential at gap {dlg.GapMm:F3} mm on this stackup.", "Net Classes");
                return;
            }
            cls.TraceWidthMm = Math.Round(w, 4);
            cls.DiffPairGapMm = dlg.GapMm;
        }
        else
        {
            double w = Interop.NativeCore.dc_phys_microstrip_width(dlg.TargetZ0, h, t, er);
            if (double.IsNaN(w))
            {
                MessageBox.Show($"No width reaches {dlg.TargetZ0:F0} Ω on this stackup.", "Net Classes");
                return;
            }
            cls.TraceWidthMm = Math.Round(w, 4);
        }
        RefreshClasses();
    }

    private sealed record NetRow(NetInfo Net, string ClassName)
    {
        public override string ToString() => $"{Net.Name}   →  {ClassName}";
    }
}

/// <summary>Target impedance input for net-class width synthesis.</summary>
public class ImpedanceTargetDialog : Window
{
    private readonly TextBox _z0 = new() { Text = "50" };
    private readonly TextBox _zd = new() { Text = "0" };
    private readonly TextBox _gap = new();

    public double TargetZ0 { get; private set; }
    public double TargetZdiff { get; private set; }
    public double GapMm { get; private set; }

    public ImpedanceTargetDialog(NetClassInfo cls)
    {
        Title = $"Impedance target — {cls.Name}";
        Width = 360; Height = 240;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ResizeMode = ResizeMode.NoResize;
        Background = (System.Windows.Media.Brush)Application.Current.Resources["BgDark"];
        _gap.Text = (cls.DiffPairGapMm > 0 ? cls.DiffPairGapMm : 0.15).ToString("F3");

        var grid = new Grid { Margin = new Thickness(12) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(190) });
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        string[] labels = { "Single-ended Z0 (Ω)", "Differential Zdiff (Ω, 0 = off)", "Diff gap (mm)" };
        UIElement[] inputs = { _z0, _zd, _gap };
        for (int i = 0; i < labels.Length; i++)
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var lbl = new TextBlock { Text = labels[i], Margin = new Thickness(0, 6, 8, 6), VerticalAlignment = VerticalAlignment.Center };
            Grid.SetRow(lbl, i); Grid.SetColumn(lbl, 0);
            var input = (FrameworkElement)inputs[i];
            input.Margin = new Thickness(0, 4, 0, 4);
            Grid.SetRow(input, i); Grid.SetColumn(input, 1);
            grid.Children.Add(lbl);
            grid.Children.Add(input);
        }
        var ok = new Button { Content = "Solve", Width = 80, Margin = new Thickness(0, 12, 8, 0), IsDefault = true };
        var cancel = new Button { Content = "Cancel", Width = 80, Margin = new Thickness(0, 12, 0, 0), IsCancel = true };
        ok.Click += (_, _) =>
        {
            if (!double.TryParse(_z0.Text, out double z0) || z0 <= 0 ||
                !double.TryParse(_zd.Text, out double zd) || zd < 0 ||
                !double.TryParse(_gap.Text, out double gap) || gap < 0)
            {
                MessageBox.Show("Enter valid impedance targets.", "Impedance");
                return;
            }
            TargetZ0 = z0; TargetZdiff = zd; GapMm = gap;
            DialogResult = true;
        };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(ok);
        buttons.Children.Add(cancel);
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(buttons, labels.Length); Grid.SetColumn(buttons, 1);
        grid.Children.Add(buttons);
        Content = grid;
    }
}

/// <summary>Pick net, layer and geometry for a copper pour (ground/power plane).</summary>
public class PourDialog : Window
{
    private readonly ComboBox _net = new();
    private readonly ComboBox _layer = new();
    private readonly TextBox _width = new() { Text = "0.30" };
    private readonly TextBox _clearance = new() { Text = "0.25" };

    public int NetId { get; private set; } = -1;
    public int Layer { get; private set; }
    public double LineWidthMm { get; private set; } = 0.3;
    public double ClearanceMm { get; private set; } = 0.25;

    public PourDialog(BoardDocument doc, int activeLayer)
    {
        Title = "Generate Copper Pour";
        Width = 380; Height = 280;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ResizeMode = ResizeMode.NoResize;
        Background = (System.Windows.Media.Brush)Application.Current.Resources["BgDark"];

        _net.ItemsSource = doc.Nets;
        _net.SelectedItem = doc.Nets.FirstOrDefault(n => n.Name is "GND" or "GNDA")
                            ?? doc.Nets.FirstOrDefault();
        for (int i = 0; i < doc.CopperLayers; i++)
            _layer.Items.Add(doc.LayerName(i));
        _layer.SelectedIndex = Math.Clamp(activeLayer, 0, doc.CopperLayers - 1);

        var grid = new Grid { Margin = new Thickness(12) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) });
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        string[] labels = { "Net (e.g. GND)", "Layer", "Fill stroke (mm)", "Clearance (mm)" };
        UIElement[] inputs = { _net, _layer, _width, _clearance };
        for (int i = 0; i < labels.Length; i++)
        {
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var lbl = new TextBlock { Text = labels[i], Margin = new Thickness(0, 6, 8, 6), VerticalAlignment = VerticalAlignment.Center };
            Grid.SetRow(lbl, i); Grid.SetColumn(lbl, 0);
            var input = (FrameworkElement)inputs[i];
            input.Margin = new Thickness(0, 4, 0, 4);
            Grid.SetRow(input, i); Grid.SetColumn(input, 1);
            grid.Children.Add(lbl);
            grid.Children.Add(input);
        }

        var ok = new Button { Content = "Generate", Width = 90, Margin = new Thickness(0, 12, 8, 0), IsDefault = true };
        var cancel = new Button { Content = "Cancel", Width = 80, Margin = new Thickness(0, 12, 0, 0), IsCancel = true };
        ok.Click += (_, _) =>
        {
            if (_net.SelectedItem is not NetInfo net)
            {
                MessageBox.Show("Create a net first (pads must belong to the pour net).", "Pour");
                return;
            }
            if (!double.TryParse(_width.Text, out double w) || w < 0.05 ||
                !double.TryParse(_clearance.Text, out double c) || c < 0)
            {
                MessageBox.Show("Enter a valid stroke width and clearance.", "Pour");
                return;
            }
            NetId = net.Id;
            Layer = _layer.SelectedIndex;
            LineWidthMm = w;
            ClearanceMm = c;
            DialogResult = true;
        };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(ok);
        buttons.Children.Add(cancel);
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(buttons, labels.Length); Grid.SetColumn(buttons, 1);
        grid.Children.Add(buttons);
        Content = grid;
    }
}

/// <summary>Free-text design goal captured into the circuit-state export.</summary>
public class DesignGoalDialog : Window
{
    private readonly TextBox _goal = new()
    {
        AcceptsReturn = true,
        TextWrapping = TextWrapping.Wrap,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        Text = "Describe the purpose of this circuit, supply constraints, interfaces needed, cost/size goals…"
    };

    public string Goal { get; private set; } = "";

    public DesignGoalDialog()
    {
        Title = "Design goal for the AI advisor";
        Width = 460; Height = 300;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (System.Windows.Media.Brush)Application.Current.Resources["BgDark"];

        var root = new DockPanel { Margin = new Thickness(12) };
        var ok = new Button { Content = "Export", Width = 90, Margin = new Thickness(0, 8, 8, 0), IsDefault = true };
        var cancel = new Button { Content = "Cancel", Width = 80, Margin = new Thickness(0, 8, 0, 0), IsCancel = true };
        ok.Click += (_, _) => { Goal = _goal.Text.Trim(); DialogResult = true; };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(ok);
        buttons.Children.Add(cancel);
        DockPanel.SetDock(buttons, Dock.Bottom);
        root.Children.Add(buttons);
        root.Children.Add(_goal);
        Content = root;
        _goal.Focus();
        _goal.SelectAll();
    }
}
