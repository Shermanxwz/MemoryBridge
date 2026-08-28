from __future__ import annotations

import math

import httpx


class EmbeddingUnavailable(RuntimeError):
    pass


class OpenAICompatibleEmbedding:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        expected_dim: int | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.expected_dim = expected_dim
        self._client = httpx.AsyncClient(headers=headers, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def embed(self, text: str) -> list[float]:
        body: dict[str, object] = {"model": self.model, "input": text}
        if self.expected_dim:
            body["dimensions"] = self.expected_dim
        try:
            response = await self._client.post(f"{self.base_url}/embeddings", json=body)
            response.raise_for_status()
            raw = response.json()["data"][0]["embedding"]
            vector = [float(x) for x in raw]
        except Exception as exc:  # provider/network errors are deliberately degradable
            raise EmbeddingUnavailable(f"embedding unavailable: {type(exc).__name__}: {exc}") from exc
        if not vector or all(v == 0.0 for v in vector):
            raise EmbeddingUnavailable("embedding provider returned an empty/all-zero vector")
        if any(math.isnan(v) or math.isinf(v) for v in vector):
            raise EmbeddingUnavailable("embedding provider returned NaN/Inf")
        if self.expected_dim is not None and len(vector) != self.expected_dim:
            raise EmbeddingUnavailable(
                f"embedding dimension mismatch: expected {self.expected_dim}, got {len(vector)}"
            )
        return vector
