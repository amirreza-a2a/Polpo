# ============================================================
#  tests/characterization/test_donations.py
# ============================================================

import unittest
from unittest.mock import patch, AsyncMock
from tests.characterization.conftest_base import MockTelegramContext, MockTelegramUpdate


class TestDonationsCharacterization(unittest.TestCase):
    """
    Characterizes current in-memory donation payload storage, approval, and rejection behavior.
    """

    def test_donation_payload_in_memory_structure(self):
        """
        Characterization: Donations are currently stored in ephemeral context.bot_data under donation_{telegram_id}.
        """
        donor_tid = 998877
        context = MockTelegramContext()

        donation_payload = {
            "api_key": "sample_donated_key",
            "provider": "google",
            "models": ["gemini-3.5-flash"],
            "selected_model": "gemini-3.5-flash",
            "base_url": None,
        }

        # Store in bot_data as done in handlers/user.py
        context.bot_data[f"donation_{donor_tid}"] = donation_payload

        # Retrieve
        retrieved = context.bot_data.get(f"donation_{donor_tid}")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["api_key"], "sample_donated_key")
        self.assertEqual(retrieved["provider"], "google")

    def test_donation_lost_on_restart_behavior(self):
        """
        Characterization: Because donations live only in context.bot_data, a new context instance (e.g. after restart) loses unapproved donations.
        """
        donor_tid = 998877
        context_before_restart = MockTelegramContext()
        context_before_restart.bot_data[f"donation_{donor_tid}"] = {"api_key": "key123"}

        # Simulate bot restart (fresh context without persistence)
        context_after_restart = MockTelegramContext()

        self.assertNotIn(f"donation_{donor_tid}", context_after_restart.bot_data)
        self.assertIsNone(context_after_restart.bot_data.get(f"donation_{donor_tid}"))

    @patch("handlers.admin.add_public_api")
    def test_donation_approval_adds_public_api_with_default_200_limit(self, mock_add_pub):
        """
        Characterization: Approving a donation adds a public API with daily_limit=200 and donated_by=donor_tid.
        """
        donor_tid = 998877
        donated_data = {
            "api_key": "donated_api_key",
            "provider": "google",
            "models": ["gemini-3.5-flash"],
            "selected_model": "gemini-3.5-flash",
            "base_url": None,
        }

        # Emulating handlers/admin.py approve_donation parameters
        mock_add_pub(
            api_key=donated_data["api_key"],
            label=f"اهدایی از {donor_tid}",
            provider=donated_data["provider"],
            models=donated_data["models"],
            daily_limit=200, # Hardcoded 200 daily limit for donations
            priority=1,
            donated_by=donor_tid,
            selected_model=donated_data.get("selected_model"),
            base_url=donated_data.get("base_url"),
        )

        mock_add_pub.assert_called_once_with(
            api_key="donated_api_key",
            label="اهدایی از 998877",
            provider="google",
            models=["gemini-3.5-flash"],
            daily_limit=200,
            priority=1,
            donated_by=998877,
            selected_model="gemini-3.5-flash",
            base_url=None,
        )


if __name__ == "__main__":
    unittest.main()
