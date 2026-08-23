"""SQLite-backed persistence for the Anti-Gravity task queue and context store."""

import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "queue.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    task_type    TEXT NOT NULL,
    priority     INTEGER DEFAULT 5,
    status       TEXT DEFAULT 'pending',
    agent_id     TEXT,
    context_json TEXT DEFAULT '{}',
    result_json  TEXT,
    patch_path   TEXT,
    error        TEXT,
    retry_count  INTEGER DEFAULT 0,
    blocked_by   TEXT,
    created_at   REAL NOT NULL,
    claimed_at   REAL,
    completed_at REAL,
    retry_after  REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status, priority, retry_after);
CREATE INDEX IF NOT EXISTS idx_tasks_type     ON tasks(task_type, status);
CREATE INDEX IF NOT EXISTS idx_tasks_blocked  ON tasks(blocked_by);

CREATE TABLE IF NOT EXISTS context_store (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    payload_json TEXT DEFAULT '{}',
    created_at   REAL NOT NULL
);
"""

BACKOFF_SECONDS = [30, 120, 600]  # retry delays: 30s, 2min, 10min
MAX_RETRIES = 3


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


# ── Task queue ────────────────────────────────────────────────────────────────

def task_push(title: str, task_type: str, priority: int = 5,
              context_json: str = "{}", blocked_by: str | None = None) -> str:
    task_id = str(uuid.uuid4())
    with conn() as c:
        c.execute(
            """INSERT INTO tasks
               (id, title, task_type, priority, context_json, blocked_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (task_id, title, task_type, priority, context_json, blocked_by, time.time()),
        )
    return task_id


def task_claim(agent_id: str, capability_filter: list[str]) -> dict | None:
    """Claim the highest-priority pending task matching capability_filter (or any if empty)."""
    now = time.time()
    with conn() as c:
        if capability_filter:
            placeholders = ",".join("?" * len(capability_filter))
            row = c.execute(
                f"""SELECT * FROM tasks
                    WHERE status = 'pending'
                      AND retry_after <= ?
                      AND (blocked_by IS NULL OR blocked_by IN (
                            SELECT id FROM tasks WHERE status='completed'))
                      AND task_type IN ({placeholders})
                    ORDER BY priority ASC, created_at ASC
                    LIMIT 1""",
                [now, *capability_filter],
            ).fetchone()
        else:
            row = c.execute(
                """SELECT * FROM tasks
                   WHERE status = 'pending'
                     AND retry_after <= ?
                     AND (blocked_by IS NULL OR blocked_by IN (
                           SELECT id FROM tasks WHERE status='completed'))
                   ORDER BY priority ASC, created_at ASC
                   LIMIT 1""",
                [now],
            ).fetchone()

        if row is None:
            return None

        c.execute(
            "UPDATE tasks SET status='in_progress', agent_id=?, claimed_at=? WHERE id=?",
            (agent_id, now, row["id"]),
        )
        return dict(row)


def task_complete(task_id: str, result_json: str = "{}", patch_path: str = "") -> bool:
    with conn() as c:
        cur = c.execute(
            """UPDATE tasks
               SET status='completed', result_json=?, patch_path=?, completed_at=?
               WHERE id=? AND status='in_progress'""",
            (result_json, patch_path, time.time(), task_id),
        )
        return cur.rowcount == 1


def task_fail(task_id: str, reason: str = "") -> dict:
    with conn() as c:
        row = c.execute("SELECT retry_count FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return {"requeued": False, "error": "task not found"}

        retries = row["retry_count"]
        if retries >= MAX_RETRIES:
            c.execute(
                "UPDATE tasks SET status='failed', error=? WHERE id=?",
                (reason, task_id),
            )
            return {"requeued": False, "retry_count": retries}

        delay = BACKOFF_SECONDS[min(retries, len(BACKOFF_SECONDS) - 1)]
        c.execute(
            """UPDATE tasks
               SET status='pending', error=?, retry_count=retry_count+1,
                   agent_id=NULL, claimed_at=NULL, retry_after=?
               WHERE id=?""",
            (reason, time.time() + delay, task_id),
        )
        return {"requeued": True, "retry_count": retries + 1, "retry_after_seconds": delay}


def task_get(task_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def task_list(status: str | None = None, limit: int = 50) -> list[dict]:
    with conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY priority,created_at LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def queue_status() -> dict:
    with conn() as c:
        counts = {}
        for (status, n) in c.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        ).fetchall():
            counts[status] = n
        return {
            "pending":     counts.get("pending", 0),
            "in_progress": counts.get("in_progress", 0),
            "completed":   counts.get("completed", 0),
            "failed":      counts.get("failed", 0),
            "total":       sum(counts.values()),
        }


# ── Context store ─────────────────────────────────────────────────────────────

def context_read(key: str) -> str | None:
    with conn() as c:
        row = c.execute("SELECT value FROM context_store WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def context_write(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO context_store(key, value, updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, time.time()),
        )


def context_list() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT key, updated_at FROM context_store").fetchall()]


# ── Events / pub-sub ──────────────────────────────────────────────────────────

def event_push(event_type: str, payload_json: str = "{}") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO events(event_type, payload_json, created_at) VALUES(?,?,?)",
            (event_type, payload_json, time.time()),
        )
        return cur.lastrowid


def events_poll(since_id: int = 0, limit: int = 100) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Result aggregation ────────────────────────────────────────────────────────

def result_aggregate(task_ids: list[str]) -> dict:
    with conn() as c:
        placeholders = ",".join("?" * len(task_ids))
        rows = c.execute(
            f"SELECT id, title, status, result_json, patch_path FROM tasks WHERE id IN ({placeholders})",
            task_ids,
        ).fetchall()
    results = []
    missing = set(task_ids)
    for r in rows:
        missing.discard(r["id"])
        results.append(dict(r))
    return {
        "results": results,
        "missing_ids": list(missing),
        "all_complete": all(r["status"] == "completed" for r in results) and not missing,
    }
