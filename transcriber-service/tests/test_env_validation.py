import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import validate_required_env  # noqa: E402


class ValidateRequiredEnvTests(unittest.TestCase):
    """Startup env-checks для transcriber-service."""

    def test_missing_hf_token_fails(self):
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "",
                "SPEAKER_LLM_REFINEMENT_ENABLED": "false",
                "TRANSCRIPT_LLM_REFINEMENT_ENABLED": "false",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                validate_required_env()
        self.assertIn("HF_TOKEN", str(ctx.exception))

    def test_llm_enabled_without_anthropic_key_fails(self):
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "hf_test",
                "TRANSCRIPT_LLM_REFINEMENT_ENABLED": "true",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                validate_required_env()
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_speaker_llm_enabled_without_anthropic_key_fails(self):
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "hf_test",
                "SPEAKER_LLM_REFINEMENT_ENABLED": "true",
                "TRANSCRIPT_LLM_REFINEMENT_ENABLED": "false",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                validate_required_env()

    def test_llm_disabled_allows_missing_anthropic_key(self):
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "hf_test",
                "SPEAKER_LLM_REFINEMENT_ENABLED": "false",
                "TRANSCRIPT_LLM_REFINEMENT_ENABLED": "false",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            validate_required_env()

    def test_all_set_passes(self):
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "hf_test",
                "SPEAKER_LLM_REFINEMENT_ENABLED": "true",
                "TRANSCRIPT_LLM_REFINEMENT_ENABLED": "true",
                "ANTHROPIC_API_KEY": "sk-test",
            },
            clear=False,
        ):
            validate_required_env()


if __name__ == "__main__":
    unittest.main()
