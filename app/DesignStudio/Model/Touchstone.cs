using System.IO;
using System.Numerics;
using System.Text.Json.Serialization;

namespace DesignStudio.Model;

// ============================================================================
// S1/E8 — Touchstone import: vendor S-parameter models as channel blocks.
//
// Parses Touchstone v1 2-port files (.s2p): any frequency unit (Hz…GHz),
// any format (RI, MA, DB), any reference impedance. A parsed block is
// attached to a net and becomes one more two-port in the ABCD cascade that
// ChannelExtractor builds — so a vendor connector, cable or package model
// drops into the simulated channel exactly where it belongs.
//
// Interpolation is linear on the complex values between measured points and
// clamped at the ends (standard practice; extrapolating S-parameters is how
// people get gain out of connectors).
// ============================================================================

public class SParameterBlock
{
    public string Name = "";
    public double RefOhm = 50;
    public double[] FreqHz = Array.Empty<double>();
    public Complex[] S11 = Array.Empty<Complex>(), S21 = Array.Empty<Complex>(),
                     S12 = Array.Empty<Complex>(), S22 = Array.Empty<Complex>();

    public (Complex s11, Complex s21, Complex s12, Complex s22) At(double fHz)
    {
        if (FreqHz.Length == 0) return (0, 1, 1, 0);     // empty = ideal through
        if (fHz <= FreqHz[0]) return (S11[0], S21[0], S12[0], S22[0]);
        if (fHz >= FreqHz[^1]) return (S11[^1], S21[^1], S12[^1], S22[^1]);
        int i = Array.BinarySearch(FreqHz, fHz);
        if (i >= 0) return (S11[i], S21[i], S12[i], S22[i]);
        i = ~i;
        double u = (fHz - FreqHz[i - 1]) / (FreqHz[i] - FreqHz[i - 1]);
        Complex L(Complex a, Complex b) => a + u * (b - a);
        return (L(S11[i - 1], S11[i]), L(S21[i - 1], S21[i]),
                L(S12[i - 1], S12[i]), L(S22[i - 1], S22[i]));
    }

    /// <summary>ABCD matrix at f, renormalised through the block's own reference impedance.</summary>
    public (Complex A, Complex B, Complex C, Complex D) Abcd(double fHz)
    {
        var (s11, s21, s12, s22) = At(fHz);
        Complex z = RefOhm;
        Complex den = 2 * s21;
        if (den == Complex.Zero) den = 1e-30;
        var a = ((1 + s11) * (1 - s22) + s12 * s21) / den;
        var b = z * ((1 + s11) * (1 + s22) - s12 * s21) / den;
        var c = ((1 - s11) * (1 - s22) - s12 * s21) / den / z;
        var d = ((1 - s11) * (1 + s22) + s12 * s21) / den;
        return (a, b, c, d);
    }

    // ---- parser ----

    public static SParameterBlock Parse(string text, string name = "")
    {
        var blk = new SParameterBlock { Name = name };
        double unit = 1e9;                    // Touchstone default: GHz
        string fmt = "MA";                    // Touchstone default: magnitude/angle
        var f = new List<double>();
        var s = new List<Complex>[4] { new(), new(), new(), new() };
        var pending = new List<double>();     // values may wrap across lines

        foreach (var raw in text.Split('\n'))
        {
            string line = raw.Trim();
            int bang = line.IndexOf('!');
            if (bang >= 0) line = line[..bang].Trim();
            if (line.Length == 0) continue;

            if (line.StartsWith('#'))
            {
                var opts = line[1..].Split(' ', StringSplitOptions.RemoveEmptyEntries);
                for (int i = 0; i < opts.Length; i++)
                {
                    switch (opts[i].ToUpperInvariant())
                    {
                        case "HZ": unit = 1; break;
                        case "KHZ": unit = 1e3; break;
                        case "MHZ": unit = 1e6; break;
                        case "GHZ": unit = 1e9; break;
                        case "RI": fmt = "RI"; break;
                        case "MA": fmt = "MA"; break;
                        case "DB": fmt = "DB"; break;
                        case "R": if (i + 1 < opts.Length &&
                                      double.TryParse(opts[i + 1], System.Globalization.CultureInfo.InvariantCulture, out var r))
                                  { blk.RefOhm = r; i++; }
                                  break;
                    }
                }
                continue;
            }

            foreach (var tok in line.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries))
                if (double.TryParse(tok, System.Globalization.CultureInfo.InvariantCulture, out double v))
                    pending.Add(v);

            while (pending.Count >= 9)
            {
                f.Add(pending[0] * unit);
                for (int p = 0; p < 4; p++)
                {
                    double x = pending[1 + p * 2], y = pending[2 + p * 2];
                    s[p].Add(fmt switch
                    {
                        "RI" => new Complex(x, y),
                        "DB" => Complex.FromPolarCoordinates(Math.Pow(10, x / 20), y * Math.PI / 180),
                        _ => Complex.FromPolarCoordinates(x, y * Math.PI / 180)
                    });
                }
                pending.RemoveRange(0, 9);
            }
        }

        blk.FreqHz = f.ToArray();
        // Touchstone 2-port column order is S11 S21 S12 S22
        blk.S11 = s[0].ToArray(); blk.S21 = s[1].ToArray();
        blk.S12 = s[2].ToArray(); blk.S22 = s[3].ToArray();
        return blk;
    }

    public static SParameterBlock Load(string path)
        => Parse(File.ReadAllText(path), Path.GetFileNameWithoutExtension(path));
}

/// <summary>A vendor S-parameter model attached to a net (persisted by file path).</summary>
public class ChannelBlock
{
    [JsonPropertyName("net")] public int NetId { get; set; } = -1;
    [JsonPropertyName("path")] public string FilePath { get; set; } = "";

    [JsonIgnore] private SParameterBlock? _parsed;
    [JsonIgnore] public string? LoadError { get; private set; }

    public SParameterBlock? Parsed()
    {
        if (_parsed != null) return _parsed;
        try { _parsed = SParameterBlock.Load(FilePath); LoadError = null; }
        catch (Exception ex) { LoadError = ex.Message; }
        return _parsed;
    }
}
