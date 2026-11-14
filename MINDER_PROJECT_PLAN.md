# MINDER — Project plan

## Product direction

MINDER is a personal desktop assistant for Windows first, with cross-platform support retained where practical. It should feel like a polished, responsive desktop product—not a demo.

In plain terms, this is the user's Jarvis-style computer assistant—not an OS or a browser. It combines voice and text conversation, a user-selected reasoning provider, speech recognition and synthesis, safe desktop/browser/file/coding tools, persistent inspectable memory, and a Liquid Glass-inspired HUD. Product-facing identity is MINDER; legacy Jarvis class names are internal compatibility details.

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
2. **Provider setup — foundation in place; provider selection pending:** the provider pipeline is the default, first-run no longer needs a Gemini key, existing config is preserved, and both conversation turns and one-shot helpers stay on the selected LLM without silently falling back to Gemini. Coding jobs use the explicit CODING provider or the active pipeline LLM. Provider probes and model/voice discovery run in background workers. Automatic discovery and manual refresh share a per-provider generation gate; stale responses are discarded, selection-only changes do not invalidate the catalogue, and SHA-256 fingerprints do not expose credentials. Concurrent builds of the same STT/TTS model are coalesced. A real endpoint/model/voice combination and measured latency still need to be selected.
3. **Voice experience — in progress:** microphone device selection and the audio settings tests are in place. The provider regression now drives synthetic PCM through automatic VAD → STT → LLM → TTS and separately checks PTT buffering until release. The desktop audio queue places the PTT release flush after all accepted microphone frames, rejects late frames, and reports queue backpressure rather than raising from the event-loop callback. Real microphone/speaker tests, latency tuning, voice selection, interruption/barging-in and Windows hardware acceptance remain pending. Keep the pretrained “Hey Jarvis” detector disabled until a genuine MINDER wake-word model exists.
4. **Desktop actions — automated safety pass in place; Windows acceptance pending:** browser, desktop input, file copy/move/rename/delete, app launching, reminders, reports, Steam/Epic updates and human-approved messaging have focused regression coverage. Dangerous Windows URI schemes are blocked. Plugin modules are statically inspected and remain off until the user approves the exact SHA-256 source. The actual app, UI, native dialogs, accounts, voice devices and end-to-end workflows still require Windows acceptance.
5. **Interface — in progress:** applied the MINDER midnight-slate palette and frosted-glass treatment to onboarding and ENGINE & PROVIDERS, then extended translucent surfaces, soft shadows and rounded corners across plugin manager/settings, clipboard, confirmation, audio-device, memory and remote-access overlays. The Windows Qt smoke test now constructs representative panels with safe empty fixtures. Remaining work is visual polish on the main HUD and hardware/display acceptance on the target PC.
6. **Reliability and release — in progress:** expand automated tests, error recovery and logging; then validate, package, and document a repeatable Windows installation path.

## Current repository state

