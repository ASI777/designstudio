"""Agentic harness — deterministic workflow orchestrator.

Implements the production pattern from docs/orchestration/PRODUCTION_ORCHESTRATION_RESEARCH.md:
a **deterministic state machine** (a workflow, not an autonomous agent) with the
LLM invoked only at named phases, the Component KG + design memory + verifier
wired in, a verifier gate (evaluator-optimizer loop), per-phase checkpointing,
a budget guard, and crash-resume.

Design goals:
  • Deterministic backbone — phases run in a fixed order; control always returns
    to the harness between phases.
  • LLM at named steps only — the LLM-dependent phases (intent, netlist) are
    *injected* via PhaseProvider, so the harness is testable with stubs and the
    real agents (intent_agent, connection_agent) plug in for production.
  • Durable — every phase checkpoints the session to JSON; a crashed run resumes
    from the last completed phase.
  • Governed — a budget cap on LLM calls; the run aborts rather than overspends.

This is the *runtime* orchestrator (ships in the product); it is NOT Anti-Gravity
(that is the prototype/dev-swarm coordinator and does not ship).
"""
from __future__ import annotations
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from .component_kg import ComponentKG
from .design_memory import DesignMemory
from .verifier import verify, VerifyResult


def _pin_count(comp2: dict) -> int:
    """Number of pins in a component/2 JSON — used to detect empty extractions."""
    return len((comp2 or {}).get("symbol", {}).get("pins", []) or [])


# ── Routing (the "routing" workflow pattern) ──────────────────────────────────
def classify_intent(text: str) -> str:
    """Return one of: build | modify | ask.  Keyword router (an LLM call can
    replace this in production; the contract is the same)."""
    t = (text or "").lower()
    build = ("build", "design", "create", "make me", "i want", "i need a",
             "develop", "prototype", "pcb for", "controller for", "board for")
    modify = ("route", "drc", "pour", "place", "fix", "re-route", "reroute",
              "optimize", "production", "fit board", "add ", "source bom")
    if any(k in t for k in build):
        return "build"
    if any(k in t for k in modify):
        return "modify"
    return "ask"


# ── Session state (the durable checkpoint) ────────────────────────────────────
@dataclass
class Session:
    session_id: str
    intent_text: str
    phase: str = "start"
    intent_class: str = ""
    components: list[dict] = field(default_factory=list)   # [{ref,mpn}]
    netlist: list[dict] = field(default_factory=list)      # [{ref,pin,net}]
    similar_cases: list[str] = field(default_factory=list) # note ids
    refine_count: int = 0
    llm_calls: int = 0
    log: list[str] = field(default_factory=list)
    status: str = "running"                                # running|complete|partial|failed|aborted
    last_feedback: str = ""
    seed_netlist: list = field(default_factory=list)   # datasheet-mandated connections
    unrealized: list = field(default_factory=list)     # parts that couldn't be sourced at all
    footprint_warnings: list = field(default_factory=list)  # parts with unverified footprints
    electrical_analysis: dict = field(default_factory=dict) # incremental ERC/DC/power/stability/SPICE
    # ── retrieve→reuse (CBR) state ──────────────────────────────────────────────
    reuse_note_id: str = ""                                # best high-quality prior case
    reuse_components: list[dict] = field(default_factory=list)   # to adopt
    reuse_netlist: list[dict] = field(default_factory=list)      # to seed
    reused_components: bool = False
    reused_netlist: bool = False
    quality: float = 0.0                                   # this design's outcome signal
    note_id: str = ""                                      # stored memory note (for outcome feedback)

    def note(self, msg: str) -> None:
        self.log.append(msg)
        print(f"[harness] {msg}", flush=True)


# ── A phase = (name, fn).  fn(session, deps) -> next phase name ────────────────
PhaseFn = Callable[["Session", "Deps"], str]


