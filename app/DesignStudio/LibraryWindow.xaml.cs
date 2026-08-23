using System.Windows;
using System.Windows.Controls;
using DesignStudio.Model;
using Microsoft.Win32;

namespace DesignStudio;

public partial class LibraryWindow : Window
{
    private readonly FootprintLibrary _library;

    /// <summary>Set when the user clicks "Place on Board"; MainWindow picks it up.</summary>
    public FootprintDef? SelectedForPlacement { get; private set; }

    public LibraryWindow(FootprintLibrary library)
    {
        InitializeComponent();
        _library = library;
        RefreshList();
    }

    private void RefreshList(string filter = "")
    {
        LibList.ItemsSource = _library.Items
            .Where(f => filter.Length == 0 ||
                        f.Name.Contains(filter, StringComparison.OrdinalIgnoreCase) ||
                        f.Description.Contains(filter, StringComparison.OrdinalIgnoreCase))
            .ToList();
    }

    private void OnSearch(object sender, TextChangedEventArgs e) => RefreshList(SearchBox.Text.Trim());

    private void OnSelect(object sender, SelectionChangedEventArgs e)
    {
        if (LibList.SelectedItem is FootprintDef def) ShowPreview(def);
    }

    private void ShowPreview(FootprintDef def)
    {
        Preview.Footprint = def;
        PreviewTitle.Text = def.Name;
        var types = def.Pads
            .GroupBy(pd => string.IsNullOrEmpty(pd.ElectricalType) ? "passive" : pd.ElectricalType)
            .Select(g => $"{g.Count()}×{g.Key}");
        string meta = string.IsNullOrEmpty(def.Mpn) ? "" : $"\n{def.Manufacturer} {def.Mpn}";
        string pin1 = string.IsNullOrEmpty(def.Pin1Marker) ? "" : $" · pin 1: {def.Pin1Marker}";
        PreviewInfo.Text = $"{def.Description}{meta}\n{def.Pads.Count} pads ({string.Join(", ", types)})" +
                           $"\nbody {def.BodyWidthMm}×{def.BodyHeightMm} mm · source: {def.Source}{pin1}";
    }

    private void OnDelete(object sender, RoutedEventArgs e)
    {
        if (LibList.SelectedItem is not FootprintDef def) return;
        if (MessageBox.Show($"Delete {def.Name}?", "Library", MessageBoxButton.YesNo) == MessageBoxResult.Yes)
        {
            _library.Delete(def);
            RefreshList(SearchBox.Text.Trim());
        }
    }

    private void OnPlace(object sender, RoutedEventArgs e)
    {
        if (LibList.SelectedItem is not FootprintDef def) return;
        SelectedForPlacement = def;
        DialogResult = true;
    }

    /// <summary>
    /// Extract a dimension graph from a layout SVG using the python extractor.
    /// </summary>
    private void OnExtractSvg(object sender, RoutedEventArgs e)
    {
        var dlg = new OpenFileDialog
        {
            Filter = "SVG Files (*.svg)|*.svg|All files (*.*)|*.*",
            Title = "Extract dimensions from layout SVG"
        };
        if (dlg.ShowDialog(this) != true) return;
        try
        {
            string repoRoot = System.IO.Path.GetFullPath(System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "..", "..", ".."));
            string script = System.IO.Path.Combine(repoRoot, "tools", "layout_extractor", "svg_dimension_graph.py");
            
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "python3",
                Arguments = $"\"{script}\" \"{dlg.FileName}\"",
                WorkingDirectory = repoRoot,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            
            using var process = System.Diagnostics.Process.Start(psi);
            if (process != null)
            {
                process.WaitForExit();
                string output = process.StandardOutput.ReadToEnd();
                string error = process.StandardError.ReadToEnd();
                
                if (process.ExitCode != 0)
                {
                    MessageBox.Show($"Extraction failed:\n{error}", "Library", MessageBoxButton.OK, MessageBoxImage.Error);
                    return;
                }
                
                string outJson = System.IO.Path.ChangeExtension(dlg.FileName, ".dimension_graph.json");
                MessageBox.Show($"Extraction succeeded.\n\n{output}\n\nGraph saved to: {outJson}", 
                    "SVG Dimension Extraction", MessageBoxButton.OK, MessageBoxImage.Information);
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Execution failed:\n{ex.Message}", "Library", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    /// <summary>
    /// Import a datasheet-extracted component JSON (design-studio.component/1).
    /// Deterministic validators gate the file; the log shows required external
    /// components and suggested high-speed net classes from the datasheet.
    /// </summary>
    private void OnImportJson(object sender, RoutedEventArgs e)
    {
        var dlg = new OpenFileDialog
        {
            Filter = "Component JSON (*.json)|*.json|All files (*.*)|*.*",
            Title = "Import datasheet-extracted component"
        };
        if (dlg.ShowDialog(this) != true) return;
        try
        {
            var result = DatasheetImporter.ImportFile(dlg.FileName);
            MessageBox.Show(string.Join("\n", result.Log),
                result.Ok ? "Import succeeded" : "Import rejected",
                MessageBoxButton.OK,
                result.Ok ? MessageBoxImage.Information : MessageBoxImage.Warning);
            if (result.Ok && result.Footprint is not null)
            {
                _library.Save(result.Footprint);
                RefreshList(SearchBox.Text.Trim());
                LibList.SelectedItem = LibList.Items.OfType<FootprintDef>()
                    .FirstOrDefault(f => f.Name == result.Footprint.Name);
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Import failed:\n{ex.Message}", "Library",
                MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }
}
