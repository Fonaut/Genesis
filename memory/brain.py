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
    def __init__(self, json_path=None, chroma_path=None):
        # 1. JSON fallback path (configurable via argument or environment for isolated testing)
        env_json = os.getenv("GENESIS_MEMORY_JSON")
        self.json_file = Path(json_path or env_json or "memory/memories.json")
        if not self.json_file.exists():
            self.json_file.parent.mkdir(parents=True, exist_ok=True)
            self.json_file.write_text("[]")

        # 2. Initialize ChromaDB persistent client (configurable via argument or environment)
        env_chroma = os.getenv("GENESIS_CHROMA_DB")
        chroma_dir = chroma_path or env_chroma or "./chroma_db"
        self.chroma_client = chromadb.PersistentClient(path=chroma_dir)

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

        # 5. Initialize separate collaborative memory staging & review system
        from memory.collaborative import CollaborativeMemoryManager
        self.collaborative = CollaborativeMemoryManager(chroma_client=self.chroma_client)

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

    # -------------------------------------------------------------------------
    # Collaborative Memory Delegations
    # -------------------------------------------------------------------------

    def remember_collaborative(self, content: str, speaker: str, category: str, session_id: str, **kwargs):
        """Proposes a collaborative memory into the staging store."""
        if not hasattr(self, "collaborative") or self.collaborative is None:
            from memory.collaborative import CollaborativeMemoryManager
            self.collaborative = CollaborativeMemoryManager(chroma_client=getattr(self, "chroma_client", None))
        return self.collaborative.propose_memory(
            content=content,
            speaker=speaker,
            category=category,
            session_id=session_id,
            **kwargs,
        )

    def recall_collaborative(self, query: str = "", limit: int = 3, active_only: bool = True, category: str = None, include_synthetic: bool = False):
        """Recalls collaborative memories, by default strictly active non-synthetic recommendations."""
        if not hasattr(self, "collaborative") or self.collaborative is None:
            from memory.collaborative import CollaborativeMemoryManager
            self.collaborative = CollaborativeMemoryManager(chroma_client=getattr(self, "chroma_client", None))
        return self.collaborative.recall_memories(
            query=query,
            limit=limit,
            active_only=active_only,
            category=category,
            include_synthetic=include_synthetic,
        )