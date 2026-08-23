#!/usr/bin/env python3
"""Anti-Gravity MCP Server — pull-based task queue + shared context for the
schematic_designer agent swarm.

Run:
    python swarm/antigravity/server.py              # streamable-http on :8765
    python swarm/antigravity/server.py --stdio      # stdio transport (single agent)
"""

import json
import sys
import argparse
from pathlib import Path

# Ensure local site-packages are importable when run directly
_site = Path.home() / ".local/lib/python3.14/site-packages"
if str(_site) not in sys.path:
    sys.path.insert(0, str(_site))

from mcp.server.fastmcp import FastMCP
import db  # sibling module

db.init()

mcp = FastMCP(
    "anti-gravity",
    instructions=(
        "Anti-Gravity is the coordination backbone for the schematic_designer swarm. "
        "Agents use it to push tasks, claim work, report results, and share context. "
        "All JSON payloads must be valid JSON strings."
    ),
    host="127.0.0.1",
    port=8765,
)


# ── Task queue tools ──────────────────────────────────────────────────────────

@mcp.tool()
def ag_task_push(
    title: str,
    task_type: str,
    priority: int = 5,
    context_json: str = "{}",
    blocked_by: str = "",
) -> str:
    """Push a new task onto the queue.

    task_type must be one of the routing tags in config.yaml:
      cpp_boilerplate, test_scaffold, json_schema, docs, build_triage,
      review_simple, cpp_algorithm, csharp_complex, physics, drc_design,
      architecture, physics_validation, novel_algorithm, cross_file_refactor

    priority: 1 (urgent) … 10 (low). Default 5.
    context_json: arbitrary JSON string passed to the claiming agent.
    blocked_by: task_id that must be completed before this task is claimable.

    Returns JSON: {"task_id": "<uuid>"}
    """
    task_id = db.task_push(
        title=title,
        task_type=task_type,
        priority=max(1, min(10, priority)),
        context_json=context_json,
        blocked_by=blocked_by or None,
    )
    db.event_push("task_pushed", json.dumps({"task_id": task_id, "title": title, "task_type": task_type}))
    return json.dumps({"task_id": task_id})


@mcp.tool()
def ag_task_claim(agent_id: str, capabilities: str = "") -> str:
    """Claim the highest-priority pending task this agent can handle.

    agent_id: unique name for the calling agent (e.g. "cpp_engineer_1").
    capabilities: comma-separated task_type values this agent handles.
                  Leave empty to claim any task type.

    Returns JSON task object, or {"task": null} if no work is available.
    The returned object contains: id, title, task_type, priority, context_json,
    retry_count, and blocked_by.
    """
    caps = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []
    task = db.task_claim(agent_id=agent_id, capability_filter=caps)
    if task is None:
        return json.dumps({"task": None})
    return json.dumps({"task": task})


@mcp.tool()
def ag_task_complete(task_id: str, result_json: str = "{}", patch_path: str = "") -> str:
    """Mark a task as successfully completed.

    task_id: the id returned by ag_task_claim.
    result_json: JSON string summarising what was produced.
    patch_path: relative path to the file patch/output, if any.

    Returns JSON: {"ok": true} or {"ok": false, "error": "..."}
    """
    ok = db.task_complete(task_id=task_id, result_json=result_json, patch_path=patch_path)
    if ok:
        db.event_push("task_completed", json.dumps({"task_id": task_id, "patch_path": patch_path}))
    return json.dumps({"ok": ok} if ok else {"ok": False, "error": "task not in_progress or not found"})


@mcp.tool()
def ag_task_fail(task_id: str, reason: str = "") -> str:
    """Mark a task as failed. It will be re-queued with exponential backoff
    (30s → 2min → 10min) up to 3 retries, then permanently failed.

    Returns JSON: {"requeued": bool, "retry_count": int, "retry_after_seconds": int}
    """
    result = db.task_fail(task_id=task_id, reason=reason)
    db.event_push("task_failed", json.dumps({"task_id": task_id, "reason": reason, **result}))
    return json.dumps(result)


@mcp.tool()
def ag_task_get(task_id: str) -> str:
    """Fetch the full record for a single task by id.

    Returns the task JSON object or {"error": "not found"}.
    """
    task = db.task_get(task_id)
    return json.dumps(task if task else {"error": "not found"})


