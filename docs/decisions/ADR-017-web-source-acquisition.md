# ADR-017: Web Source Acquisition as a Core Platform Capability

**Date:** 2026-07-28  
**Status:** ACCEPTED (2026-07-30) — D2.0 through D2.5 delivered  
**DEFINER:** B. Moses Jorgensen

> **Implementation status:** All six delivery slices (WS-1 through WS-6)
> are complete and pushed to `feat/multi-corpus`. See the delivery
> summary at the bottom of this ADR for the per-slice status.

## Context

The current roadmap treats web search as blocked on HERALD. ADR-015 correctly gives HERALD a `tool:web_search` capability, but HERALD is a future research-domain extension. Search, fetch, extraction, provenance, and controlled promotion are reusable platform functions needed now by Ask, messaging, Evaluation Runs, and later agents.

Binding web access to HERALD would create several problems:

- ordinary AIP questions could not use current public sources without invoking a domain agent;
- messaging ingress could not resolve a shared URL through a common policy;
- every future extension could implement its own unsafe fetch/extraction path;
- web provenance and corpus promotion would be inconsistent;
- HERALD would own infrastructure rather than consume it.

## Decision

AIP will implement **Web Source Acquisition** as a core read-oriented platform service. HERALD and other extensions consume this service through declared capabilities.

The platform separates:

1. search discovery;
2. resource fetching;
3. content extraction;
4. provenance/snapshot recording;
5. ephemeral grounding;
6. explicit corpus promotion.

## Interfaces

```python
class SearchProvider(Protocol):
    async def search(
        self,
        query: str,
        *,
        limit: int,
        freshness_days: int | None = None,
        domains: list[str] | None = None,
    ) -> list["SearchResult"]: ...

class WebFetcher(Protocol):
    async def fetch(self, url: str, policy: "FetchPolicy") -> "FetchedResource": ...

class ContentExtractor(Protocol):
    async def extract(self, resource: "FetchedResource") -> "ExtractedDocument": ...
```

The first real provider is configuration, not architecture. Tests use a fake provider.

## Schemas

```python
@dataclass(frozen=True)
class SearchResult:
    provider: str
    query: str
    rank: int
    url: str
    title: str
    snippet: str
    published_at: datetime | None
    provider_metadata: dict[str, Any]

@dataclass(frozen=True)
class FetchedResource:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    content_bytes_ref: str
    retrieved_at: datetime
    response_headers: dict[str, str]
    content_hash: str

@dataclass(frozen=True)
class ExtractedDocument:
    source_url: str
    canonical_url: str | None
    title: str
    text: str
    authors: list[str]
    published_at: datetime | None
    retrieved_at: datetime
    content_hash: str
    extraction_method: str
    warnings: list[str]
    snapshot_artifact_id: str | None
```

## Modes

### Ephemeral grounding

Search and fetched text are assembled into a request context and displayed as answer sources. No corpus write occurs. The trace records provider, query, result rank, URL, fetch status, extraction warnings, and source usage.

### Explicit promotion

The DEFINER may promote a fetched document into a selected document/research corpus. Promotion stores source URL, final/canonical URL, retrieval timestamp, content hash, extraction method, and snapshot reference. Duplicate hashes should not create duplicate corpus content.

A search result is not automatically ingested merely because it appeared in context.

## Trust and prompt-injection boundary

Remote content is untrusted data.

- Web text must be enclosed and labeled as source material.
- Instructions inside remote content must not alter system policy, capability permissions, corpus scope, or tool execution.
- Search snippets are discovery metadata, not authoritative evidence.
- Failed fetches, paywalls, authentication requirements, robots restrictions, unsupported formats, and truncated extraction must be surfaced.
- URL redirects and canonical URLs must be preserved.
- URL schemes are allowlisted (`http` and `https` for MVP).
- Private-network and loopback targets are blocked unless an explicit local-resource policy allows them, preventing SSRF.
- Response size, redirect count, timeout, and content type are bounded.

## Capability model

Capabilities are separate:

- `tool:web_search`
- `tool:web_fetch`
- `read:web_snapshot`
- `propose:corpus_ingest`
- `write:<corpus>:draft`

Read-only search does not imply corpus-write authority. A future HERALD actor may search and propose an ingest without being allowed to publish or mutate other corpora.

## Provider policy

The service supports multiple adapters. Configuration selects the default and optional fallbacks. Provider-specific fields stay in `provider_metadata`; core code must not depend on them.

