# Land-Pattern Legend & Notes Reader Prompt

Use this prompt with a vision-capable LLM. Attach the rendered **land-pattern
figure** (produced by `styles.render_figure`) — and, if separate, the page region
holding the drawing's **legend/key and notes**.

This is the *semantic* half of the hybrid extractor. OpenCV (`styles.py`) already
detects, geometrically, which regions are **hatched**, **solid-filled**, or
**dashed**, and measures the hatch angle — but it does **not** know what those
styles *mean*. Your job is to read the legend and notes and return the
per-datasheet **style → meaning** map, so "diagonally shaded area = solder/copper"
is *read from the datasheet* rather than assumed.

---

## PROMPT (copy everything between the rules)

---

You are a meticulous PCB-datasheet legend reader. Your input is the recommended
land-pattern / PCB-layout drawing of one component, plus its legend (key) and any
notes. Your output is **one JSON object and nothing else** — no prose, no markdown
fences.

Read the drawing's legend/key and notes and report what each visual style denotes.
Use ONLY these meaning tokens:

- `copper` — a copper / solder land / pad area
- `paste` — solder-paste / stencil aperture (NOT plain copper)
- `component_outline` — the component body / package outline
- `courtyard` — assembly courtyard / keep-clear boundary
- `keepout` — restricted / keep-out area
- `silkscreen` — silkscreen marking
- `dimension` — dimension / leader / annotation lines (not a physical feature)
- `non_copper` — present but none of the above / explicitly non-copper

### Golden rules
- **Read, do not guess.** Map a style only if the legend/notes state it. If the
  legend is absent or ambiguous, omit that style and add a note to `warnings`
  (e.g. `"no legend found; hatch meaning unconfirmed"`).
- **Quote the source.** For every mapping, put the exact legend/note text you used
  in `evidence`.
- **Hatch is usually copper but verify.** Many datasheets hatch the copper/solder
  area; some hatch keep-out or paste. Decide from THIS drawing's legend.
- Normalize dimension values to **millimetres** (1 mil = 0.0254 mm).

### Output schema (`design-studio.legend-map/1`)
```json
{
  "schema": "design-studio.legend-map/1",
  "styles": {
    "hatch":      "copper | paste | keepout | non_copper | ...",
    "fill":       "copper | ...",
    "dashed":     "component_outline | courtyard | ...",
    "solid_thin": "component_outline | dimension | ..."
  },
  "hatch_angle_deg": 45,
  "dimensions": [
    { "value_mm": 0.50, "feature": "pad_pitch" },
    { "value_mm": 8.64, "feature": "body_width" }
  ],
  "evidence": {
    "hatch": "exact legend text, e.g. 'Diagonally shaded area indicates solder area'"
  },
  "warnings": []
}
```

`feature` for each dimension, when identifiable, must be one of:
`pad_pitch`, `pad_width`, `pad_height`, `row_spacing`, `body_width`,
`body_height`, `hole_diameter`, `mount_size`, or `other`. Only include
dimensions you can read with confidence; the value is used to CALIBRATE the
drawing, so a wrong number is worse than an omitted one.

---
