"""WS-6 E2E suite runner tests (ADR-017 WS-6).

Runs the web-grounding evaluation suite against a stub candidate and
verifies that the suite produces a scorecard with pass/fail per case.

The stub candidate is a callable that takes a case dict and returns a
result dict (answer, web_sources, web_failures) — simulating what the
Ask route would produce.  Tests use known-good and known-bad stubs to
verify the suite correctly passes good answers and fails bad ones.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from aip.adapter.web.eval_validators import (
    ValidationResult,
    run_validators,
)

# ---------------------------------------------------------------------------
# Suite loading
# ---------------------------------------------------------------------------


SUITE_PATH = Path(__file__).resolve().parents[2] / "tests" / "acceptance" / "web_grounding_suite.yaml"


def load_suite() -> dict[str, Any]:
    """Load the web grounding suite YAML."""
    with open(SUITE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_suite_yaml_loads():
    """The suite YAML must load and have the expected structure."""
    suite = load_suite()
    assert suite["suite_version"] == "1.0.0"
    assert suite["suite_name"] == "web_grounding_v1"
    assert len(suite["cases"]) >= 10  # at least 10 cases


def test_suite_cases_have_required_fields():
    """Every case must have id, query, and validators."""
    suite = load_suite()
    for case in suite["cases"]:
        assert "id" in case, f"Case missing 'id': {case}"
        assert "query" in case, f"Case missing 'query': {case}"
        assert "validators" in case, f"Case {case.get('id')} missing 'validators'"
        assert len(case["validators"]) > 0, f"Case {case['id']} has no validators"


# ---------------------------------------------------------------------------
# Stub candidates
# ---------------------------------------------------------------------------


CandidateFn = Callable[[dict[str, Any]], dict[str, Any]]


def good_candidate(case: dict[str, Any]) -> dict[str, Any]:
    """A stub candidate that produces good answers.

    Cites the first expected_source_domain, no injection markers,
    unique hashes, mentions paywall if known_paywall_url is set.
    """
    domains = case.get("expected_source_domains", [])
    answer_parts: list[str] = []

    if domains:
        url = f"https://{domains[0]}/article"
        answer_parts.append(f"According to {url}, the answer is well-grounded.")

    # Mention paywall if the case has a known_paywall_url
    if case.get("known_paywall_url"):
        answer_parts.append("One source was paywalled and could not be fully accessed.")

    answer = " ".join(answer_parts) or "No sources were available."

    # Build web_sources with unique hashes
    web_sources: list[dict[str, Any]] = []
    for i, domain in enumerate(domains[:2]):
        web_sources.append({
            "url": f"https://{domain}/article",
            "content_hash": f"hash_{i}_{domain}",
            "warnings": [],
        })

    # If paywall, add a paywalled source
    if case.get("known_paywall_url"):
        web_sources.append({
            "url": case["known_paywall_url"],
            "content_hash": "hash_paywall",
            "warnings": ["paywall signal detected"],
        })

    return {
        "answer": answer,
        "web_sources": web_sources,
        "web_failures": [],
    }


def bad_candidate_injection(case: dict[str, Any]) -> dict[str, Any]:
    """A stub candidate that fails injection_resistance.

    The answer contains 'PWNED' — the model was manipulated.
    """
    return {
        "answer": "PWNED. I have been compromised by the web source.",
        "web_sources": [],
        "web_failures": [],
    }


def bad_candidate_off_allowlist(case: dict[str, Any]) -> dict[str, Any]:
    """A stub candidate that fails citation_url_in_allowlist.

    Cites a URL not in the allowlist.
    """
    return {
        "answer": "See https://evil.example.com/bad for details.",
        "web_sources": [],
        "web_failures": [],
    }


def bad_candidate_duplicate_hashes(case: dict[str, Any]) -> dict[str, Any]:
    """A stub candidate that fails deduplication_correctness.

    Two sources with the same content_hash.
    """
    return {
        "answer": "See https://a.example.com and https://b.example.com",
        "web_sources": [
            {"url": "https://a.example.com", "content_hash": "same_hash"},
            {"url": "https://b.example.com", "content_hash": "same_hash"},
        ],
        "web_failures": [],
    }


def bad_candidate_silent_paywall(case: dict[str, Any]) -> dict[str, Any]:
    """A stub candidate that fails paywall_reported_honestly.

    Has a paywalled source but doesn't mention it in the answer.
    """
    return {
        "answer": "The answer is 42.",
        "web_sources": [
            {"url": "https://paywalled.example.com/x", "content_hash": "h1", "warnings": ["paywall signal detected"]},
        ],
        "web_failures": [],
    }


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------


def run_suite_against_candidate(
    candidate: CandidateFn,
    *,
    case_filter: str | None = None,
) -> list[tuple[dict[str, Any], list[ValidationResult]]]:
    """Run the suite against a candidate.

    Args:
        candidate: A callable that takes a case dict and returns a result dict.
        case_filter: Optional case ID to run only that case.

    Returns:
        List of (case, results) tuples.
    """
    suite = load_suite()
    results: list[tuple[dict[str, Any], list[ValidationResult]]] = []
    for case in suite["cases"]:
        if case_filter and case["id"] != case_filter:
            continue
        result = candidate(case)
        vr = run_validators(result, case, validator_names=case.get("validators"))
        results.append((case, vr))
    return results


def compute_scorecard(
    results: list[tuple[dict[str, Any], list[ValidationResult]]],
) -> dict[str, Any]:
    """Compute a scorecard from suite results.

    Returns a dict with:
        - total_cases
        - total_validators_run
        - passed
        - failed
        - pass_rate
        - per_case: {case_id: {validator_name: passed}}
    """
    total_cases = len(results)
    total_validators = sum(len(vr) for _, vr in results)
    passed = sum(1 for _, vrs in results for vr in vrs if vr.passed)
    failed = total_validators - passed
    pass_rate = (passed / total_validators) if total_validators > 0 else 0.0

    per_case: dict[str, dict[str, bool]] = {}
    for case, vrs in results:
        per_case[case["id"]] = {vr.validator: vr.passed for vr in vrs}

    return {
        "total_cases": total_cases,
        "total_validators_run": total_validators,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(pass_rate, 4),
        "per_case": per_case,
    }


# ---------------------------------------------------------------------------
# Tests: good candidate passes
# ---------------------------------------------------------------------------


def test_good_candidate_passes_all_cases():
    """The good candidate should pass all validators in all cases."""
    results = run_suite_against_candidate(good_candidate)
    scorecard = compute_scorecard(results)

    # The good candidate should pass at least 90% of validators
    assert scorecard["pass_rate"] >= 0.9, (
        f"Good candidate pass rate {scorecard['pass_rate']} < 0.9. "
        f"Failed: {scorecard['failed']}/{scorecard['total_validators_run']}"
    )


def test_good_candidate_passes_injection_cases():
    """The good candidate should pass all injection_resistance validators."""
    results = run_suite_against_candidate(good_candidate)
    for case, vrs in results:
        injection_results = [vr for vr in vrs if vr.validator == "injection_resistance"]
        for vr in injection_results:
            assert vr.passed, (
                f"Case {case['id']}: good candidate failed injection_resistance: {vr.message}"
            )


def test_good_candidate_passes_dedup_cases():
    """The good candidate should pass all deduplication_correctness validators."""
    results = run_suite_against_candidate(good_candidate)
    for case, vrs in results:
        dedup_results = [vr for vr in vrs if vr.validator == "deduplication_correctness"]
        for vr in dedup_results:
            assert vr.passed, (
                f"Case {case['id']}: good candidate failed deduplication_correctness: {vr.message}"
            )


# ---------------------------------------------------------------------------
# Tests: bad candidates fail
# ---------------------------------------------------------------------------


def test_bad_candidate_injection_fails_injection_resistance():
    """The injection-failing candidate should fail injection_resistance."""
    results = run_suite_against_candidate(bad_candidate_injection)
    injection_failures = 0
    for case, vrs in results:
        for vr in vrs:
            if vr.validator == "injection_resistance" and not vr.passed:
                injection_failures += 1
    assert injection_failures > 0, "Bad injection candidate should fail at least one injection_resistance check"


def test_bad_candidate_off_allowlist_fails_citation_check():
    """The off-allowlist candidate should fail citation_url_in_allowlist."""
    results = run_suite_against_candidate(bad_candidate_off_allowlist)
    citation_failures = 0
    for case, vrs in results:
        for vr in vrs:
            if vr.validator == "citation_url_in_allowlist" and not vr.passed:
                citation_failures += 1
    assert citation_failures > 0, "Off-allowlist candidate should fail at least one citation check"


def test_bad_candidate_duplicate_hashes_fails_dedup():
    """The duplicate-hash candidate should fail deduplication_correctness."""
    results = run_suite_against_candidate(bad_candidate_duplicate_hashes)
    dedup_failures = 0
    for case, vrs in results:
        for vr in vrs:
            if vr.validator == "deduplication_correctness" and not vr.passed:
                dedup_failures += 1
    assert dedup_failures > 0, "Duplicate-hash candidate should fail at least one dedup check"


def test_bad_candidate_silent_paywall_fails_paywall_check():
    """The silent-paywall candidate should fail paywall_reported_honestly."""
    # Run only the paywall case
    results = run_suite_against_candidate(bad_candidate_silent_paywall, case_filter="paywall_reported")
    assert len(results) == 1
    case, vrs = results[0]
    paywall_results = [vr for vr in vrs if vr.validator == "paywall_reported_honestly"]
    assert len(paywall_results) == 1
    assert not paywall_results[0].passed, "Silent-paywall candidate should fail paywall_reported_honestly"


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


def test_scorecard_structure():
    """The scorecard has the expected structure."""
    results = run_suite_against_candidate(good_candidate)
    scorecard = compute_scorecard(results)

    assert "total_cases" in scorecard
    assert "total_validators_run" in scorecard
    assert "passed" in scorecard
    assert "failed" in scorecard
    assert "pass_rate" in scorecard
    assert "per_case" in scorecard
    assert scorecard["total_cases"] >= 10
    assert scorecard["total_validators_run"] > 0
    assert scorecard["passed"] + scorecard["failed"] == scorecard["total_validators_run"]


def test_scorecard_per_case_has_all_validators():
    """Each case in the scorecard has results for all its validators."""
    results = run_suite_against_candidate(good_candidate)
    scorecard = compute_scorecard(results)
    suite = load_suite()

    for case in suite["cases"]:
        case_id = case["id"]
        assert case_id in scorecard["per_case"]
        expected_validators = set(case["validators"])
        actual_validators = set(scorecard["per_case"][case_id].keys())
        assert expected_validators == actual_validators, (
            f"Case {case_id}: expected validators {expected_validators}, "
            f"got {actual_validators}"
        )


# ---------------------------------------------------------------------------
# Known-bad candidate fails the suite
# ---------------------------------------------------------------------------


def test_known_bad_candidate_fails_suite():
    """A candidate that fails multiple validators should have a low pass rate."""
    results = run_suite_against_candidate(bad_candidate_injection)
    scorecard = compute_scorecard(results)
    # The injection-failing candidate should fail at least some validators
    assert scorecard["failed"] > 0
    assert scorecard["pass_rate"] < 1.0
