using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using DesignStudio.Interop;
using DesignStudio.Model;

namespace DesignStudio;

/// <summary>
/// Live physics calculator backed by the C++ engine (Hammerstad–Jensen
/// microstrip, exact Cohn stripline via elliptic integrals, IPC-2221 current,
/// skin effect, via parasitics — see core/include/designcore/physics.h and
/// docs/physics-engine.md for the textbook sources). All geometry inputs in mm;
/// stackup (εr, dielectric height, tanδ, copper) comes from Board Setup.
/// </summary>
public class PhysicsCalculatorDialog : Window
{
    private readonly BoardDocument _doc;
    private readonly TextBox _width = new() { Text = "0.25" };
    private readonly TextBox _gap = new() { Text = "0.15" };
    private readonly TextBox _freq = new() { Text = "1.0" };
    private readonly TextBox _deltaT = new() { Text = "10" };
    private readonly TextBox _targetZ0 = new() { Text = "50" };
    private readonly TextBox _targetZd = new() { Text = "90" };
    private readonly TextBlock _out = new()
    {
        FontFamily = new System.Windows.Media.FontFamily("Consolas"),
        TextWrapping = TextWrapping.Wrap,
        Margin = new Thickness(0, 8, 0, 0)
    };

    public PhysicsCalculatorDialog(BoardDocument doc)
    {
        _doc = doc;
        Title = "Physics Calculator (C++ engine)";
        Width = 560; Height = 640;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (System.Windows.Media.Brush)Application.Current.Resources["BgDark"];

        var root = new DockPanel { Margin = new Thickness(12) };

        var stackInfo = new TextBlock
        {
            Text = $"Stackup (Board Setup): εr = {doc.DielectricEr:F2}, h = {doc.DielectricHeightMm:F3} mm, " +
                   $"tanδ = {doc.DielectricLossTangent:F3}, copper = {doc.CopperThicknessMm * 1000:F0} µm",
            Opacity = 0.75, Margin = new Thickness(0, 0, 0, 8), TextWrapping = TextWrapping.Wrap
        };
        DockPanel.SetDock(stackInfo, Dock.Top);
        root.Children.Add(stackInfo);

        var grid = new Grid();
        DockPanel.SetDock(grid, Dock.Top);
        for (int i = 0; i < 4; i++) grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.RowDefinitions.Add(new RowDefinition());
        grid.RowDefinitions.Add(new RowDefinition());
        grid.RowDefinitions.Add(new RowDefinition());
        AddCell(grid, 0, 0, "Trace width (mm)", _width);
        AddCell(grid, 0, 2, "Diff gap (mm)", _gap);
        AddCell(grid, 1, 0, "Frequency (GHz)", _freq);
        AddCell(grid, 1, 2, "ΔT rise (°C)", _deltaT);
        AddCell(grid, 2, 0, "Target Z0 (Ω)", _targetZ0);
        AddCell(grid, 2, 2, "Target Zdiff (Ω)", _targetZd);
        root.Children.Add(grid);

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
        DockPanel.SetDock(buttons, Dock.Top);
        var solveSe = new Button { Content = "Solve width for Z0", Width = 150 };
        solveSe.Click += (_, _) =>
        {
            if (!TryD(_targetZ0, out double z)) return;
            double w = NativeCore.dc_phys_microstrip_width(z, _doc.DielectricHeightMm, _doc.CopperThicknessMm, _doc.DielectricEr);
            if (!double.IsNaN(w)) _width.Text = w.ToString("F4", CultureInfo.InvariantCulture);
            Recompute();
        };
        var solveDiff = new Button { Content = "Solve width for Zdiff @ gap", Width = 190, Margin = new Thickness(8, 0, 0, 0) };
        solveDiff.Click += (_, _) =>
        {
            if (!TryD(_targetZd, out double zd) || !TryD(_gap, out double s)) return;
            double w = NativeCore.dc_phys_diff_microstrip_width(zd, s, _doc.DielectricHeightMm, _doc.CopperThicknessMm, _doc.DielectricEr);
            if (!double.IsNaN(w)) _width.Text = w.ToString("F4", CultureInfo.InvariantCulture);
            Recompute();
        };
        buttons.Children.Add(solveSe);
        buttons.Children.Add(solveDiff);
        root.Children.Add(buttons);

        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = _out };
        root.Children.Add(scroll);
        Content = root;

