# MINDER — wake-word training compatibility audit

**Audit date:** 2026-11-17  
**Scope:** producing the custom `Hey MINDER` ONNX model on the project's target Windows 11 PC (8 GB RAM).  
**Status:** compatibility findings documented; no training environment installed, no datasets downloaded, and no training started.

## Executive decision

Do not run the upstream `openwakeword/train.py` pipeline natively on Windows 11 yet. The current script uses a multiprocessing DataLoader pattern that is not safe to assume works under Windows' spawn-based process model. Its false-positive validation path also expands the full 11.3-hour validation stream into a large in-memory sliding-window array.

A community project provides a native-Windows orchestration candidate, but it is a small external repository with one commit and has not been independently validated by MINDER. We should audit and test it before adopting it. Do not silently substitute it for the official pipeline.

## Findings

### 1. The upstream `[full]` extra is not the right install command

The current upstream `setup.py` full extra includes:

- `torchaudio>=0.13.1,<1`
- `tensorflow-cpu==2.8.1`
- `tensorflow_probability==0.16.0`
- `protobuf<4`
- `onnx_tf==1.10.0`
- `onnx==1.14.0`

That is a legacy conversion-oriented dependency set. MINDER's runtime consumes ONNX, and the custom training workflow exports ONNX; TensorFlow / ONNX-TF conversion is not a required MINDER deliverable. Do not install `openwakeword[full]` as a blanket setup step on Python 3.12.