@dataclass
class Deps:
    kg: ComponentKG
    memory: DesignMemory
    # Injected LLM-backed phases (real agents in prod, stubs in tests):
    propose_components: Callable[[Session], list[dict]] | None = None  # → [{ref,mpn}]
    fetch_datasheet:    Callable[[dict], dict | None] | None = None     # {ref,mpn,…} → component/2 JSON
    find_substitute:    Callable[[dict], dict | None] | None = None     # unavailable part → replacement {ref,mpn,…}
    propose_netlist:    Callable[[Session, str], list[dict]] | None = None  # (session, feedback) → [{ref,pin,net}]


class Harness:
    BUDGET_BASE      = 12       # base LLM-call budget
    BUDGET_PER_COMP  = 2        # + per component (IC datasheets scale with count)
    BUDGET_MAX       = 80       # hard ceiling
    MAX_REFINE       = 3        # evaluator-optimizer iterations
    MIN_IC_PINS      = 2        # an extraction with fewer pins is treated as failed

    def _budget(self, s: "Session") -> int:
        # Scale the budget with design size (a 30-part board legitimately needs
        # more IC datasheet extractions than a 5-part one). Passives no longer
        # cost LLM calls, so this rarely binds now.
        return min(self.BUDGET_MAX,
                   self.BUDGET_BASE + self.BUDGET_PER_COMP * len(s.components))

    def __init__(self, deps: Deps, store_dir: str | None = None):
        self.deps = deps
        self.dir = Path(store_dir or
            (Path.home() / ".config" / "product_design" / "sessions"))
        self.phases: dict[str, PhaseFn] = {
            "start":        self._p_start,
            "retrieve":     self._p_retrieve,
            "components":   self._p_components,
            "datasheet":    self._p_datasheet,
            "expand":       self._p_expand,
            "netlist":      self._p_netlist,
            "verify":       self._p_verify,
            "store":        self._p_store,
        }

    # ── checkpointing ──────────────────────────────────────────────────────────
    def _ckpt_path(self, sid: str) -> Path:
        return self.dir / f"session_{sid}.json"

    def _checkpoint(self, s: Session) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = str(self._ckpt_path(s.session_id)) + ".tmp"
        Path(tmp).write_text(json.dumps(asdict(s), indent=2))
        os.replace(tmp, self._ckpt_path(s.session_id))

    def resume(self, sid: str) -> Session | None:
        p = self._ckpt_path(sid)
        if not p.exists():
            return None
        return Session(**json.loads(p.read_text()))

    # ── driver ─────────────────────────────────────────────────────────────────
    def run(self, intent_text: str, session: Session | None = None,
            force_class: str | None = None) -> Session:
        s = session or Session(session_id=f"ds_{int(time.time())}_{uuid.uuid4().hex[:6]}",
                               intent_text=intent_text)
        if force_class:                 # explicit /build → always a build
            s.intent_class = force_class
        s.note(f"session {s.session_id} — resuming at phase '{s.phase}'"
               if session else f"session {s.session_id} started")

        guard = 0
        while s.phase not in ("done", "failed", "aborted"):
            # Budget guard: abort ONLY if over budget AND no netlist exists yet.
            # Once a netlist is produced, the remaining phases (verify/store/apply)
            # are deterministic and free — never discard a completed design.
            if s.llm_calls > self._budget(s) and not s.netlist:
                s.status = "aborted"; s.phase = "aborted"
                s.note(f"BUDGET EXCEEDED ({s.llm_calls} LLM calls, no design "
                       f"produced) — aborting")
                break
            if (guard := guard + 1) > 100:           # safety against a phase cycle bug
                s.status = "failed"; s.phase = "failed"
                s.note("phase loop guard tripped — failing")
                break

            fn = self.phases.get(s.phase)
            if fn is None:
                s.status = "failed"; s.phase = "failed"
                s.note(f"no handler for phase '{s.phase}'")
                break
            try:
                s.phase = fn(s, self.deps)
            except Exception as e:               # a phase failed — record & stop
                s.status = "failed"
                s.note(f"phase failed: {e}")
                s.phase = "failed"
            self._checkpoint(s)                   # durable: checkpoint every phase

        if s.status == "running":
            s.status = "complete"
        s.note(f"finished: status={s.status}")
        self._checkpoint(s)
        return s

    # ── phases ─────────────────────────────────────────────────────────────────
    def _p_start(self, s: Session, d: Deps) -> str:
        if not s.intent_class:          # respect a forced class (explicit /build)
            s.intent_class = classify_intent(s.intent_text)
        s.note(f"intent classified as '{s.intent_class}'")
        if s.intent_class != "build":
            s.status = s.intent_class            # 'modify' / 'ask' handled elsewhere
            s.note(f"non-build intent → handing off ('{s.intent_class}')")
            return "done"
        return "retrieve"

    REUSE_SIM_MIN     = 0.30    # only reuse when a past case is genuinely similar
    REUSE_QUALITY_MIN = 0.60    # …and was a good outcome

    def _p_retrieve(self, s: Session, d: Deps) -> str:
        hits = d.memory.retrieve(s.intent_text, k=3)   # quality-weighted
        s.similar_cases = [n.id for n, _ in hits]
        if not hits:
            s.note("no similar past designs — cold start (would use archetype)")
            return "components"

        s.note("similar past designs: " +
               "; ".join(f"{n.content[:34]} (sim*q={sc:.2f}, q={n.quality:.2f})"
                         for n, sc in hits))

        # CBR reuse: pick the best case that is both similar AND a good outcome,
        # and surface its components + verified netlist for the proposal phases.
        for note, score in hits:
            raw_sim = d.memory._cosine(d.memory._vec(s.intent_text),
                                       d.memory._vec(note.text_blob()))
            if raw_sim >= self.REUSE_SIM_MIN and note.quality >= self.REUSE_QUALITY_MIN:
                pl = note.payload or {}
                s.reuse_note_id    = note.id
                s.reuse_components = list(pl.get("components", []))
                s.reuse_netlist    = list(pl.get("netlist", []))
                s.note(f"REUSE: adopting case {note.id} "
                       f"(quality {note.quality:.2f}) — "
                       f"{len(s.reuse_components)} parts, "
                       f"{len(s.reuse_netlist)} connections to seed")
                break
        return "components"

    def _p_components(self, s: Session, d: Deps) -> str:
        # REUSE first: adopt the component set from a similar good past design,
        # saving an LLM call entirely (a real efficiency signal).
        if s.reuse_components:
            s.components = [dict(c) for c in s.reuse_components]
            s.reused_components = True
            s.note(f"reused {len(s.components)} components from case "
                   f"{s.reuse_note_id} (no LLM call)")
            return "datasheet"
        if not d.propose_components:
            raise RuntimeError("no component-proposal provider wired")
        s.llm_calls += 1
        s.components = d.propose_components(s)
        s.note(f"proposed {len(s.components)} components: "
               + ", ".join(c.get("mpn", "?") for c in s.components[:6]))
        return "datasheet"

    SUBSTITUTE_TRIES = 2     # alternatives to try before dropping a part

    def _p_datasheet(self, s: Session, d: Deps) -> str:
        # Ensure every proposed part exists in the Component KG so the netlist
        # designer has its pin structure. For each missing part:
        #   1. try its datasheet,
        #   2. if it has no datasheet OR is out of stock → SUBSTITUTE with an
        #      in-stock alternative that does (its required-externals then replace
        #      the original's automatically at netlist time),
        #   3. if no substitute works → DROP it; its datasheet-derived
        #      sub-components are never instantiated (the cascade).
        # A part is "missing" if it isn't in the KG OR is there with no pins (a
        # poisoned/empty extraction — a chip with 0 pins can't be netlisted).
        def _kg_usable(mpn: str) -> bool:
            node = d.kg.get(mpn)
            return node is not None and len(node.pins) >= self.MIN_IC_PINS
        missing = [c for c in s.components
                   if c.get("mpn") and not _kg_usable(c["mpn"])]
        if not missing:
            s.note("datasheet phase: all parts already in KG")
            return "expand"
        if not d.fetch_datasheet:
            s.note(f"datasheet phase: {len(missing)} parts missing from KG and no "
                   f"datasheet provider — netlist will be limited")
            return "expand"

        from . import passives

        added = substituted = passive_n = 0
        dropped: list[dict] = []
        for c in missing:
            # Passives (C/R/L/FB) get a deterministic IPC land pattern — no
            # datasheet extraction, no LLM call, DRC-safe footprint. Their value
            # (from the MPN) and net (from the netlist) are unaffected.
            if passives.is_passive(c):
                comp2 = passives.synth_component2(c)
                if d.kg.ingest_json(comp2):
                    try:
                        from .apply_to_board import save_component
                        save_component(comp2)        # so apply-to-board gets the footprint
                    except Exception:
                        pass
                    added += 1; passive_n += 1
                    s.note(f"  {c['mpn']} → {comp2['footprint']['name']} "
                           f"(passive, IPC land pattern, no extraction)")
                continue

            unavailable = (c.get("stock") == 0)        # out of stock → must replace
            cj = None
            if not unavailable:
                s.llm_calls += 1
                # Announce BEFORE the (slow) fetch so the live status shows which
                # part is being read during the wait.
                s.note(f"  reading datasheet for {c['mpn']}…")
                try:
                    cj = d.fetch_datasheet(c)
                except Exception as e:
                    s.note(f"  datasheet error for {c['mpn']}: {e}")
            # An extraction that yields no pins is a FAILED extraction (the ESP32
            # case) — a chip with 0 pins can't be wired. Treat it like no datasheet
            # and route to substitution, instead of poisoning the KG.
            empty_extract = bool(cj) and _pin_count(cj) < self.MIN_IC_PINS
            if cj and not empty_extract and d.kg.ingest_json(cj):
                try:
                    from .apply_to_board import save_component
                    save_component(cj)
                except Exception as exc:
                    s.note(f"  component library write failed for {c['mpn']}: {exc}")
                added += 1
                s.note(f"  extracted {c['mpn']} ({_pin_count(cj)} pins) → KG")
                continue
            if empty_extract:
                s.note(f"  {c['mpn']} extracted with only {_pin_count(cj)} pins "
                       f"— incomplete, will substitute")

            # ── Substitute the unavailable / un-extractable part ─────────────
            reason = ("out of stock" if unavailable
                      else "incomplete datasheet" if empty_extract
                      else "no datasheet")
            sub_ok = False
            if d.find_substitute:
                tried: list[str] = []
                for _ in range(self.SUBSTITUTE_TRIES):
                    sub = d.find_substitute({**c, "_tried": tried})
                    if not sub:
                        break
                    tried.append(sub.get("mpn", ""))
                    s.llm_calls += 1
                    try:
                        scj = d.fetch_datasheet(sub)
                    except Exception:
                        scj = None
                    # Substitute must also extract real pins to be accepted.
                    if scj and _pin_count(scj) >= self.MIN_IC_PINS and d.kg.ingest_json(scj):
                        try:
                            from .apply_to_board import save_component
                            save_component(scj)
                        except Exception as exc:
                            s.note(f"  component library write failed for {sub['mpn']}: {exc}")
                        s.note(f"  SUBSTITUTED {c['mpn']} → {sub['mpn']} "
                               f"({reason}); its required-externals replace the "
                               f"original's")
                        c["substituted_from"] = c["mpn"]
                        c["mpn"] = sub["mpn"]
                        if sub.get("datasheet_url"):
                            c["datasheet_url"] = sub["datasheet_url"]
                        c["stock"] = sub.get("stock")
                        added += 1
                        substituted += 1
                        sub_ok = True
                        break
            if not sub_ok:
                # Policy: only place parts with EXACT pins from a datasheet. No
                # generic package/connector footprint guessing — a part with no
                # datasheet (and no substitute) is LEFT OUT and reported, so the
                # user can supply its datasheet via /datasheets and resume.
                dropped.append(c)
                s.unrealized.append({"ref": c.get("ref", ""), "mpn": c["mpn"],
                                     "reason": reason})
                s.note(f"  NEEDS DATASHEET {c['mpn']} ({reason} on DigiKey or "
                       f"Mouser, no substitute) — left out until you provide its "
                       f"datasheet (/datasheets)")

        # Cascade: a dropped part takes its derived sub-components with it, since
        # those are instantiated from its required-externals in the expand phase.
        if dropped:
            s.components = [c for c in s.components if c not in dropped]
        if added:
            d.kg.save()
        s.note(f"datasheet phase: {added} parts in KG "
               f"({passive_n} passive land-patterns, {substituted} substituted), "
               f"{len(dropped)} left out (need datasheet)")
        return "expand"

    def _p_expand(self, s: Session, d: Deps) -> str:
        # Datasheet-driven expansion: each component pulls in the support parts its
        # datasheet mandates (decoupling caps, pull-ups, crystals…) with the exact
        # connections from `connect_between`. Sub-components get IPC passive
        # footprints; their connections seed the netlist.
        from . import component_expansion, passives
        new_components, seed = component_expansion.expand(s.components, d.kg)
        # Give each new passive a footprint (IPC land pattern) in the KG + library.
        for nc in new_components:
            if passives.is_passive(nc):
                comp2 = passives.synth_component2(nc)
                if d.kg.ingest_json(comp2):
                    try:
                        from .apply_to_board import save_component
                        save_component(comp2)
                    except Exception:
                        pass
        s.components += new_components
        s.seed_netlist = seed
        s.note(f"expand: added {len(new_components)} datasheet-required "
               f"sub-components, {len(seed)} mandated connections")
        return "netlist"

    def _p_netlist(self, s: Session, d: Deps) -> str:
        # REUSE first (initial attempt only): adapt the prior verified netlist
        # onto THIS design's components (CBR adapt — remaps ref designators by
        # MPN), then let the verifier gate validate it. If it passes, this design
        # lands in 0 refines, 0 LLM calls — "designs better with use".
        if s.refine_count == 0 and s.reuse_netlist:
            from .reuse import adapt_netlist
            r = adapt_netlist(s.reuse_netlist, s.reuse_components, s.components)
            if r.netlist and r.coverage >= 0.5:
                s.netlist = r.netlist
                s.reused_netlist = True
                s.note(f"seeded netlist from case {s.reuse_note_id} via adapt "
                       f"({len(s.netlist)} conns, coverage {r.coverage:.0%}, "
                       f"{len(r.dropped_refs)} dropped, no LLM call)")
                return "verify"
        if not d.propose_netlist:
            raise RuntimeError("no netlist-proposal provider wired")
        s.llm_calls += 1
        proposed = d.propose_netlist(s, s.last_feedback)
        # Always include the datasheet-mandated connections (the seed); the LLM
        # adds anchor↔anchor signal interconnect on top. Dedup by (ref,pin,net).
        merged, seen = [], set()
        for a in list(s.seed_netlist) + list(proposed):
            k = (a.get("ref"), str(a.get("pin")), a.get("net"))
            if k not in seen:
                seen.add(k); merged.append(a)
        s.netlist = merged
        s.note(f"proposed netlist: {len(proposed)} from LLM + "
               f"{len(s.seed_netlist)} datasheet-mandated = {len(merged)} "
               f"(refine #{s.refine_count})")
        return "verify"

    def _p_verify(self, s: Session, d: Deps) -> str:
        # Degenerate-result guard: an empty netlist is never a valid design — the
        # verifier would pass it vacuously. Refine, then fail (never store clean).
        if s.components and not s.netlist:
            if s.refine_count < self.MAX_REFINE:
                s.refine_count += 1
                s.last_feedback = ("Netlist is empty. Assign every power and ground "
                                   "pin of each component to a net.")
                s.note("empty netlist → refine")
                return "netlist"
            s.status = "failed"
            s.note("empty netlist after max refines → failed (not stored as clean)")
            return "store"

        ref_to_mpn = {c["ref"]: c["mpn"] for c in s.components}
        res: VerifyResult = verify(s.netlist, ref_to_mpn, d.kg)
        try:
            from .incremental_design import analyze_bound_design
            s.electrical_analysis = analyze_bound_design(s.components, s.netlist)
            categories = s.electrical_analysis.get("categories", {})
            summary = ", ".join(f"{name}={value.get('status')}"
                                for name, value in categories.items())
            s.note(f"incremental electrical analysis: "
                   f"{s.electrical_analysis.get('status', 'incomplete')} ({summary})")
        except Exception as exc:
            s.electrical_analysis = {"schema": "design-studio.incremental-analysis/1",
                                     "status": "fail", "error": str(exc)}
            s.note(f"incremental electrical analysis failed closed: {exc}")
        s.note(f"verify: {len(res.errors)} errors, "
               f"{len(res.violations) - len(res.errors)} warnings")
        if res.ok:
            s.note("verification PASSED")
            return "store"
        # Evaluator-optimizer: feed errors back and re-propose the netlist — but
        # only if we still have refine budget AND LLM budget. Otherwise accept the
        # current netlist as partial and STORE/apply it (don't discard a design).
        if s.refine_count < self.MAX_REFINE and s.llm_calls <= self._budget(s):
            s.refine_count += 1
            s.last_feedback = res.feedback_block()
            s.note(f"verification failed → refine (attempt {s.refine_count})")
            return "netlist"
        # Out of refine/LLM budget — accept as partial, record what's wrong.
        s.status = "partial"
        s.last_feedback = res.feedback_block()
        s.note("verification not clean (refine/budget exhausted) → partial, "
               "storing the design")
        return "store"

    def _p_store(self, s: Session, d: Deps) -> str:
        # Footprint-verification gate: flag any placed part whose footprint isn't
        # an exact datasheet-derived land pattern, so the user knows which to check
        # before fab (nothing is blocked — uncertainty is surfaced, not hidden).
        try:
            from .footprint_verify import verify_footprint
            from .apply_to_board import resolve_component
            for c in s.components:
                comp2 = resolve_component(c.get("mpn", ""))
                if not comp2:
                    continue
                v = verify_footprint(comp2)
                if v["confidence"] != "verified":
                    s.footprint_warnings.append(
                        {"ref": c.get("ref", ""), "mpn": c.get("mpn", ""),
                         "confidence": v["confidence"], "issues": v["issues"]})
            if s.footprint_warnings:
                names = ", ".join(w["mpn"] for w in s.footprint_warnings)
                s.note(f"FOOTPRINT EXTRACTION — {len(s.footprint_warnings)} part(s) have "
                       f"unverified footprints (check vs datasheet before fab): {names}")
        except Exception:
            pass

        # Parts that couldn't be sourced at all → the design isn't fully the one
        # the user asked for, so it's PARTIAL (and we tell them which parts).
        if s.unrealized and s.status not in ("failed",):
            s.status = "partial"
            names = ", ".join(u["mpn"] for u in s.unrealized)
            s.note(f"DESIGN PARTIAL — could not source: {names}. The design as "
                   f"specified is not fully possible without manual part selection.")

        # ── Outcome signal ──────────────────────────────────────────────────────
        # Clean first pass scores highest; each refine costs quality; a design we
        # had to accept as partial scores low. Reuse that lands clean scores the
        # very top (good AND cheap), so the memory learns to prefer it.
        if s.status == "failed":
            q = 0.10
        elif s.status == "partial":
            q = 0.25
        elif s.refine_count == 0:
            q = 1.0 if s.reused_netlist else 0.95
        else:
            q = max(0.40, 0.90 - 0.15 * s.refine_count)
        passed = s.status not in ("failed", "partial")
        s.quality = q

        # Pure reuse (no new design work) → don't store a duplicate note; just
        # credit the source. This stops one good design spawning many clones.
        if s.reused_netlist and s.refine_count == 0 and s.reuse_note_id:
            d.memory.mark_reused(s.reuse_note_id)
            s.note_id = s.reuse_note_id
            s.note("reused an existing design — credited the source (no duplicate stored)")
            return "done"

        final_status = "partial" if s.status == "partial" else \
                       "failed" if s.status == "failed" else "complete"
        outcome_word = ("clean" if passed and s.refine_count == 0
                        else f"{s.refine_count} refines" if passed else final_status)
        content = (f"{s.intent_text} — {len(s.components)} components, "
                   f"{len(s.netlist)} connections, {outcome_word}")
        stored = d.memory.add(
            content,
            keywords=[c.get("mpn", "") for c in s.components][:8],
            tags=[s.intent_class],
            context=s.intent_text,
            quality=q,
            payload={
                "session_id": s.session_id,
                "status": final_status,          # FINAL status, not "running"
                "components": s.components,
                "netlist": s.netlist,            # the VERIFIED netlist — reusable
                "refines": s.refine_count,
                "first_pass_clean": passed and s.refine_count == 0,
                "unrealized": [u["mpn"] for u in s.unrealized],
                "reused_from": s.reuse_note_id,
            },
        )
        s.note_id = stored.id                    # so record_outcome can target it
        if s.reused_netlist and s.reuse_note_id:
            d.memory.mark_reused(s.reuse_note_id)
        s.note(f"stored as memory note (quality {q:.2f}, status {final_status}) "
               f"— future designs can reuse it")
        return "done"


