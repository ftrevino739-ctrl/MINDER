
from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import os
import platform
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import quote_plus, urljoin, urlsplit

from actions._browser_safety import (
    normalize_browser_url,
    requires_form_submission_approval,
    requires_human_approval,
    requires_screen_click_approval,
)
from core import appidentity, confirm, outcome

# Importing playwright cost 198 ms at every launch, paid by action discovery
# whether or not a browser was ever opened. It is the second-largest import in
# the project after pyautogui, and together the two were 78% of all module
# import time on the boot path.
#
# WHY THIS STILL RAISES WHEN PLAYWRIGHT IS MISSING
#     The eager import had a second job nobody wrote down: when playwright was
#     absent this module failed to load, action_loader dropped it, and the model
#     was never offered a browser tool it could not serve. Deferring the import
#     without keeping that would declare `browser_control` on a machine that
#     cannot run it — the model would call it, get an exception, and describe a
#     page it never opened. Exactly the failure mode this project spent Mark LVI
#     removing from local engines.
#
#     So the check stays at module scope and still stops the module loading; it
#     just asks find_spec instead of executing the package. 1 ms instead of 198,
#     same outcome, and a sentence that says what to install.
if importlib.util.find_spec("playwright") is None:      # pragma: no cover
    raise ImportError(
        "browser_control needs playwright, which is not installed. "
        "Run: pip install playwright  &&  playwright install chromium"
    )

# Annotations only — `from __future__ import annotations` above makes every one
# of them a string, so these names are never looked up at runtime and cost
# nothing outside a type checker.
if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Playwright

# WHY THIS FILE DOES NOT USE core/lazyimport.py's LazyModule LIKE THE OTHERS
#     Every other deferral in this project stands a proxy in for a whole module,
#     because the call sites say `pyautogui.press(...)` and a proxy answers that
#     the same way the module would. What this file needs are two SYMBOLS out of
#     a module: a factory function, and an exception class used in `except`.
#
#     `except` will not take a proxy. It requires a real class inheriting from
#     BaseException, checked by the interpreter at the moment the exception is
#     raised, and a LazyModule there raises TypeError instead of catching
#     anything — the original error then disappears underneath a complaint about
#     the except clause. So these two are bound as real globals instead.
#
# PlaywrightTimeout starts as a private class that is never raised, so an
# `except PlaywrightTimeout:` somehow reached before a session exists catches
# nothing rather than failing that same way.
class _PlaywrightNotLoaded(Exception):
    """Placeholder exception — declared so `except` clauses stay valid, and
    deliberately never raised anywhere."""


async_playwright = None
PlaywrightTimeout: type[BaseException] = _PlaywrightNotLoaded


def _load_playwright() -> None:
    """Import playwright and bind the two runtime symbols. Idempotent."""
    global async_playwright, PlaywrightTimeout
    if async_playwright is not None:
        return
    from playwright.async_api import (          # noqa: PLC0415 — deliberate
        async_playwright as _ap,
        TimeoutError as _timeout,
    )
    async_playwright = _ap
    PlaywrightTimeout = _timeout


_OS = platform.system()   # "Windows" | "Darwin" | "Linux"

def _normalize_url(url: str) -> str:
    """Normalize a browser address and reject non-web URI schemes."""
    return normalize_browser_url(url)


def _user_agent() -> str:
    if _OS == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    if _OS == "Darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _real_profile_dir(browser: str) -> str:
    home  = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")

    candidates: list[Path] = []

    if _OS == "Windows":
        m = {
            "chrome":   [Path(local) / "Google"          / "Chrome"          / "User Data"],
            "edge":     [Path(local) / "Microsoft"        / "Edge"            / "User Data"],
            "brave":    [Path(local) / "BraveSoftware"    / "Brave-Browser"   / "User Data"],
            "vivaldi":  [Path(local) / "Vivaldi"          / "User Data"],
            "opera":    [Path(roam)  / "Opera Software"   / "Opera Stable",
                         Path(local) / "Opera Software"   / "Opera Stable"],
            "operagx":  [Path(roam)  / "Opera Software"   / "Opera GX Stable",
                         Path(local) / "Opera Software"   / "Opera GX Stable"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Darwin":
        lib = home / "Library" / "Application Support"
        m = {
            "chrome":   [lib / "Google"             / "Chrome"],
            "edge":     [lib / "Microsoft Edge"],
            "brave":    [lib / "BraveSoftware"       / "Brave-Browser"],
            "vivaldi":  [lib / "Vivaldi"],
            "opera":    [lib / "com.operasoftware.Opera"],
            "operagx":  [lib / "com.operasoftware.OperaGX"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Linux":
        cfg = home / ".config"
        m = {
            "chrome":   [cfg / "google-chrome", cfg / "chromium"],
            "edge":     [cfg / "microsoft-edge"],
            "brave":    [cfg / "BraveSoftware" / "Brave-Browser"],
            "vivaldi":  [cfg / "vivaldi"],
            "opera":    [cfg / "opera"],
            "operagx":  [cfg / "opera-gx"],
        }
        candidates = m.get(browser, [])

    for p in candidates:
        if p.exists():
            print(f"[Browser] ✅ Real profile found for {browser}: {p}")
            return str(p)

    fallback = home / ".jarvis_profiles" / browser
    fallback.mkdir(parents=True, exist_ok=True)
    print(f"[Browser] ⚠️  Real profile not found for {browser}, using: {fallback}")
    return str(fallback)

def _firefox_profile_dir() -> Optional[str]:
    home = Path.home()

    if _OS == "Windows":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    elif _OS == "Darwin":
        base = home / "Library" / "Application Support" / "Firefox"
    else:
        base = home / ".mozilla" / "firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None

    current: dict[str, str] = {}
    default_path: Optional[str] = None

    for line in ini.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("["):
            p = current.get("Path", "")
            if p and current.get("Default") == "1":
                is_rel = current.get("IsRelative", "1") == "1"
                default_path = str(base / p) if is_rel else p
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    p = current.get("Path", "")
    if p and current.get("Default") == "1":
        is_rel = current.get("IsRelative", "1") == "1"
        default_path = str(base / p) if is_rel else p

    if default_path and Path(default_path).exists():
        print(f"[Browser] Firefox real profile: {default_path}")
        return default_path
    return None

def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")

    candidates = [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]
    for p in candidates:
        if p.exists():
            print(f"[Browser] Opera found at: {p}")
            return str(p)

    try:
        import winreg
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\opera\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass

    return shutil.which("opera") or None

def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        paths_to_try = [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]
        for key_path in paths_to_try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None

_BROWSER_SPECS: dict[str, dict] = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
        "safari":   None,
    },
    "Darwin": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": ["microsoft-edge"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi"]},
        "safari":   {"engine": "webkit",   "channel": None,      "bins": []},
    },
    "Linux": {
        "chrome":   {"engine": "chromium", "channel": None,
                     "bins": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]},
        "edge":     {"engine": "chromium", "channel": None,
                     "bins": ["microsoft-edge", "microsoft-edge-stable"]},
        "firefox":  {"engine": "firefox",  "channel": None, "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "operagx":  {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "brave":    {"engine": "chromium", "channel": None, "bins": ["brave-browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None, "bins": ["vivaldi-stable", "vivaldi"]},
        "safari":   None,
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}


