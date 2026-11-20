"""Compatibility and integrity checks for interpreter release archives."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("package", Path(__file__).resolve().parents[1] / "scripts/package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class RuntimeArchiveTests(unittest.TestCase):
    def archive(self, directory, binary, windows=False, tampered=False):
        target = "x86_64-pc-windows-msvc" if windows else "aarch64-apple-darwin"
        data = b"interpreter fixture"
        manifest = {
            "target": target, "binary": binary,
            "binary_sha256": package.digest(data), "files": {binary: package.digest(data)},
        }
        files = {binary: b"tampered" if tampered else data, "release.json": json.dumps(manifest).encode()}
        path = Path(directory) / ("runtime.zip" if windows else "runtime.tar.gz")
        if windows:
            with zipfile.ZipFile(path, "w") as archive:
                for name, contents in files.items():
                    archive.writestr("release/" + name, contents)
        else:
            with tarfile.open(path, "w:gz") as archive:
                for name, contents in files.items():
                    entry = tarfile.TarInfo("release/" + name)
                    entry.size = len(contents)
                    archive.addfile(entry, io.BytesIO(contents))
        return path, target

    def test_primary_and_legacy_executables_on_unix_and_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            for windows in (False, True):
                for name in ("scripta", "scr"):
                    binary = name + (".exe" if windows else "")
                    with self.subTest(binary=binary):
                        path, target = self.archive(directory, binary, windows)
                        files = package.read_runtime(path, target)
                        self.assertEqual(files["runtime/" + binary], b"interpreter fixture")

    def test_checksum_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, target = self.archive(directory, "scripta", tampered=True)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                package.read_runtime(path, target)

    def test_unrecognized_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, target = self.archive(directory, "other")
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                package.read_runtime(path, target)


class DependencyNoticeTests(unittest.TestCase):
    def test_runtime_and_build_notices_include_transitive_but_exclude_dev_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = []
            for name in ("scripta-playground", "runtime", "build", "transitive", "dev-only"):
                folder = root / name
                folder.mkdir()
                if name != "dev-only":
                    (folder / "LICENSE").write_text(name + " license")
                packages.append({"id": name, "name": name, "version": "1.0.0",
                                 "manifest_path": str(folder / "Cargo.toml")})

            def dependency(name, kind=None):
                return {"pkg": name, "dep_kinds": [{"kind": kind}]}

            metadata = {"packages": packages, "workspace_members": ["scripta-playground"],
                        "resolve": {"nodes": [
                            {"id": "scripta-playground", "deps": [dependency("runtime"),
                             dependency("build", "build"), dependency("dev-only", "dev")]},
                            {"id": "runtime", "deps": [dependency("transitive")]},
                            {"id": "build", "deps": []},
                            {"id": "transitive", "deps": []},
                            {"id": "dev-only", "deps": []},
                        ]}}
            with patch.object(package.subprocess, "check_output", return_value=json.dumps(metadata).encode()):
                notices = package.dependency_notices("aarch64-apple-darwin", None)
            self.assertEqual(set(notices), {f"third-party/{name}-1.0.0/LICENSE"
                                           for name in ("runtime", "build", "transitive")})
