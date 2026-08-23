using System.Numerics;

namespace DesignStudio.Model.LinkSim;

// ============================================================================
// E18. Multi-board channel assembly.
//
// A backplane link isn't one board — it's daughtercard → press-fit connector →
// backplane → connector → daughtercard, each a separate S-parameter block that
// must cascade. This assembles an ordered list of 2-port blocks (board segments
// extracted from a routed net, plus vendor connector/cable .s4p imported through
// the existing Touchstone reader) by multiplying their ABCD matrices, and feeds
// the result to the link simulator's pulse-response/eye machinery.
//
// Validated: cascading segments multiplies their losses (each connector adds its
// dB), so more boards/connectors close the eye — the 112G backplane reality.
// ============================================================================

public sealed class ChannelAssembly
{
    private readonly List<SParameterBlock> _blocks = new();

    public ChannelAssembly Add(SParameterBlock? block)
    {
        if (block != null) _blocks.Add(block);
        return this;
    }

    public int SegmentCount => _blocks.Count;

    /// <summary>Cascaded S21 at frequency f (50 Ω reference).</summary>
    public Complex S21(double f)
    {
        Complex A = 1, B = 0, C = 0, D = 1;
        foreach (var blk in _blocks)
        {
            var (a, b, c, d) = blk.Abcd(f);
            Complex na = A * a + B * c, nb = A * b + B * d;
            Complex nc = C * a + D * c, nd = C * b + D * d;
            A = na; B = nb; C = nc; D = nd;
        }
        const double z = 50;
        Complex den = A + B / z + C * z + D;
        return den == Complex.Zero ? Complex.Zero : 2 / den;
    }

    /// <summary>Run the link simulator over the assembled channel.</summary>
    public LinkSimulator.Result Simulate(LinkSimulator.Options opt, string label = "backplane")
        => LinkSimulator.SimulateChannel(S21, opt, label);

    /// <summary>Extract a routed net's channel (board segment) as an S-parameter block.</summary>
    public static SParameterBlock SegmentFromNet(BoardDocument doc, int netId,
                                                 double fStartHz = 1e7, double fStopHz = 4e10, int points = 201)
    {
        var fp = ChannelExtractor.Extract(doc, netId, fStartHz, fStopHz, points);
        return new SParameterBlock
        {
            Name = doc.NetName(netId),
            RefOhm = 50,
            FreqHz = fp.Select(p => p.FreqHz).ToArray(),
            S11 = fp.Select(p => p.S11).ToArray(),
            S21 = fp.Select(p => p.S21).ToArray(),
            S12 = fp.Select(p => p.S12).ToArray(),
            S22 = fp.Select(p => p.S22).ToArray()
        };
    }
}