def _resolve_browser(name: str) -> dict | None:
    name   = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map = _BROWSER_SPECS.get(_OS, {})
    spec   = os_map.get(name)
    if spec is None:
        return None

    engine  = spec["engine"]
    channel = spec.get("channel")
    bins    = spec.get("bins", [])
    exe     = None

    if spec.get("special") == "opera_windows":
        exe = _find_opera_windows()
        if not exe:
            print(f"[Browser] ⚠️  Opera executable not found on Windows.")
        return {"engine": engine, "exe": exe, "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and _OS == "Darwin":
        app_names = {
            "chrome":  ["Google Chrome.app"],
            "edge":    ["Microsoft Edge.app"],
            "firefox": ["Firefox.app"],
            "opera":   ["Opera.app", "Opera GX.app"],
            "brave":   ["Brave Browser.app"],
            "vivaldi": ["Vivaldi.app"],
        }
        for app in app_names.get(name, []):
            app_dir = Path("/Applications") / app / "Contents" / "MacOS"
            if app_dir.exists():
                found_bins = list(app_dir.iterdir())
                if found_bins:
                    exe = str(found_bins[0])
                    break

    if not exe and _OS == "Windows" and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        if _OS == "Windows":
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\http\UserChoice",
            )
            prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
            winreg.CloseKey(k)
            for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
                if kw in prog_id:
                    return kw
        elif _OS == "Darwin":
            out = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "safari", "chrome", "edge"):
                if kw in out:
                    return kw
        elif _OS == "Linux":
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "chrome", "edge"):
                if kw in out:
                    return kw
    except Exception:
        pass
    return "chrome"


_SEARCH_ENGINES: dict[str, str] = {
    "google":     "https://www.google.com/search?q=",
    "bing":       "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "yandex":     "https://yandex.com/search/?text=",
}

_MAC_APP_NAMES: dict[str, str] = {
    "chrome":  "Google Chrome",
    "edge":    "Microsoft Edge",
    "firefox": "Firefox",
    "opera":   "Opera",
    "operagx": "Opera GX",
    "brave":   "Brave Browser",
    "vivaldi": "Vivaldi",
    "safari":  "Safari",
}

# Windows registry lookup names for browsers whose spec has no explicit binary
_WIN_EXE_HINTS: dict[str, str] = {"chrome": "chrome", "edge": "msedge"}


