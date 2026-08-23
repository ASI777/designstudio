"""Layer A — Component Knowledge Graph.

Ingests `design-studio.component/2` JSON (the datasheet-extractor output) into a
JSON-backed graph and answers structural queries used by the verifier and the
design workflow.

Graph shape (a property graph kept in one JSON file, stdlib only):

  nodes:
    component:<mpn>      {category, prefix, description}
    pin:<mpn>#<number>   {name, role, etype, domain}
    rail:<mpn>:<domain>  {vmin,vmax,vnom}
  edges:
    (component)-[:HAS_PIN]->(pin)
    (component)-[:NEEDS_EXTERNAL]->(ext spec)        # decoupling, pull-ups…
    (pin)-[:ON_RAIL]->(rail)

The compression idea from PCBSchemaGen: store the *graph* (roles, rails, required
externals) not the 16k-token datasheet prose, so a component is ~a few hundred
tokens for downstream reasoning and verification.
"""
from __future__ import annotations
import glob
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import pin_roles


@dataclass
class PinNode:
    number: str
    name: str
    role: str                # canonical role (pin_roles.*)
    etype: str               # raw electrical_type from the datasheet
    domain: str = ""         # power-domain name this pin belongs to, if any


@dataclass
class ExternalReq:
    purpose: str
    value: str
    connect_between: list[str]
    mandatory: bool = True


@dataclass
class ApplicationCircuit:
    mode: str
    connections: list[dict]
    datasheet_pages: list[int] = field(default_factory=list)
    evidence_summary: str = ""


@dataclass
class ComponentNode:
    mpn: str
    category: str
    prefix: str              # ref-des prefix (U/R/C/J…)
    description: str
    pins: list[PinNode] = field(default_factory=list)
    rails: dict[str, dict] = field(default_factory=dict)    # domain → {vmin,vmax,vnom,pins}
    externals: list[ExternalReq] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    application_circuits: list[ApplicationCircuit] = field(default_factory=list)

    # ── convenience queries ────────────────────────────────────────────────────
    def pin_names(self) -> set[str]:
        return {p.name.upper() for p in self.pins} | {p.number.upper() for p in self.pins}

    def pins_with_role(self, role: str) -> list[PinNode]:
        return [p for p in self.pins if p.role == role]

    def power_pins(self) -> list[PinNode]:
        return [p for p in self.pins if pin_roles.is_power(p.role)]

    def ground_pins(self) -> list[PinNode]:
        return [p for p in self.pins if pin_roles.is_ground(p.role)]

    def compact(self) -> dict:
        """A ~300-token representation for the netlist LLM (PCBSchemaGen-style
        compression) — the structure needed to wire it, not the datasheet prose."""
        return {
            "mpn": self.mpn,
            "category": self.category,
            "pins": [f"{p.number}:{p.name}:{p.role}" for p in self.pins],
            "rails": list(self.rails.keys()),
            "required_externals": [
                f"{e.value} between {'+'.join(e.connect_between)}"
                for e in self.externals if e.mandatory],
            "interfaces": self.interfaces,
            "application_modes": [c.mode for c in self.application_circuits],
        }


