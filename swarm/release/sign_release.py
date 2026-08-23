#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

for candidate in (Path(__file__).resolve().parents[2],
                  Path(__file__).resolve().parents[1] / "share" / "DesignStudio" / "python"):
    if (candidate / "swarm").is_dir():
        sys.path.insert(0, str(candidate))
        break

from swarm.release.signing import (key_id_from_public, load_private_key, public_key_base64,
                                   sign_manifest, write_private_key)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate-key")
    generate.add_argument("--private-key", required=True)
    generate.add_argument("--trust-store", required=True)
    sign = sub.add_parser("sign")
    sign.add_argument("--private-key", required=True)
    sign.add_argument("--manifest", required=True)
    sign.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "generate-key":
        key = Ed25519PrivateKey.generate()
        write_private_key(args.private_key, key)
        public = public_key_base64(key)
        trust = {"schema": "design-studio.release-trust/1",
                 "keys": {key_id_from_public(public): public}}
        Path(args.trust_store).write_text(json.dumps(trust, indent=2) + "\n", encoding="utf-8")
        return 0
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    signed = sign_manifest(manifest, load_private_key(args.private_key))
    Path(args.out).write_text(json.dumps(signed, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
