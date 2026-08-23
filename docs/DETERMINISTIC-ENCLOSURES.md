# Deterministic enclosure templates

The DesignStudio FreeCAD workbench exposes three recomputable enclosure
templates: rectangular electronics enclosure, injection-moulded clamshell and
handheld instrument. `DesignStudio_GenerateEnclosure` creates editable FreeCAD
properties and four derived shapes: lower shell, upper shell, gasket/seal
channel and the legal PCB volume.

The construction owns exact wall thickness, split datum, bosses with pilot
holes, cross ribs, connector/display openings, vents, gasket geometry, PCB
clearance and a manufacturing draft intent property. The injection template
uses rounded outer surfaces while retaining exact interface datums; the
handheld template uses a solid loft between a narrower grip and full-size
shoulder. Editing a controller property and recomputing regenerates every
derived solid.

`compare_catalog()` evaluates catalog inner dimensions before custom geometry
and sorts valid candidates by unused volume, price and part number. Generated
geometry remains a local deterministic CAD authority; the helper has no mesh or
cloud dependency.

The real-FreeCAD runtime smoke test constructs all three templates, verifies
valid non-empty B-Reps, changes PCB clearance, recomputes, and proves that the
legal volume updates. That same legal-volume object can be selected as the
mechanical contract's PCB region before locking and synchronizing the contract.
