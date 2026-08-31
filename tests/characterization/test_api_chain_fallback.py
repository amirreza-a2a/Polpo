# ============================================================
#  tests/characterization/test_api_chain_fallback.py
# ============================================================

import unittest
from unittest.mock import patch
import tests.characterization.conftest_base
from services.api_manager import build_api_chain, switch_to_next_api, get_current_api


class TestApiChainFallbackCharacterization(unittest.TestCase):
    """
    Characterizes current API chain construction, priority ordering, and fallback switching.
    """

    @patch("services.api_manager.get_user_private_apis")
    @patch("services.api_manager.get_all_available_public_apis")
    def test_private_only_chain_construction(self, mock_get_public, mock_get_private):
        """
        Characterization: include_private=True, include_public=False builds chain containing only private APIs.
        """
        mock_get_private.return_value = [
            {"id": 10, "provider": "google", "api_key": "priv_key_1", "supported_models": ["gemini-3.5-flash"], "selected_model": "gemini-3.5-flash", "base_url": None, "label": "Key 1"},
            {"id": 11, "provider": "openai", "api_key": "priv_key_2", "supported_models": ["gpt-4o"], "selected_model": "gpt-4o", "base_url": None, "label": "Key 2"},
        ]
        mock_get_public.return_value = [
            {"id": 20, "provider": "google", "api_key": "pub_key", "supported_models": ["gemini-3.5-flash"], "selected_model": "gemini-3.5-flash", "base_url": None, "label": "Public"},
        ]

        chain = build_api_chain(user_id=1, include_private=True, include_public=False)

        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["type"], "private")
        self.assertEqual(chain[0]["id"], 10)
        self.assertEqual(chain[1]["type"], "private")
        self.assertEqual(chain[1]["id"], 11)

    @patch("services.api_manager.get_user_private_apis")
    @patch("services.api_manager.get_all_available_public_apis")
    def test_private_plus_public_fallback_chain_construction(self, mock_get_public, mock_get_private):
        """
        Characterization: include_private=True, include_public=True appends public APIs after private APIs.
        """
        mock_get_private.return_value = [
            {"id": 10, "provider": "google", "api_key": "priv_key", "supported_models": ["gemini-3.5-flash"], "selected_model": "gemini-3.5-flash", "base_url": None, "label": "Private 1"},
        ]
        mock_get_public.return_value = [
            {"id": 20, "provider": "openai", "api_key": "pub_key_1", "supported_models": ["gpt-4o"], "selected_model": "gpt-4o", "base_url": None, "label": "Public 1"},
            {"id": 21, "provider": "google", "api_key": "pub_key_2", "supported_models": ["gemini-3.5-flash"], "selected_model": "gemini-3.5-flash", "base_url": None, "label": "Public 2"},
        ]

        chain = build_api_chain(user_id=1, include_private=True, include_public=True)

        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[0]["type"], "private")
        self.assertEqual(chain[0]["id"], 10)
        self.assertEqual(chain[1]["type"], "public")
        self.assertEqual(chain[1]["id"], 20)
        self.assertEqual(chain[2]["type"], "public")
        self.assertEqual(chain[2]["id"], 21)

    @patch("services.api_manager.get_public_api_by_id")
    def test_switch_to_next_api_success_and_log_mutation(self, mock_get_public):
        """
        Characterization: switch_to_next_api switches index, logs switch transition, and returns next entry.
        """
        mock_get_public.return_value = {
            "id": 2, "api_key": "pub_key_updated", "is_active": 1,
            "pages_used_today": 10, "daily_page_limit": 200
        }

        job = {
            "id": 101,
            "current_api_index": 0,
            "api_chain": [
                {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "model": "m1"},
                {"type": "public", "id": 2, "provider": "google", "api_key": "k2", "model": "m2"},
            ],
            "api_switch_log": []
        }

        next_api = switch_to_next_api(job, reason="Rate limit reached", at_page=3)

        self.assertIsNotNone(next_api)
        self.assertEqual(next_api["id"], 2)
        self.assertEqual(job["current_api_index"], 1)
        self.assertEqual(len(job["api_switch_log"]), 1)
        self.assertEqual(job["api_switch_log"][0]["from"], "private_1")
        self.assertEqual(job["api_switch_log"][0]["to"], "public_2")
        self.assertEqual(job["api_switch_log"][0]["switched_at_page"], 3)
        self.assertEqual(job["api_switch_log"][0]["reason"], "Rate limit reached")

    @patch("services.api_manager.get_public_api_by_id")
    def test_switch_to_next_api_skips_exhausted_public_slots(self, mock_get_public):
        """
        Characterization: switch_to_next_api skips public slots that are exhausted or inactive.
        """
        def side_effect(api_id):
            if api_id == 2:
                return {"id": 2, "api_key": "k2", "is_active": 1, "pages_used_today": 200, "daily_page_limit": 200}
            elif api_id == 3:
                return {"id": 3, "api_key": "k3", "is_active": 1, "pages_used_today": 50, "daily_page_limit": 200}
            return None

        mock_get_public.side_effect = side_effect

        job = {
            "id": 101,
            "current_api_index": 0,
            "api_chain": [
                {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "model": "m1"},
                {"type": "public", "id": 2, "provider": "google", "api_key": "k2", "model": "m2"},
                {"type": "public", "id": 3, "provider": "google", "api_key": "k3", "model": "m3"},
            ],
            "api_switch_log": []
        }

        next_api = switch_to_next_api(job, reason="Quota error", at_page=2)

        self.assertIsNotNone(next_api)
        self.assertEqual(next_api["id"], 3)
        self.assertEqual(job["current_api_index"], 2)
        self.assertEqual(job["api_switch_log"][-1]["to"], "public_3")

    def test_switch_to_next_api_returns_none_when_chain_exhausted(self):
        """
        Characterization: switch_to_next_api returns None when no more slots exist.
        """
        job = {
            "id": 101,
            "current_api_index": 0,
            "api_chain": [
                {"type": "private", "id": 1, "provider": "google", "api_key": "k1", "model": "m1"}
            ],
            "api_switch_log": []
        }

        next_api = switch_to_next_api(job, reason="Key invalid", at_page=1)

        self.assertIsNone(next_api)
        self.assertEqual(len(job["api_switch_log"]), 1)
        self.assertNotIn("to", job["api_switch_log"][0])


if __name__ == "__main__":
    unittest.main()
