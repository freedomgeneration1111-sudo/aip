"""Compatibility barrel file — re-exports all schema types.

This module preserves backward compatibility so that existing imports like::

    from aip.foundation.schemas import Chunk, EcsState, ReviewVerdict

continue to work unchanged.

The actual definitions live in domain-specific sub-modules:
    base, retrieval, review, evaluation, trajectory,
    workflow, vector, auth, budget, surface, config, ingestion, artifact
"""

from __future__ import annotations

# -- artifact --
from .artifact import (
    ArtifactLedgerEntry,
    ArtifactMetadata,
    ArtifactType,
    ReviewQueueSummary,
)

# -- ask --
from .ask import (
    AskResult,
    AskSource,
    SourceReference,
)

# -- auth --
from .auth import (
    AuthConfig,
    AuthRole,
    AutonomyEscalation,
    AutonomyLevel,
    CollaboratorConfig,
    CollaboratorRole,
    McpAutonomyLevel,
    RateLimitConfig,
    coerce_autonomy_level,
    coerce_mcp_autonomy_level,
)

# -- base --
from .base import (
    ContractRule,
    ContractTier,
    EcsState,
    EcsTransition,
    Event,
    FailureType,
    FailureTypeCode,
    ModelSlotName,
    OutcomeType,
    ReleaseMetadata,
    VigilHealthStatus,
)

# -- budget --
from .budget import (
    BudgetConfig,
    BudgetScope,
)

# -- codex --
from .codex import (
    CodexConfig,
    CodexContradiction,
    CodexDashboard,
    CodexSource,
    CodexSourceStatus,
    CodexTopic,
    ContradictionSeverity,
    ContradictionStatus,
)

# -- config --
from .config import (
    PerformanceConfig,
    PluginConfig,
    PluginStatus,
)

# -- corpus (turn-level) --
from .corpus_turn import (
    CorpusTurn,
    make_turn_id,
)

# -- evaluation --
from .evaluation import (
    AcePlaybookEntry,
    CompilationState,
    DomainCoherenceResult,
    EvaluationScore,
    FailureClassification,
    FaithfulnessResult,
    KnowledgeCompilationConfig,
    SextonConfig,
)

# -- ingestion --
from .ingestion import (
    ConversationTurn,
    ImportedConversation,
    IngestionResult,
    SourceFormat,
)

# -- retrieval --
from .retrieval import (
    Chunk,
    RetrievalResult,
)

# -- review --
from .review import (
    CanonicalPromotionConfig,
    ReviewContext,
    ReviewQueueEntry,
    ReviewVerdict,
    VigilConfig,
)

# -- surface --
from .surface import (
    ApiRoute,
    ChatMessage,
    McpToolDef,
    SurfaceConfig,
)

# -- trajectory --
from .trajectory import (
    SessionContext,
    TrajectorySignal,
    TrajectorySignalType,
)

# -- vector --
from .vector import (
    MigrationCheckpoint,
    MigrationStatus,
    PgvectorConfig,
    VectorBackendStatus,
    VectorBackendType,
    VectorDegradationInfo,
)

# -- web (ADR-017) --
from .web import (
    ExtractedDocument,
    FetchedResource,
    FetchPolicy,
    SearchOptions,
    SearchResult,
    WebProviderConfig,
    WebSnapshotRecord,
    WebSourceRecord,
    normalize_text_for_hash,
    sha256_hex,
)

# -- workflow --
from .workflow import (
    BeastCadenceConfig,
    DeploymentProfile,
    ModelSlotConfig,
    RoutingWeight,
    WorkflowTemplate,
)

__all__ = [
    # base
    "ContractTier",
    "ContractRule",
    "EcsState",
    "ModelSlotName",
    "FailureType",
    "OutcomeType",
    "FailureTypeCode",
    "VigilHealthStatus",
    "EcsTransition",
    "Event",
    "ReleaseMetadata",
    # retrieval
    "Chunk",
    "RetrievalResult",
    # review
    "ReviewVerdict",
    "ReviewContext",
    "ReviewQueueEntry",
    "VigilConfig",
    "CanonicalPromotionConfig",
    # evaluation
    "CompilationState",
    "EvaluationScore",
    "FaithfulnessResult",
    "DomainCoherenceResult",
    "SextonConfig",
    "AcePlaybookEntry",
    "FailureClassification",
    "KnowledgeCompilationConfig",
    # trajectory
    "TrajectorySignalType",
    "TrajectorySignal",
    "SessionContext",
    # workflow
    "ModelSlotConfig",
    "RoutingWeight",
    "BeastCadenceConfig",
    "WorkflowTemplate",
    "DeploymentProfile",
    # corpus (turn-level)
    "CorpusTurn",
    "make_turn_id",
    # ask
    "AskSource",
    "SourceReference",
    "AskResult",
    # ingestion
    "SourceFormat",
    "ConversationTurn",
    "ImportedConversation",
    "IngestionResult",
    # vector
    "VectorBackendType",
    "VectorBackendStatus",
    "VectorDegradationInfo",
    "PgvectorConfig",
    "MigrationStatus",
    "MigrationCheckpoint",
    # auth
    "AutonomyLevel",
    "McpAutonomyLevel",
    "AuthRole",
    "CollaboratorRole",
    "coerce_autonomy_level",
    "coerce_mcp_autonomy_level",
    "AutonomyEscalation",
    "AuthConfig",
    "RateLimitConfig",
    "CollaboratorConfig",
    # budget
    "BudgetScope",
    "BudgetConfig",
    # surface
    "SurfaceConfig",
    "ApiRoute",
    "McpToolDef",
    "ChatMessage",
    # config
    "PluginStatus",
    "PluginConfig",
    "PerformanceConfig",
    # artifact
    "ArtifactType",
    "ArtifactMetadata",
    "ArtifactLedgerEntry",
    "ReviewQueueSummary",
    # codex
    "CodexSourceStatus",
    "ContradictionSeverity",
    "ContradictionStatus",
    "CodexSource",
    "CodexTopic",
    "CodexContradiction",
    "CodexDashboard",
    "CodexConfig",
    # web (ADR-017)
    "SearchOptions",
    "SearchResult",
    "FetchPolicy",
    "FetchedResource",
    "ExtractedDocument",
    "WebSourceRecord",
    "WebSnapshotRecord",
    "WebProviderConfig",
    "sha256_hex",
    "normalize_text_for_hash",
]
