import json
import os
import hashlib
from pathlib import Path

from dotenv import load_dotenv
import chromadb
from chromadb.utils import embedding_functions

# Load environment variables (OPENAI_API_KEY)
load_dotenv()


class Brain:
    def __init__(self):
        # 1. JSON fallback path (keep for safety and old queries)
        self.json_file = Path("memory/memories.json")
        if not self.json_file.exists():
            self.json_file.write_text("[]")

        # 2. Initialize ChromaDB persistent client
        self.chroma_client = chromadb.PersistentClient(path="./chroma_db")

        # 3. Set up OpenAI embedding function
        self.embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.getenv("OPENAI_API_KEY"),
            model_name="text-embedding-3-small"
        )

        # 4. Get or create the "memories" collection
        self.collection = self.chroma_client.get_or_create_collection(
            name="memories",
            embedding_function=self.embedding_fn
        )

    def _load_json(self):
        """Load memories from JSON (fallback / history)."""
        return json.loads(self.json_file.read_text())

    def _save_json(self, memories):
        """Save memories to JSON."""
        self.json_file.write_text(json.dumps(memories, indent=4))

    def _get_last_n_from_json(self, n=5):
        """Return the last N memories from JSON."""
        memories = self._load_json()
        return memories[-n:] if memories else []

    def recall(self, message=""):
        """
        Retrieve memories:
        - If query is empty, return last 5 from JSON.
        - Otherwise, do a semantic search in Chroma (top 3).
        - If Chroma finds nothing, fallback to last 5 from JSON.
        """
        if not message.strip():
            return self._get_last_n_from_json(5)

        # Query ChromaDB for the top 3 most similar memories
        results = self.collection.query(
            query_texts=[message],
            n_results=3
        )

        # Chroma returns: {'ids': [...], 'documents': [...], 'distances': [...]}
        if results and results['documents'] and len(results['documents'][0]) > 0:
            return results['documents'][0]   # list of memory strings
        else:
            # Fallback: no semantic match, return recent memories
            return self._get_last_n_from_json(5)

    def remember(self, memory):
        """Store a memory in both JSON (for fallback) and Chroma (for semantic search)."""
        memory = memory.strip()
        if not memory:
            return

        # --- 1. Save to JSON (avoid duplicates) ---
        json_memories = self._load_json()
        if memory not in json_memories:
            json_memories.append(memory)
            self._save_json(json_memories)

        # --- 2. Save to Chroma (use SHA256 hash as ID to prevent duplicates) ---
        memory_hash = hashlib.sha256(memory.encode()).hexdigest()

        # Check if this exact memory already exists in Chroma
        existing = self.collection.get(ids=[memory_hash])
        if not existing or not existing['ids']:
            # Add to vector database
            self.collection.add(
                ids=[memory_hash],
                documents=[memory]
            )