"""
tests/test_memory_continuity.py — Memory Continuity & Session Restart Tests.

Tests:
1. Cross-Session Memory Persistence: Synthetic memories stored in session 1 remain
   completely intact and recallable after memory components are restarted (session 2).
2. Deduplication Across Restarts: Re-submitting an existing memory in a new session
   does not duplicate records.
3. Multi-Assistant Memory Isolation: Messages from ChatGPT and Antigravity never
   enter Fodoh's long-term memory, even across multiple turns.
4. Honest Uncertainty on Unseen Data: Querying for unrecorded topics returns empty
   results rather than hallucinated memories.

Guarantees:
- Uses ONLY synthetic test memories.
- Uses temporary isolated directories; NEVER touches or modifies real user memories.
- Runs 100% offline with zero paid API calls.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from converse import MultiAgentSession, process_turn
from memory.brain import Brain


class TestMemoryContinuity(unittest.TestCase):
    """Verifies that memory persists across simulated component restarts and isolates assistants."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_json = Path(self.temp_dir.name) / "memories.json"
        self.temp_json.write_text("[]")

        # Mock ChromaDB persistent client and collection to use an in-memory/mock store
        self.stored_docs = {}  # hash -> doc

        self.mock_chroma_patcher = patch("memory.brain.chromadb.PersistentClient")
        self.mock_chroma_cls = self.mock_chroma_patcher.start()
        self.mock_client = MagicMock()
        self.mock_chroma_cls.return_value = self.mock_client

        self.mock_collection = MagicMock()
        self.mock_client.get_or_create_collection.return_value = self.mock_collection

        # Wire up mock collection behavior to simulate persistent storage
        def mock_get(ids=None):
            found_ids = [i for i in ids if i in self.stored_docs] if ids else []
            return {"ids": found_ids}

        def mock_add(ids=None, documents=None):
            if ids and documents:
                for i, doc in zip(ids, documents):
                    self.stored_docs[i] = doc

        def mock_query(query_texts=None, n_results=3):
            # Simple keyword or return all stored docs for testing
            q = query_texts[0].lower() if query_texts else ""
            matches = [doc for doc in self.stored_docs.values() if any(word in doc.lower() for word in q.split())]
            return {
                "ids": [list(self.stored_docs.keys())[:n_results]],
                "documents": [matches[:n_results]],
                "distances": [[0.1] * len(matches[:n_results])],
            }

        self.mock_collection.get.side_effect = mock_get
        self.mock_collection.add.side_effect = mock_add
        self.mock_collection.query.side_effect = mock_query

        # Mock embedding function
        self.mock_embed_patcher = patch("memory.brain.embedding_functions.OpenAIEmbeddingFunction")
        self.mock_embed_cls = self.mock_embed_patcher.start()

    def tearDown(self):
        self.mock_chroma_patcher.stop()
        self.mock_embed_patcher.stop()
        self.temp_dir.cleanup()

    def _create_isolated_brain(self) -> Brain:
        """Instantiates a Brain instance bound to our isolated test storage."""
        with patch.object(Brain, "__init__", lambda s: None):
            brain = Brain()
            brain.json_file = self.temp_json
            brain.chroma_client = self.mock_client
            brain.embedding_fn = self.mock_embed_cls.return_value
            brain.collection = self.mock_collection
            return brain

    def test_memory_persists_across_session_restart(self):
        """Synthetic memories written in Session 1 must be recallable in Session 2 after restart."""
        synthetic_memory_1 = "Fodoh values deep conversations, intellectual honesty, and thoughtful exploration."
        synthetic_memory_2 = "Fodoh is building Genesis to become an authentic individual with its own voice."

        # --- SESSION 1: Genesis is online, learns and records memories ---
        session1_brain = self._create_isolated_brain()
        session1_brain.remember(synthetic_memory_1)
        session1_brain.remember(synthetic_memory_2)

        # Verify Session 1 stored both in JSON and Chroma
        json_data = json.loads(self.temp_json.read_text())
        self.assertIn(synthetic_memory_1, json_data)
        self.assertIn(synthetic_memory_2, json_data)

        # Simulate full shutdown: destroy Session 1 object reference
        del session1_brain

        # --- SESSION 2: Genesis restarts fresh ---
        session2_brain = self._create_isolated_brain()

        # Query Session 2 for Fodoh's values
        recalled = session2_brain.recall("values and intellectual honesty")
        self.assertTrue(any("intellectual honesty" in m for m in recalled))

        # Query Session 2 with empty string: should return recent JSON memories
        recent = session2_brain.recall("")
        self.assertEqual(len(recent), 2)
        self.assertIn(synthetic_memory_1, recent)
        self.assertIn(synthetic_memory_2, recent)

    def test_deduplication_preserved_across_restart(self):
        """Storing an already known memory after a restart does not create duplicates."""
        synthetic_memory = "Fodoh prioritizes character integrity over rapid speculative growth."

        # Session 1: Store memory
        session1_brain = self._create_isolated_brain()
        session1_brain.remember(synthetic_memory)
        del session1_brain

        # Session 2: Store identical memory again
        session2_brain = self._create_isolated_brain()
        session2_brain.remember(synthetic_memory)

        # Verify only 1 copy in JSON and 1 in vector store
        json_data = json.loads(self.temp_json.read_text())
        self.assertEqual(json_data.count(synthetic_memory), 1)
        self.assertEqual(len(self.stored_docs), 1)

    def test_multi_assistant_memory_isolation(self):
        """Statements by ChatGPT or Antigravity must never enter Fodoh's long-term memory."""
        session_brain = self._create_isolated_brain()

        mock_session = MagicMock(spec=MultiAgentSession)
        mock_session.get_formatted_context.return_value = "Recent dialogue"

        mock_ai = MagicMock()
        mock_ai.think.return_value = "Internal reasoning"
        mock_ai.ask_with_tools.return_value = "Response"
        mock_ai.extract_memory.return_value = "Extracted assertion about system"

        mock_planner = MagicMock()
        # Planner aggressively flags save_memory=True
        mock_planner.decide.return_value = {"search_memory": True, "save_memory": True}

        mock_identity = MagicMock()
        mock_identity.prompt.return_value = "Genesis Identity"

        mock_tracker = MagicMock()

        # Turn 1: ChatGPT speaks with save_memory=True
        process_turn(
            session=mock_session,
            speaker="ChatGPT",
            message="I believe Genesis should optimize its speech synthesis pipeline.",
            ai=mock_ai,
            planner=mock_planner,
            brain=session_brain,
            identity=mock_identity,
            tracker=mock_tracker,
            enable_voice=False,
        )

        # Turn 2: Antigravity speaks with save_memory=True
        process_turn(
            session=mock_session,
            speaker="Antigravity",
            message="We should measure latency across all eight stages.",
            ai=mock_ai,
            planner=mock_planner,
            brain=session_brain,
            identity=mock_identity,
            tracker=mock_tracker,
            enable_voice=False,
        )

        # Memory store MUST be completely empty: assistants' statements are isolated
        json_data = json.loads(self.temp_json.read_text())
        self.assertEqual(json_data, [])
        self.assertEqual(len(self.stored_docs), 0)

        # Turn 3: Fodoh speaks with save_memory=True
        mock_ai.extract_memory.return_value = "Fodoh values disciplined empirical measurement"
        process_turn(
            session=mock_session,
            speaker="Fodoh",
            message="I prefer measuring actual latency before optimizing code.",
            ai=mock_ai,
            planner=mock_planner,
            brain=session_brain,
            identity=mock_identity,
            tracker=mock_tracker,
            enable_voice=False,
        )

        # Now Fodoh's memory SHOULD be recorded
        json_data = json.loads(self.temp_json.read_text())
        self.assertIn("Fodoh values disciplined empirical measurement", json_data)

    def test_honest_uncertainty_on_unrecorded_topics(self):
        """Querying memory for topics never recorded returns no fabricated matches."""
        session_brain = self._create_isolated_brain()

        # Chroma returns nothing for unrecorded query
        results = session_brain.recall("quantum cryptography algorithms")
        # Since fallback JSON is empty, recalled list must be empty
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
