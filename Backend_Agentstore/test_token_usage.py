import unittest
from unittest.mock import Mock, patch

from openai_service import openai_service
from subscription_packages import normalize_package
from token_usage import capture_token_usage, record_token_usage


class TokenUsageCollectorTests(unittest.TestCase):
    def test_collects_all_successful_openai_calls_in_a_chat_scope(self):
        text_response = Mock()
        text_response.ok = True
        text_response.json.return_value = {
            "id": "resp_123",
            "model": "gpt-test",
            "output_text": "Hello",
            "usage": {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
        }
        embedding_response = Mock()
        embedding_response.ok = True
        embedding_response.json.return_value = {
            "model": "embedding-test",
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 3, "total_tokens": 3},
        }

        with patch("openai_service.requests.post", side_effect=[text_response, embedding_response]):
            with capture_token_usage() as collector:
                answer = openai_service.call_text(
                    api_key="test-key",
                    model="gpt-test",
                    instructions="Reply briefly",
                    input_messages=[{"role": "user", "content": "Hi"}],
                    max_output_tokens=20,
                )
                vectors = openai_service.create_embeddings(
                    api_key="test-key",
                    model="embedding-test",
                    inputs=["query"],
                )

        self.assertEqual(answer, "Hello")
        self.assertEqual(vectors, [[0.1, 0.2]])
        self.assertEqual(
            collector.summary(),
            {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20},
        )
        self.assertEqual(len(collector.events), 2)

    def test_calls_outside_a_capture_scope_are_not_collected(self):
        record_token_usage(
            response_id="resp_outside",
            model="gpt-test",
            usage={"total_tokens": 99},
        )
        with capture_token_usage() as collector:
            pass
        self.assertEqual(collector.summary()["total_tokens"], 0)


class SubscriptionPackageTokenMigrationTests(unittest.TestCase):
    def test_legacy_message_limit_is_converted_to_tokens(self):
        package = normalize_package("starter", {"monthly_messages": 2500})
        self.assertEqual(package["monthly_tokens"], 2_500_000)
        self.assertNotIn("monthly_messages", package)

    def test_explicit_token_limit_wins_over_legacy_value(self):
        package = normalize_package(
            "starter",
            {"monthly_messages": 2500, "monthly_tokens": 750_000},
        )
        self.assertEqual(package["monthly_tokens"], 750_000)


if __name__ == "__main__":
    unittest.main()
