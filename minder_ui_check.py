"""Headless startup smoke test for the actual MINDER Qt desktop window.

This test constructs the real HUD and main window, pumps Qt events, and checks a
thread-safe state update. It does not connect providers, open audio devices,
record from a microphone, access a camera, or contact the network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))

    from PyQt6.QtWidgets import QApplication
    from ui import JarvisUI

    ui = None
    app = None
    try:
        ui = JarvisUI(str(root / "core" / "face_model.obj"))
        app = QApplication.instance()
        if app is None or app is not ui._app:
            raise RuntimeError("MINDER did not retain its single QApplication instance")

        window = ui._win
        app.processEvents()

        if not window.isVisible():
            raise RuntimeError("MINDER main window did not become visible")
        if not window.windowTitle().startswith("MINDER"):
            raise RuntimeError(f"Unexpected window title: {window.windowTitle()!r}")
        if window.hud is None or window.centralWidget() is None:
            raise RuntimeError("MINDER HUD or central window content was not constructed")

        # Exercise the same queued signal path used by the voice worker thread.
        ui.set_state("LISTENING")
        app.processEvents()
        if window.hud.state != "LISTENING":
            raise RuntimeError("Thread-safe HUD state update was not delivered")

        print(f"[OK] Qt application: {QApplication.instance() is app}")
        print(f"[OK] Main window: {window.windowTitle()}")
        print("[OK] HUD and central content: constructed")
        print("[OK] Queued state update: delivered")
        print("RESULT: MINDER DESKTOP UI STARTUP PASSED")
        return 0
    finally:
        if ui is not None:
            try:
                ui._win.close()
                ui._win.deleteLater()
            except Exception:
                pass
        if app is not None:
            try:
                app.processEvents()
                app.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