- The original 78-file Mark LVI snapshot is imported as an independent copy.
- The default assistant identity, first-run copy, desktop shortcut names, icon output path, and new auto-start registration use MINDER. Legacy Jarvis auto-start entries are still detected so the toggle can disable an existing registration.
- The provider pipeline is the default engine. Gemini Live is only selected explicitly.
- First-run setup no longer blocks a pipeline install on a Gemini API key. A Gemini key is required only when Gemini Live is selected.
- Completing first-run setup preserves existing entries in `config/api_keys.json` instead of replacing the entire file with two fields.
- Pipeline failures are kept out of Gemini's model-quota retry and Gemini key reconfiguration logic. All one-shot helper calls and selected coding-provider calls are also provider-only in pipeline mode; a missing or failed selected LLM never silently falls through to Gemini.
- The web-search action uses DuckDuckGo in provider mode and never calls Gemini grounding unless the user explicitly selected Gemini Live.
- CI checks Python syntax on 3.11, 3.12 and 3.13; it also runs provider-mode tests and a Qt-offscreen provider regression suite. The last fully confirmed run passed after the plugin, launcher and game-updater scheduler changes. Newer commits now add stronger provider-catalogue generation/fingerprint handling and expanded Liquid Glass overlay smoke coverage; the result for the current main head has not yet been confirmed. Game-updater tests cover direct scheduled startup from an external working directory and Linux schedule replacement/cancellation preserving unrelated crontab entries while recognizing legacy JARVIS and intermediate MINDER markers. Automated coverage does not replace Windows acceptance on the target PC.
- The provider regression runner emits a non-fatal warning that multimedia playback is unavailable in the headless Linux CI environment; real audio/video devices are not tested there. Wake-word detector lifecycle has been hardened: each run owns an isolated queue, stop event and model; disabling wake word stops the worker; model-load failures leave the preference disabled. The last confirmed CI run covered Python 3.11, 3.12 and 3.13 plus provider regression. The bundled model still detects the legacy phrase “Hey Jarvis”; keep it disabled until a genuine MINDER model is provided. Push-to-talk polling uses a per-session stop event, so a blocked old keyboard poll cannot change the state of a newly restarted session. Regression tests cover window fallback, callback failures and rapid restart while a simulated Win32 key poll is blocked; these passed on the tested CI head.
- xKiro is now a first-class OpenAI-compatible LLM preset (`https://api.xkiro.com/v1`). Its live catalog metadata is surfaced in the model selector as FREE, PAID or PREMIUM, with tool/vision and context hints. The provider advertises a no-card free tier and up to 500K tokens/day; this allowance is controlled by xKiro and can change. Mistral Small 4 (`mistralai/mistral-small-2603`) is a candidate to test when the live catalog marks it FREE.
- xKiro Voice is available as a separate TTS provider. It reads the public voice list (including `mexican-female`), uses WAV output when supported, and can fall back to MP3 decoding if PyAV is installed. It reuses the saved xKiro LLM key when no TTS-specific key is entered. TTS is billed per character separately from the free chat-token allowance. xKiro does not currently provide STT, so Whisper remains the microphone input provider.
- Provider/model/voice choices are still not finalized. The UI retrieves model lists from compatible endpoints; the final stack should be chosen based on tool-calling compatibility, current limits, latency and measured quality rather than static marketing estimates.
- File-operation safety pass: creating a file refuses to replace an existing path; copy and move refuse existing destinations; overwriting an existing large or non-UTF-8 file requires the UI's human confirmation gate; delete requires a Trash/Recycle Bin approval and target fingerprint recheck; the legacy file-controller desktop organizer routes through the approval preview; undo checks protect later user changes. The Windows acceptance gate is documented in [MINDER_SECURITY_REVIEW.md](MINDER_SECURITY_REVIEW.md).
- The built-in `send_message` action now parks sends behind `core.confirm.request`, displays the exact platform, recipient and complete message in a scrollable review area, refuses oversized messages or requests when the UI is unavailable, and reports actual delivery status separately from approval. Five regression tests cover the gate and its fail-closed behavior.
- External-action audit now also covers app URI schemes, reminder-task collisions, report output overwrites, provider-routed screenshot lookup and third-party plugin execution. Plugins are inspected without execution and require per-source hash approval before first use. Steam account selection and install-dialog clicks remain manual. See [MINDER_SECURITY_REVIEW.md](MINDER_SECURITY_REVIEW.md) for exact coverage and the Windows acceptance gate.
- The application has not yet been run or validated on the target Windows PC. Passing CI is not proof that the Windows app launches or that the user's microphone, speakers, browser and desktop actions work.
- Added a read-only `minder_doctor.py` command for local environment, dependency, provider-selection, audio-device and Playwright browser-runtime diagnostics. It checks whether Chromium and Firefox binaries exist without launching a browser or making network requests; it never records audio, changes settings or prints API-key values. Audio-device regression tests cover list caching, sample-rate invalidation, callback microphone transport, clock-paced speaker transport and failed-probe caching. Long-term memory uses same-directory temporary files plus `os.replace` for atomic persistence, and an `RLock` protects read-modify-write transactions shared by remembered facts and session summaries. Regression tests cover failed replacements and concurrent updates. The Windows CI job installs Chromium and Firefox, launches both against local-only HTML/JavaScript, imports the app, constructs the actual Qt window in offscreen mode, checks the HUD and queued state updates, then runs the unit/safety suite. In the last fully confirmed Windows run, both browser engines, the doctor, app import, Qt window smoke test and all 217 unit/safety tests passed (one POSIX-permission test is correctly skipped on Windows); the same workflow passed 128 provider regression checks and Python 3.11, 3.12 and 3.13. The current smoke test has since been expanded to construct eight Liquid Glass panels with safe empty fixtures, and additional catalogue-gate tests have been added. This newer main-head CI result is not yet confirmed. Windows Server CI is not acceptance on the target Windows 11 PC.

## Working rules

- Make small, reviewable commits.
- Inspect the existing implementation before changing behavior.
- Run syntax checks and focused tests after each change; run the app on Windows before calling a feature complete.
- Back up user configuration before migrations.
- Never commit API keys, tokens, memory databases, recordings, or personal data.
- Keep a change log of behavior changes and known limitations.
