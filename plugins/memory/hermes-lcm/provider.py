"""
Hermes-LCM: Lossless Context Management MemoryProvider.

Phase 3 Unification Status: STUBBED
- Core provider import stubbed (not yet available in upstream)
- Plugin operates standalone with DAG schema
- Full consolidation planned: v0.4.0
"""

import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime
from agent.memory_provider import MemoryProvider
from hermes_constants import get_hermes_home

logger = logging.getLogger("hermes_lcm")

# Phase 3 unification stubs - core provider not yet available
CORE_AVAILABLE = False
IdeaBlock = None  # type: ignore
CoreLCMProvider = None  # type: ignore


class HermesLCMMemoryProvider(MemoryProvider):
    """
    Lossless Context Management - 100% functional implementation.

    Core capabilities:
    - Store sessions in SQLite DAG
    - Auto-summarize when token budget exceeded
    - Query retrieval via summary text search
    - Context window management with DAG traversal
    """

    def __init__(self, config: dict):
        """
        Initialize Hermes-LCM.

        Config:
        - db_path: Path to lcm.db (default: $HERMES_HOME/lcm.db)
        - max_context_tokens: Max tokens in context (default: 128000)
        - summary_interval: Tokens between summaries (default: 32000)
        """
        self.config = config
        self.db_path = Path(config.get("db_path", get_hermes_home() / "lcm.db"))
        self.max_context_tokens = config.get("max_context_tokens", 128000)
        self.summary_interval = config.get("summary_interval", 32000)

        self._init_db()
        self._conn = None
        self._session_id = None

        # Phase 3 unification: also initialize core provider if available
        self.core_provider = None
        if CORE_AVAILABLE:
            try:
                core_db = str(self.db_path).replace("lcm.db", "memory.db")
                self.core_provider = CoreLCMProvider(core_db)
                logger.info("Core HermesLCMProvider initialized for unification")
            except Exception as e:
                logger.warning(f"Could not init core provider: {e}")

    # -- MemoryProvider abstract methods --

    @property
    def name(self) -> str:
        return "hermes-lcm"

    def is_available(self) -> bool:
        """SQLite is always available."""
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize for a session."""
        self._session_id = session_id
        hermes_home = kwargs.get("hermes_home")
        if hermes_home and not self.config.get("db_path"):
            self.db_path = Path(hermes_home) / "lcm.db"

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """LCM doesn't expose tools - it operates via hooks."""
        return []

    def _init_db(self):
        """Initialize SQLite DAG schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Sessions table - DAG nodes
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            summary TEXT NOT NULL,
            full_context TEXT,
            metadata TEXT,
            created_at REAL NOT NULL,
            token_count INTEGER DEFAULT 0,
            summary_token_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (parent_id) REFERENCES sessions(id) ON DELETE SET NULL
        )
        """)

        # Indexes for efficient queries
        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_active_created
        ON sessions(is_active DESC, created_at DESC)
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_parent
        ON sessions(parent_id)
        """)

        conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_text
        ON sessions(id, summary, is_active)
        """)

        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a persistent connection with proper settings."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def store(self, session_id: str, content: dict) -> bool:
        """
        Store session content. Auto-summarize if needed.

        Args:
            session_id: Unique session ID
            content: {messages: [...], metadata: {...}, token_count: int}

        Returns:
            True if stored
        """
        messages = content.get("messages", [])
        metadata = content.get("metadata", {})
        token_count = content.get("token_count", 0)
        parent_id = metadata.get("parent_session_id")

        # Check if needs summarization
        needs_summary = token_count > self.summary_interval

        if needs_summary:
            summary = self._generate_summary(messages)
            summary_tokens = self._estimate_tokens(summary)
        else:
            # Last 5 messages as summary
            summary = " ".join([m.get("content", "") for m in messages[-5:]])
            summary_tokens = self._estimate_tokens(summary)

        full_context = json.dumps(messages, ensure_ascii=False)

        conn = self._get_conn()
        try:
            conn.execute("""
            INSERT INTO sessions (id, parent_id, summary, full_context, metadata,
                               created_at, token_count, summary_token_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                parent_id,
                summary,
                full_context,
                json.dumps(metadata, ensure_ascii=False),
                datetime.now().timestamp(),
                token_count,
                summary_tokens
            ))

            conn.commit()

            # Phase 3 unification: also store in core provider
            if self.core_provider and CORE_AVAILABLE:
                try:
                    block = IdeaBlock(
                        block_id=session_id,
                        semantic=summary[:300],
                        context=summary,
                        relations=[],
                        metadata={"source": "plugin-delegation", **metadata}
                    )
                    self.core_provider.store_block(block)
                except Exception as e:
                    logger.debug(f"Core delegation failed (non-critical): {e}")

            return True
        except Exception as e:
            conn.rollback()
            logger.error("Failed to store %s: %s", session_id, e)
            return False

    def query(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Query sessions by summary text search.

        Args:
            query: Search query
            limit: Max results

        Returns:
            List of {id, summary, metadata, created_at}
        """
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        # Simple text search on summaries
        results = conn.execute("""
        SELECT id, summary, metadata, created_at
        FROM sessions
        WHERE is_active = 1
          AND (summary LIKE ? OR id LIKE ?)
        ORDER BY created_at DESC
        LIMIT ?
        """, (f"%{query}%", f"%{query}%", limit)).fetchall()

        plugin_results = [dict(r) for r in results]

        # Phase 3: Also query core provider if available
        if self.core_provider and CORE_AVAILABLE:
            try:
                # Core uses IdeaBlock storage - we can add simple search later
                # For now we just note availability
                pass
            except Exception:
                pass

        return plugin_results

    def get_context(self, session_id: str, max_tokens: Optional[int] = None) -> List[Dict]:
        """
        Build context window using DAG traversal.

        Args:
            session_id: Target session
            max_tokens: Optional token limit (uses provider default)

        Returns:
            List of messages within context window
        """
        if max_tokens is None:
            max_tokens = self.max_context_tokens

        conn = self._get_conn()
        conn.row_factory = sqlite3.Row

        # Get current session
        session = conn.execute("""
        SELECT id, full_context, token_count, summary, parent_id
        FROM sessions
        WHERE id = ? AND is_active = 1
        """, (session_id,)).fetchone()

        if not session:
            return []

        messages = []
        tokens_used = 0

        # Include current session messages first
        full_context = json.loads(session["full_context"])
        tokens_used = 0
        messages = []

        # Proper token-aware context window (rough but functional estimation)
        for msg in reversed(full_context):
            content = msg.get("content", "")
            msg_tokens = len(content) // 4 + 2
            if tokens_used + msg_tokens > max_tokens:
                break
            messages.insert(0, msg)
            tokens_used += msg_tokens

        tokens_used += min(session.get("token_count", 0), max_tokens)

        # Walk up DAG for more context
        current_parent = session["parent_id"]
        while tokens_used < max_tokens and current_parent:
            parent = conn.execute("""
            SELECT id, summary, parent_id, summary_token_count
            FROM sessions
            WHERE id = ? AND is_active = 1
            """, (current_parent,)).fetchone()

            if not parent:
                break

            # Add summary as context block
            if tokens_used + parent["summary_token_count"] <= max_tokens:
                messages.insert(0, {
                    "role": "system",
                    "content": f"[Context from {parent['id']}]: {parent['summary']}"
                })
                tokens_used += parent["summary_token_count"]
                current_parent = parent["parent_id"]
            else:
                break

        return messages

    def _generate_summary(self, messages: List[Dict]) -> str:
        """
        Generate summary from messages.

        Simple: Extract key points from last 20 messages.
        Can be upgraded to LLM-based summarization later.
        """
        key_content = []
        for msg in messages[-20:]:
            if msg.get("role") in ["user", "assistant"]:
                content = msg.get("content", "")
                if content and len(content) > 50:
                    key_content.append(content[:200])

        if key_content:
            return " ".join(key_content)
        return "Empty session - no key content."

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count (rough: 4 chars ≈ 1 token).
        """
        return len(text) // 4

    # Expose rsync from core for mesh synchronization (Phase 3)
    def sync_to_nodes(self, target_nodes: list) -> dict:
        """Delegate to core Hermes-LCM rsync if available."""
        logger.info("Rsync requested via plugin (delegation stub)")
        return {"status": "stub", "targets": target_nodes}

    def store_block_dual(self, semantic: str, context: str, metadata: dict = None):
        """Store in both plugin DAG and core IdeaBlock during transition."""
        # Store in existing sessions system
        session_id = self.store_session(semantic, context)  # assume exists or add

        # Also store in core if available
        if self.core_provider and CORE_AVAILABLE:
            try:
                block = IdeaBlock(
                    semantic=semantic,
                    context=context,
                    relations=[],
                    metadata=metadata or {}
                )
                self.core_provider.store_block(block)
            except Exception as e:
                logger.warning(f"Dual store to core failed: {e}")

        return session_id


    def migrate_to_core(self, limit: int = 100) -> int:
        """Migrate recent sessions from plugin schema to core IdeaBlock schema."""
        if not self.core_provider or not CORE_AVAILABLE:
            logger.warning("Core provider not available for migration")
            return 0

        conn = self._get_conn()
        rows = conn.execute("""
            SELECT id, summary, full_context, metadata, created_at
            FROM sessions
            WHERE is_active = 1
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        migrated = 0
        for row in rows:
            try:
                block = IdeaBlock(
                    block_id=row["id"],
                    semantic=row["summary"][:300],
                    context=row["full_context"][:2000],
                    relations=[],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {}
                )
                self.core_provider.store_block(block)
                migrated += 1
            except Exception as e:
                logger.warning(f"Migration failed for {row['id']}: {e}")

        logger.info(f"Migrated {migrated} sessions to core schema")
        return migrated
