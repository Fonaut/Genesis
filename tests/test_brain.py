"""
tests/test_brain.py — Offline Unit Tests for Genesis Brain Memory System.

Tests:
1. Memory initialization (JSON file creation).
2. JSON fallback loading, saving, and last-N retrieval.
3. Memory storage in JSON and ChromaDB vector store.
4. Duplicate prevention via string check (JSON) and SHA-256 hash ID (ChromaDB).
5. Empty / whitespace memory rejection.
6. Semantic recall when ChromaDB has matching records.
7. Fallback to recent JSON memories when semantic search yields no results.
8. Fallback to recent JSON memories when query is empty.

Runs completely offline with zero paid API calls and zero modifications to live memory stores.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from memory.brain import Brain


class TestBrain(unittest.TestCase):
    """Offline unit test suite for Brain memory system."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_json = Path(self.temp_dir.name) / "test_memories.json"

        # Mock ChromaDB persistent client and collection
        self.mock_chroma_patcher = patch("memory.brain.chromadb.PersistentClient")
        self.mock_chroma_cls = self.mock_chroma_patcher.start()
        self.mock_client_instance = MagicMock()
        self.mock_chroma_cls.return_value = self.mock_client_instance

        self.mock_collection = MagicMock()
        self.mock_client_instance.get_or_create_collection.return_value = self.mock_collection

        # Mock OpenAI embedding function
        self.mock_embed_patcher = patch("memory.brain.embedding_functions.OpenAIEmbeddingFunction")
        self.mock_embed_cls = self.mock_embed_patcher.start()

        # Patch the default JSON file path in Brain to our temp file
        with patch.object(Brain, "__init__", lambda s: None):
            self.brain = Brain()
            self.brain.json_file = self.temp_json
            self.brain.chroma_client = self.mock_client_instance
            self.brain.embedding_fn = self.mock_embed_cls.return_value
            self.brain.collection = self.mock_collection
            if not self.brain.json_file.exists():
                self.brain.json_file.write_text("[]")

    def tearDown(self):
        self.mock_chroma_patcher.stop()
        self.mock_embed_patcher.stop()
        self.temp_dir.cleanup()

    def test_json_file_created_if_missing(self):
        """Verifies that json_file is initialized with empty list if not present."""
        missing_json = Path(self.temp_dir.name) / "missing.json"
        with patch("memory.brain.chromadb.PersistentClient"), \
             patch("memory.brain.embedding_functions.OpenAIEmbeddingFunction"), \
             patch("memory.brain.Path", return_value=missing_json):
            brain = Brain()
            self.assertTrue(missing_json.exists())
            self.assertEqual(json.loads(missing_json.read_text()), [])

    def test_remember_valid_memory_saves_to_json_and_chroma(self):
        """Storing a valid memory adds it to JSON and vector store."""
        memory_text = "Values deep, honest conversation and reflective thinking."
        expected_hash = hashlib.sha256(memory_text.encode()).hexdigest()

        # Simulate Chroma get returning no existing record
        self.mock_collection.get.return_value = {"ids": []}

        self.brain.remember(memory_text)

        # Verify saved to JSON
        json_data = json.loads(self.temp_json.read_text())
        self.assertIn(memory_text, json_data)

        # Verify added to Chroma collection with SHA-256 hash
        self.mock_collection.get.assert_called_with(ids=[expected_hash])
        self.mock_collection.add.assert_called_once_with(
            ids=[expected_hash],
            documents=[memory_text]
        )

    def test_remember_empty_or_whitespace_is_ignored(self):
        """Empty strings or strings with only whitespace are discarded."""
        self.brain.remember("   ")
        self.brain.remember("")

        json_data = json.loads(self.temp_json.read_text())
        self.assertEqual(json_data, [])
        self.mock_collection.add.assert_not_called()

    def test_remember_prevents_duplicate_entries(self):
        """Submitting identical memories does not create duplicates in JSON or Chroma."""
        memory_text = "Prefers experimentation over speculative theory."
        expected_hash = hashlib.sha256(memory_text.encode()).hexdigest()

        # First store: not in Chroma
        self.mock_collection.get.return_value = {"ids": []}
        self.brain.remember(memory_text)

        # Second store: already in Chroma and in JSON
        self.mock_collection.get.return_value = {"ids": [expected_hash]}
        self.brain.remember(memory_text)

        # Verify JSON only has one copy
        json_data = json.loads(self.temp_json.read_text())
        self.assertEqual(json_data.count(memory_text), 1)

        # Verify Chroma add was only called once
        self.assertEqual(self.mock_collection.add.call_count, 1)

    def test_recall_empty_query_returns_recent_json(self):
        """When query is empty, recall falls back to the most recent JSON memories."""
        sample_memories = [f"Memory {i}" for i in range(7)]
        self.temp_json.write_text(json.dumps(sample_memories))

        results = self.brain.recall("")
        # Should return last 5 memories from JSON
        self.assertEqual(results, ["Memory 2", "Memory 3", "Memory 4", "Memory 5", "Memory 6"])
        self.mock_collection.query.assert_not_called()

    def test_recall_semantic_hit_returns_chroma_documents(self):
        """When Chroma finds semantic matches, return the documents list."""
        self.mock_collection.query.return_value = {
            "ids": [["hash1", "hash2"]],
            "documents": [["Understands Python well", "Builds reliable systems"]],
            "distances": [[0.1, 0.2]],
        }

        results = self.brain.recall("What does the user know about programming?")
        self.assertEqual(results, ["Understands Python well", "Builds reliable systems"])
        self.mock_collection.query.assert_called_once_with(
            query_texts=["What does the user know about programming?"],
            n_results=3,
        )

    def test_recall_fallback_to_json_when_chroma_returns_empty(self):
        """When semantic search yields no matches, fall back to recent JSON memories."""
        sample_memories = ["Early foundational memory", "Recent active memory"]
        self.temp_json.write_text(json.dumps(sample_memories))

        self.mock_collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "distances": [[]],
        }

        results = self.brain.recall("Query with no semantic match")
        self.assertEqual(results, sample_memories)

    def test_get_last_n_from_json_handles_empty_and_fewer_items(self):
        """_get_last_n_from_json handles empty lists and lists smaller than N."""
        self.temp_json.write_text("[]")
        self.assertEqual(self.brain._get_last_n_from_json(5), [])

        self.temp_json.write_text(json.dumps(["Only one"]))
        self.assertEqual(self.brain._get_last_n_from_json(5), ["Only one"])


if __name__ == "__main__":
    unittest.main()