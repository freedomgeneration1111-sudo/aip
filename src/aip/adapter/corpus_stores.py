"""CorpusStores — per-corpus live store bundle.

ADR-008 Rev 3.1 §5.3 (fixes B-9 from Rev 2):

Regular class (NOT a dataclass) with explicit async lifecycle. All store
params are optional so the factory can construct an empty shell, init
write_lock/closed/deletion_state, then attach stores incrementally with
working partial-init cleanup.

The write_lock, closed, and deletion_state fields are set in __init__ so
close_all() is always safe — even on a shell built before any sub-store opens.
The factory uses the normal constructor, never __new__.

Layer: adapter. Holds live store objects (which hold live aiosqlite connections
via the shared CorpusConnectionManager). Imports from foundation only.

Contract (consumed by CorpusRegistry, which holds dict[corpus_id → CorpusStores]):
    stores = CorpusStores(corpus_id="definer", corpus_type=CorpusType.CONVERSATION,
                          connection_manager=manager)
    # Factory attaches stores incrementally:
    stores.turn_store = turn_store
    stores.lexical_store = lexical_store
    # ...
    # Registry lookups:
    async with stores.write_lock:
        await stores.turn_store.delete_turn(turn_id)
    # Cleanup:
    await stores.close_all()  # idempotent
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from aip.foundation.corpus_types import CorpusDeletionState, CorpusType

if TYPE_CHECKING:
    from aip.adapter.corpus_connection import CorpusConnectionManager

logger = logging.getLogger(__name__)


class CorpusStores:
    """Per-corpus live store bundle. Regular class with explicit async lifecycle.

    All six stores (turn_store, lexical_store, vector_store, graph_store,
    artifact_store, ecs_store) are optional in __init__ so the factory can
    build incrementally with partial-init cleanup. The write_lock, closed,
    and deletion_state fields are ALWAYS set in __init__.

    The connection_manager is shared by all six stores — they receive it
    instead of a db_path and use it for all connection access. This is the
    §A0 fix that makes the connection budget work (1 write + N read per
    corpus, not 6 × (1 + N)).
    """

    __slots__ = (
        "corpus_id",
        "corpus_type",
        "connection_manager",
        "turn_store",
        "lexical_store",
        "vector_store",
        "graph_store",
        "artifact_store",
        "ecs_store",
        "write_lock",
        "closed",
        "deletion_state",
        "_branham_policy_enabled",
    )

    def __init__(
        self,
        corpus_id: str,
        corpus_type: CorpusType,
        connection_manager: "CorpusConnectionManager | None" = None,
        turn_store: Any = None,
        lexical_store: Any = None,
        vector_store: Any = None,
        graph_store: Any = None,
        artifact_store: Any = None,
        ecs_store: Any = None,
    ) -> None:
        self.corpus_id: str = corpus_id
        self.corpus_type: CorpusType = corpus_type
        self.connection_manager: CorpusConnectionManager | None = connection_manager
        self.turn_store = turn_store
        self.lexical_store = lexical_store
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.write_lock: asyncio.Lock = asyncio.Lock()
        self.closed: bool = False
        self.deletion_state: CorpusDeletionState = CorpusDeletionState.ACTIVE
        # Branham policy flag — set by CorpusRegistry.register() when
        # branham_policy_enabled=True. Checked by get_stores() Layer 3.
        self._branham_policy_enabled: bool = False

    async def close_all(self) -> None:
        """Idempotent async close of all contained stores + connection manager.

        Safe to call on a partially-initialized CorpusStores (some stores
        may be None). Each store's close() is wrapped in try/except so one
        failure doesn't prevent others from closing. The connection_manager
        is closed last (after stores release their references to it).
        """
        if self.closed:
            return
        self.closed = True

        store_attrs = (
            "turn_store",
            "lexical_store",
            "vector_store",
            "graph_store",
            "artifact_store",
            "ecs_store",
        )
        for attr in store_attrs:
            store = getattr(self, attr, None)
            if store is not None and hasattr(store, "close"):
                try:
                    await store.close()
                except Exception as exc:
                    logger.warning(
                        "corpus_store_close_failed corpus=%s store=%s error=%s",
                        self.corpus_id,
                        attr,
                        exc,
                    )
                # Clear the reference so it can't be used after close
                setattr(self, attr, None)

        # Close the shared connection manager last
        if self.connection_manager is not None:
            try:
                await self.connection_manager.close()
            except Exception as exc:
                logger.warning(
                    "corpus_connection_manager_close_failed corpus=%s error=%s",
                    self.corpus_id,
                    exc,
                )
            self.connection_manager = None

        logger.debug("corpus_stores_closed corpus=%s", self.corpus_id)

    async def __aenter__(self) -> "CorpusStores":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close_all()

    def health(self) -> dict:
        """Return a health summary for /health endpoints."""
        return {
            "corpus_id": self.corpus_id,
            "corpus_type": self.corpus_type.value,
            "closed": self.closed,
            "deletion_state": self.deletion_state.value,
            "branham_policy_enabled": self._branham_policy_enabled,
            "has_turn_store": self.turn_store is not None,
            "has_lexical_store": self.lexical_store is not None,
            "has_vector_store": self.vector_store is not None,
            "has_graph_store": self.graph_store is not None,
            "has_artifact_store": self.artifact_store is not None,
            "has_ecs_store": self.ecs_store is not None,
            "has_connection_manager": self.connection_manager is not None,
            "connection_health": (self.connection_manager.health() if self.connection_manager else None),
        }
