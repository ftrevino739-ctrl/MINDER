# MINDER — Hey MINDER wake-word implementation

## Current behavior

The application already has a local, background-thread wake-word detector in `core/wake_word.py`.
It currently loads openWakeWord's pretrained `hey_jarvis` model. The detector is intentionally
disabled by default, and Push-to-Talk remains an independent way to speak to MINDER.

Do not describe this pretrained model as “Hey MINDER”. The product phrase must not be enabled
until a model trained for that exact phrase is installed and tested.

## Model target

- Product phrase: `Hey MINDER`
- Runtime format: openWakeWord ONNX model
- Runtime sample rate: 16 kHz, mono, signed 16-bit PCM
- Runtime execution: local CPU inference on the detector's worker thread
- Model location reserved by the project: `models/wake/hey_minder.onnx`
- Keep the feature OFF by default; do not start microphone capture merely because the model exists.

## Training requirements

Use the openWakeWord custom-model training workflow, not a renamed pretrained model.
The training configuration must set:

```yaml
model_name: "hey_minder"
target_phrase:
  - "hey minder"
custom_negative_phrases:
  - "hey reminder"
  - "hey mentor"
  - "hey render"
```

The upstream example recommends at least 20,000 positive samples and commonly 100,000+ for
stronger coverage. It also generates adversarial negatives and uses background recordings and
room impulse responses. Training is therefore a separate offline build task; it is not appropriate
to fabricate an ONNX file or to claim the phrase works before a real model is produced.

## Acceptance gate before switching the default

1. Verify the ONNX model loads through the same openWakeWord runtime used by MINDER.
2. Test positive utterances from multiple speakers, distances, speaking speeds and room conditions.
3. Test hard negatives, especially “Hey reminder”, ordinary conversation and TV audio.
4. Measure false activations per hour on representative background audio. Target no more than
   0.2 false activations/hour as a training objective, then verify on held-out recordings.
5. Confirm inference remains local and the audio callback only enqueues frames.
6. Confirm disabling wake word stops the worker and does not leave the microphone active for
   wake detection; confirm Push-to-Talk still works.
7. Only after all gates pass, switch the default phrase and update the UI/status text.

## Compatibility rule

Until the custom model passes the acceptance gate, retain the existing “Hey Jarvis” model only
as an explicitly labelled legacy fallback. Never tell the user that it recognizes “Hey MINDER”.


## Windows 11 compatibility status

The upstream training commands are not an approved native Windows 11 workflow. Review [MINDER_WAKE_WORD_WINDOWS_COMPATIBILITY.md](MINDER_WAKE_WORD_WINDOWS_COMPATIBILITY.md) before installing training dependencies or attempting to train. The current preflight checks prerequisites but does not validate OS-level trainer compatibility. No datasets have been downloaded and no training has started.

## Reproducible project training files

The MINDER-specific configuration and preparation notes now live in
`tools/wake_word_training/`:

- `hey_minder.yml` — openWakeWord training configuration with explicit
  hard-negative phrases and dataset paths.
- `README.md` — required inputs, training stages, and acceptance gate.

The expected final runtime destination is documented in
`models/wake/README.md`. The model itself is not included until it has been
trained and validated.
