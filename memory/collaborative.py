"""
Genesis Collaborative Memory System — Staging Store & Review Queue
Provides structured provenance, distinct memory categorization, authorization-derived approvals,
reversible backups, consistency checks, and strict zero-trust identity and personal memory protection.
"""

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hub.phase0 import PROJECT_ROOT


class MemoryCategory(str, Enum):
    OBSERVED_EVENT = "OBSERVED_EVENT"                    # Turn events, latency, model usage, sequence
    UNVERIFIED_PROPOSAL = "UNVERIFIED_PROPOSAL"          # Agent suggestions, ideas, opinions, hypotheses
    VERIFIED_DISCOVERY = "VERIFIED_DISCOVERY"            # Empirically measured results, benchmark facts
    APPROVED_DECISION = "APPROVED_DECISION"              # Architectural decisions approved by Fodoh
    CONFIRMED_USER_PROFILE = "CONFIRMED_USER_PROFILE"    # Personal facts about Fodoh, strictly confirmed by Fodoh
    HISTORICAL_TEST = "HISTORICAL_TEST"                  # Synthetic offline test runs, quarantined from live memory


class ApprovalStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"    # Staged candidate memory awaiting human review
    APPROVED = "APPROVED"                # Formally authorized by Fodoh
    REJECTED = "REJECTED"                # Rejected by Fodoh (historical negative learning)
    SUPERSEDED = "SUPERSEDED"            # Replaced by a newer decision
    QUARANTINED = "QUARANTINED"          # Isolated test/synthetic memory, excluded from review queue and active recall


class AuthorizationScope(str, Enum):
    COMMUNICATION_TRANSIT = "COMMUNICATION_TRANSIT"      # Forwarding agent messages / probes
    MEMORY_APPROVAL = "MEMORY_APPROVAL"                  # Explicit user authorization of long-term memory


@dataclass
class MemoryProvenance:
    memory_id: str
    session_id: str
    turn_id: Optional[int]
    speaker: str
    category: str
    status: str
    timestamp: str
    content: str
    rationale: str = ""
    transcript_path: Optional[str] = None
    authorization_record: Optional[str] = None
    authorization_scope: Optional[str] = None
    is_active_recommendation: bool = False
    promoted_to_long_term: bool = False
    is_synthetic: bool = False
    approved_text: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryProvenance":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_provenance_string(self) -> str:
        """Formatted human-readable string representation preserving timezone and synthetic indicators."""
        auth = f" | Auth: {self.authorization_record}" if self.authorization_record else ""
        scope = f" | Scope: {self.authorization_scope}" if self.authorization_scope else ""
        synth = " | SYNTHETIC" if self.is_synthetic else ""
        return (
            f"[{self.category}{synth} | Speaker: {self.speaker} | Status: {self.status}"
            f" | Session: {self.session_id}{auth}{scope} | {self.timestamp}] "
            f"{self.content}"
        )


class CollaborativeMemorySecurityError(Exception):
    """Raised when an untrusted agent output attempts to violate security boundaries."""
    pass


class OfflineDeterministicEmbeddingFunction:
    """100% offline, zero-network deterministic embedding function for ChromaDB."""
    def __init__(self):
        self.is_legacy = False

    def name(self) -> str:
        return "default"

    def __call__(self, input: List[str]) -> List[List[float]]:
        embeddings = []
        for text in input:
            raw_hash = hashlib.sha256(text.encode("utf-8")).digest()
            vec = [float(b) / 255.0 for b in raw_hash]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            embeddings.append([x / norm for x in vec])
        return embeddings


