import unittest
from unittest.mock import MagicMock, patch

from core.planner import Planner


class TestPlanner(unittest.TestCase):

    def setUp(self):
        self.planner = Planner()

    @patch("core.planner.client")
    def test_search_memory_only(self, mock_client):
        """Test planner returning SEARCH_MEMORY only."""
        mock_response = MagicMock()
        mock_response.output_text = "SEARCH_MEMORY"
        mock_client.responses.create.return_value = mock_response

        result = self.planner.decide("What is my goal?")

        mock_client.responses.create.assert_called_once()
        call_kwargs = mock_client.responses.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gpt-4o-mini")
        self.assertEqual(call_kwargs["max_output_tokens"], 20)
        self.assertIn("What is my goal?", call_kwargs["input"])

        self.assertTrue(result["search_memory"])
        self.assertFalse(result["save_memory"])

    @patch("core.planner.client")
    def test_save_memory_only(self, mock_client):
        """Test planner returning SAVE_MEMORY only."""
        mock_response = MagicMock()
        mock_response.output_text = "SAVE_MEMORY"
        mock_client.responses.create.return_value = mock_response

        result = self.planner.decide("I value honesty above all else.")

        self.assertFalse(result["search_memory"])
        self.assertTrue(result["save_memory"])

    @patch("core.planner.client")
    def test_both_search_and_save_memory(self, mock_client):
        """Test planner returning both SEARCH_MEMORY and SAVE_MEMORY."""
        mock_response = MagicMock()
        mock_response.output_text = "SEARCH_MEMORY\nSAVE_MEMORY"
        mock_client.responses.create.return_value = mock_response

        result = self.planner.decide("I'm trying to become a software engineer.")

        self.assertTrue(result["search_memory"])
        self.assertTrue(result["save_memory"])

    @patch("core.planner.client")
    def test_none_response(self, mock_client):
        """Test planner returning NONE."""
        mock_response = MagicMock()
        mock_response.output_text = "NONE"
        mock_client.responses.create.return_value = mock_response

        result = self.planner.decide("Hello there!")

        self.assertFalse(result["search_memory"])
        self.assertFalse(result["save_memory"])


if __name__ == "__main__":
    unittest.main()
