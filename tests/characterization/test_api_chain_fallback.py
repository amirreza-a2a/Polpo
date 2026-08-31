# ============================================================
#  tests/characterization/test_api_chain_fallback.py
# ============================================================

import unittest
from unittest.mock import MagicMock
from core.ai.types import ApiSlot
from core.policies.fallback_policy import FallbackChainPolicy


class TestApiChainFallbackCharacterization(unittest.TestCase):
    """
    Characterizes API chain construction, priority ordering, and fallback switching.
    """

    def test_private_only_chain_construction(self):
        """
        Characterization: list_by_user with include_public=False returns only private APIs.
        """
        mock_uow_factory = MagicMock()
        mock_uow = MagicMock()
        mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

        slot1 = ApiSlot(id=10, provider="google", api_key="k1", label="Key 1", slot_type="private", selected_model="gemini-3.5-flash")
        slot2 = ApiSlot(id=11, provider="openai", api_key="k2", label="Key 2", slot_type="private", selected_model="gpt-4o")
        mock_uow.apis.list_by_user.return_value = [slot1, slot2]

        with mock_uow_factory.create() as uow:
            chain = uow.apis.list_by_user(user_id=1, include_public=False)

        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0].slot_type, "private")
        self.assertEqual(chain[0].id, 10)
        self.assertEqual(chain[1].slot_type, "private")
        self.assertEqual(chain[1].id, 11)

    def test_private_plus_public_fallback_chain_construction(self):
        """
        Characterization: list_by_user with include_public=True appends public APIs after private APIs.
        """
        mock_uow_factory = MagicMock()
        mock_uow = MagicMock()
        mock_uow_factory.create.return_value.__enter__.return_value = mock_uow

        slot_priv = ApiSlot(id=10, provider="google", api_key="k1", label="Private 1", slot_type="private", selected_model="gemini-3.5-flash")
        slot_pub1 = ApiSlot(id=20, provider="openai", api_key="k2", label="Public 1", slot_type="public", selected_model="gpt-4o")
        slot_pub2 = ApiSlot(id=21, provider="google", api_key="k3", label="Public 2", slot_type="public", selected_model="gemini-3.5-flash")

        mock_uow.apis.list_by_user.return_value = [slot_priv, slot_pub1, slot_pub2]

        with mock_uow_factory.create() as uow:
            chain = uow.apis.list_by_user(user_id=1, include_public=True)

        self.assertEqual(len(chain), 3)
        self.assertEqual(chain[0].slot_type, "private")
        self.assertEqual(chain[0].id, 10)
        self.assertEqual(chain[1].slot_type, "public")
        self.assertEqual(chain[1].id, 20)
        self.assertEqual(chain[2].slot_type, "public")
        self.assertEqual(chain[2].id, 21)

    def test_switch_to_next_api_success_and_log_mutation(self):
        """
        Characterization: advance_chain advances index, creates switch event, and returns next slot.
        """
        slot1 = ApiSlot(id=1, provider="google", api_key="k1", label="Key 1", slot_type="private", selected_model="m1")
        slot2 = ApiSlot(id=2, provider="google", api_key="k2", label="Key 2", slot_type="public", selected_model="m2")
        chain = [slot1, slot2]

        next_slot, next_idx, event = FallbackChainPolicy.advance_chain(
            chain=chain, current_index=0, reason="Rate limit reached", at_page=3
        )

        self.assertIsNotNone(next_slot)
        self.assertEqual(next_slot.id, 2)
        self.assertEqual(next_idx, 1)
        self.assertEqual(event["from_api"], "Key 1")
        self.assertEqual(event["to_api"], "Key 2")
        self.assertEqual(event["page"], 3)
        self.assertEqual(event["reason"], "Rate limit reached")

    def test_switch_to_next_api_returns_none_when_chain_exhausted(self):
        """
        Characterization: advance_chain returns None when no more active slots exist.
        """
        slot1 = ApiSlot(id=1, provider="google", api_key="k1", label="Key 1", slot_type="private", selected_model="m1")
        chain = [slot1]

        next_slot, next_idx, event = FallbackChainPolicy.advance_chain(
            chain=chain, current_index=0, reason="Key invalid", at_page=1
        )

        self.assertIsNone(next_slot)
        self.assertEqual(event, {})


if __name__ == "__main__":
    unittest.main()
