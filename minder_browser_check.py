"""Headless smoke test for MINDER's bundled Playwright engines.

This check renders only local HTML in disposable browser contexts. It does not
visit the network, read browser profiles, or use the user's saved sessions.
"""
from __future__ import annotations

from playwright.sync_api import BrowserType, sync_playwright


_HTML = """<!doctype html>
<html>
<head><title>MINDER browser smoke test</title></head>
<body>
  <main id="minder-status">ready</main>
  <script>document.body.dataset.minderSmoke = "passed";</script>
</body>
</html>
"""


def _check_browser(name: str, browser_type: BrowserType) -> bool:
    browser = None
    try:
        browser = browser_type.launch(headless=True)
        page = browser.new_page()
        page.set_content(_HTML)
        title = page.title()
        status = page.locator("#minder-status").inner_text()
        script_result = page.locator("body").get_attribute("data-minder-smoke")
        if title != "MINDER browser smoke test" or status != "ready" or script_result != "passed":
            raise RuntimeError("browser launched but local DOM/JavaScript verification failed")
        print(f"[OK] {name}: launched, rendered local HTML and executed JavaScript")
        return True
    except Exception as exc:
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return False
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def main() -> int:
    print("MINDER PLAYWRIGHT CHECK - local only; no network or user profiles")
    with sync_playwright() as runtime:
        results = (
            _check_browser("Chromium", runtime.chromium),
            _check_browser("Firefox", runtime.firefox),
        )
    if all(results):
        print("RESULT: ALL BROWSER RUNTIMES PASSED")
        return 0
    print("RESULT: ONE OR MORE BROWSER RUNTIMES FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
