"""Durable checkpointing: Postgres when ``DATABASE_URL`` is set, in-memory otherwise.

With the Postgres saver every super-step of the graph is persisted, so a run suspended at the
human-approval interrupt survives a process restart and can be resumed by thread id from any
replica.
"""

from __future__ import annotations

import os

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver


def make_checkpointer(database_url: str | None = None) -> BaseCheckpointSaver:
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        return InMemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    conn = Connection.connect(database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    saver = PostgresSaver(conn)
    saver.setup()  # idempotent: creates/migrates the checkpoint tables
    return saver
