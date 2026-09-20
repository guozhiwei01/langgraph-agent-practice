"""Alibaba Cloud embeddings and PostgreSQL/pgvector knowledge retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import os

import psycopg
from dotenv import load_dotenv
from openai import OpenAI
from pgvector import Vector
from pgvector.psycopg import register_vector

from email_agent.config import PROJECT_DIR


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "text-embedding-v4"
EMBEDDING_DIMENSIONS = 1024


@dataclass(frozen=True)
class KnowledgeHit:
    document_slug: str
    title: str
    heading: str
    content: str
    source_uri: str
    similarity: float
    is_demo: bool


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is missing from the Email Agent environment.")
    return value


def embedding_config() -> tuple[str, str, str]:
    """Return the configured API key, endpoint and model without logging secrets."""
    load_dotenv(PROJECT_DIR / ".env")
    dimensions = int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSIONS", "1024"))
    if dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError("Embedding dimensions must match VECTOR(1024).")
    return (
        _required_env("DASHSCOPE_API_KEY"),
        os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
        os.getenv("DASHSCOPE_EMBEDDING_MODEL", DEFAULT_MODEL),
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed nonempty texts, preserving the input order reported by the API."""
    if not texts or any(not text.strip() for text in texts):
        raise ValueError("Embedding input must contain nonempty text.")
    api_key, base_url, model = embedding_config()
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=2)
    response = client.embeddings.create(
        model=model,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
        encoding_format="float",
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    if [item.index for item in ordered] != list(range(len(texts))):
        raise RuntimeError("Embedding response indexes do not match inputs.")
    vectors = [item.embedding for item in ordered]
    if any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
        raise RuntimeError("Embedding response dimension does not match VECTOR(1024).")
    return vectors


def search_knowledge(
    query: str,
    *,
    category: str | None = None,
    limit: int = 2,
    min_similarity: float = 0.35,
) -> list[KnowledgeHit]:
    """Return chunks from the best matching document for the current single-issue flow."""
    if not query.strip():
        return []
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20.")
    if not -1 <= min_similarity <= 1:
        raise ValueError("min_similarity must be between -1 and 1.")

    load_dotenv(PROJECT_DIR / ".env")
    database_url = _required_env("DATABASE_URL")
    _, _, model = embedding_config()
    vector = Vector(embed_texts([query])[0])

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        register_vector(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH ranked AS (
                    SELECT document.id AS document_id, document.slug,
                           document.title, chunk.heading, chunk.content,
                           document.source_uri,
                           1 - (chunk.embedding <=> %s) AS similarity,
                           COALESCE((document.metadata->>'demo')::boolean, false) AS is_demo,
                           chunk.id AS chunk_id
                    FROM knowledge_chunks AS chunk
                    JOIN knowledge_documents AS document ON document.id = chunk.document_id
                    WHERE document.status = 'published'
                      AND chunk.embedding IS NOT NULL
                      AND chunk.embedding_model = %s
                      AND (%s::text IS NULL OR document.category = %s)
                ), best_document AS (
                    SELECT document_id
                    FROM ranked
                    GROUP BY document_id
                    HAVING MAX(similarity) >= %s
                    ORDER BY MAX(similarity) DESC
                    LIMIT 1
                )
                SELECT slug, title, heading, content, source_uri, similarity, is_demo
                FROM ranked
                WHERE document_id = (SELECT document_id FROM best_document)
                ORDER BY similarity DESC, chunk_id
                LIMIT %s
                """,
                (vector, model, category, category, min_similarity, limit),
            )
            return [KnowledgeHit(*row) for row in cursor.fetchall()]


def query_knowledge_base(query: str, *, category: str | None = None) -> list[str]:
    """Adapt structured hits to the current draft node's list[str] state."""
    return [
        (
            f"[{'DEMO / fictional policy' if hit.is_demo else 'Knowledge base'}; "
            f"source: {hit.source_uri}; similarity: {hit.similarity:.3f}] "
            f"{hit.title} — {hit.heading}\n{hit.content}"
        )
        for hit in search_knowledge(query, category=category)
    ]
