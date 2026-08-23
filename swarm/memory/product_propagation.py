"""Affected-subgraph engineering propagation with auditable evidence."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Callable
from uuid import UUID, uuid4


Status = str
Solver = Callable[[dict], dict]


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class AnalysisRule:
    analysis_id: str
    gate: str
    domain: str
    dependencies: frozenset[str]
    input_keys: frozenset[str]
    method: str
    solver: Solver | None
    requirement: str | None = None


class ProductPropagation:
    """Recompute only rules reached from changed semantic nodes."""

    def __init__(self, graph: dict, rules: list[AnalysisRule]):
        self.graph = graph
        self.rules = rules
        self._nodes = {str(node["id"]): node for node in graph.get("nodes", [])}
        self._outgoing = {node: [] for node in self._nodes}
        for edge in graph.get("edges", []):
            source, target = str(edge["from"]), str(edge["to"])
            if source not in self._nodes or target not in self._nodes:
                raise ValueError("product graph edge references a missing node")
            self._outgoing[source].append(target)
        if len({rule.analysis_id for rule in rules}) != len(rules):
            raise ValueError("analysis IDs must be unique")
        for rule in rules:
            missing = rule.dependencies - self._nodes.keys()
            if missing:
                raise ValueError(f"analysis {rule.analysis_id} has missing dependencies {missing}")
        self._cache: dict[str, tuple[str, dict]] = {}

    def affected(self, changed_nodes: set[str]) -> tuple[set[str], dict[str, list[str]]]:
        unknown = changed_nodes - self._nodes.keys()
        if unknown:
            raise ValueError(f"changed nodes do not exist: {sorted(unknown)}")
        reached = set(changed_nodes)
        paths = {node: [node] for node in changed_nodes}
        queue = deque(sorted(changed_nodes))
        while queue:
            source = queue.popleft()
            for target in sorted(self._outgoing[source]):
                if target in reached:
                    continue
                reached.add(target)
                paths[target] = paths[source] + [target]
                queue.append(target)
        return reached, paths

    def run(self, *, configuration_id: str, changed_nodes: set[str],
            values: dict, engine_versions: dict[str, str] | None = None) -> dict:
        UUID(configuration_id)
        affected, paths = self.affected(changed_nodes)
        run_id = str(uuid4())
        evidence = []
        executed, reused, unaffected = [], [], []
        for rule in self.rules:
            if not (rule.dependencies & affected):
                unaffected.append(rule.analysis_id)
                continue
            inputs = {key: values.get(key) for key in sorted(rule.input_keys)}
            input_record = {
                "analysis_id": rule.analysis_id,
                "configuration_id": configuration_id,
                "inputs": inputs,
                "engine_version": (engine_versions or {}).get(rule.domain, "unavailable"),
            }
            input_digest = _digest(input_record)
            cached = self._cache.get(rule.analysis_id)
            if cached and cached[0] == input_digest:
                record = dict(cached[1])
                record["reused_from"] = record["source_run"]
                reused.append(rule.analysis_id)
            else:
                record = self._execute(rule, configuration_id, run_id,
                                       input_digest, inputs, paths)
                self._cache[rule.analysis_id] = (input_digest, dict(record))
                executed.append(rule.analysis_id)
            evidence.append(record)
        statuses = {record["status"] for record in evidence}
        status = ("fail" if "fail" in statuses else
                  "incomplete" if "incomplete" in statuses else
                  "pass" if evidence else "not_applicable")
        return {
            "schema": "design-studio.propagation-run/1",
            "run_id": run_id,
            "configuration_id": configuration_id,
            "graph_revision": self.graph.get("revision"),
            "changed_nodes": sorted(changed_nodes),
            "affected_nodes": sorted(affected),
            "causal_paths": paths,
            "executed_analyses": executed,
            "reused_analyses": reused,
            "unaffected_analyses": unaffected,
            "status": status,
            "evidence": evidence,
            "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    @staticmethod
    def _execute(rule, configuration_id, run_id, input_digest, inputs, paths):
        dependencies = sorted(rule.dependencies)
        base = {
            "schema": "design-studio.evidence/1",
            "evidence_id": str(uuid4()),
            "configuration_id": configuration_id,
            "gate": rule.gate,
            "method": rule.method,
            "source_run": f"propagation:{run_id}:{rule.analysis_id}",
            "input_digest": input_digest,
            "requirement": rule.requirement,
            "assumptions": [],
            "dependencies": dependencies,
            "causal_paths": {node: paths[node] for node in dependencies if node in paths},
            "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        missing = [key for key, value in inputs.items() if value is None]
        if rule.solver is None:
            return {**base, "status": "incomplete", "value": None, "unit": None,
                    "margin": None, "uncertainty": "solver adapter unavailable",
                    "assumptions": ["no result was fabricated"]}
        if missing:
            return {**base, "status": "incomplete", "value": None, "unit": None,
                    "margin": None, "uncertainty": f"missing inputs: {', '.join(missing)}",
                    "assumptions": ["analysis deferred until required inputs exist"]}
        try:
            result = rule.solver(inputs)
        except Exception as error:
            return {**base, "status": "incomplete", "value": None, "unit": None,
                    "margin": None, "uncertainty": f"solver failed: {error}",
                    "assumptions": ["solver failure is not a pass"]}
        if result.get("status") not in ("pass", "fail", "incomplete", "not_applicable"):
            raise ValueError(f"solver {rule.analysis_id} returned an invalid status")
        return {**base, **result,
                "assumptions": list(result.get("assumptions", []))}


def margin_solver(*, value_key: str, requirement_key: str, unit: str,
                  higher_is_better: bool = True, uncertainty_key: str | None = None) -> Solver:
    def solve(inputs):
        value, requirement = float(inputs[value_key]), float(inputs[requirement_key])
        if not math.isfinite(value) or not math.isfinite(requirement):
            raise ValueError("non-finite engineering input")
        margin = value - requirement if higher_is_better else requirement - value
        uncertainty = float(inputs.get(uncertainty_key, 0.0)) if uncertainty_key else 0.0
        conservative = margin - abs(uncertainty)
        return {"status": "pass" if conservative >= 0 else "fail",
                "value": value, "unit": unit, "margin": margin,
                "uncertainty": uncertainty,
                "assumptions": ["gate uses worst-case margin after uncertainty"]}
    return solve


def runtime_solver(inputs):
    energy = float(inputs["battery_energy_wh"])
    load = float(inputs["average_load_w"])
    reserve = float(inputs["reserve_fraction"])
    required = float(inputs["required_runtime_h"])
    if load <= 0 or not 0 <= reserve < 1:
        raise ValueError("load must be positive and reserve in [0, 1)")
    value = energy * (1 - reserve) / load
    return {"status": "pass" if value >= required else "fail", "value": value,
            "unit": "h", "margin": value - required,
            "uncertainty": float(inputs.get("runtime_uncertainty_h", 0.0)),
            "assumptions": ["average duty-cycle load", "reserved battery capacity excluded"]}


def thermal_solver(inputs):
    junction = float(inputs["ambient_c"]) + float(inputs["loss_w"]) * float(inputs["theta_c_per_w"])
    maximum = float(inputs["maximum_junction_c"])
    return {"status": "pass" if junction <= maximum else "fail", "value": junction,
            "unit": "degC", "margin": maximum - junction,
            "uncertainty": float(inputs.get("thermal_uncertainty_c", 0.0)),
            "assumptions": ["steady-state lumped thermal resistance"]}