```toml
[web]
enabled = true
default_provider = "configured-provider"
max_results = 8
fetch_timeout_seconds = 20
max_resource_bytes = 20000000
snapshot_enabled = true
allow_private_networks = false

[web.providers.configured-provider]
api_key_env = "AIP_WEB_SEARCH_API_KEY"
```

Secrets are read from environment/secret storage and never included in corpus turns or traces.

## API surface

```text
POST /api/v1/web/search
POST /api/v1/web/fetch
POST /api/v1/web/ground
POST /api/v1/web/promote
GET  /api/v1/web/sources/{source_id}
```

`/ground` returns source records suitable for the existing source panel. `/promote` passes through the existing approval and ingestion boundaries.

## Ask integration

The Ask page gains an explicit Web control:

- Off: corpus-only/current behavior.
- On: corpus retrieval plus ephemeral web grounding.
- Web only: optional later mode for current-public-source questions.

The answer status strip must distinguish corpus sources from web sources and state when web retrieval failed or contributed nothing.

## Messaging integration

A URL received through Remote Ingress is passed to this service, not fetched directly by the messaging adapter. This centralizes SSRF protection, provenance, extraction, deduplication, and promotion rules.

## Evaluation

A web-grounding suite should test:

- correct source selection;
- citation-to-source support;
- quotation fidelity;
- duplicate/canonical URL handling;
- paywall/fetch failure honesty;
- prompt injection resistance;
- stale-versus-current source handling;
- promotion deduplication.

Live provider tests are optional/manual. CI uses fake search results and local fixtures.

## Delivery plan

### WS-1 — foundation

Schemas, `SearchProvider`, fake provider, configuration, and unit tests.

### WS-2 — fetch and extraction

Bounded HTTP fetcher, HTML extraction, PDF handoff to the existing document pipeline, provenance records, local fixtures.

### WS-3 — first provider and API

One real provider adapter, search/fetch routes, health status, clear not-configured behavior.

### WS-4 — Ask integration

Web control, source panel integration, trace events, answer citations.

### WS-5 — promotion

Explicit source-to-corpus proposal and approval, deduplication by content hash.

### WS-6 — qualification

Evaluation Run suite for web-grounded answer quality.

## Consequences

### Positive

- Current public information becomes available before HERALD exists.
- One secure/provenance-aware implementation serves Ask, messaging, and future agents.
- HERALD stays a domain workflow rather than becoming infrastructure.
- Corpus writes remain explicit and auditable.

### Costs

- Search providers and web pages change.
- Extraction is imperfect and requires warnings.
- Snapshot retention and copyright policy need operational limits.
- Network access creates a larger security surface.

## Rejected alternatives

### Wait for HERALD

Rejected. It unnecessarily blocks current dogfood use and couples shared infrastructure to one future extension.

### Let each extension call a provider directly

Rejected. It duplicates credentials, security controls, provenance, and extraction behavior.

### Automatically ingest every fetched page

Rejected. Search context is transient, may be low quality, and should not silently contaminate the corpus.

## Roadmap placement

- **Dogfood Phase D2:** WS-1 through WS-4.
- **D2.1:** promotion and deduplication.
- **D3:** web-grounding evaluation suite.
- **Phase 3A-1:** HERALD consumes the platform capability.

---

## Delivery Summary (2026-07-30)

All six delivery slices are complete, tested, and pushed to `feat/multi-corpus`.

### WS-1 (D2.0) — Foundation ✅

- `src/aip/foundation/schemas/web.py` — 8 frozen dataclasses (SearchResult, FetchPolicy, FetchedResource, ExtractedDocument, WebSourceRecord, WebSnapshotRecord, WebProviderConfig, SearchOptions) + sha256_hex / normalize_text_for_hash helpers
- `src/aip/foundation/protocols/web.py` — 5 runtime_checkable Protocols (SearchProvider, WebFetcher, ContentExtractor, WebSnapshotStore, WebSourceStore)
- `src/aip/adapter/web/policy.py` — pure-stdlib SSRF guard (is_url_allowed + is_ip_allowed; IPv4/IPv6 literals, IPv4-mapped IPv6, obfuscated forms, private/loopback/link-local/multicast/unspecified/reserved denials)
- `src/aip/adapter/web/fake_provider.py` — FakeSearchProvider, FakeWebFetcher, FakeContentExtractor (CI, no network)
- `src/aip/adapter/web/snapshot.py` — InMemoryWebSnapshotStore + InMemoryWebSourceStore (dedup by content_hash)
- 144 tests

### WS-2 (D2.1) — Bounded HTTP fetcher + extractors + provenance ✅

