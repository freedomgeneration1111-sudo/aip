# ADR-016: Evaluation Runs and Model/Workflow Qualification

**Date:** 2026-07-28  
**Status:** PROPOSED  
**DEFINER:** B. Moses Jorgensen

## Context

AIP already has three related but incomplete capabilities:

- Cohort Synthesis fans one prompt out to multiple models and synthesizes convergence and divergence.
- Retrieval evaluation compares retrieval modes against golden cases and saves a baseline.
- ADR-015 specifies future governed parallel agent execution, cost estimation, dry-run planning, and model-slot discipline.

AIP does not yet have a first-class mechanism that answers:

> For this specific task class, can a cheaper model or alternate workflow produce an accepted result at lower total cost and acceptable risk?

The DEFINER is dogfooding AIP and wants to compare AIP results with a Ringer-like external workflow while also learning which inexpensive models are qualified for bounded jobs. Ad hoc chat comparisons are insufficient because they do not preserve fixtures, validators, configuration, retries, review burden, or a repeatable scorecard.

## Decision

AIP will implement **Evaluation Runs** as a first-class platform workflow.

An Evaluation Run executes a versioned suite of controlled cases against two or more candidates, validates each output, records cost/latency/retries/review effort, and produces a task-specific qualification scorecard.

Evaluation Runs are not a domain agent, not the Fleet Coordinator, and not a replacement for Cohort Synthesis.

## Candidate kinds

An `EvaluationCandidate` may be:

- `direct_model` — direct provider/model call;
- `aip_ask` — current AIP retrieval and synthesis path;
- `aip_cohort` — Cohort/Judge/Synth path;
- `external_runner` — adapter to a Ringer-like or other external workflow;
- `agent_workflow` — future AgentRun/Fleet execution.

All candidates in one run receive equivalent case inputs, corpus snapshot, timeouts, and scoring policy. Candidate-specific constraints must be recorded rather than hidden.

## Data model

### EvaluationSuite

```python
@dataclass(frozen=True)
class EvaluationSuite:
    suite_id: str
    version: int
    name: str
    task_type: str
    description: str
    case_ids: list[str]
    validator_set_id: str
    scoring_policy_id: str
    corpus_snapshot_id: str | None
    created_at: datetime
```

### EvaluationCase

```python
@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    input_artifact_id: str
    fixture_artifact_ids: list[str]
    expected_properties: dict[str, Any]
    timeout_seconds: int
    tags: list[str]
    human_rubric: str | None
```

### EvaluationCandidate

```python
@dataclass(frozen=True)
class EvaluationCandidate:
    candidate_id: str
    kind: Literal[
        "direct_model",
        "aip_ask",
        "aip_cohort",
        "external_runner",
        "agent_workflow",
    ]
    route: str
    provider: str | None
    model: str | None
    prompt_hash: str
    config_hash: str
```

### EvaluationRun and case results

```python
@dataclass
class EvaluationRun:
    run_id: str
    suite_id: str
    candidate_ids: list[str]
    status: Literal["planned", "running", "complete", "failed", "cancelled"]
    seed: int | None
    trace_id: str
    started_at: datetime | None
    completed_at: datetime | None

@dataclass
class EvaluationCaseResult:
    run_id: str
    case_id: str
    candidate_id: str
    output_artifact_id: str | None
    accepted: bool | None
    validator_results: list[dict[str, Any]]
    failure_class: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    model_cost_usd: float
    retry_count: int
    review_seconds: int
```

## Validator policy

Validators are versioned and composable:

- command/exit-code validator;
- JSON Schema validator;
- required-file or required-field validator;
- exact/normalized text validator;
- citation/quotation support validator;
- task-specific Python validator;
- human rubric;
- LLM judge, advisory by default.

A result may be marked accepted only by a deterministic validator, explicit human approval, or a documented composite policy with a deterministic boundary. An LLM judge alone cannot create a production qualification.

Every validator set must include fixtures containing at least one known-good and one known-bad output. Validator quality is part of the suite result. False acceptance of a known-bad fixture invalidates the run.

## Scorecard

At minimum, scorecards report:

- pass rate;
- first-pass pass rate;
- retry rate;
- failure classes;
- median and p95 latency;
- model cost;
- review time;
- validator false-accept and false-reject rates where measurable;
- accepted-result cost;
- task-specific quality metrics;
- corpus/prompt/config/model identifiers.

