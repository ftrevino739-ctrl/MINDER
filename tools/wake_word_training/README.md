# MINDER — Hey MINDER model training

This directory contains the reproducible training configuration for the actual
`Hey MINDER` phrase. It does **not** contain a fabricated model or any recorded
personal audio.

## Status

- Runtime integration is implemented in `core/wake_word.py`.
- The runtime selects `models/wake/hey_minder.onnx` only when that file exists.
- Until a trained model passes the acceptance gate, the explicitly labelled
  legacy `hey_jarvis` model remains the fallback.
- Wake-word listening remains opt-in/off by default. Push-to-Talk is independent.
- **There is currently no trained Hey MINDER ONNX model in this repository.**

## Training configuration

Use `hey_minder.yml` with the official openWakeWord custom training pipeline.
It requests 20,000 positive synthetic clips, 2,000 validation clips, 50,000
training steps, hard negative phrases, room impulse response augmentation,
background audio, and the 0.2 false-activations/hour training objective.

The YAML paths are intentionally explicit. They refer to training assets that
must be downloaded/prepared first; they are not files included in this repo.
The ACAV100M feature file is large (multi-gigabyte), so check free disk space
before downloading. Keep all training assets and generated clips outside the
MINDER runtime repository except for the final accepted ONNX model.

## Required training inputs

Prepare these paths in the training working directory before running the
pipeline:

- `vendor/piper-sample-generator/` — the Piper sample generator expected by
  the upstream training code.
- `assets/mit_rirs/` — room impulse response recordings.
- `assets/background_clips/` — varied background audio.
- `assets/validation_set_features.npy` — openWakeWord false-positive
  validation features.
- `assets/openwakeword_features_ACAV100M_2000_hrs_16bit.npy` — ACAV100M
  precomputed negative features.

Use the official openWakeWord training notebook/repository to prepare these
inputs and follow its current download instructions. Do not substitute
unverified third-party model files or rename `hey_jarvis.onnx`.

## Training stages

Run from the openWakeWord checkout with the MINDER config available at
`hey_minder.yml` (adjust its relative paths only if your working directory
differs):

```powershell
python openwakeword/train.py --training_config hey_minder.yml --generate_clips
python openwakeword/train.py --training_config hey_minder.yml --augment_clips
python openwakeword/train.py --training_config hey_minder.yml --train_model
```

The expected ONNX output is normally
`artifacts/hey_minder/hey_minder.onnx`. Verify the actual output path emitted
by the trainer before copying it to MINDER's
`models/wake/hey_minder.onnx`.

Training is an offline build operation. It does not need MINDER's microphone
and must not record or upload the user's voice. The model should include
pronunciation variation for both English-like and natural Mexican-Spanish
delivery of “Hey MINDER”; synthetic-only results must still be checked against
real speakers before release.

## Runtime acceptance gate — do not skip

1. Load the candidate ONNX with the same installed openWakeWord ONNX runtime
   used by MINDER and confirm its output key matches `hey_minder`.
2. Test multiple speakers, including natural Mexican-Spanish pronunciation,
   different distances, speaking rates, microphones and rooms.
3. Test hard negatives: “Hey reminder”, “Hey mentor”, “Hey render”, normal
   conversation, music and TV audio.
4. Measure false activations on held-out background audio. The config's
   0.2/hour value is a training target, not a performance guarantee.
5. Confirm inference stays local and microphone callbacks only enqueue frames.
6. Confirm disabling wake word stops detection and releases its capture path.
7. Confirm Push-to-Talk, interruption, STT → LLM → TTS, and normal UI operation
   still work with wake word both enabled and disabled.

Only after every gate passes may the custom phrase become the default. Until
then, leave the feature disabled by default and keep the legacy fallback
explicitly labelled.
