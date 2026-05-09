"""
SQLite DAG database for Hermes-LCM context summaries.

Core design:
- Sessions stored as nodes in a DAG (Directed Acyclic Graph)
- Each session has a summary, parent references, and metadata
- Enables query-based context retrieval across session history
- WAL mode for concurrent reads + writes
"""
