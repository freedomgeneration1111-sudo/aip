"""Manifest v1 schema — ADR-014 §6.

Pydantic v2 `BaseModel` for declarative validation + JSON Schema generation.
Already a project dependency (`pydantic>=2.0`).

Layer: adapter (lives under `aip.adapter.extensions`).

Contract (consumed by ExtensionHost stage 1 validate, and by on_load hooks
via `host.manifest`):
  - Manifest: top-level manifest model.
  - Contributes: the `contributes:` sub-block.
  - CorpusContribution: one entry in `contributes.corpora`.
  - GuiContribution: v1.1 GUI mount declaration (parsed but not mounted in v1.0).

Field semantics: see ADR-014 §6.1.

Pinned by tests/test_extension_lifecycle.py (manifest_version out of range →
FAILED; invalid config.schema → FAILED).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CorpusContribution(BaseModel):
    """One entry in `contributes.corpora` — ADR-014 §6.1."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., description="Logical role within the extension, e.g. 'textbook'. Must not contain ':'.")
    type: str = Field(..., description="CorpusType value: conversation | code | document | book.")
    sensitive: bool = Field(False, description="If True, requires session opt-in via allowed_restricted_corpora.")

    @field_validator("role")
    @classmethod
    def _role_no_colon(cls, v: str) -> str:
        if ":" in v:
            raise ValueError("corpus role must not contain ':' (used for {ext_id}:{role} namespacing)")
        return v

    @field_validator("type")
    @classmethod
    def _type_lowercase(cls, v: str) -> str:
        # CorpusType is a str enum with lowercase values; accept lowercase only.
        allowed = {"conversation", "code", "document", "book"}
        if v not in allowed:
            raise ValueError(f"corpus type {v!r} not in {sorted(allowed)}")
        return v


class GuiContribution(BaseModel):
    """The `gui:` sub-block — ADR-014 §6.1 (v1.1).

    Parsed in v1.0 so a manifest declaring `gui:` validates cleanly; the host
    does not mount GUI pages until v1.1.
    """

    model_config = ConfigDict(extra="forbid")

    nav: dict[str, Any] = Field(..., description="Nav entry: {label, icon, order}.")
    pages: str = Field(..., description="Python entry-point path 'pkg.module:fn'.")


class Contributes(BaseModel):
    """The `contributes:` sub-block — ADR-014 §6.1."""

    model_config = ConfigDict(extra="forbid")

    corpora: list[CorpusContribution] = Field(default_factory=list)
    actors: list[str] = Field(
        default_factory=list,
        description="Advisory list of actor names; actual registration happens in on_load.",
    )
    channels: list[str] = Field(
        default_factory=list,
        description="Advisory list of channel names; actual registration happens in on_load.",
    )
    workflows_dir: str = Field(..., description="Directory of YAML workflows, relative to the extension dir.")
    migrations: str = Field(..., description="Directory of .sql migrations, relative to the extension dir.")
    gui: GuiContribution | None = Field(None, description="v1.1 GUI mount declaration.")


class ConfigBlock(BaseModel):
    """The top-level `config:` block — ADR-014 §6.1, §6.4."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: str | None = Field(
        default=None,
        alias="schema",
        description="Python path 'pkg.module:Class' to a BaseSettings/dataclass. Loaded + validated at stage 1.",
    )


class Manifest(BaseModel):
    """Top-level manifest — ADR-014 §6.

    Validated at stage 1 (ExtensionHost.validate). A pydantic ValidationError
    transitions the extension to FAILED with a manifest-tagged failure reason.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_version: int = Field(..., description="Manifest schema version. Host checks against its supported range.")
    id: str = Field(..., description="Extension id. Immutable post-registration. Must not contain ':'.")
    name: str = Field(..., description="Human-readable name.")
    version: str = Field(..., description="Semver version string.")
    depends: list[str] = Field(default_factory=list, description="Reserved: list of extension ids. Not enforced in v1.")
    enabled: bool = Field(True, description="Manifest-declared default. Operator override is a v1.1 concern.")
    contributes: Contributes
    config: ConfigBlock = Field(default_factory=ConfigBlock)

    @field_validator("id")
    @classmethod
    def _id_no_colon(cls, v: str) -> str:
        if ":" in v:
            raise ValueError("extension id must not contain ':' (used for {ext_id}:{role} corpus namespacing)")
        if v == "definer":
            raise ValueError("extension id 'definer' is reserved (core anchor corpus)")
        return v

    def extension_dir(self, extensions_root: Path) -> Path:
        """Resolve the extension's directory given the operator-owned extensions root."""
        return extensions_root / self.id

    def workflows_path(self, extensions_root: Path) -> Path:
        """Resolve the workflows_dir relative to the extension directory."""
        return self.extension_dir(extensions_root) / self.contributes.workflows_dir

    def migrations_path(self, extensions_root: Path) -> Path:
        """Resolve the migrations dir relative to the extension directory."""
        return self.extension_dir(extensions_root) / self.contributes.migrations


# Pydantic v2 needs model_rebuild() when models reference each other (e.g.
# Contributes references GuiContribution, Manifest references Contributes).
# This resolves all forward references at import time.
Manifest.model_rebuild()
Contributes.model_rebuild()
