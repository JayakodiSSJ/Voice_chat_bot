from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import io
import os
import re
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

from agent_configuration_service import company_kb_collection_id
from agent_runtime_utils import clean_text
from browser_renderer import BrowserRenderer, normalize_browser_mode
from openai_service import openai_service

load_dotenv()


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title and data.strip():
            self.title_parts.append(data.strip())

    def title(self) -> str:
        return clean_text(" ".join(self.title_parts))


def _extract_html_text(raw: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(raw.decode("utf-8", errors="ignore"))
    return parser.text()


def _extract_html_links_and_title(raw: bytes) -> tuple[list[str], str]:
    parser = _LinkExtractor()
    parser.feed(raw.decode("utf-8", errors="ignore"))
    return parser.links, parser.title()


def _extract_docx_text(raw: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as docx:
        for name in docx.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                parts.append(_extract_html_text(docx.read(name)))
    return clean_text(" ".join(parts))


def _xml_text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def _extract_xlsx_text(raw: bytes) -> str:
    rows: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as workbook:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in root.iter():
                if item.tag.endswith("}si") or item.tag == "si":
                    shared_strings.append(_xml_text(item))

        sheet_names = sorted(
            name
            for name in workbook.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        for sheet_name in sheet_names:
            root = ET.fromstring(workbook.read(sheet_name))
            for row in root.iter():
                if not (row.tag.endswith("}row") or row.tag == "row"):
                    continue
                values: list[str] = []
                for cell in row:
                    if not (cell.tag.endswith("}c") or cell.tag == "c"):
                        continue
                    cell_type = cell.attrib.get("t")
                    value = ""
                    if cell_type == "inlineStr":
                        value = _xml_text(cell)
                    else:
                        raw_value = next(
                            (_xml_text(child) for child in cell if child.tag.endswith("}v") or child.tag == "v"),
                            "",
                        )
                        if cell_type == "s" and raw_value.isdigit():
                            index = int(raw_value)
                            if 0 <= index < len(shared_strings):
                                value = shared_strings[index]
                        else:
                            value = raw_value
                    if value:
                        values.append(value)
                if values:
                    rows.append(" | ".join(values))
    return clean_text(" ; ".join(rows))


def _extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError("PDF ingestion requires pypdf. Install backend requirements and retry.") from exc

    reader = PdfReader(io.BytesIO(raw))
    text = clean_text(" ".join(page.extract_text() or "" for page in reader.pages))
    if not text:
        raise RuntimeError("PDF contains no extractable text. Scanned/image-only PDFs need OCR before ingestion.")
    return text


def _chunk_text(text: str, chunk_chars: int = 1200, overlap: int = 160) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunk = text[start : start + chunk_chars].strip()
        if chunk:
            chunks.append(chunk)
        start += max(1, chunk_chars - overlap)
    return chunks


def _point_id(company_id: int, agent_id: int, source_id: int, document_id: str, chunk_index: int) -> str:
    raw = f"{company_id}:{agent_id}:{source_id}:{document_id}:{chunk_index}"
    return str(uuid.UUID(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]))


class IngestionService:
    def __init__(self):
        self.qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY") or None
        self.embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small").strip() or "text-embedding-3-small"
        self.vector_size = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS") or os.getenv("QDRANT_VECTOR_SIZE", "1536"))
        self.qdrant_timeout = float(os.getenv("QDRANT_TIMEOUT_SECONDS", "10"))
        self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=self.qdrant_timeout)

    def collection_name(self, company_id: int) -> str:
        return company_kb_collection_id(company_id)

    def ensure_company_collection(self, company_id: int) -> str:
        collection = self.collection_name(company_id)
        existing = [item.name for item in self.client.get_collections().collections]
        if collection not in existing:
            try:
                self.client.create_collection(
                    collection_name=collection,
                    vectors_config=qdrant_models.VectorParams(
                        size=self.vector_size,
                        distance=qdrant_models.Distance.COSINE,
                    ),
                )
            except Exception:
                # Website sources are ingested concurrently. Another ingestion
                # worker can create this shared company collection after the
                # existence check above but before this create request.
                current = [item.name for item in self.client.get_collections().collections]
                if collection not in current:
                    raise
        try:
            self.client.create_payload_index(
                collection_name=collection,
                field_name="agent_id",
                field_schema=qdrant_models.PayloadSchemaType.INTEGER,
            )
        except Exception:
            pass
        return collection

    def _delete_points(self, collection: str, conditions: list[qdrant_models.FieldCondition]) -> None:
        self.client.delete(
            collection_name=collection,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(must=conditions)
            ),
            wait=True,
        )

    def delete_source_points(self, *, company_id: int, agent_id: int, source_id: int) -> None:
        collection = self.collection_name(company_id)
        self._delete_points(
            collection,
            [
                qdrant_models.FieldCondition(
                    key="agent_id",
                    match=qdrant_models.MatchValue(value=agent_id),
                ),
                qdrant_models.FieldCondition(
                    key="source_id",
                    match=qdrant_models.MatchValue(value=source_id),
                ),
            ],
        )

    def delete_agent_points(self, *, company_id: int, agent_id: int) -> None:
        collection = self.collection_name(company_id)
        existing = [item.name for item in self.client.get_collections().collections]
        if collection not in existing:
            return
        self._delete_points(
            collection,
            [
                qdrant_models.FieldCondition(
                    key="agent_id",
                    match=qdrant_models.MatchValue(value=agent_id),
                ),
            ],
        )

    def ingest_website(self, *, company_id: int, agent_id: int, source_id: int, url: str, crawl_options: dict[str, Any] | None = None) -> dict[str, Any]:
        start_url = self._normalize_web_url(url)
        parsed = urlparse(start_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Website source must be an http or https URL.")

        filename = unquote(os.path.basename(parsed.path or ""))
        try:
            response = requests.get(start_url, timeout=20, headers={"User-Agent": "AgentStoreIngestion/1.0"})
            response.raise_for_status()
        except Exception as exc:
            # Some sites refuse plain HTTP clients but serve a real browser. Let the crawler
            # retry the start page in the headless browser unless that is switched off.
            browser_mode = normalize_browser_mode((crawl_options or {}).get("browser"), BrowserRenderer().mode)
            if browser_mode == "never" or not self._browser_may_help(exc):
                raise
            return self._crawl_website(
                company_id=company_id,
                agent_id=agent_id,
                source_id=source_id,
                start_url=start_url,
                first_response=None,
                crawl_options=crawl_options,
            )
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type or not filename:
            return self._crawl_website(
                company_id=company_id,
                agent_id=agent_id,
                source_id=source_id,
                start_url=start_url,
                first_response=response,
                crawl_options=crawl_options,
            )

        text = self._extract_file_text(filename, response.content, content_type)
        return self._upsert_document(
            company_id=company_id,
            agent_id=agent_id,
            source_id=source_id,
            source_type="website",
            document_id=start_url,
            title=start_url,
            text=text,
        )

    def _crawl_website(
        self,
        *,
        company_id: int,
        agent_id: int,
        source_id: int,
        start_url: str,
        first_response: requests.Response | None,
        crawl_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = crawl_options or {}
        with BrowserRenderer(mode=options.get("browser")) as renderer:
            return self._crawl_website_pages(
                company_id=company_id,
                agent_id=agent_id,
                source_id=source_id,
                start_url=start_url,
                first_response=first_response,
                options=options,
                renderer=renderer,
            )

    @staticmethod
    def _browser_may_help(exc: Exception) -> bool:
        # A missing page stays missing in a browser; blocks and connection errors may not.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status not in {404, 410}

    def _fetch_crawl_page(
        self,
        page_url: str,
        prepared_response: requests.Response | None,
        renderer: BrowserRenderer,
    ) -> dict[str, Any]:
        """Download a page, falling back to the headless browser when the plain HTML is
        nearly empty (JavaScript-built sites) or the plain request was refused.

        Returns {"links", "title", "text", "rendered", "browser_used"} or
        {"skip": reason, "browser_used"}. "browser_used" is true whenever the browser
        opened the page, even if that failed, so the crawler can pace itself.
        """
        response = None
        fetch_error: Exception | None = None
        try:
            response = prepared_response or requests.get(page_url, timeout=20, headers={"User-Agent": "AgentStoreIngestion/1.0"})
            response.raise_for_status()
        except Exception as exc:
            response, fetch_error = None, exc

        links: list[str] = []
        title = ""
        text = ""
        if response is not None:
            if "html" not in response.headers.get("content-type", "").lower():
                return {"skip": "non_html_crawl_page", "browser_used": False}
            links, title = _extract_html_links_and_title(response.content)
            text = _extract_html_text(response.content)
        elif not self._browser_may_help(fetch_error):
            return {"skip": str(fetch_error), "browser_used": False}

        rendered = False
        browser_used = False
        if renderer.should_render(text, fetch_failed=response is None):
            browser_used = True
            try:
                html = renderer.render(page_url)
            except Exception as exc:
                print(f"WARNING web_crawl: browser rendering failed for {page_url}: {exc}", flush=True)
                if response is None:
                    return {"skip": f"{fetch_error}; browser fallback failed: {exc}", "browser_used": True}
            else:
                rendered_links, rendered_title = _extract_html_links_and_title(html)
                rendered_text = _extract_html_text(html)
                if len(rendered_text) > len(text):
                    text, title, rendered = rendered_text, rendered_title or title, True
                # Keep links from both versions; JavaScript menus often add the useful ones.
                links = list(dict.fromkeys([*links, *rendered_links]))
        elif response is None:
            return {"skip": str(fetch_error), "browser_used": False}

        return {"links": links, "title": title, "text": text, "rendered": rendered, "browser_used": browser_used}

    def _crawl_website_pages(
        self,
        *,
        company_id: int,
        agent_id: int,
        source_id: int,
        start_url: str,
        first_response: requests.Response | None,
        options: dict[str, Any],
        renderer: BrowserRenderer,
    ) -> dict[str, Any]:
        def option_int(name: str, fallback: int, minimum: int, maximum: int) -> int:
            try:
                value = int(options.get(name, fallback))
            except (TypeError, ValueError):
                value = fallback
            return min(maximum, max(minimum, value))

        def option_bool(name: str, fallback: bool) -> bool:
            value = options.get(name)
            if value is None:
                return fallback
            if isinstance(value, str):
                return value.strip().lower() not in {"0", "false", "no", "off"}
            return bool(value)

        max_pages = option_int("max_pages", int(os.getenv("WEB_CRAWL_MAX_PAGES", "25")), 1, 100)
        max_depth = option_int("max_depth", int(os.getenv("WEB_CRAWL_MAX_DEPTH", "1")), 0, 5)
        delay = max(0.0, float(os.getenv("WEB_CRAWL_DELAY_SECONDS", "0.25")))
        # Pages opened in the headless browser cost the site far more (scripts, API calls),
        # so wait longer after those to keep the crawl polite.
        browser_delay = max(delay, float(os.getenv("WEB_CRAWL_BROWSER_DELAY_SECONDS", "4")))
        restrict_to_start_path = option_bool(
            "restrict_to_start_path",
            os.getenv("WEB_CRAWL_RESTRICT_TO_START_PATH", "true").lower() != "false",
        )
        robots = self._robots_parser(start_url)
        queue: list[tuple[str, int, requests.Response | None]] = [(start_url, 0, first_response)]
        queued = {start_url}
        visited: set[str] = set()
        current_document_ids: set[str] = set()
        totals: dict[str, Any] = {
            "documents_found": 0,
            "chunks_indexed": 0,
            "skipped": [],
            "deleted_documents": 0,
            "pages_crawled": 0,
            "pages_rendered": 0,
        }

        while queue and totals["pages_crawled"] < max_pages:
            page_url, depth, prepared_response = queue.pop(0)
            if page_url in visited:
                continue
            visited.add(page_url)
            if not self._is_allowed_crawl_url(
                page_url,
                start_url=start_url,
                restrict_to_start_path=restrict_to_start_path,
                robots=robots,
            ):
                totals["skipped"].append({"url": page_url, "reason": "not_allowed"})
                continue

            page = self._fetch_crawl_page(page_url, prepared_response, renderer)
            if "skip" in page:
                totals["skipped"].append({"url": page_url, "reason": page["skip"]})
                if page["browser_used"] and browser_delay:
                    time.sleep(browser_delay)
                continue
            links, title, text = page["links"], page["title"], page["text"]
            if page["rendered"]:
                totals["pages_rendered"] += 1
            if text:
                try:
                    result = self._upsert_document(
                        company_id=company_id,
                        agent_id=agent_id,
                        source_id=source_id,
                        source_type="website",
                        document_id=page_url,
                        title=title or page_url,
                        text=text,
                    )
                    totals["documents_found"] += 1
                    totals["chunks_indexed"] += result["chunks_indexed"]
                    current_document_ids.add(page_url)
                except Exception as exc:
                    totals["skipped"].append({"url": page_url, "reason": str(exc)})

            totals["pages_crawled"] += 1
            if depth < max_depth:
                for link in links:
                    next_url = self._normalize_web_url(urljoin(page_url, link))
                    if next_url in queued or next_url in visited:
                        continue
                    if self._is_allowed_crawl_url(
                        next_url,
                        start_url=start_url,
                        restrict_to_start_path=restrict_to_start_path,
                        robots=robots,
                    ):
                        queue.append((next_url, depth + 1, None))
                        queued.add(next_url)
            pause = browser_delay if page["browser_used"] else delay
            if pause:
                time.sleep(pause)

        totals["deleted_documents"] = self._delete_missing_source_documents(
            company_id=company_id,
            agent_id=agent_id,
            source_id=source_id,
            current_document_ids=current_document_ids,
        )
        if not totals["chunks_indexed"]:
            raise RuntimeError(f"No supported text content was indexed from website source. Skipped: {totals['skipped']}")
        totals["collection"] = self.collection_name(company_id)
        return totals

    def ingest_onedrive_folder(
        self,
        *,
        company_id: int,
        agent_id: int,
        source_id: int,
        folder_ref: str,
        access_token: str,
    ) -> dict[str, Any]:
        print(f"DEBUG ingest_onedrive_folder: listing files for agent={agent_id} source={source_id}", flush=True)
        files = self._list_graph_drive_files(folder_ref, access_token)
        print(f"DEBUG ingest_onedrive_folder: listed {len(files)} file(s) for agent={agent_id} source={source_id}", flush=True)
        headers = {"Authorization": f"Bearer {access_token}"}
        return self._ingest_graph_items(
            company_id=company_id,
            agent_id=agent_id,
            source_id=source_id,
            files=files,
            headers=headers,
            source_type="onedrive",
            default_title="OneDrive document",
        )

    def _ingest_graph_items(
        self,
        *,
        company_id: int,
        agent_id: int,
        source_id: int,
        files: list[dict[str, Any]],
        headers: dict[str, str],
        source_type: str,
        default_title: str,
    ) -> dict[str, Any]:
        current_document_ids = {
            self._graph_document_id(item)
            for item in files
            if self._graph_document_id(item)
        }
        deleted_documents = self._delete_missing_source_documents(
            company_id=company_id,
            agent_id=agent_id,
            source_id=source_id,
            current_document_ids=current_document_ids,
        )
        totals = {
            "documents_found": len(files),
            "chunks_indexed": 0,
            "skipped": [],
            "deleted_documents": deleted_documents,
        }
        if not totals["documents_found"]:
            raise RuntimeError(f"No files were found in the {source_type} source.")
        for item in files:
            document_id = self._graph_document_id(item)
            if not document_id:
                totals["skipped"].append(f"unnamed_{source_type}_file")
                continue

            name = item.get("name") or item.get("id") or document_id
            try:
                download_response = self._download_graph_file(item, headers)
            except Exception as exc:
                totals["skipped"].append({"file": name, "reason": str(exc)})
                continue
            try:
                text = self._extract_file_text(
                    item.get("name", ""),
                    download_response.content,
                    download_response.headers.get("content-type", ""),
                )
            except Exception as exc:
                totals["skipped"].append({"file": name, "reason": str(exc)})
                continue
            if not text:
                totals["skipped"].append({"file": name, "reason": "unsupported_or_empty_file"})
                continue

            result = self._upsert_document(
                company_id=company_id,
                agent_id=agent_id,
                source_id=source_id,
                source_type=source_type,
                document_id=document_id,
                title=item.get("name") or default_title,
                text=text,
            )
            totals["chunks_indexed"] += result["chunks_indexed"]
        if totals["documents_found"] and not totals["chunks_indexed"]:
            raise RuntimeError(f"No supported text content was indexed from {source_type} source. Skipped: {totals['skipped']}")
        return totals

    def ingest_sharepoint_folder(
        self,
        *,
        company_id: int,
        agent_id: int,
        source_id: int,
        folder_ref: str,
        access_token: str,
    ) -> dict[str, Any]:
        site_page_url = self._resolve_sharepoint_site_page_url(folder_ref, access_token)
        if site_page_url:
            print(f"DEBUG ingest_sharepoint_folder: loading site page for agent={agent_id} source={source_id}", flush=True)
            page = self._get_sharepoint_site_page(site_page_url, access_token)
            text = self._extract_sharepoint_site_page_text(page)
            if not text:
                raise RuntimeError("No supported text content was found on the SharePoint site page.")

            document_id = str(page.get("id") or self._normalize_web_url(site_page_url)).strip()
            result = self._upsert_document(
                company_id=company_id,
                agent_id=agent_id,
                source_id=source_id,
                source_type="sharepoint",
                document_id=document_id,
                title=page.get("title") or page.get("name") or "SharePoint site page",
                text=text,
            )
            result["deleted_documents"] = self._delete_missing_source_documents(
                company_id=company_id,
                agent_id=agent_id,
                source_id=source_id,
                current_document_ids={document_id},
            )
            return result

        print(f"DEBUG ingest_sharepoint_folder: listing files for agent={agent_id} source={source_id}", flush=True)
        files = self._list_sharepoint_files(folder_ref, access_token)
        print(f"DEBUG ingest_sharepoint_folder: listed {len(files)} file(s) for agent={agent_id} source={source_id}", flush=True)
        headers = {"Authorization": f"Bearer {access_token}"}
        return self._ingest_graph_items(
            company_id=company_id,
            agent_id=agent_id,
            source_id=source_id,
            files=files,
            headers=headers,
            source_type="sharepoint",
            default_title="SharePoint document",
        )

    def _onedrive_document_id(self, item: dict[str, Any]) -> str:
        return str(item.get("id") or item.get("name") or "").strip()

    def _graph_document_id(self, item: dict[str, Any]) -> str:
        return self._onedrive_document_id(item)

    def _normalize_web_url(self, url: str) -> str:
        url, _fragment = urldefrag(str(url or "").strip())
        parsed = urlparse(url)
        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            parsed.query,
            "",
        ))

    def _robots_parser(self, start_url: str) -> RobotFileParser | None:
        parsed = urlparse(start_url)
        robots = RobotFileParser()
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        robots.set_url(robots_url)
        try:
            # Use the same HTTP client and user agent as page fetching. The
            # stdlib RobotFileParser fetcher can fail independently (for
            # example because of proxy or TLS settings), leaving the parser
            # uninitialized; can_fetch() then denies every URL.
            response = requests.get(
                robots_url,
                timeout=20,
                headers={"User-Agent": "AgentStoreIngestion/1.0"},
            )
            response.raise_for_status()
            robots.parse(response.text.lstrip("\ufeff").splitlines())
            return robots
        except Exception:
            return None

    def _is_allowed_crawl_url(
        self,
        url: str,
        *,
        start_url: str,
        restrict_to_start_path: bool,
        robots: RobotFileParser | None,
    ) -> bool:
        parsed = urlparse(url)
        start = urlparse(start_url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != start.netloc:
            return False
        if re.search(r"\.(?:png|jpe?g|gif|webp|svg|ico|mp4|mov|avi|mp3|wav|zip|rar|7z)(?:$|\?)", parsed.path, re.I):
            return False
        if re.search(r"/(?:login|logout|signup|cart|checkout|admin)(?:/|$)", parsed.path, re.I):
            return False
        if restrict_to_start_path:
            start_path = start.path or "/"
            if "." not in os.path.basename(start_path):
                prefix = start_path.rstrip("/") or "/"
                if prefix != "/" and not parsed.path.startswith(prefix):
                    return False
        if robots and not robots.can_fetch("AgentStoreIngestion/1.0", url):
            return False
        return True

    def _list_sharepoint_files(self, folder_ref: str, access_token: str) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        if folder_ref.startswith(("http://", "https://")):
            if self._is_sharepoint_view_url(folder_ref):
                url = self._sharepoint_site_drive_item_url(folder_ref, headers)
                return self._list_graph_item_files(url, headers)
            try:
                return self._list_graph_item_files(self._share_link_drive_item_url(folder_ref), headers)
            except requests.HTTPError:
                url = self._sharepoint_site_drive_item_url(folder_ref, headers)
                return self._list_graph_item_files(url, headers)

        if "|" in folder_ref:
            drive_id, item_id = [part.strip() for part in folder_ref.split("|", 1)]
            if drive_id and item_id:
                return self._list_graph_item_files(
                    f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}",
                    headers,
                )

        return self._list_graph_files_by_url(
            f"https://graph.microsoft.com/v1.0/drives/{folder_ref}/root/children",
            headers,
        )

    def _is_sharepoint_site_page_url(self, page_url: str) -> bool:
        parsed = urlparse(str(page_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc.lower().endswith(".sharepoint.com"):
            return False
        segments = self._sharepoint_path_segments(parsed)
        if not segments or not segments[-1].lower().endswith(".aspx"):
            return False
        return any(segment.replace(" ", "").lower() == "sitepages" for segment in segments[:-1])

    def _resolve_sharepoint_site_page_url(self, source_url: str, access_token: str) -> str:
        source_url = str(source_url or "").strip()
        if self._is_sharepoint_site_page_url(source_url):
            return source_url

        parsed = urlparse(source_url)
        segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
        is_opaque_url_share = (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower().endswith(".sharepoint.com")
            and bool(segments)
            and segments[0].lower() == ":u:"
        )
        if not is_opaque_url_share:
            return ""

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json;odata.metadata=none",
            "Prefer": "redeemSharingLinkIfNecessary",
        }
        response = requests.get(self._share_link_drive_item_url(source_url), headers=headers, timeout=20)
        response.raise_for_status()
        item = response.json()
        if not isinstance(item, dict):
            raise RuntimeError("Microsoft Graph returned an invalid SharePoint sharing-link response.")

        candidates = [item]
        if isinstance(item.get("remoteItem"), dict):
            candidates.append(item["remoteItem"])
        for candidate in candidates:
            web_url = str(candidate.get("webUrl") or "").strip()
            if self._is_sharepoint_site_page_url(web_url):
                return web_url
        return ""

    def _get_sharepoint_site_page(self, page_url: str, access_token: str) -> dict[str, Any]:
        parsed = urlparse(page_url)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json;odata.metadata=none",
        }
        site_id, remaining_segments = self._sharepoint_site_context(parsed, headers)
        if not remaining_segments:
            raise ValueError("SharePoint site page URL must include a page name.")

        target_name = unquote(remaining_segments[-1]).strip().lower()
        target_path = "/" + "/".join(unquote(segment) for segment in self._sharepoint_path_segments(parsed))
        pages_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/pages?$select=id,name,title,webUrl"
        page_summary = None
        seen_urls: set[str] = set()

        while pages_url and pages_url not in seen_urls:
            seen_urls.add(pages_url)
            response = requests.get(pages_url, headers=headers, timeout=20)
            response.raise_for_status()
            payload = response.json()
            for candidate in payload.get("value") or []:
                candidate_name = unquote(str(candidate.get("name") or "")).strip().lower()
                candidate_path = unquote(urlparse(str(candidate.get("webUrl") or "")).path).rstrip("/").lower()
                if candidate_name == target_name or candidate_path == target_path.rstrip("/").lower():
                    page_summary = candidate
                    break
            if page_summary:
                break
            pages_url = payload.get("@odata.nextLink")

        page_id = str((page_summary or {}).get("id") or "").strip()
        if not page_id:
            raise RuntimeError(f"Could not find SharePoint site page '{remaining_segments[-1]}'.")

        content_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/pages/{page_id}/microsoft.graph.sitePage"
        content_response = requests.get(
            content_url,
            headers=headers,
            params={"$expand": "canvasLayout"},
            timeout=20,
        )
        content_response.raise_for_status()
        page = content_response.json()
        if not isinstance(page, dict):
            raise RuntimeError("Microsoft Graph returned an invalid SharePoint site page response.")
        return page

    def _extract_sharepoint_site_page_text(self, page: dict[str, Any]) -> str:
        parts: list[str] = []
        seen: set[str] = set()

        def add(value: Any, *, html: bool = False) -> None:
            if not isinstance(value, str) or not value.strip():
                return
            text = _extract_html_text(value.encode("utf-8")) if html else clean_text(value)
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                parts.append(text)

        add(page.get("title"))
        add(page.get("description"))
        title_area = page.get("titleArea") if isinstance(page.get("titleArea"), dict) else {}
        add(title_area.get("title"))
        add(title_area.get("textAboveTitle"))

        text_property_names = {
            "alttext",
            "captiontext",
            "description",
            "label",
            "overlaytext",
            "subtitle",
            "text",
            "title",
        }

        def collect(value: Any, parent_key: str = "") -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item, parent_key)
                return
            if not isinstance(value, dict):
                if parent_key.lower() in text_property_names:
                    add(value)
                return

            inner_html = value.get("innerHtml")
            if isinstance(inner_html, str):
                add(inner_html, html=True)

            for key, child in value.items():
                normalized_key = str(key).replace("_", "").lower()
                if normalized_key == "innerhtml":
                    continue
                if normalized_key in {"htmlstrings", "searchableplaintexts"}:
                    collect_processed_content(child)
                elif normalized_key in text_property_names and isinstance(child, str):
                    add(child, html="<" in child and ">" in child)
                elif isinstance(child, (dict, list)):
                    collect(child, normalized_key)

        def collect_processed_content(value: Any) -> None:
            if isinstance(value, str):
                add(value, html="<" in value and ">" in value)
            elif isinstance(value, list):
                for item in value:
                    collect_processed_content(item)
            elif isinstance(value, dict):
                if isinstance(value.get("value"), str):
                    processed_value = value["value"]
                    add(processed_value, html="<" in processed_value and ">" in processed_value)
                else:
                    for child in value.values():
                        collect_processed_content(child)

        collect(page.get("canvasLayout") or {})
        return clean_text(" ".join(parts))

    def _share_link_drive_item_url(self, sharing_url: str) -> str:
        encoded = base64.urlsafe_b64encode(sharing_url.encode("utf-8")).decode("utf-8").rstrip("=")
        return f"https://graph.microsoft.com/v1.0/shares/u!{encoded}/driveItem"

    def _share_link_children_url(self, sharing_url: str) -> str:
        encoded = base64.urlsafe_b64encode(sharing_url.encode("utf-8")).decode("utf-8").rstrip("=")
        return f"https://graph.microsoft.com/v1.0/shares/u!{encoded}/driveItem/children"

    def _list_graph_item_files(self, url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        item = response.json()
        if isinstance(item, dict):
            item.setdefault("_graph_item_url", url)
        return self._graph_item_files(item, headers)

    def _graph_item_files(self, item: dict[str, Any], headers: dict[str, str]) -> list[dict[str, Any]]:
        remote_item = item.get("remoteItem") if isinstance(item.get("remoteItem"), dict) else None
        if remote_item:
            merged = dict(remote_item)
            for key in ("name", "webUrl", "@microsoft.graph.downloadUrl", "_graph_item_url"):
                if item.get(key) and not merged.get(key):
                    merged[key] = item[key]
            if isinstance(item.get("parentReference"), dict) and not isinstance(merged.get("parentReference"), dict):
                merged["parentReference"] = item["parentReference"]
            return self._graph_item_files(merged, headers)
        if "file" in item:
            return [item]
        if "folder" in item:
            child_url = self._graph_item_children_url(item)
            if child_url:
                return self._list_graph_files_by_url(child_url, headers)
        return []

    def _download_graph_file(self, item: dict[str, Any], headers: dict[str, str]):
        download_url = item.get("@microsoft.graph.downloadUrl")
        if download_url:
            response = requests.get(download_url, timeout=30)
        else:
            content_url = self._graph_item_content_url(item)
            if not content_url:
                raise RuntimeError("Graph file did not include a download URL or resolvable content URL.")
            response = requests.get(content_url, headers=headers, timeout=30, allow_redirects=True)
        response.raise_for_status()
        return response

    def _graph_item_content_url(self, item: dict[str, Any]) -> str:
        item_url = str(item.get("_graph_item_url") or "").strip()
        if item_url:
            return f"{item_url.rstrip('/')}/content"

        parent = item.get("parentReference") if isinstance(item.get("parentReference"), dict) else {}
        drive_id = parent.get("driveId") or item.get("driveId")
        item_id = item.get("id")
        if drive_id and item_id:
            return f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"
        if item_id:
            return f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/content"
        return ""

    def _list_graph_files_by_url(self, url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        pending = [url]
        seen_urls: set[str] = set()
        while pending:
            url = pending.pop(0)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            while url:
                response = requests.get(url, headers=headers, timeout=20)
                response.raise_for_status()
                data = response.json()
                for item in data.get("value", []):
                    if "file" in item:
                        items.append(item)
                    elif "folder" in item:
                        child_url = self._graph_item_children_url(item)
                        if child_url:
                            pending.append(child_url)
                url = data.get("@odata.nextLink")
        return items

    def _graph_item_children_url(self, item: dict[str, Any]) -> str:
        parent = item.get("parentReference") if isinstance(item.get("parentReference"), dict) else {}
        drive_id = parent.get("driveId") or item.get("driveId")
        item_id = item.get("id")
        if drive_id and item_id:
            return f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children"
        if item_id:
            return f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/children"
        return ""

    def _list_graph_drive_files(self, folder_ref: str, access_token: str) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        if folder_ref.startswith(("http://", "https://")):
            if self._is_sharepoint_view_url(folder_ref):
                url = self._sharepoint_site_drive_item_url(folder_ref, headers)
                return self._list_graph_item_files(url, headers)
            try:
                return self._list_graph_item_files(self._share_link_drive_item_url(folder_ref), headers)
            except requests.HTTPError:
                parsed = urlparse(folder_ref)
                if parsed.netloc.endswith("sharepoint.com"):
                    url = self._sharepoint_site_drive_item_url(folder_ref, headers)
                    return self._list_graph_item_files(url, headers)
                raise
        elif "|" in folder_ref:
            drive_id, item_id = [part.strip() for part in folder_ref.split("|", 1)]
            if drive_id and item_id:
                url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}"
            else:
                url = f"https://graph.microsoft.com/v1.0/me/drive/items/{folder_ref}"
        else:
            url = f"https://graph.microsoft.com/v1.0/me/drive/items/{folder_ref}"

        return self._list_graph_item_files(url, headers)

    def _sharepoint_site_drive_item_url(self, folder_url: str, headers: dict[str, str]) -> str:
        parsed = urlparse(folder_url)
        if not parsed.netloc:
            raise ValueError("SharePoint source must be a valid SharePoint URL.")

        site_id, remaining_segments = self._sharepoint_site_context(parsed, headers)

        drives_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
        drives_response = requests.get(drives_url, headers=headers, timeout=20)
        drives_response.raise_for_status()
        drives = drives_response.json().get("value") or []
        if not drives:
            raise RuntimeError("No SharePoint document libraries were found for this site.")

        drive = drives[0]
        folder_segments = remaining_segments
        if folder_segments:
            first_segment = folder_segments[0].strip().lower()
            matched_drive = next(
                (
                    item
                    for item in drives
                    if first_segment in {
                        str(item.get("name") or "").strip().lower(),
                        str(item.get("webUrl") or "").rstrip("/").split("/")[-1].replace("%20", " ").lower(),
                    }
                ),
                None,
            )
            if matched_drive:
                drive = matched_drive
                folder_segments = folder_segments[1:]
            elif first_segment in {"shared documents", "documents"}:
                folder_segments = folder_segments[1:]

        drive_id = drive.get("id")
        if not drive_id:
            raise RuntimeError("Could not resolve SharePoint document library.")

        if folder_segments:
            folder_path = "/".join(quote(segment, safe="") for segment in folder_segments)
            return f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder_path}:"
        return f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root"

    def _sharepoint_site_context(self, parsed, headers: dict[str, str]) -> tuple[str, list[str]]:
        if not parsed.netloc:
            raise ValueError("SharePoint source must be a valid SharePoint URL.")

        segments = self._sharepoint_path_segments(parsed)
        if len(segments) >= 2 and segments[0].lower() in {"sites", "teams", "personal"}:
            site_path_segments = segments[:2]
            remaining_segments = segments[2:]
        else:
            site_path_segments = []
            remaining_segments = segments

        site_path = "/" + "/".join(quote(segment, safe="") for segment in site_path_segments)
        site_url = f"https://graph.microsoft.com/v1.0/sites/{parsed.netloc}:{site_path}" if site_path_segments else f"https://graph.microsoft.com/v1.0/sites/{parsed.netloc}"
        site_response = requests.get(site_url, headers=headers, timeout=20)
        site_response.raise_for_status()
        site_id = site_response.json().get("id")
        if not site_id:
            raise RuntimeError("Could not resolve SharePoint site from the provided URL.")
        return str(site_id), remaining_segments

    def _sharepoint_path_segments(self, parsed) -> list[str]:
        query = parse_qs(parsed.query)
        linked_path = next(
            (
                values[0]
                for key in ("id", "ID", "RootFolder", "rootFolder", "rootfolder")
                for values in [query.get(key) or query.get(key.lower()) or []]
                if values
            ),
            "",
        )
        if linked_path:
            linked = urlparse(linked_path)
            path = linked.path if linked.netloc else linked_path
        else:
            path = parsed.path

        segments = [unquote(segment) for segment in path.split("/") if segment]
        if len(segments) >= 2 and re.fullmatch(r":[a-z]:", segments[0].lower()) and segments[1].lower() == "r":
            segments = segments[2:]
        if len(segments) >= 2 and segments[-2].lower() == "forms" and segments[-1].lower() in {"allitems.aspx", "onedrive.aspx"}:
            segments = segments[:-2]
        return segments

    def _is_sharepoint_view_url(self, folder_url: str) -> bool:
        parsed = urlparse(folder_url)
        segments = [segment.lower() for segment in parsed.path.split("/") if segment]
        if len(segments) < 2:
            return False
        if segments[-2] == "forms" and segments[-1] in {"allitems.aspx", "onedrive.aspx"}:
            return True
        return segments[-1] == "onedrive.aspx" and "_layouts" in segments

    def _sharepoint_site_folder_children_url(self, folder_url: str, headers: dict[str, str]) -> str:
        item_url = self._sharepoint_site_drive_item_url(folder_url, headers)
        return f"{item_url}/children"

    def _extract_file_text(self, filename: str, raw: bytes, content_type: str = "") -> str:
        lower = filename.lower()
        content_type = content_type.lower()
        if lower.endswith(".docx") or "wordprocessingml.document" in content_type:
            return _extract_docx_text(raw)
        if lower.endswith(".pdf") or "application/pdf" in content_type:
            return _extract_pdf_text(raw)
        if lower.endswith((".xlsx", ".xlsm")) or "spreadsheetml.sheet" in content_type or "macroenabled.12" in content_type:
            return _extract_xlsx_text(raw)
        if lower.endswith((".html", ".htm")) or "html" in content_type:
            return _extract_html_text(raw)
        if (
            lower.endswith((".txt", ".md", ".csv", ".json"))
            or content_type.startswith("text/")
            or "application/json" in content_type
            or "text/csv" in content_type
        ):
            return clean_text(raw.decode("utf-8", errors="ignore"))
        return clean_text(raw.decode("utf-8", errors="ignore")) if not lower and not content_type else ""

    def _source_document_ids(self, collection: str, *, agent_id: int, source_id: int) -> set[str]:
        document_ids: set[str] = set()
        offset = None
        source_filter = qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="agent_id",
                    match=qdrant_models.MatchValue(value=agent_id),
                ),
                qdrant_models.FieldCondition(
                    key="source_id",
                    match=qdrant_models.MatchValue(value=source_id),
                ),
            ],
        )

        while True:
            points, offset = self.client.scroll(
                collection_name=collection,
                scroll_filter=source_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                document_id = str(payload.get("document_id") or "").strip()
                if document_id:
                    document_ids.add(document_id)
            if offset is None:
                break

        return document_ids

    def _delete_missing_source_documents(
        self,
        *,
        company_id: int,
        agent_id: int,
        source_id: int,
        current_document_ids: set[str],
    ) -> int:
        collection = self.ensure_company_collection(company_id)
        existing_document_ids = self._source_document_ids(
            collection,
            agent_id=agent_id,
            source_id=source_id,
        )
        removed_document_ids = existing_document_ids - current_document_ids

        for document_id in removed_document_ids:
            self._delete_points(
                collection,
                [
                    qdrant_models.FieldCondition(
                        key="agent_id",
                        match=qdrant_models.MatchValue(value=agent_id),
                    ),
                    qdrant_models.FieldCondition(
                        key="source_id",
                        match=qdrant_models.MatchValue(value=source_id),
                    ),
                    qdrant_models.FieldCondition(
                        key="document_id",
                        match=qdrant_models.MatchValue(value=document_id),
                    ),
                ],
            )

        return len(removed_document_ids)

    def _upsert_document(
        self,
        *,
        company_id: int,
        agent_id: int,
        source_id: int,
        source_type: str,
        document_id: str,
        title: str,
        text: str,
    ) -> dict[str, Any]:
        print(f"DEBUG kb_upsert: preparing document agent={agent_id} source={source_id} document={document_id}", flush=True)
        collection = self.ensure_company_collection(company_id)
        chunks = _chunk_text(text)
        if not chunks:
            raise ValueError("Document contains no indexable text.")

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for knowledge-base embeddings.")

        print(f"DEBUG kb_upsert: embedding {len(chunks)} chunk(s) agent={agent_id} source={source_id} document={document_id}", flush=True)
        embeddings = openai_service.create_embeddings(
            api_key=api_key,
            model=self.embedding_model,
            inputs=chunks,
            dimensions=self.vector_size,
        )
        points = []
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self._delete_points(
            collection,
            [
                qdrant_models.FieldCondition(
                    key="agent_id",
                    match=qdrant_models.MatchValue(value=agent_id),
                ),
                qdrant_models.FieldCondition(
                    key="source_id",
                    match=qdrant_models.MatchValue(value=source_id),
                ),
                qdrant_models.FieldCondition(
                    key="document_id",
                    match=qdrant_models.MatchValue(value=document_id),
                ),
            ],
        )
        for index, chunk in enumerate(chunks):
            points.append(
                qdrant_models.PointStruct(
                    id=_point_id(company_id, agent_id, source_id, document_id, index),
                    vector=embeddings[index],
                    payload={
                        "company_id": company_id,
                        "agent_id": agent_id,
                        "source_id": source_id,
                        "source_type": source_type,
                        "document_id": document_id,
                        "document_title": title,
                        "chunk_index": index,
                        "content_hash": content_hash,
                        "embedding_model": self.embedding_model,
                        "embedding_dimensions": self.vector_size,
                        "text": chunk,
                    },
                )
            )
        if points:
            print(f"DEBUG kb_upsert: upserting {len(points)} point(s) into {collection} agent={agent_id} source={source_id} document={document_id}", flush=True)
            self.client.upsert(collection_name=collection, points=points)
            print(f"DEBUG kb_upsert: upsert complete agent={agent_id} source={source_id} document={document_id}", flush=True)
        return {"documents_found": 1, "chunks_indexed": len(points), "collection": collection}


ingestion_service = IngestionService()
