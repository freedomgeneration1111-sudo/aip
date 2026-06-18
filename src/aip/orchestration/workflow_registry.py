"""WorkflowRegistry.

Discovers YAML workflow templates (frontmatter + body) beyond the default 0.1 template.
Used by admin console and CLI.

Updated for WorkflowTemplate schema: template_id, name, description, yaml_path,
trigger, domains, model_gen_assumption.

ADR-014 §5.4: `add_path(dir)` supports per-extension workflow directories.
Extensions contributing workflows via their manifest's `workflows_dir` field
are globbed and merged into the registry at host stage 3 (register).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from aip.foundation.schemas import WorkflowTemplate

logger = logging.getLogger(__name__)


class WorkflowRegistry:
    """Registry for extended workflow templates."""

    def __init__(self, workflows_dir: str = "workflows") -> None:
        self.workflows_dir = Path(workflows_dir)
        self._templates: dict[str, WorkflowTemplate] = {}
        # Track the source directory for each template so load_workflow()
        # can resolve yaml_path correctly when templates come from multiple
        # dirs (the default workflows/ + per-extension dirs via add_path).
        self._template_source_dirs: dict[str, Path] = {}
        self._load_templates(self.workflows_dir)

    def _load_templates(self, source_dir: Path) -> None:
        """Glob *.yaml from source_dir and merge into self._templates.

        ADR-014 §5.4: called once for the default workflows_dir in __init__,
        and again for each extension's workflows_dir via add_path(). Templates
        are keyed by template_id; later loads overwrite earlier ones (last
        wins — extension workflows can override defaults if they declare the
        same template_id, which is rare and intentional).

        Parse failures are LOGGED, not silently swallowed. Previously this
        method had `except Exception: continue` which made malformed YAMLs
        invisible to operators. Now every failure is a WARNING with the file
        path and exception, so a broken contributed workflow is debuggable.
        """
        if not source_dir.exists():
            return
        for yaml_file in source_dir.glob("*.yaml"):
            try:
                with open(yaml_file, "r") as f:
                    content = f.read()
                    data = yaml.safe_load(content) or {}

                    # Support comment-based frontmatter (the style used in 9.3 templates)
                    if not data or "template_id" not in data:
                        meta = {}
                        for line in content.splitlines():
                            line = line.strip()
                            if line.startswith("# template_id:"):
                                meta["template_id"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# name:"):
                                meta["name"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# description:"):
                                meta["description"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# trigger:"):
                                meta["trigger"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# domains:"):
                                domains_str = line.split(":", 1)[1].strip()
                                meta["domains"] = [d.strip() for d in domains_str.split(",") if d.strip()]
                        if "template_id" in meta:
                            data = meta

                    if data and "template_id" in data:
                        tid = data["template_id"]
                        # Store yaml_path relative to the source dir for the
                        # default dir, and as an absolute path for extension
                        # dirs (so load_workflow can resolve either way).
                        if yaml_file.is_relative_to(source_dir):
                            yaml_path = str(yaml_file.relative_to(source_dir))
                        else:
                            yaml_path = str(yaml_file)
                        self._templates[tid] = WorkflowTemplate(
                            template_id=tid,
                            name=data.get("name", yaml_file.stem),
                            description=data.get("description", ""),
                            yaml_path=yaml_path,
                            trigger=data.get("trigger", "manual"),
                            domains=data.get("domains", []),
                        )
                        self._template_source_dirs[tid] = source_dir
            except Exception as exc:
                # ADR-014: log the failure with the file path so operators
                # can debug malformed contributed workflows. Previously this
                # was a silent `except Exception: continue`.
                logger.warning(
                    "workflow_template_parse_failed file=%s error=%s:%s",
                    yaml_file,
                    type(exc).__name__,
                    exc,
                )

        # Always include the default synthesis session template (only when
        # loading the default dir, not extension dirs).
        if source_dir == self.workflows_dir and "synthesis_session_v1" not in self._templates:
            self._templates["synthesis_session_v1"] = WorkflowTemplate(
                template_id="synthesis_session_v1",
                name="Synthesis Session v1",
                description="Original synthesis workflow",
                yaml_path="synthesis_session_v1.yaml",
            )
            self._template_source_dirs["synthesis_session_v1"] = self.workflows_dir

    def add_path(self, dir: Path) -> None:
        """Add another directory to the glob set and re-glob it immediately.

        ADR-014 §5.4: per-extension workflow dirs are merged into the registry
        at host stage 3 (register). Templates are keyed by template_id; an
        extension can override a default template by declaring the same id
        (last-wins, intentional).

        Args:
            dir: directory to glob for *.yaml workflow templates.

        Raises:
            Nothing — parse failures are logged as warnings (see
            _load_templates). A missing dir is a no-op (logged at debug).
        """
        dir = Path(dir)
        if not dir.exists():
            logger.debug("workflow_add_path_missing dir=%s — no workflows loaded", dir)
            return
        self._load_templates(dir)
        logger.info(
            "workflow_path_added dir=%s total_templates=%d",
            dir,
            len(self._templates),
        )

    def list_templates(self) -> list[WorkflowTemplate]:
        return list(self._templates.values())

    def get_template(self, template_id: str) -> WorkflowTemplate | None:
        return self._templates.get(template_id)

    def load_workflow(self, template_id: str) -> dict:
        tmpl = self.get_template(template_id)
        if not tmpl:
            raise ValueError(f"Unknown template: {template_id}")
        # Resolve yaml_path: if it's absolute, use it directly; otherwise
        # resolve against the source dir recorded for this template (the
        # default workflows_dir or an extension's workflows_dir).
        yaml_path = Path(tmpl.yaml_path)
        if yaml_path.is_absolute():
            with open(yaml_path, "r") as f:
                return yaml.safe_load(f) or {}
        source_dir = self._template_source_dirs.get(template_id, self.workflows_dir)
        resolved = source_dir / tmpl.yaml_path
        with open(resolved, "r") as f:
            return yaml.safe_load(f) or {}
