"""
SI/PI Advisor — Deterministic Rule Engine
==========================================
Implements the SiPiAdvisor.Recommend() logic from docs/si-pi-advisor.md
in Python, producing advice/1 JSON actions for SI/PI failures WITHOUT any
LLM call. This is the "ground truth" the LLM circuit advisor is held to.

Rules from si-pi-advisor.md:
  • Closed/failed channel eye + has_via_stub=True  → backdrill
  • Closed/failed eye + insertion_loss_db ≤ -10    → set_eq (Tx FFE + Rx CTLE)
  • Closed eye, neither stub nor loss-limited       → stackup / reference fix
  • Power rail worst_z_mohm > target_mohm          → add_decap at placement_hint
  • Any decap with negative ranking contribution   → move_decap
  • DDR lane negative worst_hold_ps               → length_tune (worst bit)
  • DDR lane negative worst_setup_ps              → set_eq
  • DDR margins OK but eye failing                → reference / SSN check
  • copper_layers too few for components' needs   → set_layers

These are first-class actions: the rules prefer the right physical fix over
a part swap. The output is a subset of the design-studio.advice/1 contract,
focused on SI/PI ops. It can be merged with the LLM advisor's output
(priority sort: deterministic findings first).
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any

# ── Op priority constants ─────────────────────────────────────────────────────
_P_CRIT  = 1   # blocks signal integrity sign-off
_P_HIGH  = 2   # degraded margin
_P_MED   = 3   # advisory
_P_LOW   = 4   # cosmetic / nice-to-have

# ── Thresholds from the si-pi-advisor.md rules ───────────────────────────────
_INSERTION_LOSS_LIMIT_DB = -10.0   # below this → loss-limited channel
_VIA_STUB_BACKDRILL_RULE = True     # has_via_stub=True + eye failed → backdrill
_LAYER_HEADROOM_PER_DIFF_PAIR = 4  # copper layers per high-speed diff pair group


@dataclass
class SiPiAction:
    op: str
    net: str | None = None
    mpn: str | None = None
    for_ref: str | None = None
    value: str | None = None
    reason: str = ""
    priority: int = _P_HIGH

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "op": self.op,
            "mpn": self.mpn,
            "manufacturer": None,
            "for_ref": self.for_ref,
            "net": self.net,
            "value": self.value,
            "reason": self.reason,
            "connections": [],
            "priority": self.priority,
        }.items() if v is not None or k in ("op","reason","priority","connections")}


def recommend(circuit_state: dict) -> dict:
    """
    Run the deterministic SI/PI advisor over a circuit-state JSON.
    Returns an advice/1 dict (same schema as the LLM advisor output).
    """
    actions: list[SiPiAction] = []
    risks: list[str] = []

    # ── Signal integrity: channel eyes ────────────────────────────────────────
    for si in circuit_state.get("signal_integrity", []):
        net       = si.get("net", "")
        eye_open  = si.get("eye_open", True)
        mask_pass = si.get("mask_pass", True)
        has_stub  = si.get("has_via_stub", False)
        loss_db   = si.get("insertion_loss_db", 0.0)

        if eye_open and mask_pass:
            continue  # this net is healthy

        if has_stub:
            # Rule: via stub is the primary cause → backdrill
            actions.append(SiPiAction(
                op="backdrill",
                net=net,
                value=str(si.get("stub_layer", "inner")),
                reason=(
                    f"Net '{net}': channel eye {'closed' if not eye_open else 'mask-failed'}. "
                    f"has_via_stub=True — backdrill the stub to remove the resonance. "
                    f"Insertion loss: {loss_db:.1f} dB."
                ),
                priority=_P_CRIT,
            ))
        elif loss_db <= _INSERTION_LOSS_LIMIT_DB:
            # Rule: loss-limited channel (no stub) → equalization
            actions.append(SiPiAction(
                op="set_eq",
                net=net,
                value=f"Tx_FFE+Rx_CTLE  (loss={loss_db:.1f}dB at Nyquist)",
                reason=(
                    f"Net '{net}': insertion loss {loss_db:.1f} dB ≤ {_INSERTION_LOSS_LIMIT_DB} dB limit "
                    f"— channel is loss-limited. Enable Tx FFE pre-emphasis and Rx CTLE/DFE "
                    f"to open the eye margin. No via stub present."
                ),
                priority=_P_CRIT,
            ))
        else:
            # Rule: neither stub nor loss-limited → stackup / reference plane problem
            actions.append(SiPiAction(
                op="stackup",
                net=net,
                reason=(
                    f"Net '{net}': eye {'closed' if not eye_open else 'mask-failed'} but no via stub "
                    f"and insertion loss ({loss_db:.1f} dB) is within limit. "
                    f"Likely cause: reference plane discontinuity, split plane, or "
                    f"inadequate dielectric stack. Review reference plane under the net's routing layer."
                ),
                priority=_P_HIGH,
            ))
            risks.append(f"Net '{net}': consider a reference-plane audit — "
                         "check for plane splits, antipads, or via-field return path gaps.")

    # ── Power integrity: rail impedance ───────────────────────────────────────
    for pi in circuit_state.get("power_integrity", []):
        rail       = pi.get("net", "")
        worst_z    = pi.get("worst_z_mohm", 0.0)
        target_z   = pi.get("target_mohm", 1.0)
        hint       = pi.get("placement_hint", "")
        resonances = pi.get("resonances", [])

        if worst_z <= target_z:
            continue  # rail is healthy

        # Rule: worst Z exceeds target → add_decap at the resonance antinode
        freq_hint = ""
        if resonances:
            # Pick the worst (highest Z) resonance
            worst_res = max(resonances, key=lambda r: r.get("z_mohm", 0))
            f_mhz = worst_res.get("freq_mhz", 0)
            # Decap value: C ≈ 1 / (2π × f × Z_target)
            import math
            f_hz = f_mhz * 1e6
            z_target_ohm = target_z * 1e-3
            if f_hz > 0 and z_target_ohm > 0:
                c_f = 1.0 / (2 * math.pi * f_hz * z_target_ohm)
                c_uf = c_f * 1e6
                freq_hint = (f"at {f_mhz:.0f} MHz resonance — "
                             f"suggest ~{c_uf:.2f} µF ceramic (X5R/X7R)")

        actions.append(SiPiAction(
            op="add_decap",
            net=rail,
            value=freq_hint or f"~{target_z:.0f} mΩ target",
            reason=(
                f"Rail '{rail}': worst impedance {worst_z:.1f} mΩ exceeds target {target_z:.1f} mΩ. "
                f"Add decoupling capacitor {freq_hint}. "
                + (f"Placement hint: {hint}." if hint else "Place at power delivery antinode.")
            ),
            priority=_P_CRIT,
        ))

        # Rule: move any decap with negative ranking contribution (worsening an anti-resonance)
        for decap in pi.get("decap_ranking", []):
            if decap.get("contribution_mohm", 0) < 0:
                actions.append(SiPiAction(
                    op="move_decap",
                    for_ref=decap.get("ref"),
                    net=rail,
                    reason=(
                        f"Rail '{rail}': decap {decap.get('ref')} has negative impedance contribution "
                        f"({decap.get('contribution_mohm', 0):.1f} mΩ) — it is worsening an "
                        f"anti-resonance. Move it to reduce the anti-resonance or remove it."
                    ),
                    priority=_P_HIGH,
                ))

    # ── DDR byte-lane timing margins ──────────────────────────────────────────
    for lane in circuit_state.get("ddr_lanes", []):
        lane_id     = lane.get("lane", "")
        hold_ps     = lane.get("worst_hold_ps", 0.0)
        setup_ps    = lane.get("worst_setup_ps", 0.0)
        worst_bit   = lane.get("worst_bit", "")
        eye_open    = lane.get("eye_open", True)

        if hold_ps < 0:
            # Rule: negative hold → length-tune the fast (early) bit
            actions.append(SiPiAction(
                op="length_tune",
                net=lane_id,
                value=f"lengthen '{worst_bit}' by ~{abs(hold_ps):.0f} ps",
                reason=(
                    f"DDR lane '{lane_id}': worst hold margin {hold_ps:.0f} ps (negative). "
                    f"Bit '{worst_bit}' arrives too early — add length to match byte-lane skew. "
                    f"1 ps ≈ 0.15 mm on FR-4 (εr≈4.4)."
                ),
                priority=_P_CRIT,
            ))

        if setup_ps < 0:
            # Rule: negative setup → equalization
            actions.append(SiPiAction(
                op="set_eq",
                net=lane_id,
                value=f"DQ Rx DFE  (setup margin {setup_ps:.0f} ps)",
                reason=(
                    f"DDR lane '{lane_id}': worst setup margin {setup_ps:.0f} ps (negative). "
                    f"Enable Rx DFE or increase Tx drive strength to recover setup timing."
                ),
                priority=_P_CRIT,
            ))

        if not eye_open and hold_ps >= 0 and setup_ps >= 0:
            # Rule: margins positive but eye closed → reference / SSN
            actions.append(SiPiAction(
                op="reference",
                net=lane_id,
                reason=(
                    f"DDR lane '{lane_id}': eye closed despite positive hold/setup margins. "
                    f"Likely cause: simultaneous switching noise (SSN) or reference plane gap. "
                    f"Check PDN on DDR VDD/VDDQ rails and return-path continuity."
                ),
                priority=_P_HIGH,
            ))
            risks.append(f"DDR lane '{lane_id}': closed eye with OK margins — SSN or power noise likely.")

    # ── Layer count sanity ────────────────────────────────────────────────────
    board      = circuit_state.get("board", {})
    layers     = board.get("copper_layers", 2)
    components = circuit_state.get("components", [])
    diff_pair_count = 0
    for comp in components:
        hs = comp.get("electrical", {}).get("high_speed", {})
        diff_pair_count += len(hs.get("diff_pairs", []))

    # Rough heuristic: need ≥ 1 routing layer per 4 diff pairs + 2 plane layers
    min_layers = max(2, 2 + (diff_pair_count + 3) // 4 * 2)
    if layers < min_layers:
        actions.append(SiPiAction(
            op="set_layers",
            value=str(min_layers),
            reason=(
                f"Board has {layers} copper layers but {diff_pair_count} diff pairs need "
                f"controlled-impedance routing plus reference planes. "
                f"Recommend increasing to {min_layers} layers."
            ),
            priority=_P_HIGH,
        ))

    # ── Build advice/1 response ───────────────────────────────────────────────
    actions.sort(key=lambda a: a.priority)
    summary = _build_summary(circuit_state, actions)

    return {
        "schema":  "design-studio.advice/1",
        "summary": summary,
        "actions": [a.to_dict() for a in actions],
        "risks":   risks,
        "next_datasheets_needed": [],
        "_source": "deterministic-sipi-advisor",
    }


def _build_summary(state: dict, actions: list[SiPiAction]) -> str:
    crits  = [a for a in actions if a.priority == _P_CRIT]
    highs  = [a for a in actions if a.priority == _P_HIGH]
    goal   = state.get("design_goal", "design goal")
    if not actions:
        return f"All SI/PI checks passed. No physical changes required for {goal}."
    return (
        f"{len(crits)} critical SI/PI issue(s), {len(highs)} high-priority — "
        f"{'board cannot reach sign-off' if crits else 'margin reduction present'} for {goal}. "
        f"Op breakdown: {', '.join(sorted(set(a.op for a in actions)))}."
    )


# ── MCP tool entry point ──────────────────────────────────────────────────────
def mcp_sipi_recommend(circuit_state_path: str) -> dict:
    """MCP-callable. Reads circuit-state JSON, returns deterministic advice/1."""
    with open(circuit_state_path) as f:
        state = json.load(f)
    return recommend(state)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python sipi_advisor.py <circuit-state.json> [output.json]")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        state = json.load(f)

    result = recommend(state)
    out_path = sys.argv[2] if len(sys.argv) > 2 else "-"

    text = json.dumps(result, indent=2)
    if out_path == "-":
        print(text)
    else:
        with open(out_path, "w") as f:
            f.write(text)
        print(f"Saved → {out_path}")
        print(f"Actions: {len(result['actions'])} ({sum(1 for a in result['actions'] if a['priority']==1)} critical)")
