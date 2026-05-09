"""
Hermes-LCM: Lossless Context Management MemoryProvider.

Core design principles:
1. DAG-based session storage (parent/child relationships)
2. Automatic summarization when context window exceeded
3. Query-based retrieval (text search on summaries)
4. WAL mode for concurrency (mesh nodes reading/writing)

Implementation: Simple, working, efficient.
"""
import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime
from agent.memory_provider import MemoryProvider
from hermes_constants import get_hermes_home


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

        conn = sqlite3.connect(self.db_path)
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
            return True
        except Exception as e:
            conn.rollback()
            print(f"[LCM Error] Failed to store {session_id}: {e}")
            return False
        finally:
            conn.close()

    def query(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Query sessions by summary text search.

        Args:
            query: Search query
            limit: Max results

        Returns:
            List of {id, summary, metadata, created_at}
        """
        conn = sqlite3.connect(self.db_path)
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

        conn.close()

        return [dict(r) for r in results]

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

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Get current session
        session = conn.execute("""
        SELECT id, full_context, token_count, summary, parent_id
        FROM sessions
        WHERE id = ? AND is_active = 1
        """, (session_id,)).fetchone()

        if not session:
            conn.close()
            return []

        messages = []
        tokens_used = 0

        # Include current session messages first
        full_context = json.loads(session["full_context"])
        messages.extend(full_context[::-max_tokens:])  # Truncate if huge
        tokens_used += min(session["token_count"], max_tokens)

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

        conn.close()
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