def _open_native(url: str, browser_name: Optional[str]) -> str:
    """
    Opens the user's REAL browser normally — with their own profile,
    logged-in accounts and extensions. No automation attaches, so an
    about:blank tab or a blank profile NEVER shows up.
    If url is empty the browser starts with no URL (its own start page /
    session restore) — exactly as if the user had opened it themselves.
    Works on all three of Windows / macOS / Linux.
    """
    url = _normalize_url(url) if url and url.strip() else ""
    if url == "about:blank":
        url = ""

    name = None
    if browser_name:
        name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
    elif not url:
        # No URL → only a window will open; needs the default browser's exe
        name = _detect_default_browser()

    # Specific browser → launch its own executable, exactly like the user would.
    if name:
        if _OS == "Darwin":
            app = _MAC_APP_NAMES.get(name)
            if app:
                cmd = ["open", "-a", app] + ([url] if url else [])
                try:
                    subprocess.run(cmd, check=True, timeout=10)
                    return f"Opened in {name}: {url}" if url else f"Opened {name}."
                except Exception as e:
                    print(f"[Browser] 'open -a {app}' failed ({e}), trying binary…")

        spec = _resolve_browser(name)
        exe  = spec.get("exe") if spec else None
        if not exe and _OS == "Windows":
            if name in ("opera", "operagx"):
                exe = _find_opera_windows()
            else:
                exe = _find_exe_windows(_WIN_EXE_HINTS.get(name, name))
        if exe:
            try:
                subprocess.Popen(
                    [exe, url] if url else [exe],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return f"Opened in {name}: {url}" if url else f"Opened {name}."
            except Exception as e:
                print(f"[Browser] Native launch failed for {name}: {e}")
        print(f"[Browser] '{name}' not found — falling back to default browser.")

    if not url:
        return outcome.failed(
            "browser_control",
            "no browser was found and no address was given to open")

    # Default browser via the OS — exactly like the user clicking a link.
    try:
        if _OS == "Windows":
            os.startfile(url)                       # ShellExecute → default browser
        elif _OS == "Darwin":
            subprocess.run(["open", url], check=True, timeout=10)
        else:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        # HANDED TO THE OPERATING SYSTEM, NOT CONFIRMED.
        #
        # os.startfile / open / xdg-open all return as soon as the request is
        # accepted. None of them waits for a browser, and none reports whether
        # the page loaded — the same gap that made open_app say "Opened
        # Spotify." off the back of a shell starting. Unlike the playwright
        # path above there is no page object here to read a url from, so this
        # says what it actually knows.
        return outcome.unverified(
            "browser_control",
            "the address was handed to the system's default browser, which "
            "does not report back - the page is not confirmed open",
            url=url)
    except Exception:
        try:
            if webbrowser.open(url):
                return outcome.unverified(
                    "browser_control",
                    "the address was handed to the default browser, which does "
                    "not report back - the page is not confirmed open",
                    url=url)
        except Exception:
            pass
        return outcome.failed(
            "browser_control",
            "no browser on this system could be opened", url=url)


class _BrowserSession:
    """
    A full session for one browser instance.
    All browsers open on the real profile via launch_persistent_context.
    """

    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread | None          = None
        self._ready    = threading.Event()

        self._pw:      Playwright     | None = None
        self._context: BrowserContext | None = None
        self._page:    Page           | None = None
        # "real" when the user's own browser profile was opened, "separate"
        # when their browser was already running and a private automation
        # profile had to be used instead. The difference is every sign-in they
        # have, so it is reported rather than only printed.
        self.profile_kind: str = ""

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        # The one door into a live session: nothing in this class reaches
        # async_playwright or PlaywrightTimeout without coming through here
        # first, so binding them at this point is enough for the whole file.
        _load_playwright()
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        if not self._loop:
            raise RuntimeError(f"Session for '{self.browser_name}' not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = self._page = None

    async def _adopt_page(self) -> Page:
        """
        launch_persistent_context already opens a starting tab.
        Instead of opening a new blank tab (about:blank), it adopts that tab —
        so the user never sees an extra blank tab.
        """
        await asyncio.sleep(0.3)
        pages = self._context.pages
        return pages[0] if pages else await self._context.new_page()

    async def _launch(self):
        """
        Launches the browser with the real user profile.
        Does nothing if the context is already open.
        """
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(
                f"'{self.browser_name}' bu platformda ({_OS}) desteklenmiyor."
            )

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        channel     = self._spec["channel"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "firefox":
            profile = _firefox_profile_dir() or str(
                Path.home() / ".jarvis_profiles" / "firefox"
            )
            kwargs: dict = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            if exe:
                kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            except Exception as e:
                print(f"[Browser] Firefox real profile failed ({e}), using JARVIS profile")
                jarvis = str(Path.home() / ".jarvis_profiles" / "firefox_jarvis")
                Path(jarvis).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(jarvis, **kwargs)

            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Firefox launched")
            return

        if engine_name == "webkit":
            safari_profile = str(Path.home() / ".jarvis_profiles" / "safari")
            Path(safari_profile).mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            self._context = await engine_obj.launch_persistent_context(safari_profile, **kwargs)
            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Safari launched")
            return

        profile = _real_profile_dir(self.browser_name)

        kwargs = {
            "headless":    False,
            "slow_mo":     0,
            "viewport":    None,
            "no_viewport": True,
            "timeout":     25_000,
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
                "--no-default-browser-check",
            ],
        }

        if exe:
            kwargs["executable_path"] = exe
        elif channel:
            kwargs["channel"] = channel

        label = (
            f"{self.browser_name}"
            + (f"/{channel}" if channel else "")
            + (f" @ {exe}" if exe else "")
        )

        # ASK FIRST, INSTEAD OF WAITING 25 SECONDS TO BE TOLD.
        #
        # A Chromium profile can only be open once. If the user already has the
        # browser running — which is the normal state of a browser — then
        # launch_persistent_context on their real profile cannot succeed, and
        # the way it fails is by sitting on the timeout above before raising.
        # Every "open this page" then cost twenty-five seconds of nothing
        # before the fallback even started, and the fallback is a profile with
        # none of their sign-ins in it.
        #
        # Whether the browser is running is a question the process table
        # answers in milliseconds, and core/appidentity was already built to
        # ask it. Knowing up front turns a long silence into an immediate,
        # explainable outcome.
        already = appidentity.running(self.browser_name)
        if already:
            print(f"[Browser] {self.browser_name} is already running "
                  f"({len(already)} processes) — its profile is locked, so the "
                  f"separate automation profile will be used.")
        else:
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
                self._page = await self._adopt_page()
                self.profile_kind = "real"
                print(f"[Browser] ✅ Launched [{label}] profile={profile}")
                return
            except Exception as e:
                print(f"[Browser] ⚠️  Real profile failed for {label}: {e}")

        # The real profile could not be opened (browser already open / locked
        # profile / newer Chrome versions block the real profile under
        # automation). Fall back to a persistent JARVIS automation profile —
        # accounts logged in here once stay logged in on later sessions too.
        jarvis_profile = str(Path.home() / ".jarvis_profiles" / self.browser_name)
        Path(jarvis_profile).mkdir(parents=True, exist_ok=True)
        print(f"[Browser] Retrying with JARVIS profile: {jarvis_profile}")

        try:
            self._context = await engine_obj.launch_persistent_context(jarvis_profile, **kwargs)
            self._page = await self._adopt_page()
            self.profile_kind = "separate"
            print(f"[Browser] ✅ Launched [{label}] with JARVIS profile "
                  f"(sign-ins persist across sessions)")
        except Exception as e2:
            raise RuntimeError(f"Could not launch {self.browser_name}: {e2}") from e2


    async def _get_page(self) -> Page:
        await self._launch()
        # If somehow page got closed, open a fresh one
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            await asyncio.sleep(0.2)
        return self._page

    async def _page_dirty_snapshot(self, page) -> list[dict]:
        """Return user-edited form fields, redacting password values."""
        return await page.evaluate("""() => {
            const nodes = [...document.querySelectorAll(
                'input:not([type="hidden"]), textarea, select, '
                + '[contenteditable="true"], [role="textbox"]'
            )];
            const dirty = [];
            for (const el of nodes) {
                const type = (el.getAttribute('type') || '').toLowerCase();
                if (['hidden', 'button', 'submit', 'reset'].includes(type)) continue;
                const label = el.getAttribute('aria-label')
                    || el.getAttribute('placeholder')
                    || el.labels?.[0]?.innerText
                    || el.getAttribute('name')
                    || el.id
                    || el.tagName.toLowerCase();
                if (type === 'file') {
                    const names = [...(el.files || [])].map(file => file.name);
                    if (names.length) {
                        dirty.push({
                            label: String(label).trim(),
                            type: 'file',
                            password: false,
                            length: names.length,
                            value: 'Selected files: ' + names.join(', '),
                            id: el.id || '',
                            name: el.getAttribute('name') || ''
                        });
                    }
                    continue;
                }
                const password = type === 'password';
                const tag = el.tagName.toLowerCase();
                const value = el.isContentEditable
                    ? String(el.innerText || '')
                    : (('value' in el) ? String(el.value || '') : String(el.textContent || ''));
                let changed = false;
                if (el.isContentEditable || el.getAttribute('role') === 'textbox') {
                    changed = value.length > 0;
                } else if (tag === 'select') {
                    changed = [...el.options].some(option =>
                        Boolean(option.selected) !== Boolean(option.defaultSelected)
                    );
                } else if (type === 'checkbox' || type === 'radio') {
                    changed = Boolean(el.checked) !== Boolean(el.defaultChecked);
                } else {
                    changed = value !== String(el.defaultValue || '');
                }
                if (!changed) continue;
                const fieldLabel = label || tag;
                dirty.push({
                    label: String(fieldLabel).trim(),
                    type: type || tag,
                    password,
                    length: value.length,
                    value: password ? null : value,
                    id: el.id || '',
                    name: el.getAttribute('name') || ''
                });
            }
            return dirty;
        }""")

    async def _navigate_page(self, page, url: str, previous_url: str) -> str:
        async def _do_goto(p: Page) -> str:
            """Attempt navigation and return the resulting URL (may still be blank)."""
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(0.3)
            except PlaywrightTimeout:
                pass
            except Exception as exc:
                print(f"[Browser] goto exception (non-fatal): {exc}")
            return p.url

        result_url = await _do_goto(page)
        if result_url in ("about:blank", "", None, previous_url) and previous_url in ("about:blank", "", None):
            print(f"[Browser] Still blank after goto — retrying on new tab: {url}")
            try:
                new_page = await self._context.new_page()
                self._page = new_page
                result_url = await _do_goto(new_page)
            except Exception as exc:
                print(f"[Browser] New-tab retry failed: {exc}")

        if result_url and result_url not in ("about:blank", "", None):
            if self.profile_kind == "separate":
                return outcome.unverified(
                    "browser_control",
                    f"the page opened at {result_url}, but "
                    f"{self.browser_name} was already running so its profile "
                    f"was locked - this window uses a separate profile where "
                    f"the user is NOT signed in to anything. Closing "
                    f"{self.browser_name} first would let their own profile be "
                    f"used",
                    opened=result_url, profile="separate")
            return outcome.done(
                "browser_control", opened=result_url,
                profile=self.profile_kind or "real", verified=True,
            )
        return outcome.failed(
            "browser_control",
            "the page stayed blank - the address may be wrong, or the site may "
            "not have answered", url=url)

    async def _navigate_after_approval(self, page, expected_url: str,
                                       expected_dirty: list[dict],
                                       target_url: str) -> str:
        try:
            if page.is_closed() or page.url != expected_url:
                return outcome.failed(
                    "browser_control",
                    "the current page changed while approval was pending; navigation was not started",
                )
            current_dirty = await self._page_dirty_snapshot(page)
            if current_dirty != expected_dirty:
                return outcome.failed(
                    "browser_control",
                    "the form or draft changed while approval was pending; navigation was not started",
                )
            return await self._navigate_page(page, target_url, expected_url)
        except Exception as exc:
            return outcome.failed(
                "browser_control", f"approved browser navigation failed: {exc}",
            )

    async def go_to(self, url: str) -> str:
        url = _normalize_url(url)
        page = await self._get_page()
        previous_url = page.url

        if previous_url not in ("about:blank", "", None) and previous_url != url:
            dirty = await self._page_dirty_snapshot(page)
            if dirty:
                lines = []
                for field in dirty:
                    value = (
                        f"[password value hidden; {field['length']} characters]"
                        if field.get("password")
                        else (field.get("value") or "[empty]")
                    )
                    lines.append(
                        f"Field: {field.get('label') or field.get('name') or 'unnamed'} "
                        f"({field.get('type')})\nCurrent unsaved value:\n{value}"
                    )
                detail = (
                    f"Current page: {await page.title()}\n"
                    f"Address: {previous_url}\n"
                    f"Destination: {url}\n\n"
                    "Unsaved or edited fields detected:\n"
                    + "\n\n".join(lines)
                    + "\n\nNavigating away may discard this content. The page and "
                    "field values will be checked again before navigation."
                )
                return self._request_browser_approval(
                    f"Approve leaving page with unsaved content",
                    detail,
                    run=lambda: self.run(
                        self._navigate_after_approval(
                            page, previous_url, dirty, url,
                        )
                    ),
                )

        return await self._navigate_page(page, url, previous_url)

    async def search(self, query: str, engine: str = "google") -> str:
        base = _SEARCH_ENGINES.get(engine.lower(), _SEARCH_ENGINES["google"])
        return await self.go_to(base + quote_plus(query))

    async def _control_details(self, locator) -> dict:
        return await locator.evaluate("""el => {
            const control = el.closest(
                'button, input[type="button"], input[type="submit"], '
                + 'input[type="image"], [role="button"], a[href]'
            ) || el;
            const form = control.form || control.closest('form');
            const fields = form ? [...form.querySelectorAll(
                'input, textarea, select, [contenteditable="true"]'
            )].map(field => {
                const type = (field.getAttribute('type') || '').toLowerCase();
                if (['password', 'hidden', 'file'].includes(type)) return null;
                const label = field.getAttribute('aria-label')
                    || field.getAttribute('placeholder')
                    || field.labels?.[0]?.innerText
                    || field.getAttribute('name')
                    || field.tagName.toLowerCase();
                const value = field.isContentEditable
                    ? field.innerText : (field.value || '');
                return value ? label + ': ' + String(value).slice(0, 2000) : null;
            }).filter(Boolean) : [];
            const isSearchForm = Boolean(form && (
                form.matches('[role="search"]')
                || form.querySelector(
                    'input[type="search"], input[name="q"], input[name="query"], '
                    + 'input[name*="search" i]'
                )
                || /search|buscar/i.test(
                    (form.id || '') + ' ' + (form.getAttribute('name') || '')
                    + ' ' + (form.getAttribute('aria-label') || '')
                )
            ));
            const isLink = control.matches('a[href]');
            return {
                label: (control.getAttribute('aria-label') || control.innerText
                    || control.value || control.getAttribute('title') || '').trim(),
                formLabel: form ? (form.getAttribute('aria-label')
                    || form.getAttribute('name') || form.id || '') : '',
                identifiers: [
                    control.id,
                    control.getAttribute('name'),
                    control.getAttribute('data-action'),
                    control.getAttribute('data-testid'),
                    control.getAttribute('title')
                ].filter(Boolean).join(' '),
                isActionControl: control.matches(
                    'button, input[type="button"], input[type="submit"], '
                    + 'input[type="image"], [role="button"]'
                ),
                isSubmitControl: Boolean(form && control.matches(
                    'button:not([type]), button[type="submit"], '
                    + 'input[type="submit"], input[type="image"]'
                )),
                isNavigationLink: isLink,
                href: isLink ? control.href : '',
                isSearchForm,
                fields
            };
        }""")

    def _request_browser_approval(self, title: str, detail: str, run) -> str:
        pending = confirm.pending_title()
        if pending:
            return outcome.ask(
                "browser_control",
                "There is already a confirmation waiting on screen. "
                "The user must answer it before another browser action can be approved.",
                pending=pending,
            )
        if len(detail) > confirm.MAX_DETAIL_CHARS:
            return outcome.failed(
                "browser_control",
                "the browser action is too long to display in full for safe approval",
            )
        return confirm.request(
            key="browser-action",
            title=title,
            detail=detail,
            run=run,
        )

    async def _confirmed_click(
        self, page, locator, expected_url: str, expected_details: dict,
        expected_dirty: list[dict] | None = None,
    ) -> str:
        try:
            if page.is_closed() or page.url != expected_url:
                return outcome.failed(
                    "browser_control",
                    "the page changed while approval was pending; nothing was clicked",
                )
            if expected_dirty is not None:
                current_dirty = await self._page_dirty_snapshot(page)
                if current_dirty != expected_dirty:
                    return outcome.failed(
                        "browser_control",
                        "unsaved form data changed while approval was pending; "
                        "nothing was clicked",
                    )
            if await locator.count() == 0:
                return outcome.failed(
                    "browser_control",
                    "the approved control is no longer available; nothing was clicked",
                )
            current = await self._control_details(locator)
            if current != expected_details:
                return outcome.failed(
                    "browser_control",
                    "the control or its form contents changed while approval was pending; "
                    "nothing was clicked",
                )
            await locator.click(timeout=8_000)
            return outcome.unverified(
                "browser_control",
                "the approved browser control was clicked, but the website's final "
                "result was not independently verified",
                url=expected_url,
                control=expected_details.get("label", ""),
            )
        except Exception as exc:
            return outcome.failed("browser_control",
                                  f"approved browser click failed: {exc}")

    async def _click_locator(self, page, locator, description: str) -> str:
        details = await self._control_details(locator)
        label = (details.get("label") or "").strip()
        form_label = (details.get("formLabel") or "").strip()
        identifiers = (details.get("identifiers") or "").strip()
        href = (details.get("href") or "").strip()
        is_navigation_link = bool(details.get("isNavigationLink"))

        # An anchor is navigation, not a neutral text click. Refuse active or
        # local URI schemes before Playwright has a chance to hand them to OS apps.
        if is_navigation_link:
            try:
                destination = urljoin(page.url, href)
                parsed_destination = urlsplit(destination)
                if parsed_destination.scheme.lower() not in {"http", "https"}:
                    raise ValueError("Only HTTP and HTTPS links are allowed.")
            except Exception as exc:
                return outcome.failed(
                    "browser_control",
                    f"blocked unsafe browser link: {exc}",
                    href=href,
                )

            current = urlsplit(page.url)
            same_document = (
                parsed_destination.scheme.lower() == current.scheme.lower()
                and parsed_destination.netloc.lower() == current.netloc.lower()
                and parsed_destination.path == current.path
                and parsed_destination.query == current.query
            )
            # Hash-only jumps do not discard the current document or its draft.
            if not same_document:
                dirty = await self._page_dirty_snapshot(page)
                if dirty:
                    page_url = page.url
                    page_title = await page.title()
                    visible = []
                    for field in dirty:
                        value = (
                            f"[password value hidden; {field.get('length', 0)} characters]"
                            if field.get("password")
                            else (field.get("value") or "[empty]")
                        )
                        visible.append(
                            f"Field: {field.get('label') or field.get('name') or 'unnamed'} "
                            f"({field.get('type', 'field')})\nUnsaved value:\n{value}"
                        )
                    detail = (
                        f"Website: {page_title}\nAddress: {page_url}\n"
                        f"Destination: {destination}\n"
                        f"Link: {label or description}\n"
                        f"Identifiers: {identifiers or '(none)'}\n\n"
                        "Unsaved or edited fields detected:\n"
                        + "\n\n".join(visible)
                        + "\n\nNavigation may discard this content. The page, link "
                        "and draft will be checked again before clicking."
                    )
                    return self._request_browser_approval(
                        f"Approve leaving page with unsaved content",
                        detail,
                        run=lambda: self.run(
                            self._confirmed_click(
                                page, locator, page_url, details, expected_dirty=dirty,
                            )
                        ),
                    )

        is_search_submit = bool(
            details.get("isSubmitControl")
            and (
                details.get("isSearchForm")
                or "search" in form_label.casefold()
                or "buscar" in form_label.casefold()
                or label.casefold() in {"search", "buscar"}
            )
        )
        sensitive = requires_human_approval(
            label, form_label, identifiers, description,
        )
        requires_approval = (
            (details.get("isSubmitControl") and not is_search_submit)
            or sensitive
            or (
                details.get("isActionControl")
                and requires_screen_click_approval(label or description)
            )
        )

        # Even a normally safe navigation/menu button must protect edits on the
        # current page. Search submitters are intentionally exempt.
        dirty = None
        if (
            not is_search_submit
            and details.get("isActionControl")
            and not details.get("isSubmitControl")
        ):
            dirty = await self._page_dirty_snapshot(page)
            if dirty:
                requires_approval = True

        if not requires_approval:
            await locator.click(timeout=8_000)
            return f"Clicked: {label or description or 'element'}"

        page_url = page.url
        page_title = await page.title()
        title = (
            "Approve leaving page with unsaved content"
            if dirty or (is_navigation_link and dirty is not None)
            else f"Approve browser action: {label or description}"
        )
        fields = details.get("fields") or []
        detail = (
            f"Website: {page_title}\n"
            f"Address: {page_url}\n"
            + (f"Destination: {href}\n" if is_navigation_link and href else "")
            + f"Control: {label or description}\n"
            + f"Form: {form_label or '(not labelled)'}\n\n"
            "Visible form values for review:\n"
            + ("\n".join(fields) if fields else "(no visible values)")
        )
        if dirty:
            dirty_lines = []
            for field in dirty:
                value = (
                    f"[password value hidden; {field.get('length', 0)} characters]"
                    if field.get("password")
                    else (field.get("value") or "[empty]")
                )
                dirty_lines.append(
                    f"Field: {field.get('label') or field.get('name') or 'unnamed'}: {value}"
                )
            detail += "\n\nUnsaved page data:\n" + "\n".join(dirty_lines)
        detail += "\n\nThe page, control and form values will be checked again before the click."

        return self._request_browser_approval(
            title, detail,
            run=lambda: self.run(
                self._confirmed_click(
                    page, locator, page_url, details, expected_dirty=dirty,
                )
            ),
        )

    async def click(self, selector: str = None, text: str = None) -> str:
        page = await self._get_page()
        try:
            if text:
                locator = page.get_by_text(text, exact=False).first
                return await self._click_locator(page, locator, text)
            if selector:
                locator = page.locator(selector).first
                return await self._click_locator(page, locator, selector)
            return outcome.failed("browser_control",
                                  "no element was named to click")
        except PlaywrightTimeout:
            return outcome.failed(
                "browser_control",
                "no element matching that appeared on the page in time",
                selector=selector or text)
        except Exception as e:
            return f"Click error: {e}"

    async def _field_snapshot(self, locator) -> dict:
        """Describe a writable field without revealing password contents."""
        return await locator.evaluate("""el => {
            const tag = el.tagName.toLowerCase();
            const type = (el.getAttribute('type') || '').toLowerCase();
            const password = type === 'password';
            const editable = tag === 'textarea'
                || el.isContentEditable
                || el.getAttribute('role') === 'textbox'
                || (tag === 'input' && ![
                    'button', 'submit', 'reset', 'checkbox', 'radio',
                    'file', 'hidden', 'image'
                ].includes(type));
            const raw = el.isContentEditable
                ? el.innerText
                : (('value' in el) ? el.value : el.textContent || '');
            const value = String(raw || '');
            let hash = 2166136261;
            for (let i = 0; i < value.length; i++) {
                hash ^= value.charCodeAt(i);
                hash = Math.imul(hash, 16777619);
            }
            const label = el.getAttribute('aria-label')
                || el.getAttribute('placeholder')
                || el.labels?.[0]?.innerText
                || el.getAttribute('name')
                || el.id
                || tag;
            return {
                editable,
                tag,
                type,
                password,
                hasValue: value.length > 0,
                valueLength: value.length,
                fingerprint: (hash >>> 0).toString(16),
                value: password ? null : value,
                label: String(label).trim(),
                id: el.id || '',
                name: el.getAttribute('name') || '',
                placeholder: el.getAttribute('placeholder') || ''
            };
        }""")

    async def _replace_field_after_approval(self, page, locator, expected_url: str,
                                            expected_snapshot: dict, text: str,
                                            target: str, clear_first: bool = True) -> str:
        try:
            if page.is_closed() or page.url != expected_url:
                return outcome.failed(
                    "browser_control",
                    "the page changed while approval was pending; the field was not changed",
                )
            if await locator.count() == 0:
                return outcome.failed(
                    "browser_control",
                    "the approved field is no longer available; nothing was typed",
                )
            current = await self._field_snapshot(locator)
            if current != expected_snapshot:
                return outcome.failed(
                    "browser_control",
                    "the field changed while approval was pending; nothing was typed",
                    target=target,
                )
            if clear_first and expected_snapshot.get("hasValue"):
                await locator.clear()
            await locator.type(text, delay=50)
            if not clear_first and expected_snapshot.get("hasValue"):
                return outcome.unverified(
                    "browser_control",
                    "text was inserted without clearing the existing field contents; "
                    "the exact final caret position was not independently verified",
                    target=target,
                )
            matches = await locator.evaluate("""(el, expected) => {
                const value = el.isContentEditable
                    ? el.innerText
                    : (('value' in el) ? el.value : el.textContent || '');
                return String(value || '') === expected;
            }""", text)
            if not matches:
                return outcome.unverified(
                    "browser_control",
                    "text was entered, but the field did not match the requested value",
                    target=target,
                )
            return outcome.done(
                "browser_control", typed=True, verified=True, target=target,
            )
        except Exception as exc:
            return outcome.failed(
                "browser_control", f"field input failed: {exc}", target=target,
            )

    async def _guarded_type(self, page, locator, text: str,
                            clear_first: bool, target: str) -> str:
        snapshot = await self._field_snapshot(locator)
        if not snapshot.get("editable"):
            return outcome.failed(
                "browser_control",
                "the selected element is not an editable text field",
                target=target,
            )
        page_url = page.url
        needs_review = bool(clear_first and snapshot.get("hasValue"))
        if needs_review:
            old_value = (
                "[password value hidden]"
                if snapshot.get("password")
                else (snapshot.get("value") or "")
            )
            new_value = (
                f"[password value hidden; {len(text)} characters]"
                if snapshot.get("password")
                else text
            )
            detail = (
                f"Website: {await page.title()}\n"
                f"Address: {page_url}\n"
                f"Field: {snapshot.get('label') or target}\n"
                f"Existing value ({snapshot.get('valueLength', 0)} characters):\n"
                f"{old_value}\n\n"
                f"Replacement value ({len(text)} characters):\n"
                f"{new_value}\n\n"
                "MINDER will replace the field only if the page and existing "
                "contents are unchanged when you approve."
            )
            return self._request_browser_approval(
                f"Approve replacing browser field: {snapshot.get('label') or target}",
                detail,
                run=lambda: self.run(
                    self._replace_field_after_approval(
                        page, locator, page_url, snapshot, text, target,
                        clear_first=clear_first,
                    )
                ),
            )

        # Do not clear a field that was empty when inspected. Recheck it just
        # before typing so a value entered in the meantime is never erased.
        return await self._replace_field_after_approval(
            page, locator, page_url, snapshot, text, target,
            clear_first=clear_first,
        )

    async def type_text(self, selector: str = None, text: str = "",
                        clear_first: bool = True) -> str:
        page = await self._get_page()
        try:
            locator = page.locator(selector).first if selector else page.locator(":focus")
            target = selector or "currently focused field"
            return await self._guarded_type(
                page, locator, str(text), bool(clear_first), target,
            )
        except Exception as exc:
            return f"Type error: {exc}"

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._get_page()
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def _form_submission_context(self, page) -> dict:
        return await page.evaluate("""() => {
            const active = document.activeElement;
            const form = active && (active.form || active.closest('form'));
            const activeType = (active?.getAttribute('type') || '').toLowerCase();
            const activeRole = (active?.getAttribute('role') || '').toLowerCase();
            const activeLabel = (
                active?.getAttribute('aria-label')
                || active?.getAttribute('placeholder')
                || active?.getAttribute('name')
                || ''
            ).trim();
            const activeControl = (
                active?.getAttribute('aria-label')
                || active?.innerText
                || active?.value
                || active?.getAttribute('title')
                || ''
            ).trim();
            const isEditable = Boolean(active && (
                active.matches('textarea, [contenteditable="true"], [role="textbox"]')
                || (active.matches('input') && ![
                    'button', 'submit', 'reset', 'checkbox', 'radio', 'file',
                    'hidden', 'image'
                ].includes(activeType))
            ));
            const isSearch = activeType === 'search'
                || activeRole === 'searchbox'
                || /search|buscar/i.test(activeLabel);
            const activeValue = isEditable && activeType !== 'password'
                ? (active.isContentEditable ? active.innerText : (active.value || ''))
                : '';
            const activeField = activeValue
                ? (activeLabel || 'Focused field') + ': ' + String(activeValue).slice(0, 2000)
                : null;
            if (!form) return {
                hasForm: false,
                isEditable: isEditable && !isSearch,
                isSearch,
                formLabel: activeLabel,
                activeControl,
                submitters: [],
                fields: activeField ? [activeField] : []
            };
            const submitters = [...form.querySelectorAll(
                'button, input[type="submit"], input[type="image"], [role="button"]'
            )].map(el => (el.getAttribute('aria-label') || el.innerText
                || el.value || el.getAttribute('title') || '').trim()).filter(Boolean);
            const fields = [...form.querySelectorAll(
                'input, textarea, select, [contenteditable="true"]'
            )].map(field => {
                const type = (field.getAttribute('type') || '').toLowerCase();
                if (['password', 'hidden', 'file'].includes(type)) return null;
                const label = field.getAttribute('aria-label')
                    || field.getAttribute('placeholder')
                    || field.labels?.[0]?.innerText
                    || field.getAttribute('name')
                    || field.tagName.toLowerCase();
                const value = field.isContentEditable
                    ? field.innerText : (field.value || '');
                return value ? label + ': ' + String(value).slice(0, 2000) : null;
            }).filter(Boolean);
            return {
                hasForm: true,
                isEditable: isEditable && !isSearch,
                isSearch,
                formLabel: form.getAttribute('aria-label')
                    || form.getAttribute('name') || activeLabel,
                activeControl,
                submitters,
                fields: activeField && !fields.some(value => value.startsWith(
                    (activeLabel || 'Focused field') + ':'
                )) ? [...fields, activeField] : fields
            };
        }""")

    async def _confirmed_press(self, page, key: str, expected_url: str,
                               expected_context: dict) -> str:
        try:
            if page.is_closed() or page.url != expected_url:
                return outcome.failed(
                    "browser_control",
                    "the page changed while approval was pending; the key was not pressed",
                )
            current = await self._form_submission_context(page)
            if current != expected_context:
                return outcome.failed(
                    "browser_control",
                    "the form changed while approval was pending; the key was not pressed",
                )
            await page.keyboard.press(key)
            return outcome.unverified(
                "browser_control",
                "the approved submit key was pressed, but the website's final result "
                "was not independently verified",
                url=expected_url,
            )
        except Exception as exc:
            return outcome.failed("browser_control",
                                  f"approved browser key press failed: {exc}")

    async def press(self, key: str) -> str:
        page = await self._get_page()
        try:
            if key.strip().lower() in {"enter", "numpadenter"}:
                context = await self._form_submission_context(page)
                if (
                    context.get("isEditable")
                    or (
                        not context.get("isSearch")
                        and requires_screen_click_approval(context.get("activeControl", ""))
                    )
                    or (
                        context.get("hasForm")
                        and requires_form_submission_approval(
                            context.get("formLabel", ""), context.get("submitters", [])
                        )
                    )
                ):
                    page_url = page.url
                    page_title = await page.title()
                    action_label = next(
                        (label for label in context.get("submitters", [])
                         if requires_human_approval(label)),
                        context.get("activeControl") or context.get("formLabel") or "submit form",
                    )
                    detail = (
                        f"Website: {page_title}\n"
                        f"Address: {page_url}\n"
                        f"Submission control: {action_label}\n\n"
                        "Visible form values for review:\n"
                        + ("\n".join(context.get("fields", []))
                           if context.get("fields") else "(no visible values)")
                        + "\n\nThe page and form will be checked again before submission."
                    )
                    return self._request_browser_approval(
                        f"Approve browser submission: {action_label}", detail,
                        run=lambda: self.run(
                            self._confirmed_press(page, key, page_url, context)
                        ),
                    )
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4_000]
        except Exception as e:
            return f"Could not get page text: {e}"

    async def get_url(self) -> str:
        page = await self._get_page()
        return page.url

    async def _fill_form_after_approval(self, page, entries: list,
                                        expected_url: str) -> str:
        try:
            if page.is_closed() or page.url != expected_url:
                return outcome.failed(
                    "browser_control",
                    "the page changed while approval was pending; no form fields were changed",
                )
            # Validate every target before changing any of them. This prevents a
            # stale or shifted selector from partially filling a different form.
            for entry in entries:
                locator = entry["locator"]
                if await locator.count() == 0:
                    return outcome.failed(
                        "browser_control",
                        f"field '{entry['selector']}' is no longer available; nothing was filled",
                    )
                current = await self._field_snapshot(locator)
                if current != entry["snapshot"]:
                    return outcome.failed(
                        "browser_control",
                        f"field '{entry['selector']}' changed while approval was pending; "
                        "nothing was filled",
                    )
            completed = []
            for entry in entries:
                locator = entry["locator"]
                if entry["snapshot"].get("hasValue"):
                    await locator.clear()
                await locator.type(entry["text"], delay=40)
                matches = await locator.evaluate("""(el, expected) => {
                    const value = el.isContentEditable
                        ? el.innerText
                        : (('value' in el) ? el.value : el.textContent || '');
                    return String(value || '') === expected;
                }""", entry["text"])
                if not matches:
                    return outcome.unverified(
                        "browser_control",
                        "the form was partly filled, but one field did not match "
                        "the requested value",
                        completed=completed,
                        failed=entry["selector"],
                    )
                completed.append(entry["selector"])
            return outcome.done(
                "browser_control",
                filled=len(completed),
                verified=True,
            )
        except Exception as exc:
            return outcome.failed("browser_control", f"form filling failed: {exc}")

    async def fill_form(self, fields: dict) -> str:
        page = await self._get_page()
        if not isinstance(fields, dict) or not fields:
            return outcome.failed("browser_control", "no form fields were provided")
        try:
            entries = []
            for selector, value in fields.items():
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    return outcome.failed(
                        "browser_control",
                        f"field '{selector}' could not be found; no fields were changed",
                    )
                snapshot = await self._field_snapshot(locator)
                if not snapshot.get("editable"):
                    return outcome.failed(
                        "browser_control",
                        f"'{selector}' is not an editable text field; no fields were changed",
                    )
                entries.append({
                    "selector": str(selector),
                    "locator": locator,
                    "snapshot": snapshot,
                    "text": str(value),
                })

            expected_url = page.url
            needs_review = any(
                entry["snapshot"].get("hasValue") for entry in entries
            )
            if needs_review:
                review_lines = []
                for entry in entries:
                    snap = entry["snapshot"]
                    existing = (
                        "[password value hidden]"
                        if snap.get("password")
                        else (snap.get("value") or "[empty]")
                    )
                    replacement = (
                        f"[password value hidden; {len(entry['text'])} characters]"
                        if snap.get("password")
                        else entry["text"]
                    )
                    review_lines.append(
                        f"Field: {entry['selector']} ({snap.get('label')})\n"
                        f"Existing value:\n{existing}\n"
                        f"Replacement value:\n{replacement}"
                    )
                detail = (
                    f"Website: {await page.title()}\n"
                    f"Address: {expected_url}\n\n"
                    + "\n\n".join(review_lines)
                    + "\n\nMINDER will fill these fields only if the page and "
                    "all current field contents are unchanged when you approve."
                )
                if len(detail) > confirm.MAX_DETAIL_CHARS:
                    return outcome.failed(
                        "browser_control",
                        "the form is too large to display in full for safe approval; "
                        "no fields were changed",
                    )
                return self._request_browser_approval(
                    "Approve replacing existing browser form fields",
                    detail,
                    run=lambda: self.run(
                        self._fill_form_after_approval(page, entries, expected_url)
                    ),
                )

            return await self._fill_form_after_approval(page, entries, expected_url)
        except Exception as exc:
            return outcome.failed("browser_control", f"form filling failed: {exc}")

    async def smart_click(self, description: str) -> str:
        page = await self._get_page()
        for role in ("button", "link", "searchbox", "textbox", "menuitem", "tab"):
            try:
                loc = page.get_by_role(role, name=description)
                if await loc.count() > 0:
                    return await self._click_locator(page, loc.first, description)
            except Exception:
                pass
        candidates = (
            page.get_by_text(description, exact=False).first,
            page.get_by_placeholder(description, exact=False).first,
            page.locator(
                f'[alt*="{description}" i],[title*="{description}" i],'
                f'[aria-label*="{description}" i]'
            ).first,
        )
        for locator in candidates:
            try:
                if await locator.count() > 0:
                    return await self._click_locator(page, locator, description)
            except Exception:
                pass
        return f"Could not find element: '{description}'"

    async def smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()
        candidates = [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox", name=description)),
            ("searchbox",   page.get_by_role("searchbox")),
            ("combobox",    page.get_by_role("combobox", name=description)),
        ]
        for method, loc in candidates:
            try:
                el = loc.first
                if await el.count() == 0:
                    continue
                return await self._guarded_type(
                    page, el, str(text), True, f"{method}: {description}",
                )
            except Exception:
                continue
        return f"Could not find input: '{description}'"

    async def new_tab(self, url: str = "") -> str:
        page = await self._get_page()
        ctx  = page.context
        new  = await ctx.new_page()
        self._page = new
        if url:
            return await self.go_to(url)
        return "New tab opened."

    async def close_tab(self) -> str:
        page = self._page
        if page and not page.is_closed():
            ctx   = page.context
            await page.close()
            pages = ctx.pages
            self._page = pages[-1] if pages else None
            return "Tab closed."
        return "No active tab to close."

    async def screenshot(self, path: str = None) -> str:
        page = await self._get_page()
        try:
            save_path = path or str(Path.home() / "Desktop" / "jarvis_screenshot.png")
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"

    async def back(self) -> str:
        page = await self._get_page()
        try:
            await page.go_back(timeout=10_000)
            return f"Navigated back: {page.url}"
        except Exception as e:
            return f"Back error: {e}"

    async def forward(self) -> str:
        page = await self._get_page()
        try:
            await page.go_forward(timeout=10_000)
            return f"Navigated forward: {page.url}"
        except Exception as e:
            return f"Forward error: {e}"

    async def reload(self) -> str:
        page = await self._get_page()
        try:
            await page.reload(timeout=15_000)
            return f"Page reloaded: {page.url}"
        except Exception as e:
            return f"Reload error: {e}"

    async def close_browser(self) -> str:
        await self._async_close()
        return f"{self.browser_name} closed."

class _SessionRegistry:
    """Manages all active browser sessions."""

    def __init__(self):
        self._sessions:        dict[str, _BrowserSession] = {}
        self._active_browser:  str                        = ""
        self._lock             = threading.Lock()
        self._last_native_url: str                        = ""

    def has(self, browser_name: str | None = None) -> bool:
        """Is there an active automation session for this browser (or any)?"""
        with self._lock:
            if not browser_name:
                return bool(self._sessions)
            name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
            return name in self._sessions

    def note_native_url(self, url: str) -> None:
        self._last_native_url = url

    def pop_native_url(self) -> str:
        """Returns the last natively-opened URL once (consumed to avoid repeats)."""
        url, self._last_native_url = self._last_native_url, ""
        return url

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            if browser_name not in self._sessions:
                sess = _BrowserSession(browser_name)
                sess.start()
                self._sessions[browser_name] = sess
                print(f"[Registry] New session: {browser_name}")
            return self._sessions[browser_name]

    def get(self, browser_name: str | None = None) -> _BrowserSession:
        if not browser_name:
            browser_name = self._active_browser or _detect_default_browser()
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        sess = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            names    = list(self._sessions.keys())
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed: " + (", ".join(names) if names else "none")

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                marker = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{marker}")
            return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()

def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = "Unknown action."

    if action == "switch":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Please specify a browser."
        _log(player, result)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        _log(player, result)
        return result

    if action == "close_all":
        sessions = _registry.list_sessions()
        detail = (
            "MINDER-managed browser sessions:\n"
            + str(sessions)
            + "\n\nClosing these sessions closes their managed tabs and may "
            "discard unsaved forms or drafts."
        )
        result = _request_browser_lifecycle_confirmation(
            "Approve closing all MINDER browser sessions",
            detail,
            run=lambda: _registry.close_all(),
        )
        _log(player, result)
        return result

    if action == "close":
        target = browser or _registry._active_browser
        if not target:
            return "No browser specified."
        detail = (
            f"Browser session: {target}\n\n"
            "Closing this managed browser session may discard unsaved forms or drafts."
        )
        result = _request_browser_lifecycle_confirmation(
            f"Approve closing browser session: {target}",
            detail,
            run=lambda: _registry.close_one(target),
        )
        _log(player, result)
        return result

    # ── Navigation is ALWAYS native ──────────────────────────────────────────
    # go_to / search / new_tab open the site in the user's own browser —
    # their own profile, logged-in accounts and start page; exactly as if the
    # user had opened it themselves. A controlled window with about:blank never
    # opens here. The only exception: if an automation flow is already running,
    # navigation continues in that window (so multi-step tasks aren't split).
    if action in ("go_to", "search", "new_tab"):
        if _registry.has(browser):
            sess = _registry.get(browser)
            try:
                if action == "search":
                    result = sess.run(sess.search(params.get("query", ""),
                                                  params.get("engine", "google")))
                elif action == "new_tab":
                    result = sess.run(sess.new_tab(params.get("url", "")))
                else:
                    result = sess.run(sess.go_to(params.get("url", "")))
            except concurrent.futures.TimeoutError:
                result = f"Browser action '{action}' timed out (60s)."
            except Exception as e:
                result = f"Browser error ({action}): {e}"
            _log(player, result)
            return result

        if action == "search":
            base    = _SEARCH_ENGINES.get(params.get("engine", "google").lower(),
                                          _SEARCH_ENGINES["google"])
            nav_url = base + quote_plus(params.get("query", ""))
        else:
            nav_url = params.get("url", "").strip()

        try:
            nav_url = _normalize_url(nav_url)
            result = _open_native(nav_url, browser)
        except ValueError as exc:
            result = outcome.failed("browser_control", str(exc), url=nav_url)
        if result.startswith("Opened") and nav_url and nav_url != "about:blank":
            _registry.note_native_url(_normalize_url(nav_url))
        _log(player, result)
        return result

    # ── Interactive actions (click/type/read…) ───────────────────────────────
    # These require a physically controllable browser; the automation window
    # only opens here, and as soon as it opens it goes to the user's last
    # navigated page — it doesn't sit on a blank page.
    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        _log(player, result)
        return result

    try:
        last = _registry.pop_native_url()
        if last:
            try:
                sess.run(sess.go_to(last))
            except Exception as e:
                print(f"[Browser] Could not resume last page ({last}): {e}")

        if action == "click":
            result = sess.run(sess.click(params.get("selector"), params.get("text")))
        elif action == "type":
            result = sess.run(sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
        elif action == "scroll":
            result = sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
        elif action == "fill_form":
            result = sess.run(sess.fill_form(params.get("fields", {})))
        elif action == "smart_click":
            result = sess.run(sess.smart_click(params.get("description", "")))
        elif action == "smart_type":
            result = sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
        elif action == "get_text":
            result = sess.run(sess.get_text())
        elif action == "get_url":
            result = sess.run(sess.get_url())
        elif action == "press":
            result = sess.run(sess.press(params.get("key", "Enter")))
        elif action == "close_tab":
            current_url = sess.run(sess.get_url())
            detail = (
                f"Browser: {browser or 'active browser'}\n"
                f"Tab: {current_url}\n\n"
                "Closing this tab may discard unsent messages, form entries or drafts."
            )
            result = _request_browser_lifecycle_confirmation(
                "Approve closing the active browser tab",
                detail,
                run=lambda: sess.run(sess.close_tab()),
            )
        elif action == "screenshot":
            result = sess.run(sess.screenshot(params.get("path")))
        elif action in ("back", "forward", "reload"):
            nav_word = {"back": "go back", "forward": "go forward", "reload": "reload"}[action]
            current_url = sess.run(sess.get_url())
            detail = (
                f"Browser: {browser or 'active browser'}\n"
                f"Current page: {current_url}\n\n"
                "This navigation may discard unsent messages, form entries or drafts."
            )
            navigation = {"back": sess.back, "forward": sess.forward, "reload": sess.reload}[action]
            result = _request_browser_lifecycle_confirmation(
                f"Approve browser action: {nav_word}",
                detail,
                run=lambda: sess.run(navigation()),
            )
        else:
            result = f"Unknown browser action: '{action}'"

    except concurrent.futures.TimeoutError:
        result = f"Browser action '{action}' timed out (60s)."
    except Exception as e:
        result = f"Browser error ({action}): {e}"

    _log(player, result)
    return result


def _request_browser_lifecycle_confirmation(title: str, detail: str, run) -> str:
    """Confirm closing or leaving a browser surface that may contain unsaved work."""
    if len(detail) > confirm.MAX_DETAIL_CHARS:
        return outcome.failed("browser_control",
                              "the browser action is too long to display for approval")
    pending = confirm.pending_title()
    if pending:
        return outcome.ask(
            "browser_control",
            "There is already a confirmation waiting on screen. "
            "The user must answer it before another browser action can be approved.",
            pending=pending,
        )
    return confirm.request(
        key="browser-lifecycle",
        title=title,
        detail=detail,
        run=run,
    )


def _log(player, text: str):
    short = str(text)[:80]
    print(f"[Browser] {short}")
    if player:
        print(f"[Browser] {short[:80]}")          # terminal, not the HUD


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "browser_control",
    "description": (
        "Controls the web browser: open an address, search, click, type, "
        "scroll, read the page, manage tabs, go back and forward. "
        "High-impact submit/send/publish/purchase/delete controls, unknown action "
        "buttons, form submissions by Enter, replacing existing browser field data, "
        "and leaving pages with unsaved forms/files require human approval on the HUD. "
        "THIS TOOL OPENS THE BROWSER ITSELF. Never call open_app for the "
        "browser first - doing both launches it twice, the second copy on a "
        "different profile with none of the user's sign-ins. 'Open Chrome and "
        "go to YouTube' is ONE call to this tool. "
        "Read the status it returns. 'done' means it was confirmed - for an "
        "open, the url reported back is the page's OWN address, so if it "
        "differs from what was asked for the site redirected and the user "
        "should be told. 'unverified' means the address was handed to the "
        "system's default browser, which never reports back: say it was opened "
        "but not confirmed, never that the page is up. 'failed' means nothing "
        "happened."
    ),
    "brief": (
        "Controls the web browser: open a URL, navigate, click, read the page."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"
            },
            "browser": {
                "type": "STRING",
                "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."
            },
            "url": {
                "type": "STRING",
                "description": "URL for go_to / new_tab action"
            },
            "query": {
                "type": "STRING",
                "description": "Search query for search action"
            },
            "engine": {
                "type": "STRING",
                "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"
            },
            "selector": {
                "type": "STRING",
                "description": "CSS selector for click/type"
            },
            "text": {
                "type": "STRING",
                "description": "Text to click or type"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for smart_click/smart_type"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down for scroll"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount in pixels (default: 500)"
            },
            "key": {
                "type": "STRING",
                "description": "Key name for press action (e.g. Enter, Escape, F5)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            },
            "incognito": {
                "type": "BOOLEAN",
                "description": "Open in private/incognito mode"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": browser_control,
}
