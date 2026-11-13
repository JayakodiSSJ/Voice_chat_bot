from types import SimpleNamespace
import os
import unittest
from unittest.mock import Mock, patch

from ingestion_service import IngestionService
from retrieval_service import RetrievalService


class EnsureCompanyCollectionTests(unittest.TestCase):
    @patch("ingestion_service.QdrantClient")
    def test_allows_concurrent_collection_creation(self, qdrant_client):
        client = qdrant_client.return_value
        client.get_collections.side_effect = [
            SimpleNamespace(collections=[]),
            SimpleNamespace(collections=[SimpleNamespace(name="company_7_kb")]),
        ]
        client.create_collection.side_effect = RuntimeError("Collection already exists")

        service = IngestionService()

        self.assertEqual(service.ensure_company_collection(7), "company_7_kb")
        client.create_payload_index.assert_called_once()


class RobotsParserTests(unittest.TestCase):
    @patch("ingestion_service.requests.get")
    @patch("ingestion_service.QdrantClient")
    def test_uses_requests_to_load_and_parse_robots_rules(self, _qdrant_client, get):
        get.return_value = Mock(
            text="\ufeffUser-agent: *\nDisallow: /private/\n",
            raise_for_status=Mock(),
        )
        service = IngestionService()

        robots = service._robots_parser("https://example.com/article")

        self.assertIsNotNone(robots)
        self.assertTrue(robots.can_fetch("AgentStoreIngestion/1.0", "https://example.com/article"))
        self.assertFalse(robots.can_fetch("AgentStoreIngestion/1.0", "https://example.com/private/data"))
        get.assert_called_once_with(
            "https://example.com/robots.txt",
            timeout=20,
            headers={"User-Agent": "AgentStoreIngestion/1.0"},
        )


