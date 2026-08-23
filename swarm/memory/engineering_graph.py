"""Deterministic executor for DesignStudio engineering workflow graphs.

Models may propose, compare and critique inside explicitly AI-capable nodes.
State transitions, validation findings and publication receipts remain owned by
software or verified solver/test workers.  Conditions are named predicates;
arbitrary expressions are never evaluated.
"""
from __future__ import annotations

import copy
import re
from typing import Any


RUN_SCHEMA = "design-studio.engineering-workflow-run/1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AI_FORBIDDEN_FIELDS = {
    "product_id", "revision", "requirements_sha256", "evidence_digests",
    "rights_constraints", "changed_domains", "deterministic_failures",
    "qualification_findings", "bundle_digest_verified", "iteration",
}
_OWNER_ACTORS = {
    "deterministic_software": {"software"},
    "ai_proposal_plus_domain_code": {"software", "ai"},
    "ai_critique_plus_engineering_rules": {"software", "ai"},
    "affected_domain_code": {"software", "ai"},
    "verified_solver_and_test_workers": {"solver_or_test_worker"},
}
_DOMAIN_ALIASES = {
    "cad": "mechanical",
    "structural": "mechanical",
    "kinematics": "mechanical",
    "pcb": "electronics",
    "firmware": "electronics",
    "controls": "electronics",
    "emc": "electronics",
    "procurement": "manufacturing",
    "manufacturing_test": "manufacturing",
}


class EngineeringGraphError(ValueError):
    def __init__(self, issues: list[str] | str):
        self.issues = issues if isinstance(issues, list) else [issues]
        super().__init__("; ".join(self.issues))


def validate_graph(graph: Any) -> dict[str, Any]:
    if not isinstance(graph, dict) \
            or graph.get("schema") != "design-studio.engineering-workflow-graph/1":
        raise EngineeringGraphError("engineering workflow graph schema is invalid")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise EngineeringGraphError("engineering workflow graph has no nodes")
    if not isinstance(edges, list):
        raise EngineeringGraphError("engineering workflow graph edges must be an array")
    identifiers = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(identifiers) != len(nodes) or any(
            not isinstance(identifier, str) or not identifier for identifier in identifiers):
        raise EngineeringGraphError("every engineering workflow node needs an id")
    if len(set(identifiers)) != len(identifiers):
        raise EngineeringGraphError("engineering workflow node ids must be unique")
    node_ids = set(identifiers)
    if "evidence_intake" not in node_ids or "finish" not in node_ids:
        raise EngineeringGraphError("workflow must contain evidence_intake and finish nodes")
    adjacency: dict[str, set[str]] = {identifier: set() for identifier in identifiers}
    state_fields = set().union(*(set(value) for value in graph.get("state", {}).values()
                                 if isinstance(value, list)))
    for node in nodes:
        if node.get("owner") not in _OWNER_ACTORS:
            raise EngineeringGraphError(
                f"workflow node {node['id']} has unsupported owner {node.get('owner')!r}")
        writes = node.get("writes")
        if not isinstance(writes, list) or any(
                not isinstance(field, str) or field not in state_fields for field in writes):
            raise EngineeringGraphError(
                f"workflow node {node['id']} writes undeclared state fields")
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict) or set(edge) != {"from", "to", "when"}:
            raise EngineeringGraphError(f"workflow edge {index} is malformed")
        source = edge["from"]
        destination = edge["to"]
        if source not in node_ids or destination not in node_ids:
            raise EngineeringGraphError(f"workflow edge {index} references an unknown node")
        if edge["when"] not in _CONDITIONS:
            raise EngineeringGraphError(
                f"workflow edge {index} uses unsupported condition {edge['when']!r}")
        adjacency[source].add(destination)
    reachable = {"evidence_intake"}
    pending = ["evidence_intake"]
    while pending:
        source = pending.pop()
        for destination in adjacency[source]:
            if destination not in reachable:
                reachable.add(destination)
                pending.append(destination)
    missing = node_ids - reachable
    if missing:
        raise EngineeringGraphError(f"workflow has unreachable nodes {sorted(missing)}")
    return graph


