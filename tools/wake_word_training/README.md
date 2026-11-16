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

- `vendor/piper-sample-generator/` — the **dscripka fork** of the Piper sample
  generator expected by openWakeWord (it exposes top-level `generate_samples.py`).
- `vendor/piper-sample-generator/models/en-us-libritts-high.pt` and its
  matching `.pt.json` config — the default LibriTTS generator checkpoint and
  model metadata. The JSON is included in the fork; the checkpoint must be
  downloaded separately.
- `assets/mit_rirs/` — room impulse response recordings.
- `assets/background_clips/` — varied background audio.
- `assets/validation_set_features.npy` — openWakeWord false-positive
  validation features.
- `assets/openwakeword_features_ACAV100M_2000_hrs_16bit.npy` — ACAV100M
  precomputed negative features.
- `openwakeword/resources/models/melspectrogram.onnx` and
  `embedding_model.onnx` — the feature-extraction models used by the trainer.

From the MINDER repo root, the following commands illustrate the expected
Piper layout (the fork's matching JSON metadata is already tracked):

```powershell
git clone https://github.com/dscripka/piper-sample-generator vendor/piper-sample-generator
Invoke-WebRequest `
  -Uri "https://github.com/rhasspy/piper-sample-generator/releases/download/v1.0.0/en-us-libritts-high.pt" `
  -OutFile "vendor/piper-sample-generator/models/en-us-libritts-high.pt"
```

In the openWakeWord checkout, prepare its feature models with:

```powershell
python -c "from openwakeword.utils import download_models; download_models()"
```

Install the training extras documented by the official openWakeWord project
and the Piper fork's requirements; the regular MINDER runtime dependencies are
not enough for training. The preflight reports missing imports before any
training command is run. Prepare the datasets using the official training
notebook and follow its current download instructions. Do not substitute
unverified third-party model files or rename `hey_jarvis.onnx`.

## Read-only preflight

Before running any training stage, place a copy of `hey_minder.yml` in the
openWakeWord checkout (or pass its absolute path) and run this from the MINDER
repository root. The preflight checks the trainer, YAML structure, Piper
entrypoint and LibriTTS checkpoint/config pair, openWakeWord feature-extraction
models, the training-time Python imports, required feature files, positive
batch sizes, background-path counts, writable output parent, flat
RIR/background folders containing only top-level files (the upstream trainer
scans these non-recursively and does not filter subdirectories), and free disk
space. It does not download, create, record, or train anything:

```powershell
python tools/wake_word_training/preflight.py `
  --training-root "PATH\\TO\\openWakeWord" `
  --config "PATH\\TO\\openWakeWord\\hey_minder.yml"
```

A failed preflight means do not start training until the reported missing
inputs are fixed. Its default 20 GiB free-space threshold is a warning, not a
claim that 20 GiB is sufficient for every training setup.

## Training stages

Run from the openWakeWord checkout with the MINDER config available at
`hey_minder.yml` (adjust its relative paths only if your working directory
differs):

```powershell
python openwakeword/train.py --training_config hey_minder.yml --generate_clips
python openwakeword/train.py --training_config hey_minder.yml --augment_clips
python openwakeword/train.py --training_config hey_minder.yml --train_model
```

With the current `output_dir: "./artifacts/hey_minder"`, the trainer should
export `artifacts/hey_minder/hey_minder.onnx`. Confirm the file exists after
training before copying it to MINDER's `models/wake/hey_minder.onnx`.

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

## Offline candidate-model check

After training, collect two folders of **already available** WAV test clips:
one with the wake phrase spoken, and one with representative negative audio
(including hard negatives, ordinary conversation, music and TV). The validator
does not record audio. Every WAV must be 16 kHz, mono, PCM16.

From the MINDER repository root, run:

```powershell
python tools/wake_word_training/validate_model.py `
  --model "PATH\\TO\\hey_minder.onnx" `
  --positive-dir "PATH\\TO\\positive_wavs" `
  --negative-dir "PATH\\TO\\negative_wavs"
```

The script uses the same openWakeWord ONNX inference API and the runtime's
two-consecutive-frame / two-second-cooldown policy. It reports positive clip
recall and false activations per hour. Exit code 0 means every supplied
positive clip triggered and the measured negative rate was <= 0.2/hour; it is
only a preliminary dataset gate, not proof of real-world performance. Include
multiple real speakers and held-out room/background recordings before
accepting the model.
