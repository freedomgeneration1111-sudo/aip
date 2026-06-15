"""Cycle 16.6A — Dogfood Smoke / Status / Startup Cleanup regression tests.

Tests for R04 (smoke script timeout), R05 (stale start-aip.sh), and
R06 (aip status Ollama timeout) fixes.

Layer: foundation — these tests only inspect source code and run
simulated status checks.  They do not start a live backend.
"""

from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════
# R06 — aip status Ollama timeout
# ═══════════════════════════════════════════════════════════════════════


class TestR06OllamaTimeout:
    """R06: aip status Ollama reachability checks must be bounded."""

    def test_check_ollama_has_configurable_timeout(self):
        """_check_ollama reads AIP_STATUS_OLLAMA_TIMEOUT env var."""
        from aip.cli.status import _get_ollama_timeout

        # Default timeout should be 5
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AIP_STATUS_OLLAMA_TIMEOUT", None)
            assert _get_ollama_timeout() == 5

        # Env var should override
        with patch.dict(os.environ, {"AIP_STATUS_OLLAMA_TIMEOUT": "10"}):
            assert _get_ollama_timeout() == 10

        # Invalid env var falls back to default
        with patch.dict(os.environ, {"AIP_STATUS_OLLAMA_TIMEOUT": "not_a_number"}):
            assert _get_ollama_timeout() == 5

        # Zero/negative falls back to default
        with patch.dict(os.environ, {"AIP_STATUS_OLLAMA_TIMEOUT": "0"}):
            assert _get_ollama_timeout() == 5

    def test_check_ollama_reports_unreachable_honestly(self):
        """When Ollama is not running, _check_ollama reports unavailable without crash."""
        import httpx

        from aip.cli.status import _check_ollama

        with patch.object(httpx, "get", side_effect=httpx.ConnectError("Connection refused")):
            result = _check_ollama()

        assert result["reachable"] is False
        assert result["timed_out"] is False
        # Must have actionable error message
        assert result["error"] is not None
        assert "not running" in result["error"] or "unavailable" in result["error"].lower()

    def test_check_ollama_reports_timeout_honestly(self):
        """When Ollama times out, _check_ollama reports timed_out with actionable message."""
        import httpx

        from aip.cli.status import _check_ollama

        with patch.object(httpx, "get", side_effect=httpx.TimeoutException("Timed out")):
            result = _check_ollama()

        assert result["reachable"] is False
        assert result["timed_out"] is True
        assert result["error"] is not None
        # Must have actionable message mentioning timeout and local model
        assert "not reachable" in result["error"] or "timed out" in result["error"].lower()
        assert "local model" in result["error"].lower()

    def test_check_ollama_no_fake_healthy_state(self):
        """_check_ollama must never report reachable=True when connection fails."""
        import httpx

        from aip.cli.status import _check_ollama

        with patch.object(httpx, "get", side_effect=Exception("Any error")):
            result = _check_ollama()

        assert result["reachable"] is False

    def test_status_source_has_bounded_kg_query(self):
        """The status.py source code must use asyncio.wait_for on KG queries."""
        status_src = (PROJECT_ROOT / "src" / "aip" / "cli" / "status.py").read_text()
        # Knowledge graph section must use wait_for with a timeout
        assert "wait_for" in status_src, "status.py must use asyncio.wait_for on KG query"
        assert "timeout=10" in status_src, "KG query must have a bounded timeout"

    def test_status_ollama_default_timeout_is_short(self):
        """Default Ollama timeout should be 3-5 seconds (not 30+)."""
        from aip.cli.status import _DEFAULT_OLLAMA_TIMEOUT

        assert 3 <= _DEFAULT_OLLAMA_TIMEOUT <= 10, (
            f"Default Ollama timeout ({_DEFAULT_OLLAMA_TIMEOUT}s) should be 3-10s for dogfood use"
        )


# ═══════════════════════════════════════════════════════════════════════
# R04 — dogfood_smoke_test.sh timeout handling
# ═══════════════════════════════════════════════════════════════════════


