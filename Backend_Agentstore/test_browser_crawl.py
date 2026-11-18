import os
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, Mock, patch

import requests

from browser_renderer import BrowserRenderer, normalize_browser_mode
from ingestion_service import IngestionService

SHELL_HTML = b"<html><head><title>Shell</title></head><body><div id='app'></div><a href='/about'>About</a></body></html>"
FULL_TEXT = "Fiber unlimited 100 Mbps home plan costs Rs. 5000 per month. " * 30
RENDERED_HTML = (
    f"<html><head><title>Packages</title></head><body><p>{FULL_TEXT}</p>"
    "<a href='/packages/fiber'>Fiber</a><a href='/about'>About</a></body></html>"
).encode("utf-8")


def html_response(body, status=200, content_type="text/html; charset=utf-8"):
    response = Mock(content=body, status_code=status, headers={"content-type": content_type})
    if status >= 400:
        error = requests.HTTPError(f"{status} error")
        error.response = response
        response.raise_for_status = Mock(side_effect=error)
    else:
        response.raise_for_status = Mock()
    return response


class FakeRenderer:
    def __init__(self, mode="auto", html=RENDERED_HTML, error=None, min_text_chars=1000):
        self.real = BrowserRenderer(mode=mode, min_text_chars=min_text_chars)
        self.html = html
        self.error = error
        self.rendered_urls = []

    def should_render(self, text, *, fetch_failed=False):
        return self.real.should_render(text, fetch_failed=fetch_failed)

    def render(self, url):
        self.rendered_urls.append(url)
        if self.error:
            raise self.error
        return self.html


class BrowserRendererDecisionTests(unittest.TestCase):
    def test_mode_comes_from_option_then_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(BrowserRenderer().mode, "auto")
        with patch.dict(os.environ, {"WEB_CRAWL_BROWSER": "never"}):
            self.assertEqual(BrowserRenderer().mode, "never")
            self.assertEqual(BrowserRenderer(mode="always").mode, "always")
            self.assertEqual(BrowserRenderer(mode="bogus").mode, "never")
        self.assertEqual(normalize_browser_mode(" ALWAYS "), "always")

    def test_auto_renders_only_thin_or_refused_pages(self):
        renderer = BrowserRenderer(mode="auto", min_text_chars=100)
        self.assertTrue(renderer.should_render("menu only"))
        self.assertFalse(renderer.should_render("x" * 200))
        self.assertTrue(renderer.should_render("x" * 200, fetch_failed=True))
        self.assertTrue(BrowserRenderer(mode="always").should_render("x" * 5000))
        self.assertFalse(BrowserRenderer(mode="never").should_render("", fetch_failed=True))

    def test_missing_playwright_disables_browser_for_the_rest_of_the_crawl(self):
        renderer = BrowserRenderer(mode="auto")
        broken = ModuleType("playwright.sync_api")
        broken.sync_playwright = MagicMock(side_effect=RuntimeError("no chromium"))
        with patch.dict(sys.modules, {"playwright": ModuleType("playwright"), "playwright.sync_api": broken}):
            with self.assertRaisesRegex(RuntimeError, "Headless browser is unavailable"):
                renderer.render("https://example.com")
        self.assertFalse(renderer.should_render(""))
        broken.sync_playwright.assert_called_once()

    def test_render_returns_page_html_and_closes_resources(self):
        page = MagicMock()
        page.goto.return_value = Mock(status=200)
        page.content.return_value = "<html>rendered</html>"
        context = MagicMock()
        context.new_page.return_value = page
        browser = MagicMock(version="131.0")
        browser.new_context.return_value = context
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        sync_api = ModuleType("playwright.sync_api")
        sync_api.sync_playwright = MagicMock(return_value=MagicMock(start=Mock(return_value=playwright)))

        with patch.dict(sys.modules, {"playwright": ModuleType("playwright"), "playwright.sync_api": sync_api}):
            with BrowserRenderer(mode="always") as renderer:
                renderer.settle_ms = 0
                self.assertEqual(renderer.render("https://example.com"), b"<html>rendered</html>")
                self.assertEqual(renderer.render("https://example.com/b"), b"<html>rendered</html>")

        playwright.chromium.launch.assert_called_once()
        self.assertIn("AgentStoreIngestion/1.0", browser.new_context.call_args.kwargs["user_agent"])
        self.assertEqual(page.close.call_count, 2)
        context.close.assert_called_once()
        browser.close.assert_called_once()
        playwright.stop.assert_called_once()

    def test_render_rejects_error_status(self):
        page = MagicMock()
        page.goto.return_value = Mock(status=403)
        renderer = BrowserRenderer(mode="always")
        renderer._context = MagicMock(new_page=Mock(return_value=page))
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            renderer.render("https://example.com")
        page.close.assert_called_once()


