# MINDER — Real Windows voice acceptance

This checklist validates the real microphone → STT → selected LLM → TTS → speaker path on the target Windows 11 PC. GitHub CI uses synthetic PCM and mocked providers; it cannot certify physical devices or account credentials.

## Before testing

1. Use a quiet room and a short, non-sensitive test phrase. Do not dictate passwords, private messages or personal documents.
2. Launch MINDER from its project folder using the configured Python virtual environment.
3. Open **ENGINE & PROVIDERS** and explicitly configure:
   - **LLM:** select the intended provider, endpoint, model and credentials; use the panel's TEST action.
   - **STT:** select Whisper and a supported model/service. xKiro is TTS-only and cannot transcribe speech.
   - **TTS:** select the intended system voice, Piper, ElevenLabs or xKiro Voice.
4. Open **AUDIO DEVICES**. Select the intended microphone and speakers by their displayed names. Run the microphone-level check, then the speaker test. The speaker test is user-confirmed; keep the volume low initially.
5. Keep wake-word detection disabled. The bundled wake-word model still recognises “Hey Jarvis”, not “Hey MINDER”.

## End-to-end test

1. Use push-to-talk (PTT) with the configured shortcut. Say: **“MINDER, responde solamente: prueba de voz completada.”**
2. Release PTT and verify all four observations:
   - the HUD enters its listening/processing state;
   - the transcript matches the spoken Spanish phrase;
   - the selected LLM returns a relevant answer;
   - the selected TTS voice is audible through the selected output device.
3. Repeat once with a different short Spanish sentence to rule out a lucky transcription.
4. While MINDER is speaking, press the interrupt control or the Escape key. Verify playback stops promptly and a later turn can still complete.
   - In Pipeline mode, this is an explicit user-triggered interrupt: it cancels the in-flight LLM/TTS task on the provider's asyncio loop and emits a turn-complete event so the receive loop resumes listening. It is not automatic barge-in; speaking over the assistant without pressing the control is not yet a supported interruption trigger.
   - A tool or desktop action already dispatched before interruption is not forcibly cancelled; verify its confirmation/safety gate still governs any external side effect. Its late result is recorded, but does not automatically restart the interrupted spoken answer or replace a newer user request. In a multi-action batch, calls not yet admitted for dispatch when interruption arrives are returned as not executed and must not launch apps, files or shutdown actions.
5. Leave the microphone silent for several seconds. Verify MINDER does not invent a transcript or start an unintended turn.
6. Close and relaunch MINDER, then repeat one short PTT turn to verify the selected provider/device settings persist.

## Diagnose by layer

| Observation | Likely layer to inspect |
| --- | --- |
| Microphone level never moves | Windows microphone permission, selected input device, mute switch, or audio transport |
| Level moves but transcript is empty/wrong | STT provider, language setting, model availability, microphone gain or noise |
| Transcript is correct but no answer arrives | LLM provider test, model ID, endpoint, quota or network |
| Answer appears but no speech is heard | TTS provider/voice, selected output device, Windows volume or audio transport |
| First turn works but later turns stall | interruption/session lifecycle, provider logs and PTT queue |
| Voice works but wake word does not | Expected until a genuine MINDER wake-word model is supplied; do not enable the legacy model |

## Record a safe test report

Record only:

- Windows edition/build and Python version;
- MINDER commit SHA;
- LLM/STT/TTS provider IDs and model/voice names (never API keys);
- microphone and speaker device names;
- whether each step passed, and the exact error text for failures.

Before sharing logs, remove API keys, authorization headers, private prompts, account identifiers and personal audio/transcripts. Do not upload raw microphone recordings.

## Pass criteria

The voice path is accepted only when two consecutive PTT turns complete through STT → selected LLM → TTS, the interrupt stops playback, silence does not trigger a turn, and the settings survive a restart. A successful synthetic CI test or a successful speaker tone alone is not an end-to-end pass.
