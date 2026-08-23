"""Transactional, content-addressed component library.

SQLite is the authority for acquired datasheets and approved component assets.
Filesystem files produced by this module are materialized cache copies for CAD
programs which require paths; their SHA-256 digest remains bound to the database.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from swarm.runtime_paths import component_library_db_path

SCHEMA_VERSION = 2
ASSET_KINDS = {"datasheet_pdf", "kicad_v6_footprint", "step"}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def normalize_mpn(value: str) -> str:
    """Comparison key only; the manufacturer's spelling remains preserved."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def infer_datasheet_identity(path: str | Path) -> tuple[str, str]:
    """Infer a search hint from a filename, never an exact-MPN assertion."""
    stem = Path(path).stem.strip()
    match = re.match(r"^(digikey|mouser|nexar|octopart)[-_](.+)$", stem, re.I)
    if match:
        return match.group(1).lower(), match.group(2).strip()
    return "unknown", stem


class ComponentLibrary:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or component_library_db_path()).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            # Read-only/network filesystems can reject WAL. Transactions and
            # foreign keys still preserve correctness.
            pass
        self._migrate()

    def __enter__(self) -> "ComponentLibrary":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"component library schema {version} is newer than supported")
        if version == 0:
            self.connection.executescript("""
                CREATE TABLE assets (
                    sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64),
                    kind TEXT NOT NULL,
                    format TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                    content BLOB NOT NULL,
                    source_uri TEXT NOT NULL DEFAULT '',
                    imported_utc TEXT NOT NULL
                );
                CREATE TABLE datasheet_sources (
                    source_path TEXT PRIMARY KEY,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
                    inferred_vendor TEXT NOT NULL,
                    inferred_mpn TEXT NOT NULL,
                    identity_state TEXT NOT NULL DEFAULT 'filename_hint',
                    imported_utc TEXT NOT NULL
                );
                CREATE INDEX datasheet_sources_sha_idx ON datasheet_sources(asset_sha256);
                CREATE TABLE components (
                    mpn_key TEXT PRIMARY KEY,
                    mpn TEXT NOT NULL,
                    manufacturer TEXT NOT NULL,
                    component_json TEXT NOT NULL,
                    binding_json TEXT,
                    status TEXT NOT NULL CHECK(status IN ('draft','approved','blocked')),
                    assumptions_json TEXT NOT NULL DEFAULT '[]',
                    evidence_state TEXT NOT NULL DEFAULT 'incomplete',
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
                CREATE TABLE component_assets (
                    mpn_key TEXT NOT NULL REFERENCES components(mpn_key) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
                    exact_mpn INTEGER NOT NULL DEFAULT 0 CHECK(exact_mpn IN (0,1)),
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(mpn_key, role)
                );
                CREATE TABLE reference_circuits (
                    mpn_key TEXT NOT NULL REFERENCES components(mpn_key) ON DELETE CASCADE,
                    circuit_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    datasheet_sha256 TEXT REFERENCES assets(sha256),
                    pages_json TEXT NOT NULL DEFAULT '[]',
                    evidence_summary TEXT NOT NULL DEFAULT '',
                    circuit_json TEXT NOT NULL,
                    PRIMARY KEY(mpn_key, circuit_id)
                );
                CREATE TABLE engineering_checks (
                    scope TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pass','fail','incomplete','not_applicable')),
                    critical INTEGER NOT NULL DEFAULT 1 CHECK(critical IN (0,1)),
                    input_digest TEXT NOT NULL DEFAULT '',
                    report_json TEXT NOT NULL,
                    checked_utc TEXT NOT NULL,
                    PRIMARY KEY(scope, category)
                );
                CREATE TABLE approvals (
                    scope TEXT NOT NULL,
                    role TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    approved INTEGER NOT NULL CHECK(approved IN (0,1)),
                    reviewer TEXT NOT NULL,
                    rationale TEXT NOT NULL DEFAULT '',
                    approved_utc TEXT NOT NULL,
                    PRIMARY KEY(scope, role, artifact_digest)
                );
                PRAGMA user_version=1;
            """)
            self.connection.commit()
            version = 1
        if version < 2:
            self.connection.executescript("""
                CREATE TABLE component_review_queue (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mpn_key TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL CHECK(length(artifact_digest)=64),
                    state TEXT NOT NULL CHECK(state IN ('ready','blocked','approved','rejected')),
                    blockers_json TEXT NOT NULL DEFAULT '[]',
                    reviewer TEXT NOT NULL DEFAULT '',
                    rationale TEXT NOT NULL DEFAULT '',
                    created_utc TEXT NOT NULL,
                    reviewed_utc TEXT,
                    UNIQUE(mpn_key,artifact_digest)
                );
                CREATE INDEX component_review_state_idx
                    ON component_review_queue(state,review_id);
                PRAGMA user_version=2;
            """)
            self.connection.commit()

    @staticmethod
    def _digest_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def put_asset(self, kind: str, content: bytes, *, format: str,
                  source_uri: str = "") -> str:
        if kind not in ASSET_KINDS:
            raise ValueError(f"unsupported component asset kind: {kind}")
        if kind == "datasheet_pdf" and not content.startswith(b"%PDF"):
            raise ValueError("datasheet asset is not a PDF")
        if kind == "step" and b"ISO-10303-21" not in content[:4096].upper():
            raise ValueError("3D asset is not an ISO-10303-21 STEP file")
        digest = self._digest_bytes(content)
        self.connection.execute(
            "INSERT OR IGNORE INTO assets "
            "(sha256,kind,format,byte_size,content,source_uri,imported_utc) "
            "VALUES (?,?,?,?,?,?,?)",
            (digest, kind, format, len(content), sqlite3.Binary(content), source_uri, _utc()))
        return digest

    def put_asset_file(self, kind: str, path: str | Path, *, format: str | None = None) -> str:
        source = Path(path).expanduser().resolve()
        return self.put_asset(kind, source.read_bytes(),
                              format=format or source.suffix.lower().lstrip("."),
                              source_uri=str(source))

    def ingest_datasheet(self, path: str | Path) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        stat = source.stat()
        previous = self.connection.execute(
            "SELECT source_size,source_mtime_ns,asset_sha256 FROM datasheet_sources "
            "WHERE source_path=?", (str(source),)).fetchone()
        if previous and previous["source_size"] == stat.st_size \
                and previous["source_mtime_ns"] == stat.st_mtime_ns:
            return {"path": str(source), "sha256": previous["asset_sha256"],
                    "status": "unchanged"}
        content = source.read_bytes()
        digest = self.put_asset("datasheet_pdf", content, format="pdf", source_uri=str(source))
        vendor, mpn = infer_datasheet_identity(source)
        self.connection.execute("""
            INSERT INTO datasheet_sources
              (source_path,source_size,source_mtime_ns,asset_sha256,inferred_vendor,
               inferred_mpn,identity_state,imported_utc)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(source_path) DO UPDATE SET
              source_size=excluded.source_size,
              source_mtime_ns=excluded.source_mtime_ns,
              asset_sha256=excluded.asset_sha256,
              inferred_vendor=excluded.inferred_vendor,
              inferred_mpn=excluded.inferred_mpn,
              identity_state=excluded.identity_state,
              imported_utc=excluded.imported_utc
        """, (str(source), stat.st_size, stat.st_mtime_ns, digest, vendor, mpn,
              "filename_hint", _utc()))
        return {"path": str(source), "sha256": digest, "status": "imported",
                "inferred_vendor": vendor, "inferred_mpn": mpn,
                "identity_state": "filename_hint"}

    def ingest_datasheet_tree(self, root: str | Path) -> dict[str, Any]:
        files = sorted(Path(root).expanduser().resolve().rglob("*.pdf"))
        summary: dict[str, Any] = {"found": len(files), "imported": 0, "unchanged": 0,
                                  "failed": 0, "failures": []}
        for path in files:
            try:
                with self.connection:
                    result = self.ingest_datasheet(path)
                summary[result["status"]] += 1
            except Exception as exc:
                summary["failed"] += 1
                summary["failures"].append({"path": str(path), "error": str(exc)})
        summary["unique_assets"] = self.connection.execute(
            "SELECT COUNT(*) FROM assets WHERE kind='datasheet_pdf'").fetchone()[0]
        return summary

    def upsert_component(self, component: dict, *, binding: dict | None = None,
                         status: str = "draft", assumptions: list | None = None,
                         evidence_state: str = "incomplete") -> str:
        identity = component.get("component") or {}
        mpn = str(identity.get("mpn") or "").strip()
        manufacturer = str(identity.get("manufacturer") or "").strip()
        key = normalize_mpn(mpn)
        if not key or not manufacturer:
            raise ValueError("component manufacturer and MPN are required")
        if status not in ("draft", "approved", "blocked"):
            raise ValueError("invalid component status")
        now = _utc()
        values = (key, mpn, manufacturer, _json(component),
                  _json(binding) if binding is not None else None, status,
                  _json(assumptions if assumptions is not None else component.get("assumptions", [])),
                  evidence_state, now, now)
        self.connection.execute("""
            INSERT INTO components
              (mpn_key,mpn,manufacturer,component_json,binding_json,status,
               assumptions_json,evidence_state,created_utc,updated_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(mpn_key) DO UPDATE SET
              mpn=excluded.mpn, manufacturer=excluded.manufacturer,
              component_json=excluded.component_json,
              binding_json=COALESCE(excluded.binding_json,components.binding_json),
              status=excluded.status, assumptions_json=excluded.assumptions_json,
              evidence_state=excluded.evidence_state, updated_utc=excluded.updated_utc
        """, values)
        return key

    def link_asset(self, mpn: str, role: str, sha256: str, *, exact_mpn: bool = False,
                   provenance: dict | None = None) -> None:
        key = normalize_mpn(mpn)
        self.connection.execute("""
            INSERT INTO component_assets(mpn_key,role,asset_sha256,exact_mpn,provenance_json)
            VALUES (?,?,?,?,?) ON CONFLICT(mpn_key,role) DO UPDATE SET
              asset_sha256=excluded.asset_sha256, exact_mpn=excluded.exact_mpn,
              provenance_json=excluded.provenance_json
        """, (key, role, sha256, int(exact_mpn), _json(provenance or {})))

    def replace_reference_circuits(self, mpn: str, circuits: Iterable[dict],
                                   datasheet_sha256: str | None = None) -> None:
        key = normalize_mpn(mpn)
        self.connection.execute("DELETE FROM reference_circuits WHERE mpn_key=?", (key,))
        for index, circuit in enumerate(circuits):
            circuit_id = str(circuit.get("id") or f"reference-{index + 1}")
            self.connection.execute("""
                INSERT INTO reference_circuits
                  (mpn_key,circuit_id,mode,datasheet_sha256,pages_json,
                   evidence_summary,circuit_json) VALUES (?,?,?,?,?,?,?)
            """, (key, circuit_id, str(circuit.get("mode") or ""), datasheet_sha256,
                  _json(circuit.get("datasheet_pages") or []),
                  str(circuit.get("evidence_summary") or ""), _json(circuit)))

    def record_check(self, scope: str, category: str, status: str, report: dict,
                     *, critical: bool = True, input_digest: str = "") -> None:
        if status not in ("pass", "fail", "incomplete", "not_applicable"):
            raise ValueError("invalid engineering check status")
        self.connection.execute("""
            INSERT INTO engineering_checks
              (scope,category,status,critical,input_digest,report_json,checked_utc)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(scope,category) DO UPDATE SET
              status=excluded.status, critical=excluded.critical,
              input_digest=excluded.input_digest, report_json=excluded.report_json,
              checked_utc=excluded.checked_utc
        """, (scope, category, status, int(critical), input_digest, _json(report), _utc()))

    def record_approval(self, scope: str, role: str, artifact_digest: str,
                        reviewer: str, *, approved: bool = True, rationale: str = "") -> None:
        if not reviewer.strip() or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
            raise ValueError("approval requires reviewer and exact artifact SHA-256")
        self.connection.execute("""
            INSERT OR REPLACE INTO approvals
              (scope,role,artifact_digest,approved,reviewer,rationale,approved_utc)
            VALUES (?,?,?,?,?,?,?)
        """, (scope, role, artifact_digest, int(approved), reviewer, rationale, _utc()))

    def enqueue_component_review(self, mpn: str, manifest_path: str | Path,
                                 artifact_digest: str, *, ready: bool,
                                 blockers: list[str] | None = None) -> int:
        if not normalize_mpn(mpn) or not re.fullmatch(r"[0-9a-f]{64}", artifact_digest):
            raise ValueError("review queue requires an MPN and exact artifact digest")
        state = "ready" if ready else "blocked"
        self.connection.execute("""
            INSERT INTO component_review_queue
              (mpn_key,manifest_path,artifact_digest,state,blockers_json,created_utc)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(mpn_key,artifact_digest) DO UPDATE SET
              manifest_path=excluded.manifest_path,
              state=CASE WHEN component_review_queue.state IN ('approved','rejected')
                         THEN component_review_queue.state ELSE excluded.state END,
              blockers_json=excluded.blockers_json
        """, (normalize_mpn(mpn), str(Path(manifest_path).resolve()), artifact_digest,
              state, _json(blockers or []), _utc()))
        row = self.connection.execute(
            "SELECT review_id FROM component_review_queue WHERE mpn_key=? AND artifact_digest=?",
            (normalize_mpn(mpn), artifact_digest)).fetchone()
        return int(row["review_id"])

    def component_review_queue(self, *, include_completed: bool = False) -> list[dict]:
        where = "" if include_completed else "WHERE state IN ('ready','blocked')"
        rows = self.connection.execute(
            f"SELECT * FROM component_review_queue {where} ORDER BY review_id").fetchall()
        return [{**dict(row), "blockers": json.loads(row["blockers_json"])} for row in rows]

    def review_component_asset(self, review_id: int, *, reviewer: str,
                               approved: bool, artifact_digest: str,
                               rationale: str = "") -> dict:
        row = self.connection.execute(
            "SELECT * FROM component_review_queue WHERE review_id=?", (review_id,)).fetchone()
        if not row:
            raise KeyError(f"component review {review_id} does not exist")
        if not reviewer.strip() or artifact_digest != row["artifact_digest"]:
            raise ValueError("review must bind to the queued exact artifact digest")
        if approved and row["state"] != "ready":
            raise ValueError("blocked or completed component assets cannot be approved")
        state = "approved" if approved else "rejected"
        self.connection.execute("""
            UPDATE component_review_queue SET state=?,reviewer=?,rationale=?,reviewed_utc=?
            WHERE review_id=?
        """, (state, reviewer, rationale, _utc(), review_id))
        self.record_approval(f"component:{row['mpn_key']}", "asset_review",
                             artifact_digest, reviewer, approved=approved,
                             rationale=rationale or f"Component assets {state}")
        return {"review_id": review_id, "mpn_key": row["mpn_key"], "state": state,
                "artifact_digest": artifact_digest, "manifest_path": row["manifest_path"]}

    def approve_all_reviewed(self, *, reviewer: str,
                             artifact_digests: dict[int, str]) -> list[dict]:
        ready = [row for row in self.component_review_queue() if row["state"] == "ready"]
        missing = [row["review_id"] for row in ready if row["review_id"] not in artifact_digests]
        if missing:
            raise ValueError(f"approve-all requires reviewed artifact digests for {missing}")
        return [self.review_component_asset(
            row["review_id"], reviewer=reviewer, approved=True,
            artifact_digest=artifact_digests[row["review_id"]],
            rationale="Approve all reviewed component assets") for row in ready]

    def get_component(self, mpn: str, *, approved_only: bool = True) -> dict | None:
        row = self.connection.execute(
            "SELECT component_json,status FROM components WHERE mpn_key=?",
            (normalize_mpn(mpn),)).fetchone()
        if not row or (approved_only and row["status"] != "approved"):
            return None
        return json.loads(row["component_json"])

    def get_binding(self, mpn: str, *, approved_only: bool = True) -> dict | None:
        row = self.connection.execute(
            "SELECT binding_json,status FROM components WHERE mpn_key=?",
            (normalize_mpn(mpn),)).fetchone()
        if not row or not row["binding_json"] \
                or (approved_only and row["status"] != "approved"):
            return None
        return json.loads(row["binding_json"])

    def asset_for(self, mpn: str, role: str) -> sqlite3.Row | None:
        return self.connection.execute("""
            SELECT a.*,ca.exact_mpn,ca.provenance_json FROM component_assets ca
            JOIN assets a ON a.sha256=ca.asset_sha256
            WHERE ca.mpn_key=? AND ca.role=?
        """, (normalize_mpn(mpn), role)).fetchone()

    def materialize_asset(self, sha256: str, destination: str | Path) -> Path:
        row = self.connection.execute(
            "SELECT content FROM assets WHERE sha256=?", (sha256,)).fetchone()
        if not row:
            raise KeyError(f"unknown component asset {sha256}")
        target = Path(destination).expanduser().resolve()
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(row["content"])
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def stats(self) -> dict[str, Any]:
        counts = {row["kind"]: row["count"] for row in self.connection.execute(
            "SELECT kind,COUNT(*) AS count FROM assets GROUP BY kind")}
        return {"path": str(self.path), "schema_version": SCHEMA_VERSION,
                "assets": counts,
                "datasheet_sources": self.connection.execute(
                    "SELECT COUNT(*) FROM datasheet_sources").fetchone()[0],
                "components": self.connection.execute(
                    "SELECT COUNT(*) FROM components").fetchone()[0],
                "approved_components": self.connection.execute(
                    "SELECT COUNT(*) FROM components WHERE status='approved'").fetchone()[0]}
