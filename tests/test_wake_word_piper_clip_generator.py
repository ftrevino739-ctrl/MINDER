"""Tests for the manifest-gated, multilingual Piper clip generator."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from tools.wake_word_training import piper_clip_generator as GENERATOR


try:
    import numpy as np
except Exception:
    np = None


def _make_approved_voice_assets(root: Path):
    assets = []
    voice_definitions = [
        ("english", "en-US", 3),
        ("mexican_spanish", "es-MX", 1),
    ]
    pool = {"voice_pool_version": 1, "voices": []}

    for voice_id, language, speaker_count in voice_definitions:
        model_rel = f"voices/{voice_id}.onnx"
        config_rel = f"voices/{voice_id}.onnx.json"
        model_path = root / model_rel
        config_path = root / config_rel
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes((voice_id + " model fixture").encode("utf-8"))
        config_path.write_text(
            json.dumps({
                "audio": {"sample_rate": 16000},
                "num_speakers": speaker_count,
                "default_speaker_id": 0,
            }),
            encoding="utf-8",
        )

        model_id = voice_id + "-model"
        config_id = voice_id + "-config"
        for asset_id, asset_type, path, filename in (
            (model_id, "voice_model", model_path, model_rel),
            (config_id, "voice_config", config_path, config_rel),
        ):
            assets.append({
                "asset_id": asset_id,
                "asset_type": asset_type,
                "source_url": "https://example.test/piper/" + filename,
                "source_revision": "voice-model-revision-1",
                "relative_path": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "license_id": "CC0-1.0",
                "attribution": "Synthetic test voice asset fixture.",
                "restrictions": [],
                "review_status": "approved",
                "reviewed_by": "unit-test",
                "reviewed_at": "2026-11-21",
                "approved_uses": ["model_training"],
                "derived_from": [],
            })
        pool["voices"].append({
            "voice_id": voice_id,
            "language": language,
            "model_asset_id": model_id,
            "config_asset_id": config_id,
            "weight": 1,
        })

    asset_manifest = root / "asset_manifest.json"
    asset_manifest.write_text(
        json.dumps({"manifest_version": 1, "assets": assets}),
        encoding="utf-8",
    )
    voice_pool = root / "voice_pool.json"
    voice_pool.write_text(json.dumps(pool), encoding="utf-8")
    return voice_pool, asset_manifest, pool


class FakePiperVoice:
    @staticmethod
    def load(model_path, config_path=None):
        voice_id = Path(model_path).stem
        count = 3 if voice_id == "english" else 1
        return FakeVoice(voice_id, count)


class FakeVoice:
    def __init__(self, voice_id, num_speakers):
        self.voice_id = voice_id
        self.config = SimpleNamespace(num_speakers=num_speakers, default_speaker_id=0)
        self.calls = []

    def synthesize(self, text, syn_config):
        self.calls.append((text, syn_config))
        # Short deterministic synthetic tone-like signal; no recording or network.
        value = 0.1 if self.voice_id == "english" else 0.2
        yield SimpleNamespace(
            sample_rate=16000,
            audio_float_array=np.asarray([0.0, value, -value, 0.0], dtype=np.float32),
        )


class FakeSynthesisConfig:
    def __init__(self, **kwargs):
        self.values = kwargs


class FakePiperApi:
    PiperVoice = FakePiperVoice
    SynthesisConfig = FakeSynthesisConfig


@unittest.skipUnless(np is not None, "NumPy is installed in Windows training/runtime environments")
class PiperClipGeneratorTests(unittest.TestCase):
    def _workspace(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "output").mkdir()
        pool, manifest, payload = _make_approved_voice_assets(root)
        return root, pool, manifest, payload

    def _generate(self, root, pool, manifest, **kwargs):
        return GENERATOR.generate_piper_corpus(
            ["hey minder", "hey minder"],
            sample_count=6,
            voice_pool_path=pool,
            asset_manifest_path=manifest,
            asset_root=root,
            output_dir=root / "output",
            corpus_manifest_path=root / "output" / "corpus_manifest.json",
            seed=123,
            piper_api=FakePiperApi,
            numpy_module=np,
            **kwargs,
        )

    def test_generates_mono_16khz_pcm16_with_per_clip_provenance(self):
        root, pool, manifest, _ = self._workspace()
        corpus_path = self._generate(root, pool, manifest)
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
        validated = GENERATOR.validate_piper_corpus_manifest(
            corpus_path, root / "output", asset_manifest_path=manifest, asset_root=root
        )
        self.assertEqual(len(validated), 6)

        self.assertEqual(payload["corpus_schema_version"], 1)
        self.assertEqual(payload["sample_count"], 6)
        self.assertEqual(payload["sample_rate"], 16000)
        self.assertEqual(payload["channels"], 1)
        self.assertEqual(payload["sample_format"], "PCM16")
        self.assertEqual(set(payload["languages"]), {"en-US", "es-MX"})
        self.assertEqual(
            set(payload["source_asset_ids"]),
            {"english-model", "english-config", "mexican_spanish-model", "mexican_spanish-config"},
        )
        self.assertEqual(len(payload["entries"]), 6)
        self.assertEqual(
            sum(entry["language"] == "en-US" for entry in payload["entries"]),
            3,
        )
        self.assertEqual(
            sum(entry["language"] == "es-MX" for entry in payload["entries"]),
            3,
        )

        for entry in payload["entries"]:
            wav_path = root / "output" / entry["relative_path"]
            self.assertTrue(wav_path.is_file())
            self.assertEqual(
                hashlib.sha256(wav_path.read_bytes()).hexdigest(),
                entry["sha256"],
            )
            with wave.open(str(wav_path), "rb") as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertGreater(wav.getnframes(), 0)

    def test_corpus_validator_rejects_tampered_wav_hash(self):
        root, pool, manifest, _ = self._workspace()
        corpus_path = self._generate(root, pool, manifest)
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
        sample_path = root / "output" / payload["entries"][0]["relative_path"]
        sample_path.write_bytes(sample_path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(GENERATOR.PiperCorpusError, "SHA-256 mismatch"):
            GENERATOR.validate_piper_corpus_manifest(
                corpus_path, root / "output", asset_manifest_path=manifest, asset_root=root
            )

    def test_corpus_validator_rejects_unapproved_source_asset(self):
        root, pool, manifest, _ = self._workspace()
        corpus_path = self._generate(root, pool, manifest)
        parent = json.loads(manifest.read_text(encoding="utf-8"))
        parent["assets"][0]["review_status"] = "pending"
        manifest.write_text(json.dumps(parent), encoding="utf-8")
        with self.assertRaisesRegex(GENERATOR.PiperCorpusError, "source asset manifest rejected"):
            GENERATOR.validate_piper_corpus_manifest(
                corpus_path, root / "output", asset_manifest_path=manifest, asset_root=root
            )

    def test_voice_pool_requires_mexican_spanish_and_english_coverage(self):
        root, pool, manifest, payload = self._workspace()
        payload["voices"][1]["language"] = "es-ES"
        pool.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(GENERATOR.PiperCorpusError, "Mexican-Spanish"):
            GENERATOR.load_approved_voice_records(pool, manifest, root)

    def test_voice_model_must_be_approved_for_training(self):
        root, pool, manifest, _pool_payload = self._workspace()
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["assets"][0]["review_status"] = "pending"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(GENERATOR.PiperCorpusError, "manifest rejected"):
            GENERATOR.load_approved_voice_records(pool, manifest, root)

    def test_rejects_existing_wav_and_manifest_without_overwriting(self):
        root, pool, manifest, _ = self._workspace()
        existing = root / "output" / "clip_0000000.wav"
        existing.write_bytes(b"user file")
        with self.assertRaisesRegex(GENERATOR.PiperCorpusError, "Refusing to overwrite"):
            self._generate(root, pool, manifest)
        self.assertEqual(existing.read_bytes(), b"user file")

    def test_rejects_path_escape_in_custom_wav_filename(self):
        root, pool, manifest, _ = self._workspace()
        with self.assertRaisesRegex(GENERATOR.PiperCorpusError, "simple basename"):
            GENERATOR.generate_piper_corpus(
                "hey minder",
                sample_count=1,
                voice_pool_path=pool,
                asset_manifest_path=manifest,
                asset_root=root,
                output_dir=root / "output",
                corpus_manifest_path=root / "output" / "manifest.json",
                seed=1,
                file_names=[r"..\\escape.wav"],
                piper_api=FakePiperApi,
                numpy_module=np,
            )

    def test_rejects_bad_sample_count_and_invalid_locale_pool_before_loading_piper(self):
        root, pool, manifest, _ = self._workspace()
        with self.assertRaisesRegex(GENERATOR.PiperCorpusError, "sample_count"):
            GENERATOR.generate_piper_corpus(
                "hey minder",
                sample_count=0,
                voice_pool_path=pool,
                asset_manifest_path=manifest,
                asset_root=root,
                output_dir=root / "output",
                corpus_manifest_path=root / "output" / "manifest.json",
                seed=1,
                piper_api=FakePiperApi,
                numpy_module=np,
            )

    def test_resampling_is_explicitly_called_to_16khz(self):
        root, pool, manifest, _ = self._workspace()
        called = []

        class ResampledVoice(FakeVoice):
            def synthesize(self, text, syn_config):
                yield SimpleNamespace(
                    sample_rate=22050,
                    audio_float_array=np.asarray([0.0, 0.1, -0.1, 0.0], dtype=np.float32),
                )

        class ResampledPiper:
            SynthesisConfig = FakeSynthesisConfig

            class PiperVoice:
                @staticmethod
                def load(model_path, config_path=None):
                    return ResampledVoice(Path(model_path).stem, 1)

        def resample(audio, source_rate, target_rate):
            called.append((source_rate, target_rate))
            return audio

        GENERATOR.generate_piper_corpus(
            "hey minder",
            sample_count=2,
            voice_pool_path=pool,
            asset_manifest_path=manifest,
            asset_root=root,
            output_dir=root / "output",
            corpus_manifest_path=root / "output" / "resampled.json",
            seed=6,
            piper_api=ResampledPiper,
            numpy_module=np,
            resample_fn=resample,
        )
        self.assertEqual(called, [(22050, 16000), (22050, 16000)])


if __name__ == "__main__":
    unittest.main()
