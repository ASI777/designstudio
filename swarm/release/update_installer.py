#!/usr/bin/env python3
"""Deterministic DesignStudio packages and fail-closed signed updates."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

for candidate in (Path(__file__).resolve().parents[2],
                  Path(__file__).resolve().parents[1] / "share" / "DesignStudio" / "python"):
    if (candidate / "swarm").is_dir():
        sys.path.insert(0, str(candidate))
        break

from swarm.release.signing import load_trust_store, verify_manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_package(source: Path, output: Path, version: str, platform: str,
                  epoch: int = 0) -> dict:
    if not source.is_dir():
        raise ValueError("package source must be a directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".tar", delete=False) as raw:
        raw_path = Path(raw.name)
    try:
        with tarfile.open(raw_path, "w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_symlink():
                    raise ValueError(f"package input contains a symbolic link: {path}")
                relative = path.relative_to(source)
                info = archive.gettarinfo(str(path), arcname=(PurePosixPath("DesignStudio") /
                                                               PurePosixPath(relative.as_posix())).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                if path.is_file():
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
        with raw_path.open("rb") as source_handle, output.open("wb") as output_handle:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output_handle, mtime=epoch) as compressed:
                shutil.copyfileobj(source_handle, compressed)
    finally:
        raw_path.unlink(missing_ok=True)
    return {
        "schema": "design-studio.update-manifest/1",
        "channel": "manual",
        "version": version,
        "published_utc": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "artifacts": [{"platform": platform, "filename": output.name,
                       "sha256": _sha256(output), "bytes": output.stat().st_size}],
    }


def _validated_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    if not members:
        raise ValueError("update archive is empty")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts \
                or path.parts[0] != "DesignStudio":
            raise ValueError(f"unsafe update archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise ValueError(f"unsafe update archive entry: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported update archive entry: {member.name}")
    return members


def install_update(archive_path: Path, manifest_path: Path, trust_store: Path,
                   destination: Path, platform: str, replace: bool = False) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "design-studio.update-manifest/1":
        raise ValueError("unsupported update manifest")
    verified, message = verify_manifest(manifest, load_trust_store(str(trust_store)))
    if not verified:
        raise ValueError(message)
    matches = [item for item in manifest.get("artifacts", [])
               if item.get("platform") == platform and item.get("filename") == archive_path.name]
    if len(matches) != 1:
        raise ValueError("update artifact is not uniquely declared for this platform")
    item = matches[0]
    if item.get("sha256") != _sha256(archive_path) or item.get("bytes") != archive_path.stat().st_size:
        raise ValueError("update archive digest or byte count does not match the signed manifest")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    backup = destination.with_name(f".{destination.name}.previous")
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _validated_members(archive)
            archive.extractall(staging, members=members, filter="data")
        product = staging / "DesignStudio"
        if not product.is_dir():
            raise ValueError("update archive has no DesignStudio root")
        if destination.exists():
            if not replace:
                raise ValueError("destination already exists; pass --replace to update it")
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(destination, backup)
        os.replace(product, destination)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    package = subcommands.add_parser("package")
    package.add_argument("--source", required=True)
    package.add_argument("--out", required=True)
    package.add_argument("--manifest-out", required=True)
    package.add_argument("--version", required=True)
    package.add_argument("--platform", required=True)
    package.add_argument("--epoch", type=int, default=int(os.environ.get("SOURCE_DATE_EPOCH", "0")))
    install = subcommands.add_parser("install")
    install.add_argument("--archive", required=True)
    install.add_argument("--manifest", required=True)
    install.add_argument("--trust-store", required=True)
    install.add_argument("--destination", required=True)
    install.add_argument("--platform", required=True)
    install.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.command == "package":
        manifest = build_package(Path(args.source), Path(args.out), args.version,
                                 args.platform, args.epoch)
        Path(args.manifest_out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        install_update(Path(args.archive), Path(args.manifest), Path(args.trust_store),
                       Path(args.destination), args.platform, args.replace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
