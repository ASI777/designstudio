namespace DesignStudio.Model;

/// <summary>
/// Deterministic IPC-7351-style land pattern generators (Phase 2-lite of the
/// autonomous engine): no AI, no manual drawing — footprints computed from
/// package parameters. Density level B (nominal production).
/// </summary>
public static class ParametricFootprints
{
    /// <summary>Adds the standard set to the library (skips names that already exist).</summary>
    public static int SeedInto(FootprintLibrary lib)
    {
        int added = 0;
        foreach (var def in StandardSet())
        {
            if (lib.Items.Any(f => f.Name == def.Name)) continue;
            lib.Save(def);
            added++;
        }
        return added;
    }

    public static List<FootprintDef> StandardSet() => new()
    {
        Chip("R_0402", "Resistor 0402 (1005 metric)", "R", 1.0, 0.5),
        Chip("R_0805", "Resistor 0805 (2012 metric)", "R", 2.0, 1.25),
        Chip("R_1206", "Resistor 1206 (3216 metric)", "R", 3.2, 1.6),
        Chip("C_0402", "Capacitor 0402", "C", 1.0, 0.5),
        Chip("C_0805", "Capacitor 0805", "C", 2.0, 1.25),
        Chip("L_0603", "Inductor 0603", "L", 1.6, 0.8),
        Chip("LED_0603", "LED 0603", "D", 1.6, 0.8),
        Sot23("SOT-23", "SOT-23 transistor/regulator", 3),
        Soic("SOIC-14", "SOIC-14, 1.27 mm pitch", 14),
        Soic("SOIC-16", "SOIC-16, 1.27 mm pitch", 16),
        Qfn("QFN-16_3x3", "QFN-16 3x3 mm, 0.5 mm pitch", 16, 3.0, 0.5),
        Qfn("QFN-32_5x5", "QFN-32 5x5 mm, 0.5 mm pitch", 32, 5.0, 0.5),
        PinHeader("PinHeader_1x02_P2.54", "Pin header 1x2, 2.54 mm", 2),
        PinHeader("PinHeader_1x04_P2.54", "Pin header 1x4, 2.54 mm", 4),
        PinHeader("PinHeader_1x06_P2.54", "Pin header 1x6, 2.54 mm", 6),
    };

    /// <summary>2-pad chip component (IPC-7351B nominal: pad extends ~0.35 mm past body end).</summary>
    public static FootprintDef Chip(string name, string desc, string prefix, double bodyL, double bodyW)
    {
        double padW = 0.5 * bodyL + 0.15;            // toe+heel coverage
        double padH = bodyW + 0.1;
        double padCx = bodyL / 2 + 0.05;
        return new FootprintDef
        {
            Name = name, Description = desc, RefDesPrefix = prefix, Source = "parametric",
            BodyWidthMm = bodyL, BodyHeightMm = bodyW,
            Pads = new List<PadDef>
            {
                new() { Name = "1", XMm = -padCx, WMm = padW, HMm = padH },
                new() { Name = "2", XMm =  padCx, WMm = padW, HMm = padH },
            }
        };
    }

    public static FootprintDef Soic(string name, string desc, int pins)
    {
        var pads = new List<PadDef>();
        int perSide = pins / 2;
        for (int i = 0; i < perSide; i++)
        {
            double y = (i - (perSide - 1) / 2.0) * 1.27;
            pads.Add(new PadDef { Name = (i + 1).ToString(), XMm = -2.7, YMm = y, WMm = 1.5, HMm = 0.6 });
            pads.Add(new PadDef { Name = (pins - i).ToString(), XMm = 2.7, YMm = -y, WMm = 1.5, HMm = 0.6 });
        }
        return new FootprintDef
        {
            Name = name, Description = desc, RefDesPrefix = "U", Source = "parametric",
            BodyWidthMm = 3.9, BodyHeightMm = perSide * 1.27 + 0.7, Pads = pads
        };
    }

    public static FootprintDef Sot23(string name, string desc, int pins)
    {
        // SOT-23-3: pins 1,2 on one side (0.95 mm pitch), pin 3 centred opposite.
        var pads = new List<PadDef>
        {
            new() { Name = "1", XMm = -0.95, YMm =  1.1, WMm = 0.6, HMm = 0.7 },
            new() { Name = "2", XMm =  0.95, YMm =  1.1, WMm = 0.6, HMm = 0.7 },
            new() { Name = "3", XMm =  0.0,  YMm = -1.1, WMm = 0.6, HMm = 0.7 },
        };
        return new FootprintDef
        {
            Name = name, Description = desc, RefDesPrefix = "Q", Source = "parametric",
            BodyWidthMm = 2.9, BodyHeightMm = 1.3, Pads = pads
        };
    }

    /// <summary>QFN, pins counter-clockwise from pin 1 (left side, top pad), no thermal pad yet.</summary>
    public static FootprintDef Qfn(string name, string desc, int pins, double bodyMm, double pitch)
    {
        int perSide = pins / 4;
        double padLen = 0.8, padWid = pitch * 0.55;
        double centre = bodyMm / 2 + padLen / 2 - 0.25;   // pad straddles body edge
        var pads = new List<PadDef>();
        int pin = 1;
        // left side, top→bottom
        for (int i = 0; i < perSide; i++, pin++)
            pads.Add(new PadDef { Name = pin.ToString(), XMm = -centre, YMm = (i - (perSide - 1) / 2.0) * pitch, WMm = padLen, HMm = padWid });
        // bottom side, left→right
        for (int i = 0; i < perSide; i++, pin++)
            pads.Add(new PadDef { Name = pin.ToString(), XMm = (i - (perSide - 1) / 2.0) * pitch, YMm = centre, WMm = padWid, HMm = padLen });
        // right side, bottom→top
        for (int i = 0; i < perSide; i++, pin++)
            pads.Add(new PadDef { Name = pin.ToString(), XMm = centre, YMm = -(i - (perSide - 1) / 2.0) * pitch, WMm = padLen, HMm = padWid });
        // top side, right→left
        for (int i = 0; i < perSide; i++, pin++)
            pads.Add(new PadDef { Name = pin.ToString(), XMm = -(i - (perSide - 1) / 2.0) * pitch, YMm = -centre, WMm = padWid, HMm = padLen });

        return new FootprintDef
        {
            Name = name, Description = desc, RefDesPrefix = "U", Source = "parametric",
            BodyWidthMm = bodyMm, BodyHeightMm = bodyMm, Pads = pads
        };
    }

    /// <summary>Through-hole pin header, 2.54 mm pitch. IPC-2221: drill = lead + 0.25, pad = drill + 0.5.</summary>
    public static FootprintDef PinHeader(string name, string desc, int pins)
    {
        const double pitch = 2.54, lead = 0.64;
        double drill = lead + 0.25;
        double pad = drill + 0.5;
        var pads = new List<PadDef>();
        for (int i = 0; i < pins; i++)
            pads.Add(new PadDef
            {
                Name = (i + 1).ToString(),
                YMm = (i - (pins - 1) / 2.0) * pitch,
                WMm = pad, HMm = pad,
                ThroughHole = true, DrillMm = drill
            });
        return new FootprintDef
        {
            Name = name, Description = desc, RefDesPrefix = "J", Source = "parametric",
            BodyWidthMm = 2.54, BodyHeightMm = pins * pitch, Pads = pads
        };
    }
}
