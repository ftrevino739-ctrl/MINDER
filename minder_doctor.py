"""
MINDER Doctor — read-only local installation diagnostics.

Run with: python minder_doctor.py

This script never starts MINDER, records audio, makes network requests, changes
configuration, or prints API keys. It is intended to collect a safe first-pass
report before Windows acceptance testing.
"""
from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_REQUIRED_MODULES = (
    ("PyQt6", "PyQt6"),
    ("NumPy", "numpy"),
    ("SoundDevice", "sounddevice"),
    ("HTTPX", "httpx"),
    ("Requests", "requests"),
    ("Playwright", "playwright"),
    ("PyAutoGUI", "pyautogui"),
    ("Pyperclip", "pyperclip"),
)


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _line(status: str, label: str, detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def _provider_report() -> None:
    try:
        from memory import config_manager as config
        mode = config.get_provider_mode()
        pipeline = config.get_pipeline()
        _line("OK", "Engine mode", mode)

        for slot in ("llm", "stt", "tts"):
            provider_id = str(pipeline.get(slot) or "").strip()
            if not provider_id:
                _line("WARN", f"{slot.upper()} provider", "not selected")
                continue

            settings = config.get_provider_config(provider_id)
            model = str(settings.get("model") or settings.get("model_id") or "").strip()
            key_present = bool(str(settings.get("api_key") or "").strip())
            detail = provider_id
            if model:
                detail += f" / model: {model}"
            if "@" in provider_id and not provider_id.split("@", 1)[1]:
                detail += " / incomplete service id"
            if key_present:
                detail += " / API key: configured (value hidden)"
            elif str(settings.get("base_url") or "").startswith(("http://localhost", "http://127.0.0.1")):
                detail += " / local endpoint (no key shown)"
            else:
                detail += " / API key: not detected"
            _line("OK", f"{slot.upper()} provider", detail)

        if mode == "gemini":
            _line("INFO", "Gemini Live", "explicitly selected; credential value is never displayed")
    except Exception as exc:
        _line("WARN", "Provider configuration", f"could not inspect safely: {type(exc).__name__}: {exc}")


def _audio_report() -> None:
    if not _module_available("sounddevice"):
        _line("WARN", "Audio devices", "sounddevice is not installed")
        return
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_input, default_output = sd.default.device
        _line("OK" if len(devices) else "WARN", "Audio device enumeration",
              f"{len(devices)} device(s) visible")
        for direction, index in (("input", default_input), ("output", default_output)):
            try:
                idx = int(index)
                if idx < 0:
                    raise ValueError("no default device")
                device = sd.query_devices(idx)
                name = str(device.get("name") or "unnamed device")
                _line("OK", f"Default audio {direction}", f"device {idx}: {name}")
            except Exception:
                _line("WARN", f"Default audio {direction}", "no usable default device reported")
    except Exception as exc:
        _line("WARN", "Audio device enumeration", f"{type(exc).__name__}: {exc}")


def main() -> int:
    print("MINDER DOCTOR - read-only diagnostics")
    print("=" * 48)
    print(f"Operating system: {platform.platform()}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"Project: {ROOT}")

    version = sys.version_info[:2]
    if (3, 11) <= version <= (3, 13):
        _line("OK", "Python version", "within the tested 3.11-3.13 range")
    elif version < (3, 11):
        _line("FAIL", "Python version", "MINDER requires Python 3.11 or newer")
    else:
        _line("WARN", "Python version", "newer than the currently tested 3.13 maximum")

    missing = []
    for label, module_name in _REQUIRED_MODULES:
        available = _module_available(module_name)
        _line("OK" if available else "FAIL", f"Dependency: {label}",
              "importable" if available else "not installed")
        if not available:
            missing.append(label)

    face_model = ROOT / "core" / "face_model.obj"
    if face_model.is_file() and face_model.stat().st_size >= 4096:
        _line("OK", "HUD face asset", f"{face_model.stat().st_size:,} bytes")
    else:
        _line("WARN", "HUD face asset", "missing or truncated; the HUD may use its fallback")

    config_file = ROOT / "config" / "api_keys.json"
    _line("OK" if config_file.is_file() else "INFO", "Local configuration",
          "present (contents hidden)" if config_file.is_file() else "not created yet")

    _provider_report()
    _audio_report()

    if platform.system() == "Windows":
        for label, module_name in (
            ("pywin32 / COM", "win32com"),
            ("Windows UI automation", "pywinauto"),
            ("Windows audio controls", "pycaw"),
        ):
            available = _module_available(module_name)
            _line("OK" if available else "WARN", label,
                  "available" if available else "optional Windows integration not detected")

    print("=" * 48)
    if missing:
        print("RESULT: INCOMPLETE — install the missing required dependencies with:")
        print("  python -m pip install -r requirements.txt")
        return 1
    print("RESULT: BASIC ENVIRONMENT CHECK PASSED")
    print("This does not prove microphone capture, speaker playback, UI automation,")
    print("provider connectivity, or native Windows dialogs work end to end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