Accepted-result cost is:

```text
(model cost + retry cost + configured human review cost) / accepted cases
```

The human review rate is configuration, not a hidden constant.

## Qualification records

An Evaluation Run may propose a `RouteQualification`:

```python
@dataclass(frozen=True)
class RouteQualification:
    task_type: str
    candidate_id: str
    suite_id: str
    suite_version: int
    confidence: float
    constraints: dict[str, Any]
    qualified_at: datetime
    valid_until: datetime | None
    invalidation_keys: list[str]
    status: Literal["proposed", "approved", "expired", "revoked"]
```

Invalidation keys may include model alias/version, prompt hash, validator version, provider route, AIP commit, retrieval configuration, or corpus snapshot.

The DEFINER approves production routing changes. Evaluation Runs do not silently rewrite model slots.

## Storage and corpus policy

Raw outputs, fixtures, and traces are stored in evaluation tables and artifact storage. They are not automatically added to ordinary knowledge corpora.

Reasons:

- failed outputs are intentionally present;
- adversarial fixtures may contain false statements;
- benchmark duplication would pollute retrieval;
- automatic ingestion would make model comparison change the system being measured.

Approved conclusions, qualification records, and explicitly promoted outputs may enter the audit/trajectory stream or a designated evaluation corpus.

## Reuse of existing AIP infrastructure

- Reuse provider/model invocation and token/cost records.
- Reuse ADR-009 async fan-out patterns for parallel candidates.
- Reuse ADR-013 suite/baseline conventions and VIGIL sampling pattern.
- Reuse artifact, event, trace, and review stores.
- Reuse Judge/Synth only for advisory comparison narratives, not acceptance.
- Later link an `AgentRun` to `evaluation_run_id`; do not make EvaluationRun a competing agent execution envelope.

## API and CLI MVP

```text
POST /api/v1/evaluations/suites
POST /api/v1/evaluations/runs
GET  /api/v1/evaluations/runs/{run_id}
GET  /api/v1/evaluations/runs/{run_id}/scorecard
POST /api/v1/evaluations/qualifications/{id}/approve

aip eval suite list
aip eval run <suite> --candidate ... --candidate ...
aip eval scorecard <run_id>
```

The first UI may be a simple run form and score table. Full visualization is not required for MVP.

## Initial dogfood suites

1. Transcript import and role reconstruction.
2. Source-grounded Q&A with citation support.
3. Exact quotation extraction without stitching.
4. Corpus/domain routing and tagging.
5. Small code diagnosis with a deterministic regression test.
6. Bounded code edit with expected files and test command.

Each suite should begin with 10–20 real cases and grow only when dogfood failures reveal missing coverage.

## Consequences

### Positive

- Cheap models earn task-specific trust instead of receiving global approval.
- AIP can compare itself to external workflows under controlled conditions.
- Routing decisions become evidence-based and reversible.
- Dogfood failures become reusable regression cases.
- Future Fleet dispatch can consult qualifications without embedding evaluation logic in the Coordinator.

### Costs

- Suites and validators require maintenance.
- Some tasks cannot be judged deterministically and still need human review.
- Provider/model drift requires revalidation.
- Evaluation artifacts require storage and retention policy.

## Rejected alternatives

### Put the feature inside Cohort Synthesis

Rejected. Cohort is a synthesis workflow and currently ingests panel responses. Evaluation needs controlled fixtures, validators, isolation, and scorecards.

### Put the feature inside VIGIL

Rejected. VIGIL may schedule regression samples but should not own interactive benchmark design or route qualification.

### Wait for the full Fleet Coordinator

Rejected. Model/workflow qualification is useful during current dogfooding and can use existing dispatch/storage infrastructure. The schema remains compatible with future AgentRun linkage.

### Let an LLM judge decide everything

Rejected. It recreates the same uncertainty the evaluation is intended to reduce.

## Roadmap placement

- **Dogfood Phase D3:** Evaluation Run schemas/store, deterministic validators, direct-model and AIP Ask candidates, scorecard.
- **D3.1:** AIP Cohort and external-runner adapters.
- **D3.2:** route qualification approval and Models-page display.
- **Phase 3B+:** Fleet candidates and Coordinator consumption of approved qualifications.
- **Phase 3C:** VIGIL/CURATOR longitudinal revalidation and expiry handling.
