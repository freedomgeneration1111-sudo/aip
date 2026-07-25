"""L4 trajectory regulation and context reset.

Renamed from orchestration/trajectory/ to orchestration/l4_regulation/
on 2026-07-23 (DEBT-023) to avoid naming collision with ADR-015's
trajectory corpus (memory storage — different concern). The function
names (regulate_trajectory, etc.) are kept for backward compat.
"""

from .context_reset import execute_context_reset, inject_deterministic_recovery
from .regulator import regulate_trajectory, should_intervene

__all__ = [
    "execute_context_reset",
    "inject_deterministic_recovery",
    "regulate_trajectory",
    "should_intervene",
]
