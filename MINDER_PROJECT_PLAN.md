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
2. **Provider setup — foundation in place; provider selection pending:** the provider pipeline is the default, first-run no longer needs a Gemini key, existing config is preserved, and both conversation turns and one-shot helpers stay on the selected LLM without silently falling back to Gemini. Coding jobs use the explicit CODING provider or the active pipeline LLM. A real endpoint/model/voice combination and measured latency still need to be selected.
3. **Voice experience — pending:** tune microphone selection, push-to-talk, interruption, latency, and the chosen voice. Keep the pretrained “Hey Jarvis” detector disabled until a genuine MINDER wake-word model exists.
4. **Desktop actions — partial audit:** file-operation safeguards are in place and covered by regression tests; application control, browser automation, remaining file workflows, permissions, confirmations, and undo still require a full Windows audit.
5. **Interface — in progress:** applied the MINDER midnight-slate palette and first frosted-glass treatment to onboarding and ENGINE & PROVIDERS, including translucent surfaces, soft shadow, rounded controls and revised typography. Other inherited HUD panels still need a staged visual audit.
6. **Reliability and release — in progress:** expand automated tests, error recovery and logging; then validate, package, and document a repeatable Windows installation path.

## Current repository state

- The original 78-file Mark LVI snapshot is imported as an independent copy.
- The default assistant identity, first-run copy, desktop shortcut names, icon output path, and new auto-start registration use MINDER. Legacy Jarvis auto-start entries are still detected so the toggle can disable an existing registration.
- The provider pipeline is the default engine. Gemini Live is only selected explicitly.
- First-run setup no longer blocks a pipeline install on a Gemini API key. A Gemini key is required only when Gemini Live is selected.
- Completing first-run setup preserves existing entries in `config/api_keys.json` instead of replacing the entire file with two fields.
- Pipeline failures are kept out of Gemini's model-quota retry and Gemini key reconfiguration logic. All one-shot helper calls and selected coding-provider calls are also provider-only in pipeline mode; a missing or failed selected LLM never silently falls through to Gemini.
- The web-search action uses DuckDuckGo in provider mode and never calls Gemini grounding unless the user explicitly selected Gemini Live.
- CI checks Python syntax on 3.11, 3.12 and 3.13; it also runs provider-mode tests and a Qt-offscreen provider regression suite. The last fully completed provider regression run passed 115 checks with zero failures, and the syntax matrix passed on all three versions.
- The provider regression runner emits a non-fatal warning that multimedia playback is unavailable in the headless Linux CI environment; real audio/video devices are not tested there.
- xKiro is now a first-class OpenAI-compatible LLM preset (`https://api.xkiro.com/v1`). Its live catalog metadata is surfaced in the model selector as FREE, PAID or PREMIUM, with tool/vision and context hints. The provider advertises a no-card free tier and up to 500K tokens/day; this allowance is controlled by xKiro and can change. Mistral Small 4 (`mistralai/mistral-small-2603`) is a candidate to test when the live catalog marks it FREE.
- xKiro Voice is available as a separate TTS provider. It reads the public voice list (including `mexican-female`), uses WAV output when supported, and can fall back to MP3 decoding if PyAV is installed. It reuses the saved xKiro LLM key when no TTS-specific key is entered. TTS is billed per character separately from the free chat-token allowance. xKiro does not currently provide STT, so Whisper remains the microphone input provider.
- Provider/model/voice choices are still not finalized. The UI retrieves model lists from compatible endpoints; the final stack should be chosen based on tool-calling compatibility, current limits, latency and measured quality rather than static marketing estimates.
- File-operation safety pass: creating a file refuses to replace an existing path; copy and move refuse existing destinations; overwriting an existing large or non-UTF-8 file requires the UI's human confirmation gate; the target is rechecked before the confirmed write; new files are created exclusively to avoid truncating a path that appeared during the operation. Eight focused regression tests cover these rules and undo behavior.
- The application has not yet been run or validated on the target Windows PC. Passing CI is not proof that the Windows app launches or that the user's microphone, speakers, browser and desktop actions work.

## Working rules

- Make small, reviewable commits.
- Inspect the existing implementation before changing behavior.
- Run syntax checks and focused tests after each change; run the app on Windows before calling a feature complete.
- Back up user configuration before migrations.
- Never commit API keys, tokens, memory databases, recordings, or personal data.
- Keep a change log of behavior changes and known limitations.
