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

1. **Foundation — completed:** import the source independently, preserve attribution/license, establish CI, and apply MINDER identity to core defaults and desktop integration.
2. **Provider setup — in progress:** make the provider pipeline the default, allow first-run setup without a Gemini key, preserve existing config, test provider configuration, and connect a user-selected LLM/STT/TTS stack. Exact providers and models still need selection and measured latency checks.
3. **Voice experience — pending:** tune microphone selection, push-to-talk, interruption, latency, and the chosen voice. Keep the pretrained “Hey Jarvis” detector disabled until a genuine MINDER wake-word model exists.
4. **Desktop actions — pending audit:** verify application control, browser automation, file operations, permissions, confirmations, and undo on Windows.
5. **Interface — pending:** progressively replace the inherited HUD styling with the MINDER visual system, including restrained Liquid Glass materials, while keeping functionality intact.
6. **Reliability and release — in progress:** expand automated tests, error recovery and logging; then validate, package, and document a repeatable Windows installation path.

## Current repository state

- The original 78-file Mark LVI snapshot is imported as an independent copy.
- The default assistant identity, first-run copy, desktop shortcut names, icon output path, and new auto-start registration use MINDER. Legacy Jarvis auto-start entries are still detected so the toggle can disable an existing registration.
- The provider pipeline is the default engine. Gemini Live is only selected explicitly.
- First-run setup no longer blocks a pipeline install on a Gemini API key. A Gemini key is required only when Gemini Live is selected.
- Completing first-run setup preserves existing entries in `config/api_keys.json` instead of replacing the entire file with two fields.
- Pipeline failures are kept out of Gemini's model-quota retry and Gemini key reconfiguration logic. There is no automatic provider-to-Gemini fallback.
- CI checks Python syntax on 3.11, 3.12 and 3.13; it also runs provider-mode tests and a Qt-offscreen provider regression suite. The last verified full run passed 102 provider checks and all three syntax/test matrix jobs.
- The provider regression runner emits a non-fatal warning that multimedia playback is unavailable in the headless Linux CI environment; real audio/video devices are not tested there.
- Provider/model/voice choices are intentionally not finalized yet.
- The application has not yet been run or validated on the target Windows PC. Passing CI is not proof that the Windows app launches or that the user's microphone, speakers, browser and desktop actions work.

## Working rules

- Make small, reviewable commits.
- Inspect the existing implementation before changing behavior.
- Run syntax checks and focused tests after each change; run the app on Windows before calling a feature complete.
- Back up user configuration before migrations.
- Never commit API keys, tokens, memory databases, recordings, or personal data.
- Keep a change log of behavior changes and known limitations.