def _component_from_json(d: dict) -> ComponentNode | None:
    if not str(d.get("schema", "")).startswith("design-studio.component/"):
        return None
    comp = d.get("component", {})
    sym  = d.get("symbol", {})
    el   = d.get("electrical", {})

    node = ComponentNode(
        mpn=comp.get("mpn", "?"),
        category=comp.get("category", "other"),
        prefix=sym.get("ref_des_prefix", "U"),
        description=comp.get("description", ""),
        interfaces=[i.get("type", i) if isinstance(i, dict) else i
                    for i in (comp.get("interfaces") or [])],
    )

    # Map pin -> power domain for the ON_RAIL edges.
    pin_to_domain: dict[str, str] = {}
    for dom in (el.get("power_domains") or []):
        dname = dom.get("name", "")
        node.rails[dname] = {
            "vmin": dom.get("vmin_v"), "vmax": dom.get("vmax_v"),
            "vnom": dom.get("vnom_v") or dom.get("typ_v"),
            "pins": dom.get("pins", []),
        }
        for pn in dom.get("pins", []):
            pin_to_domain[str(pn)] = dname

    for p in sym.get("pins", []):
        num  = str(p.get("number", ""))
        name = p.get("name", num)
        role = pin_roles.normalize(name, p.get("electrical_type", ""))
        node.pins.append(PinNode(
            number=num, name=name, role=role,
            etype=p.get("electrical_type", ""),
            domain=pin_to_domain.get(num, ""),
        ))

    for ex in (el.get("required_externals") or []):
        node.externals.append(ExternalReq(
            purpose=ex.get("purpose", ""),
            value=ex.get("value", ""),
            connect_between=ex.get("connect_between", []),
            mandatory=bool(ex.get("mandatory", True)),
        ))
    for circuit in (el.get("application_circuits") or []):
        node.application_circuits.append(ApplicationCircuit(
            mode=str(circuit.get("mode") or ""),
            connections=list(circuit.get("connections") or []),
            datasheet_pages=list(circuit.get("datasheet_pages") or []),
            evidence_summary=str(circuit.get("evidence_summary") or ""),
        ))
    return node


class ComponentKG:
    def __init__(self, store_path: str | None = None):
        self.store_path = Path(store_path or
            (Path.home() / ".config" / "product_design" / "component_kg.json"))
        self.components: dict[str, ComponentNode] = {}

    # ── persistence ────────────────────────────────────────────────────────────
    def load(self) -> "ComponentKG":
        if self.store_path.exists():
            raw = json.loads(self.store_path.read_text())
            for mpn, c in raw.get("components", {}).items():
                node = ComponentNode(
                    mpn=c["mpn"], category=c["category"], prefix=c["prefix"],
                    description=c["description"], interfaces=c.get("interfaces", []),
                    rails=c.get("rails", {}),
                    pins=[PinNode(**p) for p in c.get("pins", [])],
                    externals=[ExternalReq(**e) for e in c.get("externals", [])],
                    application_circuits=[ApplicationCircuit(**item)
                                          for item in c.get("application_circuits", [])],
                )
                self.components[mpn] = node
        return self

    def save(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        out = {"components": {mpn: asdict(c) for mpn, c in self.components.items()}}
        tmp = str(self.store_path) + ".tmp"
        Path(tmp).write_text(json.dumps(out, indent=2))
        os.replace(tmp, self.store_path)

    # ── ingestion ──────────────────────────────────────────────────────────────
    def ingest_json(self, d: dict) -> str | None:
        node = _component_from_json(d)
        if node is None:
            return None
        self.components[node.mpn] = node
        return node.mpn

    def ingest_file(self, path: str) -> str | None:
        try:
            return self.ingest_json(json.loads(Path(path).read_text()))
        except Exception:
            return None

    def ingest_dir(self, dir_path: str) -> list[str]:
        added = []
        for f in sorted(glob.glob(os.path.join(dir_path, "*.json"))):
            mpn = self.ingest_file(f)
            if mpn:
                added.append(mpn)
        return added

    # ── queries ────────────────────────────────────────────────────────────────
    def get(self, mpn: str) -> ComponentNode | None:
        return self.components.get(mpn)

    def find_by_category(self, category: str) -> list[ComponentNode]:
        return [c for c in self.components.values() if c.category == category]

    def stats(self) -> dict:
        roles: dict[str, int] = {}
        for c in self.components.values():
            for p in c.pins:
                roles[p.role] = roles.get(p.role, 0) + 1
        return {
            "components": len(self.components),
            "pins": sum(len(c.pins) for c in self.components.values()),
            "roles": dict(sorted(roles.items(), key=lambda kv: -kv[1])),
        }


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from swarm.runtime_paths import component_library_dir
    src = sys.argv[1] if len(sys.argv) > 1 else str(component_library_dir())
    kg = ComponentKG().load()
    added = kg.ingest_dir(src)
    kg.save()
    print(f"[component-kg] ingested {len(added)} components from {src}")
    s = kg.stats()
    print(f"[component-kg] store now: {s['components']} components, {s['pins']} pins")
    print(f"[component-kg] top roles: "
          + ", ".join(f"{k}={v}" for k, v in list(s['roles'].items())[:8]))
