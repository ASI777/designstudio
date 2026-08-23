#!/usr/bin/env python3
"""Cross-language restart/revision smoke for the real agentd executable."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


binary = Path(sys.argv[1]).resolve()
repository = Path(__file__).resolve().parents[2]


def compact(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


with tempfile.TemporaryDirectory(prefix="designstudio-agentd-stdio-") as raw:
    root = Path(raw)
    for name in ("mechanical", "electronics", "configurations", "evidence"):
        (root / name).mkdir()
    (root / "mechanical" / "product.FCStd").write_bytes(b"desktop-owned fixture")
    (root / "electronics" / "product.dsproj").write_text("{}")
    workspace_id = "64f7bfe7-d053-4c97-b8a7-c63d8995e31d"
    product_id = "a0650713-b330-4b8f-842e-74baaaa3c22c"
    node_id = "832ef354-78c8-43be-b00b-6e5a00731c61"
    configuration_id = "992bb75c-cfe8-4f5a-8bcd-91cdcebe3b44"
    graph = {
        "schema": "design-studio.product-graph/1", "product_id": product_id, "revision": 1,
        "nodes": [{"id": node_id, "name": "Battery", "assembly_path": "product/power/battery",
                   "domain": "electrical", "authority": "product_graph", "source_ref": "BAT1",
                   "requirements": []}],
        "edges": [],
        "slots": [{"id": "slot.battery", "name": "Battery", "node_id": node_id,
                   "assembly_path": "product/power/battery", "allowed_change_class": "C3",
                   "protected_properties": [], "customizable_properties": ["capacity"],
                   "required_evidence": []}],
        "variants": [{"id": "battery.baseline", "slot_id": "slot.battery",
                      "name": "Baseline", "change_class": "C3", "parameters": {},
                      "failed_gates": [], "incomplete_gates": []}],
    }
    (root / "product-graph.json").write_text(json.dumps(graph))
    configuration = {
        "schema": "design-studio.configuration/1",
        "configuration_id": configuration_id,
        "revision": 1,
        "parent_configuration_id": None,
        "baseline_configuration_id": configuration_id,
        "workspace_revision": 1,
        "graph_revision": 1,
        "state": "sandbox",
        "equipped": {"slot.battery": "battery.baseline"},
        "parameter_overrides": {},
        "requirement_profile": "default",
        "created_utc": "2026-07-14T00:00:00.000Z",
        "digest": "",
    }
    configuration["digest"] = hashlib.sha256(compact(configuration)).hexdigest()
    (root / "configurations" / "baseline.json").write_text(json.dumps(configuration))
    manifest = {
        "schema": "design-studio.workspace/1", "workspace_id": workspace_id, "revision": 1,
        "product": {"name": "Protocol fixture", "description": ""},
        "documents": {"mechanical": "mechanical/product.FCStd",
                      "electronics": "electronics/product.dsproj"},
        "product_graph": "product-graph.json",
        "baseline_configuration": "configurations/baseline.json",
        "contracts": [], "evidence_directories": ["evidence"],
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    def session(expected_revision: int) -> None:
        process = subprocess.Popen([binary, "--stdio"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)

        def request(identifier: int, method: str, params: dict, permissions: list[str]) -> dict:
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier,
                                            "method": method, "params": params,
                                            "auth": {"permissions": permissions}}) + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline())

        denied = request(1, "product/read", {}, [])
        assert denied["error"]["code"] == -32003
        opened = request(2, "project/open", {"manifest_path": str(manifest_path)}, ["project:open"])
        result = opened["result"]
        assert result["api"] == "design-studio.agentd/1"
        assert result["workspace_id"] == workspace_id and result["workspace_revision"] == 1
        assert result["graph_revision"] == 1 and result["daemon_revision"] == expected_revision
        product = request(3, "product/read", {}, ["product:read"])["result"]
        assert product["baseline_configuration"]["configuration_id"] == configuration_id
        source_project = repository / "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/agent-workflow-controller.dsproj"
        source_report = repository / "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/acceptance-report.json"
        intent = {
            "schema": "design-studio.product-intent/1",
            "product_id": "desktop-ai-control-console",
            "application_family": "desktop_ai_control_console",
            "name": "USB-C plus BLE desktop AI control console",
            "source_product": {
                "product": "acceptance/agent-workflow-controller",
                "source_root": str(repository.resolve()),
                "project_path": "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/agent-workflow-controller.dsproj",
                "project_digest": hashlib.sha256(source_project.read_bytes()).hexdigest(),
                "acceptance_report_path": "acceptance/agent-workflow-controller/checkpoints/pre-v72-authoritative/acceptance-report.json",
                "acceptance_report_digest": hashlib.sha256(source_report.read_bytes()).hexdigest(),
            },
            "requirements": {
                "usb_c_connector_mpn": "USB4085-GF-A",
                "ble_mcu_module_mpn": "ESP32-S3-WROOM-1-N8R8",
                "host_transport": "usb_c_usb_2",
                "wireless_transport": "ble_5",
                "local_backend": True,
                "gpu_allowed": False,
                "architecture_approval_required": True,
            },
            "budget_units": 72,
        }
        compiled = request(4, "product/compile", {"intent": intent}, ["workflow:compile"])
        assert "error" not in compiled, compiled
        assert compiled["result"]["workflow"]["status"] == "awaiting_approval"
        assert compiled["result"]["work_package_graph"]["packages"][0]["engine_id"] == "design-studio.component-workflow/1"
        process.stdin.close()
        assert process.wait(timeout=5) == 0

    session(1)
    # The first session persists both project/open and product/compile, so the
    # next project/open is sequence three rather than sequence two.
    session(3)
    events = (root / ".designstudio" / "events.jsonl").read_text().splitlines()
    assert len(events) == 4
    assert [json.loads(line)["event"] for line in events] == [
        "project/open", "product/compile", "project/open", "product/compile"
    ]

print("DESIGNSTUDIO_AGENTD_STDIO_RESTART_OK")
