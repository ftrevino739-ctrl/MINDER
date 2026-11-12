import os
import platform
import re
import sys
import json
import time
import subprocess
import threading
from pathlib import Path
from datetime import datetime

from config import get_os, is_windows, is_mac, is_linux
from core import confirm, outcome

_CNW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if platform.system() == "Windows" else {}
)

_KNOWN_APPIDS: dict[str, tuple[str, str]] = {
    "pubg":                ("578080",  "PUBG: Battlegrounds"),
    "pubg battlegrounds":  ("578080",  "PUBG: Battlegrounds"),
    "pubg: battlegrounds": ("578080",  "PUBG: Battlegrounds"),
    "battlegrounds":       ("578080",  "PUBG: Battlegrounds"),
    "gta5":                ("271590",  "Grand Theft Auto V"),
    "gta v":               ("271590",  "Grand Theft Auto V"),
    "grand theft auto v":  ("271590",  "Grand Theft Auto V"),
    "cs2":                 ("730",     "Counter-Strike 2"),
    "csgo":                ("730",     "Counter-Strike 2"),
    "counter-strike 2":    ("730",     "Counter-Strike 2"),
    "counter strike 2":    ("730",     "Counter-Strike 2"),
    "dota2":               ("570",     "Dota 2"),
    "dota 2":              ("570",     "Dota 2"),
    "rust":                ("252490",  "Rust"),
    "valheim":             ("892970",  "Valheim"),
    "cyberpunk":           ("1091500", "Cyberpunk 2077"),
    "cyberpunk 2077":      ("1091500", "Cyberpunk 2077"),
    "elden ring":          ("1245620", "ELDEN RING"),
    "minecraft":           ("1672970", "Minecraft Launcher"),
    "apex legends":        ("1172470", "Apex Legends"),
    "apex":                ("1172470", "Apex Legends"),
    "fortnite":            ("1517990", "Fortnite"),
    "goose goose duck":    ("1568590", "Goose Goose Duck"),
    "among us":            ("945360",  "Among Us"),
    "fall guys":           ("1097150", "Fall Guys"),
    "rocket league":       ("252950",  "Rocket League"),
    "warframe":            ("230410",  "Warframe"),
    "destiny 2":           ("1085660", "Destiny 2"),
    "team fortress 2":     ("440",     "Team Fortress 2"),
    "tf2":                 ("440",     "Team Fortress 2"),
    "left 4 dead 2":       ("550",     "Left 4 Dead 2"),
    "l4d2":                ("550",     "Left 4 Dead 2"),
    "paladins":            ("444090",  "Paladins"),
    "smite":               ("386360",  "SMITE"),
    "war thunder":         ("236390",  "War Thunder"),
    "world of warships":   ("552990",  "World of Warships"),
    "path of exile":       ("238960",  "Path of Exile"),
    "poe":                 ("238960",  "Path of Exile"),
    "lost ark":            ("1599340", "Lost Ark"),
    "new world":           ("1063730", "New World: Aeternum"),
}

def _find_steam_path() -> Path | None:
    if is_windows(): return _find_steam_windows()
    if is_mac():     return _find_steam_mac()
    return _find_steam_linux()


def _find_steam_windows() -> Path | None:
    try:
        import winreg
        for hive, key_path in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam"),
        ]:
            try:
                key = winreg.OpenKey(hive, key_path)
                val, _ = winreg.QueryValueEx(key, "InstallPath")
                winreg.CloseKey(key)
                p = Path(val)
                if p.exists() and (p / "steam.exe").exists():
                    return p
            except Exception:
                continue
    except ImportError:
        pass
    for p in [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Steam",
        Path(os.environ.get("ProgramFiles", ""))       / "Steam",
        Path("C:/Steam"), Path("D:/Steam"), Path("E:/Steam"), Path("F:/Steam"),
    ]:
        if p.exists() and (p / "steam.exe").exists():
            return p
    return None


def _find_steam_mac() -> Path | None:
    for p in [
        Path.home() / "Library" / "Application Support" / "Steam",
        Path("/Applications/Steam.app/Contents/MacOS"),
    ]:
        if p.exists():
            return p
    return None


