from __future__ import annotations

import re


def normalize_text(value: str) -> str:
    """Collapse whitespace while preserving the legacy upload text contract."""
    return re.sub(r"\s+", " ", value).strip()


def clean_ai_text(value: object, max_length: int) -> str:
    """Normalize arbitrary model/document text and apply a deterministic limit."""
    text = normalize_text(str(value or ""))
    return text[:max_length]


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Return ordered, de-duplicated keyword hits using legacy semantics."""
    lowered = text.lower()
    hits: list[str] = []
    for keyword in keywords:
        if keyword.lower() in lowered and keyword not in hits:
            hits.append(keyword)
    return hits


def has_any_keyword(text: str, keywords: list[str]) -> bool:
    """Check keyword presence using the same case-insensitive substring rule."""
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
