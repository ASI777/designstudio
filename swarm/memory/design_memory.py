"""Layer B — Self-evolving design-case memory (A-Mem style).

Each completed (or attempted) design becomes a *note*.  New notes auto-link to
similar existing notes, and retrieval returns the most relevant past cases for a
new intent — so "robotic arm controller" is answered by a cluster of real past
designs rather than a hand-authored template (arXiv:2502.12110, A-Mem).

Self-contained: similarity is TF-IDF cosine over tokenised note text (stdlib
only — no embedding model, which keeps it runnable on the local 6 GB GPU box).

Note schema (one JSON file per note):
  id, created, content, keywords[], tags[], context, links[],
  payload{ archetype, components[], nets, status, ... }
"""
from __future__ import annotations
import json
import math
import os
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "with", "on", "in",
         "is", "it", "this", "that", "by", "as", "at", "be", "are"}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower())
            if t not in _STOP and len(t) > 1]


@dataclass
class Note:
    id: str
    created: float
    content: str                       # human-readable summary of the design
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    context: str = ""                  # contextual description (the "why")
    links: list[str] = field(default_factory=list)   # ids of related notes
    payload: dict = field(default_factory=dict)       # structured design data
    quality: float = 0.5               # outcome signal 0..1 (1 = clean first pass)
    reuse_count: int = 0               # how often this case has been reused
    corroborations: int = 0            # how many later similar designs corroborated it
    real_outcome: str = ""             # "", "accepted", "exported", "rejected", "manufactured"

    def text_blob(self) -> str:
        return " ".join([self.content, " ".join(self.keywords),
                         " ".join(self.tags), self.context])


