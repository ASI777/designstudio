#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.memory.authoritative_product import compile_product  # noqa: E402
from swarm.memory.engineering_graph import (  # noqa: E402
    EngineeringGraphError,
    complete_node,
    new_run,
    repair_scope_patch,
    required_repair_domains,
    validate_graph,
)


FIXTURE = ROOT / "acceptance/authoritative-seven-axis-robot/source/engineering-requirements.json"
A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64


def load_graph() -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        manifest = compile_product(FIXTURE, Path(temporary))
        return json.loads(
            (manifest.parent / "workflow/engineering-graph.json").read_text(encoding="utf-8")
        )


def expect_error(operation, expected: str) -> None:
    try:
        operation()
    except EngineeringGraphError as error:
        assert expected in str(error), error
    else:
        raise AssertionError(f"expected EngineeringGraphError containing {expected!r}")


def advance_to_domain_synthesis(graph: dict) -> dict:
    run = new_run(
        graph,
        product_id="ds-cobot-r7",
        revision=1,
        evidence_digests=[A],
        rights_constraints=["inspiration_only"],
        observations=["workflow graph lesson"],
    )
    run = complete_node(
        graph,
        run,
        "evidence_intake",
        {"evidence_digests": [A], "rights_constraints": ["inspiration_only"],
         "observations": ["workflow graph lesson"]},
        actor_kind="software",
    )
    return complete_node(
        graph,
        run,
        "requirements_normalization",
        {"requirements_sha256": B, "changed_domains": ["mechanical", "electronics"]},
        actor_kind="software",
    )


