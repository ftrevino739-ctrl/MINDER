# MINDER — Hey MINDER model training

This directory contains the reproducible training configuration for the actual
`Hey MINDER` phrase. It does **not** contain a fabricated model or any recorded
personal audio.

## Platform compatibility — important before installing anything

The current `preflight.py` checks files, configuration and whether Python modules
are discoverable. It does **not** prove that the upstream training script can run
on the host operating system, nor does it validate binary imports or package
version compatibility.

- The upstream `openwakeword/train.py` is not currently an approved native
  Windows 11 training route. Its current training block creates a local
  `IterableDataset`, uses worker processes and passes `prefetch_factor`; the
  Windows spawn model needs a separate compatibility treatment. Its false-
  positive validation path also materializes a large sliding-window array.
- Do **not** install `openwakeword[full]` as a shortcut. The current upstream
  extras still include old TensorFlow / ONNX-TF pins and a `torchaudio<1`
  constraint. They are not required for MINDER's ONNX-only runtime/export path
  and are a poor fit for a modern Python 3.12 training environment.
- The dscripka Piper sample-generator fork has its own native dependencies
  (`webrtcvad`, `espeak_phonemizer`, PyTorch and TorchAudio). Its checkpoint and
  script being present does not establish that those dependencies install and
  import successfully on Windows.
- A community native-Windows orchestration exists, using a pinned openWakeWord
  checkout, a replacement Piper-ONNX generator and a curated Python 3.12
  dependency set. It is a **candidate for review, not yet an approved MINDER
  dependency**: it is a small, single-commit external repository and has not
  been independently run through MINDER's acceptance tests. See
  [the compatibility audit](../../MINDER_WAKE_WORD_WINDOWS_COMPATIBILITY.md).

For now, treat the three upstream `train.py` commands below as the reference
Linux/Colab workflow only. **Do not run them natively on the target Windows 11
PC.** No training assets are downloaded and no training is started by this
project until a Windows-compatible pipeline is reviewed and validated.

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

Set `$TRAIN_ROOT` to the root of the openWakeWord checkout. These commands
place Piper where the relative path in `hey_minder.yml` expects it (the fork's
matching JSON metadata is already tracked):

```powershell
$TRAIN_ROOT = "PATH\TO\openWakeWord"
git clone https://github.com/dscripka/piper-sample-generator "$TRAIN_ROOT\vendor\piper-sample-generator"
Invoke-WebRequest `
  -Uri "https://github.com/rhasspy/piper-sample-generator/releases/download/v1.0.0/en-us-libritts-high.pt" `
  -OutFile "$TRAIN_ROOT\vendor\piper-sample-generator\models\en-us-libritts-high.pt"
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
space. Pass `--verify-imports` to actually import every required module (catching binary/DLL load failures) and verify that the installed PyTorch and TorchAudio versions/builds match exactly. The default mode only checks module discoverability. It does not download, create, record, or train anything:

```powershell
python tools/wake_word_training/preflight.py `
  --training-root "PATH\\TO\\openWakeWord" `
  --config "PATH\\TO\\openWakeWord\\hey_minder.yml" `
  --verify-imports
```

A failed preflight means do not start training until the reported missing
inputs are fixed. Its default 20 GiB free-space threshold is a warning, not a
claim that 20 GiB is sufficient for every training setup.

## Reference training stages (Linux / compatible hosted environment only)

These are the upstream reference commands, **not a supported native Windows
11 workflow**. Use them only after selecting and validating a compatible Linux
or hosted environment, with all datasets and dependencies already prepared.
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


## Windows-port building block — bounded validation windows

The first MINDER-owned Windows-port component is now in
`streaming_windows.py`. It reproduces the pinned upstream validator's
stride-1 window order and count, but emits a maximum of 8,192 windows at a
time. `TorchWindowBatches` is re-iterable, so each auto-training validation
pass receives the complete 11.3-hour stream again. It also stacks each
batch into a writable contiguous NumPy array before creating tensors, avoiding
the read-only overlapping-array wrapper used in the external candidate.

