"""Ed25519 signing boundary for release and update manifests."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)


def canonical_unsigned(manifest: dict) -> bytes:
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def public_key_base64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def key_id_from_public(public_base64: str) -> str:
    raw = base64.b64decode(public_base64, validate=True)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must contain 32 bytes")
    return "ed25519:" + hashlib.sha256(raw).hexdigest()[:24]


def sign_manifest(manifest: dict, private_key: Ed25519PrivateKey) -> dict:
    result = {key: value for key, value in manifest.items() if key != "signature"}
    material = canonical_unsigned(result)
    public = public_key_base64(private_key)
    result["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id_from_public(public),
        "public_key_base64": public,
        "signed_digest": hashlib.sha256(material).hexdigest(),
        "signature_base64": base64.b64encode(private_key.sign(material)).decode("ascii"),
    }
    return result


def verify_manifest(manifest: dict, trusted_keys: dict[str, str]) -> tuple[bool, str]:
    signature = manifest.get("signature") or {}
    if signature.get("algorithm") != "ed25519":
        return False, "release manifest is not signed with Ed25519"
    key_id = signature.get("key_id", "")
    trusted = trusted_keys.get(key_id)
    if not trusted:
        return False, f"release signing key {key_id!r} is not trusted"
    if signature.get("public_key_base64") != trusted:
        return False, "embedded release public key does not match the trust store"
    try:
        material = canonical_unsigned(manifest)
        digest = hashlib.sha256(material).hexdigest()
        if digest != signature.get("signed_digest"):
            return False, "release signed digest does not match manifest content"
        public_raw = base64.b64decode(trusted, validate=True)
        signature_raw = base64.b64decode(signature.get("signature_base64", ""), validate=True)
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature_raw, material)
        return True, "verified"
    except Exception as error:
        return False, f"release signature verification failed: {error}"


def load_trust_store(path: str | None = None) -> dict[str, str]:
    selected = path or os.environ.get("DESIGNSTUDIO_RELEASE_TRUST_STORE", "")
    if not selected:
        return {}
    value = json.loads(Path(selected).read_text(encoding="utf-8"))
    if value.get("schema") != "design-studio.release-trust/1" \
            or not isinstance(value.get("keys"), dict):
        raise ValueError("unsupported release trust store")
    for key_id, public in value["keys"].items():
        if key_id_from_public(public) != key_id:
            raise ValueError(f"trust-store key ID mismatch: {key_id}")
    return value["keys"]


def load_private_key(path: str, password: bytes | None = None) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("release signing key must be Ed25519")
    return key


def write_private_key(path: str, key: Ed25519PrivateKey) -> None:
    output = Path(path)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
