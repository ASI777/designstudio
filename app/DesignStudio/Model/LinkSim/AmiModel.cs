using System.Globalization;
using System.Runtime.InteropServices;

namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E17. Vendor IBIS-AMI model support.
//
// A real SerDes sign-off uses the vendor's AMI model: a compiled .dll/.so
// (AMI_Init → AMI_GetWave → AMI_Close) configured by a `.ami` parameter file —
// a nested-parenthesis tree of (name (Usage…)(Type…)(Value…)) clauses.
//
// This parses the `.ami` tree, maps its Model_Specific CTLE/FFE/DFE parameters
// onto the in-house equalisers (so the eye opens exactly as the parameters say),
// and exposes `IAmiModel` implementations: `ParametricAmi` (the managed default,
// driven by the parsed parameters) and `NativeAmi` (the P/Invoke wrapper around a
// real vendor binary — present for completeness; loading an actual vendor .dll is
// a platform integration and can't run in the cross-platform test suite).
// ============================================================================

/// <summary>A node in the parsed .ami parenthesis tree.</summary>
public sealed class AmiNode
{
    public string Name = "";
    public readonly List<string> Values = new();
    public readonly List<AmiNode> Children = new();

    public AmiNode? Child(string name)
        => Children.FirstOrDefault(c => c.Name.Equals(name, StringComparison.OrdinalIgnoreCase));

    public AmiNode? Descend(params string[] path)
    {
        var node = this;
        foreach (var p in path) { node = node?.Child(p); if (node == null) return null; }
        return node;
    }

    /// <summary>The value(s) of a parameter: its (Value …) child, else its own values.</summary>
    public IReadOnlyList<string> ValueOf(string param)
    {
        var pn = Child(param);
        if (pn == null) return System.Array.Empty<string>();
        var v = pn.Child("Value");
        return (v != null ? v.Values : pn.Values);
    }

    public double Float(string param, double dflt = 0)
        => ValueOf(param) is { Count: > 0 } vs &&
           double.TryParse(vs[0], NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d : dflt;

    public double[] Floats(string param)
        => ValueOf(param).Select(s => double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d : 0)
                         .ToArray();

    public int Int(string param, int dflt = 0) => (int)Math.Round(Float(param, dflt));
}

public static class AmiModel
{
    // ---- parser --------------------------------------------------------------

    public static AmiNode Parse(string text)
    {
        var toks = Tokenize(text);
        int pos = 0;
        return ParseNode(toks, ref pos) ?? new AmiNode();
    }

    private static List<string> Tokenize(string s)
    {
        var toks = new List<string>();
        int i = 0;
        while (i < s.Length)
        {
            char c = s[i];
            if (c == '|') { while (i < s.Length && s[i] != '\n') i++; continue; }   // IBIS comment
            if (char.IsWhiteSpace(c)) { i++; continue; }
            if (c == '(' || c == ')') { toks.Add(c.ToString()); i++; continue; }
            if (c == '"')
            {
                int j = ++i; var sb = new System.Text.StringBuilder();
                while (j < s.Length && s[j] != '"') sb.Append(s[j++]);
                toks.Add(sb.ToString()); i = j + 1; continue;
            }
            int k = i;
            while (k < s.Length && !char.IsWhiteSpace(s[k]) && s[k] != '(' && s[k] != ')') k++;
            toks.Add(s[i..k]); i = k;
        }
        return toks;
    }

    private static AmiNode? ParseNode(List<string> toks, ref int pos)
    {
        while (pos < toks.Count && toks[pos] != "(") pos++;
        if (pos >= toks.Count) return null;
        pos++;                                            // consume '('
        var node = new AmiNode();
        if (pos < toks.Count && toks[pos] != ")" && toks[pos] != "(") node.Name = toks[pos++];
        while (pos < toks.Count && toks[pos] != ")")
        {
            if (toks[pos] == "(") { var child = ParseNode(toks, ref pos); if (child != null) node.Children.Add(child); }
            else node.Values.Add(toks[pos++]);
        }
        if (pos < toks.Count) pos++;                      // consume ')'
        return node;
    }