def test_graph_executes_failure_loops_and_atomic_publication() -> None:
    graph = load_graph()
    validate_graph(graph)
    run = advance_to_domain_synthesis(graph)
    run = complete_node(
        graph,
        run,
        "domain_synthesis",
        {"domain_artifact_digests": {"mechanical": C, "electronics": C},
         "inference_usage_records": [{"provider": "test", "total_tokens": 10}]},
        actor_kind="ai",
    )

    run = complete_node(
        graph, run, "deterministic_validation",
        {"deterministic_failures": [{"domain": "electronics", "code": "clearance"}]},
        actor_kind="software",
    )
    assert run["active_nodes"] == ["repair_scoping"]
    assert required_repair_domains(run["state"]) == ["electronics"]
    run = complete_node(
        graph, run, "repair_scoping",
        repair_scope_patch(run), actor_kind="software",
    )
    run = complete_node(
        graph, run, "targeted_repair",
        {"domain_artifact_digests": {"mechanical": C, "electronics": D},
         "inference_usage_records": [{"provider": "test", "total_tokens": 15}]},
        actor_kind="ai",
    )
    run = complete_node(
        graph, run, "deterministic_validation", {"deterministic_failures": []},
        actor_kind="software",
    )

    run = complete_node(
        graph, run, "cross_domain_review",
        {"interface_findings": [{"domains": ["mechanical", "electronics"],
                                  "code": "thermal_interface"}]},
        actor_kind="ai",
    )
    assert run["active_nodes"] == ["repair_scoping"]
    assert required_repair_domains(run["state"]) == ["electronics", "mechanical"]
    run = complete_node(
        graph, run, "repair_scoping",
        repair_scope_patch(run),
        actor_kind="software",
    )
    run = complete_node(
        graph, run, "targeted_repair",
        {"domain_artifact_digests": {"mechanical": D, "electronics": E},
         "inference_usage_records": [{"provider": "test", "total_tokens": 20}]},
        actor_kind="software",
    )
    run = complete_node(
        graph, run, "deterministic_validation", {"deterministic_failures": []},
        actor_kind="software",
    )
    run = complete_node(
        graph, run, "cross_domain_review", {"interface_findings": []}, actor_kind="ai",
    )

    run = complete_node(
        graph, run, "physics_qualification",
        {"qualification_findings": [{"domain": "structural", "status": "fail"}]},
        actor_kind="solver_or_test_worker",
    )
    run = complete_node(
        graph, run, "repair_scoping",
        repair_scope_patch(run), actor_kind="software",
    )
    run = complete_node(
        graph, run, "targeted_repair",
        {"domain_artifact_digests": {"mechanical": E, "electronics": E},
         "inference_usage_records": [{"provider": "test", "total_tokens": 25}]},
        actor_kind="ai",
    )
    run = complete_node(
        graph, run, "deterministic_validation", {"deterministic_failures": []},
        actor_kind="software",
    )
    run = complete_node(
        graph, run, "cross_domain_review", {"interface_findings": []}, actor_kind="ai",
    )
    run = complete_node(
        graph, run, "physics_qualification",
        {"qualification_findings": [
            {"domain": "structural", "status": "pass"},
            {"domain": "thermal", "status": "pass"},
        ]},
        actor_kind="solver_or_test_worker",
    )
    run = complete_node(
        graph, run, "atomic_publication", {"bundle_digest_verified": True},
        actor_kind="software",
    )
    run = complete_node(graph, run, "finish", {}, actor_kind="software")

    assert run["status"] == "complete"
    assert run["active_nodes"] == []
    assert run["state"]["iteration"] == 3
    assert [entry["node_id"] for entry in run["history"]].count("targeted_repair") == 3
    run_schema = json.loads(
        (ROOT / "docs/schemas/engineering-workflow-run-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(run_schema)
    Draft202012Validator(run_schema).validate(run)


def test_graph_rejects_actor_impersonation_and_out_of_scope_repair() -> None:
    graph = load_graph()
    run = advance_to_domain_synthesis(graph)
    expect_error(
        lambda: complete_node(
            graph, run, "domain_synthesis",
            {"domain_artifact_digests": {"mechanical": C, "electronics": C}},
            actor_kind="solver_or_test_worker",
        ),
        "cannot complete",
    )
    run = complete_node(
        graph, run, "domain_synthesis",
        {"domain_artifact_digests": {"mechanical": C, "electronics": C}},
        actor_kind="ai",
    )
    run = complete_node(
        graph, run, "deterministic_validation",
        {"deterministic_failures": [{"domain": "electronics", "code": "clearance"}]},
        actor_kind="software",
    )
    expect_error(
        lambda: complete_node(
            graph, run, "repair_scoping",
            {"changed_domains": ["electronics"], "iteration": 2}, actor_kind="software",
        ),
        "increment iteration by exactly one",
    )
    run = complete_node(
        graph, run, "repair_scoping",
        repair_scope_patch(run), actor_kind="software",
    )
    expect_error(
        lambda: complete_node(
            graph, run, "targeted_repair",
            {"domain_artifact_digests": {"mechanical": D, "electronics": D}},
            actor_kind="ai",
        ),
        "out-of-scope domains",
    )

    graph = load_graph()
    run = advance_to_domain_synthesis(graph)
    run = complete_node(
        graph, run, "domain_synthesis",
        {"domain_artifact_digests": {"mechanical": C, "electronics": C}},
        actor_kind="software",
    )
    run = complete_node(
        graph, run, "deterministic_validation",
        {"deterministic_failures": [{"domains": ["mechanical", "electronics"],
                                      "code": "cross_domain_collision"}]},
        actor_kind="software",
    )
    expect_error(
        lambda: complete_node(
            graph, run, "repair_scoping",
            {"changed_domains": ["electronics"], "iteration": 1},
            actor_kind="software",
        ),
        "must equal computed scope",
    )


def test_graph_rejects_unknown_conditions_and_ai_owned_system_state() -> None:
    graph = load_graph()
    malformed = copy.deepcopy(graph)
    malformed["edges"][0]["when"] = "python_expression_here"
    expect_error(lambda: validate_graph(malformed), "unsupported condition")

    run = advance_to_domain_synthesis(graph)
    permissive = copy.deepcopy(graph)
    synthesis = next(node for node in permissive["nodes"] if node["id"] == "domain_synthesis")
    synthesis["writes"].append("requirements_sha256")
    expect_error(
        lambda: complete_node(
            permissive, run, "domain_synthesis", {"requirements_sha256": C}, actor_kind="ai"
        ),
        "AI cannot write software-owned fields",
    )


def main() -> None:
    test_graph_executes_failure_loops_and_atomic_publication()
    test_graph_rejects_actor_impersonation_and_out_of_scope_repair()
    test_graph_rejects_unknown_conditions_and_ai_owned_system_state()
    print("Engineering workflow graph runtime tests passed")


if __name__ == "__main__":
    main()
