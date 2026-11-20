#!/usr/bin/env python3
"""Manual Actions build, validation, and release helpers. No publication by default."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"aarch64-apple-darwin", "x86_64-unknown-linux-gnu",
           "aarch64-unknown-linux-gnu", "x86_64-pc-windows-msvc"}
CHECKS = {"rust-tests", "bundled-tests", "clippy", "python-tests", "bundled-http",
          "installed-http", "version", "licenses"}


def version_input(value, option):
    value = value.strip()
    number = r"(?:0|[1-9][0-9]*)"
    if not re.fullmatch(rf"{number}\.{number}\.{number}(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", value):
        raise ValueError("version must be X.Y.Z or X.Y.Z-rc.N, without a leading v")
    if "-" in value:
        for identifier in value.split("-", 1)[1].split("."):
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError("numeric prerelease identifiers cannot have leading zeros")
    if option not in {"Build only", "Prerelease", "Release"}:
        raise ValueError("unknown release option")
    if option == "Release" and "-" in value:
        raise ValueError("choose Prerelease for a version with a prerelease suffix")
    return value


def run(command, cwd=ROOT, environment=None):
    subprocess.run([str(part) for part in command], cwd=cwd, env=environment, check=True)


def revision(directory):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=directory, text=True).strip()


def stamp_version(root, version):
    """Set only the application version in the ephemeral runner checkout."""
    manifest = root / "Cargo.toml"
    text, count = re.subn(r'(\[package\]\s*\nname = "scripta-playground"\s*\nversion = ")[^"]+("\s*\n)',
                          lambda match: match[1] + version + match[2], manifest.read_text(), count=1)
    if count != 1:
        raise ValueError("could not locate playground version in Cargo.toml")
    lock = root / "Cargo.lock"
    lock_text, count = re.subn(r'(\[\[package\]\]\s*\nname = "scripta-playground"\s*\nversion = ")[^"]+("\s*\n)',
                               lambda match: match[1] + version + match[2], lock.read_text(), count=1)
    if count != 1:
        raise ValueError("could not locate playground version in Cargo.lock")
    manifest.write_text(text, encoding="utf-8")
    lock.write_text(lock_text, encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_name(version, target):
    extension = ".exe" if "windows" in target else ".tar.gz"
    return f"scripta-playground-{version}-{target}{extension}"


def prepare(source, version):
    sha = revision(source)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"version={version}\nscripta_sha={sha}\n")
    print(f"Playground {version}; Scripta commit {sha}")


def native(source, target, version):
    if target not in TARGETS:
        raise ValueError("unsupported native target")
    source = source.resolve()
    source_sha = revision(source)
    playground_sha = revision(ROOT)
    if source_sha != os.environ["BUILD_SCRIPTA_SHA"] or playground_sha != os.environ["GITHUB_SHA"]:
        raise ValueError("checkout revision differs from the requested build")
    host = subprocess.check_output(["rustc", "-vV"], cwd=source, text=True)
    if f"host: {target}" not in host.splitlines():
        raise ValueError("runner architecture differs from the native target")
    stamp_version(ROOT, version)
    environment = os.environ.copy()
    environment["SCR_BUILD_COMMIT"] = source_sha
    run([sys.executable, ROOT / "scripts/build-standalone.py", "--scripta-source", source], environment=environment)
    suffix = ".exe" if "windows" in target else ""
    binary = ROOT / "dist" / f"standalone-{target}" / ("scr-playground" + suffix)
    interpreter = ROOT / "target/interpreter" / target / "dist" / ("scripta" + suffix)
    environment.update({"SCR_TEST_BIN": str(interpreter), "SCR_EMBED_INTERPRETER": str(interpreter),
                        "SCR_EMBED_NOTICES": str(ROOT / "target/standalone-licenses.txt"),
                        "CARGO_TARGET_DIR": str(ROOT / "target")})
    if "apple" in target:
        environment["MACOSX_DEPLOYMENT_TARGET"] = "15.0"
    for command in (
        ["cargo", "fmt", "--manifest-path", ROOT / "Cargo.toml", "--all", "--", "--check"],
        ["cargo", "test", "--manifest-path", ROOT / "Cargo.toml", "--locked", "--target", target],
        ["cargo", "test", "--manifest-path", ROOT / "Cargo.toml", "--locked", "--target", target, "--features", "bundled-interpreter"],
        ["cargo", "clippy", "--manifest-path", ROOT / "Cargo.toml", "--locked", "--target", target,
         "--all-targets", "--all-features", "--", "-D", "warnings"],
    ):
        run(command, cwd=source, environment=environment)
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    actual_version = subprocess.check_output([str(binary), "--version"], text=True).strip()
    if actual_version != f"Scripta Playground {version}":
        raise ValueError("executable version differs from requested version")
    notices = subprocess.check_output([str(binary), "--licenses"], text=True)
    if not all(marker in notices for marker in ("scripta/LICENSE", "playground/LICENSE", "third-party/")):
        raise ValueError("standalone license notices missing")
    with tempfile.TemporaryDirectory() as folder:
        isolated = Path(folder) / binary.name
        shutil.copy2(binary, isolated)
        smoke_environment = environment.copy()
        smoke_environment.pop("SCR_BIN", None)
        smoke_environment["PATH"] = ""
        smoke = [sys.executable, ROOT / "tests/http_smoke.py", "--binary", isolated, "--expect-interpreter"]
        run([*smoke, "bundled"], environment=smoke_environment)
        smoke_environment["PATH"] = str(interpreter.parent)
        run([*smoke, "installed"], environment=smoke_environment)
    destination = ROOT / "dist/workflow" / target
    destination.mkdir(parents=True, exist_ok=True)
    asset = destination / asset_name(version, target)
    if suffix:
        shutil.copy2(binary, asset)
    else:
        data = binary.read_bytes()
        with tarfile.open(asset, "w:gz") as archive:
            entry = tarfile.TarInfo("scr-playground")
            entry.mode, entry.size = 0o755, len(data)
            archive.addfile(entry, io.BytesIO(data))
    record = {"version": version, "target": target, "playground_sha": playground_sha,
              "scripta_sha": source_sha, "asset": asset.name, "sha256": digest(asset),
              "binary_sha256": digest(binary), "checks": sorted(CHECKS)}
    (destination / "build.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def verify_record(record, version, target):
    if (record["version"], record["target"], record["playground_sha"], record["scripta_sha"]) != (
            version, target, os.environ["GITHUB_SHA"], os.environ["BUILD_SCRIPTA_SHA"]):
        raise ValueError("build provenance differs from this workflow run")
    if record["asset"] != asset_name(version, target) or set(record["checks"]) != CHECKS:
        raise ValueError("unexpected asset name or missing native validation")


def assemble(inputs, output, version):
    records = {}
    for path in inputs.glob("*/build.json"):
        record = json.loads(path.read_text())
        target = record["target"]
        if target not in TARGETS or target in records:
            raise ValueError("duplicate or unexpected native target")
        verify_record(record, version, target)
        asset = path.parent / record["asset"]
        if digest(asset) != record["sha256"]:
            raise ValueError("native asset checksum mismatch")
        records[target] = (record, asset)
    if set(records) != TARGETS:
        raise ValueError("all four native targets must pass before assembly")
    output.mkdir(parents=True, exist_ok=True)
    for record, asset in records.values():
        shutil.copy2(asset, output / record["asset"])
    (output / "build.json").write_text(json.dumps([records[target][0] for target in sorted(records)], indent=2) + "\n", encoding="utf-8")
    (output / "SHA256SUMS").write_text("".join(f"{record['sha256']}  {record['asset']}\n"
        for record, _ in sorted(records.values(), key=lambda pair: pair[0]["asset"])), encoding="utf-8")


def verify_assets(output, version):
    records = json.loads((output / "build.json").read_text())
    if len(records) != len(TARGETS) or {record["target"] for record in records} != TARGETS:
        raise ValueError("saved build does not contain all four platforms")
    for record in records:
        verify_record(record, version, record["target"])
        if digest(output / record["asset"]) != record["sha256"]:
            raise ValueError("saved asset checksum mismatch")
    expected = "".join(f"{record['sha256']}  {record['asset']}\n" for record in sorted(records, key=lambda item: item["asset"]))
    if (output / "SHA256SUMS").read_text() != expected:
        raise ValueError("saved checksum manifest differs")
    expected_files = {record["asset"] for record in records} | {"build.json", "SHA256SUMS"}
    if {path.name for path in output.iterdir()} != expected_files:
        raise ValueError("unexpected files in release assets")
    return sorted(output.iterdir())


def publish(output, version, option):
    if option == "Build only":
        raise ValueError("Build only cannot publish a release")
    assets = verify_assets(output, version)
    tag = f"v{version}"
    repository = os.environ["GITHUB_REPOSITORY"]
    # GitHub refuses an existing release; never upload over its assets or move tags.
    # Also reject tags that were created outside this workflow.
    tags = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp",
        f"repos/{repository}/git/matching-refs/tags/{tag}"], text=True))
    if any(ref["ref"] == f"refs/tags/{tag}" for page in tags for ref in page):
        raise ValueError(f"tag {tag} already exists; choose a new version")
    notes = (f"Standalone Scripta Playground {version}.\n\n"
             f"Playground source: {os.environ['GITHUB_SHA']}\n\n"
             f"Scripta source: {os.environ['BUILD_SCRIPTA_SHA']}\n\n"
             "macOS/Linux: extract the archive and run `./scr-playground`.\n"
             "Windows: run the downloaded `.exe`.\n\n"
             "Scripta and license notices are embedded; use `--licenses` to view them.\n"
             "macOS requires Apple Silicon and macOS 15+. Executables are unsigned.\n")
    with tempfile.TemporaryDirectory() as folder:
        notes_file = Path(folder) / "notes.md"
        notes_file.write_text(notes, encoding="utf-8")
        command = ["gh", "release", "create", tag, "--repo", repository, "--target", os.environ["GITHUB_SHA"],
                   "--title", f"Scripta Playground {version}", "--notes-file", notes_file, "--draft"]
        if option == "Prerelease":
            command.append("--prerelease")
        run(command)
    run(["gh", "release", "upload", tag, "--repo", repository, *assets])
    run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "native", "assemble", "publish", "summary"))
    parser.add_argument("--scripta-source", type=Path, default=ROOT.parent / "scripta")
    parser.add_argument("--target", choices=sorted(TARGETS))
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        option = os.environ["BUILD_RELEASE_OPTION"]
        version = version_input(os.environ["BUILD_VERSION"], option)
        if args.command == "prepare":
            prepare(args.scripta_source, version)
        elif args.command == "native":
            native(args.scripta_source, args.target, version)
        elif args.command == "assemble":
            assemble(args.inputs, args.output, version)
        elif args.command == "publish":
            publish(args.output, version, option)
        else:
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write(f"Playground **{version}**: [Download all platforms]({os.environ['ARTIFACT_URL']})\n\n"
                              f"Release option: **{option}**. All four native builds and tests passed.\n")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Workflow failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
