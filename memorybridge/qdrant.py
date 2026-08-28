from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class QdrantError(RuntimeError):
    pass


class QdrantHTTP:
    """Small REST adapter. Qdrant is treated as a replaceable storage/index implementation."""

    def __init__(self, base_url: str, api_key: str = "", *, timeout: float = 30.0) -> None:
        headers = {"accept": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise QdrantError(f"Qdrant {method} {path} -> {response.status_code}: {response.text[:500]}")
        if not response.content:
            return None
        data = response.json()
        if isinstance(data, dict) and data.get("status") not in (None, "ok"):
            raise QdrantError(f"Qdrant operation failed: {data}")
        return data.get("result") if isinstance(data, dict) and "result" in data else data

    async def list_collections(self) -> list[str]:
        result = await self.request("GET", "/collections")
        return [str(row.get("name")) for row in (result or {}).get("collections", []) if row.get("name")]

    async def collection_exists(self, name: str) -> bool:
        response = await self._client.get(f"/collections/{quote(name, safe='')}")
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise QdrantError(response.text[:500])
        return True

    async def ensure_vectorless_collection(self, name: str) -> None:
        if await self.collection_exists(name):
            return
        try:
            await self.request("PUT", f"/collections/{quote(name, safe='')}", json={})
        except QdrantError:
            # A concurrent creator may have won the race.
            if not await self.collection_exists(name):
                raise

    async def ensure_vector_collection(self, name: str, *, size: int, distance: str = "Cosine") -> None:
        if await self.collection_exists(name):
            return
        try:
            await self.request(
                "PUT",
                f"/collections/{quote(name, safe='')}",
                json={"vectors": {"size": size, "distance": distance}},
            )
        except QdrantError:
            if not await self.collection_exists(name):
                raise

    async def ensure_integer_index(self, collection: str, field: str) -> None:
        try:
            await self.request(
                "PUT",
                f"/collections/{quote(collection, safe='')}/index?wait=true",
                json={"field_name": field, "field_schema": "integer"},
            )
        except QdrantError as exc:
            text = str(exc).lower()
            if "already" not in text and "exists" not in text:
                raise

    async def upsert_payload_point(self, collection: str, point_id: str | int, payload: dict[str, Any]) -> None:
        await self.request(
            "PUT",
            f"/collections/{quote(collection, safe='')}/points?wait=true",
            json={"points": [{"id": point_id, "payload": payload}]},
        )

    async def upsert_vector_point(
        self, collection: str, point_id: str | int, vector: list[float], payload: dict[str, Any]
    ) -> None:
        await self.request(
            "PUT",
            f"/collections/{quote(collection, safe='')}/points?wait=true",
            json={"points": [{"id": point_id, "vector": vector, "payload": payload}]},
        )

    async def set_payload(self, collection: str, point_id: str | int, payload: dict[str, Any]) -> None:
        await self.request(
            "POST",
            f"/collections/{quote(collection, safe='')}/points/payload?wait=true",
            json={"payload": payload, "points": [point_id]},
        )

    async def get_point(self, collection: str, point_id: str | int) -> dict[str, Any] | None:
        response = await self._client.get(f"/collections/{quote(collection, safe='')}/points/{point_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise QdrantError(response.text[:500])
        data = response.json()
        return data.get("result")

    async def scroll(
        self,
        collection: str,
        *,
        limit: int = 100,
        offset: str | int | None = None,
        filter_: dict[str, Any] | None = None,
        order_by: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str | int | None]:
        body: dict[str, Any] = {"limit": limit, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        if filter_:
            body["filter"] = filter_
        if order_by:
            body["order_by"] = order_by
        result = await self.request(
            "POST", f"/collections/{quote(collection, safe='')}/points/scroll", json=body
        )
        return list(result.get("points", [])), result.get("next_page_offset")

    async def query_vector(self, collection: str, vector: list[float], *, limit: int) -> list[dict[str, Any]]:
        path = f"/collections/{quote(collection, safe='')}/points/query"
        body = {"query": vector, "limit": limit, "with_payload": True, "with_vector": False}
        response = await self._client.post(path, json=body)
        if response.status_code < 400:
            data = response.json().get("result", {})
            return list(data.get("points", data if isinstance(data, list) else []))

        # Compatibility with Qdrant releases that still use /points/search for dense search.
        legacy = await self._client.post(
            f"/collections/{quote(collection, safe='')}/points/search",
            json={"vector": vector, "limit": limit, "with_payload": True, "with_vector": False},
        )
        if legacy.status_code >= 400:
            raise QdrantError(
                f"vector query failed ({response.status_code}/{legacy.status_code}): {legacy.text[:500]}"
            )
        return list(legacy.json().get("result", []))

    async def count(self, collection: str, *, filter_: dict[str, Any] | None = None) -> int:
        body: dict[str, Any] = {"exact": True}
        if filter_:
            body["filter"] = filter_
        result = await self.request(
            "POST", f"/collections/{quote(collection, safe='')}/points/count", json=body
        )
        return int(result.get("count", 0))

    async def create_snapshot(self, collection: str) -> dict[str, Any]:
        return await self.request("POST", f"/collections/{quote(collection, safe='')}/snapshots?wait=true")

    async def list_snapshots(self, collection: str) -> list[dict[str, Any]]:
        result = await self.request("GET", f"/collections/{quote(collection, safe='')}/snapshots")
        return list(result or [])

    async def download_snapshot_to(self, collection: str, snapshot_name: str, path: Any) -> None:
        url = f"/collections/{quote(collection, safe='')}/snapshots/{quote(snapshot_name, safe='')}"
        async with self._client.stream("GET", url, timeout=300) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise QdrantError(body.decode("utf-8", errors="replace")[:500])
            with open(path, "wb") as fh:
                async for chunk in response.aiter_bytes():
                    fh.write(chunk)
                fh.flush()
                try:
                    import os

                    os.fsync(fh.fileno())
                except OSError:
                    pass

    async def delete_snapshot(self, collection: str, snapshot_name: str) -> None:
        await self.request(
            "DELETE",
            f"/collections/{quote(collection, safe='')}/snapshots/{quote(snapshot_name, safe='')}?wait=true",
        )

    async def delete_collection(self, collection: str) -> None:
        await self.request("DELETE", f"/collections/{quote(collection, safe='')}?timeout=60")

    async def upload_snapshot_file(
        self, collection: str, path: Any, *, checksum: str | None = None
    ) -> Any:
        query = "priority=snapshot&wait=true"
        if checksum:
            query += f"&checksum={quote(checksum, safe='')}"
        with open(path, "rb") as fh:
            response = await self._client.post(
                f"/collections/{quote(collection, safe='')}/snapshots/upload?{query}",
                files={"snapshot": (getattr(path, "name", str(path)), fh, "application/octet-stream")},
                timeout=300,
            )
        if response.status_code >= 400:
            raise QdrantError(response.text[:500])
        body = response.json()
        return body.get("result", body)