def _find_steam_linux() -> Path | None:
    for p in [
        Path.home() / ".steam" / "steam",
        Path.home() / ".steam" / "root",
        Path.home() / ".local"  / "share" / "Steam",
        Path("/usr/share/steam"),
        Path("/opt/steam"),
    ]:
        if p.exists():
            return p
    return None


def _steam_exe(steam_path: Path) -> Path:
    if is_windows(): return steam_path / "steam.exe"
    if is_mac():     return Path("/Applications/Steam.app/Contents/MacOS/steam_osx")
    return steam_path / "steam.sh"


def _launch_steam_url(exe: Path, url: str) -> None:
    if is_mac():
        subprocess.Popen(["open", url])
    elif is_linux():
        subprocess.Popen(["xdg-open", url])
    else:
        subprocess.Popen([str(exe), url])

def _get_steam_libraries(steam_path: Path) -> list[Path]:
    libraries = [steam_path / "steamapps"]
    vdf_path  = steam_path / "steamapps" / "libraryfolders.vdf"
    if not vdf_path.exists():
        return libraries
    try:
        content = vdf_path.read_text(encoding="utf-8", errors="ignore")
        for raw_path in re.findall(r'"path"\s+"([^"]+)"', content):
            lib = Path(raw_path.replace("\\\\", "/")) / "steamapps"
            if lib.exists() and lib not in libraries:
                libraries.append(lib)
    except Exception:
        pass
    return libraries


def _get_steam_games(steam_path: Path) -> list[dict]:
    games = []
    for lib in _get_steam_libraries(steam_path):
        for acf in lib.glob("appmanifest_*.acf"):
            try:
                content  = acf.read_text(encoding="utf-8", errors="ignore")
                app_id   = re.search(r'"appid"\s+"(\d+)"',     content)
                name     = re.search(r'"name"\s+"([^"]+)"',     content)
                state    = re.search(r'"StateFlags"\s+"(\d+)"', content)
                size     = re.search(r'"SizeOnDisk"\s+"(\d+)"', content)
                if app_id and name:
                    games.append({
                        "id":    app_id.group(1),
                        "name":  name.group(1),
                        "state": int(state.group(1)) if state else 0,
                        "size":  int(size.group(1))  if size  else 0,
                        "lib":   str(lib),
                        "acf":   str(acf),
                    })
            except Exception:
                continue
    return games

def _is_steam_running() -> bool:
    try:
        if is_windows():
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                                 capture_output=True, text=True, **_CNW).stdout
            return "steam.exe" in out.lower()
        proc = "steam_osx" if is_mac() else "steam"
        return bool(subprocess.run(["pgrep", "-x", proc],
                                   capture_output=True, text=True).stdout.strip())
    except Exception:
        return False

# Steam profile and installation dialogs are intentionally left to the human.
# MINDER must not select an account, pick a drive, or click Install on its own.

def _ensure_steam_running(steam_path: Path) -> bool:
    if _is_steam_running():
        return True

    exe = _steam_exe(steam_path)
    if not exe.exists():
        print(f"[GameUpdater] ❌ Steam not found: {exe}")
        return False

    print("[GameUpdater] 🚀 Starting Steam...")
    if is_mac():
        subprocess.Popen(["open", "-a", "Steam"])
    else:
        subprocess.Popen([str(exe)])

    for _ in range(20):
        time.sleep(1)
        if _is_steam_running():
            print("[GameUpdater] ✅ Steam is running")
            time.sleep(4)
            return True

    print("[GameUpdater] ⚠️ Could not start Steam")
    return False

