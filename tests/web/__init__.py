"""AIP Web Source Acquisition test suite (ADR-017).

WS-1 tests cover:
    - ``test_web_schemas.py``         — dataclass round-trip, hash stability, immutability
    - ``test_web_protocols.py``       — Protocol isinstance checks against fakes
    - ``test_fetch_policy.py``        — SSRF matrix, scheme allowlist, redirect cap
    - ``test_fake_provider.py``       — FakeSearchProvider determinism + limit
                                       FakeWebFetcher policy enforcement
    - ``test_snapshot_store.py``      — dedup by hash, list-by-query, delete-expired
    - ``test_no_network_honored.py``  — AST scan: no httpx/requests/aiohttp in WS-1 files

All tests in this subpackage run WITHOUT network.  ``tests/test_no_network.py``
at the repo root enforces the same contract for foundation + orchestration.
"""
