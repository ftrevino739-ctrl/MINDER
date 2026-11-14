"""Core package initialization and cross-platform console safety.

MINDER emits Unicode status markers in its diagnostic logs. Windows Python
consoles may still use legacy code pages (for example cp1252); a plain print()
of an emoji or arrow would then raise UnicodeEncodeError and interrupt an
otherwise safe action. Preserve the current console encoding, but escape only
characters it cannot represent instead of allowing logging to break behavior.
"""
import sys


def _make_console_output_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="backslashreplace")
            except (OSError, ValueError):
                # Embedded consoles and test capture streams may not support it.
                pass


_make_console_output_safe()
