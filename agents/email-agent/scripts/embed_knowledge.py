"""Generate Alibaba Cloud Model Studio embeddings for pending knowledge chunks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from email_agent.tools.knowledge_base import (
    embed_texts,
    embedding_config,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} is missing. Copy .env.example to .env and set it."
        )
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed knowledge chunks that have no current embedding."
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1")

    load_dotenv(PROJECT_DIR / ".env")
    database_url = required_env("DATABASE_URL")
    _, _, model = embedding_config()
    where_clause = "TRUE" if args.force else "(embedding IS NULL OR embedding_model IS DISTINCT FROM %s)"
    limit_clause = " LIMIT %s" if args.limit is not None else ""
    parameters = (() if args.force else (model,)) + (
        (args.limit,) if args.limit is not None else ()
    )

    with psycopg.connect(database_url) as connection:
        register_vector(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, heading, content
                FROM knowledge_chunks
                WHERE {where_clause}
                ORDER BY id
                {limit_clause}
                """,
                parameters,
            )
            rows = cursor.fetchall()

        if not rows:
            print("No knowledge chunks require embedding.")
            return

        updated = 0
        for offset in range(0, len(rows), args.batch_size):
            batch = rows[offset : offset + args.batch_size]
            inputs = [f"{heading}\n\n{content}" for _, heading, content in batch]
            embeddings = embed_texts(inputs)

            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    UPDATE knowledge_chunks
                    SET embedding = %s,
                        embedding_model = %s,
                        embedded_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    [
                        (embedding, model, chunk_id)
                        for (chunk_id, _, _), embedding in zip(batch, embeddings)
                    ],
                )
            connection.commit()
            updated += len(batch)
            print(f"Embedded {updated}/{len(rows)} chunks.")


if __name__ == "__main__":
    main()
