"""Cross-domain bound component records.

A bound component is the only component object consumed after library
resolution. It joins symbol pins, concrete pad geometry, courtyard, STEP model,
and datasheet electrical behavior under one digest so schematic, PCB, FreeCAD,
and analysis cannot silently select different variants.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from swarm.runtime_paths import bound_component_library_dir

SCHEMA = "design-studio.bound-component/1"
_STEP_HEADER = b"ISO-10303-21"


class BindingError(ValueError):
    def __init__(self, issues: list[str] | str):
        self.issues = issues if isinstance(issues, list) else [issues]
        super().__init__("; ".join(self.issues))


def _normalize_number(value: Any) -> str:
    return str(value or "").strip().upper()


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BindingError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise BindingError(f"{label} must be finite")
    return result


def _canonical(value: Any) -> bytes:
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def binding_digest(binding: dict) -> str:
    payload = copy.deepcopy(binding)
    payload.pop("binding_digest", None)
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _source_digest(component: dict) -> str:
    return hashlib.sha256(_canonical(component)).hexdigest()


def _step_metadata(path: Path) -> dict:
    if not path.is_file():
        raise BindingError(f"STEP model does not exist: {path}")
    if path.suffix.lower() not in (".step", ".stp"):
        raise BindingError("3D model must use .step or .stp")
    size = path.stat().st_size
    if size < len(_STEP_HEADER):
        raise BindingError("STEP model is empty or truncated")
    with path.open("rb") as handle:
        head = handle.read(4096).upper()
    if _STEP_HEADER not in head:
        raise BindingError("3D asset is not an ISO-10303-21 STEP exchange file")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"format": "step", "asset_uri": str(path.resolve()),
            "sha256": sha, "byte_size": size}


def _resolved_footprint(component: dict) -> dict:
    footprint = copy.deepcopy(component.get("footprint") or {})
    if footprint.get("pads"):
        return footprint
    try:
        from . import ic_footprints
        generated = ic_footprints.regenerate_footprint(component)
    except Exception:
        generated = None
    if generated and generated.get("pads"):
        footprint.update(generated)
    return footprint


def _courtyard(footprint: dict) -> list[list[float]]:
    pads = footprint.get("pads") or []
    xs: list[float] = []
    ys: list[float] = []
    for index, pad in enumerate(pads):
        x = _finite(pad.get("x_mm"), f"pad {index} x_mm")
        y = _finite(pad.get("y_mm"), f"pad {index} y_mm")
        width = _finite(pad.get("width_mm"), f"pad {index} width_mm")
        height = _finite(pad.get("height_mm"), f"pad {index} height_mm")
        if width <= 0 or height <= 0:
            raise BindingError(f"pad {index} dimensions must be positive")
        xs.extend((x - width / 2, x + width / 2))
        ys.extend((y - height / 2, y + height / 2))
    body = footprint.get("body") or {}
    body_w = body.get("width_mm")
    body_l = body.get("length_mm")
    if isinstance(body_w, (int, float)) and body_w > 0:
        xs.extend((-float(body_w) / 2, float(body_w) / 2))
    if isinstance(body_l, (int, float)) and body_l > 0:
        ys.extend((-float(body_l) / 2, float(body_l) / 2))
    if not xs or not ys:
        raise BindingError("footprint has no measurable pads or body")
    margin = footprint.get("courtyard_margin_mm")
    margin = 0.5 if margin is None else _finite(margin, "courtyard_margin_mm")
    if margin < 0:
        raise BindingError("courtyard margin cannot be negative")
    x0, x1 = min(xs) - margin, max(xs) + margin
    y0, y1 = min(ys) - margin, max(ys) + margin
    return [[round(x0, 6), round(y0, 6)], [round(x1, 6), round(y0, 6)],
            [round(x1, 6), round(y1, 6)], [round(x0, 6), round(y1, 6)]]


def _identity_transform(standoff_mm: float = 0.0) -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, standoff_mm, 0, 0, 0, 1]


def bind_component(component2: dict, step_path: str | Path,
                   *, model_transform: list[float] | None = None,
                   alignment_status: str = "unverified",
                   model_mpn: str = "",
                   simulation_models: dict | None = None,
                   source_uri: str = "") -> dict:
    """Create a complete binding or raise `BindingError`.

    `model_transform` maps STEP model coordinates into footprint coordinates in mm.
    The default is identity plus the datasheet package standoff; callers must
    explicitly label its review state with `alignment_status`.
    """
    issues: list[str] = []
    if not isinstance(component2, dict) \
            or component2.get("schema") != "design-studio.component/2":
        raise BindingError("expected design-studio.component/2")
    identity = component2.get("component") or {}
    mpn = str(identity.get("mpn") or "").strip()
    if not mpn:
        issues.append("component MPN is missing")
    if not model_mpn:
        issues.append("STEP model MPN assertion is required")
    elif re.sub(r"[^A-Z0-9]", "", model_mpn.upper()) \
            != re.sub(r"[^A-Z0-9]", "", mpn.upper()):
        issues.append(f"STEP model MPN {model_mpn!r} does not match component MPN {mpn!r}")

    symbol = copy.deepcopy(component2.get("symbol") or {})
    pins = symbol.get("pins") or []
    if not pins:
        issues.append("schematic symbol has no pins")
    pin_numbers: list[str] = []
    for index, pin in enumerate(pins):
        number = _normalize_number(pin.get("number"))
        if not number:
            issues.append(f"symbol pin {index} has no number")
        elif number in pin_numbers:
            issues.append(f"duplicate symbol pin number {number}")
        pin_numbers.append(number)

    footprint = _resolved_footprint(component2)
    pads = footprint.get("pads") or []
    if not pads:
        issues.append("2D land pattern has no concrete pads")
    pad_ids: list[str] = []
    electrical_by_number: dict[str, list[str]] = {}
    normalized_pads = []
    for index, pad in enumerate(pads):
        normalized = copy.deepcopy(pad)
        number = _normalize_number(pad.get("number", pad.get("name")))
        if not number:
            issues.append(f"pad {index} has no number")
        pad_id = f"pad:{index + 1}"
        normalized["pad_id"] = pad_id
        normalized["number"] = number
        pad_ids.append(pad_id)
        normalized_pads.append(normalized)
        if not bool(pad.get("mechanical", False)):
            electrical_by_number.setdefault(number, []).append(pad_id)
    footprint["pads"] = normalized_pads

    pin_pad_map = []
    known_pins = set(pin_numbers)
    for pin, original in zip(pin_numbers, pins):
        mapped = electrical_by_number.get(pin, [])
        if not mapped:
            issues.append(f"symbol pin {pin or '?'} has no electrical pad")
        pin_pad_map.append({"pin_number": pin, "pin_name": original.get("name", ""),
                            "pad_ids": mapped})
    for number in sorted(set(electrical_by_number) - known_pins):
        issues.append(f"electrical pad {number} is not present in the symbol")

    electrical = copy.deepcopy(component2.get("electrical") or {})
    if not electrical:
        issues.append("datasheet electrical model is missing")
    try:
        courtyard = _courtyard(footprint)
    except BindingError as exc:
        issues.extend(exc.issues)
        courtyard = []
    try:
        model = _step_metadata(Path(step_path).expanduser())
    except BindingError as exc:
        issues.extend(exc.issues)
        model = {}

    if alignment_status not in ("verified", "unverified"):
        issues.append("alignment_status must be verified or unverified")
    transform = model_transform
    if transform is None:
        standoff = float((component2.get("package_3d") or {}).get("standoff_mm") or 0.0)
        transform = _identity_transform(standoff)
    if not isinstance(transform, list) or len(transform) != 16:
        issues.append("model_transform must contain 16 row-major values")
    else:
        try:
            transform = [round(_finite(value, "model_transform"), 9) for value in transform]
        except BindingError as exc:
            issues.extend(exc.issues)

    if issues:
        raise BindingError(issues)

    footprint["courtyard_pts"] = courtyard
    model["model_to_footprint"] = transform
    model["alignment_status"] = alignment_status
    model["claimed_mpn"] = model_mpn
    binding = {
        "schema": SCHEMA,
        "binding_id": f"component:{mpn}",
        "status": "complete" if alignment_status == "verified" else "incomplete",
        "component": copy.deepcopy(identity),
        "source_component": {"schema": component2["schema"],
                             "uri": source_uri, "sha256": _source_digest(component2)},
        "symbol": symbol,
        "pin_pad_map": pin_pad_map,
        "footprint": footprint,
        "model_3d": model,
        "electrical": electrical,
        "simulation_models": copy.deepcopy(simulation_models or {}),
        "datasheet_evidence": copy.deepcopy(component2.get("evidence") or {}),
        "assumptions": copy.deepcopy(component2.get("assumptions") or []),
        "orientation": copy.deepcopy(component2.get("orientation") or {}),
    }
    binding["binding_digest"] = binding_digest(binding)
    validate_binding(binding, require_complete=False, verify_asset=True)
    return binding


def validate_binding(binding: dict, *, require_complete: bool = True,
                     verify_asset: bool = True, library_root: Path | None = None) -> None:
    issues: list[str] = []
    if not isinstance(binding, dict) or binding.get("schema") != SCHEMA:
        raise BindingError("unsupported bound-component schema")
    if require_complete and binding.get("status") != "complete":
        issues.append("bound component is incomplete")
    if binding.get("binding_digest") != binding_digest(binding):
        issues.append("binding digest mismatch")
    pin_numbers = {_normalize_number(pin.get("number"))
                   for pin in (binding.get("symbol") or {}).get("pins", [])}
    pad_by_id = {pad.get("pad_id"): pad for pad in (binding.get("footprint") or {}).get("pads", [])}
    if require_complete:
        evidence = binding.get("datasheet_evidence") or {}
        package_pin_count = evidence.get("package_pin_count")
        electrical_pad_numbers = {
            _normalize_number(pad.get("number", pad.get("name")))
            for pad in pad_by_id.values() if not pad.get("mechanical", False)
        }
        if isinstance(package_pin_count, bool) or not isinstance(package_pin_count, int) \
                or package_pin_count <= 0:
            issues.append("complete binding requires datasheet package_pin_count evidence")
        elif package_pin_count != len(pin_numbers) \
                or package_pin_count != len(electrical_pad_numbers):
            issues.append(
                f"datasheet package_pin_count {package_pin_count} does not match "
                f"{len(pin_numbers)} symbol pins and {len(electrical_pad_numbers)} electrical pads")
        if not str(evidence.get("package_variant", "")).strip():
            issues.append("complete binding requires an exact datasheet package_variant")
        electrical = binding.get("electrical") or {}
        parameters = electrical.get("parameters") if isinstance(electrical, dict) else None
        placeholder = isinstance(parameters, list) and len(parameters) == 1 \
            and str(parameters[0].get("name", "")).strip().lower() == "datasheet_bound" \
            and set(electrical) <= {"kind", "parameters"}
        if placeholder:
            issues.append("complete binding cannot use datasheet_bound as its only electrical model")
    for mapping in binding.get("pin_pad_map", []):
        if _normalize_number(mapping.get("pin_number")) not in pin_numbers:
            issues.append("pin_pad_map references an unknown symbol pin")
        if not mapping.get("pad_ids"):
            issues.append(f"pin {mapping.get('pin_number')} has no mapped pad")
        for pad_id in mapping.get("pad_ids", []):
            if pad_id not in pad_by_id:
                issues.append(f"pin_pad_map references unknown {pad_id}")
            elif pad_by_id[pad_id].get("mechanical", False):
                issues.append(f"pin_pad_map references mechanical {pad_id}")
    model = binding.get("model_3d") or {}
    if model.get("format") != "step" or not re.fullmatch(r"[0-9a-f]{64}", model.get("sha256", "")):
        issues.append("model_3d is not a hashed STEP asset")
    component_mpn = str((binding.get("component") or {}).get("mpn", ""))
    claimed_mpn = str(model.get("claimed_mpn", ""))
    normalize_mpn = lambda value: re.sub(r"[^A-Z0-9]", "", value.upper())
    if not claimed_mpn or normalize_mpn(claimed_mpn) != normalize_mpn(component_mpn):
        issues.append("model_3d claimed_mpn does not match the bound component")
    if verify_asset and model.get("asset_uri"):
        path = Path(model["asset_uri"])
        if not path.is_absolute() and library_root is not None:
            path = library_root / path
        if not path.is_file():
            issues.append(f"bound STEP asset is missing: {path}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != model.get("sha256"):
            issues.append("bound STEP asset digest mismatch")
    simulation = binding.get("simulation_models") or {}
    behavioral = simulation.get("behavioral_power")
    if behavioral is not None:
        if behavioral.get("schema") != "design-studio.behavioral-power/1" \
                or behavioral.get("kind") not in ("linear_regulator", "dc_converter"):
            issues.append("behavioral_power schema/kind is invalid")
        pin_keys = {_normalize_number(pin.get("number")) for pin in
                    (binding.get("symbol") or {}).get("pins", [])}
        pin_keys |= {_normalize_number(pin.get("name")) for pin in
                     (binding.get("symbol") or {}).get("pins", [])}
        for key in ("input_pin", "output_pin"):
            if _normalize_number(behavioral.get(key)) not in pin_keys:
                issues.append(f"behavioral_power {key} does not match a symbol pin")
        for key in ("dropout_v", "max_output_current_a", "quiescent_current_a",
                    "ambient_c", "theta_ja_c_per_w", "max_junction_c"):
            if key not in behavioral or isinstance(behavioral.get(key), bool) \
                    or not isinstance(behavioral.get(key), (int, float)) \
                    or not math.isfinite(float(behavioral[key])):
                issues.append(f"behavioral_power {key} must be finite numeric")
        efficiency = behavioral.get("efficiency")
        if efficiency is not None and (isinstance(efficiency, bool)
                or not isinstance(efficiency, (int, float))
                or not 0 < float(efficiency) <= 1):
            issues.append("behavioral_power efficiency must be in (0, 1]")
    if issues:
        raise BindingError(issues)


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in value) or "part"


class BoundComponentStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or bound_component_library_dir()).expanduser()
        self.bindings = self.root / "bindings"
        self.models = self.root / "models"

    def save(self, binding: dict) -> Path:
        validate_binding(binding, require_complete=False, verify_asset=True)
        stored = copy.deepcopy(binding)
        source_model = Path(stored["model_3d"]["asset_uri"])
        self.bindings.mkdir(parents=True, exist_ok=True)
        self.models.mkdir(parents=True, exist_ok=True)
        model_name = f"{_safe(stored['component'].get('mpn', 'part'))}-{stored['model_3d']['sha256'][:12]}.step"
        target_model = self.models / model_name
        if not target_model.exists():
            temporary = target_model.with_suffix(".step.tmp")
            shutil.copy2(source_model, temporary)
            os.replace(temporary, target_model)
        stored["model_3d"]["asset_uri"] = f"models/{model_name}"
        stored["binding_digest"] = binding_digest(stored)
        validate_binding(stored, require_complete=False, verify_asset=True,
                         library_root=self.root)
        target = self.bindings / f"{_safe(stored['component'].get('mpn', 'part'))}.bound.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        os.replace(temporary, target)
        return target

    def get(self, mpn: str, require_complete: bool = True) -> dict | None:
        path = self.bindings / f"{_safe(mpn)}.bound.json"
        if not path.is_file():
            return None
        binding = json.loads(path.read_text(encoding="utf-8"))
        validate_binding(binding, require_complete=require_complete,
                         verify_asset=True, library_root=self.root)
        return binding

    def reference(self, binding: dict) -> dict:
        """Compact project instance reference with paths FreeCAD can resolve."""
        validate_binding(binding, require_complete=True, verify_asset=True,
                         library_root=self.root)
        mpn = binding["component"]["mpn"]
        record = self.bindings / f"{_safe(mpn)}.bound.json"
        model = copy.deepcopy(binding["model_3d"])
        model["asset_uri"] = str((self.root / model["asset_uri"]).resolve())
        return {
            "schema": "design-studio.bound-component-ref/1",
            "binding_id": binding["binding_id"],
            "binding_digest": binding["binding_digest"],
            "record_uri": str(record.resolve()),
            "model_3d": model,
        }