def new_run(graph: dict[str, Any], *, product_id: str, revision: int,
            evidence_digests: list[str], rights_constraints: list[str],
            observations: list[str]) -> dict[str, Any]:
    validate_graph(graph)
    if not isinstance(product_id, str) or not product_id:
        raise EngineeringGraphError("product_id is required")
    if type(revision) is not int or revision < 1:
        raise EngineeringGraphError("revision must be a positive integer")
    if not evidence_digests or any(not _SHA256.fullmatch(item) for item in evidence_digests):
        raise EngineeringGraphError("evidence_digests must contain SHA-256 values")
    if not isinstance(rights_constraints, list) or not isinstance(observations, list):
        raise EngineeringGraphError("rights_constraints and observations must be arrays")
    return {
        "schema": RUN_SCHEMA,
        "workflow_id": graph["workflow_id"],
        "status": "active",
        "active_nodes": ["evidence_intake"],
        "completed_nodes": [],
        "state": {
            "product_id": product_id,
            "revision": revision,
            "requirements_sha256": "",
            "evidence_digests": list(evidence_digests),
            "rights_constraints": list(rights_constraints),
            "observations": list(observations),
            "changed_domains": [],
            "domain_artifact_digests": {},
            "interface_findings": [],
            "deterministic_failures": [],
            "qualification_findings": [],
            "bundle_digest_verified": False,
            "iteration": 0,
            "inference_usage_records": [],
        },
        "history": [],
    }


def _condition_evidence_valid(state: dict[str, Any]) -> bool:
    return bool(state["evidence_digests"]) and all(
        _SHA256.fullmatch(item) for item in state["evidence_digests"])


def _condition_requirements_valid(state: dict[str, Any]) -> bool:
    return bool(_SHA256.fullmatch(state.get("requirements_sha256", ""))) \
        and bool(state.get("changed_domains"))


def _condition_domain_candidates_written(state: dict[str, Any]) -> bool:
    digests = state.get("domain_artifact_digests", {})
    return bool(state.get("changed_domains")) and all(
        domain in digests and _SHA256.fullmatch(str(digests[domain]))
        for domain in state["changed_domains"])


def _condition_no_deterministic_failures(state: dict[str, Any]) -> bool:
    return state.get("deterministic_failures") == []


def _condition_deterministic_failure_present(state: dict[str, Any]) -> bool:
    return bool(state.get("deterministic_failures"))


def _condition_no_interface_findings(state: dict[str, Any]) -> bool:
    return state.get("interface_findings") == []


def _condition_interface_finding_present(state: dict[str, Any]) -> bool:
    return bool(state.get("interface_findings"))


def _condition_repair_scope_written(state: dict[str, Any]) -> bool:
    return bool(state.get("changed_domains")) and state.get("iteration", 0) > 0


def _condition_qualification_finding_present(state: dict[str, Any]) -> bool:
    findings = state.get("qualification_findings", [])
    return bool(findings) and any(item.get("status") != "pass" for item in findings)


def _condition_required_qualification_satisfied(state: dict[str, Any]) -> bool:
    findings = state.get("qualification_findings", [])
    return bool(findings) and all(item.get("status") == "pass" for item in findings)


def _condition_bundle_digest_verified(state: dict[str, Any]) -> bool:
    return state.get("bundle_digest_verified") is True \
        and _condition_no_deterministic_failures(state) \
        and _condition_no_interface_findings(state) \
        and _condition_required_qualification_satisfied(state)


_CONDITIONS = {
    "evidence_valid": _condition_evidence_valid,
    "requirements_valid": _condition_requirements_valid,
    "domain_candidates_written": _condition_domain_candidates_written,
    "no_deterministic_failures": _condition_no_deterministic_failures,
    "deterministic_failure_present": _condition_deterministic_failure_present,
    "no_interface_findings": _condition_no_interface_findings,
    "interface_finding_present": _condition_interface_finding_present,
    "repair_scope_written": _condition_repair_scope_written,
    "qualification_finding_present": _condition_qualification_finding_present,
    "required_qualification_satisfied": _condition_required_qualification_satisfied,
    "bundle_digest_verified": _condition_bundle_digest_verified,
}