Illustrative integration after the feature file has been prepared:

```python
import numpy as np
from tools.wake_word_training.streaming_windows import TorchWindowBatches

false_positive_features = np.load(
    config["false_positive_validation_data_path"],
    mmap_mode="r",
    allow_pickle=False,
)
false_positive_batches = TorchWindowBatches(
    false_positive_features,
    window_size=input_shape[0],
    batch_size=8192,
)
```

This helper has unit coverage for upstream-equivalent window count/order,
partial batches, invalid input, repeated passes and the tensor-adapter
contract using lightweight test doubles. It is now used by MINDER’s own
model-training bridge; the external community trainer and its downloader
remain unapproved and are not invoked.


## Dataset provenance and human approval gate

A second release blocker was found during the source audit: the community
candidate's FMA downloader blindly selects MP3s and does not consult the
per-track license metadata or retain attribution. The FMA maintainers state
that they do not hold copyright in the audio; each track is distributed under
the artist-selected license. The candidate's AudioSet mirror is labelled
CC BY 4.0 but contains YouTube-derived clips, so its repository-level label
must not be treated as a substitute for reviewing source terms. The
openwakeword_features dataset is labelled CC BY-NC-SA 4.0; assess the
obligations for any model trained from those features before distributing
that model. See
[MINDER_WAKE_WORD_WINDOWS_COMPATIBILITY.md](../../MINDER_WAKE_WORD_WINDOWS_COMPATIBILITY.md)
for the source-by-source findings.

MINDER now includes a small stdlib-only manifest validator:

- `asset_manifest.example.json` is a template. It deliberately has
  `review_status: pending` and cannot pass until a human fills in real
  provenance and approval details. Add `derived_from: []` for an original
  source or list the IDs of the input assets used to produce a derived file.
- `asset_manifest.py` requires a canonical HTTPS source, immutable source
  revision, exact license identifier, attribution, restrictions, review
  identity/date, explicit approval for `model_training`, source-to-derived
  lineage, a safe relative path and a SHA-256 that matches the local file.
- It rejects unapproved assets, path traversal, duplicated IDs and hash
  mismatches. Hashing is streamed in 1 MiB chunks.
- It does not interpret legal terms or decide whether a license permits a
  model-training use. A successful result records a human review and proves
  local byte integrity; it is not a legal opinion.

Example invocation after an asset has been independently obtained and
reviewed:

```powershell
python tools/wake_word_training/asset_manifest.py `
  --manifest "PATH\TO\asset_manifest.json" `
  --asset-root "PATH\TO\training_assets"
```

Do not mark any openWakeWord feature file, FMA track, AudioSet clip or voice
model approved just to make this check pass. The external candidate downloader
is still not approved, and the manifest tool is not yet wired into a complete
Windows trainer. No source datasets, clips or voice files have been downloaded
for MINDER.

## MINDER-owned Windows model-training bridge

`trainer_bridge.py` now connects the project’s actual `hey_minder.yml` and approved feature assets to the pinned openWakeWord model class. It deliberately implements only the model-fitting/export stage: it does not synthesize clips, augment recordings, fetch datasets, record microphone audio or change MINDER’s application runtime.

The bridge:

- Requires the exact audited openWakeWord source checkout commit `368c03716d1e92591906a84949bc477f3a834455`.
- Requires `model_name: hey_minder` and exactly the product phrase `hey minder`; it rejects accidental training of a different wake phrase.
- Requires an approved asset manifest with SHA-256 verification for every input array.
- Opens large NumPy feature arrays as read-only memory maps instead of loading the full ACAV100M set into RAM.
- Uses a re-iterable training dataset with `num_workers=0`, so Windows does not need to spawn/pickle a local dataset class or prefetch 16 worker batches.
- Feeds false-positive validation data through the MINDER bounded-window iterator (1,024 windows per batch by default, configurable up to 4,096).
- Runs a final smoke test through the same openWakeWord ONNX runtime used by MINDER and verifies that the model exposes the `hey_minder` output key.