def _search_steam_appid(game_name: str) -> tuple[str | None, str | None]:
    name_lower = game_name.lower().strip()


    steam_path = _find_steam_path()
    if steam_path:
        for g in _get_steam_games(steam_path):
            if name_lower in g["name"].lower():
                return g["id"], g["name"]

    if name_lower in _KNOWN_APPIDS:
        app_id, canonical = _KNOWN_APPIDS[name_lower]
        print(f"[GameUpdater] 📖 Bilinen: {canonical} ({app_id})")
        return app_id, canonical

    for key, (app_id, canonical) in _KNOWN_APPIDS.items():
        if name_lower in key or key in name_lower:
            print(f"[GameUpdater] 📖 Partial match: {canonical} ({app_id})")
            return app_id, canonical

    try:
        import urllib.request, urllib.parse
        query = urllib.parse.quote(game_name)
        url   = f"https://store.steampowered.com/api/storesearch/?term={query}&l=english&cc=US"
        req   = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            items = json.loads(resp.read().decode()).get("items", [])
        if items:
            best = items[0]
            print(f"[GameUpdater] 🌐 Store API: {best['name']} ({best['id']})")
            return str(best["id"]), best["name"]
    except Exception as e:
        print(f"[GameUpdater] ⚠️ AppID lookup failed: {e}")

    return None, None

def _update_steam_games(steam_path: Path, game_name: str = None) -> str:
    if not _ensure_steam_running(steam_path):
        return "Could not start Steam."

    exe   = _steam_exe(steam_path)
    games = _get_steam_games(steam_path)
    if not games:
        return "No Steam games found."

    if game_name:
        name_lower = game_name.lower()
        targets    = [g for g in games if name_lower in g["name"].lower()]
        if not targets:
            available = ", ".join(g["name"] for g in games[:5])
            return f"Game '{game_name}' not found. Installed: {available}..."
    else:
        targets = games

    already_updated, already_running, update_started, errors = [], [], [], []

    for game in targets:
        state = game["state"]
        name  = game["name"]
        if state == 4:
            already_updated.append(name)
        elif state == 1026:
            already_running.append(name)
        else:
            try:
                _launch_steam_url(exe, f"steam://update/{game['id']}")
                update_started.append(name)
                time.sleep(0.3)
            except Exception as e:
                errors.append(f"{name}: {e}")

    parts = []
    if update_started:
        names  = ", ".join(update_started[:3])
        suffix = f" and {len(update_started) - 3} more" if len(update_started) > 3 else ""
        parts.append(f"Update started for: {names}{suffix}.")
    if already_running:
        parts.append(f"Already updating: {', '.join(already_running)}.")
    if already_updated:
        parts.append(
            f"{already_updated[0]} is already up to date."
            if game_name else
            f"{len(already_updated)} game(s) already up to date."
        )
    if errors:
        parts.append(f"Errors: {'; '.join(errors)}.")
    return " ".join(parts) if parts else "No games to update."

def _install_steam_game(steam_path: Path, game_name: str = None,
                        app_id: str = None) -> str:
    if not _ensure_steam_running(steam_path):
        return "Could not start Steam."

    exe             = _steam_exe(steam_path)
    installed_games = _get_steam_games(steam_path)

    already = None
    if app_id:
        already = next((g for g in installed_games if g["id"] == str(app_id)), None)
    elif game_name:
        name_lower = game_name.lower()
        already    = next((g for g in installed_games
                           if name_lower in g["name"].lower()), None)
    else:
        return "Please specify a game name or AppID."

    if already:
        state = already["state"]
        name  = already["name"]
        if state == 4:
            return f"'{name}' is already installed and up to date."
        if state == 1026:
            return f"'{name}' is currently downloading or updating."
        if state in (6, 516):
            _launch_steam_url(exe, f"steam://update/{already['id']}")
            return f"'{name}' has a pending update. Update started."
        return f"'{name}' is already installed."

    if not app_id and game_name:
        found_id, found_name = _search_steam_appid(game_name)
        if not found_id:
            return (f"Could not find '{game_name}' on Steam. "
                    f"Try providing the AppID directly.")
        app_id    = found_id
        game_name = found_name or game_name
        print(f"[GameUpdater] 🔍 Kuruluyor: {game_name} (AppID: {app_id})")

    try:
        _launch_steam_url(exe, f"steam://install/{app_id}")

        return (
            f"Steam opened the install dialog for '{game_name or app_id}'. "
            "Choose the library/drive and click Install yourself in Steam."
        )
    except Exception as e:
        return f"Install failed: {e}"