@mcp.tool()
def ag_task_list(status: str = "", limit: int = 20) -> str:
    """List tasks, optionally filtered by status.

    status: pending | in_progress | completed | failed | "" (all)
    limit: max results (default 20, max 100)

    Returns JSON: {"tasks": [...]}
    """
    tasks = db.task_list(status=status or None, limit=min(limit, 100))
    return json.dumps({"tasks": tasks})


@mcp.tool()
def ag_queue_status() -> str:
    """Return queue statistics: counts of tasks in each status.

    Returns JSON: {"pending": N, "in_progress": N, "completed": N, "failed": N, "total": N}
    """
    return json.dumps(db.queue_status())


# ── Shared context tools ───────────────────────────────────────────────────────

@mcp.tool()
def ag_context_read(key: str) -> str:
    """Read a value from the shared context store.

    Well-known keys used by the swarm:
      current_phase       — active roadmap phase name
      failing_tests       — JSON list of currently red test names
      budget_spent_usd    — cumulative Claude API spend this session
      board_version       — BoardDocument.Version at last extraction
      last_completed_item — last roadmap item finished

    Returns JSON: {"key": "...", "value": "..."} or {"key": "...", "value": null}
    """
    value = db.context_read(key)
    return json.dumps({"key": key, "value": value})


@mcp.tool()
def ag_context_write(key: str, value: str) -> str:
    """Write a value to the shared context store (durable across restarts).

    value must be a string (JSON-encode objects before storing).

    Returns JSON: {"ok": true}
    """
    db.context_write(key, value)
    return json.dumps({"ok": True})


@mcp.tool()
def ag_context_list() -> str:
    """List all keys in the shared context store with their last-updated timestamps.

    Returns JSON: {"keys": [{"key": "...", "updated_at": <unix_ts>}, ...]}
    """
    return json.dumps({"keys": db.context_list()})


# ── Event / pub-sub tools ─────────────────────────────────────────────────────

@mcp.tool()
def ag_broadcast(event_type: str, payload_json: str = "{}") -> str:
    """Broadcast an event to the event log. Other agents poll with ag_events_poll.

    event_type: arbitrary string, e.g. "phase_complete", "new_roadmap_item",
                "build_broken", "review_requested".
    payload_json: JSON string with event data.

    Returns JSON: {"event_id": N}
    """
    event_id = db.event_push(event_type=event_type, payload_json=payload_json)
    return json.dumps({"event_id": event_id})


@mcp.tool()
def ag_events_poll(since_id: int = 0, limit: int = 50) -> str:
    """Poll for events newer than since_id. Use the highest returned id as
    the next since_id to avoid re-processing events.

    Returns JSON: {"events": [...], "last_id": N}
    """
    events = db.events_poll(since_id=since_id, limit=limit)
    last_id = events[-1]["id"] if events else since_id
    return json.dumps({"events": events, "last_id": last_id})


# ── Result aggregation ────────────────────────────────────────────────────────

@mcp.tool()
def ag_result_aggregate(task_ids_json: str) -> str:
    """Aggregate results from multiple completed tasks.

    task_ids_json: JSON array of task id strings,
                   e.g. '["uuid1", "uuid2", "uuid3"]'

    Returns JSON:
      {
        "results": [{id, title, status, result_json, patch_path}, ...],
        "missing_ids": [...],
        "all_complete": bool
      }
    """
    try:
        task_ids = json.loads(task_ids_json)
        if not isinstance(task_ids, list):
            return json.dumps({"error": "task_ids_json must be a JSON array"})
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid JSON: {e}"})

    return json.dumps(db.result_aggregate(task_ids))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anti-Gravity MCP server")
    parser.add_argument("--stdio", action="store_true", help="Use stdio transport")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.stdio:
        print("[anti-gravity] starting on stdio", file=sys.stderr)
        mcp.run(transport="stdio")
    else:
        # host/port are set in FastMCP constructor via Settings
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(f"[anti-gravity] listening on http://{args.host}:{args.port}/mcp", file=sys.stderr)
        mcp.run(transport="streamable-http")
