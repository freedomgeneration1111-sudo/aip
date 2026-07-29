"""Compatibility barrel file — re-exports all protocol types.

This module preserves backward compatibility so that existing imports like::

    from aip.foundation.protocols import VectorStore, EmbeddingProvider, AuthStore

continue to work unchanged.

The actual definitions live in domain-specific sub-modules:
    storage, model, auth, budget, actors, knowledge, plugin, corpus_registry
"""

from __future__ import annotations

# -- actors --
from .actors import (
    Actor,
    ActorContext,
    ActorResult,
    VigilStore,
)

# -- auth --
from .auth import (
    AuthStore,
    AutonomyGate,
)

# -- budget --
from .budget import (
    BudgetStore,
)

# -- corpus_registry (ADR-008 Multi-Corpus) --
from .corpus_registry import (
    CorpusRegistryProtocol,
    ReviewItem,
)

# -- knowledge --
from .knowledge import (
    KnowledgeStore,
)

# -- model --
from .model import (
    EmbeddingProvider,
    ModelProvider,
)

# -- plugin --
from .plugin import (
    PluginProvider,
)

# -- storage --
from .storage import (
    ArtifactStore,
    CanonicalStore,
    EcsStore,
    EntityStore,
    EventStore,
    GraphStore,
    LexicalStore,
    ProjectStore,
    SessionStore,
    TraceStore,
    VectorStore,
)

# -- web (ADR-017) --
from .web import (
    ContentExtractor,
    SearchProvider,
    WebFetcher,
    WebSnapshotStore,
    WebSourceStore,
)

__all__ = [
    # storage
    "VectorStore",
    "LexicalStore",
    "CanonicalStore",
    "ArtifactStore",
    "TraceStore",
    "EntityStore",
    "EventStore",
    "ProjectStore",
    "EcsStore",
    "SessionStore",
    "GraphStore",
    # model
    "ModelProvider",
    "EmbeddingProvider",
    # auth
    "AutonomyGate",
    "AuthStore",
    # budget
    "BudgetStore",
    # actors
    "VigilStore",
    "Actor",
    "ActorContext",
    "ActorResult",
    # knowledge
    "KnowledgeStore",
    # plugin
    "PluginProvider",
    # corpus_registry (ADR-008 Multi-Corpus)
    "CorpusRegistryProtocol",
    "ReviewItem",
    # web (ADR-017)
    "SearchProvider",
    "WebFetcher",
    "ContentExtractor",
    "WebSnapshotStore",
    "WebSourceStore",
]