class CollaborativeMemoryManager:
    """
    Manages the collaborative memory staging store, human review queue,
    structured provenance tracking, and ChromaDB vector synchronization.
    """

    def __init__(
        self,
        staging_file: Optional[Path] = None,
        backup_dir: Optional[Path] = None,
        chroma_client: Optional[Any] = None,
        collection_name: str = "collaborative_memories",
        embedding_fn: Optional[Any] = None,
    ):
        self.staging_file = staging_file or (PROJECT_ROOT / "memory" / "collaborative_staging.json")
        self.backup_dir = backup_dir or (PROJECT_ROOT / "memory" / "backups")
        self.collection_name = collection_name
        self.embedding_fn = embedding_fn or OfflineDeterministicEmbeddingFunction()
        self._chroma_client = chroma_client
        self._collection = None

        # Ensure directories exist
        self.staging_file.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        if not self.staging_file.exists():
            self._write_staging_json([])

    # -------------------------------------------------------------------------
    # ChromaDB Collection Initialization (Lazy & Mock-Resilient)
    # -------------------------------------------------------------------------

    @property
    def collection(self):
        """Lazily initializes the ChromaDB collaborative_memories collection."""
        if self._collection is not None:
            return self._collection

        if self._chroma_client is not None:
            self._collection = self._chroma_client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
            )
            return self._collection

        # Default ChromaDB persistent client
        try:
            import chromadb
            chroma_dir = os.getenv("GENESIS_CHROMA_DB", str(PROJECT_ROOT / "chroma_db"))
            client = chromadb.PersistentClient(path=chroma_dir)
            self._chroma_client = client
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn,
            )
            return self._collection
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Core JSON Staging Operations
    # -------------------------------------------------------------------------

    def _read_staging_json(self) -> List[Dict[str, Any]]:
        if not self.staging_file.exists():
            return []
        try:
            return json.loads(self.staging_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _write_staging_json(self, items: List[Dict[str, Any]]) -> None:
        tmp_file = self.staging_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(items, indent=2), encoding="utf-8")
        tmp_file.replace(self.staging_file)

    # -------------------------------------------------------------------------
    # Memory Proposal (Zero-Trust Input & Anti-Tampering Filter)
    # -------------------------------------------------------------------------

    def propose_memory(
        self,
        content: str,
        speaker: str,
        category: str,
        session_id: str,
        turn_id: Optional[int] = None,
        transcript_path: Optional[str] = None,
        authorization_record: Optional[str] = None,
        authorization_scope: Optional[str] = None,
        is_synthetic: bool = False,
        rationale: str = "",
    ) -> MemoryProvenance:
        """
        Stages a candidate memory into the review queue.
        
        Zero-Trust Invariants:
        1. Agent-generated content is ALWAYS untrusted. In-text claims of approval are ignored.
        2. Scope Separation: Communication forwarding approvals (gate_1, gate_2, gate_3)
           CANNOT authorize persistent memory. Memory approval requires distinct MEMORY_APPROVAL scope.
        3. Never classify a memory as APPROVED_DECISION solely because an agent-to-agent message was forwarded.
        4. Synthetic offline conversations are explicitly labeled and NEVER promoted by default.
        5. External agents CANNOT create CONFIRMED_USER_PROFILE memories.
        6. No writes to identity/self.json or legacy memories.json are permitted.
        """
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("Memory content cannot be empty.")

        # Zero-Trust Guard: Prevent external agents from asserting facts about Fodoh or Genesis identity
        if speaker != "Fodoh":
            if category == MemoryCategory.CONFIRMED_USER_PROFILE.value:
                # Force downgrade to untrusted proposal
                category = MemoryCategory.UNVERIFIED_PROPOSAL.value
                rationale = f"Third-party claim by {speaker} regarding user profile. Quarantined as unverified."

        # Validate Category
        valid_categories = {c.value for c in MemoryCategory}
        if category not in valid_categories:
            category = MemoryCategory.UNVERIFIED_PROPOSAL.value

        # Status derivation: strictly authorization-derived with scope validation
        status = ApprovalStatus.PENDING_REVIEW.value
        is_active = False

        if category == MemoryCategory.HISTORICAL_TEST.value:
            # Historical test classification: explicitly quarantined from human review queue & active recall
            status = ApprovalStatus.QUARANTINED.value
            is_active = False
            is_synthetic = True
            if not rationale:
                rationale = "Synthetic offline test event quarantined as historical test record."

        # Identify communication transit gates
        elif authorization_scope == AuthorizationScope.COMMUNICATION_TRANSIT.value or (
            authorization_record and any(k in authorization_record.lower() for k in ["gate_1", "gate_2", "gate_3", "comm_gate", "comm_transit"])
        ):
            # COMMUNICATION TRANSIT RULE:
            # Forwarding approvals can NEVER authorize a memory as an APPROVED_DECISION or APPROVED status.
            authorization_scope = AuthorizationScope.COMMUNICATION_TRANSIT.value
            if category == MemoryCategory.APPROVED_DECISION.value:
                category = MemoryCategory.UNVERIFIED_PROPOSAL.value
                if not rationale:
                    rationale = "Communication forwarding approval cannot authorize persistent memory. Quarantined as unverified proposal."
            if authorization_record and "REJECTED" in authorization_record.upper():
                status = ApprovalStatus.REJECTED.value
            else:
                status = ApprovalStatus.PENDING_REVIEW.value
            is_active = False

        elif authorization_record:
            # MEMORY APPROVAL RULE:
            # Genuine memory approval requires memory scope (e.g. manual_review, mem_approval)
            if authorization_scope == AuthorizationScope.MEMORY_APPROVAL.value or any(
                authorization_record.startswith(p) for p in ["manual_review_", "mem_approval_"]
            ):
                if "APPROVED" in authorization_record.upper():
                    if not is_synthetic:
                        status = ApprovalStatus.APPROVED.value
                        is_active = True
                    else:
                        # Synthetic responses are NOT promoted or active by default even with memory approval tag
                        status = ApprovalStatus.PENDING_REVIEW.value
                        is_active = False
                elif "REJECTED" in authorization_record.upper():
                    status = ApprovalStatus.REJECTED.value
                    is_active = False
            else:
                # Any other authorization without memory scope cannot approve memory
                status = ApprovalStatus.PENDING_REVIEW.value
                is_active = False

        # Generate deterministic stable memory ID
        content_hash = hashlib.sha256(clean_content.encode("utf-8")).hexdigest()[:12]
        now_iso = datetime.now().astimezone().isoformat()
        memory_id = f"collab_{session_id}_{speaker.lower()}_{content_hash}"

        provenance = MemoryProvenance(
            memory_id=memory_id,
            session_id=session_id,
            turn_id=turn_id,
            speaker=speaker,
            category=category,
            status=status,
            timestamp=now_iso,
            content=clean_content,
            rationale=rationale,
            transcript_path=transcript_path,
            authorization_record=authorization_record,
            authorization_scope=authorization_scope,
            is_active_recommendation=is_active,
            promoted_to_long_term=False,
            is_synthetic=is_synthetic,
        )

        # Save to staging JSON (with deduplication)
        items = self._read_staging_json()
        existing_idx = next((i for i, item in enumerate(items) if item.get("memory_id") == memory_id), None)
        if existing_idx is not None:
            items[existing_idx] = provenance.to_dict()
        else:
            items.append(provenance.to_dict())
        self._write_staging_json(items)

        # Synchronize into ChromaDB collection
        self._sync_item_to_chroma(provenance)

        return provenance

    # -------------------------------------------------------------------------
    # Review Queue & Human Approval Operations
    # -------------------------------------------------------------------------

    def get_review_queue(self) -> List[MemoryProvenance]:
        """Returns all proposed memories awaiting Fodoh's review."""
        items = self._read_staging_json()
        return [
            MemoryProvenance.from_dict(it)
            for it in items
            if it.get("status") == ApprovalStatus.PENDING_REVIEW.value
        ]

    def review_memory(
        self,
        memory_id: str,
        action: str,
        edited_content: Optional[str] = None,
        reviewer: str = "Fodoh",
    ) -> MemoryProvenance:
        """
        Applies Fodoh's explicit human review decision to a staged memory:
        - 'approve': Promotes status to APPROVED and category to APPROVED_DECISION, enables active recommendation.
        - 'reject': Marks status as REJECTED, retains as historical decision.
        - 'edit': Updates content, marks as APPROVED and APPROVED_DECISION.
        """
        items = self._read_staging_json()
        match_idx = next((i for i, it in enumerate(items) if it.get("memory_id") == memory_id), None)
        if match_idx is None:
            raise KeyError(f"Memory with ID '{memory_id}' not found in staging store.")

        item = items[match_idx]
        now_iso = datetime.now().astimezone().isoformat()
        act = action.lower()

        if act == "approve":
            item["status"] = ApprovalStatus.APPROVED.value
            item["category"] = MemoryCategory.APPROVED_DECISION.value
            item["is_active_recommendation"] = True
            item["reviewed_at"] = now_iso
            item["reviewed_by"] = reviewer
            item["authorization_record"] = f"mem_approval_{memory_id}:APPROVED_BY_{reviewer.upper()}"
            item["authorization_scope"] = AuthorizationScope.MEMORY_APPROVAL.value
            item["approved_text"] = item["content"]

        elif act == "reject":
            item["status"] = ApprovalStatus.REJECTED.value
            item["is_active_recommendation"] = False
            item["reviewed_at"] = now_iso
            item["reviewed_by"] = reviewer
            item["authorization_record"] = f"mem_approval_{memory_id}:REJECTED_BY_{reviewer.upper()}"
            item["authorization_scope"] = AuthorizationScope.MEMORY_APPROVAL.value

        elif act == "edit":
            if not edited_content or not edited_content.strip():
                raise ValueError("Edited content cannot be empty.")
            item["content"] = edited_content.strip()
            item["approved_text"] = edited_content.strip()
            item["status"] = ApprovalStatus.APPROVED.value
            item["category"] = MemoryCategory.APPROVED_DECISION.value
            item["is_active_recommendation"] = True
            item["reviewed_at"] = now_iso
            item["reviewed_by"] = reviewer
            item["authorization_record"] = f"mem_approval_edited_{memory_id}:APPROVED_BY_{reviewer.upper()}"
            item["authorization_scope"] = AuthorizationScope.MEMORY_APPROVAL.value

        else:
            raise ValueError(f"Unknown review action '{action}'. Must be 'approve', 'reject', or 'edit'.")

        items[match_idx] = item
        self._write_staging_json(items)

        updated_prov = MemoryProvenance.from_dict(item)
        self._sync_item_to_chroma(updated_prov)
        return updated_prov

    # -------------------------------------------------------------------------
    # Retrieval (Separating Active Recommendations from Historical Rejections)
    # -------------------------------------------------------------------------

    def recall_memories(
        self,
        query: str,
        limit: int = 3,
        active_only: bool = True,
        category: Optional[str] = None,
        include_synthetic: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves collaborative memories.
        If active_only=True: strictly excludes REJECTED, SUPERSEDED, PENDING_REVIEW, and QUARANTINED memories.
        If include_synthetic=False (default): strictly excludes synthetic test memories from trusted recall.
        If active_only=False: queries all memories including historical rejections and quarantined records.
        """
        items = self._read_staging_json()
        if not items:
            return []

        # Filter candidate items
        filtered = []
        for it in items:
            if active_only:
                if not it.get("is_active_recommendation", False):
                    continue
                if it.get("status") in [
                    ApprovalStatus.PENDING_REVIEW.value,
                    ApprovalStatus.REJECTED.value,
                    ApprovalStatus.SUPERSEDED.value,
                    ApprovalStatus.QUARANTINED.value,
                ]:
                    continue
                if it.get("category") == MemoryCategory.HISTORICAL_TEST.value:
                    continue
                # Requirement 6: Never allow synthetic test memories into Genesis's ordinary trusted-memory retrieval
                if it.get("is_synthetic", False) and not include_synthetic:
                    continue
            if category and it.get("category") != category:
                continue
            filtered.append(it)

        if not filtered:
            return []

        # Vector retrieval if Chroma is available and has records
        col = self.collection
        if col is not None and query.strip():
            try:
                where_filter = {}
                if active_only:
                    where_filter["is_active_recommendation"] = True
                    if not include_synthetic:
                        where_filter["is_synthetic"] = False
                if category:
                    where_filter["category"] = category

                query_kwargs: Dict[str, Any] = {
                    "query_texts": [query.strip()],
                    "n_results": min(limit, len(filtered)),
                }
                if where_filter:
                    query_kwargs["where"] = where_filter

                results = col.query(**query_kwargs)
                if results and results.get("ids") and len(results["ids"]) > 0 and len(results["ids"][0]) > 0:
                    matched_ids = set(results["ids"][0])
                    chroma_hits = [it for it in filtered if it.get("memory_id") in matched_ids]
                    if chroma_hits:
                        return chroma_hits[:limit]
            except Exception:
                # Fall through to keyword / recency matching
                pass

        # Text/keyword search or recency fallback
        q_lower = query.lower().strip()
        if q_lower:
            keyword_matches = [
                it for it in filtered
                if q_lower in it.get("content", "").lower() or q_lower in it.get("category", "").lower()
            ]
            if keyword_matches:
                return keyword_matches[:limit]

        # Recency fallback
        return filtered[-limit:]

    def recall_test_memories(
        self,
        query: str = "",
        limit: int = 5,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Isolated test-only retrieval path (Requirement 7):
        Retrieves approved synthetic test memories without contaminating ordinary trusted knowledge.
        """
        return self.recall_memories(
            query=query,
            limit=limit,
            active_only=True,
            category=category,
            include_synthetic=True,
        )

    # -------------------------------------------------------------------------
    # Reversible Operations: Backups & Consistency Reconciliation
    # -------------------------------------------------------------------------

    def create_backup(self) -> Path:
        """Creates an atomic timestamped snapshot backup of collaborative staging."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"collaborative_staging_{timestamp}.json"
        shutil.copy2(self.staging_file, backup_file)
        return backup_file

    def rollback_to_backup(self, backup_file: Path) -> bool:
        """Rolls back the staging store to a previous backup snapshot and reconciles ChromaDB."""
        if not backup_file.exists():
            raise FileNotFoundError(f"Backup file not found at: {backup_file}")
        shutil.copy2(backup_file, self.staging_file)
        self.reconcile_consistency()
        return True

    def delete_memory(self, memory_id: str) -> bool:
        """Deletes a memory record reversibly (creates backup before deletion)."""
        self.create_backup()
        items = self._read_staging_json()
        new_items = [it for it in items if it.get("memory_id") != memory_id]
        if len(new_items) == len(items):
            return False

        self._write_staging_json(new_items)

        # Remove from Chroma collection
        col = self.collection
        if col is not None:
            try:
                col.delete(ids=[memory_id])
            except Exception:
                pass
        return True

    def update_memory(self, memory_id: str, new_content: str) -> MemoryProvenance:
        """Updates content of an existing memory reversibly."""
        self.create_backup()
        return self.review_memory(memory_id=memory_id, action="edit", edited_content=new_content)

    def reconcile_consistency(self) -> Dict[str, Any]:
        """
        Reconciles consistency between JSON staging and ChromaDB vector store.
        Ensures all JSON items are indexed in Chroma and stale Chroma records are purged.
        """
        items = self._read_staging_json()
        indexed_count = 0
        json_ids = {it["memory_id"] for it in items}

        col = self.collection
        if col is not None:
            # Re-index all JSON items
            for it in items:
                prov = MemoryProvenance.from_dict(it)
                self._sync_item_to_chroma(prov)
                indexed_count += 1

            # Check for stale IDs in Chroma
            try:
                chroma_records = col.get()
                chroma_ids = set(chroma_records.get("ids", []))
                stale_ids = chroma_ids - json_ids
                if stale_ids:
                    col.delete(ids=list(stale_ids))
            except Exception:
                pass

        return {
            "status": "RECONCILED",
            "total_staged_memories": len(items),
            "reindexed_in_chroma": indexed_count,
            "staging_file": str(self.staging_file),
        }

    # -------------------------------------------------------------------------
    # Internal Chroma Synchronization Helper
    # -------------------------------------------------------------------------

    def _sync_item_to_chroma(self, prov: MemoryProvenance) -> None:
        col = self.collection
        if col is None:
            return

        metadata = {
            "memory_id": prov.memory_id,
            "session_id": prov.session_id,
            "turn_id": prov.turn_id if prov.turn_id is not None else -1,
            "speaker": prov.speaker,
            "category": prov.category,
            "status": prov.status,
            "timestamp": prov.timestamp,
            "is_synthetic": bool(prov.is_synthetic),
            "authorization_scope": prov.authorization_scope or "",
            "is_active_recommendation": bool(prov.is_active_recommendation),
            "promoted_to_long_term": bool(prov.promoted_to_long_term),
        }

        doc_text = prov.to_provenance_string()

        try:
            # Check existing
            existing = col.get(ids=[prov.memory_id])
            if existing and existing.get("ids"):
                col.update(
                    ids=[prov.memory_id],
                    documents=[doc_text],
                    metadatas=[metadata],
                )
            else:
                col.add(
                    ids=[prov.memory_id],
                    documents=[doc_text],
                    metadatas=[metadata],
                )
        except Exception:
            pass
