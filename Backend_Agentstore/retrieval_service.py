from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse

from agent_configuration_service import company_kb_collection_id
from openai_service import openai_service

load_dotenv()


class RetrievalService:
    def __init__(self):
        self.qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY") or None
        self.embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small").strip() or "text-embedding-3-small"
        self.vector_size = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS") or os.getenv("QDRANT_VECTOR_SIZE", "1536"))
        self.min_similarity_score = self._minimum_similarity_score()
        self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)

    @staticmethod
    def _minimum_similarity_score() -> float:
        """Return the cosine-similarity floor for KB context.

        The score is configurable so it can be tuned for a workspace's
        content. A malformed value falls back to the safe default.
        """
        try:
            score = float(os.getenv("KB_MIN_SIMILARITY_SCORE", "0.60"))
        except (TypeError, ValueError):
            return 0.60
        return min(1.0, max(-1.0, score))

    def _filter_by_similarity(self, points):
        filtered = []
        for point in points:
            try:
                score = float(getattr(point, "score", None))
            except (TypeError, ValueError):
                continue
            if score >= self.min_similarity_score:
                filtered.append(point)
        return filtered

    def collection_name(self, company_id: int) -> str:
        return company_kb_collection_id(company_id)

    def search_points(self, *, company_id: int, agent_id: int, question: str, top_k: int = 5):
        collection = self.collection_name(company_id)
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for knowledge-base retrieval.")
        query_vector = openai_service.create_embeddings(
            api_key=api_key,
            model=self.embedding_model,
            inputs=[question],
            dimensions=self.vector_size,
        )[0]
        query_filter = qdrant_models.Filter(
            must=[
                qdrant_models.FieldCondition(
                    key="agent_id",
                    match=qdrant_models.MatchValue(value=agent_id),
                ),
                qdrant_models.FieldCondition(
                    key="embedding_model",
                    match=qdrant_models.MatchValue(value=self.embedding_model),
                ),
                qdrant_models.FieldCondition(
                    key="embedding_dimensions",
                    match=qdrant_models.MatchValue(value=self.vector_size),
                ),
            ]
        )
        search_error: Exception | None = None
        try:
            points = self.client.search(
                collection_name=collection,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )
            return self._filter_by_similarity(points)
        except UnexpectedResponse as exc:
            search_error = exc
            if exc.status_code not in {404, 405, 410}:
                raise

        try:
            response = self.client.query_points(
                collection_name=collection,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return []
            raise search_error or exc from exc
        return self._filter_by_similarity(response.points)

    def search(self, *, company_id: int, agent_id: int, question: str, top_k: int = 5) -> dict[str, Any]:
        result = self.search_points(
            company_id=company_id,
            agent_id=agent_id,
            question=question,
            top_k=top_k,
        )
        return {
            "answer": "\n\n".join((point.payload or {}).get("text", "") for point in result),
            "sources": [point.payload for point in result],
        }


retrieval_service = RetrievalService()
