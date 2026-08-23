using System.Globalization;

namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E4 step 1 — IBIS 7.x buffer model parser.
//
// Reads the parts of an .ibs [Model] the link simulator needs: type, C_comp,
// the supply ([Voltage Range]), edge rates ([Ramp]), the I-V tables
// ([Pulldown]/[Pullup]) and the [Package] R/L/C. From those it derives a linear
// driver (swing, edge rise time, output resistance) for the LTI statistical-eye
// pipeline; the full nonlinear/AMI buffer is E4 step 4 (Phase S6).
//
// The parser is tolerant: unknown keywords and the min/max columns are ignored,
// units (p/n/u/m/k/M/G and V/A/F/H suffixes) are normalised to SI, and a missing
// section just leaves a sensible default. Reserved-word keywords are in [..].
// ============================================================================

public sealed class IbisModel
{
    public string ComponentName = "";
    public string ModelName = "";
    public string ModelType = "";              // Output, I/O, Open_drain, Input…
    public double CCompF;                       // die capacitance
    public double VoltageRangeV = double.NaN;   // supply (typ)
    public double RampDvRiseV, RampDtRiseS;     // [Ramp] dV/dt_r (typ)
    public double RampDvFallV, RampDtFallS;     // [Ramp] dV/dt_f (typ)
    public double RPkgOhm, LPkgH, CPkgF;        // package parasitics
    /// <summary>Pulldown I-V points (V, I_typ); used to estimate output resistance.</summary>
    public readonly List<(double v, double i)> Pulldown = new();
    public readonly List<(double v, double i)> Pullup = new();

    public sealed record LinearDriver(double SwingV, double RiseTimeS, double FallTimeS,
                                      double ROutOhm, double CLoadF);

    /// <summary>Linear driver for the LTI eye: swing, edge time, output R, die+pkg C.</summary>
    public LinearDriver ToLinearDriver()
    {
        double swing = !double.IsNaN(VoltageRangeV) && VoltageRangeV > 0
            ? VoltageRangeV
            : (RampDvRiseV > 0 ? RampDvRiseV / 0.6 : 1.0);    // ramp dV is ~20-80 % of swing
        double tr = RampDtRiseS > 0 ? RampDtRiseS : 0.0;
        double tf = RampDtFallS > 0 ? RampDtFallS : tr;
        double rout = OutputResistance();
        double cload = CCompF + CPkgF;
        return new LinearDriver(swing, tr, tf, rout, cload);
    }

    /// <summary>Output resistance from the pulldown I-V slope near mid-swing, else 50 Ω.</summary>
    public double OutputResistance()
    {
        var t = Pulldown.Count >= 2 ? Pulldown : Pullup;
        if (t.Count < 2) return 50.0;
        // slope dI/dV across the table's active span (skip clamp tails)
        var pts = t.OrderBy(p => p.v).ToList();
        double v0 = pts[0].v, v1 = pts[^1].v, i0 = pts[0].i, i1 = pts[^1].i;
        double di = i1 - i0, dv = v1 - v0;
        if (Math.Abs(di) < 1e-9 || Math.Abs(dv) < 1e-9) return 50.0;
        double r = Math.Abs(dv / di);
        return double.IsFinite(r) && r > 1 ? Math.Min(r, 1000) : 50.0;
    }

    // ---- parser --------------------------------------------------------------

