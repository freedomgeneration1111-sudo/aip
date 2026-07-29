"""AST scan: web files must not import forbidden network libraries
where they shouldn't.

This is a defense-in-depth check that complements
``tests/test_no_network.py``.  The repo-root test scans
``foundation/`` and ``orchestration/`` (the layers forbidden from
network access by AIP's layering rules).  This test additionally
scans the web adapter files and enforces a split:

    - **Network-free files** (WS-1): must NOT import httpx/requests/
      aiohttp.  These are the foundation/protocol/policy/fake/snapshot
      files that must run in CI without network.

    - **Network-allowed files** (WS-2+): MAY import httpx/requests/
      aiohttp.  These are the real fetcher and provider adapters.
      They are still scanned to confirm they import ONLY the allowed
      network libraries (no openai/anthropic direct imports — those
      go through the model abstraction layer).

Forbidden imports (per ``tests/test_no_network.py``):
    - httpx, requests, aiohttp, openai, anthropic
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN_IMPORTS = {
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "anthropic",
}

# Network libraries allowed in the adapter layer (per tests/test_no_network.py).
# These are the ONLY network libs that WS-2+ web files may import.
ALLOWED_NETWORK_LIBS = {
    "httpx",
    "requests",
    "aiohttp",
}

# Files that must remain NETWORK-FREE (WS-1 foundation + fakes + stores).
# Adding a file here requires updating this list — the drift guard enforces it.
NETWORK_FREE_FILES: list[str] = [
    "src/aip/adapter/web/__init__.py",
    "src/aip/adapter/web/policy.py",
    "src/aip/adapter/web/fake_provider.py",
    "src/aip/adapter/web/snapshot.py",
    "src/aip/adapter/web/lifecycle.py",
    "src/aip/adapter/web/provenance.py",
    "src/aip/adapter/web/promotion.py",
    "src/aip/adapter/web/eval_validators.py",
    "src/aip/foundation/schemas/web.py",
    "src/aip/foundation/protocols/web.py",
]

# Files that ARE allowed to import network libraries (WS-2+ real adapters).
# These are still scanned for forbidden non-network libs (openai/anthropic).
NETWORK_ALLOWED_FILES: list[str] = [
    "src/aip/adapter/web/http_fetcher.py",
    "src/aip/adapter/web/extractors/html.py",
    "src/aip/adapter/web/extractors/pdf.py",
    "src/aip/adapter/web/extractors/plain_text.py",
    "src/aip/adapter/web/extractors/factory.py",
    "src/aip/adapter/web/extractors/__init__.py",
    "src/aip/adapter/web/providers/tavily.py",
    "src/aip/adapter/web/providers/factory.py",
    "src/aip/adapter/web/providers/__init__.py",
]


def _collect_imports(filepath: Path) -> set[str]:
    """Return the set of top-level module names imported by ``filepath``."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("rel_path", NETWORK_FREE_FILES)
def test_network_free_file_has_no_network_imports(rel_path):
    """Each network-free file must not import any forbidden network library."""
    repo_root = Path(__file__).resolve().parents[2]
    filepath = repo_root / rel_path
    assert filepath.exists(), f"Network-free file missing: {rel_path}"

    imports = _collect_imports(filepath)
    forbidden = imports & FORBIDDEN_IMPORTS
    assert not forbidden, (
        f"{rel_path} imports forbidden network libraries: {sorted(forbidden)}. "
        "This file must remain network-free (CI runs without network)."
    )


@pytest.mark.parametrize("rel_path", NETWORK_ALLOWED_FILES)
def test_network_allowed_file_has_no_forbidden_non_network_imports(rel_path):
    """Network-allowed files must not import openai/anthropic directly."""
    repo_root = Path(__file__).resolve().parents[2]
    filepath = repo_root / rel_path
    if not filepath.exists():
        pytest.skip(f"File not yet created: {rel_path}")

    imports = _collect_imports(filepath)
    # openai and anthropic are ALWAYS forbidden — they go through the model abstraction layer.
    always_forbidden = FORBIDDEN_IMPORTS - ALLOWED_NETWORK_LIBS
    forbidden = imports & always_forbidden
    assert not forbidden, (
        f"{rel_path} imports forbidden non-network libraries: {sorted(forbidden)}. "
        "Model access must go through the configured model abstraction layer."
    )


def test_web_file_list_is_complete():
    """Guard: if a new web/ file is added, this test list must be updated.

    This prevents a new file from silently shipping with network imports
    because the parametrized tests above weren't updated.
    """
    repo_root = Path(__file__).resolve().parents[2]
    web_dir = repo_root / "src" / "aip" / "adapter" / "web"
    extractors_dir = web_dir / "extractors"
    providers_dir = web_dir / "providers"

    actual_web_files = sorted(
        p.relative_to(repo_root).as_posix()
        for p in web_dir.glob("*.py")
    )
    actual_extractor_files = sorted(
        p.relative_to(repo_root).as_posix()
        for p in extractors_dir.glob("*.py")
    ) if extractors_dir.exists() else []
    actual_provider_files = sorted(
        p.relative_to(repo_root).as_posix()
        for p in providers_dir.glob("*.py")
    ) if providers_dir.exists() else []

    actual_files = actual_web_files + actual_extractor_files + actual_provider_files
    expected_files = NETWORK_FREE_FILES + NETWORK_ALLOWED_FILES

    # Filter to only adapter/web files (exclude foundation files from the drift check)
    # and sort both lists the same way so order doesn't cause false drift.
    actual_adapter_files = sorted(f for f in actual_files if "adapter/web" in f)
    expected_adapter_files = sorted(f for f in expected_files if "adapter/web" in f)

    assert actual_adapter_files == expected_adapter_files, (
        f"Web file list drift detected.\n"
        f"  actual:   {actual_adapter_files}\n"
        f"  expected: {expected_adapter_files}\n"
        f"Update NETWORK_FREE_FILES or NETWORK_ALLOWED_FILES in this test "
        f"to reflect the new file set."
    )