Sources: [upstream openWakeWord setup.py](https://github.com/dscripka/openWakeWord/blob/main/setup.py).

### 2. The upstream trainer has Windows-sensitive orchestration

The current upstream trainer defines an `IterableDataset` locally inside its training entry point, creates a DataLoader with `num_workers = os.cpu_count() // 2`, and supplies `prefetch_factor=16`. It also creates the entire false-positive validation window array before wrapping it in a DataLoader.

These choices are not proof that it will fail on every Windows machine, but they are sufficient to reject an untested native-Windows training run. Windows process spawning requires objects sent to workers to be pickleable, and the target PC has only 8 GB RAM.

Sources: [upstream train.py](https://github.com/dscripka/openWakeWord/blob/main/openwakeword/train.py), [Windows-specific technical analysis and alternative orchestration](https://github.com/Nexus-nimdA/openwakeword-windows-training).

### 3. The Piper generator has its own native dependencies

The dscripka Piper sample-generator fork imports `webrtcvad`, `espeak_phonemizer`, PyTorch and TorchAudio. These are generator dependencies, not imports from the core openWakeWord trainer itself. MINDER's preflight should keep checking them when this specific generator is selected.

For Windows wheels, `webrtcvad-wheels` provides the `webrtcvad` import name and publishes Windows builds for modern CPython versions. This does not by itself validate the complete Piper generator stack.

Sources: [dscripka Piper sample generator](https://github.com/dscripka/piper-sample-generator), [webrtcvad-wheels](https://github.com/daanzu/py-webrtcvad-wheels).

### 4. Torch and TorchAudio must be selected as a compatible pair

TorchAudio binary extensions are tied to their matching PyTorch release for the 2.x versions used by the candidate workflow. The candidate Windows requirements pin `torch==2.8.0` and `torchaudio==2.8.0`; do not independently upgrade one of them.

TorchAudio's compatibility table lists the supported Python ranges per release. For example, the 2.8 pair supports Python 3.9–3.13, while 2.11 uses a stable ABI and supports Python 3.10–3.14. MINDER should test the exact pair it adopts rather than depending on unconstrained resolver choices.

Sources: [TorchAudio installation and compatibility matrix](https://docs.pytorch.org/audio/stable/installation), [PyTorch Windows installation](https://pytorch.org/get-started/locally/).

### 5. A native-Windows candidate exists, but is not approved yet

The repository [Nexus-nimdA/openwakeword-windows-training](https://github.com/Nexus-nimdA/openwakeword-windows-training) describes a Python 3.12 native-Windows pipeline. It:

- pins an openWakeWord source checkout;
- replaces the Linux-oriented Piper generator with a `piper-tts` ONNX voice generator;
- sets DataLoader workers to zero;
- windows the long false-positive validation set in bounded batches;
- provides a resumable dataset fetcher and a curated dependency list.

Its README reports approximately 18.3 GB of public training data and a ~20 GB disk requirement. It also documents its own deliberate changes to the upstream trainer. The repository currently has only one commit, so its claims and output have not yet been independently verified by MINDER. We will not vendor or execute it until its code, data hashes, licenses, and model compatibility are reviewed.

Source: [native-Windows training repository and README](https://github.com/Nexus-nimdA/openwakeword-windows-training).

### Additional review blockers in the candidate

Static inspection of its current `fetch_data.py` found that the two large feature files and the AudioSet parquet shard have hard-coded size/SHA-256 checks, but the MIT RIR downloads are not individually hash-verified and the FMA archive is accessed by HTTP byte ranges without an end-to-end archive hash. The README's broad statement that all big data is hash-verified therefore needs qualification. MINDER must add per-file integrity checks before trusting those downloads. In addition, its resumable downloader trusts the presence of a `.sha256ok` marker when an existing file has the expected size; it does not recompute that file's digest on every later preflight. MINDER should re-hash existing cached assets or bind the marker to a verified file identity before treating a cached dataset as trusted.

The candidate's sample YAML is not a drop-in replacement for MINDER's current `hey_minder.yml`: its dataset folders, output layout, Piper path and sample/step counts differ. An explicit MINDER config adapter is required; copying the example YAML unchanged would silently train a different phrase/configuration. The sample configuration also uses one US-English LibriTTS-R voice. That is useful for broad speaker variation but does not establish robust recognition of natural Mexican-Spanish pronunciation. MINDER's acceptance gate explicitly requires that coverage, so we need additional voice/language synthesis or real-speaker held-out evaluation before accepting the model.

## MINDER-specific constraints

- Target PC: Windows 11, 8 GB RAM.
- The production config currently requests 20,000 positive clips, 2,000 validation clips and 50,000 training steps.
- The ACAV100M negative feature file is approximately 17.28 GB; the full training data collection described by the Windows candidate is approximately 18.3 GB.
- The existing 20 GiB preflight disk threshold is only a warning threshold, not a complete disk-space estimate.
- Do not download the datasets, install the multi-gigabyte training stack, or start training without explicit approval.
- Keep wake word disabled by default. Never rename the legacy `hey_jarvis.onnx` model to claim it detects `Hey MINDER`.

## Next engineering gates

1. Pin and review the candidate Windows trainer's exact commit; compare its model architecture, feature pipeline, augmentation, validation metric and export path with the upstream version.
2. Verify every dependency and data source, including hashes and licenses. Confirm no user microphone capture or upload exists in the pipeline.
3. Add MINDER-specific tests for config mapping, output filename/model key, bounded-memory validation, resumability and Windows path handling.
4. Run only dependency/import and tiny synthetic smoke tests in an isolated environment first. Do not fetch the 18.3 GB datasets during this gate.
5. Only after those gates pass, ask before downloading datasets or launching a real training run.
6. Validate the resulting ONNX with MINDER's offline validator and real held-out speakers/audio before enabling the phrase.

**Current conclusion:** the preflight work is useful, but it is not yet a Windows training solution. The next phase is to verify and integrate a safe Windows-compatible orchestration—not to start training.

### Source-level review of the candidate (pinned snapshot)

The candidate's inspected main tree currently resolves to commit 1cdca2c3a0edd236608fe3c25023db2045d2de52. This is a review snapshot only; MINDER has not cloned, installed, or executed it.

The source review confirms that its trainer calls the pinned openWakeWord library's Model, AudioFeatures, augment_clips, and mmap_batch_generator, while implementing a separate orchestration. This is a plausible architecture for a Windows port, but it is not a byte-for-byte upstream trainer. It deliberately changes adversarial-text handling, augmentation-round retention, DataLoader worker count, and false-positive validation batching. Those changes need differential tests against the pinned upstream implementation before adoption.

Additional concrete data-integrity blockers found in fetch_data.py:

- RIR downloads are accepted when a filename exists with the expected byte size; no per-file SHA-256 is checked. The early-completion gate accepts any directory with at least 270 WAV files, without validating a manifest of expected names and hashes.
- The FMA archive is read through HTTP Range requests without a full-archive digest or signed per-member hashes. The downloader checks only the reported archive size.
- Existing feature/parquet downloads are trusted when their size matches and a .sha256ok marker exists. The marker's contents are not checked and the file is not re-hashed on later runs, so a modified cached file can pass this shortcut.
- AudioSet and FMA extraction use a .done marker as their completion gate. The marker is not bound to a manifest of output filenames, sizes, or hashes, so partial or modified extracted data can be mistaken for a complete dataset.
- HTTP resume checks that the server returns status 206, but does not validate the returned Content-Range start/end against the requested byte range.

These are fixable, but they mean the candidate's current resumability and integrity claims are not sufficient for a trusted MINDER training pipeline. Do not run its downloader against the target machine yet.

The candidate config is also a different model configuration (hey_nimbus, 3,000/1,000 clips and 20,000 steps), not MINDER's production hey_minder configuration. Any adapter must explicitly map MINDER's phrase, confusables, counts, paths, thresholds and output contract; silently reusing the candidate YAML is prohibited.
### Training and validation code review against the pinned upstream commit

The candidate's declared openWakeWord checkout is pinned to 368c03716d1e92591906a84949bc477f3a834455. Static comparison against that exact trainer confirms that it uses the upstream Model class, auto_train(), AudioFeatures, augment_clips(), mmap_batch_generator(), and export_model() rather than defining a different classifier architecture. Its output naming convention is compatible with MINDER's expected artifacts/hey_minder/hey_minder.onnx layout when the existing MINDER output_dir is used.

The candidate's stride-1 false-positive window count and order match the upstream reference: both yield windows for indexes range(0, frame_count - window_size), excluding the final mathematically possible window. However, the candidate wraps the overlapping, non-writable NumPy sliding-window view directly with torch.from_numpy(). PyTorch warns that writing through a tensor backed by a read-only NumPy array is undefined. The current validation path appears read-only, but MINDER should not rely on that undocumented restriction. A MINDER-owned replacement now stacks only one bounded batch at a time and creates writable contiguous tensors.

Added tools/wake_word_training/streaming_windows.py. Its pure iterator preserves the upstream window count/order and emits at most the configured batch size; its PyTorch adapter lazily imports training dependencies and is re-iterable, as required because Model.auto_train performs multiple validation passes. Added tests/test_wake_word_streaming_windows.py covering exact window equivalence, order, partial batches, short streams, invalid dimensions and repeatable iteration. The helper is a tested building block, not yet an adopted full Windows trainer.

Other comparison findings:

- Candidate feature generation accumulates embeddings in a Python list and then stacks them. At MINDER's present 20,000/2,000 clip counts and one augmentation round this is a bounded intermediate allocation, but MINDER should keep the official memmap writer or another streaming writer if sample counts are raised toward 100,000.
- The candidate's augmentation_rounds change retains repeated augmentations where the pinned upstream script currently asks its feature writer to trim back to the original clip count. The candidate change is intentional but changes sample weighting/counts and therefore needs a focused regression test before it is accepted.
- The candidate's negative-text generator uses the pinned English phoneme-overlap generator. That can create useful English confusables, but it does not generate Spanish phonetic confusables automatically. The current MINDER list of manually specified negatives must remain and should be expanded from held-out false-wake data.
- The candidate's Piper generator API usage is structurally consistent with piper-tts 1.7.0: that release exposes SynthesisConfig, PiperVoice.synthesize(), AudioChunk.audio_float_array and sample_rate. API shape alone does not validate voice quality or sample coverage.

### Mexican-Spanish synthetic coverage

The candidate currently synthesizes every clip with the single en_US LibriTTS-R multi-speaker voice. This offers broad speaker variation but cannot be treated as evidence of Mexican-Spanish pronunciation coverage. The official Piper voice collection currently lists Mexican-Spanish voices including es_MX-ald-medium and es_MX-claude-high. Their model cards identify one speaker each; the Ald card points to an Unlicense dataset, while the Claude card points to an Apache-2.0 dataset. These are candidate assets only: review each model card, voice file terms and pronunciation quality before deciding to use them. A MINDER voice pool should mix English multi-speaker samples with carefully licensed Spanish-Mexican samples, then validate the spoken phrase with native-speaker recordings kept local and held out from training.

Sources: [pinned upstream trainer](https://github.com/dscripka/openWakeWord/blob/368c03716d1e92591906a84949bc477f3a834455/openwakeword/train.py), [Piper 1.7.0 Python API source](https://github.com/OHF-Voice/piper1-gpl/tree/v1.7.0), [Mexican-Spanish Ald model card](https://huggingface.co/rhasspy/piper-voices/blob/main/es/es_MX/ald/medium/MODEL_CARD), [Mexican-Spanish Claude model card](https://huggingface.co/rhasspy/piper-voices/blob/main/es/es_MX/claude/high/MODEL_CARD).