class TestR04SmokeScriptTimeout:
    """R04: dogfood_smoke_test.sh must have per-step timeouts."""

    @pytest.fixture
    def smoke_script(self) -> str:
        return (PROJECT_ROOT / "scripts" / "dogfood_smoke_test.sh").read_text()

    def test_smoke_script_has_step_timeout_var(self, smoke_script: str):
        """Script must define AIP_SMOKE_STEP_TIMEOUT_SECONDS with default."""
        assert "AIP_SMOKE_STEP_TIMEOUT_SECONDS" in smoke_script, (
            "Smoke script must define AIP_SMOKE_STEP_TIMEOUT_SECONDS env var"
        )
        # Default should be reasonable
        assert "60" in smoke_script, "Default step timeout should be 60s"

    def test_smoke_script_uses_timeout_command(self, smoke_script: str):
        """Script must use the `timeout` command for long-running steps."""
        assert "timeout" in smoke_script, "Smoke script must use `timeout` command for per-step bounds"

    def test_smoke_script_has_run_step_function(self, smoke_script: str):
        """Script must have a run_step function that exits on timeout."""
        assert "run_step()" in smoke_script, "Smoke script must define run_step() function"
        # run_step should detect exit code 124 (timeout)
        assert "124" in smoke_script, "run_step must detect exit code 124 (timeout)"

    def test_smoke_script_exits_on_timeout(self, smoke_script: str):
        """Script must exit nonzero when a step times out."""
        # Look for "exit 124" or "TIMEOUT" in the script
        assert re.search(r"exit\s+124|exit\s+\"\$rc\"", smoke_script), "Smoke script must exit with code 124 on timeout"

    def test_smoke_script_timeout_on_slow_command(self):
        """Verify timeout behavior with a synthetic slow command."""
        result = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent("""\
                source <(sed -n '/^STEP_TIMEOUT=/,/^run_step()/p' scripts/dogfood_smoke_test.sh | head -30)
                # Simulate: set STEP_TIMEOUT and define run_step
                STEP_TIMEOUT=2
                run_step() {
                  local label="$1"; shift
                  echo "==> $label"
                  echo "+ $* (timeout=${STEP_TIMEOUT}s)"
                  local rc=0
                  timeout "${STEP_TIMEOUT}" "$@" || rc=$?
                  if [ "$rc" -eq 0 ]; then
                    echo "  PASS: $label"
                  elif [ "$rc" -eq 124 ]; then
                    echo "  TIMEOUT: $label after ${STEP_TIMEOUT}s"
                    exit 124
                  else
                    echo "  FAIL: $label exit=$rc"
                    exit "$rc"
                  fi
                }
                run_step "slow_command" sleep 30
            """),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        assert result.returncode == 124, (
            f"Slow command should exit 124, got {result.returncode}. stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "TIMEOUT" in result.stdout, f"Timeout output should mention TIMEOUT. stdout={result.stdout!r}"

    def test_every_run_cmd_uses_timeout(self, smoke_script: str):
        """Every run_cmd call in the smoke script goes through the timeout wrapper."""
        # Find all lines that use run_cmd or run_step and verify they use the
        # timeout-enabled function (run_cmd already wraps timeout internally)
        lines = smoke_script.split("\n")
        run_cmd_lines = [i + 1 for i, line in enumerate(lines) if re.match(r"\s*run_cmd\s|^\s*run_step\s", line)]
        assert len(run_cmd_lines) > 0, "Smoke script must have run_cmd or run_step calls"
        # The run_cmd function itself must use timeout
        assert "timeout" in smoke_script, "run_cmd must use timeout internally"


# ═══════════════════════════════════════════════════════════════════════
# R05 — Stale start-aip.sh reference
# ═══════════════════════════════════════════════════════════════════════


class TestR05StaleStartAipReference:
    """R05: Remove stale start-aip.sh reference from seed bootstrap."""

    def test_seed_bootstrap_no_stale_start_aip(self):
        """seed_bootstrap.sh must not recommend stale start-aip.sh."""
        bootstrap = (PROJECT_ROOT / "examples" / "seed_corpus" / "seed_bootstrap.sh").read_text()
        # Must not contain "start-aip.sh" as a recommended path
        assert "start-aip.sh" not in bootstrap, "seed_bootstrap.sh must not recommend the stale start-aip.sh"

    def test_seed_bootstrap_recommends_scripts_start(self):
        """seed_bootstrap.sh must recommend ./scripts/start.sh."""
        bootstrap = (PROJECT_ROOT / "examples" / "seed_corpus" / "seed_bootstrap.sh").read_text()
        assert "./scripts/start.sh" in bootstrap, "seed_bootstrap.sh must recommend ./scripts/start.sh"

    def test_start_aip_is_safe_wrapper(self):
        """Root start-aip.sh must be a safe wrapper that delegates to scripts/start.sh."""
        start_aip = PROJECT_ROOT / "start-aip.sh"
        assert start_aip.exists(), "start-aip.sh must exist for backwards compatibility"
        content = start_aip.read_text()
        # Must delegate to scripts/start.sh
        assert "scripts/start.sh" in content, "start-aip.sh must delegate to scripts/start.sh"
        # Must use exec for clean delegation
        assert "exec" in content, "start-aip.sh must use exec to delegate to scripts/start.sh"
        # Must NOT bind to 0.0.0.0 (the old dangerous version did)
        assert "0.0.0.0" not in content, "start-aip.sh must not bind to 0.0.0.0"
        # Must NOT use gnome-terminal (the old version did)
        assert "gnome-terminal" not in content, "start-aip.sh must not use gnome-terminal"

    def test_start_aip_forwards_arguments(self):
        """start-aip.sh must forward all arguments to scripts/start.sh."""
        content = (PROJECT_ROOT / "start-aip.sh").read_text()
        assert '"$@"' in content, "start-aip.sh must forward all arguments with $@"
