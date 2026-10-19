import unittest
from unittest.mock import MagicMock, patch

from core.wake_word import parse_wake_word, wait_for_wake_word
from core.config import SHOW_INTERNAL_THOUGHTS


class TestWakeWordParsing(unittest.TestCase):

    def test_one_shot_commands(self):
        """Test natural one-shot activation with command in the same breath."""
        cases = [
            ("Genesis, what time is it?", "what time is it?"),
            ("genesis what time is it", "what time is it"),
            ("Hey Genesis, open Safari", "open Safari"),
            ("Hello Genesis! Can you list files?", "Can you list files?"),
            ("OK Genesis: What are my goals?", "What are my goals?"),
            ("Uh, Genesis, what is the date?", "what is the date?"),
        ]
        for transcript, expected_command in cases:
            with self.subTest(transcript=transcript):
                is_wake, command = parse_wake_word(transcript)
                self.assertTrue(is_wake)
                self.assertEqual(command, expected_command)

    def test_two_stage_wake_alone(self):
        """Test wake word alone returning empty command string for two-stage flow."""
        cases = [
            "Genesis",
            "genesis",
            "GENESIS",
            "Genesis!",
            "Genesis.",
            "Genesis...",
            "Hey Genesis",
            "Hello Genesis!",
            "   Genesis   ",
        ]
        for transcript in cases:
            with self.subTest(transcript=transcript):
                is_wake, command = parse_wake_word(transcript)
                self.assertTrue(is_wake)
                self.assertEqual(command, "")

    def test_preserves_internal_wake_word_occurrences(self):
        """Test that occurrences of 'Genesis' inside the query are NOT stripped."""
        cases = [
            (
                "Genesis, what is the meaning of Genesis in the Bible?",
                "what is the meaning of Genesis in the Bible?",
            ),
            (
                "Genesis, tell me about the Genesis block in Bitcoin.",
                "tell me about the Genesis block in Bitcoin.",
            ),
            (
                "Hey Genesis, who created Genesis?",
                "who created Genesis?",
            ),
        ]
        for transcript, expected_command in cases:
            with self.subTest(transcript=transcript):
                is_wake, command = parse_wake_word(transcript)
                self.assertTrue(is_wake)
                self.assertEqual(command, expected_command)

    def test_empty_and_whitespace_transcripts(self):
        """Test that empty or whitespace transcripts do not trigger wake word."""
        for transcript in ["", "   ", "\n\t", None]:
            with self.subTest(transcript=transcript):
                is_wake, command = parse_wake_word(transcript)
                self.assertFalse(is_wake)
                self.assertEqual(command, "")

    def test_false_wake_word_matches(self):
        """Test that similar words or wake word used non-activation do not trigger."""
        false_cases = [
            "General consensus is required.",
            "The genesis of the company was in 2020.",
            "We need to start from the genesis.",
            "Regenesis is a common sci-fi trope.",
            "Tell me about Genesis.",
            "I like reading the book of Genesis.",
        ]
        for transcript in false_cases:
            with self.subTest(transcript=transcript):
                is_wake, command = parse_wake_word(transcript)
                self.assertFalse(is_wake)
                self.assertEqual(command, "")

    @patch("core.wake_word.sf.write")
    @patch("core.wake_word.client")
    @patch("core.wake_word.sd.InputStream")
    def test_wait_for_wake_word_one_shot(self, mock_stream_cls, mock_client, mock_sf):
        """Test wait_for_wake_word returns command string on one-shot query."""
        # Mock InputStream
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream
        # Simulate audio chunks where volume > threshold
        mock_chunk = MagicMock()
        mock_chunk.copy.return_value = [0.05]
        mock_chunk.__iter__.return_value = [0.05]
        mock_stream.read.return_value = (mock_chunk, None)

        # Mock Whisper transcription response
        mock_transcript = MagicMock()
        mock_transcript.text = "Genesis, what is my schedule?"
        mock_client.audio.transcriptions.create.return_value = mock_transcript

        with patch("numpy.abs") as mock_abs:
            mock_abs.return_value.mean.return_value = 0.05
            with patch("builtins.open", MagicMock()):
                command = wait_for_wake_word()

        self.assertEqual(command, "what is my schedule?")

    @patch("core.wake_word.sf.write")
    @patch("core.wake_word.client")
    @patch("core.wake_word.sd.InputStream")
    def test_wait_for_wake_word_two_stage(self, mock_stream_cls, mock_client, mock_sf):
        """Test wait_for_wake_word returns empty string when only wake word is spoken."""
        mock_stream = MagicMock()
        mock_stream_cls.return_value = mock_stream
        mock_chunk = MagicMock()
        mock_chunk.copy.return_value = [0.05]
        mock_stream.read.return_value = (mock_chunk, None)

        mock_transcript = MagicMock()
        mock_transcript.text = "Genesis!"
        mock_client.audio.transcriptions.create.return_value = mock_transcript

        with patch("numpy.abs") as mock_abs:
            mock_abs.return_value.mean.return_value = 0.05
            with patch("builtins.open", MagicMock()):
                command = wait_for_wake_word()

        self.assertEqual(command, "")

    def test_reasoning_privacy_config_default(self):
        """Verifies that SHOW_INTERNAL_THOUGHTS defaults to False."""
        self.assertFalse(SHOW_INTERNAL_THOUGHTS)


if __name__ == "__main__":
    unittest.main()
