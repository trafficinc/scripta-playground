#!/usr/bin/env python3
"""Build a native playground executable with an embedded Scripta fallback."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from package import ROOT, dependency_notices


def build(source):
    source = source.resolve()
    if not (source / "crates/scr-cli/Cargo.toml").is_file():
        raise ValueError(f"Scripta source not found at {source}; pass --scripta-source /path/to/scripta")
    environment = os.environ.copy()
    # Keep all generated files in the playground workspace.
    environment["CARGO_TARGET_DIR"] = str(ROOT / "target/interpreter")
    host_info = subprocess.check_output(["rustc", "-vV"], cwd=source, text=True)
    target = next(line.removeprefix("host: ") for line in host_info.splitlines() if line.startswith("host: "))
    if "apple-darwin" in target:
        environment["MACOSX_DEPLOYMENT_TARGET"] = "15.0"
    subprocess.run(["cargo", "build", "-p", "scr-cli", "--bin", "scripta", "--locked",
                    "--profile", "dist", "--target", target], cwd=source, env=environment, check=True)
    suffix = ".exe" if "windows" in target else ""
    interpreter = ROOT / "target/interpreter" / target / "dist" / ("scripta" + suffix)

    # Embed license texts as well, so distributing the executable preserves notices.
    notices = {"playground/LICENSE": (ROOT / "LICENSE").read_bytes(),
               "scripta/LICENSE": (source / "LICENSE").read_bytes()}
    supplements = source / "release-notices"
    for prefix, root, package_name in (("playground", ROOT, "scripta-playground"), ("scripta", source, "scr-cli")):
        notices.update({f"{prefix}/{name}": data for name, data in
                        dependency_notices(target, supplements, root=root, package_name=package_name).items()})
    notice_path = ROOT / "target/standalone-licenses.txt"
    notice_path.write_text("\n\n".join(f"===== {name} =====\n{data.decode('utf-8')}"
                                      for name, data in sorted(notices.items())), encoding="utf-8")
    environment["SCR_EMBED_INTERPRETER"] = str(interpreter)
    environment["SCR_EMBED_NOTICES"] = str(notice_path)
    environment["CARGO_TARGET_DIR"] = str(ROOT / "target")
    # Use the interpreter's pinned toolchain for both binaries.
    subprocess.run(["cargo", "build", "--manifest-path", str(ROOT / "Cargo.toml"), "--locked",
                    "--release", "--target", target, "--features", "bundled-interpreter"],
                   cwd=source, env=environment, check=True)
    destination = ROOT / "dist" / f"standalone-{target}" / ("scr-playground" + suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "target" / target / "release" / ("scr-playground" + suffix), destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{hashlib.sha256(destination.read_bytes()).hexdigest()}  {destination.name}\n", encoding="utf-8")
    print(f"\nStandalone executable: {destination}\nRun: {destination}")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripta-source", type=Path, default=ROOT.parent / "scripta")
    args = parser.parse_args()
    try:
        build(args.scripta_source)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Standalone build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
