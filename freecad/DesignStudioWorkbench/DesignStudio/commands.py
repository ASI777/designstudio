"""Deterministic FreeCAD command registration for DesignStudio.

Only product-workflow entry points are placed on the production toolbar.
Specialist mechanical-contract commands remain registered so the contextual
task panel can invoke them, but they are never exposed globally.
"""
from __future__ import annotations

from pathlib import Path

PRIMARY_COMMANDS = [
    "DesignStudio_ReferenceForm",
    "DesignStudio_CaptureRedesign",
    "DesignStudio_MechanicalContract",
    "DesignStudio_GenerateEnclosure",
    "DesignStudio_MechanicalProgram",
    "DesignStudio_OpenMechanical3D",
    "DesignStudio_PipelineStatus",
]

MECHANICAL_CONTRACT_COMMANDS = [
    "DesignStudio_SetBoardRegion",
    "DesignStudio_MarkFixed", "DesignStudio_MarkConnector",
    "DesignStudio_MarkMountingHole", "DesignStudio_MarkHeightZone",
    "DesignStudio_MarkCoolingZone", "DesignStudio_MarkServiceClearance",
    "DesignStudio_SyncContract",
]

COMMANDS = PRIMARY_COMMANDS + MECHANICAL_CONTRACT_COMMANDS
_REGISTERED = False


def _modules():
    import FreeCAD as App
    import FreeCADGui as Gui
    from PySide import QtWidgets
    return App, Gui, QtWidgets


def _active():
    App, Gui, QtWidgets = _modules()
    if App.ActiveDocument is None:
        raise RuntimeError("Open or create a FreeCAD document first")
    from .freecad_adapter import ensure_controller
    return App, Gui, QtWidgets, ensure_controller(App.ActiveDocument)


