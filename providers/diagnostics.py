"""Background-safe provider diagnostics shared by the settings panel.

These helpers are synchronous by design: callers must run them in a worker
thread, never on the Qt main thread. They return plain data and never touch UI
widgets. API keys are removed from provider error text before it is displayed.
"""
from __future__ import annotations

from typing import Any


def _redact(message: Any, values: dict) -> str:
    text = " ".join(str(message or "").split())
    for key, value in (values or {}).items():
        if not any(part in str(key).lower() for part in ("key", "token", "secret", "password")):
            continue
        secret = str(value or "")
        if len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return text[:180]


def probe_provider(registry, provider_id: str, values: dict) -> dict:
    """Build and probe one provider; return a UI-neutral result dictionary."""
    try:
        instance = registry.build(provider_id, dict(values or {}))
        probe = getattr(instance, "probe", None)
        if not callable(probe):
            return {"ok": None, "message": "Nothing to check for this provider.",
                    "vision": None}
        ok, message = probe()
        caps = getattr(instance, "caps", {}) or {}
        return {
            "ok": bool(ok),
            "message": _redact(message, values),
            "vision": caps.get("vision"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": _redact(f"{type(exc).__name__}: {exc}", values),
            "vision": None,
        }


def list_provider_options(registry, provider_id: str, values: dict) -> dict:
    """Fetch model/voice choices off-thread and return normalised value-label pairs."""
    try:
        instance = registry.build(provider_id, dict(values or {}))
        if callable(getattr(instance, "model_options", None)):
            raw = instance.model_options()
            field = "model"
        elif callable(getattr(instance, "models", None)):
            raw = [(name, name) for name in instance.models()]
            field = "model"
        elif callable(getattr(instance, "voices", None)):
            raw = list(instance.voices())
            field = "voice_id"
        else:
            return {"field": "", "items": [], "message": "Nothing to list for this provider.",
                    "error": False}

        items = []
        for row in raw or []:
            if isinstance(row, (tuple, list)) and len(row) >= 2:
                value, label = row[0], row[1]
            elif isinstance(row, str):
                value = label = row
            else:
                continue
            if value is None or label is None:
                continue
            value, label = str(value).strip(), str(label).strip()
            if value and label:
                items.append((value, label))

        return {
            "field": field,
            "items": items,
            "message": f"{len(items)} available." if items else "None found.",
            "error": False,
        }
    except Exception as exc:
        return {
            "field": "",
            "items": [],
            "message": _redact(f"{type(exc).__name__}: {exc}", values),
            "error": True,
        }
