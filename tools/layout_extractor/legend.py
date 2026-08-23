#!/usr/bin/env python3
"""Consume the vision-LLM's `legend-map/1` and join it with OpenCV's detected
styles (styles.py) so every region gets a MEANING read from the datasheet.

Workflow (offline-attach, matching the repo's EXTRACTOR_PROMPT pattern):
  1. locate.locate_layout(pdf)            -> figure page+clip
  2. styles.detect_styles(page, clip)     -> regions tagged hatch/fill/dash
  3. styles.render_figure(...)            -> PNG to attach to the vision-LLM
  4. vision-LLM + docs/.../LEGEND_PROMPT.md -> legend-map/1 JSON
  5. load_legend_map(json); apply(...)    -> regions tagged with `meaning`

If no legend-map is supplied, DEFAULT_LEGEND is used (the common convention) and
every applied meaning is flagged `assumed=True` so downstream can warn.
"""
import json

MEANINGS = {"copper", "paste", "component_outline", "courtyard",
            "keepout", "silkscreen", "dimension", "non_copper"}

# common-but-not-guaranteed convention; used only when the LLM map is absent.
DEFAULT_LEGEND = {
    "schema": "design-studio.legend-map/1",
    "styles": {"hatch": "copper", "fill": "copper",
               "dashed": "component_outline", "solid_thin": "component_outline"},
    "hatch_angle_deg": None, "dimensions": [], "evidence": {},
    "warnings": ["DEFAULT legend assumed (no legend-map supplied); verify hatch=copper"],
    "_assumed": True,
}


def load_legend_map(path_or_obj):
    """Load + validate a legend-map/1 (path, JSON string, or dict). Raises on a
    malformed map; unknown meaning tokens are rejected so a bad LLM reply can't
    silently mislabel copper."""
    if isinstance(path_or_obj, dict):
        m = path_or_obj
    else:
        s = path_or_obj
        if isinstance(s, str) and "{" not in s:
            s = open(s).read()
        m = json.loads(s)
    if m.get("schema") != "design-studio.legend-map/1":
        raise ValueError(f"not a legend-map/1 (schema={m.get('schema')!r})")
    styles = m.get("styles", {})
    if not isinstance(styles, dict) or not styles:
        raise ValueError("legend-map has no styles")
    bad = {v for v in styles.values() if v not in MEANINGS}
    if bad:
        raise ValueError(f"unknown meaning token(s): {sorted(bad)} (allowed: {sorted(MEANINGS)})")
    m.setdefault("dimensions", []); m.setdefault("warnings", [])
    m["_assumed"] = False
    return m


def meaning_for(style_label, legend_map):
    """Map a detected style ('hatch'|'fill'|'dash'|'solid_thin') to a meaning."""
    key = "dashed" if style_label == "dash" else style_label
    return legend_map.get("styles", {}).get(key)


def apply(detected, legend_map=None):
    """Annotate styles.detect_styles() output with `meaning` + `assumed`. Returns
    a flat list of regions plus the resolved legend_map."""
    lm = legend_map or DEFAULT_LEGEND
    assumed = lm.get("_assumed", False)
    out = []
    for style_label, regions in detected.items():
        meaning = meaning_for(style_label, lm)
        for r in regions:
            rr = dict(r); rr["style"] = style_label
            rr["meaning"] = meaning
            rr["assumed"] = assumed or meaning is None
            out.append(rr)
    return out, lm


def pitch_mm_from_dimensions(legend_map, feature="pad_pitch"):
    """The LLM-read dimension value for a feature (used to CALIBRATE px/mm)."""
    for d in legend_map.get("dimensions", []):
        if d.get("feature") == feature and isinstance(d.get("value_mm"), (int, float)):
            return float(d["value_mm"])
    return None


if __name__ == "__main__":
    import sys, fitz, locate, styles
    from runtime_paths import sample_datasheet
    pdf = sys.argv[1] if len(sys.argv) > 1 else str(sample_datasheet())
    lm = load_legend_map(sys.argv[2]) if len(sys.argv) > 2 else None
    fig = locate.locate_layout(pdf)
    page = fitz.open(pdf)[fig.page]
    detected = styles.detect_styles(page, fig.clip)
    regions, used = apply(detected, lm)
    print(f"legend source: {'LLM map' if lm else 'DEFAULT (assumed)'}")
    print(f"pad_pitch from map: {pitch_mm_from_dimensions(used)}")
    by = {}
    for r in regions:
        key = (r["style"], r["meaning"], r["assumed"])
        by[key] = by.get(key, 0) + 1
    for (s, m, a), n in sorted(by.items()):
        print(f"  {s:10} -> meaning={m!s:18} assumed={a}  ({n} region(s))")
