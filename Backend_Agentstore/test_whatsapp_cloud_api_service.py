import unittest
from unittest.mock import Mock, patch

import requests

from whatsapp_cloud_api_service import (
    WhatsAppCloudAPIError,
    WhatsAppCloudAPIService,
    split_whatsapp_text,
)


class WhatsAppCloudAPIServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = WhatsAppCloudAPIService()

    @patch("whatsapp_cloud_api_service.requests.post")
    def test_sends_text_through_the_phone_number_messages_endpoint(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"messages": [{"id": "wamid.outbound"}]}
        post.return_value = response

        message_id = self.service.send_text(
            phone_number_id="123456789012345",
            access_token="secret-token",
            recipient_phone="+94 77 000 0000",
            message_text="Hello from the agent",
            graph_api_version="v26.0",
        )

        self.assertEqual(message_id, "wamid.outbound")
        post.assert_called_once_with(
            "https://graph.facebook.com/v26.0/123456789012345/messages",
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": "94770000000",
                "type": "text",
                "text": {"preview_url": False, "body": "Hello from the agent"},
            },
            timeout=20,
        )

    @patch("whatsapp_cloud_api_service.requests.post")
    def test_classifies_transient_and_permanent_meta_errors(self, post):
        response = Mock(status_code=503)
        response.json.return_value = {"error": {"message": "Unavailable", "code": 2}}
        post.return_value = response

        with self.assertRaises(WhatsAppCloudAPIError) as raised:
            self.service.send_text(
                phone_number_id="12345",
                access_token="token",
                recipient_phone="94770000000",
                message_text="Hello",
            )
        self.assertTrue(raised.exception.retryable)

        response.status_code = 400
        with self.assertRaises(WhatsAppCloudAPIError) as raised:
            self.service.send_text(
                phone_number_id="12345",
                access_token="token",
                recipient_phone="94770000000",
                message_text="Hello",
            )
        self.assertFalse(raised.exception.retryable)

    @patch("whatsapp_cloud_api_service.requests.post")
    def test_network_errors_are_retryable(self, post):
        post.side_effect = requests.Timeout("timed out")

        with self.assertRaises(WhatsAppCloudAPIError) as raised:
            self.service.send_text(
                phone_number_id="12345",
                access_token="token",
                recipient_phone="94770000000",
                message_text="Hello",
            )
        self.assertTrue(raised.exception.retryable)

    def test_splits_long_messages_without_losing_text(self):
        chunks = split_whatsapp_text(("word " * 2000).strip(), limit=300)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 300 for chunk in chunks))
        self.assertEqual(" ".join(chunks), ("word " * 2000).strip())


if __name__ == "__main__":
    unittest.main()
