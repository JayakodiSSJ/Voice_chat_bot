from __future__ import annotations

from typing import Any

import requests
from dotenv import load_dotenv

from agent_runtime_utils import clean_answer_text
from token_usage import record_token_usage

load_dotenv()


class OpenAIService:
    def create_embeddings(
        self,
        *,
        api_key: str,
        model: str,
        inputs: list[str],
        dimensions: int | None = None,
    ) -> list[list[float]]:
        if not inputs:
            return []

        body: dict[str, Any] = {
            "model": model,
            "input": inputs,
            "encoding_format": "float",
        }
        if dimensions and model.startswith("text-embedding-3"):
            body["dimensions"] = dimensions

        response = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:2000]}

        if not response.ok:
            message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else None
            raise RuntimeError(message or f"OpenAI embeddings request failed with status {response.status_code}.")

        record_token_usage(
            response_id=None,
            model=data.get("model") or model,
            usage=data.get("usage"),
        )

        embeddings_by_index: dict[int, list[float]] = {}
        for item in data.get("data", []) or []:
            index = item.get("index")
            embedding = item.get("embedding")
            if isinstance(index, int) and isinstance(embedding, list):
                embeddings_by_index[index] = [float(value) for value in embedding]

        embeddings = [embeddings_by_index[index] for index in range(len(inputs)) if index in embeddings_by_index]
        if len(embeddings) != len(inputs):
            raise RuntimeError("OpenAI embeddings response did not include all requested vectors.")
        return embeddings

    def call_text(
        self,
        *,
        api_key: str,
        model: str,
        instructions: str,
        input_messages: list[dict[str, Any]],
        max_output_tokens: int,
    ) -> str:
        print(f"OpenAI request model: {model}")
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "instructions": instructions,
                "input": input_messages,
                "max_output_tokens": max_output_tokens,
            },
            timeout=45,
        )
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:2000]}

        if not response.ok:
            message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else None
            raise RuntimeError(message or f"OpenAI request failed with status {response.status_code}.")

        record_token_usage(
            response_id=data.get("id"),
            model=data.get("model") or model,
            usage=data.get("usage"),
        )

        output_text = data.get("output_text")
        if output_text:
            return clean_answer_text(output_text)

        parts: list[str] = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") in {"output_text", "text"}:
                    parts.append(content.get("text") or "")
        answer = clean_answer_text("\n".join(parts))
        if not answer:
            raise RuntimeError("OpenAI response did not include text output.")
        return answer


openai_service = OpenAIService()