    // ---- map .ami parameters → equaliser ------------------------------------

    /// <summary>Build the in-house equaliser from a parsed AMI tree's Model_Specific
    /// CTLE / FFE / DFE parameters.</summary>
    public static Equalizer ToEqualizer(AmiNode root)
    {
        var ms = root.Child("Model_Specific") ?? root;

        Ctle? ctle = null;
        var ctleNode = ms.Child("CTLE") ?? ms.Child("RxCTLE");
        if (ctleNode != null)
            ctle = new Ctle(
                DcGainDb: ctleNode.Float("dc_gain", ctleNode.Float("gain_db", 0)),
                ZeroHz: ctleNode.Float("zero_ghz", 4) * 1e9,
                PoleHz: ctleNode.Float("pole_ghz", 12) * 1e9);

        TxFfe? ffe = null;
        var ffeNode = ms.Child("FFE") ?? ms.Child("TxFFE");
        if (ffeNode != null)
        {
            double[] taps = ffeNode.Floats("taps");
            if (taps.Length == 0) taps = ffeNode.Floats("tap");
            if (taps.Length > 0)
            {
                int main = 0; double mx = -1;
                for (int i = 0; i < taps.Length; i++) if (Math.Abs(taps[i]) > mx) { mx = Math.Abs(taps[i]); main = i; }
                ffe = new TxFfe(taps, main);
            }
        }

        Dfe? dfe = null;
        var dfeNode = ms.Child("DFE") ?? ms.Child("RxDFE");
        if (dfeNode != null)
        {
            int n = dfeNode.Int("taps", dfeNode.Int("ntaps", 0));
            if (n > 0) dfe = new Dfe(n);
        }

        return new Equalizer { Ctle = ctle, Ffe = ffe, Dfe = dfe };
    }
}

/// <summary>Reference AMI model: the in-house equalisers configured from a parsed
/// .ami file. CTLE is applied in the frequency domain by the link simulator
/// (set <c>Options.Eq = ami.Equalizer</c>); FFE/DFE here for the IAmiModel flow.</summary>
public sealed class ParametricAmi : IAmiModel
{
    public Equalizer Equalizer { get; }
    public string Name { get; }

    public ParametricAmi(AmiNode amiRoot, string name = "AMI")
    { Equalizer = AmiModel.ToEqualizer(amiRoot); Name = name; }

    public (double[] Pulse, int DfeTaps) Process(double[] pulse, double dtS, double uiS)
        => (Equalizer.ApplyFfe(pulse, dtS, uiS), Equalizer.DfeTaps);
}

/// <summary>
/// P/Invoke wrapper around a compiled vendor AMI binary (AMI_Init/GetWave/Close).
/// Loading a real vendor .dll/.so is a platform integration — this resolves the
/// library at runtime and surfaces a clear error when it (or the symbols) are
/// absent, which is the case in the cross-platform test environment.
/// </summary>
public sealed class NativeAmi : IAmiModel
{
    private readonly string _path;
    public string Name { get; }

    public NativeAmi(string dllPath, string name = "vendor-AMI") { _path = dllPath; Name = name; }

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int AmiInit(IntPtr impulse, int rows, int cols, double sampleS,
                                 IntPtr amiParamsIn, ref IntPtr amiParamsOut, ref IntPtr memHandle, IntPtr msg);

    public (double[] Pulse, int DfeTaps) Process(double[] pulse, double dtS, double uiS)
    {
        if (!NativeLibrary.TryLoad(_path, out var lib))
            throw new DllNotFoundException($"vendor AMI model '{_path}' not available on this platform");
        try
        {
            // A real flow marshals the impulse/parameter tree through AMI_Init and
            // (for time-domain) AMI_GetWave, then AMI_Close. Left as the integration
            // point; the managed ParametricAmi is the default path.
            if (!NativeLibrary.TryGetExport(lib, "AMI_Init", out _))
                throw new EntryPointNotFoundException("AMI_Init not exported by the vendor model");
            throw new NotSupportedException("native AMI_GetWave marshalling is a platform integration; use ParametricAmi");
        }
        finally { NativeLibrary.Free(lib); }
    }
}
