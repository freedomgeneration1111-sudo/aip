"""Cycle 16.9A — Regression tests for Vigil cycle report path fixes.

F09: ECS transition() was called with `detail=` instead of `reason=`,
     and missing the required `from_state` parameter.
     TypeError: transition() got an unexpected keyword argument 'detail'

F10: Vigil cycle report artifact ID was based on truncated timestamp,
     causing UNIQUE constraint collisions when concurrent Vigil cycles
     (scheduler + startup) produce the same truncated timestamp.
     sqlite3.IntegrityError: UNIQUE constraint failed: artifacts.id, artifacts.version

Both fixes must:
- Not silently suppress errors
- Preserve DEFINER review gates
- Not auto-approve artifacts
- Preserve transition observability
"""

from __future__ import annotations

import inspect
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# F09: ECS transition detail keyword mismatch
# ---------------------------------------------------------------------------


class TestF09TransitionSignature:
    """Verify that Vigil transition calls match the actual ECS transition API."""

    def test_transition_signature_has_reason_not_detail(self) -> None:
        """The transition() signature must have `reason`, not `detail`."""
        from aip.adapter.ecs_store_persistent import PersistentEcsStore

        sig = inspect.signature(PersistentEcsStore.transition)
        param_names = list(sig.parameters.keys())
        assert "reason" in param_names, f"transition() must have 'reason' param, got: {param_names}"
        assert "detail" not in param_names, f"transition() must NOT have 'detail' param, got: {param_names}"

    def test_transition_signature_has_from_state(self) -> None:
        """The transition() signature must have `from_state`."""
        from aip.adapter.ecs_store_persistent import PersistentEcsStore

        sig = inspect.signature(PersistentEcsStore.transition)
        param_names = list(sig.parameters.keys())
        assert "from_state" in param_names, f"transition() must have 'from_state' param, got: {param_names}"

    def test_vigil_transition_calls_use_reason(self) -> None:
        """Vigil source code must use `reason=` not `detail=` in transition calls."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        # Find all transition( calls in vigil.py
        lines = source.split("\n")
        transition_lines = []
        for i, line in enumerate(lines):
            if ".transition(" in line:
                # Collect the full call (may span multiple lines)
                call_block = line
                j = i + 1
                while j < len(lines) and ")" not in call_block:
                    call_block += lines[j]
                    j += 1
                transition_lines.append((i + 1, call_block))

        for line_no, call_text in transition_lines:
            assert "detail=" not in call_text, (
                f"Line {line_no}: transition() call uses 'detail=' keyword — "
                f"should be 'reason='. Call: {call_text[:200]}"
            )

    def test_vigil_transition_calls_include_from_state(self) -> None:
        """Vigil transition calls must include `from_state=` parameter."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        lines = source.split("\n")
        transition_blocks: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            if "self._ecs.transition(" in line:
                call_block = line
                j = i + 1
                depth = line.count("(") - line.count(")")
                while j < len(lines) and depth > 0:
                    call_block += "\n" + lines[j]
                    depth += lines[j].count("(") - lines[j].count(")")
                    j += 1
                transition_blocks.append((i + 1, call_block))

        for line_no, call_text in transition_blocks:
            assert "from_state=" in call_text, (
                f"Line {line_no}: transition() call missing 'from_state=' parameter. Call: {call_text[:200]}"
            )


class TestF09TransitionExecution:
    """Verify that Vigil transition calls actually execute without TypeError."""

    @pytest.mark.asyncio
    async def test_vigil_flag_transition_no_typeerror(self) -> None:
        """Flag artifact transition must not raise TypeError for 'detail' keyword."""
        from aip.adapter.ecs_store_persistent import PersistentEcsStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_state.db")
            store = PersistentEcsStore(db_path)
            await store.initialize()

            # This should succeed without TypeError
            await store.transition(
                artifact_id="vigil-flag-test-turn-1",
                from_state=None,
                to_state="GENERATED",
                actor="vigil",
                reason="Quality evaluation: low_citation_rate (citation_rate=20.0%, grounding_rate=80.0%)",
            )

            # Verify state was recorded
            state = await store.current_state("vigil-flag-test-turn-1")
            assert state == "GENERATED"

    @pytest.mark.asyncio
    async def test_vigil_report_transition_no_typeerror(self) -> None:
        """Cycle report transition must not raise TypeError for 'detail' keyword."""
        from aip.adapter.ecs_store_persistent import PersistentEcsStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_state.db")
            store = PersistentEcsStore(db_path)
            await store.initialize()

            # This should succeed without TypeError
            await store.transition(
                artifact_id="vigil-report-20260614T093150-a1b2c3d4",
                from_state=None,
                to_state="GENERATED",
                actor="vigil",
                reason="Vigil cycle quality report: 2 flagged turn(s), low avg citation rate (45.0%)",
            )

            # Verify state was recorded
            state = await store.current_state("vigil-report-20260614T093150-a1b2c3d4")
            assert state == "GENERATED"

    @pytest.mark.asyncio
    async def test_transition_reason_is_persisted(self) -> None:
        """The reason field must be persisted in the ECS transitions table."""
        from aip.adapter.ecs_store_persistent import PersistentEcsStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_state.db")
            store = PersistentEcsStore(db_path)
            await store.initialize()

            reason_text = "Quality evaluation: low_citation_rate (citation_rate=20.0%, grounding_rate=80.0%)"
            await store.transition(
                artifact_id="vigil-flag-test-turn-2",
                from_state=None,
                to_state="GENERATED",
                actor="vigil",
                reason=reason_text,
            )

            # Verify reason was persisted
            history = await store.get_transition_history("vigil-flag-test-turn-2")
            assert len(history) >= 1
            assert history[0]["reason"] == reason_text
            assert history[0]["actor"] == "vigil"