@patch("ingestion_service.QdrantClient")
class FetchCrawlPageTests(unittest.TestCase):
    def test_thin_javascript_page_uses_rendered_text_and_merges_links(self, _qdrant):
        renderer = FakeRenderer()
        page = IngestionService()._fetch_crawl_page("https://example.com", html_response(SHELL_HTML), renderer)

        self.assertTrue(page["rendered"])
        self.assertEqual(page["title"], "Packages")
        self.assertIn("Fiber unlimited 100 Mbps", page["text"])
        self.assertEqual(page["links"], ["/about", "/packages/fiber"])

    def test_content_rich_page_skips_browser(self, _qdrant):
        renderer = FakeRenderer()
        page = IngestionService()._fetch_crawl_page("https://example.com", html_response(RENDERED_HTML), renderer)

        self.assertFalse(page["rendered"])
        self.assertEqual(renderer.rendered_urls, [])

    def test_never_mode_keeps_plain_text(self, _qdrant):
        renderer = FakeRenderer(mode="never")
        page = IngestionService()._fetch_crawl_page("https://example.com", html_response(SHELL_HTML), renderer)

        self.assertFalse(page["rendered"])
        self.assertEqual(page["text"], "Shell About")
        self.assertEqual(renderer.rendered_urls, [])

    def test_browser_failure_falls_back_to_plain_text(self, _qdrant):
        renderer = FakeRenderer(error=RuntimeError("timeout"))
        page = IngestionService()._fetch_crawl_page("https://example.com", html_response(SHELL_HTML), renderer)

        self.assertFalse(page["rendered"])
        self.assertEqual(page["text"], "Shell About")

    @patch("ingestion_service.requests.get")
    def test_refused_plain_request_is_retried_in_browser(self, get, _qdrant):
        get.return_value = html_response(b"Forbidden", status=403)
        renderer = FakeRenderer()
        page = IngestionService()._fetch_crawl_page("https://example.com/p", None, renderer)

        self.assertTrue(page["rendered"])
        self.assertEqual(renderer.rendered_urls, ["https://example.com/p"])

    @patch("ingestion_service.requests.get")
    def test_missing_page_is_not_retried_in_browser(self, get, _qdrant):
        get.return_value = html_response(b"Not found", status=404)
        renderer = FakeRenderer()
        page = IngestionService()._fetch_crawl_page("https://example.com/missing", None, renderer)

        self.assertIn("404", page["skip"])
        self.assertEqual(renderer.rendered_urls, [])

    def test_non_html_page_is_skipped(self, _qdrant):
        page = IngestionService()._fetch_crawl_page(
            "https://example.com/a.pdf", html_response(b"%PDF", content_type="application/pdf"), FakeRenderer()
        )
        self.assertEqual(page, {"skip": "non_html_crawl_page", "browser_used": False})

    def test_reports_browser_use_even_when_it_fails(self, _qdrant):
        page = IngestionService()._fetch_crawl_page(
            "https://example.com", html_response(SHELL_HTML), FakeRenderer(error=RuntimeError("HTTP 403"))
        )
        self.assertTrue(page["browser_used"])
        self.assertFalse(page["rendered"])

        page = IngestionService()._fetch_crawl_page("https://example.com", html_response(RENDERED_HTML), FakeRenderer())
        self.assertFalse(page["browser_used"])