### Expected prepared feature layout

With the current `output_dir: ./artifacts/hey_minder`, the train bridge expects these feature arrays under the selected openWakeWord training root:

```text
assets/
  validation_set_features.npy
  openwakeword_features_ACAV100M_2000_hrs_16bit.npy
artifacts/
  hey_minder/
    hey_minder/
      positive_features_train.npy
      negative_features_train.npy
      positive_features_test.npy
      negative_features_test.npy
asset_manifest.json
```

The manifest must contain an individually reviewed record for each of these six arrays, including the four generated feature arrays. Generated arrays must list their source records under `derived_from`. The bridge rejects missing, pending, out-of-root or hash-mismatched assets before it imports PyTorch.

### Read-only bridge check

Run the following from the MINDER repository root. Replace the paths with your actual checkouts. This checks the Git pin, MINDER phrase/config contract, asset hashes, manifest coverage and required feature files. It does not import PyTorch, change files or start training:

```powershell
python tools/wake_word_training/trainer_bridge.py `
  --training-root "PATH\\TO\\openWakeWord" `
  --config "$PWD\\tools\\wake_word_training\\hey_minder.yml" `
  --asset-manifest "PATH\\TO\\openWakeWord\\asset_manifest.json"
```

### Explicit model-training command

Only after the source-specific license reviews are complete, every required feature file exists, and the read-only command passes, the actual CPU/RAM-intensive operation can be explicitly authorized:

```powershell
python tools/wake_word_training/trainer_bridge.py `
  --training-root "PATH\\TO\\openWakeWord" `
  --config "$PWD\\tools\\wake_word_training\\hey_minder.yml" `
  --asset-manifest "PATH\\TO\\openWakeWord\\asset_manifest.json" `
  --execute --confirm-heavy-training
```

The two confirmation flags are both required. The current assistant has not run this command, installed a training stack or downloaded the datasets. The bridge is code-reviewed and CI-tested, but the actual model-training stage and exported-model smoke test have not yet been exercised with real model assets on the target Windows 11 PC.

Important: this is not yet a complete data-preparation pipeline. The feature-generation stage still needs an approved voice pool (including genuine Mexican-Spanish pronunciation coverage), a safe augmentation workflow and source-specific licensing/provenance records. Do not infer that a passing bridge dry run means those earlier stages have been completed.

## Windows-safe feature materialization

The next pipeline component is now available as `feature_writer.py`. It
accepts an iterator of augmented PCM batches and an openWakeWord
`AudioFeatures` instance, then writes features directly into an exact-size
NumPy memory map. It does not build a full feature matrix in RAM and does not
call the upstream `trim_mmap` routine that attempts to delete/rename a mapped
file while Windows may still hold it open.

The writer verifies the declared row count and every embedded batch shape.
It flushes and closes the mapping before publishing the completed file with a
same-directory atomic replace. Existing outputs are refused by default;
replacing one requires `overwrite=True`. A failed or incomplete generation
keeps the previous output intact and removes the partial file.

Example integration once the clip list, augmentations and their source
manifest have been prepared:

```python
from pathlib import Path

from openwakeword.data import augment_clips
from openwakeword.utils import AudioFeatures
from tools.wake_word_training.feature_writer import write_features_from_generator

