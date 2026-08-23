"""Trusted deterministic operations used by the embedded Codex host.

The C++ host calls :func:`execute` with a registered operation and validated
JSON arguments.  Source code is never accepted from an agent.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _require_keys(args, allowed, required=()):
    unknown = set(args) - set(allowed)
    missing = set(required) - set(args)
    if unknown or missing:
        raise ValueError(f"invalid arguments; unknown={sorted(unknown)}, missing={sorted(missing)}")


def _document_name(value):
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_")
    return (clean or "DesignStudioProduct")[:80]


def _show_mechanical():
    try:
        import FreeCADGui as Gui
    except ImportError:
        return {"ok": True, "message": "Mechanical document updated in headless mode"}

    if not hasattr(Gui, "activeDocument"):
        return {"ok": True, "message": "Mechanical document updated in headless mode"}
    gui_document = Gui.activeDocument()
    if gui_document is None:
        return {"ok": True, "message": "Mechanical document updated in headless mode"}
    gui_document.activeView().viewAxonometric()
    gui_document.activeView().fitAll()
    return {"ok": True, "message": "Mechanical view is visible"}


def _physical_codesign():
    """Resolve the installed or source-tree deterministic co-design module."""
    here = Path(__file__).resolve()
    candidates = []
    for parent in here.parents:
        candidates.extend((parent, parent / "share" / "DesignStudio" / "python"))
    for candidate in candidates:
        if (candidate / "swarm" / "memory" / "physical_codesign.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            from swarm.memory import physical_codesign
            return physical_codesign
    raise RuntimeError("installed physical co-design engine is unavailable")


def _workspace_json(args, name):
    root = Path(str(args["workspace_root"])).expanduser().resolve()
    source = Path(str(args[name])).expanduser().resolve()
    if not root.is_dir() or not source.is_relative_to(root):
        raise ValueError(f"{name} must remain inside the active workspace")
    if source.suffix.lower() not in {".json", ".dsproj"} or not source.is_file():
        raise ValueError(f"{name} must identify an existing JSON or dsproj document")
    if source.stat().st_size <= 0 or source.stat().st_size > 64 * 1024 * 1024:
        raise ValueError(f"{name} is empty or exceeds 64 MiB")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return root, value


def _create_pcb_topology_study(args):
    _require_keys(args, {"workspace_root", "mechanical_path", "source_path", "study_id"},
                  {"workspace_root", "source_path"})
    root, source = _workspace_json(args, "source_path")
    engine = _physical_codesign()
    if "footprints" in source and "net_table" in source:
        source = engine.topology_source_from_project(source)
    study = engine.create_topology_study(source, study_id=args.get("study_id") or None)
    output = root / "contracts" / f"{study['study_id']}.pcb-topology-study.json"
    engine.write_record(output, study)
    return {"ok": True,
            "message": "Three provisional PCB topology alternatives generated; user approval and exact solvers remain required",
            "data": {"study_path": str(output), "study_digest": study["study_digest"],
                     "candidates": study["candidates"], "ranking_status": "provisional"}}


def _derive_mechanical_component_requirements(args):
    _require_keys(args, {"workspace_root", "mechanical_path", "source_path", "requirements_id"},
                  {"workspace_root", "source_path"})
    root, source = _workspace_json(args, "source_path")
    engine = _physical_codesign()
    requirements = engine.derive_mechanical_requirements(
        source, requirements_id=args.get("requirements_id") or None)
    output = root / "contracts" / f"{requirements['requirements_id']}.mechanical-requirements.json"
    engine.write_record(output, requirements)
    return {"ok": True,
            "message": "Mechanical requirements derived from recorded loads and interfaces; missing exact analyses remain incomplete",
            "data": {"requirements_path": str(output),
                     "requirements_digest": requirements["requirements_digest"],
                     "requirements": requirements["requirements"],
                     "analysis_summary": requirements["analysis_summary"]}}


def _open_reference_form():
    import FreeCADGui as Gui
    from .commands import ReferenceFormTask

    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    Gui.Control.showDialog(ReferenceFormTask())
    return {
        "ok": True,
        "message": "Mechanical → Reference Form opened in the native FreeCAD task panel",
    }


def _create_workspace(args):
    _require_keys(args, {"mechanical_path", "name", "family"}, {"mechanical_path", "name"})
    import FreeCAD as App

    path = str(args["mechanical_path"])
    if not path.endswith(".FCStd"):
        raise ValueError("mechanical_path must be an FCStd document")
    name = _document_name(args["name"])
    for existing in App.listDocuments().values():
        if existing.FileName == path:
            return {"ok": True, "message": "Authoritative FreeCAD document already open",
                    "data": {"document": existing.Name, "path": path, "reused": True}}
    document = App.newDocument(name)
    document.addObject("App::FeaturePython", "ProductIdentity")
    identity = document.getObject("ProductIdentity")
    identity.Label = str(args["name"])
    identity.addProperty("App::PropertyString", "ProductType", "DesignStudio")
    identity.ProductType = str(args.get("family") or "custom_concept")
    identity.addProperty("App::PropertyString", "Authority", "DesignStudio")
    identity.Authority = "FreeCAD mechanical document"
    document.recompute()
    document.saveAs(path)
    _show_mechanical()
    return {"ok": True, "message": "Authoritative FreeCAD document created",
            "data": {"document": document.Name, "path": path}}


def _import_freecad_project(args):
    """Open an existing FCStd document without exposing the legacy linker UI."""
    _require_keys(args, {"path"}, {"path"})
    from pathlib import Path
    import FreeCAD as App

    path = Path(str(args["path"])).expanduser().resolve()
    if path.suffix.lower() != ".fcstd" or not path.is_file():
        raise ValueError("path must identify an existing .FCStd document")
    for existing in App.listDocuments().values():
        if Path(existing.FileName).resolve() == path:
            _show_mechanical()
            return {"ok": True, "message": "Existing FreeCAD project focused",
                    "data": {"document": existing.Name, "path": str(path), "reused": True}}
    document = App.openDocument(str(path))
    _show_mechanical()
    return {"ok": True,
            "message": "FreeCAD project imported; save it into a product workspace to complete migration",
            "data": {"document": document.Name, "path": str(path), "reused": False}}


def _reload_after_worker(args):
    """Reload an atomically committed mechanical document in the GUI process."""
    _require_keys(args, {"mechanical_path"}, {"mechanical_path"})
    import FreeCAD as App

    path = Path(str(args["mechanical_path"])).expanduser().resolve()
    if path.suffix.lower() != ".fcstd" or not path.is_file():
        raise ValueError("mechanical_path must identify a committed FCStd document")
    for existing in tuple(App.listDocuments().values()):
        filename = str(getattr(existing, "FileName", "") or "")
        if filename and Path(filename).resolve() == path:
            App.closeDocument(existing.Name)
            break
    document = App.openDocument(str(path))
    _show_mechanical()
    return {"ok": True, "message": "Committed mechanical revision reloaded",
            "data": {"document": document.Name, "path": str(path)}}


def _mechanical_stage(args):
    allowed = {"mechanical_path", "template", "width_mm", "depth_mm", "height_mm", "wall_mm", "family"}
    required = {"mechanical_path", "template", "width_mm", "depth_mm", "height_mm", "wall_mm"}
    _require_keys(args, allowed, required)
    import FreeCAD as App
    from .enclosure_generator import create_enclosure

    width = float(args["width_mm"])
    depth = float(args["depth_mm"])
    height = float(args["height_mm"])
    wall = float(args["wall_mm"])
    if not (40 <= width <= 400 and 30 <= depth <= 300 and 15 <= height <= 160 and 1 <= wall <= 8):
        raise ValueError("enclosure dimensions are outside the registered tool limits")
    path = str(args["mechanical_path"])
    document = App.ActiveDocument
    if document is None or document.FileName != path:
        document = App.openDocument(path)
    controller = document.getObject("DesignStudioEnclosure")
    if controller is None:
        generated = create_enclosure(document, str(args["template"]), {
            "width": width, "depth": depth, "height": height, "wall": wall,
            "split": height * 0.52, "corner": 5.0, "pcb_clearance": 2.5,
        })
        controller = generated["controller"]
        reused = False
    else:
        controller.Template = str(args["template"])
        controller.Width = width
        controller.Depth = depth
        controller.Height = height
        controller.WallThickness = wall
        controller.SplitHeight = height * 0.52
        controller.CornerRadius = 5.0
        controller.PcbClearance = 2.5
        generated = {
            "controller": controller,
            "lower": document.getObject("LowerShell"),
            "upper": document.getObject("UpperShell"),
            "gasket": document.getObject("GasketChannel"),
            "legal_pcb": document.getObject("LegalPCBVolume"),
        }
        if any(value is None for key, value in generated.items() if key != "controller"):
            raise RuntimeError("existing enclosure is incomplete")
        document.recompute()
        reused = True
    if "ProductProfile" not in controller.PropertiesList:
        controller.addProperty("App::PropertyString", "ProductProfile", "DesignStudio")
    family = str(args.get("family") or "custom_concept")
    controller.ProductProfile = family
    if "PowerInput" not in controller.PropertiesList:
        controller.addProperty("App::PropertyString", "PowerInput", "DesignStudio")
    controller.PowerInput = "Defined by approved electrical configuration"
    if "FieldInterface" not in controller.PropertiesList:
        controller.addProperty("App::PropertyString", "FieldInterface", "DesignStudio")
    controller.FieldInterface = "Derived from extracted connector footprints and bound models"

    # The previous generic workflow always added condition-monitor cylinders
    # (M12, probe and LED), even for robot controllers.  Mechanical component
    # geometry now comes only from extracted footprints and optional bound STEP
    # models; no guessed component solids are created here.
    for stale_name in ("M12FieldConnector", "TemperatureProbePort", "StatusLED"):
        if document.getObject(stale_name) is not None:
            document.removeObject(stale_name)
    if generated["legal_pcb"].ViewObject is not None:
        generated["legal_pcb"].ViewObject.Transparency = 75
        generated["legal_pcb"].ViewObject.ShapeColor = (0.20, 0.55, 0.95)
        generated["gasket"].ViewObject.ShapeColor = (0.10, 0.55, 0.20)
        generated["upper"].ViewObject.Transparency = 25
    document.recompute()
    document.saveAs(path)
    _show_mechanical()
    legal_box = generated["legal_pcb"].Shape.BoundBox
    return {"ok": True, "message": "Editable product enclosure built without placeholder components",
            "data": {"document": document.Name, "object": controller.Name,
                     "dimensions_mm": [width, depth, height], "wall_mm": wall,
                     "legal_pcb_origin_mm": [float(legal_box.XMin), float(legal_box.YMin),
                                             float(legal_box.ZMin)],
                     "legal_pcb_size_mm": [float(legal_box.XLength), float(legal_box.YLength),
                                           float(legal_box.ZLength)],
                     "family": family, "interfaces": [],
                     "solid_valid": controller.BuildStatus == "valid", "reused": reused}}


def _sync_completed_pcb_3d(args):
    _require_keys(args, {"mechanical_path", "project_path"},
                  {"mechanical_path", "project_path"})
    from pathlib import Path
    import FreeCAD as App
    from .freecad_adapter import ensure_controller, sync_bound_components

    mechanical_path = Path(str(args["mechanical_path"])).expanduser().resolve()
    project_path = Path(str(args["project_path"])).expanduser().resolve()
    if mechanical_path.suffix.lower() != ".fcstd" or not mechanical_path.is_file():
        raise ValueError("mechanical_path must identify the workspace FCStd document")
    if project_path.suffix.lower() != ".dsproj" or not project_path.is_file():
        raise ValueError("project_path must identify the completed electronics document")
    document = next((item for item in App.listDocuments().values()
                     if item.FileName and Path(item.FileName).resolve() == mechanical_path), None)
    if document is None:
        document = App.openDocument(str(mechanical_path))
    controller = ensure_controller(document)
    controller.ProjectPath = str(project_path)
    report = sync_bound_components(document, controller)
    legal = document.getObject("LegalPCBVolume")
    if legal is not None and getattr(legal, "ViewObject", None) is not None:
        legal.ViewObject.Visibility = False
    for shell_name in ("LowerShell", "UpperShell"):
        shell = document.getObject(shell_name)
        if shell is not None and getattr(shell, "ViewObject", None) is not None:
            shell.ViewObject.Transparency = 80
    document.recompute()
    document.saveAs(str(mechanical_path))
    _show_mechanical()
    return {"ok": True,
            "message": (f"Completed PCB 3D view: {report['imported']} component assets "
                        f"({report['ready']} ready, {report['pending']} proxy/pending, "
                        f"{report['missing']} missing)"),
            "data": report}


def _generate_interaction_structure(args):
    """Turn an approved neutral support specification into exact FreeCAD B-Rep."""
    _require_keys(args, {"mechanical_path", "workspace_root", "spec_path", "result_path"},
                  {"mechanical_path", "workspace_root", "spec_path"})
    from pathlib import Path
    import FreeCAD as App
    from .interaction_structure import create_support_structure

    mechanical_path = Path(str(args["mechanical_path"])).expanduser().resolve()
    workspace_root = Path(str(args["workspace_root"])).expanduser().resolve()
    spec_path = Path(str(args["spec_path"])).expanduser().resolve()
    if not workspace_root.is_dir():
        raise ValueError("workspace_root must identify the active workspace")
    if not spec_path.is_relative_to(workspace_root):
        raise ValueError("spec_path must remain inside workspace_root")
    if not mechanical_path.is_relative_to(workspace_root):
        raise ValueError("mechanical_path must remain inside workspace_root")
    if mechanical_path.suffix.lower() != ".fcstd" or not mechanical_path.is_file():
        raise ValueError("mechanical_path must identify a workspace FCStd document")
    if spec_path.suffix.lower() != ".json" or not spec_path.is_file():
        raise ValueError("spec_path must identify a support-generation JSON document")
    if spec_path.stat().st_size <= 0 or spec_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("support specification is empty or exceeds 64 MiB")
    specification = json.loads(spec_path.read_text(encoding="utf-8"))
    candidate_result = None
    if args.get("result_path"):
        result_path = Path(str(args["result_path"])).expanduser().resolve()
        if not result_path.is_relative_to(workspace_root):
            raise ValueError("result_path must remain inside workspace_root")
        if result_path.suffix.lower() != ".json" or not result_path.is_file():
            raise ValueError("result_path must identify a support-result JSON document")
        if result_path.stat().st_size <= 0 or result_path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("support result is empty or exceeds 64 MiB")
        candidate_result = json.loads(result_path.read_text(encoding="utf-8"))
    document = next((item for item in App.listDocuments().values()
                     if item.FileName and Path(item.FileName).resolve() == mechanical_path), None)
    if document is None:
        document = App.openDocument(str(mechanical_path))
    result = create_support_structure(document, specification, candidate_result)
    document.saveAs(str(mechanical_path))
    _show_mechanical()
    return {
        "ok": True,
        "message": (f"Generated {len(result['cavities'])} protected cavities and "
                    f"{len(result['ribs'])} exact parametric rib segments"),
        "data": {
            "document": document.Name,
            "input_sha256": result["input_sha256"],
            "result_sha256": result["result_sha256"],
            "objects": result["freecad_objects"],
            "required_host_checks": result["required_host_checks"],
        },
    }


def _apply_mechanical_cad_program(args):
    """Apply an approved typed mechanical feature program in FreeCAD."""
    _require_keys(args, {"mechanical_path", "workspace_root", "program_path", "program"},
                  {"mechanical_path", "workspace_root"})
    from pathlib import Path
    import FreeCAD as App
    from .mechanical_cad import execute_program, validate_program

    workspace_root = Path(str(args["workspace_root"])).expanduser().resolve()
    mechanical_path = Path(str(args["mechanical_path"])).expanduser().resolve()
    if not workspace_root.is_dir():
        raise ValueError("workspace_root must identify the active workspace")
    if not mechanical_path.is_relative_to(workspace_root):
        raise ValueError("mechanical_path must remain inside workspace_root")
    if mechanical_path.suffix.lower() != ".fcstd" or not mechanical_path.is_file():
        raise ValueError("mechanical_path must identify a workspace FCStd document")
    has_path = "program_path" in args
    has_inline = "program" in args
    if has_path == has_inline:
        raise ValueError("provide exactly one of program_path or program")
    if has_path:
        program_path = Path(str(args["program_path"])).expanduser().resolve()
        if not program_path.is_relative_to(workspace_root):
            raise ValueError("program_path must remain inside workspace_root")
        if program_path.suffix.lower() != ".json" or not program_path.is_file():
            raise ValueError("program_path must identify a mechanical CAD JSON document")
        if program_path.stat().st_size <= 0 or program_path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("mechanical CAD program is empty or exceeds 8 MiB")
        try:
            program = json.loads(program_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"mechanical CAD program is invalid JSON: {exc}") from exc
    else:
        normalized = validate_program(args["program"])
        program = normalized
        program_directory = workspace_root / "mechanical" / "programs"
        program_directory.mkdir(parents=True, exist_ok=True)
        program_path = program_directory / f"{normalized['program_id']}.json"
        program_path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    document = next((item for item in App.listDocuments().values()
                     if item.FileName and Path(item.FileName).resolve() == mechanical_path), None)
    if document is None:
        document = App.openDocument(str(mechanical_path))
    receipt = execute_program(document, program)
    receipt["program_path"] = str(program_path)
    document.recompute()
    document.saveAs(str(mechanical_path))
    _show_mechanical()
    return {
        "ok": True,
        "message": (f"Applied mechanical CAD program {receipt['program_id']} with "
                     f"{len(receipt['objects'])} editable FreeCAD features"),
        "data": receipt,
    }


def _physical_workspace_document(args, required_extra=()):
    """Resolve the active workspace and FCStd document for physical tools."""
    allowed = {"mechanical_path", "workspace_root", *required_extra}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root", *required_extra})
    from pathlib import Path
    import FreeCAD as App

    root = Path(str(args["workspace_root"])).expanduser().resolve()
    mechanical = Path(str(args["mechanical_path"])).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("workspace_root must identify the active workspace")
    if not mechanical.is_relative_to(root) or mechanical.suffix.lower() != ".fcstd":
        raise ValueError("mechanical_path must be an FCStd document inside workspace_root")
    if not mechanical.is_file():
        raise ValueError("mechanical_path does not exist")
    document = next((item for item in App.listDocuments().values()
                     if item.FileName and Path(item.FileName).resolve() == mechanical), None)
    if document is None:
        document = App.openDocument(str(mechanical))
    return root, mechanical, document


def _create_physical_design_session(args):
    allowed = {"mechanical_path", "workspace_root", "session", "session_path"}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root"})
    root, _, _ = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"],
         "workspace_root": args["workspace_root"]})
    has_inline = isinstance(args.get("session"), dict)
    has_path = "session_path" in args
    if has_inline == has_path:
        raise ValueError("provide exactly one of session or session_path")
    if has_path:
        from pathlib import Path
        source = Path(str(args["session_path"])).expanduser().resolve()
        if not source.is_relative_to(root) or source.suffix.lower() != ".json" or not source.is_file():
            raise ValueError("session_path must identify a workspace JSON file")
        if source.stat().st_size <= 0 or source.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("physical design session is empty or exceeds 8 MiB")
        session = json.loads(source.read_text(encoding="utf-8"))
    else:
        session = args["session"]
    from .physical_design import create_physical_design_session
    result = create_physical_design_session(root, session)
    return {"ok": True,
            "message": (f"Physical design session accepted in {result['workflow_mode']} mode; "
                        "images and sketches remain non-authoritative evidence"),
            "data": result}


def _create_physical_design_session_v2(args):
    allowed = {"mechanical_path", "workspace_root", "session", "session_path"}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root"})
    root, _, _ = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"],
         "workspace_root": args["workspace_root"]})
    has_inline = isinstance(args.get("session"), dict)
    has_path = "session_path" in args
    if has_inline == has_path:
        raise ValueError("provide exactly one v2 session or session_path")
    if has_path:
        from pathlib import Path
        source = Path(str(args["session_path"])).expanduser().resolve()
        if not source.is_relative_to(root) or source.suffix.lower() != ".json" or not source.is_file():
            raise ValueError("session_path must identify a workspace v2 JSON file")
        session = json.loads(source.read_text(encoding="utf-8"))
    else:
        session = args["session"]
    from .constraint_candidates import create_physical_design_session_v2
    result = create_physical_design_session_v2(root, session)
    return {"ok": True,
            "message": (f"Constraint-driven physical design session accepted in {result['workflow_mode']} mode; "
                        "exactly three editable candidates are ready to generate"),
            "data": result}


def _create_guided_physical_design_session(args):
    """Translate the guided form into the strict public v2 session contract."""
    allowed = {"mechanical_path", "workspace_root", "capture"}
    _require_keys(args, allowed, allowed)
    root, mechanical, _ = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    capture = args["capture"]
    if not isinstance(capture, dict):
        raise ValueError("capture must be the structured guided form")
    required = {"session_id", "product_family", "workflow_mode", "evidence_path", "evidence_sha256",
                "evidence_kind", "known_length_mm", "pixels_per_mm", "occupied_volumes", "desire",
                "material", "process", "created_utc"}
    _require_keys(capture, required, required)
    from pathlib import Path
    import hashlib
    import shutil
    assets = []
    evidence_path = str(capture["evidence_path"]).strip()
    if evidence_path:
        source = Path(evidence_path).expanduser().resolve()
        suffix_media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".webp": "image/webp", ".svg": "image/svg+xml", ".pdf": "application/pdf"}
        if not source.is_file() or source.suffix.lower() not in suffix_media \
                or source.stat().st_size <= 0 or source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("evidence must be a supported readable file no larger than 64 MiB")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != str(capture["evidence_sha256"]):
            raise ValueError("evidence changed after the guided UI hashed it")
        destination = root / "assets" / "physical-design" / f"{actual}{source.suffix.lower()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != actual:
            raise ValueError("workspace evidence destination contains conflicting bytes")
        if not destination.exists():
            shutil.copy2(source, destination)
        kind = str(capture["evidence_kind"])
        role = "measurement_reference" if kind == "engineering_drawing" else "silhouette"
        assets.append({"asset_id": "guided-evidence", "path": str(destination.relative_to(root)),
            "sha256": actual, "media_type": suffix_media[source.suffix.lower()], "input_kind": kind,
            "role": role, "geometry_authority": False, "measurement_status": "user_calibrated",
            "calibration": {"method": "known_length", "pixels_per_mm": float(capture["pixels_per_mm"]),
                            "verified": True}})
    raw_volumes = capture["occupied_volumes"]
    if not isinstance(raw_volumes, list) or not raw_volumes:
        raise ValueError("add at least one measured locked hardware volume")
    occupied, clearances = [], []
    for index, raw in enumerate(raw_volumes):
        if not isinstance(raw, dict) or set(raw) != {"kind", "semantic_id", "min_mm", "max_mm"}:
            raise ValueError("every guided volume requires kind, semantic ID and min/max coordinates")
        entry = {"volume_id": f"guided-volume-{index + 1}", "semantic_id": raw["semantic_id"],
                 "kind": raw["kind"], "min_mm": raw["min_mm"], "max_mm": raw["max_mm"],
                 "clearance_mm": 2.0, "locked": True, "source": "guided verified measurement"}
        (clearances if raw["kind"] in {"human_clearance", "service"} else occupied).append(entry)
    if not occupied:
        raise ValueError("at least one hardware/mechanism/PCB/battery/display/connector/cable volume is required")
    material = str(capture["material"]).strip()
    desire = str(capture["desire"]).strip()
    process = str(capture["process"]).strip()
    if not material or not process:
        raise ValueError("material and process are required")
    from .physical_design import file_digest
    from .constraint_candidates import create_physical_design_session_v2
    session = {
        "schema": "design-studio.physical-design-session/2", "session_id": str(capture["session_id"]),
        "revision": 1, "product_family": str(capture["product_family"]),
        "workflow_mode": str(capture["workflow_mode"]),
        "document": {"document_id": "GuidedPhysicalProduct", "path": str(mechanical.relative_to(root)),
                     "sha256": file_digest(mechanical)},
        "intent": desire or "Compare three editable ergonomic candidates around locked measured hardware.",
        "input_assets": assets,
        "verified_dimensions": [{"dimension_id": "guided-known-length", "source": "guided calibration",
            "axis": "X", "value_mm": float(capture["known_length_mm"]), "tolerance_mm": 0.1,
            "status": "user_calibrated"}],
        "occupied_volumes": occupied, "clearance_volumes": clearances,
        "desire_capture": ([{"desire_id": "guided-desire", "statement": desire,
            "priority": "required", "status": "user_confirmed"}] if desire else []),
        "interaction_objectives": [{"objective_id": "guided-grip", "metric": "grip", "target": 0.0,
            "unit": "physical-test-required", "status": "unresolved"}],
        "material_constraints": [{"region_id": "shell", "candidate_materials": [material],
            "requirements": [process], "evidence_status": "unverified"}],
        "manufacturing_constraints": {"processes": [process], "units": "mm", "minimum_wall_mm": 2.0,
            "minimum_feature_mm": 1.2, "status": "provisional"},
        "hard_constraints": {"locked_envelope": {"min_mm": [0.0, 0.0, 0.0], "max_mm": [160.0, 106.0, 66.0]},
            "max_external_dimensions_mm": [160.0, 106.0, 66.0], "minimum_clearance_mm": 2.0,
            "require_watertight_solid": True, "forbid_interference": True},
        "scoring_weights": {"occupied_volume": 1.0, "reach": 1.0, "clearance": 1.0, "wall": 1.0,
            "continuity": 1.0, "manufacturability": 1.0, "mass": 1.0, "material_use": 1.0,
            "user_priority": 1.0},
        "unresolved_evidence": ["physical grip observations not yet recorded", "exact solvers pending"],
        "geometry_authority": {"authoritative_sources": ["freecad_brep", "typed_mechanical_cad_program", "verified_measurements"],
            "raster_and_sketch_are_geometry_authority": False,
            "unknown_dimensions_policy": "blocked_until_measured_or_explicitly_constrained"},
        "candidate_policy": {"exact_candidate_count": 3, "labels": ["compact", "balanced", "comfort"],
            "ranking_policy": "hard_failures_removed_missing_analysis_incomplete"},
        "candidates": None,
        "provenance": {"created_by": "DesignStudio guided physical-design panel",
            "created_utc": str(capture["created_utc"]),
            "user_prompt": "Guided form capture; image/sketch evidence is non-authoritative."},
    }
    result = create_physical_design_session_v2(root, session)
    return {"ok": True,
            "message": "Guided photo/sketch/hardware capture was converted to a strict provisional physical-design session",
            "data": result}


def _generate_constraint_candidates(args):
    allowed = {"mechanical_path", "workspace_root", "session_path"}
    _require_keys(args, allowed, allowed)
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"],
         "workspace_root": args["workspace_root"]})
    from pathlib import Path
    source = Path(str(args["session_path"])).expanduser().resolve()
    if not source.is_relative_to(root) or source.suffix.lower() != ".json" or not source.is_file():
        raise ValueError("session_path must identify a workspace v2 JSON file")
    from .constraint_candidates import generate_constraint_candidates
    result = generate_constraint_candidates(document, root, source)
    _show_mechanical()
    return {"ok": True,
            "message": ("Generated exactly three constraint-driven editable candidates; "
                        "hard failures were removed from ranking and missing analyses remain incomplete"),
            "data": result}


def _export_physical_test_plan(args):
    allowed = {"mechanical_path", "workspace_root", "session_path", "request"}
    _require_keys(args, allowed, allowed)
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    source = Path(str(args["session_path"])).expanduser().resolve()
    if not source.is_relative_to(root) or source.suffix.lower() != ".json" or not source.is_file():
        raise ValueError("session_path must identify a workspace v2 session JSON file")
    if not isinstance(args["request"], dict):
        raise ValueError("request must be a structured physical test plan")
    from .physical_validation import export_grip_test_plan
    result = export_grip_test_plan(document, root, source, args["request"])
    return {"ok": True,
            "message": "Exported three digest-bound grip-only FCStd, STEP and STL bucks; ranking remains provisional",
            "data": result}


def _record_physical_observation(args):
    allowed = {"mechanical_path", "workspace_root", "plan_path", "observation"}
    _require_keys(args, allowed, allowed)
    root, _, _ = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    plan = Path(str(args["plan_path"])).expanduser().resolve()
    if not plan.is_relative_to(root) or plan.suffix.lower() != ".json" or not plan.is_file():
        raise ValueError("plan_path must identify a workspace physical test plan")
    if not isinstance(args["observation"], dict):
        raise ValueError("observation must be the fixed structured 1-5 form")
    from .physical_validation import record_observation
    result = record_observation(root, plan, args["observation"])
    return {"ok": True,
            "message": "Physical observation recorded with candidate and buck digests; no winner was inferred",
            "data": result}


def _create_physical_candidate_decision(args):
    allowed = {"mechanical_path", "workspace_root", "plan_path", "observation_paths",
               "decision_id", "observer", "created_utc"}
    _require_keys(args, allowed, allowed)
    root, _, _ = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    plan = Path(str(args["plan_path"])).expanduser().resolve()
    if not plan.is_relative_to(root) or plan.suffix.lower() != ".json" or not plan.is_file():
        raise ValueError("plan_path must identify a workspace physical test plan")
    observation_paths = args["observation_paths"]
    if not isinstance(observation_paths, list) or not all(isinstance(item, str) for item in observation_paths):
        raise ValueError("observation_paths must be an array")
    resolved = []
    for item in observation_paths:
        path = Path(item).expanduser()
        path = path.resolve() if path.is_absolute() else (root / path).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".json" or not path.is_file():
            raise ValueError("every observation must be a workspace JSON file")
        resolved.append(str(path))
    from .physical_validation import create_candidate_decision
    result = create_candidate_decision(root, plan, resolved,
        decision_id=str(args["decision_id"]), observer=str(args["observer"]),
        created_utc=str(args["created_utc"]))
    return {"ok": True,
            "message": "A real digest-bound physical candidate decision was created from complete hand-size coverage",
            "data": result}


def _capture_selected_design_region(args):
    allowed = {"mechanical_path", "workspace_root", "intent", "max_expansion_mm",
               "protected_semantic_ids", "physical_design_session_path"}
    required = {"mechanical_path", "workspace_root", "intent", "max_expansion_mm"}
    _require_keys(args, allowed, required)
    base = {"mechanical_path": args["mechanical_path"],
            "workspace_root": args["workspace_root"]}
    root, mechanical, document = _physical_workspace_document(base)
    protected = args.get("protected_semantic_ids", [])
    if not isinstance(protected, list) or not all(isinstance(item, str) for item in protected):
        raise ValueError("protected_semantic_ids must be an array of strings")
    session_path = args.get("physical_design_session_path")
    if session_path:
        from pathlib import Path
        session_path = Path(str(session_path)).expanduser()
        if not session_path.is_absolute():
            session_path = root / session_path
    from .physical_design import capture_local_redesign
    result = capture_local_redesign(
        document, root, str(args["intent"]), float(args["max_expansion_mm"]),
        protected, session_path)
    document.recompute()
    document.saveAs(str(mechanical))
    return {"ok": True,
            "message": "One semantic object/face region captured for a bounded AI redesign",
            "data": result}


def _preview_local_redesign(args):
    allowed = {"mechanical_path", "workspace_root", "redesign_path", "program",
               "candidate_command_id"}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root", "redesign_path"})
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"],
         "workspace_root": args["workspace_root"]})
    from pathlib import Path
    redesign_path = Path(str(args["redesign_path"])).expanduser()
    if not redesign_path.is_absolute():
        redesign_path = root / redesign_path
    has_program = isinstance(args.get("program"), dict)
    has_candidate = isinstance(args.get("candidate_command_id"), str)
    if has_program != has_candidate:
        raise ValueError("program and candidate_command_id must be supplied together")
    prepared = None
    if has_program:
        from .physical_design import prepare_local_redesign
        prepared = prepare_local_redesign(
            root, redesign_path, args["program"], args["candidate_command_id"])
        redesign_path = Path(prepared["path"])
    from .physical_design import preview_local_redesign
    result = preview_local_redesign(document, root, redesign_path)
    if prepared is not None:
        result["program_ready_path"] = prepared["path"]
    _show_mechanical()
    return {"ok": True,
            "message": "Local redesign rebuilt and verified as a sibling preview; baseline is still visible",
            "data": result}


def _commit_local_redesign(args):
    allowed = {"mechanical_path", "workspace_root", "preview_receipt_path"}
    _require_keys(args, allowed, allowed)
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"],
         "workspace_root": args["workspace_root"]})
    from pathlib import Path
    receipt_path = Path(str(args["preview_receipt_path"])).expanduser()
    if not receipt_path.is_absolute():
        receipt_path = root / receipt_path
    from .physical_design import commit_local_redesign
    result = commit_local_redesign(document, root, receipt_path)
    _show_mechanical()
    return {"ok": True,
            "message": "Local redesign committed; stable identity transferred and baseline retained hidden",
            "data": result}


def _capture_local_redesign_v2(args):
    allowed = {"mechanical_path", "workspace_root", "intent", "permitted_expansion_mm",
               "protected_objects", "continuity_required", "manufacturing", "redesign_id"}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root", "intent",
                                  "permitted_expansion_mm"})
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from .local_redesign_v2 import capture_local_redesign_v2
    result = capture_local_redesign_v2(
        document, root, args["intent"], float(args["permitted_expansion_mm"]),
        args.get("protected_objects", []), args.get("continuity_required", "G1"),
        args.get("manufacturing"), redesign_id=args.get("redesign_id"))
    document.recompute()
    document.save()
    return {"ok": True, "message": "Topology-aware FaceN local redesign scope captured",
            "data": result}


def _preview_local_redesign_v2(args):
    allowed = {"mechanical_path", "workspace_root", "redesign_path", "program",
               "candidate_command_id", "candidate_faces"}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root", "redesign_path"})
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    redesign_path = Path(str(args["redesign_path"])).expanduser()
    if not redesign_path.is_absolute():
        redesign_path = root / redesign_path
    has_program = isinstance(args.get("program"), dict)
    has_candidate = isinstance(args.get("candidate_command_id"), str)
    has_faces = isinstance(args.get("candidate_faces"), list)
    if has_program != has_candidate or has_program != has_faces:
        raise ValueError("v2 preview requires program, candidate command ID, and candidate_faces together")
    prepared = None
    if has_program:
        from .local_redesign_v2 import prepare_local_redesign_v2
        prepared = prepare_local_redesign_v2(root, redesign_path, args["program"],
                                             args["candidate_command_id"], args["candidate_faces"])
        redesign_path = Path(prepared["path"])
    from .local_redesign_v2 import preview_local_redesign_v2
    result = preview_local_redesign_v2(document, root, redesign_path)
    if prepared is not None:
        result["program_ready_path"] = prepared["path"]
    _show_mechanical()
    return {"ok": True, "message": "Topology-aware local patch rebuilt and verified as a sibling preview",
            "data": result}


def _commit_local_redesign_v2(args):
    allowed = {"mechanical_path", "workspace_root", "preview_receipt_path"}
    _require_keys(args, allowed, allowed)
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    receipt = Path(str(args["preview_receipt_path"])).expanduser()
    if not receipt.is_absolute():
        receipt = root / receipt
    from .local_redesign_v2 import commit_local_redesign_v2
    result = commit_local_redesign_v2(document, root, receipt)
    _show_mechanical()
    return {"ok": True, "message": "Topology-aware local patch committed; baseline retained for rollback",
            "data": result}


def _discard_local_redesign_v2(args):
    allowed = {"mechanical_path", "workspace_root", "preview_receipt_path"}
    _require_keys(args, allowed, allowed)
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    receipt = Path(str(args["preview_receipt_path"])).expanduser()
    if not receipt.is_absolute():
        receipt = root / receipt
    from .local_redesign_v2 import discard_local_redesign_v2
    result = discard_local_redesign_v2(document, root, receipt)
    _show_mechanical()
    return {"ok": True,
            "message": "Uncommitted local patch preview rejected; baseline remains active",
            "data": result}


def _rollback_local_redesign_v2(args):
    allowed = {"mechanical_path", "workspace_root", "commit_receipt_path"}
    _require_keys(args, allowed, allowed)
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    receipt = Path(str(args["commit_receipt_path"])).expanduser()
    if not receipt.is_absolute():
        receipt = root / receipt
    from .local_redesign_v2 import rollback_local_redesign_v2
    result = rollback_local_redesign_v2(document, root, receipt)
    _show_mechanical()
    return {"ok": True, "message": "Topology-aware local patch rolled back to retained baseline",
            "data": result}


def _generate_authoritative_drawings(args):
    allowed = {"mechanical_path", "workspace_root", "source_semantic_id", "output_directory",
               "sections", "selected_faces", "selected_edges", "package_id"}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root", "source_semantic_id", "output_directory"})
    root, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from pathlib import Path
    from .physical_design import _find_semantic
    from .authoritative_drawings import make_authoritative_request, generate_authoritative_drawing_package
    source = _find_semantic(document, str(args["source_semantic_id"]))
    package = make_authoritative_request(
        document, root, source, str(args["output_directory"]),
        sections=args.get("sections", []), selected_faces=args.get("selected_faces", []),
        selected_edges=args.get("selected_edges", []), package_id=args.get("package_id"))
    request_path = root / "contracts" / f"authoritative-drawing-request-{package['package_id']}.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    result = generate_authoritative_drawing_package(document, root, request_path)
    _show_mechanical()
    return {"ok": True,
            "message": "Authoritative HLR engineering drawings generated from the FreeCAD B-Rep",
            "data": {**result, "request_path": str(request_path)}}


def _select_semantic_object(args):
    """Select the authoritative FreeCAD feature for a shared UI identity."""
    allowed = {"mechanical_path", "workspace_root", "semantic_id", "reference_designator"}
    _require_keys(args, allowed, {"mechanical_path", "workspace_root"})
    _, _, document = _physical_workspace_document(
        {"mechanical_path": args["mechanical_path"], "workspace_root": args["workspace_root"]})
    from .physical_design import _find_semantic
    semantic_id = str(args["semantic_id"]).strip()
    reference = str(args.get("reference_designator", "")).strip()
    obj = None
    if semantic_id:
        try:
            obj = _find_semantic(document, semantic_id)
        except Exception:
            obj = None
    if obj is None and reference:
        obj = next((candidate for candidate in getattr(document, "Objects", ())
                    if str(getattr(candidate, "ReferenceDesignator", "")).strip() == reference), None)
    if obj is None:
        raise ValueError("FreeCAD semantic object was not found by AP242 identity or reference designator")
    try:
        import FreeCADGui as Gui
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)
    except Exception as exc:
        raise RuntimeError(f"FreeCAD selection bridge unavailable: {exc}") from exc
    return {"ok": True, "message": f"Selected FreeCAD object {obj.Name}",
            "data": {"semantic_id": semantic_id or str(getattr(obj, "DesignStudioSemanticId", "")),
                     "reference_designator": reference, "object_name": obj.Name}}


def execute(operation, arguments_json):
    """Execute one registered operation and return a compact JSON result."""
    try:
        arguments = json.loads(arguments_json)
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        if operation == "create_product_workspace":
            result = _create_workspace(arguments)
        elif operation == "import_freecad_project":
            result = _import_freecad_project(arguments)
        elif operation == "reload_after_worker":
            result = _reload_after_worker(arguments)
        elif operation == "apply_mechanical_stage":
            result = _mechanical_stage(arguments)
        elif operation == "sync_completed_pcb_3d":
            result = _sync_completed_pcb_3d(arguments)
        elif operation == "generate_interaction_structure":
            result = _generate_interaction_structure(arguments)
        elif operation == "apply_mechanical_cad_program":
            result = _apply_mechanical_cad_program(arguments)
        elif operation == "create_physical_design_session":
            result = _create_physical_design_session(arguments)
        elif operation == "create_physical_design_session_v2":
            result = _create_physical_design_session_v2(arguments)
        elif operation == "create_guided_physical_design_session":
            result = _create_guided_physical_design_session(arguments)
        elif operation == "generate_constraint_candidates":
            result = _generate_constraint_candidates(arguments)
        elif operation == "export_physical_test_plan":
            result = _export_physical_test_plan(arguments)
        elif operation == "record_physical_observation":
            result = _record_physical_observation(arguments)
        elif operation == "create_physical_candidate_decision":
            result = _create_physical_candidate_decision(arguments)
        elif operation == "capture_selected_design_region":
            result = _capture_selected_design_region(arguments)
        elif operation == "preview_local_redesign":
            result = _preview_local_redesign(arguments)
        elif operation == "commit_local_redesign":
            result = _commit_local_redesign(arguments)
        elif operation == "capture_local_redesign_v2":
            result = _capture_local_redesign_v2(arguments)
        elif operation == "preview_local_redesign_v2":
            result = _preview_local_redesign_v2(arguments)
        elif operation == "commit_local_redesign_v2":
            result = _commit_local_redesign_v2(arguments)
        elif operation == "discard_local_redesign_v2":
            result = _discard_local_redesign_v2(arguments)
        elif operation == "rollback_local_redesign_v2":
            result = _rollback_local_redesign_v2(arguments)
        elif operation == "generate_authoritative_drawings":
            result = _generate_authoritative_drawings(arguments)
        elif operation == "create_pcb_topology_study":
            result = _create_pcb_topology_study(arguments)
        elif operation == "derive_mechanical_component_requirements":
            result = _derive_mechanical_component_requirements(arguments)
        elif operation == "select_semantic_object":
            result = _select_semantic_object(arguments)
        elif operation == "open_reference_form":
            _require_keys(arguments, set())
            result = _open_reference_form()
        elif operation == "show_workspace_view":
            _require_keys(arguments, {"view"}, {"view"})
            if arguments["view"] not in ("mechanical", "pcb_3d"):
                raise ValueError("FreeCAD dispatcher owns only mechanical and completed PCB 3D views")
            result = _show_mechanical()
        else:
            raise ValueError(f"unregistered native operation: {operation}")
    except Exception as exc:
        result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, separators=(",", ":"))
