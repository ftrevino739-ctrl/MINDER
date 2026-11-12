"""Pure safeguards for keyboard shortcuts and desktop input actions."""
from __future__ import annotations

_KEY_ALIASES = {
    "return": "enter", "numpadenter": "enter", "del": "delete",
    "control": "ctrl", "command": "meta", "cmd": "meta",
    "windows": "meta", "win": "meta", "super": "meta", "option": "alt",
}
_RISKY_KEYS = {"enter", "delete"}
_RISKY_SHORTCUTS = {
    frozenset(("alt", "f4")),
    frozenset(("ctrl", "w")),
    frozenset(("ctrl", "shift", "w")),
    frozenset(("ctrl", "q")),
    frozenset(("meta", "w")),
    frozenset(("meta", "q")),
    frozenset(("ctrl", "shift", "delete")),
    frozenset(("meta", "shift", "delete")),
    frozenset(("shift", "delete")),
    frozenset(("ctrl", "enter")),
    frozenset(("meta", "enter")),
    frozenset(("ctrl", "alt", "delete")),
    frozenset(("meta", "r")),
}


def _tokens(keys: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(keys, str):
        values = keys.split("+")
    elif isinstance(keys, (list, tuple)):
        values = keys
    else:
        return ()
    return tuple(
        _KEY_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
        for value in values if str(value).strip()
    )


def is_risky_desktop_key(key: str) -> bool:
    """Keys that can submit a form, send a message, or delete a selected item."""
    tokens = _tokens(key)
    return len(tokens) == 1 and tokens[0] in _RISKY_KEYS


def is_risky_desktop_hotkey(keys: str | list[str] | tuple[str, ...]) -> bool:
    """Shortcuts that close windows, submit, permanently delete, or open Run."""
    tokens = _tokens(keys)
    return len(tokens) > 1 and frozenset(tokens) in _RISKY_SHORTCUTS


def requires_desktop_confirmation(action: str, *, key: str = "", keys=(),
                                   clear_first: bool = False,
                                   press_enter: bool = False) -> bool:
    """Identify desktop actions without enough context to safely execute blindly."""
    normalized = (action or "").strip().lower().replace("-", "_")
    if normalized in {"click", "left_click", "double_click", "right_click",
                      "drag", "clear_field", "close_window", "close_tab", "enter"}:
        return True
    if normalized in {"press", "press_key"} and is_risky_desktop_key(key):
        return True
    if normalized == "hotkey" and is_risky_desktop_hotkey(keys):
        return True
    if normalized in {"smart_type", "smart_type_text"} and clear_first:
        return True
    if normalized in {"type_text", "write_on_screen", "type", "write"} and press_enter:
        return True
    return False