paths_for_this_split = positive_train_wav_paths
rounds = config["augmentation_rounds"]
augmented = augment_clips(
    paths_for_this_split * rounds,
    total_length=clip_length_samples,
    batch_size=config["augmentation_batch_size"],
    background_clip_paths=approved_background_paths,
    RIR_paths=approved_rir_paths,
)
writer = AudioFeatures(device="cpu")
write_features_from_generator(
    augmented,
    total_samples=len(paths_for_this_split) * rounds,
    clip_duration_samples=clip_length_samples,
    audio_features=writer,
    output_path=Path("artifacts/hey_minder/hey_minder/positive_features_train.npy"),
)
```

The writer itself is implemented and unit-tested with synthetic batches. The
complete corpus-generation/augmentation stage is still not wired to the CLI:
MINDER must first finish an approved multi-voice generator and source manifest
(including Mexican-Spanish coverage, RIR and background rights). The bridge
therefore still requires precomputed feature files and will not create or
download them automatically.

## Local multilingual Piper corpus generation

MINDER now has a first-party voice-pool generator in `piper_clip_generator.py`. It synthesizes text locally; it does not access a microphone, upload audio, install Piper, or download voice models. It requires an isolated build environment with NumPy, Piper TTS and SciPy (SciPy is needed only when a voice sample rate is not 16 kHz).

The draft `voice_pool.example.json` shows the required layout. Make a separate `voice_pool.json` and replace every placeholder ID with real `asset_id` values from the approved `asset_manifest.json`:

- Each model file must be an ONNX Piper voice recorded as `asset_type: voice_model`.
- Its sidecar JSON must be recorded separately as `asset_type: voice_config`.
- Both records need their own SHA-256, exact license, attribution, restrictions and explicit `model_training` approval.
- The pool must contain at least one English voice and one Mexican-Spanish (`es-MX`) voice. Voice selection is weighted and balanced across cycles; a multi-speaker voice selects a speaker ID per clip.
- No candidate voice is bundled or automatically selected. A voice must be obtained and reviewed by the maintainer first.

Example PowerShell command for a positive training split (create the output directory first):

```powershell
$ROOT = "D:\\MINDER-training"
$OUT = Join-Path $ROOT "corpus\\positive_train"
New-Item -ItemType Directory -Force -Path $OUT | Out-Null
python tools/wake_word_training/piper_clip_generator.py `
  --voice-pool "PATH\\TO\\voice_pool.json" `
  --asset-manifest (Join-Path $ROOT "asset_manifest.json") `
  --asset-root $ROOT `
  --output-dir $OUT `
  --corpus-manifest (Join-Path $OUT "corpus_manifest.json") `
  --text "hey minder" `
  --samples 20000 `
  --seed 101
```

For the negative split, pass the hand-reviewed negative phrases from `hey_minder.yml` as repeated `--text` arguments and use a different empty output directory and seed. Use separate positive and negative directories for training and synthetic validation. The generator refuses existing filenames and manifests; choose a new folder rather than overwriting an earlier corpus.

After generation, verify every WAV and its lineage before using it:

```powershell
python tools/wake_word_training/validate_piper_corpus.py `
  --corpus-manifest (Join-Path $OUT "corpus_manifest.json") `
  --corpus-root $OUT `
  --asset-manifest (Join-Path $ROOT "asset_manifest.json") `
  --asset-root $ROOT