class _Base:
    text = "DesignStudio"
    tip = "DesignStudio"

    def GetResources(self):
        return {"MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        import FreeCAD as App
        return App.ActiveDocument is not None


class SetBoardRegion(_Base):
    text = "Set Legal PCB Face"
    tip = "Use the selected planar face as the legal PCB outline and cutouts"

    def Activated(self):
        App, Gui, _, controller = _active()
        selection = Gui.Selection.getSelectionEx()
        if len(selection) != 1 or len(selection[0].SubElementNames) != 1 \
                or not selection[0].SubElementNames[0].startswith("Face"):
            raise RuntimeError("Select exactly one planar face")
        from .freecad_adapter import set_board_region
        set_board_region(controller, selection[0].Object, selection[0].SubElementNames[0])
        controller.SyncStatus = "Board face selected; mark fixed items and zones"
        App.ActiveDocument.recompute()


class MarkRole(_Base):
    role = "none"

    def Activated(self):
        App, Gui, _, _ = _active()
        selected = Gui.Selection.getSelection()
        if not selected:
            raise RuntimeError("Select one or more FreeCAD objects")
        from .freecad_adapter import mark_object
        for obj in selected:
            mark_object(obj, self.role)
        App.ActiveDocument.recompute()


def _role_command(name, text, tip, role):
    return type(name, (MarkRole,), {"text": text, "tip": tip, "role": role})


MarkFixed = _role_command("MarkFixed", "Mark Fixed Item",
                          "Mark a fixed mechanical/electrical envelope", "fixed_item")
MarkConnector = _role_command("MarkConnector", "Mark Connector",
                              "Mark a connector whose PCB placement is mechanically fixed", "connector")
MarkMountingHole = _role_command("MarkMountingHole", "Mark Mounting Hole",
                                 "Mark a mounting-hole object", "mounting_hole")
MarkHeightZone = _role_command("MarkHeightZone", "Mark Height Zone",
                               "Mark a region with a maximum component height", "height_zone")
MarkCoolingZone = _role_command("MarkCoolingZone", "Mark Cooling Zone",
                                "Mark an airflow/cooling keep-clear region", "cooling_zone")
MarkServiceClearance = _role_command("MarkServiceClearance", "Mark Service Clearance",
                                    "Mark a placement/routing service keepout", "service_clearance")


class SyncContract(_Base):
    text = "Lock Mechanical Contract"
    tip = "Derive a locked contract and synchronize it into the electronics project"

    def Activated(self):
        App, _, _, controller = _active()
        if not controller.ProjectPath:
            mechanical_path = Path(App.ActiveDocument.FileName) if App.ActiveDocument.FileName else None
            if mechanical_path and mechanical_path.parent.name == "mechanical":
                candidate = mechanical_path.parent.parent / "electronics" / "product.dsproj"
                if candidate.is_file():
                    controller.ProjectPath = str(candidate)
            if not controller.ProjectPath:
                raise RuntimeError(
                    "This document is not inside a DesignStudio workspace; import it from Product Home first"
                )
        from .freecad_adapter import snapshot_from_document
        from .mechanical_contract import derive_contract, write_contract_and_project
        if App.ActiveDocument.FileName:
            App.ActiveDocument.save()
        snapshot = snapshot_from_document(App.ActiveDocument, controller)
        old_revision = 0
        contract_path = Path(controller.ProjectPath).with_suffix(".mechanical-contract.json")
        if contract_path.exists():
            import json
            old_revision = json.loads(contract_path.read_text(encoding="utf-8")).get("revision", 0)
        contract = derive_contract(snapshot, revision=old_revision + 1)
        _, written_contract = write_contract_and_project(controller.ProjectPath, contract)
        controller.ContractDigest = contract["contract_digest"]
        try:
            from .freecad_adapter import sync_bound_components
            synchronized = sync_bound_components(App.ActiveDocument, controller)
            controller.SyncStatus = (
                f"Locked revision {contract['revision']}: {written_contract.name}; "
                f"synchronized {synchronized['imported']} bound components"
            )
        except Exception as exc:
            controller.SyncStatus = (
                f"Locked revision {contract['revision']}; automatic component "
                f"synchronization failed — retry from the notification details: {exc}"
            )
        App.ActiveDocument.recompute()


class MechanicalContractTask:
    """Contextual task panel; contract tools never occupy the global toolbar."""

    def __init__(self):
        _, Gui, QtWidgets = _modules()
        self.form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.form)
        heading = QtWidgets.QLabel("Mechanical Contract")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        help_text = QtWidgets.QLabel(
            "Define the legal PCB face, fixed interfaces and clearance zones. "
            "Locking is the final action and triggers component synchronization."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        for command_name in MECHANICAL_CONTRACT_COMMANDS:
            button = QtWidgets.QPushButton(_COMMAND_TYPES[command_name].text)
            command_type = _COMMAND_TYPES[command_name]
            button.clicked.connect(command_type().Activated)
            layout.addWidget(button)
        layout.addStretch(1)

    def accept(self):
        import FreeCADGui as Gui
        Gui.Control.closeDialog()
        return True

    def reject(self):
        import FreeCADGui as Gui
        Gui.Control.closeDialog()
        return True


class BeginMechanicalContract(_Base):
    text = "Mechanical Contract…"
    tip = "Open the contextual task for legal PCB volume and interface constraints"

    def Activated(self):
        _, Gui, _, _ = _active()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.Control.showDialog(MechanicalContractTask())


class GenerateEnclosure(_Base):
    text = "Generate Parametric Enclosure"
    tip = "Create a deterministic rectangular, clamshell or handheld enclosure"

    def Activated(self):
        App, _, QtWidgets, _ = _active()
        choices = ["rectangular", "injection_clamshell", "handheld"]
        template, accepted = QtWidgets.QInputDialog.getItem(
            None, "Enclosure template", "Template", choices, 0, False)
        if not accepted:
            return
        from .enclosure_generator import create_enclosure
        create_enclosure(App.ActiveDocument, template)
        App.ActiveDocument.recompute()


class MechanicalProgram(_Base):
    text = "Mechanical Feature Program…"
    tip = "Apply a validated SolidWorks-inspired mechanical CAD command program"

    def Activated(self):
        App, _, QtWidgets, _ = _active()
        path, accepted = QtWidgets.QFileDialog.getOpenFileName(
            None, "Open mechanical CAD program", "", "JSON files (*.json)")
        if not accepted or not path:
            return
        import json
        from .mechanical_cad import execute_program
        with open(path, "r", encoding="utf-8") as handle:
            program = json.load(handle)
        receipt = execute_program(App.ActiveDocument, program)
        App.ActiveDocument.recompute()
        self.last_receipt = receipt


class CaptureLocalRedesign(_Base):
    text = "AI Redesign Selected Region…"
    tip = ("Capture one or more selected faces as a digest-bound topology edit; "
           "Codex previews a sibling branch and the baseline is never edited")

    def Activated(self):
        App, Gui, QtWidgets, _ = _active()
        document = App.ActiveDocument
        if not document.FileName:
            raise RuntimeError("Save the active document inside a DesignStudio workspace first")
        mechanical_path = Path(document.FileName).resolve()
        if mechanical_path.parent.name != "mechanical":
            raise RuntimeError("The document must be inside the workspace mechanical directory")
        workspace = mechanical_path.parent.parent
        selection = Gui.Selection.getSelectionEx()
        if len(selection) != 1 or not selection[0].SubElementNames or not all(
                str(name).startswith("Face") for name in selection[0].SubElementNames):
            raise RuntimeError("Select one or more faces on exactly one body")
        intent, accepted = QtWidgets.QInputDialog.getMultiLineText(
            None, "AI redesign selected region",
            "What should improve? Include functional and manufacturing intent:", "")
        if not accepted or not intent.strip():
            return
        expansion, accepted = QtWidgets.QInputDialog.getDouble(
            None, "Maximum expansion", "Allowed expansion outside current object (mm):",
            0.0, 0.0, 10000.0, 3)
        if not accepted:
            return
        from .local_redesign_v2 import capture_local_redesign_v2
        result = capture_local_redesign_v2(
            document, workspace, intent, expansion,
            continuity_required="G1", selection_ex=selection)
        QtWidgets.QMessageBox.information(
            None, "Selection captured",
            "A topology-aware local-redesign/2 contract was created. Open Physical "
            "Design → Local Redesign and ask Codex to create a preview. Apply, Modify, "
            "and Reject remain explicit user decisions.\n\n"
            + result["path"])


class ReferenceFormTask:
    """Native four-stage capture UI backed by immutable workspace revisions."""

    def __init__(self):
        App, _, QtWidgets = _modules()
        import hashlib
        from .reference_form import ReferenceFormStore, workspace_from_mechanical_path

        document = App.ActiveDocument
        if document is None or not document.FileName:
            raise RuntimeError("Save the active FreeCAD document in a workspace first")
        workspace = workspace_from_mechanical_path(document.FileName)
        document_bytes = Path(document.FileName).read_bytes()
        self.store = ReferenceFormStore(workspace, {
            "document_id": document.Name,
            "revision": 0,
            "sha256": hashlib.sha256(document_bytes).hexdigest(),
        })
        self.QtWidgets = QtWidgets
        self.form = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.form)
        self.tabs = QtWidgets.QTabWidget(self.form)
        root.addWidget(self.tabs, 1)
        generation = QtWidgets.QGroupBox("Progressive shape generation")
        generation_layout = QtWidgets.QVBoxLayout(generation)
        generation_buttons = QtWidgets.QHBoxLayout()
        self.generate_button = QtWidgets.QPushButton("Generate 4 candidates")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        generation_buttons.addWidget(self.generate_button)
        generation_buttons.addWidget(self.cancel_button)
        generation_layout.addLayout(generation_buttons)
        self.candidates = QtWidgets.QComboBox()
        self.candidates.setEnabled(False)
        self.accept_candidate_button = QtWidgets.QPushButton(
            "Accept selected candidate as reference evidence"
        )
        self.accept_candidate_button.setEnabled(False)
        generation_layout.addWidget(self.candidates)
        generation_layout.addWidget(self.accept_candidate_button)
        root.addWidget(generation)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        history = QtWidgets.QHBoxLayout()
        undo = QtWidgets.QPushButton("Undo accepted revision")
        redo = QtWidgets.QPushButton("Redo accepted revision")
        history.addWidget(undo)
        history.addWidget(redo)
        root.addLayout(history)
        undo.clicked.connect(self._undo)
        redo.clicked.connect(self._redo)
        self.generate_button.clicked.connect(self._start_generation)
        self.cancel_button.clicked.connect(self._cancel_generation)
        self.accept_candidate_button.clicked.connect(self._accept_candidate)
        from PySide import QtCore
        self.job_timer = QtCore.QTimer(self.form)
        self.job_timer.setInterval(1000)
        self.job_timer.timeout.connect(self._poll_generation)
        self.job_id = None
        self.generation_result = None

        self._build_reference()
        self._build_mask_curves()
        self._build_engineering()
        self._build_motifs()
        self._refresh()

    def _build_reference(self):
        Q = self.QtWidgets
        page = Q.QWidget()
        layout = Q.QFormLayout(page)
        self.source = Q.QLineEdit()
        self.source.setPlaceholderText("Original URL, filename, photographer, or design source")
        self.license = Q.QComboBox()
        self.license.addItems(["review_required", "unknown", "approved_for_project"])
        self.viewpoint = Q.QComboBox()
        self.viewpoint.addItems(["front", "rear", "left", "right", "top", "bottom"])
        self.projection = Q.QComboBox()
        self.projection.addItems(["perspective", "orthographic", "unknown"])
        self.dimension_axis = Q.QComboBox()
        self.dimension_axis.addItems(["x", "y", "z", "feature"])
        self.dimension = Q.QDoubleSpinBox()
        self.dimension.setRange(0.001, 10000.0)
        self.dimension.setDecimals(3)
        self.dimension.setValue(120.0)
        self.bounds = []
        bounds_host = Q.QWidget()
        bounds_layout = Q.QHBoxLayout(bounds_host)
        bounds_layout.setContentsMargins(0, 0, 0, 0)
        for value, suffix in ((120.0, " X"), (80.0, " Y"), (35.0, " Z")):
            control = Q.QDoubleSpinBox()
            control.setRange(0.001, 10000.0)
            control.setDecimals(3)
            control.setValue(value)
            control.setSuffix(suffix + " mm")
            bounds_layout.addWidget(control)
            self.bounds.append(control)
        import_button = Q.QPushButton("Import reference and accept measured revision…")
        import_button.clicked.connect(self._import_reference)
        note = Q.QLabel(
            "At least one real measurement is mandatory. Pixel dimensions are "
            "never used as manufacturing scale."
        )
        note.setWordWrap(True)
        layout.addRow("Source / provenance", self.source)
        layout.addRow("License status", self.license)
        layout.addRow("Viewpoint", self.viewpoint)
        layout.addRow("Projection", self.projection)
        layout.addRow("Measured axis", self.dimension_axis)
        layout.addRow("Measured value (mm)", self.dimension)
        layout.addRow("Confirmed final X/Y/Z", bounds_host)
        layout.addRow(note)
        layout.addRow(import_button)
        self.tabs.addTab(page, "1 Reference")

    def _build_mask_curves(self):
        Q = self.QtWidgets
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        help_text = Q.QLabel(
            "Use SAM 2.1 click/box suggestions externally, then import the "
            "brush-corrected mask and silhouette. The result remains a preview "
            "until you explicitly accept it."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.curve_label = Q.QComboBox()
        self.curve_label.addItems([
            "preserve curve", "hard edge", "soft transition", "opening",
            "motif", "candidate seam", "ignore",
        ])
        layout.addWidget(self.curve_label)
        mask_button = Q.QPushButton("Import corrected mask + silhouette…")
        mask_button.clicked.connect(self._import_mask)
        layout.addWidget(mask_button)
        depth = Q.QCheckBox(
            "Show uncertain relative-depth preview (Depth Anything V2 Small)"
        )
        depth.setToolTip(
            "Preview guidance only; never used as authoritative geometry or scale."
        )
        layout.addWidget(depth)
        layout.addStretch(1)
        self.tabs.addTab(page, "2 Mask and Curves")

    def _build_engineering(self):
        Q = self.QtWidgets
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        heading = Q.QLabel(
            "Section curves · symmetry planes · keep/avoid points · voxel "
            "keep-outs · keyboard plane · key-travel envelope · hand-rest · "
            "PCB/battery/antenna volumes · connector access"
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)
        function_first = Q.QLabel(
            "Function wins over reference styling. Conflicts must remain visible "
            "in the deviation heatmap before candidate acceptance."
        )
        function_first.setWordWrap(True)
        layout.addWidget(function_first)
        preset = Q.QGroupBox("FDM prototype preset")
        preset_layout = Q.QFormLayout(preset)
        preset_layout.addRow("Nominal wall", Q.QLabel("2.0 mm"))
        preset_layout.addRow("Minimum rib", Q.QLabel("1.2 mm"))
        preset_layout.addRow("Assembly clearance", Q.QLabel("0.30 mm"))
        preset_layout.addRow("Nozzle assumption", Q.QLabel("0.4 mm"))
        preset_layout.addRow("Surface deviation target", Q.QLabel("1.0 mm (editable at reconstruction)"))
        layout.addWidget(preset)
        layout.addStretch(1)
        self.tabs.addTab(page, "3 Engineering Controls")

    def _build_motifs(self):
        Q = self.QtWidgets
        page = Q.QWidget()
        layout = Q.QFormLayout(page)
        self.motif_form = Q.QComboBox()
        self.motif_form.addItems(["roofline", "fender", "intake", "spoiler", "window edge", "custom"])
        self.motif_application = Q.QComboBox()
        self.motif_application.addItems([
            "blended surface curve", "raised feature", "debossed feature",
            "separate insert", "opening", "seam",
        ])
        self.motif_intensity = Q.QDoubleSpinBox()
        self.motif_intensity.setRange(0.0, 1.0)
        self.motif_intensity.setSingleStep(0.05)
        self.motif_intensity.setValue(0.5)
        self.motif_scale = Q.QDoubleSpinBox()
        self.motif_scale.setRange(0.01, 100.0)
        self.motif_scale.setValue(1.0)
        self.motif_orientation = Q.QDoubleSpinBox()
        self.motif_orientation.setRange(-360.0, 360.0)
        self.motif_symmetric = Q.QCheckBox("Mirror across approved symmetry plane")
        self.motif_falloff = Q.QDoubleSpinBox()
        self.motif_falloff.setRange(0.0, 1000.0)
        self.motif_falloff.setValue(8.0)
        layout.addRow("Extracted form", self.motif_form)
        layout.addRow("Apply as", self.motif_application)
        layout.addRow("Intensity", self.motif_intensity)
        layout.addRow("Scale", self.motif_scale)
        layout.addRow("Orientation (deg)", self.motif_orientation)
        layout.addRow("Symmetry", self.motif_symmetric)
        layout.addRow("Blend falloff (mm)", self.motif_falloff)
        apply_button = Q.QPushButton("Preview motif on editable surface")
        apply_button.clicked.connect(self._preview_motif)
        layout.addRow(apply_button)
        note = Q.QLabel(
            "Motifs and AI-suggested seams are editable proposals. Part division "
            "occurs only after the exact master B-Rep and explicit seam approval."
        )
        note.setWordWrap(True)
        layout.addRow(note)
        self.tabs.addTab(page, "4 Motif Extraction")

    def _refresh(self):
        active = self.store.active()
        if active is None:
            self.status.setText(
                "No active reference-form revision. Import one image and a measured dimension."
            )
            return
        silhouettes = sum(
            1 for view in active["views"]
            if view.get("approved") and view.get("silhouette_asset_id")
        )
        backend = "Hunyuan3D-Omni" if silhouettes == 6 else "Hunyuan3D-2.1"
        self.status.setText(
            f"Active revision {active['revision']} · {len(active['views'])} views · "
            f"{silhouettes}/6 approved silhouettes · next generation: {backend}"
        )

    def _import_reference(self):
        Q = self.QtWidgets
        path, _ = Q.QFileDialog.getOpenFileName(
            self.form, "Import inspirational reference", "",
            "Reference images (*.png *.jpg *.jpeg *.webp)"
        )
        if not path:
            return
        try:
            self.store.import_reference(
                path,
                viewpoint=self.viewpoint.currentText(),
                source=self.source.text().strip() or path,
                license_status=self.license.currentText(),
                projection=self.projection.currentText(),
                known_axis=self.dimension_axis.currentText(),
                known_value_mm=self.dimension.value(),
                target_bounds_mm=tuple(control.value() for control in self.bounds),
                symmetry_planes=[],
            )
            self._refresh()
        except Exception as exc:
            Q.QMessageBox.critical(self.form, "Reference import failed", str(exc))

    def _import_mask(self):
        Q = self.QtWidgets
        active = self.store.active()
        if active is None:
            Q.QMessageBox.warning(self.form, "Mask correction", "Import a reference first.")
            return
        view_ids = [view["view_id"] for view in active["views"]]
        view_id, accepted = Q.QInputDialog.getItem(
            self.form, "Corrected mask", "Reference view", view_ids, 0, False
        )
        if not accepted:
            return
        mask, _ = Q.QFileDialog.getOpenFileName(
            self.form, "Import brush-corrected mask", "", "Mask images (*.png *.jpg *.webp)"
        )
        if not mask:
            return
        silhouette, _ = Q.QFileDialog.getOpenFileName(
            self.form, "Import approved silhouette", "", "Silhouette images (*.png *.jpg *.webp)"
        )
        if not silhouette:
            return
        try:
            from .reference_form import extract_silhouette_curve
            curves = extract_silhouette_curve(silhouette, view_id)
            feature_label = self.curve_label.currentText()
            if feature_label not in ("ignore", "silhouette"):
                feature = dict(curves[0])
                feature["curve_id"] = (
                    f"curve.feature.{feature_label.replace(' ', '-')}"
                    f".{feature['curve_id'].rsplit('.', 1)[-1]}"
                )
                feature["label"] = feature_label
                curves.append(feature)
            preview = self.store.attach_corrected_mask(
                view_id, mask, silhouette, curves, base_revision=active["revision"]
            )
            answer = Q.QMessageBox.question(
                self.form, "Accept mask revision",
                "Accept the SAM-assisted, brush-corrected mask and silhouette as "
                "a new active revision?",
                Q.QMessageBox.Yes | Q.QMessageBox.No, Q.QMessageBox.No,
            )
            if answer == Q.QMessageBox.Yes:
                self.store.accept_preview(preview["preview_id"])
            else:
                self.store.discard_preview(preview["preview_id"])
            self._refresh()
        except Exception as exc:
            Q.QMessageBox.critical(self.form, "Mask correction failed", str(exc))

    def _preview_motif(self):
        Q = self.QtWidgets
        active = self.store.active()
        if active is None:
            Q.QMessageBox.warning(self.form, "Motif", "Import a reference first.")
            return
        curves = [
            curve for curve in active["curves"]
            if curve.get("approved") and curve.get("label") != "ignore"
        ]
        if not curves:
            Q.QMessageBox.warning(
                self.form, "Motif",
                "Approve a corrected silhouette or feature curve before extracting a motif."
            )
            return
        try:
            replacement = __import__("copy").deepcopy(active)
            motif_id = f"motif.{len(replacement['motifs']) + 1}"
            application = self.motif_application.currentText()
            replacement["motifs"].append({
                "motif_id": motif_id,
                "source_curve_ids": [curves[0]["curve_id"]],
                "form": self.motif_form.currentText(),
                "application": application,
                "intensity": self.motif_intensity.value(),
                "scale": self.motif_scale.value(),
                "orientation_deg": self.motif_orientation.value(),
                "symmetric": self.motif_symmetric.isChecked(),
                "blend_falloff_mm": self.motif_falloff.value(),
                "approved": True,
            })
            if application == "seam":
                replacement["seams"].append({
                    "seam_id": f"seam.{len(replacement['seams']) + 1}",
                    "curve_id": curves[0]["curve_id"],
                    "source": "user",
                    "approved": True,
                })
            preview = self.store.create_preview(
                base_revision=active["revision"],
                operation="motif_projection",
                replacement=replacement,
            )
            answer = Q.QMessageBox.question(
                self.form, "Accept motif revision",
                "Accept this editable motif/seam proposal as a new active revision?",
                Q.QMessageBox.Yes | Q.QMessageBox.No, Q.QMessageBox.No,
            )
            if answer == Q.QMessageBox.Yes:
                self.store.accept_preview(preview["preview_id"])
            else:
                self.store.discard_preview(preview["preview_id"])
            self._refresh()
        except Exception as exc:
            Q.QMessageBox.critical(self.form, "Motif preview failed", str(exc))

    def _undo(self):
        try:
            self.store.undo()
            self._refresh()
        except Exception as exc:
            self.QtWidgets.QMessageBox.information(self.form, "Undo", str(exc))

    def _redo(self):
        try:
            self.store.redo()
            self._refresh()
        except Exception as exc:
            self.QtWidgets.QMessageBox.information(self.form, "Redo", str(exc))

    def _start_generation(self):
        Q = self.QtWidgets
        active = self.store.active()
        if active is None:
            Q.QMessageBox.warning(
                self.form, "Reference generation",
                "Import and approve a reference with a measured dimension first."
            )
            return
        try:
            from .reference_form import ReferenceFormGatewayClient
            self.gateway = ReferenceFormGatewayClient()
            job = self.gateway.submit(active)
            self.job_id = job["job_id"]
            self.generate_button.setEnabled(False)
            self.cancel_button.setEnabled(job["status"] not in (
                "completed", "failed", "cancelled"
            ))
            self.status.setText(
                f"Generation {job['status']} · {job['backend']} · "
                f"four sequential seeds"
            )
            if job["status"] == "completed":
                self._poll_generation()
            else:
                self.job_timer.start()
        except Exception as exc:
            Q.QMessageBox.critical(self.form, "Generation submission failed", str(exc))

    def _poll_generation(self):
        if not self.job_id:
            return
        try:
            job = self.gateway.status(self.job_id)
            status = job["status"]
            self.status.setText(
                f"Generation {status} · {job['backend']} · job {self.job_id}"
            )
            if status == "completed":
                self.job_timer.stop()
                self.cancel_button.setEnabled(False)
                self.generate_button.setEnabled(True)
                self.generation_result = self.gateway.result(self.job_id)
                results = self.store.root / "results"
                results.mkdir(parents=True, exist_ok=True)
                (results / f"{self.job_id}.json").write_text(
                    __import__("json").dumps(
                        self.generation_result, indent=2, sort_keys=True
                    ) + "\n",
                    encoding="utf-8",
                )
                self.candidates.clear()
                by_index = {
                    item["index"]: item
                    for item in self.generation_result["candidates"]
                }
                for index in self.generation_result["ranking"]:
                    candidate = by_index[index]
                    score = candidate["scores"]["rank_score"]
                    self.candidates.addItem(
                        f"Candidate {index + 1} · seed {candidate['seed']} · "
                        f"rank {score:.3f}",
                        index,
                    )
                self.candidates.setEnabled(True)
                self.accept_candidate_button.setEnabled(True)
            elif status in ("failed", "cancelled"):
                self.job_timer.stop()
                self.cancel_button.setEnabled(False)
                self.generate_button.setEnabled(True)
                if status == "failed":
                    self.QtWidgets.QMessageBox.critical(
                        self.form, "Generation failed",
                        job.get("failure") or "Generation worker failed."
                    )
        except Exception as exc:
            self.job_timer.stop()
            self.generate_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.QtWidgets.QMessageBox.critical(
                self.form, "Generation status failed", str(exc)
            )

    def _cancel_generation(self):
        if not self.job_id:
            return
        try:
            job = self.gateway.cancel(self.job_id)
            self.job_timer.stop()
            self.cancel_button.setEnabled(False)
            self.generate_button.setEnabled(True)
            self.status.setText(f"Generation {job['status']} · job {self.job_id}")
        except Exception as exc:
            self.QtWidgets.QMessageBox.critical(
                self.form, "Cancellation failed", str(exc)
            )

    def _accept_candidate(self):
        Q = self.QtWidgets
        if self.generation_result is None or self.candidates.currentIndex() < 0:
            return
        index = int(self.candidates.currentData())
        candidate = next(
            item for item in self.generation_result["candidates"]
            if item["index"] == index
        )
        try:
            data = self.gateway.download(candidate["artifact_url"])
            import hashlib
            if hashlib.sha256(data).hexdigest() != candidate["sha256"]:
                raise RuntimeError("downloaded candidate SHA-256 does not match result")
            suffix = ".glb" if candidate["media_type"] == "model/gltf-binary" else ".stl"
            downloads = self.store.root / "downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            path = downloads / f"{candidate['sha256']}{suffix}"
            if not path.exists():
                path.write_bytes(data)
            preview = self.store.candidate_acceptance_preview(
                self.generation_result, index, path
            )
            answer = Q.QMessageBox.question(
                self.form, "Accept candidate",
                "Accept this mesh as hidden reference evidence? This advances "
                "the active reference-form revision; manufacturing CAD remains "
                "an exact FreeCAD reconstruction.",
                Q.QMessageBox.Yes | Q.QMessageBox.No, Q.QMessageBox.No,
            )
            if answer != Q.QMessageBox.Yes:
                self.store.discard_preview(preview["preview_id"])
                return
            accepted = self.store.accept_preview(preview["preview_id"])
            silhouette_scores = list(
                candidate["scores"].get("silhouette_iou", {}).values()
            )
            if silhouette_scores:
                import FreeCAD as App
                from .reconstruction import reconstruct_candidate
                generated = reconstruct_candidate(
                    App.ActiveDocument,
                    path,
                    bounding_box_mm=candidate["dimensions_mm"],
                    silhouette_scores=silhouette_scores,
                    minimum_silhouette_score=0.90,
                    surface_deviation_target_mm=1.0,
                )
                App.ActiveDocument.recompute()
                App.ActiveDocument.save()
                self.status.setText(
                    f"Candidate accepted at revision {accepted['revision']}; "
                    f"editable exact master B-Rep created as "
                    f"{generated['controller'].MasterBRep.Label}"
                )
            else:
                self.status.setText(
                    f"Candidate accepted at revision {accepted['revision']} as "
                    "reference evidence; approve at least one silhouette before CAD reconstruction."
                )
            self._refresh()
        except Exception as exc:
            Q.QMessageBox.critical(self.form, "Candidate acceptance failed", str(exc))

    def accept(self):
        import FreeCADGui as Gui
        Gui.Control.closeDialog()
        return True

    def reject(self):
        import FreeCADGui as Gui
        Gui.Control.closeDialog()
        return True


class BeginReferenceForm(_Base):
    text = "Reference Form…"
    tip = "Capture measured references, silhouettes, controls and editable motifs"

    def Activated(self):
        _, Gui, _, _ = _active()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        Gui.Control.showDialog(ReferenceFormTask())


# Documents already given the full presentation pass in this session. The
# Mechanical 3D tab fires on every entry; re-opening and re-styling an already
# prepared document must stay near-instant instead of freezing the UI.  The
# cache is keyed by canonical source path, not the FCStd stem: every workspace
# uses "product.FCStd", and FreeCAD can keep more than one of those documents
# open at once.
_PREPARED_MECHANICAL_DOCS: set[str] = set()
_MECHANICAL_DOCUMENT_NAMES: dict[str, str] = {}


def _set_object_visibility(obj, visible):
    """Set document and GUI visibility flags when each side is available."""
    value = bool(visible)
    try:
        if hasattr(obj, "Visibility"):
            obj.Visibility = value
    except Exception:
        pass
    view_obj = getattr(obj, "ViewObject", None)
    try:
        if view_obj is not None and hasattr(view_obj, "Visibility"):
            view_obj.Visibility = value
    except Exception:
        pass


def reset_mechanical_view(document_name=None):
    """Restore the deterministic default camera for a mechanical document."""
    App, Gui, _ = _modules()
    name = str(document_name or getattr(App.ActiveDocument, "Name", "") or "")
    if not name:
        return False
    gui_doc = Gui.getDocument(name) if hasattr(Gui, "getDocument") else None
    if gui_doc is None and hasattr(Gui, "activeDocument"):
        gui_doc = Gui.activeDocument()
    if gui_doc is None:
        return False
    active_view = getattr(gui_doc, "ActiveView", None)
    if active_view is None and hasattr(gui_doc, "activeView"):
        active_view = gui_doc.activeView()
    if active_view is None:
        return False
    if hasattr(active_view, "viewAxonometric"):
        active_view.viewAxonometric()
    if hasattr(active_view, "fitAll"):
        active_view.fitAll()
    return True


def ensure_mechanical_document(target):
    """Open the workspace mechanical FCStd, apply viewport presentation.

    Shared by the Open Mechanical 3D command and the C++ host module that
    swaps FreeCAD's shared MDI surface when the Mechanical 3D tab activates.
    Returns the internal document name.
    """
    App, Gui, QtWidgets = _modules()
    target_path = Path(target).expanduser().resolve()
    if not target_path.is_file():
        raise FileNotFoundError(
            f"Mechanical FCStd does not exist: {target_path}")
    canonical_path = str(target_path)

    def open_document_for_path():
        for open_name in App.listDocuments():
            try:
                candidate = App.getDocument(open_name)
                filename = str(getattr(candidate, "FileName", "") or "").strip()
                if filename and str(Path(filename).expanduser().resolve()) == canonical_path:
                    return candidate
            except Exception:
                # A document can disappear while FreeCAD is processing a close
                # event; let the normal open path recover it on the next pass.
                continue
        return None

    doc = open_document_for_path()
    if doc is None:
        doc = App.openDocument(canonical_path)
    if doc is None:
        raise RuntimeError(
            f"FreeCAD could not open mechanical FCStd: {canonical_path}")

    name = str(getattr(doc, "Name", "") or "").strip()
    if not name or name not in App.listDocuments():
        raise RuntimeError(
            f"FreeCAD opened the mechanical file without a usable document name: "
            f"{canonical_path}")
    App.setActiveDocument(name)

    # Hide construction helpers so the viewport shows only product bodies.
    already_prepared = canonical_path in _PREPARED_MECHANICAL_DOCS
    for obj in doc.Objects:
        label = getattr(obj, "Label", "")
        type_id = str(getattr(obj, "TypeId", ""))
        if "orthographic" in obj.Name or ".profile" in label:
            _set_object_visibility(obj, False)
        elif type_id.startswith("PartDesign"):
            _set_object_visibility(obj, True)
        elif type_id in ("App::Part", "App::DocumentObjectGroup") \
                and not list(getattr(obj, "InList", ()) or ()):
            # A hidden top-level container suppresses every visible child.
            _set_object_visibility(obj, True)

    if already_prepared:
        from .mechanical_view_modes import apply_view_mode
        apply_view_mode(doc, "manufacturing_internals", fit=False)
        reset_mechanical_view(name)
        _MECHANICAL_DOCUMENT_NAMES[canonical_path] = name
        return name

    # SolidWorks-style appearance pass: colour by semantic family so fine
    # features (ribs, controls) read against the housing and interior
    # reservations stay clearly non-authoritative behind transparency.
    families = {
        "midframe": ((0.60, 0.63, 0.66), 0),
        "front-cover": ((0.15, 0.16, 0.18), 0),
        "rear-cover": ((0.15, 0.16, 0.18), 0),
        "body-split": ((0.35, 0.38, 0.42), 0),
        "grip-transition": ((0.22, 0.24, 0.27), 0),
        "rib": ((0.87, 0.53, 0.12), 0),
        "control-dial": ((0.78, 0.78, 0.80), 0),
        "shutter": ((0.85, 0.30, 0.25), 0),
        "lens-mount": ((0.10, 0.10, 0.11), 0),
        "lens-barrel": ((0.08, 0.08, 0.09), 0),
        "lens-glass": ((0.35, 0.55, 0.70), 20),
        "rear-display-window": ((0.05, 0.05, 0.06), 35),
        "reservation-optical-module": ((0.90, 0.75, 0.20), 65),
        "reservation-rear-display": ((0.60, 0.35, 0.80), 65),
        "reservation-battery": ((0.20, 0.65, 0.30), 60),
        "reservation-logic-pcb": ((0.10, 0.45, 0.80), 55),
        "reservation-usb-c": ((0.90, 0.90, 0.95), 40),
        "reservation-speaker": ((0.75, 0.45, 0.20), 50),
    }

    def _family(label):
        parts = [p for p in label.split("·")[0].strip().split(".") if p]
        if len(parts) >= 3 and parts[1] == "reservation":
            return "reservation-" + parts[2]
        for part in parts[2:]:
            for fam_name in families:
                if part == fam_name or part.startswith(fam_name):
                    return fam_name
        return None

    for obj in doc.Objects:
        vo = getattr(obj, "ViewObject", None)
        if vo is None or not str(getattr(obj, "TypeId", "")).startswith("PartDesign"):
            continue
        nname = obj.Name
        # pipeline-generated structures & instances: dedicated colours so the
        # rib frame and electronics read inside the body
        if nname.startswith("DS_V2_rib"):
            try:
                vo.ShapeColor=(0.20,0.45,0.95); vo.Transparency=0; vo.Deviation=0.04
            except Exception: pass
            continue
        if nname.startswith("COMP_") or nname=="BOARD_SLAB":
            try:
                vo.ShapeColor=(0.15,0.60,0.30); vo.Transparency=15
            except Exception: pass
            continue
        key = _family(getattr(obj, "Label", ""))
        if key is None:
            continue
        color, transparency = families[key]
        try:
            vo.ShapeColor = color
            vo.Transparency = transparency
            vo.Deviation = 0.04
        except Exception:
            pass

    doc.recompute()

    # The engineering default is the manufacturing-internals view.  Clearance
    # envelopes remain available through the explicit clearance mode and never
    # cover the real ribs/components during ordinary tab entry.
    from .mechanical_view_modes import apply_view_mode
    apply_view_mode(doc, "manufacturing_internals", fit=False)

    # Always restore the product's default camera. This is intentionally done
    # after visibility and recompute so fitAll sees the displayed solids rather
    # than a stale camera from a previous tab entry.
    reset_mechanical_view(name)

    _PREPARED_MECHANICAL_DOCS.add(canonical_path)
    _MECHANICAL_DOCUMENT_NAMES[canonical_path] = name
    return name


class OpenMechanical3D(_Base):
    text = "Open Mechanical 3D"
    tip = ("Open the active workspace mechanical FCStd in FreeCAD's shaded "
           "3D viewport (orbit, clipping, section, transparency)")

    def IsActive(self):
        return True

    def Activated(self):
        import os
        App, Gui, QtWidgets = _modules()
        candidates = []
        env_root = os.environ.get("DESIGNSTUDIO_WORKSPACE_PATH", "").strip()
        if env_root:
            candidates.append(Path(env_root) / "mechanical" / "product.FCStd")
        if App.ActiveDocument is not None and App.ActiveDocument.FileName:
            active = Path(App.ActiveDocument.FileName)
            if active.parent.name == "mechanical":
                candidates.append(active)
            elif active.parent.name == "electronics":
                candidates.append(active.parent.parent / "mechanical" / "product.FCStd")
        target = next((c for c in candidates if c.is_file()), None)
        if target is None:
            picked, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, "Open Mechanical Document", "", "FreeCAD Documents (*.FCStd)")
            if not picked:
                return
            target = Path(picked)
        ensure_mechanical_document(target)
        Gui.SendMsgToActiveView("ViewFit")
        App.Console.PrintMessage(f"Mechanical 3D viewport: {target}\n")


