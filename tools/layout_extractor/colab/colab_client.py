#!/usr/bin/env python3
"""Client for the Colab DeepSeek-OCR + Qwen2.5-VL-3B extractor.

POSTs a datasheet PDF to the Colab /extract endpoint and saves the returned
design-studio.component/2 JSON into the user's external component library. Invoked by the
DesignStudio Components panel "Add Datasheet -> Extract Footprint" action; its
stdout streams into the chat.

Endpoint URL resolution (first found wins):
  1. $DS_COLAB_ENDPOINT
  2. ~/.config/DesignStudio/colab_endpoint.txt
"""
import os, sys, json, uuid, pathlib, urllib.request, urllib.error


def _endpoint():
    u = os.environ.get("DS_COLAB_ENDPOINT")
    if u:
        return u.strip().rstrip("/")
    cfg = pathlib.Path.home() / ".config" / "DesignStudio" / "colab_endpoint.txt"
    if cfg.exists():
        return cfg.read_text().strip().rstrip("/")
    return None


def _post_pdf(endpoint, pdf):
    """multipart/form-data POST using only the stdlib (no requests dependency)."""
    boundary = "----ds" + uuid.uuid4().hex
    data = pathlib.Path(pdf).read_bytes()
    name = os.path.basename(pdf).encode()
    body = (b"--" + boundary.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="pdf"; filename="' + name + b'"\r\n'
            b"Content-Type: application/pdf\r\n\r\n" + data + b"\r\n"
            b"--" + boundary.encode() + b"--\r\n")
    req = urllib.request.Request(
        endpoint + "/extract", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())


def main():
    if len(sys.argv) < 2:
        print("usage: colab_client.py <datasheet.pdf>")
        sys.exit(2)
    pdf = sys.argv[1]
    ep = _endpoint()
    if not ep:
        print("No Colab endpoint configured.")
        print("  1. Run tools/layout_extractor/colab/datasheet_extract_colab.py on a")
        print("     Colab T4 — it prints  ENDPOINT: https://xxxx.ngrok-free.app")
        print("  2. Put that URL in  $DS_COLAB_ENDPOINT  or")
        print("     ~/.config/DesignStudio/colab_endpoint.txt")
        sys.exit(1)

    print(f"Extracting {os.path.basename(pdf)} via DeepSeek-OCR + Qwen2.5-VL-3B …", flush=True)
    print(f"  endpoint: {ep}", flush=True)
    try:
        comp = _post_pdf(ep, pdf)
    except urllib.error.URLError as e:
        print(f"Could not reach the Colab endpoint ({e}). Is the notebook still running?")
        sys.exit(1)

    if comp.get("error"):
        print("Extraction error:", comp["error"])
        if comp.get("raw"):
            print(comp["raw"])
        sys.exit(1)

    mpn = (comp.get("component", {}) or {}).get("mpn") or pathlib.Path(pdf).stem
    configured = os.environ.get("DESIGNSTUDIO_LIBRARY_ROOT")
    xdg_data = pathlib.Path(os.environ.get(
        "XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share"))
    out_dir = pathlib.Path(configured).expanduser() if configured else \
        xdg_data / "designstudio" / "datasets" / "components"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{mpn}.json"
    dest.write_text(json.dumps(comp, indent=2))

    pads = (comp.get("footprint", {}) or {}).get("pads", [])
    for w in (comp.get("extraction", {}) or {}).get("warnings", []):
        print("  ⚠  " + str(w))
    print(f"Saved → {dest}  ({len(pads)} pads)")


if __name__ == "__main__":
    main()
