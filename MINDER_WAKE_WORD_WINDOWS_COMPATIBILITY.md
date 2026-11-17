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
