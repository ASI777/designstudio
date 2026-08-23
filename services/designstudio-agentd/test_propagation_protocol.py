#!/usr/bin/env python3
"""Acceptance fixture for the coupled propagation control-plane operation."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - the release environment installs it
    Draft202012Validator = None


binary = Path(sys.argv[1]).resolve()
repository = Path(__file__).resolve().parents[2]
schema = json.loads((repository / "docs/schemas/propagation-run-v2.schema.json").read_text())
if Draft202012Validator is not None:
    Draft202012Validator.check_schema(schema)


def compact(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


with tempfile.TemporaryDirectory(prefix="designstudio-propagation-") as raw:
    root = Path(raw)
    for name in ("mechanical", "electronics", "configurations", "evidence"):
        (root / name).mkdir()
    (root / "mechanical" / "product.FCStd").write_bytes(b"propagation fixture")
    (root / "electronics" / "product.dsproj").write_text("{}")
    workspace_id = "64f7bfe7-d053-4c97-b8a7-c63d8995e31d"
    product_id = "a0650713-b330-4b8f-842e-74baaaa3c22c"
    configuration_id = "992bb75c-cfe8-4f5a-8bcd-91cdcebe3b44"
    display_id = "832ef354-78c8-43be-b00b-6e5a00731c61"
    bezel_id = "d8ea0c7b-49d5-46d0-a07e-e4a4f962b150"
    cable_id = "50d6606e-9aab-4cd4-9b7b-07e7e8e0d2df"
    graph = {
        "schema": "design-studio.product-graph/1", "product_id": product_id, "revision": 7,
        "nodes": [
            {"id": display_id, "name": "Display", "assembly_path": "product/ui/display",
             "domain": "mechanical", "authority": "product_graph", "source_ref": "DS1",
             "requirements": ["reach", "viewing angle"]},
            {"id": bezel_id, "name": "Display bezel", "assembly_path": "product/body/bezel",
             "domain": "mechanical", "authority": "product_graph", "source_ref": "BEZEL",
             "requirements": ["clearance", "service"]},
            {"id": cable_id, "name": "Display cable", "assembly_path": "product/electronics/flex",
             "domain": "electrical", "authority": "product_graph", "source_ref": "FLEX1",
             "requirements": ["cable bend radius", "connector clearance"]},
        ],
        "edges": [
            {"from": display_id, "to": bezel_id, "kind": "constrains"},
            {"from": bezel_id, "to": cable_id, "kind": "depends_on"},
        ],
        "slots": [{"id": "slot.display", "name": "Display", "node_id": display_id,
                    "assembly_path": "product/ui/display", "allowed_change_class": "C3",
                    "protected_properties": [], "customizable_properties": ["position"],
                    "required_evidence": []}],
        "variants": [{"id": "display.baseline", "slot_id": "slot.display",
                       "name": "Baseline", "change_class": "C3", "parameters": {},
                       "failed_gates": [], "incomplete_gates": []}],
    }
    (root / "product-graph.json").write_text(json.dumps(graph))
    configuration = {
        "schema": "design-studio.configuration/1", "configuration_id": configuration_id,
        "revision": 1, "parent_configuration_id": None,
        "baseline_configuration_id": configuration_id, "workspace_revision": 1,
        "graph_revision": 7, "state": "sandbox", "equipped": {"slot.display": "display.baseline"},
        "parameter_overrides": {}, "requirement_profile": "default",
        "created_utc": "2026-07-14T00:00:00.000Z", "digest": "",
    }
    configuration["digest"] = hashlib.sha256(compact(configuration)).hexdigest()
    (root / "configurations" / "baseline.json").write_text(json.dumps(configuration))
    manifest = {
        "schema": "design-studio.workspace/1", "workspace_id": workspace_id, "revision": 1,
        "product": {"name": "Propagation fixture", "description": ""},
        "documents": {"mechanical": "mechanical/product.FCStd", "electronics": "electronics/product.dsproj"},
        "product_graph": "product-graph.json", "baseline_configuration": "configurations/baseline.json",
        "contracts": [], "evidence_directories": ["evidence"],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    process = subprocess.Popen([str(binary), "--stdio"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)

    def request(identifier: int, method: str, params: dict, permissions: list[str]) -> dict:
        assert process.stdin and process.stdout
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier,
                                        "method": method, "params": params,
                                        "auth": {"permissions": permissions}}) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())

    opened = request(1, "project/open", {"manifest_path": str(manifest_path)}, ["project:open"])
    assert "error" not in opened, opened
    result = request(2, "propagation/run", {
        "configuration_id": configuration_id,
        "changed_node_ids": [display_id],
        "edit_reason": "display moved closer to the user",
        "engine_versions": {"thermal": "fixture/1"},
        "inputs": {
            "thermal_hot_surface": {"value": 80.0, "requirement": 100.0,
                                     "unit": "degC", "higher_is_better": False,
                                     "uncertainty": 2.0},
            "scores": {"reach": {"before": 0.4, "after": 0.8, "unit": "score"}},
        },
    }, ["analysis:execute"])
    assert "error" not in result, result
    receipt = result["result"]
    if Draft202012Validator is not None:
        Draft202012Validator(schema).validate(receipt)
    assert receipt["schema"] == "design-studio.propagation-run/2"
    assert bezel_id in receipt["affected_node_ids"] and cable_id in receipt["affected_node_ids"]
    assert receipt["causal_paths"][cable_id] == [display_id, bezel_id, cable_id]
    assert receipt["score_changes"]["reach"]["delta"] == 0.4
    by_gate = {item["gate"]: item for item in receipt["evidence"]}
    assert by_gate["thermal_hot_surface"]["status"] == "pass"
    assert by_gate["reach_grip_viewing"]["status"] == "incomplete"
    assert receipt["solver_summary"]["incomplete"] >= 1
    assert receipt["status"] == "incomplete"
    bad = request(3, "propagation/run", {
        "configuration_id": configuration_id,
        "changed_node_ids": ["00000000-0000-0000-0000-000000000000"],
    }, ["analysis:execute"])
    assert bad["error"]["code"] == -32602
    process.stdin.close()
    assert process.wait(timeout=5) == 0

print("DESIGNSTUDIO_PROPAGATION_PROTOCOL_OK")
