# DesignStudio workbench for FreeCAD

DesignStudio is a focused product-design workbench hosted by FreeCAD. FreeCAD
remains the mechanical, enclosure, assembly and STEP authority; schematic, PCB,
verification and the Design Assistant are embedded in the same process.

The production workbench has one top-level window. Its product surface is a
FreeCAD MDI document and its Product Tree, Assistant, Properties, Verification
Details and Design Alternatives are native host docks. There is no companion
electronics window, nested menu bar or duplicate status bar.

The normal workflow starts at **Product Home**:

1. Create or open one `.dsworkspace` containing the mechanical and electronics
   documents, baseline configuration, contracts, evidence and verification
   directories.
2. Work in Mechanical, Schematic, PCB and Verification modes. The single
   toolbar changes automatically with the selected mode.
3. Use **Reference Form…** to import a digest-addressed inspirational image,
   record a real dimension, correct/approve silhouettes, label editable curves,
   add engineering controls and project editable motifs. AI operations create
   preview revisions; only explicit acceptance advances the active revision.
4. Use **Mechanical Contract…** only while defining the legal PCB face,
   interfaces and clearance zones. Locking the contract triggers component STEP
   synchronization automatically.
5. Use Components and Bindings for specialist component records. A permanent
   footprint editor is not part of the normal product workflow.
6. Review explicit routing, binding, evidence and check status in Verification.
   Failed or incomplete required gates block release.

Reference-form generation uses Hunyuan3D-2.1 while fewer than six approved
silhouettes exist and switches to the six-view visual-hull/Omni route only when
all cardinal silhouettes are approved. Generated meshes remain hidden evidence.
The selected form is rebuilt as editable FreeCAD section curves and an exact
master B-Rep before user-approved seams divide parts.

Developer diagnostics are excluded by default. Configure with
`-DDESIGNSTUDIO_DEVELOPER_TOOLS=ON` to build the separate
`designstudio-diagnostics` executable containing legacy provider and internal
inspection controls.