class PipelineStatus(_Base):
    text = "Product Pipeline"
    tip = ("Show deterministic product-build pipeline progress for the "
           "active workspace — the same 11-stage process every product follows")

    def IsActive(self):
        return True

    def Activated(self):
        import os
        App, Gui, QtWidgets = _modules()
        root = os.environ.get("DESIGNSTUDIO_WORKSPACE_PATH", "").strip()
        if not root and App.ActiveDocument is not None and App.ActiveDocument.FileName:
            p = Path(App.ActiveDocument.FileName)
            if p.parent.name == "mechanical":
                root = str(p.parent.parent)
            elif p.parent.name == "electronics":
                root = str(p.parent.parent.parent)
        if not (root and Path(root).is_dir()):
            QtWidgets.QMessageBox.information(
                None, "Product Pipeline",
                "Open a product workspace first, then re-run Product Pipeline.")
            return
        from . import pipeline
        report = pipeline.inspect(root)
        QtWidgets.QMessageBox.information(
            None, "Product Pipeline", pipeline.format_report(report))


_COMMAND_TYPES = {
    "DesignStudio_ReferenceForm": BeginReferenceForm,
    "DesignStudio_CaptureRedesign": CaptureLocalRedesign,
    "DesignStudio_MechanicalContract": BeginMechanicalContract,
    "DesignStudio_GenerateEnclosure": GenerateEnclosure,
    "DesignStudio_MechanicalProgram": MechanicalProgram,
    "DesignStudio_OpenMechanical3D": OpenMechanical3D,
    "DesignStudio_PipelineStatus": PipelineStatus,
    "DesignStudio_SetBoardRegion": SetBoardRegion,
    "DesignStudio_MarkFixed": MarkFixed,
    "DesignStudio_MarkConnector": MarkConnector,
    "DesignStudio_MarkMountingHole": MarkMountingHole,
    "DesignStudio_MarkHeightZone": MarkHeightZone,
    "DesignStudio_MarkCoolingZone": MarkCoolingZone,
    "DesignStudio_MarkServiceClearance": MarkServiceClearance,
    "DesignStudio_SyncContract": SyncContract,
}


def register():
    global _REGISTERED
    import FreeCADGui as Gui
    if not _REGISTERED:
        for command_name in COMMANDS:
            Gui.addCommand(command_name, _COMMAND_TYPES[command_name]())
        _REGISTERED = True
    return tuple(COMMANDS)


def verify_registered(command_names):
    """Fail activation before toolbar construction if a command is missing."""
    import FreeCADGui as Gui
    available = set(Gui.listCommands()) if hasattr(Gui, "listCommands") else set(_COMMAND_TYPES)
    missing = [name for name in command_names
               if name not in _COMMAND_TYPES or name not in available]
    if missing:
        raise RuntimeError(f"DesignStudio command registration incomplete: {missing}")
