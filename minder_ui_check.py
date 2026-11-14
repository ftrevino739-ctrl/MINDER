"""Headless startup smoke test for the actual MINDER Qt desktop window.

This test constructs the real HUD and main window, pumps Qt events, and checks a
thread-safe state update. It does not connect providers, open audio devices,
record from a microphone, access a camera, read personal memory or contact the
network. Audio and memory panels use empty fixtures.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from ui import (
        AudioDeviceOverlay, ClipboardPanel, ConfirmBanner, CustomizeOverlay,
        JarvisUI, MemoryOverlay, PluginManagerOverlay, PluginSettingsOverlay,
        RemoteKeyOverlay, _CameraPreview,
    )

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

        # Instantiate representative frosted-glass overlays without opening
        # devices, reading user memory, enabling plugins or taking any action.
        glass_panels = [
            PluginManagerOverlay([], parent=window.centralWidget()),
            PluginSettingsOverlay([], parent=window.centralWidget()),
            CustomizeOverlay(parent=window.centralWidget()),
            ClipboardPanel(parent=window.centralWidget()),
            _CameraPreview(parent=window.centralWidget()),
            RemoteKeyOverlay(
                "http://127.0.0.1:8765", "smoke-token",
                auto_login_url="http://127.0.0.1:8765/?smoke=1",
                expiry_secs=600, parent=window.centralWidget(),
            ),
            ConfirmBanner("Smoke test", "No action is attached.",
                          parent=window.centralWidget()),
        ]

        # These two panels normally inspect the user's saved memory and the
        # host's audio endpoints. Supply empty, read-only fixtures so CI checks
        # widget construction without reading personal data or opening devices.
        from unittest.mock import patch
        import core.audio_devices as audio_devices
        import memory.config_manager as config_manager
        import memory.memory_manager as memory_manager

        with (
            patch.object(audio_devices, "list_devices", return_value=[]),
            patch.object(audio_devices, "get_output_volume", return_value=None),
            patch.object(config_manager, "get_input_device", return_value=""),
            patch.object(config_manager, "get_output_device", return_value=""),
            patch.object(memory_manager, "all_entries_for_ui", return_value=[]),
        ):
            glass_panels.extend([
                AudioDeviceOverlay(parent=window.centralWidget()),
                MemoryOverlay(parent=window.centralWidget()),
            ])
        for panel in glass_panels:
            if panel.objectName() != "minderGlassSurface":
                raise RuntimeError(
                    f"{type(panel).__name__} did not receive the glass surface")
            if not panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
                raise RuntimeError(
                    f"{type(panel).__name__} is not configured as translucent")
            if panel.graphicsEffect() is None:
                raise RuntimeError(
                    f"{type(panel).__name__} is missing its depth shadow")
            panel.close()
            panel.deleteLater()
        app.processEvents()

        print(f"[OK] Qt application: {QApplication.instance() is app}")
        print(f"[OK] Main window: {window.windowTitle()}")
        print("[OK] HUD and central content: constructed")
        print("[OK] Queued state update: delivered")
        print("[OK] Nine glass overlays: constructed with safe fixtures")
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
