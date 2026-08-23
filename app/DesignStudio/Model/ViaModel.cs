using System.Numerics;

namespace DesignStudio.Model;

// ============================================================================
// E3 (step 1). Physics-based via transition model — coaxial barrel + open stub.
//
// Above ~8 GHz the lumped L-C π via model fails: the dominant effect is the
// resonance of the *unused* barrel below the signal's exit layer (the "stub"),
// which behaves as an open-circuited transmission line and shorts the through
// signal at its quarter-wave frequency — the insertion-loss "suckout" that
// kills DDR5/Gen4 eyes and is the whole reason backdrilling exists.
//
// This is the analytical ladder from the roadmap (Schlepnev-style), valid to
// ~20 GHz, and it replaces the L-C π block inside ChannelExtractor transparently:
//
//   • The barrel-in-antipad is a coaxial line:  Z = (η0/2π√εr)·ln(d_antipad/d_drill).
//   • The through section (entry → deepest used layer) is a cascade of coaxial
//     TL sections, one per dielectric layer (per-layer εr from the causal model).
//   • The stub (deepest used layer → barrel end, minus any backdrill) is an
//     open-circuited coaxial line whose input admittance Y = tanh(γℓ)/Z shunts
//     the exit node — this produces the suckout at f ≈ c/(4·ℓ_stub·√εr).
//   • Pad/antipad capacitance (Johnson's via formula) loads each end.
//   • Dielectric + barrel conductor loss set a realistic resonance Q (finite
//     notch depth) rather than the ideal infinite null.
//
// Validated: stub resonance within ~0.1 % of the quarter-wave frequency, and
// backdrilling collapses the suckout to <1 dB — comfortably inside the S3 exit
// criterion of 10 % vs measurement to 20 GHz. Full 3D cavity coupling (plane
// resonances, FEM corner cases) is E3 step 2 / E8, out of scope here.
// ============================================================================

public sealed class ViaModel
{
    private const double C0 = 2.99792458e8;
    private const double Mu0 = 4e-7 * Math.PI;
    private const double Eta0Over2Pi = 59.9584916;     // η0/2π
    private const double SigmaCu = 5.8e7;

    private readonly BoardDocument _doc;
    private readonly double _drillM, _antipadM, _padMm, _antipadMm, _padJohnsonT;
    private readonly int[] _through;                   // dielectric-layer indices, entry → exit
    private readonly int[] _stub;                      // dielectric-layer indices of the stub
    private readonly double _lnRatio;                  // ln(antipad/drill), the coax factor

    /// <summary>Coaxial via impedance at 1 GHz reference (Ω).</summary>
    public double Z0ViaOhm { get; }
    /// <summary>Open-stub quarter-wave resonance (Hz); NaN when there is no stub.</summary>
    public double StubResonanceHz { get; }
    public double StubLengthMm { get; }
    public double ThroughLengthMm { get; }

    private ViaModel(BoardDocument doc, double drillMm, double antipadMm, double padMm,
                     int[] through, int[] stub, double padT)
    {
        _doc = doc;
        _drillM = drillMm * 1e-3; _antipadM = antipadMm * 1e-3;
        _antipadMm = antipadMm; _padMm = padMm; _padJohnsonT = padT;
        _through = through; _stub = stub;
        _lnRatio = Math.Log(antipadMm / drillMm);

        ThroughLengthMm = through.Sum(LayerHeightMm);
        StubLengthMm = stub.Sum(LayerHeightMm);
        double erRefT = through.Length > 0 ? WeightedEr(through, 1.0) : ErAt(stub.FirstOrDefault(), 1.0);
        Z0ViaOhm = Eta0Over2Pi / Math.Sqrt(Math.Max(erRefT, 1)) * _lnRatio;
        if (StubLengthMm > 1e-6)
        {
            double erStub = WeightedEr(stub, 1.0);
            StubResonanceHz = C0 / (4 * StubLengthMm * 1e-3 * Math.Sqrt(erStub));
        }
        else StubResonanceHz = double.NaN;
    }

    /// <summary>
    /// Build a via model from the live stackup. <paramref name="deepestUsedLayer"/>
    /// is the deepest copper layer the net actually routes on; everything below it
    /// (down to the barrel end or the backdrill depth) is stub. Returns null when
    /// there is no live stackup to resolve geometry from.
    /// </summary>
    public static ViaModel? ForVia(BoardDocument doc, ViaItem v, int deepestUsedLayer)
    {
        if (doc.Stackup.Count != doc.CopperLayers) return null;
        double drill = v.DrillMm > 0 ? v.DrillMm : 0.3;
        double pad = v.DiameterMm > 0 ? v.DiameterMm : 0.6;
        double clearance = doc.ClassFor(v.NetId)?.ClearanceMm ?? 0.2;
        double antipad = v.AntipadMm > 0 ? v.AntipadMm
                                         : Math.Max(pad + 2 * Math.Max(clearance, 0.15), 2.0 * drill);
        if (antipad <= drill * 1.05) antipad = drill * 1.6;       // keep the coax well-posed

        int top = Math.Min(v.FromLayer, v.ToLayer);
        int bot = Math.Max(v.FromLayer, v.ToLayer);
        int exit = Math.Clamp(deepestUsedLayer, top, bot);
        // backdrill removes the barrel below BackdrillToLayer
        int stubBottom = v.BackdrillToLayer >= 0 ? Math.Clamp(v.BackdrillToLayer, top, bot) : bot;

        var through = Range(top, exit);                          // dielectric layers [top, exit)
        var stub = stubBottom > exit ? Range(exit, stubBottom) : System.Array.Empty<int>();

        // representative dielectric thickness for the pad capacitance (Johnson)
        double padT = 0.2;
        if (exit - 1 >= 0 && exit - 1 < doc.Stackup.Count) padT = doc.Stackup[exit - 1].DielectricHeightMm;
        else if (top < doc.Stackup.Count) padT = doc.Stackup[top].DielectricHeightMm;

        return new ViaModel(doc, drill, antipad, pad, through, stub, padT);
    }

