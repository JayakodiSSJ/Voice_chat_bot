import os
import unittest
from unittest.mock import patch

from model_router import ModelRouter


class ModelRouterTests(unittest.TestCase):
    def test_small_model_defaults_to_supported_model(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ModelRouter().small_model(), "gpt-4o-mini")

    def test_deprecated_model_configuration_uses_replacement(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_SMALL_MODEL": "gpt-4.1-nano",
                "OPENAI_DEFAULT_MODEL": "gpt-4.1-nano",
            },
            clear=True,
        ):
            router = ModelRouter()
            self.assertEqual(router.small_model(), "gpt-4o-mini")
            self.assertEqual(router.default_model(), "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