class SharePointSitePageTests(unittest.TestCase):
    PAGE_URL = (
        "https://sltdigitallab.sharepoint.com/:u:/r/sites/SriLankaTelecomPLC/"
        "SitePages/SharePoint-Tool-Test-Page.aspx?web=2"
    )
    SHORT_PAGE_URL = (
        "https://sltdigitallab.sharepoint.com/:u:/s/SriLankaTelecomPLC/"
        "IQC8dzhxy6bdSr4T9Dv6HSLoAegQj602iNh2-dotWzS8lfk?e=FwFpls"
    )

    def setUp(self):
        self.service = IngestionService.__new__(IngestionService)

    def test_recognizes_site_page_links_without_treating_documents_as_pages(self):
        self.assertTrue(self.service._is_sharepoint_site_page_url(self.PAGE_URL))
        self.assertFalse(
            self.service._is_sharepoint_site_page_url(
                "https://sltdigitallab.sharepoint.com/:b:/r/sites/SriLankaTelecomPLC/Shared%20Documents/Test.pdf"
            )
        )

    @patch("ingestion_service.requests.get")
    def test_resolves_opaque_url_share_to_canonical_site_page_url(self, get):
        canonical_url = (
            "https://sltdigitallab.sharepoint.com/sites/SriLankaTelecomPLC/"
            "SitePages/SharePoint-Tool-Test-Page.aspx"
        )
        response = Mock()
        response.json.return_value = {
            "id": "drive-item-id",
            "name": "SharePoint-Tool-Test-Page.aspx",
            "webUrl": canonical_url,
            "file": {},
        }
        get.return_value = response

        resolved = self.service._resolve_sharepoint_site_page_url(self.SHORT_PAGE_URL, "graph-token")

        self.assertEqual(resolved, canonical_url)
        get.assert_called_once()
        self.assertIn("/shares/u!", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["headers"]["Prefer"], "redeemSharingLinkIfNecessary")

    @patch("ingestion_service.requests.get")
    def test_does_not_resolve_normal_document_link_as_site_page(self, get):
        document_url = (
            "https://sltdigitallab.sharepoint.com/:b:/r/sites/SriLankaTelecomPLC/"
            "Shared%20Documents/Test.pdf"
        )

        resolved = self.service._resolve_sharepoint_site_page_url(document_url, "graph-token")

        self.assertEqual(resolved, "")
        get.assert_not_called()

    @patch("ingestion_service.requests.get")
    def test_resolves_site_page_and_requests_expanded_canvas(self, get):
        site_response = Mock()
        site_response.json.return_value = {"id": "site-id"}
        pages_response = Mock()
        pages_response.json.return_value = {
            "value": [
                {
                    "id": "page-id",
                    "name": "SharePoint-Tool-Test-Page.aspx",
                    "title": "SharePoint Tool Test Page",
                    "webUrl": (
                        "https://sltdigitallab.sharepoint.com/sites/SriLankaTelecomPLC/"
                        "SitePages/SharePoint-Tool-Test-Page.aspx"
                    ),
                }
            ]
        }
        content_response = Mock()
        content_response.json.return_value = {
            "id": "page-id",
            "name": "SharePoint-Tool-Test-Page.aspx",
            "title": "SharePoint Tool Test Page",
            "canvasLayout": {},
        }
        get.side_effect = [site_response, pages_response, content_response]

        page = self.service._get_sharepoint_site_page(self.PAGE_URL, "graph-token")

        self.assertEqual(page["id"], "page-id")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(
            get.call_args_list[0].args[0],
            "https://graph.microsoft.com/v1.0/sites/sltdigitallab.sharepoint.com:/sites/SriLankaTelecomPLC",
        )
        self.assertEqual(
            get.call_args_list[1].args[0],
            "https://graph.microsoft.com/v1.0/sites/site-id/pages?$select=id,name,title,webUrl",
        )
        self.assertEqual(get.call_args_list[2].kwargs["params"], {"$expand": "canvasLayout"})

    def test_extracts_text_and_standard_web_part_content(self):
        page = {
            "title": "SharePoint Tool Test Page",
            "description": "Knowledge base validation page",
            "canvasLayout": {
                "horizontalSections": [
                    {
                        "columns": [
                            {
                                "webparts": [
                                    {
                                        "@odata.type": "#microsoft.graph.textWebPart",
                                        "innerHtml": "<h2>Project Marigold</h2><p>The launch code is SP-KB-92741.</p>",
                                    },
                                    {
                                        "@odata.type": "#microsoft.graph.standardWebPart",
                                        "data": {
                                            "properties": {"overlayText": "Important announcement"},
                                            "serverProcessedContent": {
                                                "searchablePlainTexts": [
                                                    {"key": "items[0].title", "value": "Employee handbook"}
                                                ]
                                            },
                                        },
                                    },
                                ]
                            }
                        ]
                    }
                ]
            },
        }

        text = self.service._extract_sharepoint_site_page_text(page)

        self.assertIn("SharePoint Tool Test Page", text)
        self.assertIn("Project Marigold", text)
        self.assertIn("SP-KB-92741", text)
        self.assertIn("Important announcement", text)
        self.assertIn("Employee handbook", text)

    def test_site_page_ingestion_uses_page_content_instead_of_drive_download(self):
        page = {"id": "page-id", "name": "Test.aspx", "title": "Test Page"}
        self.service._get_sharepoint_site_page = Mock(return_value=page)
        self.service._extract_sharepoint_site_page_text = Mock(return_value="Page body")
        self.service._upsert_document = Mock(
            return_value={"documents_found": 1, "chunks_indexed": 1, "collection": "company_4_kb"}
        )
        self.service._delete_missing_source_documents = Mock(return_value=0)
        self.service._list_sharepoint_files = Mock()

        result = self.service.ingest_sharepoint_folder(
            company_id=4,
            agent_id=66,
            source_id=12,
            folder_ref=self.PAGE_URL,
            access_token="graph-token",
        )

        self.assertEqual(result["chunks_indexed"], 1)
        self.service._list_sharepoint_files.assert_not_called()
        self.service._upsert_document.assert_called_once_with(
            company_id=4,
            agent_id=66,
            source_id=12,
            source_type="sharepoint",
            document_id="page-id",
            title="Test Page",
            text="Page body",
        )


class RetrievalSimilarityThresholdTests(unittest.TestCase):
    @patch.dict(os.environ, {"KB_MIN_SIMILARITY_SCORE": "0.60"})
    @patch("retrieval_service.openai_service.create_embeddings", return_value=[[0.0]])
    @patch("retrieval_service.QdrantClient")
    def test_excludes_chunks_below_similarity_threshold(self, qdrant_client, _embeddings):
        qdrant_client.return_value.search.return_value = [
            SimpleNamespace(score=0.78, payload={"text": "relevant"}),
            SimpleNamespace(score=0.59, payload={"text": "irrelevant"}),
            SimpleNamespace(payload={"text": "unscored"}),
        ]
        service = RetrievalService()

        points = service.search_points(
            company_id=7,
            agent_id=9,
            question="What is reinforcement learning?",
        )

        self.assertEqual([point.payload["text"] for point in points], ["relevant"])


if __name__ == "__main__":
    unittest.main()
