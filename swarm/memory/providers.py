"""Shared provider wiring for the design harness.

Both the MCP server (harness_mcp) and the Qt CLI bridge (harness_cli) build the
harness the same way: real intent + compact-KG netlist providers, with graceful
fallback. Keeping this here avoids duplicating the wiring and keeps the CLI free
of the MCP/FastMCP dependency.
"""
from __future__ import annotations
import os
import re
from pathlib import Path

from .component_kg import ComponentKG
from .design_memory import DesignMemory
from .harness import Harness, Deps, Session
from swarm.runtime_paths import component_fixture_dir

_ROOT = Path(__file__).resolve().parents[2]


def datasheet_dirs() -> list[Path]:
    """All folders searched for locally-stored datasheet PDFs (in priority order).
    The first path is mutable user data; the repository path contains immutable
    test fixtures only."""
    cands, seen, out = [], set(), []
    env = os.environ.get("DESIGNSTUDIO_DATASHEETS")
    if env:
        cands.append(Path(env))
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    cands.append(xdg_data / "designstudio" / "datasets" / "datasheets")
    cands.append(_ROOT / "testdata" / "datasheets" / "source")
    for d in cands:
        if str(d) not in seen:
            seen.add(str(d)); out.append(d)
    return out


def datasheet_dir() -> Path:
    """Primary folder (used for writing). Created if missing."""
    d = datasheet_dirs()[0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iter_pdfs():
    for d in datasheet_dirs():
        if d.exists():
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() == ".pdf":
                    yield f


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


_PDF_TEXT_CACHE: dict = {}


def _pdf_head_text(path: str, pages: int = 3) -> str:
    """First few pages of a PDF as text (cached by path+mtime). Used to identify a
    datasheet by the part number printed INSIDE it when the file name is generic."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return ""
    if key in _PDF_TEXT_CACHE:
        return _PDF_TEXT_CACHE[key]
    txt = ""
    try:
        import subprocess
        txt = subprocess.run(["pdftotext", "-l", str(pages), "-layout", path, "-"],
                             capture_output=True, text=True, timeout=25).stdout or ""
    except Exception:
        txt = ""
    if len(txt) < 50:                              # scanned/odd PDF → try PyMuPDF
        try:
            import fitz
            doc = fitz.open(path)
            txt = "\n".join(doc[i].get_text() for i in range(min(pages, len(doc))))
        except Exception:
            pass
    _PDF_TEXT_CACHE[key] = txt
    return txt


def local_datasheet(mpn: str) -> str | None:
    """Find a locally-stored PDF for an MPN. Two stages:
       1. file-name match (fast) — the part number appears in the file name;
       2. CONTENT match — the part number is printed inside the PDF text, so a
          generically-named file (e.g. 'datasheet(1).pdf') is still identified."""
    key = _norm(mpn)
    if len(key) < 4:
        return None
    # 1) file-name match (prefer the most specific name)
    best = None
    for f in _iter_pdfs():
        fn = _norm(f.stem)
        if key in fn or fn in key or (len(key) >= 6 and key[:8] in fn):
            if best is None or len(fn) > len(_norm(best.stem)):
                best = f
    if best:
        return str(best)
    # 2) content match — the MPN printed inside the datasheet. Also match on a
    #    significant prefix so a variant (RTL8153-CG ↔ RTL8153-VC-CG) still hits.
    prefix = key[:7]
    for f in _iter_pdfs():
        c = _norm(_pdf_head_text(str(f)))
        if not c:
            continue
        if key in c or (len(prefix) >= 6 and prefix in c):
            return str(f)
    return None

_KG: ComponentKG | None = None
_MEM: DesignMemory | None = None


def kg() -> ComponentKG:
    global _KG
    if _KG is None:
        _KG = ComponentKG().load()
    return _KG


def mem() -> DesignMemory:
    global _MEM
    if _MEM is None:
        _MEM = DesignMemory().load()
    return _MEM


def provider_components(session: Session) -> list[dict]:
    """intent_agent + vendor search → [{ref,mpn}]. [] (logged) on failure."""
    try:
        from swarm.agents import intent_agent
        from swarm.vendors.aggregator import Aggregator
    except Exception as e:
        session.note(f"component provider unavailable: {e}")
        return []
    try:
        intent = intent_agent.parse(session.intent_text)
        agg = Aggregator()
        comps, prefix_n = [], {}
        for spec in intent.components:
            results = agg.search(spec.search_query, limit=5)
            best = agg.best(results)
            mpn = getattr(best, "mpn", "") if best else ""
            # Carry the datasheet URL, stock, and the search query so the
            # datasheet phase can extract and (if needed) substitute.
            ds_url = getattr(best, "datasheet_url", None) if best else None
            prefix = {"mcu": "U", "regulator": "U", "connector": "J",
                      "passive": "C", "transistor": "Q"}.get(spec.role, "U")
            prefix_n[prefix] = prefix_n.get(prefix, 0) + 1
            comps.append({"ref": f"{prefix}{prefix_n[prefix]}",
                          "mpn": mpn or spec.search_query, "role": spec.role,
                          "search_query": spec.search_query,
                          "stock": getattr(best, "stock", None) if best else None,
                          "datasheet_url": ds_url})
        return comps
    except Exception as e:
        session.note(f"component proposal failed: {e}")
        return []


# A substitute must be in the same functional domain as the recommended part.
# Keyed by component role → category/description keywords that mark that domain.
_ROLE_DOMAIN = {
    "mcu": ("microcontroller", "mcu", "embedded", "cortex", "processor"),
    "regulator": ("regulator", "ldo", "voltage", "power", "pmic", "dc-dc", "converter"),
    "power_supply": ("regulator", "power", "voltage", "converter", "pmic", "supply"),
    "isolator": ("isolator", "isolation", "optocoupler"),
    "transceiver": ("transceiver", "interface", "rs-485", "rs485", "rs-232", "can",
                    "transmitter", "receiver", "phy"),
    "connector": ("connector", "terminal", "header", "socket", "jack", "receptacle"),
    "sensor": ("sensor", "detector", "transducer"),
    "amplifier": ("amplifier", "op-amp", "opamp", "comparator"),
    "memory": ("memory", "flash", "eeprom", "sram", "fram"),
    "metering": ("metering", "energy", "analog front end", "data acquisition", "afe"),
    "driver": ("driver", "gate driver", "motor"),
}

_BAD_DATASHEET = ("shopify", "usersguide", "user-guide", "user_guide", "userguide",
                  "/products/", "manual", "instructable", "/wiki", "forum", "/blog")


def _real_datasheet_url(url: str | None) -> bool:
    """Reject obvious non-datasheets (store/product-guide URLs like the Shopify
    '16-INPUTS-UsersGuide.pdf' that got swapped in for an STM32)."""
    if not url:
        return False
    u = url.lower()
    if any(b in u for b in _BAD_DATASHEET):
        return False
    return u.endswith(".pdf") or "datasheet" in u or "/document" in u or "/dl/" in u


def _tokens(s: str | None) -> set[str]:
    junk = {"ic", "ics", "integrated", "circuit", "and", "the", "of", "with", "for"}
    out = set()
    for t in re.findall(r"[a-z0-9]+", (s or "").lower()):
        if len(t) <= 2:
            continue
        if len(t) > 4 and t.endswith("s"):     # crude plural→singular (microcontrollers)
            t = t[:-1]
        if t not in junk:
            out.add(t)
    return out


def provider_substitute(component: dict) -> dict | None:
    """Find an in-stock alternative for an unavailable part — but ONLY one in the
    SAME functional domain as the recommended component (its DigiKey/Mouser
    category/description must match), with a REAL datasheet. Otherwise return None
    so the harness leaves the part out and asks the user, instead of swapping in an
    unrelated part."""
    try:
        from swarm.vendors.aggregator import Aggregator
    except Exception:
        return None
    q = component.get("search_query") or component.get("mpn", "")
    if not q:
        return None
    skip = set(component.get("_tried", []))
    skip.add(component.get("mpn", ""))
    role = (component.get("role", "") or "").lower()
    agg = Aggregator()

    # Domain of the ORIGINAL recommended part: its category from a quick lookup,
    # plus the role's domain keywords as a fallback.
    orig_tokens: set[str] = set()
    try:
        og = agg.search(component.get("mpn", ""), limit=2)
        if og:
            orig_tokens = _tokens(getattr(og[0], "category", "")) | _tokens(getattr(og[0], "description", ""))
    except Exception:
        pass
    domain_kw = _ROLE_DOMAIN.get(role, ())

    try:
        results = agg.search(q, limit=10)
    except Exception:
        return None
    for r in sorted(results, key=lambda x: -(getattr(x, "stock", 0) or 0)):
        mpn = getattr(r, "mpn", "")
        if not mpn or mpn in skip or (getattr(r, "stock", 0) or 0) <= 0:
            continue
        url = getattr(r, "datasheet_url", None)
        if not _real_datasheet_url(url):            # reject product-guide/store URLs
            continue
        cat = getattr(r, "category", "") or ""
        desc = getattr(r, "description", "") or ""
        cand_tokens = _tokens(cat) | _tokens(desc)
        text = (cat + " " + desc).lower()
        # Same domain: shares category tokens with the original, OR matches the
        # role's domain keywords. No match → not a valid substitute.
        same_domain = bool(orig_tokens & cand_tokens) or any(k in text for k in domain_kw)
        if not same_domain:
            continue
        return {"ref": component.get("ref", ""), "mpn": mpn,
                "role": component.get("role", ""), "search_query": q,
                "datasheet_url": url, "stock": getattr(r, "stock", None),
                "sub_reason": f"same domain ({cat or desc[:30]})"}
    return None


def provider_datasheet(component: dict) -> dict | None:
    """Fetch + extract a component/2 JSON for one proposed part. Tries EVERY
    vendor's datasheet (DigiKey AND Mouser) — if DigiKey lacks one, Mouser's is
    tried — until one extracts. None if no vendor has a usable datasheet."""
    mpn = component.get("mpn", "")
    if not mpn:
        return None
    try:
        from swarm.agents import datasheet_agent
    except Exception:
        return None

    # Gather candidate datasheet sources, in priority order:
    #   1. a locally-stored PDF the user downloaded into the datasheets folder
    #      (lets the user supply datasheets for parts the vendors don't carry),
    #   2. the URL carried from the component proposal,
    #   3. every vendor's datasheet URL (DigiKey + Mouser).
    urls: list[str] = []
    manufacturer = str(component.get("manufacturer") or "").strip()
    local = local_datasheet(mpn)
    if local:
        urls.append(local)
    if component.get("datasheet_url"):
        if _real_datasheet_url(component["datasheet_url"]):
            urls.append(component["datasheet_url"])
    try:
        from swarm.vendors.aggregator import Aggregator
        from swarm.vendors.base import exact_mpn_match
        for r in Aggregator().search(mpn, limit=6):       # both vendors in parallel
            if not exact_mpn_match(mpn, getattr(r, "mpn", "")):
                continue
            if not manufacturer:
                manufacturer = str(getattr(r, "manufacturer", "") or "").strip()
            u = getattr(r, "datasheet_url", None)
            if _real_datasheet_url(u) and u not in urls:
                urls.append(u)
    except Exception:
        pass

    def try_urls(candidates):
        for url in candidates:                             # try each until one works
            try:
                comp2 = datasheet_agent.process(url, mpn=mpn)
            except Exception:
                continue
            if not comp2:
                continue
            # Persist so apply-to-board can read the footprint (KG keeps only pins).
            try:
                from .apply_to_board import save_component
                save_component(comp2)
            except Exception:
                pass
            return comp2
        return None

    result = try_urls(urls)
    if result:
        return result
    # Public-web search is discovery only. Every returned URL still crosses the
    # bounded HTTPS/PDF/digest/exact-MPN trust boundary in datasheet_agent.
    try:
        from swarm.vendors.web_fallback import search_datasheets
        web_urls = [item["url"] for item in search_datasheets(
                        mpn, limit=5, manufacturer=manufacturer)
                    if item.get("url") not in urls]
        result = try_urls(web_urls)
        if result:
            result.setdefault("evidence", {})["discovery_source"] = "public-web-search"
            return result
    except Exception:
        pass
    return None


def provider_netlist(session: Session, feedback: str) -> list[dict]:
    """Compact-KG netlist designer → [{ref,pin,net}]. [] (logged) on failure."""
    try:
        from .netlist_designer import design_netlist
    except Exception as e:
        session.note(f"netlist provider unavailable: {e}")
        return []
    have = {c.get("mpn") for c in session.components}
    if any(m and kg().get(m) is None for m in have):
        kg().ingest_dir(str(component_fixture_dir())); kg().save()
    try:
        return design_netlist(session.components, kg(),
                              session.intent_text, feedback)
    except Exception as e:
        session.note(f"netlist proposal failed: {e}")
        return []


def make_harness() -> Harness:
    deps = Deps(kg=kg(), memory=mem(),
                propose_components=provider_components,
                fetch_datasheet=provider_datasheet,
                find_substitute=provider_substitute,
                propose_netlist=provider_netlist)
    return Harness(deps)
