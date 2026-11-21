"""Unit tests for the explicit wake-word asset provenance gate."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "wake_word_training"
    / "asset_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("minder_asset_manifest", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _asset(file_path: Path, **overrides):
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    record = {
        "asset_id": "validation-features-v1",
        "asset_type": "precomputed_features",
        "source_url": "https://huggingface.co/example/dataset",
        "source_revision": "0123456789abcdef0123456789abcdef01234567",
        "relative_path": "validation.npy",
        "sha256": digest,
        "license_id": "CC-BY-NC-SA-4.0",
        "attribution": "Example attribution text",
        "restrictions": ["Non-commercial use only"],
        "review_status": "approved",
        "reviewed_by": "maintainer",
        "reviewed_at": "2026-11-21",
        "approved_uses": ["model_training"],
        "derived_from": [],
    }
    record.update(overrides)
    return record


class AssetManifestTests(unittest.TestCase):
    def _workspace(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "validation.npy").write_bytes(b"small synthetic fixture")
        manifest = root / "manifest.json"
        payload = {"manifest_version": 1, "assets": [_asset(root / "validation.npy")]}
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        return root, manifest, payload

    def test_rejects_non_integer_manifest_version(self):
        root, manifest, payload = self._workspace()
        payload["manifest_version"] = 1.0
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "manifest_version"):
            MODULE.validate_manifest(manifest, root)

    def test_accepts_human_approved_asset_with_matching_hash(self):
        root, manifest, _ = self._workspace()
        self.assertEqual(
            MODULE.validate_manifest(manifest, root),
            ["validation-features-v1"],
        )

    def test_rejects_pending_rights_review(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["review_status"] = "pending"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "not approved"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_asset_without_explicit_training_approval(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["approved_uses"] = ["local_testing"]
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "model_training"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_hash_mismatch(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "SHA-256 mismatch"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_non_https_source(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["source_url"] = "http://example.com/audio.wav"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "HTTPS URL"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_windows_separator_in_portable_path(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["relative_path"] = r"nested\\..\\outside.wav"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "relative POSIX path"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_windows_drive_path_on_non_windows_host(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["relative_path"] = "C:/temp/validation.npy"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "relative POSIX path"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_path_traversal(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["relative_path"] = "../outside.wav"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "escape the asset root"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_missing_attribution_and_license(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["license_id"] = ""
        payload["assets"][0]["attribution"] = ""
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(MODULE.ManifestError) as caught:
            MODULE.validate_manifest(manifest, root)
        self.assertIn("license_id", str(caught.exception))
        self.assertIn("attribution", str(caught.exception))

    def test_rejects_unknown_provenance_parent(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["derived_from"] = ["missing-source"]
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "unknown derived_from"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_provenance_cycles(self):
        root, manifest, payload = self._workspace()
        child = dict(payload["assets"][0])
        child["asset_id"] = "derived-background"
        child["relative_path"] = "validation.npy"
        child["derived_from"] = ["validation-features-v1"]
        payload["assets"][0]["derived_from"] = ["derived-background"]
        payload["assets"].append(child)
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "Provenance cycle"):
            MODULE.validate_manifest(manifest, root)

    def test_rejects_duplicate_asset_ids(self):
        root, manifest, payload = self._workspace()
        payload["assets"].append(dict(payload["assets"][0]))
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "Duplicate asset_id"):
            MODULE.validate_manifest(manifest, root)

    def test_hash_verification_can_be_skipped_but_approval_cannot(self):
        root, manifest, payload = self._workspace()
        payload["assets"][0]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(
            MODULE.validate_manifest(manifest, root, verify_hashes=False),
            ["validation-features-v1"],
        )
        payload["assets"][0]["review_status"] = "pending"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ManifestError, "not approved"):
            MODULE.validate_manifest(manifest, root, verify_hashes=False)


if __name__ == "__main__":
    unittest.main()
