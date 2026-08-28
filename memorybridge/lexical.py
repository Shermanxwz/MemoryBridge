from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable

from .models import SearchHit

IDENT_RE = re.compile(r"[A-Za-z0-9_./:@-]+|[\u3400-\u9fff]")


def tokens(text: str) -> list[str]:
    base = [m.group(0).lower() for m in IDENT_RE.finditer(text)]
    cjk = [t for t in base if len(t) == 1 and "\u3400" <= t <= "\u9fff"]
    grams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return base + grams


def lexical_rank(query: str, docs: Iterable[tuple[str, str, str, dict]], *, limit: int) -> list[SearchHit]:
    """Deterministic no-model fallback. Intentionally simple and rebuild-free."""
    q = query.strip().lower()
    q_tokens = tokens(q)
    if not q_tokens:
        return []
    q_counts = Counter(q_tokens)
    ranked: list[SearchHit] = []
    for point_id, content, collection, payload in docs:
        text = content.lower()
        d_counts = Counter(tokens(text))
        overlap = 0.0
        for tok, qn in q_counts.items():
            dn = d_counts.get(tok, 0)
            if dn:
                overlap += min(qn, dn) * (1.0 + math.log1p(len(tok)))
        exact = 6.0 if q in text else 0.0
        identifier = 0.0
        for tok in q_tokens:
            if any(ch in tok for ch in "_./:@-") and tok in text:
                identifier += 2.5
        length_penalty = 1.0 / (1.0 + math.log1p(max(1, len(d_counts))))
        score = exact + identifier + overlap * (0.75 + 0.25 * length_penalty)
        if score > 0:
            ranked.append(
                SearchHit(id=str(point_id), content=content, score=score, collection=collection, payload=payload)
            )
    ranked.sort(key=lambda h: (-h.score, h.id))
    return ranked[:limit]
