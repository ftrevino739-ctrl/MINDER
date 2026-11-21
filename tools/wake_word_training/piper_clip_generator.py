"""Manifest-gated multilingual Piper clip generator for MINDER.

This build-time utility synthesizes text locally from human-approved Piper
voice assets. It never records microphone audio or uploads audio. A sidecar
corpus manifest records the exact voice asset IDs, language, speaker ID,
text, output filename and SHA-256 for every generated WAV.

The generated audio corpus is not silently added to the main asset manifest:
a maintainer must review its terms and provenance, then register the corpus
manifest itself as a derived asset before later feature/training stages use it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import uuid
import re
import sys
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


TARGET_SAMPLE_RATE = 16000
CORPUS_SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class PiperCorpusError(ValueError):
    """Raised when voice provenance or synthesis output is unsafe or invalid."""


@dataclass(frozen=True)
class VoiceRecord:
    voice_id: str
    language: str
    model_asset_id: str
    config_asset_id: str
    model_path: Path
    config_path: Path
    weight: int


@dataclass
class LoadedVoice:
    record: VoiceRecord
    voice: Any


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PiperCorpusError(f"Cannot read {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise PiperCorpusError(f"{description} must contain a JSON object.")
    return value


def _asset_path(root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if "\\" in relative_path or ":" in relative_path or candidate.is_absolute():
        raise PiperCorpusError(f"Voice asset path must be a relative POSIX path: {relative_path!r}")
    if not candidate.parts or any(part in ("..", "") for part in candidate.parts):
        raise PiperCorpusError(f"Voice asset path may not escape the asset root: {relative_path!r}")
    resolved_root = root.expanduser().resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PiperCorpusError(f"Voice asset path escapes the asset root: {relative_path!r}") from exc
    return resolved


def load_approved_voice_records(
    voice_pool_path: Path,
    asset_manifest_path: Path,
    asset_root: Path,
    *,
    require_english_and_mexican_spanish: bool = True,
) -> tuple[list[VoiceRecord], list[str]]:
    """Validate every paired Piper ONNX/config asset against the reviewed manifest."""
    pool = _read_json(voice_pool_path, "voice pool")
    if type(pool.get("voice_pool_version")) is not int or pool["voice_pool_version"] != 1:
        raise PiperCorpusError("voice_pool_version must be exactly 1.")
    raw_voices = pool.get("voices")
    if not isinstance(raw_voices, list) or len(raw_voices) < 2:
        raise PiperCorpusError("The voice pool must contain at least two separately reviewed voices.")

    try:
        from tools.wake_word_training.asset_manifest import validate_manifest
        from tools.wake_word_training.asset_manifest import ManifestError
    except ImportError:
        from asset_manifest import validate_manifest, ManifestError

    manifest = _read_json(asset_manifest_path, "training asset manifest")
    try:
        approved_ids = set(validate_manifest(
            asset_manifest_path,
            asset_root,
            verify_hashes=True,
            required_use="model_training",
        ))
    except ManifestError as exc:
        raise PiperCorpusError(f"Training asset manifest rejected: {exc}") from exc

    records = {
        asset["asset_id"]: asset
        for asset in manifest.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("asset_id"), str)
    }

    voices: list[VoiceRecord] = []
    seen_voice_ids: set[str] = set()
    for index, raw in enumerate(raw_voices):
        label = f"voices[{index}]"
        if not isinstance(raw, dict):
            raise PiperCorpusError(f"{label} must be a JSON object.")

        voice_id = raw.get("voice_id")
        language = raw.get("language")
        model_id = raw.get("model_asset_id")
        config_id = raw.get("config_asset_id")
        weight = raw.get("weight", 1)

        if not isinstance(voice_id, str) or not _SAFE_ID.fullmatch(voice_id):
            raise PiperCorpusError(f"{label}.voice_id must be a short safe identifier.")
        if voice_id in seen_voice_ids:
            raise PiperCorpusError(f"Duplicate voice_id: {voice_id}")
        seen_voice_ids.add(voice_id)

        if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}[-_][A-Za-z0-9-]{2,16}", language):
            raise PiperCorpusError(f"{label}.language must be a locale such as en-US or es-MX.")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight <= 0:
            raise PiperCorpusError(f"{label}.weight must be a positive integer.")
        if not isinstance(model_id, str) or not isinstance(config_id, str):
            raise PiperCorpusError(f"{label} must reference model_asset_id and config_asset_id.")

        if model_id not in approved_ids or config_id not in approved_ids:
            raise PiperCorpusError(
                f"{label} references a Piper asset that is missing, unapproved, "
                "or not explicitly approved for model_training."
            )
        model_asset = records.get(model_id, {})
        config_asset = records.get(config_id, {})
        if model_asset.get("asset_type") != "voice_model":
            raise PiperCorpusError(f"{label} model asset must have asset_type 'voice_model'.")
        if config_asset.get("asset_type") != "voice_config":
            raise PiperCorpusError(f"{label} config asset must have asset_type 'voice_config'.")

        model_path = _asset_path(asset_root, str(model_asset.get("relative_path", "")))
        config_path = _asset_path(asset_root, str(config_asset.get("relative_path", "")))
        if model_path.suffix.casefold() != ".onnx":
            raise PiperCorpusError(f"{label} model asset must be a Piper .onnx file.")
        if config_path.suffix.casefold() != ".json":
            raise PiperCorpusError(f"{label} config asset must be a .json file.")
        if not model_path.is_file() or not config_path.is_file():
            raise PiperCorpusError(f"{label} model/config files are missing.")

        voices.append(VoiceRecord(
            voice_id=voice_id,
            language=language.replace("_", "-"),
            model_asset_id=model_id,
            config_asset_id=config_id,
            model_path=model_path,
            config_path=config_path,
            weight=weight,
        ))

    languages = {record.language.casefold() for record in voices}
    has_english = any(language.startswith("en-") for language in languages)
    has_mexican_spanish = any(language in ("es-mx", "es-mx-ne") for language in languages)
    if require_english_and_mexican_spanish and not (has_english and has_mexican_spanish):
        raise PiperCorpusError(
            "MINDER requires at least one reviewed English voice and one reviewed "
            "Mexican-Spanish (es-MX) voice in the synthesis pool."
        )

    voice_asset_ids = sorted({
        asset_id
        for record in voices
        for asset_id in (record.model_asset_id, record.config_asset_id)
    })
    return voices, voice_asset_ids


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_texts(text: str | Sequence[str]) -> list[str]:
    phrases = [text] if isinstance(text, str) else list(text)
    phrases = [phrase.strip() for phrase in phrases if isinstance(phrase, str) and phrase.strip()]
    if not phrases:
        raise PiperCorpusError("At least one non-empty synthesis phrase is required.")
    return phrases


def _safe_filename(name: str) -> str:
    if (not isinstance(name, str) or not name or Path(name).name != name
            or "\\" in name or "/" in name or ":" in name):
        raise PiperCorpusError(f"Output filename must be a simple basename: {name!r}")
    if not name.casefold().endswith(".wav") or name in (".wav", "..wav"):
        raise PiperCorpusError(f"Output filename must end with .wav: {name!r}")
    return name


def _default_resample(audio: Any, source_rate: int, target_rate: int):
    if source_rate == target_rate:
        return audio
    try:
        from scipy.signal import resample_poly
        import numpy as np
    except Exception as exc:
        raise PiperCorpusError(f"SciPy/NumPy are required for sample-rate conversion: {exc}") from exc
    import math
    divisor = math.gcd(source_rate, target_rate)
    return resample_poly(
        audio,
        target_rate // divisor,
        source_rate // divisor,
    ).astype(np.float32)


def _write_pcm16_wav(path: Path, audio: Any, *, numpy_module: Any) -> None:
    samples = numpy_module.asarray(audio, dtype=numpy_module.float32)
    if getattr(samples, "ndim", None) != 1 or samples.size == 0:
        raise PiperCorpusError("Piper synthesis must return non-empty mono audio.")
    samples = numpy_module.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype(numpy_module.int16)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(TARGET_SAMPLE_RATE)
        output.writeframes(pcm.tobytes())


def _write_pcm16_wav_exclusive(path: Path, audio: Any, *, numpy_module: Any) -> None:
    """Create a new WAV atomically with exclusive file creation (never truncate)."""
    samples = numpy_module.asarray(audio, dtype=numpy_module.float32)
    if getattr(samples, "ndim", None) != 1 or samples.size == 0:
        raise PiperCorpusError("Piper synthesis must return non-empty mono audio.")
    samples = numpy_module.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype(numpy_module.int16)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with wave.open(raw, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(TARGET_SAMPLE_RATE)
                output.writeframes(pcm.tobytes())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

def generate_piper_corpus(
    text: str | Sequence[str],
    *,
    sample_count: int,
    voice_pool_path: Path,
    asset_manifest_path: Path,
    asset_root: Path,
    output_dir: Path,
    corpus_manifest_path: Path,
    seed: int,
    length_scales: Sequence[float] = (0.75, 1.0, 1.25),
    noise_scales: Sequence[float] = (0.98,),
    noise_width_scales: Sequence[float] = (0.98,),
    file_names: Sequence[str] | None = None,
    require_english_and_mexican_spanish: bool = True,
    piper_api: Any | None = None,
    resample_fn: Callable[[Any, int, int], Any] | None = None,
    numpy_module: Any | None = None,
) -> Path:
    """Synthesize a fully recorded voice-diverse 16 kHz mono PCM16 WAV corpus.

    The voice pool's model and config asset IDs must both be human-approved
    for model_training and SHA-256 verified in the provided asset manifest.
    A separate corpus manifest records source IDs and each output file's
    hash, voice, language, speaker, text, and synthesis settings.

    Existing output filenames and corpus manifests are always rejected. Choose
    a new output directory for a new corpus; the routine never overwrites files.
    This routine does not approve the generated corpus in the parent manifest.
    """
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count <= 0:
        raise PiperCorpusError("sample_count must be a positive integer.")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise PiperCorpusError("seed must be a non-negative integer.")
    phrases = _validate_texts(text)
    if not length_scales or not noise_scales or not noise_width_scales:
        raise PiperCorpusError("All synthesis variation lists must be non-empty.")
    for label, values in (
        ("length_scales", length_scales),
        ("noise_scales", noise_scales),
        ("noise_width_scales", noise_width_scales),
    ):
        if any(not isinstance(value, (int, float)) or not (0 < float(value) < 3.0) for value in values):
            raise PiperCorpusError(f"{label} values must be finite numbers between 0 and 3.")

    voices, source_asset_ids = load_approved_voice_records(
        voice_pool_path,
        asset_manifest_path,
        asset_root,
        require_english_and_mexican_spanish=require_english_and_mexican_spanish,
    )

    output_root = output_dir.expanduser().resolve()
    if not output_root.is_dir():
        raise PiperCorpusError(f"Corpus output directory must already exist: {output_root}")
    corpus_manifest = corpus_manifest_path.expanduser().resolve()
    try:
        corpus_manifest.relative_to(output_root)
    except ValueError as exc:
        raise PiperCorpusError("Corpus manifest must be inside the corpus output directory.") from exc
    if corpus_manifest.exists():
        raise PiperCorpusError(f"Refusing to replace existing corpus manifest: {corpus_manifest}")

    if file_names is not None and len(file_names) != sample_count:
        raise PiperCorpusError("file_names must contain exactly sample_count entries.")
    names = (
        [_safe_filename(name) for name in file_names]
        if file_names is not None
        else [f"clip_{index:07d}.wav" for index in range(sample_count)]
    )
    if len(set(names)) != len(names):
        raise PiperCorpusError("file_names must be unique.")
    output_paths = [output_root / name for name in names]
    existing = [path.name for path in output_paths if path.exists()]
    if existing:
        raise PiperCorpusError(
            "Refusing to overwrite existing WAV output(s): " + ", ".join(existing[:5])
        )

    try:
        import numpy as np
    except Exception as exc:
        raise PiperCorpusError(f"NumPy is required for Piper corpus generation: {exc}") from exc
    np = numpy_module or np

    if piper_api is None:
        try:
            from piper import PiperVoice, SynthesisConfig
            piper_api = type("PiperApi", (), {
                "PiperVoice": PiperVoice,
                "SynthesisConfig": SynthesisConfig,
            })
        except Exception as exc:
            raise PiperCorpusError(f"Piper TTS could not be imported: {exc}") from exc

    voice_instances: dict[str, Any] = {}
    for record in voices:
        try:
            voice_instances[record.voice_id] = piper_api.PiperVoice.load(
                str(record.model_path),
                config_path=str(record.config_path),
            )
        except Exception as exc:
            raise PiperCorpusError(
                f"Could not load approved Piper voice {record.voice_id!r}: {exc}"
            ) from exc

    rng = random.Random(seed)
    voice_cycle = [record for record in voices for _ in range(record.weight)]
    rng.shuffle(voice_cycle)
    entries: list[dict[str, Any]] = []
    completed_paths: list[Path] = []

    try:
        for index, name in enumerate(names):
            if index and index % len(voice_cycle) == 0:
                rng.shuffle(voice_cycle)
            voice_record = voice_cycle[index % len(voice_cycle)]
            loaded_voice = voice_instances[voice_record.voice_id]
            voice_config = loaded_voice.config
            speaker_count = int(getattr(voice_config, "num_speakers", 1) or 1)
            if speaker_count > 1:
                speaker_id = rng.randrange(speaker_count)
            else:
                speaker_id = getattr(voice_config, "default_speaker_id", 0)
            phrase = phrases[index % len(phrases)]
            length_scale = float(rng.choice(list(length_scales)))
            noise_scale = float(rng.choice(list(noise_scales)))
            noise_width_scale = float(rng.choice(list(noise_width_scales)))

            try:
                synthesis_config = piper_api.SynthesisConfig(
                    speaker_id=speaker_id,
                    length_scale=length_scale,
                    noise_scale=noise_scale,
                    noise_w_scale=noise_width_scale,
                )
                chunks = list(loaded_voice.synthesize(
                    phrase,
                    syn_config=synthesis_config,
                ))
            except Exception as exc:
                raise PiperCorpusError(
                    f"Piper synthesis failed at clip {index + 1} ({voice_record.voice_id}): {exc}"
                ) from exc

            if not chunks:
                raise PiperCorpusError(
                    f"Piper returned no audio for clip {index + 1} ({voice_record.voice_id})."
                )
            sample_rates = {int(chunk.sample_rate) for chunk in chunks}
            if len(sample_rates) != 1:
                raise PiperCorpusError("Piper audio chunks in one utterance use inconsistent sample rates.")
            source_rate = sample_rates.pop()
            if source_rate <= 0:
                raise PiperCorpusError("Piper reported an invalid audio sample rate.")

            audio = np.concatenate([
                np.asarray(chunk.audio_float_array, dtype=np.float32).reshape(-1)
                for chunk in chunks
            ])
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak <= 0.0:
                raise PiperCorpusError(
                    f"Piper returned silent audio for clip {index + 1} ({voice_record.voice_id})."
                )
            audio = audio * ((10.0 ** (-1.0 / 20.0)) / peak)

            if resample_fn is None:
                audio = _default_resample(audio, source_rate, TARGET_SAMPLE_RATE)
            else:
                audio = resample_fn(audio, source_rate, TARGET_SAMPLE_RATE)
            audio = np.asarray(audio, dtype=np.float32)
            output_path = output_root / name
            _write_pcm16_wav_exclusive(output_path, audio, numpy_module=np)
            completed_paths.append(output_path)

            entries.append({
                "index": index,
                "relative_path": name,
                "sha256": _sha256_file(output_path),
                "text": phrase,
                "voice_id": voice_record.voice_id,
                "language": voice_record.language,
                "model_asset_id": voice_record.model_asset_id,
                "config_asset_id": voice_record.config_asset_id,
                "speaker_id": speaker_id,
                "source_sample_rate": source_rate,
                "target_sample_rate": TARGET_SAMPLE_RATE,
                "length_scale": length_scale,
                "noise_scale": noise_scale,
                "noise_width_scale": noise_width_scale,
            })

        payload = {
            "corpus_schema_version": CORPUS_SCHEMA_VERSION,
            "corpus_type": "synthetic_tts_wav",
            "generator": "MINDER Piper voice-pool generator",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "sample_rate": TARGET_SAMPLE_RATE,
            "channels": 1,
            "sample_format": "PCM16",
            "source_asset_ids": source_asset_ids,
            "sample_count": sample_count,
            "languages": sorted({entry["language"] for entry in entries}),
            "entries": entries,
        }
        temporary_manifest = corpus_manifest.with_name(
            f".{corpus_manifest.stem}.{uuid.uuid4().hex}.partial.json"
        )
        temporary_manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.link(temporary_manifest, corpus_manifest)
        except OSError as exc:
            raise PiperCorpusError(f"Could not publish corpus manifest without overwriting: {exc}") from exc
        temporary_manifest.unlink(missing_ok=True)
        return corpus_manifest
    except Exception:
        # Leave user-owned/pre-existing files untouched; remove only files that
        # this invocation created and only when this invocation did not finish.
        for path in completed_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-pool", type=Path, required=True)
    parser.add_argument("--asset-manifest", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--text", action="append", required=True,
                        help="Phrase to synthesize. May be repeated for a text pool.")
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args(argv)

    try:
        manifest = generate_piper_corpus(
            args.text,
            sample_count=args.samples,
            voice_pool_path=args.voice_pool,
            asset_manifest_path=args.asset_manifest,
            asset_root=args.asset_root,
            output_dir=args.output_dir,
            corpus_manifest_path=args.corpus_manifest,
            seed=args.seed,
        )
    except PiperCorpusError as exc:
        print(f"MINDER Piper corpus generation FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"Generated {args.samples} local WAV clips; corpus manifest: {manifest}")
    print("No microphone capture or network transfer occurred.")
    print("Register and review this derived corpus manifest before training uses its clips.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