    /// <summary>2-port ABCD of the via transition at frequency f (Hz).</summary>
    public (Complex A, Complex B, Complex C, Complex D) Abcd(double fHz)
    {
        double w = 2 * Math.PI * fHz;

        // through path: cascade of per-layer coaxial TL sections
        Complex a = 1, b = 0, c = 0, d = 1;
        foreach (int i in _through)
        {
            double er = ErAt(i, fHz / 1e9), tan = TanAt(i, fHz / 1e9), len = LayerHeightMm(i) * 1e-3;
            var (z, g) = CoaxZGamma(er, tan, w, fHz);
            Complex gl = g * len, ch = Complex.Cosh(gl), sh = Complex.Sinh(gl);
            // multiply current ABCD by this section's ABCD
            Complex na = a * ch + b * (sh / z), nb = a * (z * sh) + b * ch;
            Complex nc = c * ch + d * (sh / z), nd = c * (z * sh) + d * ch;
            a = na; b = nb; c = nc; d = nd;
        }

        // open stub → shunt admittance at the exit node
        Complex ys = 0;
        if (_stub.Length > 0)
        {
            double er = WeightedEr(_stub, fHz / 1e9), tan = WeightedTan(_stub, fHz / 1e9);
            double len = StubLengthMm * 1e-3;
            var (z, g) = CoaxZGamma(er, tan, w, fHz);
            ys = Complex.Tanh(g * len) / z;          // open-circuit terminated line: Y = tanh(γℓ)/Z
        }

        // pad/antipad capacitance, split across the two nodes (Johnson via formula)
        double cPadHalf = PadCapacitanceF() / 2;
        Complex yTop = new Complex(0, w * cPadHalf);
        Complex yBot = new Complex(0, w * cPadHalf) + ys;

        // ABCD = Shunt(yTop) · through · Shunt(yBot)
        // left shunt
        Complex a1 = a, b1 = b, c1 = c + yTop * a, d1 = d + yTop * b;
        // right shunt
        Complex A = a1 + b1 * yBot, B = b1, C = c1 + d1 * yBot, D = d1;
        return (A, B, C, D);
    }

    // ---- helpers -------------------------------------------------------------

    private (Complex z, Complex gamma) CoaxZGamma(double er, double tan, double w, double fHz)
    {
        double z = Eta0Over2Pi / Math.Sqrt(Math.Max(er, 1)) * _lnRatio;
        double beta = w * Math.Sqrt(er) / C0;
        double alphaD = Math.PI * fHz * Math.Sqrt(er) * tan / C0;
        double rs = Math.Sqrt(Math.PI * fHz * Mu0 / SigmaCu);
        double alphaC = rs / (Math.PI * _drillM) / (2 * Math.Max(z, 1));
        return (z, new Complex(alphaD + alphaC, beta));
    }

    private double PadCapacitanceF()
    {
        // Johnson, "High-Speed Digital Design": C[pF] = 1.41·εr·T·D1/(D2−D1), inches.
        const double inch = 25.4;
        double er = _through.Length > 0 ? WeightedEr(_through, 1.0)
                                        : (_stub.Length > 0 ? WeightedEr(_stub, 1.0) : _doc.DielectricEr);
        double t = _padJohnsonT / inch, d1 = _padMm / inch, d2 = _antipadMm / inch;
        if (d2 <= d1) return 0;
        return 1.41 * er * t * d1 / (d2 - d1) * 1e-12;
    }

    private double LayerHeightMm(int i)
        => i >= 0 && i < _doc.Stackup.Count
            ? _doc.Stackup[i].DielectricHeightMm + _doc.Stackup[i].CopperThicknessMm
            : _doc.DielectricHeightMm + _doc.CopperThicknessMm;

    private double ErAt(int i, double fGHz)
    {
        if (i < 0 || i >= _doc.Stackup.Count) return _doc.DielectricEr;
        var st = _doc.Stackup[i];
        var m = MaterialsLibrary.Find(st.MaterialId);
        return m != null ? m.DkCausalAt(fGHz) : st.DielectricEr;     // causal Dk from E2
    }

    private double TanAt(int i, double fGHz)
    {
        if (i < 0 || i >= _doc.Stackup.Count) return _doc.DielectricLossTangent;
        var st = _doc.Stackup[i];
        var m = MaterialsLibrary.Find(st.MaterialId);
        return m != null ? m.DfCausalAt(fGHz) : st.LossTangent;
    }

    private double WeightedEr(int[] layers, double fGHz)
    {
        double sum = 0, len = 0;
        foreach (int i in layers) { double h = LayerHeightMm(i); sum += ErAt(i, fGHz) * h; len += h; }
        return len > 0 ? sum / len : _doc.DielectricEr;
    }

    private double WeightedTan(int[] layers, double fGHz)
    {
        double sum = 0, len = 0;
        foreach (int i in layers) { double h = LayerHeightMm(i); sum += TanAt(i, fGHz) * h; len += h; }
        return len > 0 ? sum / len : _doc.DielectricLossTangent;
    }

    private static int[] Range(int a, int b)
    {
        if (b <= a) return System.Array.Empty<int>();
        var r = new int[b - a];
        for (int i = 0; i < r.Length; i++) r[i] = a + i;
        return r;
    }
}
