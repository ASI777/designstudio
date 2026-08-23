"""Layer C — Netlist verification against the Component KG.

The anti-hallucination gate.  Given a proposed netlist (pin → net assignments)
and the Component KG, it runs structural/ERC checks that geometric DRC cannot,
and returns interpretable per-pin feedback the LLM can use to self-correct
(the evaluator-optimizer loop; PCBSchemaGen-style ERC + connectivity checks).

Checks:
  1. hallucinated_pin     — netlist names a pin the component does not have
  2. ground_short         — one net carries both a power-rail pin and a ground pin
  3. power_floating       — a power-input pin sits on a signal net (or no net)
  4. ground_misconnected  — a ground pin sits on a non-ground net
  5. missing_external     — a mandatory required-external (decoupling…) is absent

Input is provider-agnostic:
  pins        : list of {"ref","pin","net"}      (pin == number OR name)
  ref_to_mpn  : { ref : mpn }                      so we can look up the KG node
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict

from . import pin_roles
from .component_kg import ComponentKG, ComponentNode


SEVERITY_ERROR = "error"
SEVERITY_WARN  = "warning"

# Multi-pin buses where a PARTIALLY-wired group signals a missing connection
# (you don't wire SDA without SCL, or USB_DP without USB_DM). Single-ended classes
# like GPIO/UART/analog are excluded — they legitimately float when unused.
_INTERFACE_ROLES = {pin_roles.SIG_I2C, pin_roles.SIG_SPI, pin_roles.SIG_USB,
                    pin_roles.SIG_DDR, pin_roles.SIG_DIFF, pin_roles.SIG_CLK}


@dataclass
class Violation:
    rule: str
    severity: str
    ref: str
    pin: str
    net: str
    message: str

    def feedback(self) -> str:
        loc = f"{self.ref}.{self.pin}" if self.pin else self.ref
        n = f" [net {self.net}]" if self.net else ""
        return f"[{self.severity.upper()}] {self.rule} @ {loc}{n}: {self.message}"


@dataclass
class VerifyResult:
    violations: list[Violation] = field(default_factory=list)
    checked_pins: int = 0
    checked_components: int = 0

    @property
    def ok(self) -> bool:
        return not any(v.severity == SEVERITY_ERROR for v in self.violations)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == SEVERITY_ERROR]

    def feedback_block(self) -> str:
        """LLM-facing, line-per-violation feedback for the refine step."""
        if not self.violations:
            return "VERIFICATION PASSED — no connectivity violations."
        lines = [f"VERIFICATION: {len(self.errors)} error(s), "
                 f"{len(self.violations) - len(self.errors)} warning(s)."]
        lines += [v.feedback() for v in self.violations]
        return "\n".join(lines)


def _is_ground_net(name: str) -> bool:
    u = (name or "").upper()
    return any(g in u for g in ("GND", "VSS", "AGND", "PGND", "VSSQ"))


def _is_power_net(name: str) -> bool:
    u = (name or "").upper()
    if _is_ground_net(name):
        return False
    return any(p in u for p in ("VCC", "VDD", "VBUS", "VIN", "VBAT", "VPP", "VDDQ",
                                "3V3", "5V", "1V8", "1V2", "2V5", "IN_12V", "V1P8",
                                "VM", "VREF", "VDRAIN"))


def _resolve_pin(comp: ComponentNode, pin_key: str):
    """Find a pin by number or name (case-insensitive)."""
    k = (pin_key or "").upper()
    for p in comp.pins:
        if p.number.upper() == k or p.name.upper() == k:
            return p
    return None


def verify(pins: list[dict], ref_to_mpn: dict[str, str],
           kg: ComponentKG) -> VerifyResult:
    res = VerifyResult()

    # Group assignments by net and by component.
    net_members: dict[str, list[tuple[str, str, str]]] = defaultdict(list)  # net -> [(ref,pin,role)]
    comp_assigned: dict[str, set[str]] = defaultdict(set)                   # ref -> {pin keys}

    seen_refs = set()
    for a in pins:
        ref, pin, net = a.get("ref", ""), str(a.get("pin", "")), a.get("net", "")
        seen_refs.add(ref)
        res.checked_pins += 1
        mpn = ref_to_mpn.get(ref)
        comp = kg.get(mpn) if mpn else None
        if comp is None:
            continue  # unknown component — can't verify against KG

        p = _resolve_pin(comp, pin)
        # 1) hallucinated pin
        if p is None:
            res.violations.append(Violation(
                "hallucinated_pin", SEVERITY_ERROR, ref, pin, net,
                f"component {mpn} has no pin '{pin}' "
                f"(valid: {sorted(comp.pin_names())[:6]}…)"))
            continue

        role = p.role
        comp_assigned[ref].add(p.number.upper())
        comp_assigned[ref].add(p.name.upper())
        if net:
            net_members[net].append((ref, p.name, role))

        # 3) power input pin on a clearly-signal net
        if pin_roles.is_power(role) and net and not _is_power_net(net):
            res.violations.append(Violation(
                "power_floating", SEVERITY_WARN, ref, p.name, net,
                f"power pin (role {role}) connected to non-power net '{net}'"))
        # 4) ground pin on a non-ground net
        if pin_roles.is_ground(role) and net and not _is_ground_net(net):
            res.violations.append(Violation(
                "ground_misconnected", SEVERITY_ERROR, ref, p.name, net,
                f"ground pin connected to non-ground net '{net}'"))
        # power pin / ground pin with NO net at all
        if (pin_roles.is_power(role) or pin_roles.is_ground(role)) and not net:
            res.violations.append(Violation(
                "power_floating", SEVERITY_ERROR, ref, p.name, "",
                f"{'ground' if pin_roles.is_ground(role) else 'power'} pin is unconnected"))

    # 2) ground short — a single net carrying both a rail pin and a ground pin
    for net, members in net_members.items():
        has_rail = any(pin_roles.is_power(r) for _, _, r in members)
        has_gnd  = any(pin_roles.is_ground(r) for _, _, r in members)
        if has_rail and has_gnd:
            rail = next((f"{ref}.{pn}" for ref, pn, r in members if pin_roles.is_power(r)), "?")
            gnd  = next((f"{ref}.{pn}" for ref, pn, r in members if pin_roles.is_ground(r)), "?")
            res.violations.append(Violation(
                "ground_short", SEVERITY_ERROR, "", "", net,
                f"net '{net}' shorts a power pin ({rail}) to a ground pin ({gnd})"))

    # 6) COMPLETENESS: every power and ground pin of each component must be wired.
    #    Catches pins that are absent from the netlist entirely (the half-wired
    #    design problem) — these would never reach checks 1-4 which only see pins
    #    that ARE in the netlist. Signal/NC pins are NOT flagged (they may
    #    legitimately float — unused GPIOs etc.).
    for ref, mpn in ref_to_mpn.items():           # ALL design components, not just
        comp = kg.get(mpn) if mpn else None        # those that appear in the netlist
        if comp is None:
            continue
        assigned = comp_assigned.get(ref, set())
        iface_wired: dict[str, int] = defaultdict(int)
        iface_unwired: dict[str, list] = defaultdict(list)
        for p in comp.pins:
            connected = (p.number.upper() in assigned or p.name.upper() in assigned)
            # (a) power/ground completeness — by NAME (high confidence) so
            #     sense/aux pins (SHA, VDRAIN…) misread as "power" aren't demanded.
            if not connected and (_is_ground_net(p.name) or _is_power_net(p.name)):
                res.violations.append(Violation(
                    "missing_connection", SEVERITY_ERROR, ref, p.name, "",
                    f"power/ground pin '{p.name}' is not connected to any net "
                    f"(every supply and ground pin must be wired)"))
            # (b) track interface-bus pins for the partial-bus check below.
            if p.role in _INTERFACE_ROLES:
                if connected:
                    iface_wired[p.role] += 1
                else:
                    iface_unwired[p.role].append(p)
        # Partial bus: some pins of an interface wired, others not → the unwired
        # ones are almost certainly missing connections. (All-unwired = unused
        # interface, not flagged; all-wired = fine.)
        for role, unwired in iface_unwired.items():
            if iface_wired.get(role, 0) > 0:
                for p in unwired:
                    res.violations.append(Violation(
                        "incomplete_interface", SEVERITY_ERROR, ref, p.name, "",
                        f"{role} bus is partially wired — '{p.name}' is unconnected "
                        f"while other {role} pins are connected"))

    # 5) missing mandatory required-externals
    for ref, mpn in ref_to_mpn.items():
        comp = kg.get(mpn) if mpn else None
        if comp is None:
            continue
        res.checked_components += 1
        for ext in comp.externals:
            if not ext.mandatory:
                continue
            # Heuristic presence test: are both endpoints of the external present
            # on this component's assigned pins?  (A real decoupling check would
            # look for the actual C across them — this flags the obvious misses.)
            endpoints = [e.upper() for e in ext.connect_between]
            assigned = comp_assigned.get(ref, set())
            if endpoints and not all(any(ep in a or a in ep for a in assigned)
                                     for ep in endpoints):
                res.violations.append(Violation(
                    "missing_external", SEVERITY_WARN, ref, "", "",
                    f"mandatory external missing/unwired: {ext.purpose} "
                    f"({ext.value}) between {ext.connect_between}"))
    return res


# ── CLI smoke test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    kg = ComponentKG().load()
    if not kg.components:
        from swarm.runtime_paths import component_fixture_dir
        kg.ingest_dir(str(component_fixture_dir())); kg.save()

    comp = next((c for c in kg.components.values() if "DRV8353" in c.mpn),
                list(kg.components.values())[0])
    mpn = comp.mpn
    print(f"verifying against {mpn}")

    gnd = comp.ground_pins()[0].name if comp.ground_pins() else "GND"
    pwr = comp.power_pins()[0].name  if comp.power_pins()  else "VM"

    # A deliberately broken netlist exercising every check.
    bad = [
        {"ref": "U1", "pin": pwr,  "net": "SIGNAL_A"},      # power on signal net
        {"ref": "U1", "pin": gnd,  "net": "VCC_3V3"},       # ground on power net
        {"ref": "U1", "pin": "NOTAPIN", "net": "GND"},      # hallucinated pin
        {"ref": "U1", "pin": pwr,  "net": "MIXED"},
        {"ref": "U1", "pin": gnd,  "net": "MIXED"},         # short: power+gnd same net
    ]
    res = verify(bad, {"U1": mpn}, kg)
    print(res.feedback_block())
    print(f"\nok={res.ok}  errors={len(res.errors)}")
