"""Runtime resources shared by the web UI and command-line entrypoint."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from email_agent.config import get_settings
from email_agent.graph import compile_agent_app


@contextmanager
def postgres_agent_app() -> Iterator[Any]:
    """Create a graph backed by a process-safe PostgreSQL checkpointer."""
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for persistent LangGraph checkpoints.")

    pool = ConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    pool.open()
    try:
        pool.wait()
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        yield compile_agent_app(checkpointer)
    finally:
        pool.close()
