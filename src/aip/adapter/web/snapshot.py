"""In-memory snapshot and source stores for Web Source Acquisition (ADR-017 WS-1).

These in-memory implementations exist for CI: they let the snapshot /
dedup / list-by-query code paths run without a database.  The
SQLite-backed implementations land in WS-2 (snapshots) and WS-3
(sources) for production use.

Both stores deduplicate by ``content_hash``.  The contract is:

    - ``put`` with a hash that already exists returns the existing id
      and does NOT overwrite the stored record/bytes.
    - ``get`` returns ``None`` for unknown ids.
    - ``get_by_hash`` returns ``None`` for unknown hashes.
    - ``delete_expired`` / ``delete`` return counts/deleted-flags.

The stores are NOT thread-safe; they are bounded by the async event
loop and the caller (the fetcher / extractor pipeline) is expected
to await each call.  If concurrent puts ever become a concern, wrap
the inner dicts with ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from aip.foundation.protocols.web import WebSnapshotStore, WebSourceStore
from aip.foundation.schemas.web import WebSnapshotRecord, WebSourceRecord

# ---------------------------------------------------------------------------
# InMemoryWebSnapshotStore
# ---------------------------------------------------------------------------


class InMemoryWebSnapshotStore:
    """In-memory ``WebSnapshotStore`` for CI.

    Stores raw bytes keyed by ``snapshot_id`` and indexed by
    ``content_hash`` for dedup.  ``put`` with an existing hash returns
    the existing snapshot_id and ``deduplicated=True`` without storing
    new bytes.
    """

    def __init__(self) -> None:
        self._records: dict[str, WebSnapshotRecord] = {}
        self._bytes: dict[str, bytes] = {}
        self._by_hash: dict[str, str] = {}  # content_hash -> snapshot_id
        self._lock = asyncio.Lock()

    async def put(
        self,
        *,
        requested_url: str,
        final_url: str,
        retrieved_at: Any,
        content_type: str,
        content_hash: str,
        bytes_data: bytes,
    ) -> tuple[str, bool]:
        async with self._lock:
            existing_id = self._by_hash.get(content_hash)
            if existing_id is not None:
                return existing_id, True

            snapshot_id = f"snap_{len(self._records):08d}"
            record = WebSnapshotRecord(
                snapshot_id=snapshot_id,
                requested_url=requested_url,
                final_url=final_url,
                retrieved_at=retrieved_at,
                content_type=content_type,
                content_hash=content_hash,
                bytes_ref=f"memory:{snapshot_id}",
                bytes_size=len(bytes_data),
            )
            self._records[snapshot_id] = record
            self._bytes[snapshot_id] = bytes_data
            self._by_hash[content_hash] = snapshot_id
            return snapshot_id, False

    async def get(self, snapshot_id: str) -> WebSnapshotRecord | None:
        return self._records.get(snapshot_id)

    async def get_bytes(self, snapshot_id: str) -> bytes | None:
        return self._bytes.get(snapshot_id)

    async def get_by_hash(self, content_hash: str) -> WebSnapshotRecord | None:
        snapshot_id = self._by_hash.get(content_hash)
        if snapshot_id is None:
            return None
        return self._records.get(snapshot_id)

    async def delete_expired(self, cutoff: Any) -> int:
        if not isinstance(cutoff, datetime):
            raise TypeError(f"cutoff must be a datetime, got {type(cutoff)!r}")
        async with self._lock:
            to_delete = [
                sid for sid, rec in self._records.items() if rec.retrieved_at < cutoff
            ]
            for sid in to_delete:
                rec = self._records.pop(sid)
                self._bytes.pop(sid, None)
                self._by_hash.pop(rec.content_hash, None)
            return len(to_delete)


# ---------------------------------------------------------------------------
# InMemoryWebSourceStore
# ---------------------------------------------------------------------------


class InMemoryWebSourceStore:
    """In-memory ``WebSourceStore`` for CI.

    Stores ``WebSourceRecord`` keyed by ``source_id`` and indexed by
    ``content_hash`` for dedup.  ``put`` with an existing hash returns
    the existing source_id without storing a new record.
    """

    def __init__(self) -> None:
        self._records: dict[str, WebSourceRecord] = {}
        self._by_hash: dict[str, str] = {}  # content_hash -> source_id
        self._by_query: dict[str, list[str]] = {}  # query -> [source_id, ...] (insertion order)
        self._lock = asyncio.Lock()

    async def put(self, record: WebSourceRecord) -> str:
        async with self._lock:
            existing_id = self._by_hash.get(record.content_hash)
            if existing_id is not None:
                return existing_id

            self._records[record.source_id] = record
            self._by_hash[record.content_hash] = record.source_id

            query = record.search_result.query if record.search_result else ""
            if query:
                self._by_query.setdefault(query, []).append(record.source_id)

            return record.source_id

    async def get(self, source_id: str) -> WebSourceRecord | None:
        return self._records.get(source_id)

    async def get_by_hash(self, content_hash: str) -> WebSourceRecord | None:
        sid = self._by_hash.get(content_hash)
        if sid is None:
            return None
        return self._records.get(sid)

    async def list_by_query(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[WebSourceRecord]:
        """List source records produced by a given search query.

        When ``query`` is empty, returns the most-recent records across
        ALL queries (used by the /sources panel for a recent-activity view).
        """
        if not query:
            # Empty query → return most-recent records across all queries.
            # _records is insertion-ordered (oldest first); reverse for
            # most-recent first.
            all_records = list(reversed(self._records.values()))
            return all_records[: max(0, limit)]

        ids = self._by_query.get(query, [])
        # Most-recent first: insertion order is oldest-first, so reverse.
        ids_reversed = list(reversed(ids))
        capped = ids_reversed[: max(0, limit)]
        return [self._records[sid] for sid in capped if sid in self._records]

    async def delete(self, source_id: str) -> bool:
        async with self._lock:
            rec = self._records.pop(source_id, None)
            if rec is None:
                return False
            self._by_hash.pop(rec.content_hash, None)
            query = rec.search_result.query if rec.search_result else ""
            if query:
                ids = self._by_query.get(query, [])
                if source_id in ids:
                    ids.remove(source_id)
                    if not ids:
                        self._by_query.pop(query, None)
                    else:
                        self._by_query[query] = ids
            return True


# Static protocol checks (cheap; verifies the in-memory impls satisfy
# the Protocols at import time so a future refactor that breaks the
# contract fails loudly).
assert isinstance(InMemoryWebSnapshotStore(), WebSnapshotStore), (
    "InMemoryWebSnapshotStore must satisfy the WebSnapshotStore Protocol"
)
assert isinstance(InMemoryWebSourceStore(), WebSourceStore), (
    "InMemoryWebSourceStore must satisfy the WebSourceStore Protocol"
)


__all__ = [
    "InMemoryWebSnapshotStore",
    "InMemoryWebSourceStore",
]