def _validate_patch(state: dict[str, Any], patch: dict[str, Any]) -> None:
    trial = {**state, **patch}
    if not isinstance(trial.get("changed_domains"), list) or any(
            not isinstance(item, str) or not item for item in trial["changed_domains"]):
        raise EngineeringGraphError("changed_domains must be a string array")
    digests = trial.get("domain_artifact_digests")
    if not isinstance(digests, dict) or any(
            not isinstance(domain, str) or not _SHA256.fullmatch(str(value))
            for domain, value in digests.items()):
        raise EngineeringGraphError("domain_artifact_digests must map domains to SHA-256 values")
    for key in ("interface_findings", "deterministic_failures", "qualification_findings",
                "inference_usage_records"):
        if not isinstance(trial.get(key), list):
            raise EngineeringGraphError(f"{key} must be an array")
    if type(trial.get("iteration")) is not int or trial["iteration"] < 0:
        raise EngineeringGraphError("iteration must be a non-negative integer")
    if type(trial.get("bundle_digest_verified")) is not bool:
        raise EngineeringGraphError("bundle_digest_verified must be boolean")


def _finding_domains(finding: Any) -> set[str]:
    if not isinstance(finding, dict):
        raise EngineeringGraphError("engineering findings must be objects")
    raw_domains: list[Any] = []
    if "domain" in finding:
        raw_domains.append(finding["domain"])
    for field in ("domains", "owner_domains"):
        value = finding.get(field, [])
        if isinstance(value, list):
            raw_domains.extend(value)
        elif value is not None:
            raise EngineeringGraphError(f"finding {field} must be an array")
    if not raw_domains:
        raise EngineeringGraphError("engineering finding does not identify an affected domain")
    if any(not isinstance(item, str) or not item for item in raw_domains):
        raise EngineeringGraphError("engineering finding domains must be non-empty strings")
    return {_DOMAIN_ALIASES.get(item, item) for item in raw_domains}


def required_repair_domains(state: dict[str, Any]) -> list[str]:
    """Compute, rather than ask a model to choose, the active repair scope."""
    findings: list[Any] = []
    findings.extend(state.get("deterministic_failures", []))
    findings.extend(state.get("interface_findings", []))
    findings.extend(
        item for item in state.get("qualification_findings", [])
        if isinstance(item, dict) and item.get("status") != "pass")
    domains: set[str] = set()
    for finding in findings:
        domains.update(_finding_domains(finding))
    available = set(state.get("domain_artifact_digests", {}))
    missing = domains - available
    if missing:
        raise EngineeringGraphError(
            f"active findings reference unsynthesized domains {sorted(missing)}")
    if not domains:
        raise EngineeringGraphError("repair_scoping has no active failing finding")
    return sorted(domains)


def repair_scope_patch(run: dict[str, Any]) -> dict[str, Any]:
    if run.get("schema") != RUN_SCHEMA or run.get("status") != "active" \
            or run.get("active_nodes") != ["repair_scoping"]:
        raise EngineeringGraphError("repair_scope_patch requires an active repair_scoping node")
    state = run["state"]
    return {
        "changed_domains": required_repair_domains(state),
        "iteration": state["iteration"] + 1,
    }