```

The sidecar records SHA-256 for each WAV, source voice asset IDs, locale, speaker ID, text and synthesis parameters. The corpus manifest itself is a derived artifact: review it, add a `generated_audio_corpus_manifest` record with `derived_from` listing the voice model/config IDs, then explicitly approve its use before it can be used as a source for feature generation. The generator never marks generated output as approved.

These generated WAVs are synthetic coverage only. A high-quality `es-MX` TTS voice may still be a single speaker and does not prove that MINDER recognizes real people using natural Mexican-Spanish pronunciation. The final wake-word acceptance gate still requires multiple held-out human speakers and separate real-room false-wake measurements. Do not record or upload personal audio as part of this tool.
## Manifest-gated augmentation and feature preparation

`prepare_features.py` is now the MINDER-owned fourth stage after (1) asset review, (2) local Piper WAV corpus generation, and (3) corpus validation. It uses the pinned openWakeWord `augment_clips()` and `AudioFeatures` implementations but performs the file writing through MINDER's Windows-safe atomic `feature_writer.py`.

The stage requires four distinct corpus manifests and four distinct asset IDs: `positive_train`, `positive_test`, `negative_train`, and `negative_test`. It checks the declared counts against `hey_minder.yml`, requires the positive corpus phrase to be exactly `hey minder`, and requires each negative entry's text to appear in `custom_negative_phrases`. Each registered corpus manifest must already be approved in the parent `asset_manifest.json` as `generated_audio_corpus_manifest` and must list its approved voice model/config asset IDs in `derived_from`.

RIR and background folders must be flat, non-empty directories. Every top-level file must have its own SHA-256-verified, human-approved parent-manifest entry with `asset_type: room_impulse_response` or `asset_type: background_audio`, respectively. The official openWakeWord trainer scans these folders non-recursively, so nested folders are rejected.

### Read-only feature-preparation preflight

Run from the MINDER repository root after the four corpus manifests, approved voice models/configs, RIRs and backgrounds have been prepared. The four corpus manifests and their IDs must all be unique and recorded in `asset_manifest.json` before this command can pass:

```powershell
python tools/wake_word_training/prepare_features.py `
  --training-root "PATH\\TO\\openWakeWord" `
  --asset-root "PATH\\TO\\training-assets" `
  --config "$PWD\\tools\\wake_word_training\\hey_minder.yml" `
  --asset-manifest "PATH\\TO\\training-assets\\asset_manifest.json" `
  --positive-train-corpus-manifest "PATH\\TO\\positive_train\\corpus_manifest.json" `
  --positive-train-corpus-asset-id "APPROVED_POSITIVE_TRAIN_CORPUS_ASSET_ID" `
  --positive-test-corpus-manifest "PATH\\TO\\positive_test\\corpus_manifest.json" `
  --positive-test-corpus-asset-id "APPROVED_POSITIVE_TEST_CORPUS_ASSET_ID" `
  --negative-train-corpus-manifest "PATH\\TO\\negative_train\\corpus_manifest.json" `
  --negative-train-corpus-asset-id "APPROVED_NEGATIVE_TRAIN_CORPUS_ASSET_ID" `
  --negative-test-corpus-manifest "PATH\\TO\\negative_test\\corpus_manifest.json" `
  --negative-test-corpus-asset-id "APPROVED_NEGATIVE_TEST_CORPUS_ASSET_ID"
```

This default mode is read-only: it verifies the exact openWakeWord Git commit, MINDER config, all corpus files and their hashes, the four source registrations, room impulse responses/background files and output paths. It does not import PyTorch, create directories, modify manifests, synthesize audio, download anything or start feature extraction.

### Explicit feature extraction

Only after all upstream source-specific license decisions have been made, the four corpora have been locally generated and manually registered/approved, and the read-only preflight succeeds, this same command can be repeated with both confirmation flags:

```powershell
  --execute --confirm-heavy-preparation
```

This performs CPU-first room/background augmentation and streaming feature embedding for four splits. It writes `positive_features_train.npy`, `positive_features_test.npy`, `negative_features_train.npy` and `negative_features_test.npy` directly to exact-size memory-mapped files, one batch at a time. It never builds a full feature matrix in RAM and never calls the upstream `trim_mmap` path that unlinks an open map on Windows.

At successful completion it creates `feature_asset_proposals.pending.json`. This is deliberately NOT an approved asset manifest: all four records have `review_status: pending`, empty `approved_uses`, a `REVIEW_REQUIRED` license field, incremental SHA-256 hashes and `derived_from` links to the reviewed corpora, RIRs and background files. A maintainer must review source rights/attribution and the feature dataset's derivative-license implications, then merge/review these records into `asset_manifest.json`. `trainer_bridge.py` will reject the features until their asset records have been approved. Do not mark the proposals approved merely to bypass the gate.

The feature stage is a CPU/disk-intensive operation and has not been executed against real voice/data assets. CI uses synthetic fixtures only. No datasets, Piper models or real audio corpora have been downloaded or generated by MINDER during this development phase.