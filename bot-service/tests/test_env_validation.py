import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEMOST_SERVICE_API_KEY", "supersecret-test-only")

from app.main import validate_required_env  # noqa: E402


class ValidateRequiredEnvTests(unittest.TestCase):
    """Startup env-checks для bot-service (TELEMOST_SERVICE_API_KEY
    проверяется отдельно в test_service_refuses_to_start_without_api_key)."""

    def test_metadata_llm_enabled_without_anthropic_key_fails(self):
        with patch.dict(
            os.environ,
            {
                "MEETING_METADATA_LLM_ENABLED": "true",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                validate_required_env()
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_metadata_llm_disabled_allows_missing_anthropic_key(self):
        with patch.dict(
            os.environ,
            {
                "MEETING_METADATA_LLM_ENABLED": "false",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            validate_required_env()

    def test_metadata_llm_enabled_with_anthropic_key_passes(self):
        with patch.dict(
            os.environ,
            {
                "MEETING_METADATA_LLM_ENABLED": "true",
                "ANTHROPIC_API_KEY": "sk-test",
            },
            clear=False,
        ):
            validate_required_env()


if __name__ == "__main__":
    unittest.main()