    public static IbisModel Parse(string text)
    {
        var m = new IbisModel();
        string section = "";
        foreach (var raw in text.Split('\n'))
        {
            string line = raw;
            int bar = line.IndexOf('|');                     // IBIS comment char
            if (bar >= 0) line = line[..bar];
            line = line.Trim();
            if (line.Length == 0) continue;

            if (line.StartsWith('['))
            {
                int close = line.IndexOf(']');
                string kw = (close > 0 ? line[1..close] : line[1..]).Trim().ToLowerInvariant();
                string rest = close > 0 ? line[(close + 1)..].Trim() : "";
                section = kw;
                switch (kw)
                {
                    case "component": m.ComponentName = rest; break;
                    case "model": m.ModelName = rest; break;
                    case "voltage range": m.VoltageRangeV = FirstNumber(rest); break;
                }
                continue;
            }

            var toks = line.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
            if (toks.Length == 0) continue;
            string key = toks[0].ToLowerInvariant();

            switch (section)
            {
                case "model":
                    if (key == "model_type" && toks.Length > 1) m.ModelType = toks[1];
                    else if (key == "c_comp") m.CCompF = Unit(toks.ElementAtOrDefault(1));
                    break;
                case "voltage range":
                    if (double.IsNaN(m.VoltageRangeV)) m.VoltageRangeV = Unit(toks[0]);
                    break;
                case "ramp":
                    if (key == "dv/dt_r") { var (dv, dt) = Slope(toks.ElementAtOrDefault(1)); m.RampDvRiseV = dv; m.RampDtRiseS = dt; }
                    else if (key == "dv/dt_f") { var (dv, dt) = Slope(toks.ElementAtOrDefault(1)); m.RampDvFallV = dv; m.RampDtFallS = dt; }
                    break;
                case "package":
                    if (key == "r_pkg") m.RPkgOhm = Unit(toks.ElementAtOrDefault(1));
                    else if (key == "l_pkg") m.LPkgH = Unit(toks.ElementAtOrDefault(1));
                    else if (key == "c_pkg") m.CPkgF = Unit(toks.ElementAtOrDefault(1));
                    break;
                case "pulldown":
                case "pullup":
                    if (TryNum(toks[0], out double v) && toks.Length > 1 && TryNum(toks[1], out double i))
                        (section == "pulldown" ? m.Pulldown : m.Pullup).Add((v, i));
                    break;
            }
        }
        return m;
    }

    // ---- number / unit helpers ----

    private static bool TryNum(string? s, out double v)
    {
        v = 0;
        if (string.IsNullOrEmpty(s)) return false;
        // strip a trailing engineering/unit suffix for the bare-number test
        return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v)
               || !double.IsNaN(v = Unit(s));
    }

    private static double FirstNumber(string s)
    {
        foreach (var t in s.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries))
        { double x = Unit(t); if (!double.IsNaN(x)) return x; }
        return double.NaN;
    }

    /// <summary>Parse "0.45/0.310n" → (dV volts, dt seconds).</summary>
    private static (double dv, double dt) Slope(string? s)
    {
        if (string.IsNullOrEmpty(s)) return (0, 0);
        var parts = s.Split('/');
        if (parts.Length < 2) return (0, 0);
        double dv = Unit(parts[0]), dt = Unit(parts[1]);
        return (double.IsNaN(dv) ? 0 : dv, double.IsNaN(dt) ? 0 : dt);
    }

    /// <summary>Number with an optional engineering suffix and unit letter → SI.</summary>
    public static double Unit(string? s)
    {
        if (string.IsNullOrEmpty(s) || s.Equals("NA", StringComparison.OrdinalIgnoreCase))
            return double.NaN;
        s = s.Trim().TrimEnd('V', 'A', 'F', 'H', 'v', 'a', 'f', 'h');   // drop unit letter
        if (s.EndsWith("Ohm", StringComparison.OrdinalIgnoreCase)) s = s[..^3];
        if (s.Length == 0) return double.NaN;
        double mult = 1;
        char c = s[^1];
        switch (c)
        {
            case 'T': mult = 1e12; s = s[..^1]; break;
            case 'G': mult = 1e9; s = s[..^1]; break;
            case 'M': mult = 1e6; s = s[..^1]; break;
            case 'k': case 'K': mult = 1e3; s = s[..^1]; break;
            case 'm': mult = 1e-3; s = s[..^1]; break;
            case 'u': mult = 1e-6; s = s[..^1]; break;
            case 'n': mult = 1e-9; s = s[..^1]; break;
            case 'p': mult = 1e-12; s = s[..^1]; break;
            case 'f': mult = 1e-15; s = s[..^1]; break;
        }
        return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out double v)
            ? v * mult : double.NaN;
    }
}
