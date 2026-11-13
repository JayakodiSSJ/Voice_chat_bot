from __future__ import annotations

import hashlib
import math
import re


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def clean_answer_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]

    compacted = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen:
                compacted.append("")
            blank_seen = True
            continue
        compacted.append(line)
        blank_seen = False

    return "\n".join(compacted).strip()


def extract_json_object(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return cleaned[start : end + 1]
    return cleaned


def hash_embedding(text: str, vector_size: int) -> list[float]:
    vector = [0.0] * vector_size
    tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % vector_size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def is_assigned_resource_refusal(text: str) -> bool:
    normalized = (text or "").lower()
    return (
        "i don't have that information in this agent's assigned resources" in normalized
        or "outside this agent's assigned resources" in normalized
    )


def is_general_chat(question: str) -> bool:
    text = re.sub(r"[^a-z0-9\s]", "", (question or "").strip().lower())
    text = re.sub(r"\s+", " ", text)
    if not text:
        return True
    greetings = {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "thanks",
        "thank you",
        "who are you",
        "what can you do",
    }
    if text in greetings:
        return True
    return len(text.split()) <= 3 and any(word in text.split() for word in {"hi", "hello", "hey", "thanks"})
