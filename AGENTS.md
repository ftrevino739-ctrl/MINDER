# Rules for coding agents working on MINDER

The built-in coding agent (`actions/dev_agent.py`) reads this file before every
change it makes to this project, and so should any other agent or contributor.
Each rule below exists because breaking it produced code that looked right,
passed a syntax check and did nothing when used.

This codebase retains several inherited internal names such as `JarvisUI` and
`shutdown_jarvis` for compatibility. Do not mass-rename those symbols as part
of routine work. The product and all new user-facing identity must be MINDER.

## How the app runs

- `main.py` holds the voice session on an asyncio event loop in a background
  thread. `ui.py` holds the window on the **Qt main thread**.
- Tools — `actions/*.py` and `plugins/*.py` — run on **worker threads**, never
  on the Qt main thread.
- There is exactly **one** `QApplication`, created by `ui.py`.

## Threading: the rule that breaks most generated code

The Qt main thread must never wait for network calls, provider probes, voice
catalogue requests, model downloads, local model loading, speech synthesis, or
other potentially slow work. Capture widget values first, run the work on a
worker thread, and return only plain result data through a `pyqtSignal` or a
queued Qt callback. Apply results on the Qt thread and discard stale results
when the settings changed while the worker was running. Do not call
`QApplication.processEvents()` as a way to hide a blocking operation.

Code in `actions/` and `plugins/` must **never create or touch a Qt widget**.
That means no `QApplication`, `QWidget`, `QMainWindow`, `QDialog`, `QThread` or
anything else from `PyQt6.QtWidgets` or `PyQt6.QtGui`. Creating a widget from a
worker thread prints `QObject::setParent: Cannot set parent, new parent is in a
different thread` and nothing appears on screen.

To show something, call the **thread-safe methods on `player`** (the `JarvisUI`
object passed to every tool). They hand the work to the Qt thread for you. The
current list, with signatures, is appended below this file by the coding agent
automatically. The ones plugins usually need:

| Need | Call |
|---|---|
| A line in the activity log | `player.write_log("text")` |
| Text in the panel under the head | `player.show_content(title, text)` |
| A quiz the user answers on screen | `player.show_quiz(topic, questions, grade=None)` |
| A structured review | `player.show_review(title, summary, findings)` |
| Play a video in the HUD | `player.show_video(path_or_url, title)` |

`show_quiz` takes `questions` as a list of dicts:
`{"question": str, "options": [str, ...] (empty list for a typed answer),
"answer": str, "note": str (shown after answering, optional)}`.
`grade(question, given) -> True | False | None` is optional; `None` means
"let the assistant judge this answer". The user answers at their own pace and
the result comes back to the conversation by itself — `run()` returns at once.

Do **not** start a separate GUI program (`subprocess` running a PyQt script)
to work around this rule. It hides every error from the user, and the app
already has a screen for it.

## Plugins

A plugin is one file `plugins/<name>.py` (no leading underscore) with:

- `PLUGIN = {"name": ..., "description": ..., "parameters": {"type": "OBJECT", ...}}`
- `def run(parameters: dict, player=None, session_memory=None) -> str`

`run()` must:

- return a **short string** — the assistant turns it into speech;
- **never raise** — catch errors and return a sentence saying what went wrong;
- return **within a few seconds** — put slow work on a `threading.Thread`
  and show its result through `player` when it is done;
- treat `player` as possibly `None` (tests and the phone dashboard).

Helper modules may live beside it with a leading underscore
(`plugins/_helper.py`); the loader skips those. See `plugins/_template.py`.

A plugin is **OFF by default**. Adding a file does not authorise it to run.
The user must enable it in PLUGIN MANAGER after reviewing the source. The
approved SHA-256 is bound to that exact source; editing the file invalidates the
approval and requires a new review. Never edit `config/api_keys.json` directly
to "enable" one — use the config manager and the existing per-source approval
flow. Never weaken the default-OFF or hash-matching checks to make a test pass.

## Asking a model for something

For model calls originating in actions and helpers, use the established
`core.gemini` compatibility facade rather than constructing an unbounded client.
In **MY OWN SETUP** mode it routes to the configured provider pipeline; in
**GEMINI LIVE** mode it uses Gemini. A provider error must never silently switch
to Gemini or another vendor, and audio/screen data must not be sent to a
different provider than the user selected:

```python
from core import gemini
text = gemini.text(prompt, tier=gemini.FAST)       # short answers
data = gemini.as_json(prompt, tier=gemini.SMART)    # structured, may return None
```

Every call can fail or return `None`. Code must handle that — for example with
a small built-in fallback — instead of crashing.

## Changing the window (`ui.py`)

- Widgets are created and changed only on the Qt thread.
- To let other threads trigger a change: add a `pyqtSignal` to `MainWindow`,
  connect it to a slot in `__init__`, and add a thin public method on
  `JarvisUI` that only emits the signal. `show_content` / `_content_sig` /
  `_show_content` is the pattern to copy.
- Colours come from the `C` palette class; fonts are `QFont("Courier New", size)`.

## Persistent memory

`memory/memory_manager.py` is the owner of `memory/long_term.json`.

- Use `update_memory()`, `remember()`, `forget()`, `save_session_summary()` and `pop_last_session()`; do not write the memory JSON from another module.
- Read-modify-write operations must hold the module's re-entrant `_lock` for the whole transaction.
- Persist snapshots through `_write_memory_file()`, which writes a same-directory temporary file and uses `os.replace`. Do not replace this with a direct `write_text()`.
- Keep API keys, access tokens, recordings and unrelated private data out of long-term memory and test fixtures.

## General

- Never send a child process's output to `DEVNULL` without logging it, and
  never report success without checking that the thing actually happened.
- External communication (messages, emails, posts and calls) must use `core.confirm.request` so a human approves the exact recipient, platform and content on the HUD. Never treat a model-supplied `confirmed` value or tool call as human approval; if the confirmation UI is unavailable, fail closed.
- Settings and keys live in `config/api_keys.json` through
  `memory/config_manager.py`; never write that file directly.
- Keep to the libraries already in `requirements.txt`.
