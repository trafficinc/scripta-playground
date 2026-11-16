#!/usr/bin/env python3
"""Maintainer-only packaging. End users need neither Python nor Rust."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"aarch64-apple-darwin", "x86_64-apple-darwin", "x86_64-unknown-linux-gnu",
           "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or ":" in name or any(p in ("", ".", "..") for p in name.split("/")):
        raise ValueError("unsafe archive path")
    return name


def read_runtime(path, target):
    files = {}

    def add(name, data):
        safe_name(name)
        if name in files or len(files) >= 4096 or sum(map(len, files.values())) + len(data) > 128 * 1024 * 1024:
            raise ValueError("duplicate entry or oversized interpreter archive")
        files[name] = data

    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or entry.file_size > 128 * 1024 * 1024 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("invalid interpreter archive entry")
                add(entry.filename, archive.read(entry))
    else:
        with tarfile.open(path, "r:gz") as archive:
            for entry in archive:
                if not entry.isfile() or entry.size > 128 * 1024 * 1024:
                    raise ValueError("invalid interpreter archive entry")
                add(entry.name, archive.extractfile(entry).read())
    manifests = [name for name in files if name.endswith("/release.json")]
    if len(manifests) != 1:
        raise ValueError("expected one Scripta release manifest")
    manifest_name = manifests[0]
    prefix = manifest_name[:-len("release.json")]
    manifest = json.loads(files[manifest_name])
    if manifest["target"] != target:
        raise ValueError("interpreter target does not match package target")
    expected = {prefix + name for name in manifest["files"]} | {manifest_name}
    if expected != set(files):
        raise ValueError("interpreter inventory differs")
    for name, expected_hash in manifest["files"].items():
        safe_name(name)
        if digest(files[prefix + name]) != expected_hash:
            raise ValueError("interpreter checksum mismatch")
    suffix = ".exe" if "windows" in target else ""
    binary = manifest["binary"]
    if binary not in {"scripta" + suffix, "scr" + suffix} or digest(files[prefix + binary]) != manifest["binary_sha256"]:
        raise ValueError("interpreter identity mismatch")
    return {"runtime/" + name[len(prefix):]: data for name, data in files.items()}


def dependency_notices(target, supplements):
    metadata = json.loads(subprocess.check_output([
        "cargo", "metadata", "--locked", "--offline", "--format-version", "1", "--filter-platform", target
    ], cwd=ROOT))
    files = {}
    for package in metadata["packages"]:
        if package["name"] == "scripta-playground":
            continue
        directory = Path(package["manifest_path"]).parent
        prefix = f"third-party/{package['name']}-{package['version']}/"
        notices = [p for p in directory.rglob("*") if p.is_file() and p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))]
        if notices:
            for notice in notices:
                files[prefix + notice.relative_to(directory).as_posix()] = notice.read_bytes()
        else:
            supplement = supplements / f"{package['name']}-{package['version']}" if supplements else None
            if not supplement or not (supplement / "provenance.json").is_file():
                raise ValueError(f"missing license text for {package['name']}; pass --notices-dir with reviewed supplements")
            provenance_bytes = (supplement / "provenance.json").read_bytes()
            provenance = json.loads(provenance_bytes)
            revision = json.loads((directory / ".cargo_vcs_info.json").read_text())["git"]["sha1"]
            if (provenance["name"], provenance["version"], provenance["revision"]) != (package["name"], package["version"], revision):
                raise ValueError("supplemental notice provenance differs")
            for name, entry in provenance["files"].items():
                safe_name(name)
                data = (supplement / name).read_bytes()
                if digest(data) != entry["sha256"]:
                    raise ValueError("supplemental notice checksum mismatch")
                files[prefix + name] = data
            files[prefix + "provenance.json"] = provenance_bytes
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True, help="Verified Scripta release archive for the same target")
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--notices-dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    files = read_runtime(args.runtime, args.target)
    files.update(dependency_notices(args.target, args.notices_dir))
    windows = "windows" in args.target
    executable = "scr-playground.exe" if windows else "scr-playground"
    files[executable] = args.binary.read_bytes()
    for name in ("README.md", "LICENSE", "Cargo.lock"):
        files[name] = (ROOT / name).read_bytes()
    if windows:
        launcher = "Start Playground.cmd"
        files[launcher] = b'@echo off\r\ncd /d "%~dp0"\r\n"%~dp0scr-playground.exe"\r\nif errorlevel 1 pause\r\n'
    else:
        launcher = "Start Playground.command" if "apple" in args.target else "start-playground.sh"
        files[launcher] = b'#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\nexec ./scr-playground\n'
    files["playground-release.json"] = json.dumps({
        "target": args.target, "version": "0.1.0", "files": {name: digest(data) for name, data in sorted(files.items())}
    }, indent=2).encode()
    base = f"scripta-playground-0.1.0-{args.target}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    destination = args.out_dir / (base + (".zip" if windows else ".tar.gz"))
    executables = {executable, launcher, "runtime/scripta", "runtime/scr"}
    if windows:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(base + "/" + name)
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    else:
        with tarfile.open(destination, "w:gz") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(base + "/" + name)
                info.mode = 0o755 if name in executables else 0o644
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    destination.with_name(destination.name + ".sha256").write_text(f"{digest(destination.read_bytes())}  {destination.name}\n")
    print(destination)


if __name__ == "__main__":
    main()
