# MINDER — Project plan

## Product direction

MINDER is a personal desktop assistant for Windows first, with cross-platform support retained where practical. It should feel like a polished, responsive desktop product—not a demo.

The starting codebase is Mark LVI by FatihMakes. This repository is an independent rebrand/adaptation, not a fork. The upstream attribution and CC BY-NC 4.0 terms remain in place. Read LICENSE before redistributing or using the project commercially.

## Product requirements

- Voice-first interaction with a reliable text fallback.
- Understand requests, plan multi-step work, and use desktop tools.
- Open applications, navigate supported interfaces, write code, and manage files.
- Persistent, inspectable memory with user control.
- Confirm destructive or irreversible actions before execution.
- Clear status, progress, errors, and truthful reporting—never claim an action succeeded unless verified.
- A polished MINDER identity and a premium, restrained Liquid Glass-inspired interface.
- Provider choice belongs to the user. Do not silently change providers or send audio/screen data to a different vendor.

## Hardware and model strategy

Target development machine: Windows 11 with 8 GB RAM.

- Do not require a large local language model. Prefer hosted inference for the main reasoning model until benchmarks prove a local option fits comfortably.
- Keep local speech-to-text and text-to-speech options available where they are practical on CPU.
- Make provider credentials opt-in and store them locally; never commit secrets.
- Gemini Live is optional, not the default. An unconfigured or failed provider must not silently fall back to Gemini.
- Select exact providers, model IDs, free-tier limits, and voice after current verification and a measured comparison. Do not hard-code unverified free-tier claims.

## Implementation phases

1. **Foundation:** import and rebrand the complete source, preserve attribution, establish CI, and identify startup blockers.
2. **Provider setup:** choose and verify the LLM, STT, and TTS providers; add a guided setup flow and connection tests.
3. **Voice experience:** tune microphone selection, push-to-talk, interruption, latency, and the chosen voice. Keep the pretrained “Hey Jarvis” detector disabled until a genuine MINDER wake-word model exists.
4. **Desktop actions:** audit application control, browser automation, file operations, permissions, confirmations, and undo on Windows.
5. **Interface:** progressively replace the inherited HUD styling with the MINDER visual system, keeping functionality intact.
6. **Reliability:** add automated tests, error recovery, logging, packaging, and a repeatable Windows installation path.

## Current repository state

- The complete 78-file Mark LVI snapshot is imported.
- Branding defaults are being changed to MINDER.
- The provider pipeline is the default mode.
- Gemini Live remains optional; the app should not silently send microphone or screen data to Gemini when a selected provider is missing or misconfigured.
- Provider/model/voice choices are intentionally not finalized yet.
- The imported application has not yet been run or validated on the target Windows PC. A successful GitHub commit is not proof that the app launches.

## Working rules

- Make small, reviewable commits.
- Inspect the existing implementation before changing behavior.
- Run syntax checks and focused tests after each change; run the app on Windows before calling a feature complete.
- Back up user configuration before migrations.
- Never commit API keys, tokens, memory databases, recordings, or personal data.
- Keep a change log of behavior changes and known limitations.
