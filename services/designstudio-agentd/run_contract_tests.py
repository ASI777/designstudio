#!/usr/bin/env python3
"""Dependency-light schema and source-contract checks for designstudio-agentd."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "docs" / "schemas"
UUIDS = {
    "workspace": "64f7bfe7-d053-4c97-b8a7-c63d8995e31d",
    "product": "a0650713-b330-4b8f-842e-74baaaa3c22c",
    "node": "832ef354-78c8-43be-b00b-6e5a00731c61",
    "configuration": "992bb75c-cfe8-4f5a-8bcd-91cdcebe3b44",
    "evidence": "9f29e1d9-b7a7-4caf-85ff-0c39ea5af566",
    "job": "384807db-f412-43db-a99e-4cdd0ea431b7",
}
CHECKS = 0


def load(name: str) -> dict:
    global CHECKS
    value = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    CHECKS += 1
    return value


def validate(schema: dict, instance: dict) -> None:
    global CHECKS
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(instance)
    CHECKS += 1


def rejects(schema: dict, instance: dict) -> None:
    global CHECKS
    errors = list(Draft202012Validator(
        schema, format_checker=FormatChecker()).iter_errors(instance))
    assert errors, f"schema unexpectedly accepted {instance}"
    CHECKS += 1


# Every schema shipped by the desktop installer must at least be a valid Draft
# 2020-12 schema, including contracts owned by the C++ and cloud subsystems.
for schema_path in sorted(SCHEMAS.glob("*.schema.json")):
    load(schema_path.name)

workspace_schema = load("workspace-v1.schema.json")
graph_schema = load("product-graph-v1.schema.json")
configuration_schema = load("configuration-v1.schema.json")
evidence_schema = load("evidence-record-v1.schema.json")
generation_schema = load("generation-job-v1.schema.json")
intent_schema = load("product-intent-v1.schema.json")
work_graph_schema = load("work-package-graph-v1.schema.json")
workflow_schema = load("workflow-run-v1.schema.json")
surface_schema = load("interaction-surface-map-v1.schema.json")

workspace = {
    "schema": "design-studio.workspace/1",
    "workspace_id": UUIDS["workspace"],
    "revision": 1,
    "product": {"name": "Self-balancing sphere", "description": "Vertical-slice fixture"},
    "documents": {
        "mechanical": "mechanical/product.FCStd",
        "electronics": "electronics/product.dsproj",
    },
    "product_graph": "contracts/product-graph.json",
    "baseline_configuration": "configurations/baseline.json",
    "contracts": ["contracts/mechanical-contract.json"],
    "evidence_directories": ["evidence"],
}
validate(workspace_schema, workspace)
for unsafe in ("/etc/passwd", "../outside.json", "contracts/../../outside.json", "C:\\secret"):
    broken = copy.deepcopy(workspace)
    broken["product_graph"] = unsafe
    rejects(workspace_schema, broken)

graph = {
    "schema": "design-studio.product-graph/1",
    "product_id": UUIDS["product"],
    "revision": 1,
    "nodes": [{
        "id": UUIDS["node"], "name": "Battery", "assembly_path": "product/power/battery",
        "domain": "electrical", "authority": "product_graph", "source_ref": "BAT1",
        "requirements": ["PWR-01"],
    }],
    "edges": [],
    "slots": [{
        "id": "slot.battery", "name": "Battery", "node_id": UUIDS["node"],
        "assembly_path": "product/power/battery", "allowed_change_class": "C3",
        "protected_properties": ["connector"], "customizable_properties": ["capacity"],
        "required_evidence": ["runtime", "clearance"],
    }],
    "variants": [{
        "id": "battery.3s_baseline", "slot_id": "slot.battery", "name": "3S baseline",
        "change_class": "C3", "parameters": {"capacity_ah": 2.5},
        "failed_gates": [], "incomplete_gates": [],
    }],
}
validate(graph_schema, graph)
broken = copy.deepcopy(graph)
broken["nodes"][0]["authority"] = "mesh_generator"
rejects(graph_schema, broken)

configuration = {
    "schema": "design-studio.configuration/1",
    "configuration_id": UUIDS["configuration"],
    "revision": 1,
    "parent_configuration_id": None,
    "baseline_configuration_id": UUIDS["configuration"],
    "workspace_revision": 1,
    "graph_revision": 1,
    "state": "sandbox",
    "equipped": {"slot.battery": "battery.3s_baseline"},
    "parameter_overrides": {},
    "requirement_profile": "demonstrator",
    "created_utc": "2026-07-14T00:00:00Z",
    "digest": "0" * 64,
}
validate(configuration_schema, configuration)
broken = copy.deepcopy(configuration)
broken["state"] = "preview_that_mutated_baseline"
rejects(configuration_schema, broken)

evidence = {
    "schema": "design-studio.evidence/1",
    "evidence_id": UUIDS["evidence"],
    "configuration_id": UUIDS["configuration"],
    "gate": "runtime",
    "status": "incomplete",
    "method": "duty_cycle_energy_model",
    "source_run": "analysis:run-1",
    "input_digest": hashlib.sha256(b"inputs").hexdigest(),
    "value": 2.1,
    "unit": "hour",
    "requirement": "PWR-01",
    "margin": -5.9,
    "uncertainty": "battery model unverified",
    "assumptions": ["nominal cell capacity"],
    "dependencies": ["slot.battery"],
    "created_utc": "2026-07-14T00:01:00Z",
}
validate(evidence_schema, evidence)
broken = copy.deepcopy(evidence)
del broken["status"]
rejects(evidence_schema, broken)

generation = {
    "schema": "design-studio.generation-job/1",
    "job_id": UUIDS["job"],
    "configuration_id": UUIDS["configuration"],
    "slot_id": "slot.upper_shell",
    "provider": "hunyuan3d-omni-amd",
    "status": "queued",
    "input_digest": hashlib.sha256(b"generation package").hexdigest(),
    "candidate_count": 3,
    "seeds": [101, 102, 103],
    "created_utc": "2026-07-14T00:02:00Z",
    "artifact_manifest_url": None,
}
validate(generation_schema, generation)
broken = copy.deepcopy(generation)
broken["candidate_count"] = 17
rejects(generation_schema, broken)

intent = {
    "schema": "design-studio.product-intent/1",
    "product_id": "desktop-ai-control-console",
    "application_family": "desktop_ai_control_console",
    "name": "USB-C plus BLE desktop AI control console",
    "source_product": {
        "product": "acceptance/agent-workflow-controller",
        "source_root": str(ROOT.resolve()),
        "project_path": "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/agent-workflow-controller.dsproj",
        "project_digest": hashlib.sha256((ROOT / "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/agent-workflow-controller.dsproj").read_bytes()).hexdigest(),
        "acceptance_report_path": "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/acceptance-report.json",
        "acceptance_report_digest": hashlib.sha256((ROOT / "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/acceptance-report.json").read_bytes()).hexdigest(),
    },
    "requirements": {
        "usb_c_connector_mpn": "USB4085-GF-A",
        "ble_mcu_module_mpn": "ESP32-S3-WROOM-1-N8R8",
        "host_transport": "usb_c_usb_2",
        "wireless_transport": "ble_5",
        "local_backend": True,
        "gpu_allowed": False, "architecture_approval_required": True,
    },
    "budget_units": 72,
}
validate(intent_schema, intent)
broken = copy.deepcopy(intent)
broken["requirements"]["gpu_allowed"] = True
rejects(intent_schema, broken)
broken = copy.deepcopy(intent)
broken["unexpected"] = "strict"
rejects(intent_schema, broken)

engine_ids = [
    "design-studio.component-workflow/1",
    "design-studio.incremental-analysis/1",
    "designcore-native",
    "freecad",
    "design-studio.integration-sandbox/1",
    "design-studio.verification/1",
]
package_ids = [
    "component-evidence-cad", "schematic", "pcb", "freecad", "integration", "verification",
]
dependencies = [[], ["component-evidence-cad"], ["schematic"],
                ["component-evidence-cad", "pcb"], ["schematic", "pcb", "freecad"],
                ["integration"]]
work_graph = {
    "schema": "design-studio.work-package-graph/1",
    "graph_id": "wpg-" + "1" * 16,
    "product_id": intent["product_id"],
    "product_digest": "2" * 64,
    "runnable": False,
    "approval_gate": "architecture",
    "packages": [{
        "id": package_id, "name": package_id, "engine_id": engine_id,
        "depends_on": depends_on,
        "input_refs": [f"acceptance/agent-workflow-controller/generated/{package_id}.evidence"],
        "budget_units": cost,
    } for package_id, engine_id, depends_on, cost in zip(
        package_ids, engine_ids, dependencies, [10, 12, 18, 14, 8, 10])],
    "graph_digest": "3" * 64,
}
validate(work_graph_schema, work_graph)
broken = copy.deepcopy(work_graph)
broken["packages"][0]["engine_id"] = "duplicated.new.engine"
rejects(work_graph_schema, broken)
broken = copy.deepcopy(work_graph)
broken["runnable"] = True
rejects(work_graph_schema, broken)
broken = copy.deepcopy(work_graph)
broken["packages"][1] = copy.deepcopy(broken["packages"][0])
rejects(work_graph_schema, broken)
broken = copy.deepcopy(work_graph)
broken["packages"][0]["depends_on"] = ["verification"]
rejects(work_graph_schema, broken)
broken = copy.deepcopy(work_graph)
broken["packages"][2]["engine_id"] = "freecad"
rejects(work_graph_schema, broken)

workflow = {
    "schema": "design-studio.workflow-run/1",
    "workflow_id": "wfr-" + "3" * 16,
    "product_digest": "2" * 64,
    "graph_digest": "3" * 64,
    "status": "awaiting_approval",
    "approval": None,
    "checkpoints": [{
        "package_id": package_id, "status": "blocked", "input_digest": None,
        "output_digest": None, "result_ref": None, "consumed_units": 0,
    } for package_id in package_ids],
    "budget": {
        "allocated_units": 72, "consumed_units": 0, "remaining_units": 72,
        "rejected_packages": [],
    },
    "created_utc": "2026-07-26T00:00:00Z",
    "updated_utc": "2026-07-26T00:00:00Z",
}
validate(workflow_schema, workflow)
broken = copy.deepcopy(workflow)
broken["checkpoints"][0]["status"] = "executing_shell"
rejects(workflow_schema, broken)
broken = copy.deepcopy(workflow)
broken["status"] = "succeeded"
rejects(workflow_schema, broken)

surface = {
    "schema": "design-studio.interaction-surface-map/1",
    "application_family": "desktop_ai_control_console",
    "backend": "local_non_gpu",
    "surfaces": [
        {"id": "intent", "rpc_methods": ["product/compile"], "capabilities": ["compile"]},
        {"id": "architecture_approval", "rpc_methods": ["workflow/approve"], "capabilities": ["approve"]},
        {"id": "workflow_control", "rpc_methods": ["workflow/start", "workflow/cancel", "workflow/resume"],
         "capabilities": ["control"]},
        {"id": "workflow_observation", "rpc_methods": ["workflow/read"], "capabilities": ["read"]},
    ],
    "map_digest": "4" * 64,
}
validate(surface_schema, surface)
broken = copy.deepcopy(surface)
broken["backend"] = "remote_gpu"
rejects(surface_schema, broken)
broken = copy.deepcopy(surface)
broken["surfaces"][2]["rpc_methods"] = ["workflow/start"]
rejects(surface_schema, broken)
broken = copy.deepcopy(surface)
broken["surfaces"][3] = copy.deepcopy(broken["surfaces"][0])
rejects(surface_schema, broken)

# Keep the portable check meaningful when Rust is not installed: verify that the
# source registers every minimum API operation and never exposes a generic exec.
source = (Path(__file__).parent / "src" / "lib.rs").read_text(encoding="utf-8")
required_methods = {
    "project/create", "project/open", "product/read", "selection/update", "slot/read",
    "variant/generate", "variant/preview", "variant/equip",
    "configuration/save", "configuration/commit", "analysis/start", "propagation/run",
    "analysis/cancel", "evidence/read", "generation/submit", "release/evaluate",
    "product/context", "branch/create", "branch/read", "branch/update",
    "integration/create", "integration/evaluate", "integration/approve",
    "agent/start", "agent/progress", "agent/cancel", "agent/recover",
    "product/compile", "workflow/approve", "workflow/start", "workflow/read",
    "workflow/cancel", "workflow/resume",
}
registered = set(re.findall(r'"([a-z]+/[a-z]+)"\s*=>', source))
assert required_methods <= registered, required_methods - registered
assert "Command::new" not in source and "std::process" not in source

print(f"designstudio-agentd schema/source contract tests passed ({CHECKS} schema checks)")