def _get_download_status(steam_path: Path) -> str:
    games   = _get_steam_games(steam_path)
    active  = [g for g in games if g["state"] == 1026]
    pending = [g for g in games if g["state"] in (6, 516)]
    lines   = []
    if active:
        lines.append(f"Downloading: {', '.join(g['name'] for g in active)}.")
    if pending:
        names  = ", ".join(g["name"] for g in pending[:5])
        suffix = f" and {len(pending) - 5} more" if len(pending) > 5 else ""
        lines.append(f"Pending updates: {names}{suffix}.")
    return " ".join(lines) if lines else "No active downloads or pending updates."


def _system_shutdown() -> None:
    if is_windows():
        subprocess.run(["shutdown", "/s", "/t", "10"], **_CNW)
    elif is_mac():
        subprocess.run(["osascript", "-e", 'tell app "System Events" to shut down'])
    else:
        subprocess.run(["systemctl", "poweroff"])


def _watch_and_shutdown(steam_path: Path, speak=None,
                        check_interval: int = 30, timeout_hours: int = 12):
    print("[GameUpdater]...")
    deadline = time.time() + timeout_hours * 3600

    for _ in range(24):
        time.sleep(5)
        active = [g for g in _get_steam_games(steam_path) if g["state"] == 1026]
        if active:
            names = ", ".join(g["name"] for g in active)
            if speak:
                speak(f"Download started for {names}. I'll shut down when done.")
            break
    else:
        return  

    while time.time() < deadline:
        time.sleep(check_interval)
        if not any(g["state"] == 1026 for g in _get_steam_games(steam_path)):
            if speak:
                speak("Download complete. Shutting down now.")
            time.sleep(5)
            _system_shutdown()
            return

    if speak:
        speak("Download taking too long. Cancelling auto-shutdown.")


def _find_epic_exe() -> Path | None:
    if is_windows(): return _find_epic_exe_windows()
    if is_mac():     return _find_epic_exe_mac()
    return _find_epic_exe_linux()


def _find_epic_exe_windows() -> Path | None:
    try:
        import winreg
        for hive, key_path in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\EpicGames\EpicGamesLauncher"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\EpicGamesLauncher"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\EpicGames\EpicGamesLauncher"),
        ]:
            try:
                key = winreg.OpenKey(hive, key_path)
                val, _ = winreg.QueryValueEx(key, "AppDataPath")
                winreg.CloseKey(key)
                exe = Path(val) / "Binaries" / "Win64" / "EpicGamesLauncher.exe"
                if exe.exists():
                    return exe
            except Exception:
                continue
    except ImportError:
        pass
    for candidate in [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Epic Games" / "Launcher" / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe",
        Path(os.environ.get("ProgramFiles", ""))       / "Epic Games" / "Launcher" / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))        / "EpicGamesLauncher" / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe",
    ]:
        if candidate.exists():
            return candidate
    return None


def _find_epic_exe_mac() -> Path | None:
    p = Path("/Applications/Epic Games Launcher.app/Contents/MacOS/EpicGamesLauncher")
    return p if p.exists() else None


def _find_epic_exe_linux() -> Path | None:
    for c in [Path.home() / ".local" / "bin" / "heroic", Path("/usr/bin/heroic")]:
        if c.exists():
            return c
    return None


def _epic_manifests_path() -> Path | None:
    if is_windows():
        p = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) \
            / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        return p if p.exists() else None
    if is_mac():
        p = Path.home() / "Library" / "Application Support" \
            / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        return p if p.exists() else None
    return None  


def _get_epic_games() -> list[dict]:
    manifests = _epic_manifests_path()
    if not manifests:
        return []
    games = []
    for item_file in manifests.glob("*.item"):
        try:
            data = json.loads(item_file.read_text(encoding="utf-8"))
            name = data.get("DisplayName") or data.get("AppName", "")
            if name:
                games.append({"id": data.get("AppName", ""), "name": name})
        except Exception:
            continue
    return games


