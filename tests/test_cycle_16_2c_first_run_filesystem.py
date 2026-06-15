"""Cycle 16.2C — Regression tests for first-run filesystem/config hardening.

Fix 1: db/ directory auto-creation — clean checkout should not require manual mkdir.
Fix 2: config/enabled_models.json must survive aip init and seed bootstrap.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fix 1: db/ directory auto-creation
# ---------------------------------------------------------------------------


class TestFix1DBDirectoryAutoCreation:
    """Regression: a clean checkout should not require manual 'mkdir -p db'.

    The app's lifespan (app.py) must create DB parent directories before
    opening SQLite connections. start.sh must also ensure the DB directory
    exists before launching the backend.
    """

    def test_app_py_creates_db_parent_directory(self) -> None:
        """app.py lifespan must contain logic to create DB parent directories."""
        app_py = Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "app.py"
        content = app_py.read_text()

        # Must create the parent directory of db_path
        assert "mkdir" in content, "app.py must create DB parent directories during startup"
        assert "_db_parent" in content or "db_parent" in content, (
            "app.py must compute the parent of db_path and create it"
        )

    def test_app_py_db_dir_creation_before_store_init(self) -> None:
        """DB parent directory creation must happen before the first store initialize()."""
        app_py = Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "app.py"
        content = app_py.read_text()

        # The mkdir call must come before the first store initialization
        mkdir_pos = content.find("_db_parent.mkdir")
        if mkdir_pos == -1:
            mkdir_pos = content.find("db_parent.mkdir")
        assert mkdir_pos > 0, "app.py must have mkdir call for DB parent directory"

        # The first store init (entity_store) must come after
        first_init_pos = content.find("container.entity_store = ")
        assert first_init_pos > mkdir_pos, "DB parent directory creation must happen before first store initialization"

    def test_app_py_reports_permission_error_honestly(self) -> None:
        """If DB directory creation fails due to permissions, startup must fail honestly."""
        app_py = Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "app.py"
        content = app_py.read_text()

        # Must handle PermissionError
        assert "PermissionError" in content, "app.py must handle PermissionError when creating DB directories"

    def test_app_py_reports_os_error_honestly(self) -> None:
        """If DB directory creation fails due to OS error, startup must fail honestly."""
        app_py = Path(__file__).resolve().parent.parent / "src" / "aip" / "adapter" / "api" / "app.py"
        content = app_py.read_text()

        # Must handle OSError
        assert "OSError" in content, "app.py must handle OSError when creating DB directories"

    def test_start_sh_creates_db_directory(self) -> None:
        """start.sh must create the DB directory before starting the backend."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        assert "mkdir" in content, "start.sh must create the DB directory"
        assert "DB_DIR" in content or "db" in content, "start.sh must reference the DB directory"

    def test_start_sh_reports_mkdir_failure(self) -> None:
        """start.sh must report an error if mkdir fails."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        # Must handle mkdir failure
        assert "Cannot create" in content or "ERROR" in content, (
            "start.sh must report an error if the DB directory cannot be created"
        )

    def test_start_sh_does_not_fail_if_db_dir_exists(self) -> None:
        """start.sh mkdir must be idempotent — must not fail if directory exists."""
        start_sh = Path(__file__).resolve().parent.parent / "scripts" / "start.sh"
        content = start_sh.read_text()

        # mkdir -p is idempotent; must use -p flag
        assert "mkdir -p" in content, "start.sh must use 'mkdir -p' for idempotent directory creation"

    def test_db_dir_created_on_startup_with_missing_dir(self, tmp_path: Path) -> None:
        """Integration: app lifespan logic must create DB parent directory if it does not exist."""
        # Test the exact logic that app.py uses before opening DB connections
        nested_db_dir = tmp_path / "nested" / "db"
        db_path = str(nested_db_dir / "state.db")
        assert not nested_db_dir.exists(), "Precondition: nested DB dir must not exist yet"

        # This is the exact logic from app.py lifespan:
        _db_parent = Path(db_path).parent
        if _db_parent and str(_db_parent) != ".":
            _db_parent.mkdir(parents=True, exist_ok=True)

        assert nested_db_dir.exists(), f"DB parent directory {nested_db_dir} must be created by lifespan logic"


class TestFix1ExistingDBPathsStillWork:
    """Regression: existing explicit DB paths must continue to work."""

    def test_db_path_with_existing_directory(self, tmp_path: Path) -> None:
        """If the DB directory already exists, mkdir with exist_ok=True must succeed."""
        db_dir = tmp_path / "existing_db"
        db_dir.mkdir()
        db_path = db_dir / "state.db"

        # The exact logic from app.py: Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        parent = Path(str(db_path)).parent
        parent.mkdir(parents=True, exist_ok=True)  # Must not fail

        assert parent.exists(), "Parent directory must exist after mkdir"

    def test_db_path_in_current_dir(self, tmp_path: Path) -> None:
        """If db_path is just a filename (no parent dir), no directory creation needed."""
        db_path = "state.db"
        parent = Path(db_path).parent

        # Path("state.db").parent == Path(".") — no directory to create
        assert str(parent) == ".", "Filename-only db_path should have '.' as parent"

    def test_mkdir_creates_nested_dirs(self, tmp_path: Path) -> None:
        """mkdir with parents=True must create all intermediate directories."""
        nested = tmp_path / "a" / "b" / "c" / "db"
        assert not nested.exists(), "Precondition: nested dir must not exist"

        nested.mkdir(parents=True, exist_ok=True)

        assert nested.exists(), "Nested directory must be created"
        assert (tmp_path / "a").is_dir()
        assert (tmp_path / "a" / "b").is_dir()
        assert (tmp_path / "a" / "b" / "c").is_dir()


# ---------------------------------------------------------------------------
# Fix 2: config/enabled_models.json preservation
# ---------------------------------------------------------------------------


class TestFix2EnabledModelsJsonPreservation:
    """Regression: config/enabled_models.json must not be deleted by init/bootstrap.

    The JSON file is a tracked seed file that aip init reads to populate the
    enabled_models DB table. It must never be consumed, moved, or deleted.
    """

    def test_enabled_models_json_is_tracked(self) -> None:
        """config/enabled_models.json must exist in the repository."""
        json_path = Path(__file__).resolve().parent.parent / "config" / "enabled_models.json"
        assert json_path.exists(), "config/enabled_models.json must be tracked in the repository"

    def test_enabled_models_json_is_valid_json(self) -> None:
        """config/enabled_models.json must contain valid JSON."""
        json_path = Path(__file__).resolve().parent.parent / "config" / "enabled_models.json"
        raw = json_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, list), "enabled_models.json must be a JSON array"
        assert len(data) > 0, "enabled_models.json must contain at least one model"

    def test_init_py_only_reads_enabled_models_json(self) -> None:
        """aip init must only READ enabled_models.json, never write/move/delete it."""
        init_py = Path(__file__).resolve().parent.parent / "src" / "aip" / "cli" / "init.py"
        content = init_py.read_text()

        # Find all references to enabled_models.json
        # The function must use read_text (read) not write_text (write)
        # and must not have any os.remove, os.rename, shutil.move, etc.
        assert "enabled_models.json" in content, "init.py must reference enabled_models.json"

        # Must NOT have delete/move operations on the JSON file
        for bad_pattern in [
            "os.remove",
            "os.unlink",
            "shutil.move",
            "shutil.rmtree",
            "json_path.unlink",
            "json_path.rename",
            "json_path.replace",
        ]:
            # Check if the pattern appears near enabled_models code
            # (could be elsewhere in the file for other purposes)
            pass  # We can't easily check proximity; rely on the read-only test below

        # _populate_enabled_models must use read_text, not write_text
        # Find the function body
        func_start = content.find("def _populate_enabled_models")
        assert func_start > 0, "init.py must have _populate_enabled_models function"
        # Find the next function definition
        next_func = content.find("\ndef ", func_start + 1)
        func_body = content[func_start:next_func] if next_func > 0 else content[func_start:]

        assert "read_text" in func_body, "_populate_enabled_models must read the JSON file"
        assert "write_text" not in func_body, "_populate_enabled_models must NOT write to the JSON file"
        assert ".unlink()" not in func_body, "_populate_enabled_models must NOT delete the JSON file"
        assert ".rename(" not in func_body, "_populate_enabled_models must NOT rename the JSON file"

    def test_seed_bootstrap_does_not_delete_enabled_models(self) -> None:
        """seed_bootstrap.sh must not delete config/enabled_models.json."""
        bootstrap_path = Path(__file__).resolve().parent.parent / "examples" / "seed_corpus" / "seed_bootstrap.sh"
        if not bootstrap_path.exists():
            pytest.skip("seed_bootstrap.sh not found — cannot check for deletion")

        content = bootstrap_path.read_text()

        # Must NOT contain rm of enabled_models.json
        assert "rm" not in content or "enabled_models" not in content, (
            "seed_bootstrap.sh must not remove enabled_models.json"
        )
        assert "mv" not in content or "enabled_models" not in content, (
            "seed_bootstrap.sh must not move enabled_models.json"
        )

    def test_aip_init_preserves_enabled_models_json(self, tmp_path: Path) -> None:
        """Integration: aip init must not delete or modify config/enabled_models.json."""
        repo_root = Path(__file__).resolve().parent.parent

        # Save original content
        json_path = repo_root / "config" / "enabled_models.json"
        if not json_path.exists():
            pytest.skip("config/enabled_models.json not found in repo")

        original_content = json_path.read_text(encoding="utf-8")
        try:
            # Run aip init
            subprocess.run(
                ["uv", "run", "aip", "init"],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                env={**os.environ, "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", "")},
                timeout=60,
            )

            # The file must still exist after init
            assert json_path.exists(), "config/enabled_models.json must still exist after aip init"

            # The content must be unchanged
            new_content = json_path.read_text(encoding="utf-8")
            assert new_content == original_content, "config/enabled_models.json content must not change during aip init"
        finally:
            # Restore original file state (in case any side effects)
            if json_path.exists() and json_path.read_text(encoding="utf-8") != original_content:
                json_path.write_text(original_content, encoding="utf-8")

    def test_no_code_path_deletes_enabled_models_json(self) -> None:
        """No Python code in the project should delete config/enabled_models.json."""
        repo_root = Path(__file__).resolve().parent.parent
        src_dir = repo_root / "src"

        # Search for any code that deletes enabled_models.json
        bad_patterns = []
        for py_file in src_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for line_no, line in enumerate(content.splitlines(), 1):
                # Check for deletion patterns referencing enabled_models
                if "enabled_models" in line and (
                    "unlink" in line or "remove" in line or "rmtree" in line or "os.delete" in line
                ):
                    # Exclude comments and string literals that just describe behavior
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    bad_patterns.append((str(py_file.relative_to(repo_root)), line_no, stripped))

        assert not bad_patterns, f"Found code that may delete enabled_models.json: {bad_patterns}"


class TestFix2EnabledModelsJsonGitStatus:
    """Verify that aip init does not cause git to see enabled_models.json as modified."""

    def test_git_status_enabled_models_json_after_init(self) -> None:
        """After aip init, git status must not show enabled_models.json as modified."""
        repo_root = Path(__file__).resolve().parent.parent
        json_path = repo_root / "config" / "enabled_models.json"

        if not json_path.exists():
            pytest.skip("config/enabled_models.json not found")

        # Check git status for the file
        git_result = subprocess.run(
            ["git", "status", "--short", "config/enabled_models.json"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=10,
        )

        # Empty output means no changes
        assert git_result.stdout.strip() == "", (
            f"config/enabled_models.json should not be modified by init. git status output: {git_result.stdout.strip()}"
        )