def _validate_node_result(node_id: str, state: dict[str, Any],
                          patch: dict[str, Any]) -> None:
    if node_id == "repair_scoping":
        if "changed_domains" not in patch or "iteration" not in patch:
            raise EngineeringGraphError(
                "repair_scoping must write changed_domains and iteration")
        if patch["iteration"] != state["iteration"] + 1:
            raise EngineeringGraphError("repair_scoping must increment iteration by exactly one")
        expected_domains = required_repair_domains(state)
        if sorted(patch["changed_domains"]) != expected_domains \
                or len(patch["changed_domains"]) != len(set(patch["changed_domains"])):
            raise EngineeringGraphError(
                f"repair_scoping domains must equal computed scope {expected_domains}")
    if node_id == "targeted_repair":
        candidate_digests = patch.get("domain_artifact_digests")
        if not isinstance(candidate_digests, dict):
            raise EngineeringGraphError(
                "targeted_repair must write domain_artifact_digests")
        missing = set(state["changed_domains"]) - set(candidate_digests)
        unchanged = [domain for domain in state["changed_domains"]
                     if candidate_digests.get(domain)
                     == state["domain_artifact_digests"].get(domain)]
        if missing:
            raise EngineeringGraphError(
                f"targeted_repair omitted affected domains {sorted(missing)}")
        if unchanged:
            raise EngineeringGraphError(
                f"targeted_repair did not replace affected domains {sorted(unchanged)}")
        previous_digests = state["domain_artifact_digests"]
        if set(candidate_digests) != set(previous_digests):
            raise EngineeringGraphError(
                "targeted_repair must preserve the complete domain artifact map")
        out_of_scope = [domain for domain, digest in candidate_digests.items()
                        if domain not in state["changed_domains"]
                        and digest != previous_digests[domain]]
        if out_of_scope:
            raise EngineeringGraphError(
                f"targeted_repair changed out-of-scope domains {sorted(out_of_scope)}")


def complete_node(graph: dict[str, Any], run: dict[str, Any], node_id: str,
                  patch: dict[str, Any], *, actor_kind: str) -> dict[str, Any]:
    """Complete one active node and deterministically activate its successor."""
    validate_graph(graph)
    if run.get("schema") != RUN_SCHEMA or run.get("workflow_id") != graph["workflow_id"]:
        raise EngineeringGraphError("workflow run does not match graph")
    if run.get("status") != "active" or node_id not in run.get("active_nodes", []):
        raise EngineeringGraphError(f"node {node_id} is not active")
    nodes = {node["id"]: node for node in graph["nodes"]}
    node = nodes[node_id]
    if not isinstance(patch, dict):
        raise EngineeringGraphError("node result patch must be an object")
    unauthorized = set(patch) - set(node["writes"])
    if unauthorized:
        raise EngineeringGraphError(
            f"node {node_id} cannot write fields {sorted(unauthorized)}")
    if actor_kind not in {"software", "ai", "solver_or_test_worker"}:
        raise EngineeringGraphError("actor_kind is unsupported")
    owner = node["owner"]
    if actor_kind not in _OWNER_ACTORS[owner]:
        raise EngineeringGraphError(
            f"actor {actor_kind} cannot complete {owner} node {node_id}")
    if actor_kind == "ai" and set(patch) & _AI_FORBIDDEN_FIELDS:
        raise EngineeringGraphError(
            f"AI cannot write software-owned fields {sorted(set(patch) & _AI_FORBIDDEN_FIELDS)}")
    _validate_node_result(node_id, run["state"], patch)
    next_run = copy.deepcopy(run)
    _validate_patch(next_run["state"], patch)
    next_run["state"].update(copy.deepcopy(patch))
    matching = [edge for edge in graph["edges"] if edge["from"] == node_id
                and _CONDITIONS[edge["when"]](next_run["state"])]
    outgoing = [edge for edge in graph["edges"] if edge["from"] == node_id]
    if outgoing and len(matching) != 1:
        raise EngineeringGraphError(
            f"node {node_id} must select exactly one transition, selected {len(matching)}")
    destinations = [edge["to"] for edge in matching]
    next_run["active_nodes"] = destinations
    next_run["completed_nodes"].append(node_id)
    next_run["history"].append({
        "node_id": node_id,
        "actor_kind": actor_kind,
        "written_fields": sorted(patch),
        "selected_condition": matching[0]["when"] if matching else "terminal",
        "next_nodes": destinations,
    })
    if node_id == "finish":
        next_run["status"] = "complete"
    return next_run


__all__ = [
    "EngineeringGraphError",
    "RUN_SCHEMA",
    "complete_node",
    "new_run",
    "repair_scope_patch",
    "required_repair_domains",
    "validate_graph",
]