- `src/aip/adapter/web/http_fetcher.py` — HttpxWebFetcher with SSRF at every redirect hop, DNS-rebinding defense, max_bytes truncation, sensitive-header stripping, lifecycle registration, optional bytes_sink for body persistence
- `src/aip/adapter/web/extractors/{html,pdf,plain_text,factory}.py` — BeautifulSoup4+lxml HTML extractor (title, canonical, authors, published_at, paywall/login-wall detection), pypdf PDF handoff, plain-text, content-type factory
- `src/aip/adapter/web/provenance.py` — build_web_source_record, make_source_id, redact_provider_metadata
- `src/aip/adapter/web/lifecycle.py` — BackgroundTaskRegistry (W5 minimal prerequisite)
- 78 tests

### WS-3 (D2.2) — Tavily provider + API routes + health ✅

- `src/aip/adapter/web/providers/{tavily,factory}.py` — TavilySearchProvider (pluggable key_loader, never cached on instance, redaction), build_search_provider, is_provider_configured, provider_status
- `src/aip/adapter/api/routes/web.py` — 4 routes: POST /web/search, POST /web/fetch, POST /web/ground, GET /web/sources/{id}
- `src/aip/adapter/api/routes/health.py` — web health block (enabled, provider, provider_state, fetcher_wired, store_wired)
- `src/aip/adapter/api/dependencies.py` — 6 web_* container attrs
- `config/aip.config.toml.example` — [web] + [web.providers.tavily]
- `.env.example` — AIP_WEB_SEARCH_API_KEY
- 49 tests

### WS-3.5 — Lifespan wiring ✅

- `src/aip/adapter/api/app.py` — _wire_web_source_acquisition helper (5-step, failure-tolerant), shutdown handler cancels in-flight fetches, bytes_sink wired to snapshot store
- 14 tests

### WS-4 (D2.3) — Ask integration ✅

- `src/aip/foundation/schemas/ask.py` — AskResult gains web_grounding, web_sources, web_failures
- `src/aip/adapter/api/routes/_augmented_context.py` — build_web_source_context_block (BEGIN_WEB_SOURCE / END_WEB_SOURCE markers, prompt-injection boundary), load_web_grounding_prompt_fragment
- `prompts/web_grounding.md` — system-prompt fragment (4 injection defense rules, citation rule, honesty rules)
- `src/aip/adapter/api/routes/ask.py` — web_grounding toggle on POST /ask
- `src/aip/adapter/api/routes/sources.py` — kind=corpus|web discriminator
- 32 tests

### WS-5 (D2.4) — Explicit source promotion ✅

- `src/aip/adapter/web/promotion.py` — WebSourcePromoter (approval gate, dedup by content_hash, doc_version increment, source_model='web', full provenance metadata)
- `src/aip/adapter/api/routes/web.py` — POST /web/promote route
- 24 tests

### WS-6 (D2.5) — Web-grounding Evaluation Suite ✅

- `src/aip/adapter/web/eval_validators.py` — 5 deterministic validators (citation_url_in_allowlist, citation_count_in_range, paywall_reported_honestly, injection_resistance, deduplication_correctness) + ValidationResult + VALIDATORS registry + run_validators
- `tests/acceptance/web_grounding_suite.yaml` — 16 cases (version 1.0.0)
- 40 tests

### Additional fixes (2026-07-30)

- **bytes_sink fix** — HttpxWebFetcher now persists body to snapshot store during streaming via pluggable bytes_sink; closes the "bytes_unavailable 500" gap
- **Multi-Cast retrieval telemetry** — ModelCouncilResponse gains 6 retrieval fields (retrieval_attempted, context_assembled, active_corpus_ids, source_count, augmented_sources, retrieval_warnings); GUI renders sources + warnings on per-model cards
- **Corpus selection persistence** — GuiState.active_corpus_ids survives reset_session(); ensure_session() re-applies to replacement sessions
- **FTS5 syntax error fix** — sanitize_fts_query now strips '/' (file paths like gui/pages/ask.py no longer crash FTS5)
- **Retrieval gate fix** — backend gates on session_id (not fake turn_id); GUI passes turn_id="" honestly

### Totals

- ~490 tests added across all slices (all passing)
- ~20 new source files in src/aip/adapter/web/ + foundation/{schemas,protocols}/web.py
- 5 API routes + 1 Ask toggle + 1 sources discriminator + 1 health block
- 0 regressions in existing tests
- ruff + mypy clean on all new files
