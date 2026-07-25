"""Custom-channel scoping — ADR-008 Rev 3.1 Amendment §A14.

Wraps custom retrieval channel registration so custom channels receive ONLY
the CorpusStores the registry resolved for the session (post Branham/allowlist
check). They never get a raw db_path or the container.

ADR-008 Rev 3.1 Amendment §A14:
  "Custom channel register_fns receive only the CorpusStores the registry
   resolved for the session (post Branham/allowlist check); they never get
   a raw db_path or the container. Add an acceptance test: a custom channel
   cannot reach restricted corpora without policy approval."

This module provides a wrapper that filters the stores passed to custom
channels, ensuring only session-resolved CorpusStores are visible.

Layer: adapter. Imports from foundation and adapter. Consumed by the
retrieval orchestrator wiring in ask_pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ScopedCorpusStores:
    """A read-only view of CorpusStores that only exposes session-resolved corpora.

    ADR-008 Rev 3.1 §A14: custom channels receive this wrapper instead of
    the raw container or a db_path. The wrapper only exposes stores for
    corpus_ids that the registry resolved for the session (after the
    Branham/allowlist check). A custom channel cannot reach restricted corpora without
    policy approval because they won't be in the resolved set unless the
    session has the allowlist.

    This is a defensive layer — even if a custom channel is buggy or
    malicious, it can only access the corpora the session is authorized
    to see.
    """

    def __init__(self, resolved_stores: dict[str, Any]):
        """Initialize with a dict of {corpus_id → CorpusStores}.

        Only the corpora in this dict are accessible. The dict is typically
        built by resolving active_corpus_ids through the registry with the
        session's allowed_restricted_corpora.
        """
        # Use object.__setattr__ to bypass __setattr__ if we were to use slots.
        # For a regular class, just store normally.
        self._resolved = dict(resolved_stores)

    def get_stores(self, corpus_id: str) -> Any:
        """Get CorpusStores for a corpus_id. Returns None if not in the resolved set.

        A custom channel calling this with a sensitive corpus_id will get None unless
        the corpus was in the session's active_corpus_ids AND the session had
        the allowlist (which the registry checked during resolution).
        """
        return self._resolved.get(corpus_id)

    @property
    def available_corpus_ids(self) -> list[str]:
        """Return the list of corpus_ids this scoped view can access."""
        return list(self._resolved.keys())

    def __contains__(self, corpus_id: str) -> bool:
        return corpus_id in self._resolved

    def __len__(self) -> int:
        return len(self._resolved)


async def resolve_scoped_stores(
    container: Any,
    active_corpus_ids: list[str],
    allowed_restricted_corpora: list[str] | None = None,
) -> ScopedCorpusStores:
    """Resolve active_corpus_ids through the registry into a ScopedCorpusStores.

    ADR-008 Rev 3.1 §A14: this is the resolution point where Branham
    isolation is enforced. Each corpus is fetched via
    registry.get_stores(corpus_id, allowed_restricted_corpora=...).
    RestrictedCorpusAccessViolation is caught and the corpus is simply omitted
    from the resolved set (graceful degrade, not an error).

    Returns a ScopedCorpusStores that custom channels can safely receive.
    """
    registry = getattr(container, "corpus_registry", None)
    if registry is None:
        return ScopedCorpusStores({})

    from aip.foundation.corpus_exceptions import RestrictedCorpusAccessViolation

    resolved: dict[str, Any] = {}
    for cid in active_corpus_ids:
        try:
            stores = await registry.get_stores(cid, allowed_restricted_corpora=allowed_restricted_corpora)
            resolved[cid] = stores
        except RestrictedCorpusAccessViolation:
            logger.info("scoped_stores_restricted_suppressed corpus=%s", cid)
            # Omit from resolved set — custom channel won't see it
        except Exception as exc:
            logger.warning("scoped_stores_resolve_failed corpus=%s error=%s", cid, exc)

    return ScopedCorpusStores(resolved)


def wrap_custom_channel_register(
    register_fn: Any,
    scoped_stores: ScopedCorpusStores,
) -> Any:
    """Wrap a custom channel register_fn so it only sees the scoped stores.

    ADR-008 Rev 3.1 §A14: custom channels receive the ScopedCorpusStores
    instead of the raw AskStores/container. The register_fn signature is
    (orchestrator, stores, config) — the stores arg is replaced with the
    ScopedCorpusStores.

    This is a thin wrapper. The custom channel's register_fn must be updated
    to expect a ScopedCorpusStores (which has get_stores(corpus_id) instead
    of direct store attributes). Built-in channels are NOT affected — they
    continue to receive the AskStores they expect.
    """

    def wrapped(orchestrator: Any, _stores: Any, config: dict | None = None) -> Any:
        return register_fn(orchestrator, scoped_stores, config)

    return wrapped