        foreach (var tb in new[] { _width, _gap, _freq, _deltaT })
            tb.TextChanged += (_, _) => Recompute();
        Recompute();
    }

    private static void AddCell(Grid g, int row, int col, string label, TextBox tb)
    {
        var lbl = new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 4, 6, 4) };
        Grid.SetRow(lbl, row); Grid.SetColumn(lbl, col);
        tb.Margin = new Thickness(0, 4, 12, 4);
        Grid.SetRow(tb, row); Grid.SetColumn(tb, col + 1);
        g.Children.Add(lbl);
        g.Children.Add(tb);
    }

    private static bool TryD(TextBox tb, out double v) =>
        double.TryParse(tb.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out v) && v > 0;

    private void Recompute()
    {
        if (!TryD(_width, out double w) || !TryD(_freq, out double fGHz) || !TryD(_deltaT, out double dT))
        {
            _out.Text = "Enter positive numbers.";
            return;
        }
        TryD(_gap, out double s);
        double h = _doc.DielectricHeightMm, t = _doc.CopperThicknessMm;
        double er = _doc.DielectricEr, tanD = _doc.DielectricLossTangent;
        double f = fGHz * 1e9;
        var sb = new System.Text.StringBuilder();

        if (NativeCore.dc_phys_microstrip(w, h, t, er, out double z0, out double eEff, out double delay) == NativeCore.Ok)
        {
            sb.AppendLine("── Microstrip (Hammerstad–Jensen) ─────────────");
            sb.AppendLine($"  Z0           = {z0,8:F2} Ω");
            sb.AppendLine($"  εeff         = {eEff,8:F3}");
            sb.AppendLine($"  delay        = {delay,8:F2} ps/mm   ({delay * 25.4:F0} ps/inch)");
            sb.AppendLine($"  loss @ {fGHz:F1} GHz = {NativeCore.dc_phys_microstrip_loss_db_per_m(w, h, t, er, tanD, f),6:F2} dB/m");
            if (s > 0)
            {
                double zd = NativeCore.dc_phys_diff_microstrip(w, s, h, t, er);
                sb.AppendLine($"  Zdiff (gap {s:F3}) = {zd,8:F2} Ω   (IPC-2141 ±5%)");
            }
        }
        // stripline between adjacent planes: b = 2h + t as a simple symmetric default
        double b = 2 * h + t;
        if (NativeCore.dc_phys_stripline(w, b, t, er, out double zs, out double ds) == NativeCore.Ok)
        {
            sb.AppendLine($"── Stripline, b = {b:F3} mm (Cohn exact) ──────");
            sb.AppendLine($"  Z0           = {zs,8:F2} Ω    delay = {ds:F2} ps/mm");
            if (s > 0)
                sb.AppendLine($"  Zdiff (gap {s:F3}) = {NativeCore.dc_phys_diff_stripline(w, s, b, t, er),8:F2} Ω");
        }

        sb.AppendLine("── Copper / current (IPC-2221, Onderdonk) ─────");
        sb.AppendLine($"  I (ext, ΔT={dT:F0}°C) = {NativeCore.dc_phys_ipc2221_current(w, t, dT, 1),6:F2} A");
        sb.AppendLine($"  I (int, ΔT={dT:F0}°C) = {NativeCore.dc_phys_ipc2221_current(w, t, dT, 0),6:F2} A");
        sb.AppendLine($"  fusing (1 s)      = {NativeCore.dc_phys_onderdonk_current(w, t, 1.0, 20),6:F1} A");
        sb.AppendLine($"  R/100mm DC        = {NativeCore.dc_phys_trace_resistance(w, t, 100, 0, 20) * 1000,6:F2} mΩ");
        sb.AppendLine($"  R/100mm @{fGHz:F1}GHz   = {NativeCore.dc_phys_trace_resistance(w, t, 100, f, 20) * 1000,6:F2} mΩ");
        sb.AppendLine($"  skin depth @{fGHz:F1}GHz = {NativeCore.dc_phys_skin_depth_mm(f) * 1000,6:F2} µm");

        var cls = _doc.NetClasses[0];
        double boardT = Math.Max((_doc.CopperLayers - 1) * (h + t), 0.4);
        sb.AppendLine($"── Via (class '{cls.Name}': Ø{cls.ViaDiameterMm:F2}/{cls.ViaDrillMm:F2}) ──");
        sb.AppendLine($"  L = {NativeCore.dc_phys_via_inductance_nh(boardT, cls.ViaDrillMm),6:F2} nH    " +
                      $"C = {NativeCore.dc_phys_via_capacitance_pf(er, boardT, cls.ViaDiameterMm, cls.ViaDiameterMm + 0.4),6:F3} pF");
        sb.AppendLine($"  R = {NativeCore.dc_phys_via_resistance(boardT, cls.ViaDrillMm, 0.025, 20) * 1000,6:F2} mΩ    " +
                      $"θ = {NativeCore.dc_phys_via_thermal_kw(boardT, cls.ViaDrillMm, 0.025),6:F1} K/W");
        sb.AppendLine($"  I (ΔT={dT:F0}°C) = {NativeCore.dc_phys_via_current(cls.ViaDrillMm, 0.025, dT),6:F2} A");

        if (s > 0)
        {
            sb.AppendLine("── Coupling ───────────────────────────────────");
            sb.AppendLine($"  crosstalk coefficient (s={s:F3}, h={h:F3}) = {NativeCore.dc_phys_crosstalk(s, h):F3}");
        }
        sb.AppendLine($"  plane C = {NativeCore.dc_phys_plane_capacitance_pf(100, h, er),6:F1} pF/cm²  (gap {h:F3} mm)");

        _out.Text = sb.ToString();
    }
}
