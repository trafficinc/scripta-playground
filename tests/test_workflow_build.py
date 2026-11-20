"""Manual build input validation, artifact integrity, and publication boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("workflow_build", Path(__file__).resolve().parents[1] / "scripts/workflow_build.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class VersionTests(unittest.TestCase):
    def test_accepts_semver_and_rejects_shell_or_tag_inputs(self):
        for version in ("0.1.0", "1.2.3-rc.1"):
            self.assertEqual(workflow.version_input(version, "Build only"), version)
        for version in ("v1.2.3", "1.2", "01.2.3", "1.2.3-01", "$(echo bad)", "1.2.3\nother", "../release"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                workflow.version_input(version, "Build only")
        with self.assertRaisesRegex(ValueError, "Prerelease"):
            workflow.version_input("1.2.3-rc.1", "Release")

    def test_version_stamping_preserves_dependency_versions_and_checksums(self):
        for newline in ("\n", "\r\n"):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                manifest = '[package]\nname = "scripta-playground"\nversion = "0.1.0"\n\n[dependencies]\nexample = "1.0.0"\n'
                lock = 'version = 4\n\n[[package]]\nname = "dependency"\nversion = "1.0.0"\nchecksum = "abc123"\n\n[[package]]\nname = "scripta-playground"\nversion = "0.1.0"\n'
                (root / "Cargo.toml").write_bytes(manifest.replace("\n", newline).encode())
                (root / "Cargo.lock").write_bytes(lock.replace("\n", newline).encode())
                workflow.stamp_version(root, "2.3.4")
                self.assertEqual((root / "Cargo.toml").read_text(), manifest.replace('version = "0.1.0"', 'version = "2.3.4"'))
                self.assertEqual((root / "Cargo.lock").read_text(), lock.replace('version = "0.1.0"', 'version = "2.3.4"'))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.inputs = self.root / "inputs"
        self.output = self.root / "assets"
        self.version = "1.2.3"
        self.environment = patch.dict(os.environ, {"GITHUB_SHA": "a" * 40, "BUILD_SCRIPTA_SHA": "b" * 40,
                                                  "GITHUB_REPOSITORY": "example/playground"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for target in workflow.TARGETS:
            folder = self.inputs / target
            folder.mkdir(parents=True)
            asset = folder / workflow.asset_name(self.version, target)
            asset.write_bytes(target.encode())
            record = {"version": self.version, "target": target, "playground_sha": "a" * 40,
                      "scripta_sha": "b" * 40, "asset": asset.name, "sha256": workflow.digest(asset),
                      "binary_sha256": "c" * 64, "checks": sorted(workflow.CHECKS)}
            (folder / "build.json").write_text(json.dumps(record))

    def assemble(self):
        workflow.assemble(self.inputs, self.output, self.version)

    def test_all_four_platforms_assemble_and_preserve_checksums(self):
        self.assemble()
        self.assertEqual(len(workflow.verify_assets(self.output, self.version)), 6)

    def test_missing_platform_and_changed_source_are_rejected(self):
        path = next(self.inputs.glob('*/build.json'))
        record = json.loads(path.read_text())
        path.unlink()
        with self.assertRaisesRegex(ValueError, "all four"):
            self.assemble()
        record['scripta_sha'] = 'd' * 40
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.assemble()

    def test_tampered_native_and_saved_assets_are_rejected(self):
        path = next(self.inputs.glob('*/build.json'))
        record = json.loads(path.read_text())
        asset = path.parent / record['asset']
        original = asset.read_bytes()
        asset.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.assemble()
        asset.write_bytes(original)
        self.assemble()
        (self.output / record['asset']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, "checksum"):
            workflow.verify_assets(self.output, self.version)

    def test_build_only_and_existing_tags_cannot_publish(self):
        self.assemble()
        with patch.object(workflow, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Build only'):
                workflow.publish(self.output, self.version, 'Build only')
            run.assert_not_called()
        tags = [[{'ref': 'refs/tags/v1.2.3'}]]
        with patch.object(workflow.subprocess, 'check_output', return_value=json.dumps(tags)), patch.object(workflow, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'already exists'):
                workflow.publish(self.output, self.version, 'Release')
            run.assert_not_called()

    def test_failed_upload_never_publishes_the_draft(self):
        self.assemble()
        def fail_upload(command):
            if command[2] == 'upload':
                raise workflow.subprocess.CalledProcessError(1, command)
        with patch.object(workflow.subprocess, 'check_output', return_value='[[]]'), patch.object(workflow, 'run', side_effect=fail_upload) as run:
            with self.assertRaises(workflow.subprocess.CalledProcessError):
                workflow.publish(self.output, self.version, 'Release')
            self.assertEqual([call.args[0][2] for call in run.call_args_list], ['create', 'upload'])

    def test_missing_checks_and_tampered_checksum_manifest_are_rejected(self):
        path = next(self.inputs.glob('*/build.json'))
        record = json.loads(path.read_text())
        record['checks'].remove('bundled-http')
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'validation'):
            self.assemble()
        record['checks'] = sorted(workflow.CHECKS)
        path.write_text(json.dumps(record))
        self.assemble()
        (self.output / 'SHA256SUMS').write_text('wrong manifest')
        with self.assertRaisesRegex(ValueError, 'checksum manifest'):
            workflow.verify_assets(self.output, self.version)

    def test_release_is_a_draft_until_assets_upload_and_prerelease_flag_is_explicit(self):
        self.assemble()
        with patch.object(workflow.subprocess, 'check_output', return_value='[[]]'), patch.object(workflow, 'run') as run:
            workflow.publish(self.output, self.version, 'Prerelease')
            calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual([command[2] for command in calls], ['create', 'upload', 'edit'])
        self.assertIn('--draft', calls[0])
        self.assertIn('--prerelease', calls[0])
        self.assertIn('a' * 40, calls[0])
        self.assertNotIn('--clobber', calls[1])
        self.assertIn('--draft=false', calls[-1])
