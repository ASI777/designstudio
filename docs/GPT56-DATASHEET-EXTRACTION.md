# GPT-5.6 Sol/Luna datasheet-to-component CAD

DesignStudio accepts an exact MPN plus a local PDF, manufacturer HTTPS URL, or
configured supplier result. It hashes and caches the PDF and proves that its
text contains the requested MPN before spending model tokens.

GPT-5.6 Sol at medium reasoning visually inspects labelled page contact sheets.
It identifies the package variant and selects tight overview, pinout, package,
orientation, and recommended-layout regions. Only those regions are rendered
at high resolution for GPT-5.6 Luna at xhigh reasoning. Both stages run through
read-only, ephemeral Codex CLI processes and reuse the user's `codex login`.

Luna extracts the symbol, electrical data, land pattern, and an executable
package construction. Deterministic postprocessing then:

1. verifies exact-MPN and source-digest evidence;
2. regenerates or grounds SMT/THT pad geometry;
3. validates numbering, overlap, dimensions, drills, and annular rings;
4. creates the exact placement courtyard and pin-to-pad mapping;
5. validates the typed symbol, footprint, and model command program;
6. executes package solids through Open CASCADE;
7. exports and round-trips the authoritative STEP model; and
8. records both model stages, selected regions, commands, and gate results.

A successful extraction creates an isolated combined 2D/3D preview with
`state=review_required` and `placement_allowed=false`. It does not modify the
component library or active PCB. Missing, ambiguous, assumed, or unproven
dimensions stop CAD execution instead of producing estimated manufacturing
geometry.

```sh
python3 tools/extract_component_datasheet.py \
  https://manufacturer.example/part.pdf --mpn EXACT-MPN

python3 tools/publish_component_assets.py \
  /path/from/PREVIEW_MANIFEST/preview-manifest.json
```

Publishing is a separate approved operation. It atomically stores the
component/2 record and complete bound-component record with a digest-bound STEP
asset. Pixels may identify topology and callout associations, but are never a
source of millimetre dimensions.