def _is_epic_running() -> bool:
    try:
        if is_windows():
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq EpicGamesLauncher.exe"],
                capture_output=True, text=True, **_CNW
            ).stdout
            return "epicgameslauncher.exe" in out.lower()
        proc = "EpicGamesLauncher" if is_mac() else "heroic"
        return bool(subprocess.run(["pgrep", "-x", proc],
                                   capture_output=True, text=True).stdout.strip())
    except Exception:
        return False


def _update_epic_games(epic_exe: Path, game_name: str = None) -> str:
    games = _get_epic_games()

    if game_name:
        name_lower = game_name.lower()
        matched    = [g for g in games if name_lower in g["name"].lower()]
        if not matched:
            return f"'{game_name}' not found in Epic."
        try:
            url = f"com.epicgames.launcher://apps/{matched[0]['id']}?action=launch&silent=true"
            if is_mac():
                subprocess.Popen(["open", url])
            elif is_linux():
                subprocess.Popen([str(epic_exe), url] if epic_exe else ["xdg-open", url])
            else:
                subprocess.Popen([str(epic_exe), url])
            return f"Opened Epic for '{matched[0]['name']}'."
        except Exception as e:
            return f"Epic update failed: {e}"
    else:
        try:
            if is_mac():
                subprocess.Popen(["open", "-a", "Epic Games Launcher"])
            elif is_linux():
                if epic_exe:
                    subprocess.Popen([str(epic_exe)])
                else:
                    return ("Epic Games is not natively supported on Linux. "
                            "Consider using Heroic Launcher.")
            else:

                if _is_epic_running():
                    for g in games[:10]:
                        subprocess.Popen([str(epic_exe),
                            f"com.epicgames.launcher://apps/{g['id']}?action=launch&silent=true"])
                        time.sleep(0.5)
                    return f"Triggered update check for {len(games)} Epic game(s)."
                else:
                    subprocess.Popen([str(epic_exe)])
            count = len(games)
            return (f"Epic Games Launcher opened. {count} game(s) will be checked."
                    if count else "Epic Games Launcher opened.")
        except Exception as e:
            return f"Epic launch failed: {e}"

def _schedule_daily_update(hour: int = 3, minute: int = 0) -> str:
    if is_windows(): return _schedule_windows(hour, minute)
    if is_mac():     return _schedule_mac(hour, minute)
    return _schedule_linux(hour, minute)


def _schedule_windows(hour: int, minute: int) -> str:
    task_name   = "JARVIS_GameUpdater"
    script_path = Path(__file__).resolve()
    subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, **_CNW)
    for extra in (["/RL", "HIGHEST", "/RU", "SYSTEM"], []):
        cmd    = ["schtasks", "/Create", "/TN", task_name,
                  "/TR", f'"{sys.executable}" "{script_path}" --scheduled',
                  "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}", "/F", *extra]
        result = subprocess.run(cmd, capture_output=True, text=True, **_CNW)
        if result.returncode == 0:
            return f"Daily game update scheduled at {hour:02d}:{minute:02d}."
    return f"Scheduling failed: {result.stderr.strip()}"


def _schedule_mac(hour: int, minute: int) -> str:
    plist_dir   = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path  = plist_dir / "com.jarvis.gameupdater.plist"
    script_path = Path(__file__).resolve()
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.jarvis.gameupdater</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{script_path}</string>
        <string>--scheduled</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{hour}</integer>
        <key>Minute</key><integer>{minute}</integer>
    </dict>
    <key>RunAtLoad</key><false/>
