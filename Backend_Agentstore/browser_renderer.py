"""Headless-browser page rendering for websites that build their content with JavaScript.

The crawler downloads pages with plain HTTP first. Sites built with React/Vue/Nuxt return
an almost empty HTML shell to such requests, so for those pages the crawler asks this
renderer to open the page in headless Chromium (via Playwright), let its JavaScript run,
and return the resulting HTML.
"""

from __future__ import annotations

import os
from typing import Any

BROWSER_MODES = {"auto", "always", "never"}
DEFAULT_MIN_TEXT_CHARS = 1000
CRAWLER_TOKEN = "AgentStoreIngestion/1.0"
# Images, video and fonts are not needed to read a page's text; skipping them makes rendering faster.
_SKIPPED_RESOURCE_TYPES = {"image", "media", "font"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value >= minimum else default


def normalize_browser_mode(value: Any, fallback: str = "auto") -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in BROWSER_MODES else fallback


class BrowserRenderer:
    """Renders pages in one headless Chromium that is started on first use and reused.

    Playwright's sync API is bound to the thread that started it, so create one renderer
    per crawl (inside the crawling thread) and close it when the crawl ends.
    """

    def __init__(self, mode: str | None = None, min_text_chars: int | None = None) -> None:
        env_mode = normalize_browser_mode(os.getenv("WEB_CRAWL_BROWSER"), "auto")
        self.mode = normalize_browser_mode(mode, env_mode)
        self.min_text_chars = (
            min_text_chars
            if min_text_chars is not None
            else _env_int("WEB_CRAWL_BROWSER_MIN_CHARS", DEFAULT_MIN_TEXT_CHARS)
        )
        self.timeout_ms = _env_int("WEB_CRAWL_BROWSER_TIMEOUT_SECONDS", 30, minimum=1) * 1000
        # Sites with analytics or chat widgets never go fully idle, so cap that wait.
        self.idle_wait_ms = _env_int("WEB_CRAWL_BROWSER_IDLE_WAIT_SECONDS", 8) * 1000
        self.settle_ms = _env_int("WEB_CRAWL_BROWSER_SETTLE_MS", 1500)
        self._playwright = None
        self._browser = None
        self._context = None
        self._unavailable_reason: str | None = None

    def __enter__(self) -> "BrowserRenderer":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def should_render(self, plain_text: str, *, fetch_failed: bool = False) -> bool:
        """Decide whether a page needs the browser after the plain HTTP download."""
        if self.mode == "never" or self._unavailable_reason:
            return False
        if self.mode == "always" or fetch_failed:
            return True
        return len(plain_text or "") < self.min_text_chars

    def _start(self) -> None:
        if self._context is not None:
            return
        if self._unavailable_reason:
            raise RuntimeError(self._unavailable_reason)
        try:
            # Imported lazily so the backend still runs where Playwright is not installed.
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                # Docker limits /dev/shm to 64 MB and runs as root, which Chromium needs told about.
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            user_agent = os.getenv("WEB_CRAWL_BROWSER_USER_AGENT", "").strip() or (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{self._browser.version} Safari/537.36 {CRAWLER_TOKEN}"
            )
            self._context = self._browser.new_context(
                user_agent=user_agent,
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            self._context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in _SKIPPED_RESOURCE_TYPES
                else route.continue_(),
            )
        except Exception as exc:
            self.close()
            # Remember the failure so a crawl does not retry a missing browser on every page.
            self._unavailable_reason = f"Headless browser is unavailable: {exc}"
            raise RuntimeError(self._unavailable_reason) from exc

    def render(self, url: str) -> bytes:
        """Open the page, let its JavaScript run, and return the rendered HTML."""
        self._start()
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"Browser received HTTP {response.status} for {url}")
            try:
                # Wait for API calls that fill in the content; some sites never go fully idle.
                if self.idle_wait_ms:
                    page.wait_for_load_state("networkidle", timeout=min(self.timeout_ms, self.idle_wait_ms))
            except Exception:
                pass
            # Scroll to the bottom so lazy-loaded sections are requested, then let them settle.
            page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
            if self.settle_ms:
                page.wait_for_timeout(self.settle_ms)
            return page.content().encode("utf-8")
        finally:
            page.close()

    def close(self) -> None:
        for resource, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:
                    pass
        self._context = self._browser = self._playwright = None