@patch("ingestion_service.QdrantClient")
class CrawlWithBrowserTests(unittest.TestCase):
    @patch.dict(os.environ, {"WEB_CRAWL_DELAY_SECONDS": "0.25", "WEB_CRAWL_BROWSER_DELAY_SECONDS": "4"})
    @patch("ingestion_service.time.sleep")
    @patch("ingestion_service.requests.get")
    def test_crawl_waits_longer_after_browser_pages(self, get, sleep, _qdrant):
        content_page = RENDERED_HTML.replace(b"href='/packages/fiber'", b"href='/thin'")

        def respond(url, **_kw):
            return html_response(SHELL_HTML if url.endswith("/thin") or url.endswith("/about") else content_page)

        get.side_effect = respond
        service = IngestionService()
        service._robots_parser = Mock(return_value=None)
        service._delete_missing_source_documents = Mock(return_value=0)
        service._upsert_document = Mock(return_value={"chunks_indexed": 1})
        fake = FakeRenderer(error=RuntimeError("HTTP 403"))

        with patch("ingestion_service.BrowserRenderer") as renderer_cls:
            renderer_cls.return_value.__enter__.return_value = fake
            service._crawl_website(
                company_id=2,
                agent_id=5,
                source_id=9,
                start_url="https://example.com/",
                first_response=html_response(content_page),
                crawl_options={"max_pages": 3, "max_depth": 1},
            )

        # The content-rich start page needs no browser; both thin pages tried the browser.
        self.assertEqual(fake.rendered_urls, ["https://example.com/thin", "https://example.com/about"])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.25, 4.0, 4.0])

    @patch.dict(os.environ, {"WEB_CRAWL_DELAY_SECONDS": "0", "WEB_CRAWL_BROWSER_DELAY_SECONDS": "0"})
    @patch("ingestion_service.BrowserRenderer")
    @patch("ingestion_service.requests.get")
    def test_crawl_indexes_rendered_pages_and_follows_rendered_links(self, get, renderer_cls, _qdrant):
        fake = FakeRenderer()
        renderer_cls.return_value.__enter__.return_value = fake
        get.side_effect = lambda url, **_kw: html_response(SHELL_HTML)
        service = IngestionService()
        service._robots_parser = Mock(return_value=None)
        service._delete_missing_source_documents = Mock(return_value=0)
        service._upsert_document = Mock(return_value={"chunks_indexed": 2})

        totals = service._crawl_website(
            company_id=2,
            agent_id=5,
            source_id=9,
            start_url="https://example.com/",
            first_response=html_response(SHELL_HTML),
            crawl_options={"max_pages": 3, "max_depth": 1, "browser": "auto"},
        )

        renderer_cls.assert_called_once_with(mode="auto")
        renderer_cls.return_value.__exit__.assert_called_once()
        self.assertEqual(totals["pages_crawled"], 3)
        self.assertEqual(totals["pages_rendered"], 3)
        self.assertEqual(
            fake.rendered_urls,
            ["https://example.com/", "https://example.com/about", "https://example.com/packages/fiber"],
        )
        self.assertIn("Fiber unlimited", service._upsert_document.call_args.kwargs["text"])

    @patch("ingestion_service.requests.get")
    def test_blocked_start_page_still_crawls_through_browser(self, get, _qdrant):
        get.return_value = html_response(b"Forbidden", status=403)
        service = IngestionService()
        service._crawl_website = Mock(return_value={"chunks_indexed": 1})

        service.ingest_website(company_id=2, agent_id=5, source_id=9, url="https://example.com")

        self.assertIsNone(service._crawl_website.call_args.kwargs["first_response"])

    @patch("ingestion_service.requests.get")
    def test_blocked_start_page_raises_when_browser_disabled(self, get, _qdrant):
        get.return_value = html_response(b"Forbidden", status=403)
        service = IngestionService()
        service._crawl_website = Mock()

        with self.assertRaises(requests.HTTPError):
            service.ingest_website(
                company_id=2, agent_id=5, source_id=9, url="https://example.com", crawl_options={"browser": "never"}
            )
        service._crawl_website.assert_not_called()


if __name__ == "__main__":
    unittest.main()
