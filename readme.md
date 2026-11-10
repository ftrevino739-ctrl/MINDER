# MINDER — Personal Desktop Assistant

MINDER is a Windows-first desktop assistant adapted from [Mark LVI by FatihMakes](https://github.com/FatihMakes/Mark-LVI). This repository is an independent copy and adaptation, not a GitHub fork. The upstream attribution and **CC BY-NC 4.0** terms are preserved; see [LICENSE](LICENSE) before sharing or redistributing it. Commercial use is not permitted by that license.

> **Development status:** foundation and provider integration are in progress. The application has not yet been verified on the target Windows 11 computer. A green syntax check is not proof that the desktop app launches or that every feature works.

## Current direction

- Personal assistant for Windows first; the inherited codebase also contains macOS and Linux paths.
- Voice input → language model → speech output, with text interaction as a fallback.
- Desktop automation, file operations, browser control, memory, undo and confirmation features are inherited from the source and are being audited before being treated as production-ready.
- Provider choice must be explicit. The default engine is the configurable **MY OWN SETUP** pipeline. Gemini Live is an optional mode, not an automatic fallback.
- The interface will progressively receive a distinct MINDER visual identity with a restrained Liquid Glass-inspired treatment. Existing functionality takes priority over cosmetic rewrites.

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

1. **LLM (required):** choose a service in the OpenAI-compatible provider catalog, press refresh to load the models offered by that endpoint, select a model, and save the connection. The catalog includes local endpoints such as Ollama and LM Studio and hosted endpoints such as Groq, OpenAI, DeepSeek, Mistral, xAI and OpenRouter. A compatible custom endpoint can also be entered.
2. **STT (for voice input):** choose Whisper. Its catalog includes local Whisper (requires the optional `faster-whisper` package and a model download) and hosted transcription options such as Groq Whisper and OpenAI Whisper.
3. **TTS (for spoken replies):** choose the computer's installed system voice, Piper (optional local neural voice), or another listed voice provider.

For a Windows PC with 8 GB RAM, start with a hosted LLM rather than downloading a large language model locally. Local speech recognition can also use substantial memory; start with a small Whisper model if you install it.

### Privacy and fallback behavior

- A fresh configuration defaults to the provider pipeline.
- If the pipeline has no configured LLM or its required settings are invalid, MINDER stays idle and reports that provider setup is required.
- MINDER must not silently switch to Gemini or send microphone/screen data to Gemini because another provider failed.
- Gemini Live is used only after the user explicitly selects that engine and configures its credentials.
- The bundled wake-word model still recognises **“Hey Jarvis”**. Keep wake-word detection disabled until a genuine MINDER wake-word model is available.

## Tests and verification

The GitHub Actions workflow currently checks Python syntax on Python 3.11, 3.12 and 3.13, and runs standard-library tests for the provider-mode configuration defaults.

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