# ── CLI demo: full pipeline with stub LLM phases driven by the real KG ─────────
if __name__ == "__main__":
    kg = ComponentKG().load()
    if not kg.components:
        from swarm.runtime_paths import component_fixture_dir
        kg.ingest_dir(str(component_fixture_dir())); kg.save()
    import tempfile
    mem = DesignMemory(tempfile.mkdtemp()).load()

    # Pick a real component from the KG for the stub to "design" with.
    drv = next((c for c in kg.components.values() if "DRV8353" in c.mpn),
               list(kg.components.values())[0])
    gnd = drv.ground_pins()[0].name if drv.ground_pins() else "GND"
    pwr = drv.power_pins()[0].name  if drv.power_pins()  else "VM"

    def stub_components(s): return [{"ref": "U1", "mpn": drv.mpn}]

    # First netlist proposal is BROKEN; after feedback, the stub "fixes" it —
    # demonstrating the evaluator-optimizer refine loop closing.
    def stub_netlist(s, feedback):
        if s.refine_count == 0:
            return [{"ref": "U1", "pin": "NOTAPIN", "net": "GND"},   # hallucinated
                    {"ref": "U1", "pin": gnd, "net": "VCC_3V3"}]     # gnd on power
        # refined: correct assignments
        return [{"ref": "U1", "pin": gnd, "net": "GND"},
                {"ref": "U1", "pin": pwr, "net": "VM"}]

    deps = Deps(kg=kg, memory=mem,
                propose_components=stub_components, propose_netlist=stub_netlist)
    h = Harness(deps, store_dir=tempfile.mkdtemp())

    print("\n=== RUN 1: cold start — broken netlist self-corrects via verifier gate ===")
    s = h.run("build a BLDC robotic arm motor controller, 24V, FOC")
    print(f"\nRUN 1: status={s.status}, refines={s.refine_count}, "
          f"llm_calls={s.llm_calls}, quality={s.quality:.2f}")

    print("\n=== RUN 2: similar design — REUSES the prior verified case ===")
    s2 = h.run("build a brushless robot arm controller with FOC")
    print(f"\nRUN 2: status={s2.status}, refines={s2.refine_count}, "
          f"llm_calls={s2.llm_calls}, "
          f"reused_components={s2.reused_components}, reused_netlist={s2.reused_netlist}, "
          f"quality={s2.quality:.2f}")

    print("\n" + "=" * 64)
    print("LOOP-CLOSED SIGNAL:")
    print(f"  Run 1 (cold):   {s.refine_count} refine(s), {s.llm_calls} LLM call(s)")
    print(f"  Run 2 (reuse):  {s2.refine_count} refine(s), {s2.llm_calls} LLM call(s)")
    better = (s2.refine_count < s.refine_count) or (s2.llm_calls < s.llm_calls)
    print(f"  → second design was {'CHEAPER & CLEANER' if better else 'not better'} "
          f"by reusing memory  ✓" if better else "  → no improvement")
    print("=" * 64)

    print("\n=== RUN 3: non-build intent routes away from the pipeline ===")
    s3 = h.run("run DRC on the current board")
    print(f"intent='{s3.intent_class}', status='{s3.status}' "
          f"(correctly did NOT enter the design pipeline)")
