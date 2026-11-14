import unittest
from pathlib import Path
from unittest.mock import patch

from actions import game_updater as game_module


class GameUpdaterSafetyTests(unittest.TestCase):
    def test_windows_schedule_never_runs_elevated_or_as_system(self):
        fake_result = type("Result", (), {"returncode": 0, "stderr": ""})()
        with (
            patch.object(game_module, "is_windows", return_value=True),
            patch.object(game_module.subprocess, "run", return_value=fake_result) as run,
        ):
            result = game_module._schedule_windows(4, 15)
        self.assertIn("scheduled at 04:15", result)
        args = run.call_args.args[0]
        self.assertIn("JARVIS_GameUpdater", args)
        self.assertNotIn("/RL", args)
        self.assertNotIn("/RU", args)
        self.assertNotIn("SYSTEM", args)

    def test_windows_scheduler_uses_one_create_call_without_predeleting(self):
        fake_result = type("Result", (), {"returncode": 0, "stderr": ""})()
        with (
            patch.object(game_module, "is_windows", return_value=True),
            patch.object(game_module.subprocess, "run", return_value=fake_result) as run,
        ):
            game_module._schedule_windows(4, 15)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1], "/Create")

    def test_install_requires_human_approval_before_execution(self):
        captured = {}
        params = {"action": "install", "platform": "steam", "game_name": "Example Game"}
        with (
            patch.object(game_module.confirm, "pending_title", return_value=""),
            patch.object(
                game_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
            patch.object(game_module, "_game_updater_execute") as execute,
        ):
            result = game_module.game_updater(params)
            self.assertEqual(result, "PENDING")
            execute.assert_not_called()
            self.assertIn("Example Game", captured["detail"])
            self.assertIn("will not shut down", captured["detail"].lower())
            captured["run"]()
            execute.assert_called_once()

    def test_shutdown_is_spelled_out_in_approval(self):
        captured = {}
        with (
            patch.object(game_module.confirm, "pending_title", return_value=""),
            patch.object(
                game_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
        ):
            game_module.game_updater({
                "action": "update", "platform": "steam",
                "shutdown_when_done": True,
            })
        self.assertIn("AUTOMATIC SHUTDOWN", captured["detail"])
        self.assertIn("shut down this computer", captured["detail"])

    def test_schedule_requires_human_approval_and_states_future_runs(self):
        captured = {}
        with (
            patch.object(game_module.confirm, "pending_title", return_value=""),
            patch.object(
                game_module.confirm, "request",
                side_effect=lambda **kwargs: captured.update(kwargs) or "PENDING",
            ),
            patch.object(game_module, "_game_updater_execute") as execute,
        ):
            result = game_module.game_updater({
                "action": "schedule", "hour": 4, "minute": 15, "platform": "both",
            })
        self.assertEqual(result, "PENDING")
        self.assertIn("04:15", captured["detail"])
        self.assertIn("without asking again", captured["detail"])
        execute.assert_not_called()

    def test_install_epic_is_rejected_until_epic_install_is_supported(self):
        with patch.object(game_module.confirm, "request") as request:
            result = game_module.game_updater({
                "action": "install", "platform": "epic", "game_name": "Example Game",
            })
        self.assertIn("only through Steam", result)
        request.assert_not_called()

    def test_schedule_must_match_the_current_both_platforms_task(self):
        with patch.object(game_module.confirm, "request") as request:
            result = game_module.game_updater({
                "action": "schedule", "platform": "steam", "hour": 3, "minute": 0,
            })
        self.assertIn("updates both Steam and Epic", result)
        request.assert_not_called()

    def test_install_requires_a_game_name(self):
        with patch.object(game_module.confirm, "request") as request:
            result = game_module.game_updater({
                "action": "install", "platform": "steam", "app_id": "730",
            })
        self.assertIn("game name is required", result)
        request.assert_not_called()

    def test_shutdown_with_combined_platform_updates_is_rejected(self):
        with patch.object(game_module.confirm, "request") as request:
            result = game_module.game_updater({
                "action": "update", "platform": "both",
                "shutdown_when_done": True,
            })
        self.assertIn("Steam-only downloads", result)
        request.assert_not_called()

    def test_read_only_action_does_not_require_approval(self):
        with (
            patch.object(game_module.confirm, "request") as request,
            patch.object(game_module, "_game_updater_execute", return_value="status") as execute,
        ):
            result = game_module.game_updater({"action": "download_status"})
        self.assertEqual(result, "status")
        execute.assert_called_once()
        request.assert_not_called()

    def test_invalid_app_id_is_rejected(self):
        with patch.object(game_module.confirm, "request") as request:
            result = game_module.game_updater({
                "action": "install", "platform": "steam", "app_id": "730;shutdown",
            })
        self.assertIn("status=failed", result)
        request.assert_not_called()

    def test_steam_install_never_automatically_selects_drive_or_clicks_install(self):
        with (
            patch.object(game_module, "_ensure_steam_running", return_value=True),
            patch.object(game_module, "_steam_exe", return_value=Path("steam.exe")),
            patch.object(game_module, "_get_steam_games", return_value=[]),
            patch.object(game_module, "_search_steam_appid", return_value=("730", "Counter-Strike 2")),
            patch.object(game_module, "_launch_steam_url") as launch,
            patch.object(game_module.threading, "Thread") as thread,
        ):
            result = game_module._install_steam_game(Path("C:/Steam"), game_name="Counter-Strike 2")

        self.assertIn("click Install yourself", result)
        launch.assert_called_once()
        thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
