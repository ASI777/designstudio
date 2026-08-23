#!/usr/bin/env python3
"""DigiKey (parts + datasheet) agent.

Searches the DigiKey API (via the vendor Aggregator, which also falls back to
Mouser/Octopart when their keys are set) for a manufacturer part number or
keyword, prints the top matches, downloads the primary datasheet PDF, and —
with --extract — chains the datasheet extractor to produce component/2 JSON.

Credentials (put these in ~/.config/designstudio/env, loaded by run.sh):
  DIGIKEY_CLIENT_ID, DIGIKEY_CLIENT_SECRET     (required)
  DIGIKEY_SANDBOX=1                             (optional, test catalog)

Usage: digikey_agent.py "<MPN or keywords>" [--extract]
"""
from __future__ import annotations
import os
import sys
import urllib.request
from pathlib import Path

# Make the `vendors` package importable (swarm/ is the parent of this dir).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CACHE = Path.home() / ".config" / "product_design" / "datasheets"


def _download(url: str, mpn: str) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in mpn) or "part"
    out = CACHE / f"{safe}.pdf"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(out, "wb") as f:
            f.write(r.read())
        return str(out)
    except Exception as e:
        print(f"  (datasheet download failed: {e})")
        return None


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    do_extract = "--extract" in args
    query = " ".join(a for a in args if a != "--extract").strip()
    if not query:
        print("error: no part number / keywords given"); return 1

    try:
        from vendors.aggregator import Aggregator
    except Exception as e:
        print(f"error: cannot load vendor module: {e}"); return 1

    agg = Aggregator()
    online = agg.vendors_online
    if callable(online):           # tolerate either property or method
        online = online()
    if not online:
        print("No part vendor is configured.\n"
              "Add your DigiKey API credentials to ~/.config/designstudio/env:\n"
              "  DIGIKEY_CLIENT_ID=...\n  DIGIKEY_CLIENT_SECRET=...\n"
              "then relaunch. (Mouser/Octopart keys also work.)")
        return 2

    print(f"Searching {', '.join(online)} for: {query}")
    results = agg.search(query, limit=8)
    if not results:
        print("No matches found (or the API rejected the request — check credentials).")
        return 0

    for i, r in enumerate(results[:8], 1):
        price = f"₹{r.price_inr:.2f}" if r.price_inr else "—"
        life = r.lifecycle or "?"
        print(f"{i}. {r.mpn}  [{r.manufacturer}]  {price}  {life}\n"
              f"     {r.description}")

    best = agg.best(results) or results[0]
    ds = agg.datasheet_url(results)
    print(f"\nBest match: {best.mpn} ({best.manufacturer})")
    if not ds:
        print("No datasheet URL available for these results.")
        return 0

    print(f"Datasheet: {ds}")
    pdf = _download(ds, best.mpn)
    if pdf:
        print(f"Saved datasheet → {pdf}")

    if do_extract and pdf:
        print("\nExtracting component/2 from datasheet…")
        import importlib
        ex = importlib.import_module("datasheet_extractor_agent")
        try:
            comp = ex.extract_component(pdf)
            import json
            print(json.dumps(comp, indent=2))
        except Exception as e:
            print(f"extraction failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
