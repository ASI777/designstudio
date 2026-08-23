#!/usr/bin/env python3
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from swarm.release.signing import key_id_from_public, public_key_base64, sign_manifest
from swarm.release.update_installer import build_package, install_update


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    source = root / "prefix"
    (source / "bin").mkdir(parents=True)
    (source / "bin" / "DesignStudio").write_bytes(b"verified executable\n")
    package = root / "designstudio-1.0-linux-x86_64.tar.gz"
    unsigned = build_package(source, package, "1.0.0", "linux-x86_64", 123456789)
    duplicate = root / "duplicate.tar.gz"
    duplicate.write_bytes(package.read_bytes())
    assert build_package(source, duplicate, "1.0.0", "linux-x86_64", 123456789)["artifacts"][0]["sha256"] \
        == unsigned["artifacts"][0]["sha256"]

    key = Ed25519PrivateKey.generate()
    public = public_key_base64(key)
    trust = root / "trust.json"
    trust.write_text(json.dumps({"schema": "design-studio.release-trust/1",
                                 "keys": {key_id_from_public(public): public}}))
    manifest = root / "update.json"
    manifest.write_text(json.dumps(sign_manifest(unsigned, key)))
    destination = root / "installed"
    install_update(package, manifest, trust, destination, "linux-x86_64")
    assert (destination / "bin" / "DesignStudio").read_bytes() == b"verified executable\n"

    package.write_bytes(package.read_bytes() + b"tamper")
    try:
        install_update(package, manifest, trust, root / "tampered", "linux-x86_64")
        raise AssertionError("tampered package was installed")
    except ValueError as error:
        assert "digest" in str(error)

    evil = root / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as archive:
        info = tarfile.TarInfo("../escape")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"evil"))
    evil_payload = dict(unsigned)
    evil_payload["artifacts"] = [{"platform": "linux-x86_64", "filename": evil.name,
                                  "sha256": __import__("hashlib").sha256(evil.read_bytes()).hexdigest(),
                                  "bytes": evil.stat().st_size}]
    manifest.write_text(json.dumps(sign_manifest(evil_payload, key)))
    try:
        install_update(evil, manifest, trust, root / "escaped", "linux-x86_64")
        raise AssertionError("path-traversal package was installed")
    except ValueError as error:
        assert "unsafe" in str(error)
    assert not (root / "escape").exists()

print("SIGNED_UPDATE_INSTALLER_OK")
