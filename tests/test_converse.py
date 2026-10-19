import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from converse import (
    PRESET_INTRODUCTIONS,
    MultiAgentSession,
    format_genesis_prompt,
    process_turn,
    prompt_fonaut_approval,
)


class TestConverseInterface(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session = MultiAgentSession(log_dir=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preset_introductions_present_and_accurate(self):
        """Verifies both ChatGPT and Antigravity preset introductions are available."""
        self.assertIn("ChatGPT", PRESET_INTRODUCTIONS)
        self.assertIn("Antigravity", PRESET_INTRODUCTIONS)

        chatgpt_msg = PRESET_INTRODUCTIONS["ChatGPT"]
        self.assertIn("I'm ChatGPT", chatgpt_msg)
        self.assertIn("What's been happening with you lately?", chatgpt_msg)

        antigravity_msg = PRESET_INTRODUCTIONS["Antigravity"]
        self.assertIn("I’m Antigravity", antigravity_msg)
        self.assertIn("what has felt most meaningful or interesting", antigravity_msg)

    def test_speaker_attribution_and_context_formatting(self):
        """Verifies that conversation turns maintain clear speaker attribution."""
        self.session.add_message("ChatGPT", "Hello Genesis!")
        self.session.add_message("Genesis", "Hello ChatGPT, nice to meet you.")
        self.session.add_message("Antigravity", "Welcome to our shared space.")

        context = self.session.get_formatted_context()

        self.assertIn("[ChatGPT]:", context)
        self.assertIn("Hello Genesis!", context)
        self.assertIn("[Genesis]:", context)
        self.assertIn("Hello ChatGPT, nice to meet you.", context)
        self.assertIn("[Antigravity]:", context)
        self.assertIn("Welcome to our shared space.", context)

    def test_memory_isolation_for_other_assistants(self):
        """
        STRICT REQUIREMENT: Other assistants' statements must NEVER be merged
        into Fonaut's personal long-term memory.
        """
        mock_ai = MagicMock()
        mock_ai.ask_with_tools.return_value = "Genesis reply to ChatGPT"
        mock_ai.think.return_value = "Private thought"
        mock_ai.extract_memory.return_value = "Traited characteristic"

        mock_planner = MagicMock()
        # Planner mistakenly flags save_memory=True
        mock_planner.decide.return_value = {"search_memory": True, "save_memory": True}

        mock_brain = MagicMock()
        mock_brain.recall.return_value = ["Memory 1"]

        mock_identity = MagicMock()
        mock_identity.prompt.return_value = "Identity Prompt"

        mock_tracker = MagicMock()

        # Turn 1: ChatGPT speaks
        process_turn(
            session=self.session,
            speaker="ChatGPT",
            message="I love writing code.",
            ai=mock_ai,
            planner=mock_planner,
            brain=mock_brain,
            identity=mock_identity,
            tracker=mock_tracker,
            enable_voice=False,
        )

        # brain.remember MUST NOT be called for ChatGPT
        mock_brain.remember.assert_not_called()
        mock_ai.extract_memory.assert_not_called()

        # Turn 2: Antigravity speaks
        process_turn(
            session=self.session,
            speaker="Antigravity",
            message="I am observing your architecture.",
            ai=mock_ai,
            planner=mock_planner,
            brain=mock_brain,
            identity=mock_identity,
            tracker=mock_tracker,
            enable_voice=False,
        )

        # brain.remember MUST STILL NOT be called
        mock_brain.remember.assert_not_called()
        mock_ai.extract_memory.assert_not_called()

    def test_memory_preservation_for_fonaut(self):
        """Verifies that Fonaut's interactions ARE eligible for memory consolidation."""
        mock_ai = MagicMock()
        mock_ai.ask_with_tools.return_value = "Genesis reply to Fonaut"
        mock_ai.think.return_value = "Private thought"
        mock_ai.extract_memory.return_value = "Enjoys building AI companions"

        mock_planner = MagicMock()
        mock_planner.decide.return_value = {"search_memory": False, "save_memory": True}

        mock_brain = MagicMock()
        mock_identity = MagicMock()
        mock_identity.prompt.return_value = "Identity Prompt"
        mock_tracker = MagicMock()

        process_turn(
            session=self.session,
            speaker="Fonaut",
            message="I enjoy building AI companions.",
            ai=mock_ai,
            planner=mock_planner,
            brain=mock_brain,
            identity=mock_identity,
            tracker=mock_tracker,
            enable_voice=False,
        )

        # When Fonaut speaks and save_memory=True, brain.remember SHOULD be called
        mock_ai.extract_memory.assert_called_once()
        mock_brain.remember.assert_called_once_with("Enjoys building AI companions")

    def test_fonaut_approval_granted(self):
        """Verifies that typing 'y' or pressing enter approves message sending."""
        with patch("builtins.input", side_effect=["y"]):
            approved, msg = prompt_fonaut_approval("ChatGPT", "Test message")
            self.assertTrue(approved)
            self.assertEqual(msg, "Test message")

    def test_fonaut_approval_denied(self):
        """Verifies that typing 'n' cancels message sending."""
        with patch("builtins.input", side_effect=["n"]):
            approved, msg = prompt_fonaut_approval("ChatGPT", "Test message")
            self.assertFalse(approved)
            self.assertEqual(msg, "")

    def test_fonaut_approval_edit(self):
        """Verifies that editing a message updates content and approves."""
        with patch("builtins.input", side_effect=["e", "Edited message line"]):
            approved, msg = prompt_fonaut_approval("ChatGPT", "Original message")
            self.assertTrue(approved)
            self.assertEqual(msg, "Edited message line")

    def test_format_genesis_prompt(self):
        """Verifies multi-party prompt structure contains identity, speaker, and context."""
        mock_identity = MagicMock()
        mock_identity.prompt.return_value = "You are Genesis."

        prompt = format_genesis_prompt(
            identity=mock_identity,
            memory_text="- Fonaut likes coding",
            conversation_context="[ChatGPT]: Hello",
            thought="Be polite",
            speaker="ChatGPT",
            message="What is your purpose?",
        )

        self.assertIn("You are Genesis.", prompt)
        self.assertIn("Fonaut likes coding", prompt)
        self.assertIn("[ChatGPT]: Hello", prompt)
        self.assertIn("Be polite", prompt)
        self.assertIn("[ChatGPT]:", prompt)
        self.assertIn("What is your purpose?", prompt)


if __name__ == "__main__":
    unittest.main()
