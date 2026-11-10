# MINDER — Personal Desktop Assistant

MINDER is a Windows-first desktop assistant adapted from [Mark LVI by FatihMakes](https://github.com/FatihMakes/Mark-LVI). This repository is an independent copy and adaptation, not a GitHub fork. The upstream attribution and **CC BY-NC 4.0** terms are preserved; see [LICENSE](LICENSE) before sharing or redistributing it. Commercial use is not permitted by that license.

> **Development status:** foundation and provider integration are in progress. The application has not yet been verified on the target Windows 11 computer. A green syntax check is not proof that the desktop app launches or that every feature works.

## Current direction

- Personal assistant for Windows first; the inherited codebase also contains macOS and Linux paths.
- Voice input → language model → speech output, with text interaction as a fallback.
- Desktop automation, file operations, browser control, memory, undo and confirmation features are inherited from the source and are being audited before being treated as production-ready.
- Provider choice must be explicit. The default engine is the configurable **MY OWN SETUP** pipeline. Main conversation turns, coding jobs and one-shot helper requests use the selected LLM; they do not silently fall back to Gemini. Gemini Live is an optional mode.
- The MINDER interface is being restyled with a midnight-slate palette, translucent frosted-glass setup/provider panels, soft depth, rounded controls and Segoe UI typography. This is the first visual layer, not a claim that every inherited screen is finished.

## Windows installation

Use Python 3.11 or newer. Python 3.12 is a sensible starting point for this project.

Open PowerShell in the repository folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe setup.py
.\.venv\Scripts\python.exe main.py
```

The setup script installs the dependencies listed in `requirements.txt` and attempts to install the Chromium and Firefox browsers used by browser automation. The browser download is non-fatal; it can be retried later with:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium firefox
```

Do not put API keys in source files or commits. Runtime configuration is stored locally in `config/api_keys.json`; keep that file private.

## Configure the assistant

Open **ENGINE & PROVIDERS** in MINDER and choose **MY OWN SETUP**.

1. **LLM (required):** choose a service in the OpenAI-compatible provider catalog, refresh its live model list, select a model, and save the connection. The catalog includes local endpoints such as Ollama and LM Studio and hosted endpoints such as xKiro, Groq, OpenAI, DeepSeek, Mistral, xAI and OpenRouter. xKiro's model dropdown labels each option **FREE**, **PAID** or **PREMIUM** using the provider's live `access_tier` metadata; a model appearing in its public catalog does not by itself mean your account can call it. A compatible custom endpoint can also be entered.
2. **STT (for voice input):** choose Whisper. Its catalog includes local Whisper (requires the optional `faster-whisper` package and a model download) and hosted transcription options such as Groq Whisper and OpenAI Whisper.
3. **TTS (for spoken replies):** choose the computer's installed system voice, Piper (optional local neural voice), ElevenLabs or **xKiro Voice**. xKiro exposes a public voice catalogue that includes `mexican-female`; its speech API is billed per character and is not the same allowance as the free chat-model token quota. When xKiro is also your LLM provider, MINDER can reuse that saved xKiro key for TTS; an override key can be entered in the TTS panel. xKiro currently provides TTS but not speech-to-text, so use Whisper for microphone transcription.

For a Windows PC with 8 GB RAM, start with a hosted LLM rather than downloading a large language model locally. Local speech recognition can also use substantial memory; start with a small Whisper model if you install it.

### xKiro option

MINDER includes xKiro at `https://api.xkiro.com/v1`. xKiro advertises a no-card free tier with a daily token allowance (currently advertised as up to 500,000 tokens/day); this is a provider-side allowance, not a MINDER guarantee, and limits/eligibility can change. The dropdown reads the live model catalogue and marks free, paid and premium access tiers to make the distinction visible.

A model worth checking first is **Mistral Small 4** (`mistralai/mistral-small-2603`) when xKiro's live catalogue marks it FREE for your account. The provider's published catalogue describes it as a 256K-context model with tool use, vision and reasoning capabilities. Always use the current tier label in MINDER rather than relying on a model name or an old list.

### Privacy and fallback behavior

- A fresh configuration defaults to the provider pipeline.
- If the pipeline has no configured LLM or its required settings are invalid, MINDER stays idle and reports that provider setup is required.
- Main conversation and one-shot helper calls in provider mode stay on the selected LLM. A failed provider does not trigger a Gemini fallback or an implicit Gemini credential read.
- Coding jobs use the separately selected CODING provider when configured; otherwise they use the active pipeline LLM. The coding provider is called provider-only.
- In provider mode, MINDER's web-search action uses the DuckDuckGo path and does not contact Gemini for grounding. Gemini-grounded search is available only after the user explicitly selects Gemini Live.
- Gemini Live is used only after the user explicitly selects that engine and configures its credentials.
- The bundled wake-word model still recognises **“Hey Jarvis”**. Keep wake-word detection disabled until a genuine MINDER wake-word model is available.

## Tests and verification

The GitHub Actions workflow checks Python syntax on Python 3.11, 3.12 and 3.13, runs standard-library provider-mode tests, and runs the Qt-offscreen provider regression suite. The latest fully completed provider regression run passed 115 checks with zero failures; the latest syntax matrix passed on all three Python versions.

After installing the application dependencies, the provider package also has a local regression suite:

```powershell
.\.venv\Scripts\python.exe -m providers._tests
```

That suite exercises the provider pipeline with mocks and a local test server. It does not replace a real test on Windows with your microphone, speakers, browser and installed applications.

## Project notes

- [MINDER project plan](MINDER_PROJECT_PLAN.md)
- [Upstream source](https://github.com/FatihMakes/Mark-LVI)
- [CC BY-NC 4.0 license](https://creativecommons.org/licenses/by-nc/4.0/)

Before calling a feature complete, verify it on the target Windows machine. Never claim an action succeeded unless its result was actually observed, and require confirmation for destructive or irreversible operations.