class DesignMemory:
    def __init__(self, store_dir: str | None = None):
        self.dir = Path(store_dir or
            (Path.home() / ".config" / "product_design" / "design_memory"))
        self.notes: dict[str, Note] = {}
        self._idf: dict[str, float] = {}

    # ── persistence ────────────────────────────────────────────────────────────
    def load(self) -> "DesignMemory":
        if self.dir.exists():
            for f in self.dir.glob("note_*.json"):
                try:
                    d = json.loads(f.read_text())
                    self.notes[d["id"]] = Note(**d)
                except Exception:
                    pass
        self._rebuild_idf()
        return self

    def _save_note(self, note: Note) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"note_{note.id}.json").write_text(
            json.dumps(asdict(note), indent=2))

    # ── TF-IDF index ───────────────────────────────────────────────────────────
    def _rebuild_idf(self) -> None:
        n = len(self.notes) or 1
        df: Counter = Counter()
        for note in self.notes.values():
            for tok in set(_tokens(note.text_blob())):
                df[tok] += 1
        self._idf = {tok: math.log((n + 1) / (c + 1)) + 1.0 for tok, c in df.items()}

    def _vec(self, text: str) -> dict[str, float]:
        tf = Counter(_tokens(text))
        if not tf:
            return {}
        maxf = max(tf.values())
        return {tok: (0.5 + 0.5 * f / maxf) * self._idf.get(tok, 1.0)
                for tok, f in tf.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    # ── retrieval ──────────────────────────────────────────────────────────────
    def retrieve(self, query: str, k: int = 5,
                 min_score: float = 0.05,
                 quality_weight: float = 0.5) -> list[tuple[Note, float]]:
        """Return top-k notes by an outcome-weighted score:
              effective = similarity * (1 - qw + qw * note.quality)
        so a clean past design (quality≈1) outranks a flaky one (quality≈0.2) at
        equal textual similarity. Set quality_weight=0 for pure similarity."""
        qv = self._vec(query)
        scored = []
        for note in self.notes.values():
            sim = self._cosine(qv, self._vec(note.text_blob()))
            if sim < min_score:
                continue
            eff = sim * (1.0 - quality_weight + quality_weight * note.quality)
            scored.append((note, eff))
        scored.sort(key=lambda ns: -ns[1])
        return scored[:k]

    def forget_all(self) -> int:
        """Delete every design note (reset the memory). Returns count removed."""
        n = 0
        if self.dir.exists():
            for f in self.dir.glob("note_*.json"):
                try:
                    f.unlink(); n += 1
                except Exception:
                    pass
        self.notes.clear()
        self._idf = {}
        return n

    def prune_bad(self, min_quality: float = 0.5) -> int:
        """Delete anything not a genuinely-complete design — partial, failed,
        low-quality, unrealized-parts, OR legacy notes whose status was never
        finalised to 'complete' (these predate proper status recording and are
        the source of bad reuse). Returns count removed."""
        victims = [nid for nid, n in self.notes.items()
                   if n.quality < min_quality
                   or n.payload.get("status") != "complete"
                   or n.payload.get("unrealized")]
        for nid in victims:
            try:
                (self.dir / f"note_{nid}.json").unlink()
            except Exception:
                pass
            self.notes.pop(nid, None)
        if victims:
            self._rebuild_idf()
        return len(victims)

    def mark_reused(self, note_id: str) -> None:
        n = self.notes.get(note_id)
        if n:
            n.reuse_count += 1
            self._save_note(n)

    # Real-world outcome feedback — moves quality toward what actually happened,
    # not just whether the verifier passed. This is the signal that lets memory
    # prefer designs that shipped over designs that merely parsed.
    _OUTCOME_DELTA = {
        "manufactured": +0.20,   # strongest: a real board was built
        "exported":     +0.12,   # gerbers/BOM exported → user committed to it
        "accepted":     +0.06,   # user accepted the design in-app
        "rejected":     -0.30,   # user threw it away → strong negative
    }

    def record_outcome(self, note_id: str, outcome: str) -> bool:
        """Apply a real user action to a design note. Returns True if applied."""
        n = self.notes.get(note_id)
        if not n or outcome not in self._OUTCOME_DELTA:
            return False
        n.quality = max(0.0, min(1.0, n.quality + self._OUTCOME_DELTA[outcome]))
        # Keep the strongest outcome seen (manufactured > exported > accepted).
        rank = {"": 0, "accepted": 1, "exported": 2, "manufactured": 3, "rejected": -1}
        if rank.get(outcome, 0) > rank.get(n.real_outcome, 0) or outcome == "rejected":
            n.real_outcome = outcome
        self._save_note(n)
        self._rebuild_idf()
        return True

    def find_by_session(self, session_id: str) -> Note | None:
        for n in self.notes.values():
            if n.payload.get("session_id") == session_id:
                return n
        return None

    # ── write + auto-evolve ────────────────────────────────────────────────────
    def add(self, content: str, *, keywords=None, tags=None, context="",
            payload=None, quality: float = 0.5, link_threshold: float = 0.18) -> Note:
        note = Note(
            id=uuid.uuid4().hex[:10],
            created=time.time(),
            content=content,
            keywords=keywords or [],
            tags=tags or [],
            context=context,
            payload=payload or {},
            quality=max(0.0, min(1.0, quality)),
        )
        # Auto-link + EVOLVE (A-Mem): connect to similar notes, back-link, and
        # evolve the neighbour (corroboration count + refreshed context) so a
        # recurring successful pattern strengthens over time.
        self._rebuild_idf()
        nv = self._vec(note.text_blob())
        for other in self.notes.values():
            if self._cosine(nv, self._vec(other.text_blob())) >= link_threshold:
                note.links.append(other.id)
                if note.id not in other.links:
                    other.links.append(note.id)
                self._evolve(other)
                self._save_note(other)

        self.notes[note.id] = note
        self._save_note(note)
        self._rebuild_idf()
        return note

    def _evolve(self, existing: Note) -> None:
        """A-Mem evolution: a newer similar note corroborates an older one.
        Bump a corroboration counter and refresh the context to record the
        recurring pattern. Quality is NOT changed here — only design-time results
        and real outcomes (record_outcome) move quality, to avoid drift."""
        existing.corroborations += 1
        base = existing.context.split("  (corroborated")[0].rstrip()
        existing.context = f"{base}  (corroborated ×{existing.corroborations})"

    def neighbours(self, note_id: str) -> list[Note]:
        note = self.notes.get(note_id)
        if not note:
            return []
        return [self.notes[i] for i in note.links if i in self.notes]

    def stats(self) -> dict:
        return {
            "notes": len(self.notes),
            "links": sum(len(n.links) for n in self.notes.values()) // 2,
            "vocab": len(self._idf),
        }


# ── CLI smoke test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile, sys
    d = tempfile.mkdtemp()
    mem = DesignMemory(d).load()
    mem.add("6-axis BLDC robotic arm controller with FOC, DRV8353 gate drivers, "
            "STM32H7, CAN and EtherCAT, 24V bus, incremental encoders",
            keywords=["robotic-arm", "bldc", "foc", "drv8353"],
            tags=["motor", "industrial"], context="servo joint controller",
            payload={"archetype": "robotic_arm", "status": "complete"})
    mem.add("Brushed DC 4-axis arm, simple H-bridge, 12V, no feedback",
            keywords=["robotic-arm", "brushed", "hbridge"],
            tags=["motor"], context="hobby arm",
            payload={"archetype": "robotic_arm", "status": "partial"})
    mem.add("USB-C audio synthesizer, OLED, rotary encoders, PCM5102 DAC",
            keywords=["synth", "audio", "usb"], tags=["audio"],
            context="portable synth", payload={"archetype": "audio"})
    print("stats:", mem.stats())
    print("\nretrieve 'brushless robot arm motor controller':")
    for note, score in mem.retrieve("brushless robot arm motor controller", k=3):
        print(f"  {score:.3f}  {note.content[:60]}  links={len(note.links)}")
    print("\n(audio query should rank the synth note first):")
    for note, score in mem.retrieve("audio DAC synthesizer", k=2):
        print(f"  {score:.3f}  {note.content[:50]}")
    import shutil; shutil.rmtree(d)
