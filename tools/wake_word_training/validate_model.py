#!/usr/bin/env python3
"""Offline acceptance check for a custom MINDER openWakeWord ONNX model.

Only reads WAV files from disk. It never opens a microphone or sends audio
anywhere. Inputs must already be 16 kHz, mono, signed 16-bit PCM.
"""
from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

FRAME_SAMPLES = 1280  # 80 ms at 16 kHz; matches the runtime's streaming input.


def score_file(model, model_key: str, path: Path, threshold: float,
               consecutive_hits: int = 2, cooldown_seconds: float = 2.0) -> tuple[bool, int, float]:
    """Return (detected, event_count, duration_seconds) for one WAV."""
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        if (channels, sample_width, sample_rate) != (1, 2, 16000):
            raise ValueError(
                f"{path}: expected 16 kHz mono PCM16 WAV; got "
                f"{sample_rate} Hz, {channels} channel(s), {sample_width * 8}-bit"
            )
        duration = frame_count / float(sample_rate)
        consecutive = 0
        events = 0
        last_event = float("-inf")
        frame_index = 0
        while True:
            raw = wav.readframes(FRAME_SAMPLES)
            if len(raw) < FRAME_SAMPLES * sample_width:
                break  # Ignore a partial trailing frame; do not pad synthetic silence.
            audio = np.frombuffer(raw, dtype="<i2")
            scores = model.predict(audio)
            matching = [float(v) for k, v in scores.items() if model_key in k.lower()]
            if not matching:
                raise RuntimeError(
                    f"Model output has no key containing '{model_key}'. "
                    f"Available keys: {', '.join(scores.keys())}"
                )
            score = max(matching)
            consecutive = consecutive + 1 if score >= threshold else 0
            elapsed = frame_index * FRAME_SAMPLES / float(sample_rate)
            if consecutive >= consecutive_hits and elapsed - last_event >= cooldown_seconds:
                events += 1
                last_event = elapsed
                consecutive = 0
            frame_index += 1
    return events > 0, events, duration


def wav_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise FileNotFoundError(f"Directory not found: {folder}")
    files = sorted(folder.rglob("*.wav"))
    if not files:
        raise FileNotFoundError(f"No .wav files found in: {folder}")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="Candidate hey_minder.onnx")
    parser.add_argument("--positive-dir", type=Path, required=True,
                        help="WAVs that contain the spoken wake phrase")
    parser.add_argument("--negative-dir", type=Path, required=True,
                        help="WAVs that must not activate the model")
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    if not args.model.is_file():
        parser.error(f"Model file not found: {args.model}")
    if not 0.0 < args.threshold < 1.0:
        parser.error("--threshold must be between 0 and 1")

    try:
        from openwakeword.model import Model
    except Exception as exc:
        print(f"ERROR: openWakeWord runtime is unavailable: {exc}", file=sys.stderr)
        return 2

    model_key = args.model.stem.lower()
    try:
        model = Model(wakeword_models=[str(args.model)], inference_framework="onnx")
        positives = wav_files(args.positive_dir)
        negatives = wav_files(args.negative_dir)

        positive_hits = 0
        for path in positives:
            detected, _, _ = score_file(model, model_key, path, args.threshold)
            positive_hits += int(detected)

        false_events = 0
        negative_seconds = 0.0
        for path in negatives:
            _, events, duration = score_file(model, model_key, path, args.threshold)
            false_events += events
            negative_seconds += duration

        false_per_hour = (false_events / (negative_seconds / 3600.0)
                          if negative_seconds else float("inf"))
        recall = positive_hits / len(positives)
        print(f"Model: {args.model}")
        print(f"Positive clips detected: {positive_hits}/{len(positives)} ({recall:.1%})")
        print(f"Negative audio duration: {negative_seconds / 3600.0:.2f} h")
        print(f"False activation events: {false_events}")
        print(f"False activations/hour: {false_per_hour:.3f}")
        print(f"Training target (not a guarantee): <= 0.2/hour")
        return 0 if positive_hits == len(positives) and false_per_hour <= 0.2 else 1
    except Exception as exc:
        print(f"ERROR: validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