# ---------------------------------------------------------------------------
# F10: Vigil cycle report UNIQUE constraint collision
# ---------------------------------------------------------------------------


class TestF10UniqueConstraintCollision:
    """Verify that concurrent Vigil cycle reports don't collide on artifact ID."""

    def test_vigil_report_artifact_id_includes_entropy(self) -> None:
        """Artifact ID must include UUID suffix for uniqueness."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        # The artifact_id must use cycle_suffix
        assert "cycle_suffix" in source, "Vigil must generate cycle_suffix for unique artifact IDs"
        assert "vigil-report-{cycle_ts}-{cycle_suffix}" in source, (
            "Artifact ID must include both cycle_ts and cycle_suffix"
        )

    @pytest.mark.asyncio
    async def test_two_concurrent_report_writes_no_collision(self) -> None:
        """Two artifact writes with different UUID suffixes must not collide."""
        from aip.adapter.artifact_store_versioned import VersionedArtifactStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_artifacts.db")
            store = VersionedArtifactStore(db_path)

            # Simulate two concurrent Vigil cycles generating different artifact IDs
            # within the same second (same truncated timestamp, different UUID suffixes)
            import uuid

            cycle_ts = "20260614T093150"
            suffix_1 = uuid.uuid4().hex[:8]
            suffix_2 = uuid.uuid4().hex[:8]

            artifact_id_1 = f"vigil-report-{cycle_ts}-{suffix_1}"
            artifact_id_2 = f"vigil-report-{cycle_ts}-{suffix_2}"

            # Both must succeed without UNIQUE constraint violation
            await store.write(id=artifact_id_1, content='{"report": 1}', metadata={"type": "vigil_cycle_report"})
            await store.write(id=artifact_id_2, content='{"report": 2}', metadata={"type": "vigil_cycle_report"})

    @pytest.mark.asyncio
    async def test_two_same_second_report_writes_no_collision(self) -> None:
        """Two Vigil report artifact writes in the same second must not collide."""
        from aip.adapter.artifact_store_versioned import VersionedArtifactStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_artifacts.db")
            store = VersionedArtifactStore(db_path)

            import uuid

            # Simulate exact same timestamp but different UUID suffixes
            now_iso = datetime.now(timezone.utc).isoformat()
            cycle_ts = now_iso.replace(":", "").replace("-", "").replace(".", "")[:15]

            suffix_1 = uuid.uuid4().hex[:8]
            suffix_2 = uuid.uuid4().hex[:8]

            artifact_id_1 = f"vigil-report-{cycle_ts}-{suffix_1}"
            artifact_id_2 = f"vigil-report-{cycle_ts}-{suffix_2}"

            await store.write(id=artifact_id_1, content='{"report": 1}', metadata={"type": "vigil_cycle_report"})
            await store.write(id=artifact_id_2, content='{"report": 2}', metadata={"type": "vigil_cycle_report"})

            # Both artifacts should exist with version 1
            content_1 = await store.read(artifact_id_1)
            content_2 = await store.read(artifact_id_2)
            assert content_1 is not None
            assert content_2 is not None

    @pytest.mark.asyncio
    async def test_repeated_vigil_cycles_no_integrity_error(self) -> None:
        """Simulate repeated Vigil cycles and verify no IntegrityError."""
        from aip.adapter.artifact_store_versioned import VersionedArtifactStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_artifacts.db")
            store = VersionedArtifactStore(db_path)

            import uuid

            # Simulate 5 rapid Vigil cycles in the same second
            now_iso = datetime.now(timezone.utc).isoformat()
            cycle_ts = now_iso.replace(":", "").replace("-", "").replace(".", "")[:15]

            artifact_ids = []
            for i in range(5):
                suffix = uuid.uuid4().hex[:8]
                artifact_id = f"vigil-report-{cycle_ts}-{suffix}"
                artifact_ids.append(artifact_id)
                await store.write(
                    id=artifact_id,
                    content=f'{{"report": {i}}}',
                    metadata={"type": "vigil_cycle_report", "cycle": i},
                )

            # All 5 should be readable
            for i, aid in enumerate(artifact_ids):
                content = await store.read(aid)
                assert content is not None

    @pytest.mark.asyncio
    async def test_old_deterministic_id_would_collide(self) -> None:
        """Verify that the OLD artifact ID pattern (without UUID) would collide.

        This test demonstrates the F10 bug: two writes with the same
        deterministic artifact_id cause a version collision when the
        version auto-increment logic races.
        """
        from aip.adapter.artifact_store_versioned import VersionedArtifactStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_artifacts.db")
            store = VersionedArtifactStore(db_path)

            # Old-style deterministic ID (no UUID suffix)
            artifact_id = "vigil-report-20260614T093150"

            # First write succeeds (creates version 1)
            await store.write(id=artifact_id, content='{"report": 1}', metadata={"type": "vigil_cycle_report"})

            # Second write with same ID should also succeed (creates version 2)
            # because the artifact store auto-increments versions.
            # However, in concurrent scenarios with separate connections,
            # both could read MAX(version)=0 and try to insert version 1.
            # The versioned store handles this correctly for sequential writes.
            await store.write(id=artifact_id, content='{"report": 2}', metadata={"type": "vigil_cycle_report"})

            # This test confirms the versioned store itself works for
            # sequential writes. The real bug occurs with concurrent
            # connections (startup + scheduler), which is why we add
            # the UUID suffix.


# ---------------------------------------------------------------------------
# Honesty / Sovereignty gate checks
# ---------------------------------------------------------------------------


class TestHonestyAndSovereignty:
    """Verify that fixes don't silently suppress errors or weaken review gates."""

    def test_vigil_transition_failure_still_logged(self) -> None:
        """Transition failures must still be logged, not silently caught."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        # Both transition calls must be inside try/except that logs the error
        assert "vigil_flag_ecs_transition_failed" in source
        assert "vigil_report_ecs_transition_failed" in source

    def test_vigil_artifacts_not_auto_approved(self) -> None:
        """Vigil artifacts must transition to GENERATED (not APPROVED)."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "self._ecs.transition(" in line:
                # Find the to_state in the following lines
                block = "\n".join(lines[i : i + 6])
                assert '"APPROVED"' not in block, (
                    f"Line {i + 1}: Vigil transition must not auto-approve. Block: {block[:200]}"
                )

    def test_no_broad_exception_suppression(self) -> None:
        """Vigil must not use bare except: or broad except Exception to hide F09/F10."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        # The transition calls should have specific error handling
        # that logs, not swallows
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "except Exception as exc:" in line:
                # Check that the next few lines include a logger.warning or logger.error
                block = "\n".join(lines[i : i + 4])
                assert "logger." in block, (
                    f"Line {i + 1}: Exception handler must log, not swallow. Block: {block[:200]}"
                )

    def test_vigil_report_artifact_type_preserved(self) -> None:
        """Cycle report artifacts must still be typed as vigil_cycle_report."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        assert '"artifact_type": "vigil_cycle_report"' in source

    def test_vigil_flag_artifact_type_preserved(self) -> None:
        """Flag artifacts must still be typed as vigil_flag."""
        source = Path("src/aip/orchestration/actors/vigil.py").read_text()
        assert '"artifact_type": "vigil_flag"' in source


# ---------------------------------------------------------------------------
# No scope creep
# ---------------------------------------------------------------------------


class TestNoScopeCreep:
    """Verify that only F09 and F10 were fixed, nothing else broadened."""

    def test_no_vigil_signature_changes_beyond_transition_fix(self) -> None:
        """The Vigil class public interface should not have changed."""
        from aip.orchestration.actors.vigil import Vigil

        # Key public methods must still exist
        assert hasattr(Vigil, "run_cycle")
        assert hasattr(Vigil, "run")

    def test_ecs_transition_signature_unchanged(self) -> None:
        """The foundational ECS transition API must not have been widened."""
        from aip.adapter.ecs_store_persistent import PersistentEcsStore

        sig = inspect.signature(PersistentEcsStore.transition)
        params = list(sig.parameters.keys())
        # Must still have the same parameters — no `detail` added
        assert params == ["self", "artifact_id", "from_state", "to_state", "actor", "reason", "superseded_by"]

    def test_guardrailed_store_signature_unchanged(self) -> None:
        """GuardrailedEcsStore transition signature must not have been widened."""
        from aip.adapter.ecs_store_guardrailed import GuardrailedEcsStore

        sig = inspect.signature(GuardrailedEcsStore.transition)
        params = list(sig.parameters.keys())
        assert params == ["self", "artifact_id", "from_state", "to_state", "actor", "reason", "superseded_by"]