</dict></plist>"""
    try:
        plist_path.write_text(plist_content, encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        result = subprocess.run(["launchctl", "load", str(plist_path)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return f"Daily game update scheduled at {hour:02d}:{minute:02d} via launchd."
        return f"Scheduling failed: {result.stderr.strip()}"
    except Exception as e:
        return f"Scheduling failed: {e}"


def _schedule_linux(hour: int, minute: int) -> str:
    script_path = Path(__file__).resolve()
    marker      = "# JARVIS_GameUpdater"
    cron_entry  = f"{minute} {hour} * * * {sys.executable} {script_path} --scheduled  {marker}"
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines    = [l for l in existing.stdout.splitlines()
                    if marker not in l and str(script_path) not in l]
        lines.append(cron_entry)
        proc = subprocess.run(["crontab", "-"],
                              input="\n".join(lines) + "\n",
                              text=True, capture_output=True)
        if proc.returncode == 0:
            return f"Daily game update scheduled at {hour:02d}:{minute:02d} via cron."
        return f"Scheduling failed: {proc.stderr.strip()}"
    except Exception as e:
        return f"Scheduling failed: {e}"


def _cancel_scheduled_update() -> str:
    if is_windows():
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", "JARVIS_GameUpdater", "/F"],
            capture_output=True, text=True, **_CNW
        )
        return ("Scheduled update cancelled."
                if result.returncode == 0 else "No scheduled update found.")
    if is_mac():
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.jarvis.gameupdater.plist"
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
            plist_path.unlink()
            return "Scheduled update cancelled."
        return "No scheduled update found."

    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines    = [l for l in existing.stdout.splitlines()
                    if "JARVIS_GameUpdater" not in l]
        subprocess.run(["crontab", "-"],
                       input="\n".join(lines) + "\n", text=True)
        return "Scheduled update cancelled."
    except Exception as e:
        return f"Cancel failed: {e}"


def _get_schedule_status() -> str:
    if is_windows():
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "JARVIS_GameUpdater", "/FO", "LIST"],
            capture_output=True, text=True, **_CNW
        )
        if result.returncode != 0:
            return "No scheduled game update found."
        for line in result.stdout.strip().splitlines():
            if any(k in line for k in
                   ("Next Run", "Sonraki", "Prochaine", "Próxima", "Nächste")):
                return f"Game update scheduled. {line.strip()}"
        return "Game update is scheduled."
    if is_mac():
        plist_path = (Path.home() / "Library" / "LaunchAgents"
                      / "com.jarvis.gameupdater.plist")
        return ("Game update is scheduled via launchd."
                if plist_path.exists() else "No scheduled game update found.")

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if "JARVIS_GameUpdater" in result.stdout:
            for line in result.stdout.splitlines():
                if "JARVIS_GameUpdater" in line:
                    return f"Game update is scheduled: {line.split('#')[0].strip()}"
        return "No scheduled game update found."
    except Exception:
        return "No scheduled game update found."


def _game_updater_execute(parameters: dict, player=None, speak=None) -> str:
    p         = parameters or {}
    action    = p.get("action",    "update").lower().strip()
    platform  = p.get("platform",  "both").lower().strip()
    game_name = (p.get("game_name") or "").strip() or None
    app_id    = (p.get("app_id")    or "").strip() or None
    hour      = int(p.get("hour",   3))
    minute    = int(p.get("minute", 0))
    shutdown_value = p.get("shutdown_when_done", False)
    shutdown = shutdown_value is True or str(shutdown_value).lower() in ("true", "1", "yes")
    if platform not in {"steam", "epic", "both"}:
        return outcome.failed("game_updater", "unsupported platform; choose steam, epic, or both")
    if app_id and (not app_id.isdigit() or len(app_id) > 12):
        return outcome.failed("game_updater", "Steam AppID must contain 1 to 12 digits")
    params["action"] = action
    params["platform"] = platform
    params["game_name"] = game_name
    params["app_id"] = app_id
    params["shutdown_when_done"] = shutdown
    if action in {"schedule", "cancel_schedule", "schedule_status"}:
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return outcome.failed("game_updater", "schedule time must be between 00:00 and 23:59")
    if shutdown and action not in {"install", "update"}:
        return outcome.failed("game_updater", "shutdown_when_done is only valid for install/update")

    results = []

    if action == "schedule":        return _schedule_daily_update(hour=hour, minute=minute)
    if action == "cancel_schedule": return _cancel_scheduled_update()
    if action == "schedule_status": return _get_schedule_status()

    if action == "list":
        if platform in ("steam", "both"):
            steam_path = _find_steam_path()
            if steam_path:
                games = _get_steam_games(steam_path)
                if games:
                    names  = ", ".join(g["name"] for g in games[:8])
                    suffix = f" and {len(games) - 8} more" if len(games) > 8 else ""
                    results.append(f"Steam ({len(games)} games): {names}{suffix}.")
                else:
                    results.append("Steam: No games found.")
            else:
                results.append("Steam: Not installed.")
        if platform in ("epic", "both"):
            if is_linux():
                results.append("Epic: Not natively supported on Linux.")
            else:
                games = _get_epic_games()
                if games:
                    names  = ", ".join(g["name"] for g in games[:8])
                    suffix = f" and {len(games) - 8} more" if len(games) > 8 else ""
                    results.append(f"Epic ({len(games)} games): {names}{suffix}.")
                else:
                    results.append("Epic: No games found.")
        return " | ".join(results) or "No platforms found."

    if action == "download_status":
        if platform in ("steam", "both"):
            steam_path = _find_steam_path()
            results.append(
                _get_download_status(steam_path) if steam_path else "Steam: Not installed."
            )
        if platform in ("epic", "both"):
            results.append("Epic download status not available directly.")
        return " ".join(results)

    if action in ("install", "update"):
        if platform in ("steam", "both"):
            steam_path = _find_steam_path()
            if not steam_path:
                results.append("Steam: Not installed.")
            else:
                if game_name:
                    installed  = _get_steam_games(steam_path)
                    name_lower = game_name.lower()
                    is_installed = any(
                        name_lower in g["name"].lower() for g in installed
                    )
                    if not is_installed:
                        msg = _install_steam_game(
                            steam_path, game_name=game_name, app_id=app_id
                        )
                        if shutdown:
                            threading.Thread(
                                target=_watch_and_shutdown,
                                kwargs={"steam_path": steam_path, "speak": speak},
                                daemon=True
                            ).start()
                            msg += " Auto-shutdown enabled."
                        if player: player.write_log(f"[GameUpdater] {msg[:100]}")
                        if speak:  speak(msg)
                        return msg
                    else:
                        results.append(
                            f"Steam: {_update_steam_games(steam_path, game_name=game_name)}"
                        )
                else:
                    if action == "install":
                        results.append("Steam: Please specify a game name to install.")
                    else:
                        results.append(f"Steam: {_update_steam_games(steam_path)}")

                if shutdown:
                    threading.Thread(
                        target=_watch_and_shutdown,
                        kwargs={"steam_path": steam_path, "speak": speak},
                        daemon=True
                    ).start()
                    results.append("Auto-shutdown enabled.")

        if platform in ("epic", "both"):
            if is_linux():
                results.append(
                    "Epic: Not natively supported on Linux. Use Heroic Launcher."
                )
            else:
                epic_exe = _find_epic_exe()
                if epic_exe:
                    results.append(
                        f"Epic: {_update_epic_games(epic_exe, game_name=game_name)}"
                    )
                else:
                    results.append("Epic: Not installed.")

        output = " | ".join(results) or "Nothing to do."
        if player: player.write_log(f"[GameUpdater] {output[:100]}")
        if speak:  speak(output)
        return output

    return f"Unknown action: '{action}'."


def game_updater(parameters: dict, player=None, speak=None) -> str:
    """Route downloads, updates and scheduler mutations through a human approval gate."""
    params = dict(parameters or {})
    action = str(params.get("action", "update")).lower().strip()
    platform = str(params.get("platform", "both")).lower().strip()
    game_name = str(params.get("game_name") or "").strip()
    app_id = str(params.get("app_id") or "").strip()
    shutdown_value = params.get("shutdown_when_done", False)
    shutdown = shutdown_value is True or str(shutdown_value).lower() in ("true", "1", "yes")

    if platform not in {"steam", "epic", "both"}:
        return outcome.failed("game_updater", "unsupported platform; choose steam, epic, or both")
    if app_id and (not app_id.isdigit() or len(app_id) > 12):
        return outcome.failed("game_updater", "Steam AppID must contain 1 to 12 digits")
    if action in {"schedule", "cancel_schedule", "schedule_status"}:
        try:
            hour = int(params.get("hour", 3))
            minute = int(params.get("minute", 0))
        except (TypeError, ValueError):
            return outcome.failed("game_updater", "schedule hour and minute must be integers")
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return outcome.failed("game_updater", "schedule time must be between 00:00 and 23:59")
        params["hour"], params["minute"] = hour, minute

    if action not in {"install", "update", "schedule", "cancel_schedule"}:
        return _game_updater_execute(params, player=player, speak=speak)

    if confirm.pending_title():
        return outcome.ask(
            "game_updater",
            "There is already a confirmation waiting on screen. Answer it before "
            "requesting another game installation, update, or schedule change.",
            action=action,
            platform=platform,
        )

    if action == "install":
        target = game_name or (f"Steam AppID {app_id}" if app_id else "(missing game)")
        title = f"Approve game installation: {target}"
        operation = (
            "Open the installation flow. MINDER will not choose an account, library, "
            "drive, or click Install automatically."
        )
    elif action == "update":
        target = game_name or "all installed games"
        title = f"Approve game update: {target}"
        operation = "Check for and start available game updates/downloads."
    elif action == "schedule":
        target = f"daily updates at {params['hour']:02d}:{params['minute']:02d}"
        title = "Approve daily game-update schedule"
        operation = (
            "Create or replace the daily operating-system task. Future scheduled "
            "runs will start game updates without asking again."
        )
    else:
        target = "the existing daily game-update task"
        title = "Approve cancelling scheduled game updates"
        operation = "Remove the configured daily game-update task."

    detail = (
        f"Operation: {action}\n"
        f"Platform: {platform}\n"
        f"Target: {target}\n"
        f"Details: {operation}"
    )
    if action in {"install", "update"} and app_id:
        detail += f"\nSteam AppID: {app_id}"
    if action in {"install", "update"} and shutdown:
        detail += (
            "\n\nAUTOMATIC SHUTDOWN: After Steam downloads finish, MINDER will "
            "shut down this computer. Save your work before approving."
        )
    else:
        detail += "\n\nMINDER will not shut down the computer."
    if len(detail) > confirm.MAX_DETAIL_CHARS:
        return outcome.failed(
            "game_updater",
            "the full game operation could not be displayed for approval",
            action=action,
        )

    return confirm.request(
        key="game-updater",
        title=title,
        detail=detail,
        run=lambda: _game_updater_execute(params, player=player, speak=speak),
    )


if __name__ == "__main__":
    if "--scheduled" in sys.argv:
        print(f"[GameUpdater] 🕐 Scheduled run at {datetime.now().strftime('%H:%M')}")
        result = _game_updater_execute({"action": "update", "platform": "both"})
        print(f"[GameUpdater] ✅ {result}")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "game_updater",
    "description": "THE ONLY tool for ANY Steam or Epic Games request. Use for: installing, downloading, updating games, listing installed games, checking download status, scheduling updates. ALWAYS call directly for any Steam/Epic/game request. NEVER use browser_control or web_search for Steam/Epic.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"
            },
            "platform": {
                "type": "STRING",
                "description": "steam | epic | both (default: both)"
            },
            "game_name": {
                "type": "STRING",
                "description": "Game name (partial match supported)"
            },
            "app_id": {
                "type": "STRING",
                "description": "Steam AppID for install (optional)"
            },
            "hour": {
                "type": "INTEGER",
                "description": "Hour for scheduled update 0-23 (default: 3)"
            },
            "minute": {
                "type": "INTEGER",
                "description": "Minute for scheduled update 0-59 (default: 0)"
            },
            "shutdown_when_done": {
                "type": "BOOLEAN",
                "description": "Shut down PC when download finishes"
            }
        },
        "required": []
    },
    "handler": game_updater,
}
