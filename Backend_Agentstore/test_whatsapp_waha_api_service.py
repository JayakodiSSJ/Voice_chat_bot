import unittest
from unittest.mock import Mock, patch

import requests

from whatsapp_waha_api_service import WhatsAppWahaAPIError, WhatsAppWahaAPIService


class WhatsAppWahaAPIServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = WhatsAppWahaAPIService()

    @patch("whatsapp_waha_api_service.requests.post")
    def test_sends_text_through_waha(self, post):
        response = Mock(status_code=201)
        response.json.return_value = {"id": "true_94770000000@c.us_OUT"}
        post.return_value = response

        message_id = self.service.send_text(
            base_url="http://waha:3000/",
            session="workspace-7",
            api_key="secret-key",
            recipient_phone="+94 77 000 0000",
            message_text="Hello from the agent",
        )

        self.assertEqual(message_id, "true_94770000000@c.us_OUT")
        post.assert_called_once_with(
            "http://waha:3000/api/sendText",
            headers={
                "X-Api-Key": "secret-key",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "session": "workspace-7",
                "chatId": "94770000000@c.us",
                "text": "Hello from the agent",
            },
            timeout=20,
        )

    @patch("whatsapp_waha_api_service.requests.post")
    def test_classifies_waha_failures(self, post):
        response = Mock(status_code=503)
        response.json.return_value = {"message": "Session unavailable"}
        post.return_value = response

        with self.assertRaises(WhatsAppWahaAPIError) as raised:
            self.service.send_text(
                base_url="http://waha:3000",
                session="default",
                api_key="key",
                recipient_phone="94770000000",
                message_text="Hello",
            )
        self.assertTrue(raised.exception.retryable)

        post.side_effect = requests.Timeout("timed out")
        with self.assertRaises(WhatsAppWahaAPIError) as raised:
            self.service.send_text(
                base_url="http://waha:3000",
                session="default",
                api_key="key",
                recipient_phone="94770000000",
                message_text="Hello",
            )
        self.assertTrue(raised.exception.retryable)

    def test_rejects_invalid_base_urls(self):
        for value in ("", "waha:3000", "file:///tmp/waha", "http://user:pass@waha:3000"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.service.send_text(
                        base_url=value,
                        session="default",
                        api_key="key",
                        recipient_phone="94770000000",
                        message_text="Hello",
                    )


if __name__ == "__main__":
    unittest.main()
