# ADR-018: Remote Ingress and Messaging Gateway

**Date:** 2026-07-28  
**Status:** PROPOSED  
**DEFINER:** B. Moses Jorgensen

## Context

AIP is local-first and currently expects the operator to work near the machine that runs the backend or manually export/copy files into the ingestion path. The DEFINER needs to:

- send documents and AI-chat transcripts from a phone or another device;
- send URLs and notes while working on other platforms;
- query AIP directly from a messaging application;
- keep one corpus current instead of allowing knowledge to fragment by device or AI platform.

ADR-010 proposes a deferred browser extension for scraping specific AI interfaces and posting to localhost. That remains useful later, but it does not provide transport-neutral remote ingress, mobile document delivery, direct AIP queries, receipts, or secure device identity.

## Decision

AIP will implement a **Remote Ingress and Messaging Gateway** with a provider-neutral transport protocol and a canonical `RemoteEnvelope`.

The first dogfood transport will be a Telegram bot using long polling. This is an implementation choice, not a permanent platform dependency. Long polling allows a locally running AIP instance to receive updates through outbound HTTPS without exposing the AIP API publicly.

## Boundaries

The messaging adapter is responsible for:

- transport authentication and sender identity;
- receiving text, metadata, and attachments;
- sending replies, receipts, and result files;
- mapping transport events into `RemoteEnvelope`.

It is not responsible for:

- parsing every document format;
- fetching arbitrary URLs directly;
- deciding corpus truth;
- performing retrieval or model calls itself;
- bypassing approval/capability gates.

Those functions remain in the existing ingestion, retrieval, model, web acquisition, and review layers.

## Transport protocol

```python
class MessageTransport(Protocol):
    transport_id: str

    async def start(self, handler: "RemoteEnvelopeHandler") -> None: ...
    async def stop(self) -> None: ...
    async def send_text(self, destination: str, text: str, **options: Any) -> str: ...
    async def send_document(
        self,
        destination: str,
        artifact_id: str,
        *,
        caption: str | None = None,
    ) -> str: ...
```

Transport implementations do not import orchestration internals. They call a gateway service through stable schemas.

## Canonical envelope

```python
@dataclass(frozen=True)
class RemoteEnvelope:
    transport: str
    sender_id: str
    conversation_id: str
    external_message_id: str
    received_at: datetime
    text: str | None
    attachments: list["RemoteAttachment"]
    urls: list[str]
    requested_action: Literal[
        "auto",
        "ask",
        "ask_web",
        "ingest",
        "status",
        "cancel",
    ]
    target_corpus_id: str | None
    reply_to_external_id: str | None
    metadata: dict[str, Any]
    content_hash: str

@dataclass(frozen=True)
class RemoteAttachment:
    transport_file_id: str
    filename: str
    declared_mime_type: str | None
    declared_size_bytes: int | None
    downloaded_artifact_id: str | None
```

Idempotency is enforced by `(transport, conversation_id, external_message_id)` and content hash.

## Identity and authorization

The first release is single-DEFINER:

- every transport has an allowlist of sender/account IDs;
- unknown senders receive no operational detail and cannot queue work;
- allowlisted identities map to the `definer` principal;
- transport tokens are environment/secret values, never corpus data;
- every action receives a trace ID;
- audit events record transport, mapped principal, action, target corpus, and outcome without logging secrets.

Future multi-user support must add explicit principal mapping and per-corpus permissions rather than broadening the initial allowlist.

## Commands and interaction

### Core commands

```text
/ask <question>
/ask-web <question>
/ingest [corpus_id]
/corpus <corpus_id>
/status
/cancel <job_id>
/help
```

### Natural behavior

- A plain supported document from an allowlisted sender creates an ingest job using the current/default target corpus.
- A pasted transcript is classified and sent to the existing conversation importer.
- A URL is handed to Web Source Acquisition and returns a preview or ingest proposal.
- A plain question may default to `/ask` only when no attachment or explicit ingest context is present.
- Ambiguous messages produce a small action choice rather than silently guessing.

## Ingress pipeline

```text
transport update
  -> sender allowlist
  -> RemoteEnvelope normalization
  -> idempotency check
  -> attachment download to quarantine
  -> size/MIME/hash validation
  -> action classification
  -> existing ingest / Ask / web service
  -> audit event
  -> receipt or answer
```

### Quarantine and staging

Attachments are written to a staging area first. The gateway:

- enforces configurable size limits;
- computes a cryptographic content hash;
- verifies/sniffs type instead of trusting only the transport MIME type;
- rejects executable or unsupported content by policy;
- never executes macros, scripts, archives, or binaries;
- removes or expires staged files after success/failure according to retention policy.

Optional malware scanning can be added without changing the envelope or ingestion contract.

## Transcript ingestion

Supported fast paths:

- pasted plain text/Markdown;
- exported JSON from supported chat platforms;
- HTML, PDF, or text exports;
- generic role-labeled transcripts.

The parser records source platform when detectable, original filename/message ID, participant roles, timestamps when available, and parsing warnings.

A shared URL to a logged-in AI conversation may not be fetchable. The gateway must reply honestly and request pasted/exported content rather than pretending the page was ingested.

## Query flow

`/ask` uses the existing session/retrieval/synthesis path with a transport-created session bound to the selected corpus scope. Results include:

- concise answer text;
- source references suitable for the messaging client;
- dogfood health/degradation warning when retrieval or model routing is impaired;
- trace ID;
- optional “send full answer/artifact” action when the answer exceeds message limits.

`/ask-web` additionally invokes ephemeral web grounding through ADR-017. The transport adapter does not perform its own search.

## Corpus selection

Each authorized conversation has a small gateway state record:

```text
transport + conversation_id
  -> active_corpus_id
  -> default_action
  -> last_session_id
  -> last_job_id
```

A corpus change is explicit and acknowledged. Attachments are never silently routed to a newly inferred corpus without a receipt showing the destination.

## Receipts

Every accepted job returns a receipt such as:

```text
INGESTED
job: ing_01...
corpus: definer
source: telegram:message:12345
parsed: 42 turns
new: 40
duplicates: 2
warnings: 1 timestamp could not be parsed
trace: tr_01...
```

Every rejected/failed job states the reason and whether staged content was retained or deleted.

## Telegram implementation

The initial adapter uses `getUpdates` long polling and the Bot API file-download mechanism. It supports text and general documents first. Photos, voice, and rich interactive controls are later increments.

Configuration:

```toml
[messaging]
enabled = true

[messaging.telegram]
enabled = true
token_env = "AIP_TELEGRAM_BOT_TOKEN"
allowed_sender_ids = ["<definer-id>"]
default_corpus_id = "definer"
mode = "long_polling"
max_attachment_bytes = 20000000
```

The token and sender IDs must not appear in status payloads, logs, exports, or corpus turns.

## API/service placement

Suggested modules:

```text
src/aip/foundation/schemas/remote_ingress.py
src/aip/foundation/protocols/messaging.py
src/aip/orchestration/remote_ingress_service.py
src/aip/adapter/messaging/telegram.py
src/aip/adapter/remote_ingress_store.py
src/aip/adapter/api/routes/remote_ingress.py
```

The adapter starts/stops in application lifespan with deterministic teardown. It should use `start_policy="scheduled"` as a read/receive service, while every write-capable action still passes through the appropriate gate/service.

## Testing

CI uses a fake transport and recorded update/file fixtures. Required tests:

- allowlisted and denied sender behavior;
- duplicate update idempotency;
- attachment size/type rejection;
- transcript classification and ingestion handoff;
- URL handoff to web service;
- `/ask` session/corpus binding;
- message chunking and artifact fallback;
- token redaction from logs/status;
- graceful stop with no lingering tasks or threads;
- restart resumes from the last confirmed external update safely.

Live Telegram tests are manual/optional.

## Relationship to ADR-010

ADR-010 remains deferred as a browser capture convenience. When resumed, it should produce the same `RemoteEnvelope` or call the same ingress service. It must not create a second parsing, deduplication, or corpus-promotion path.

## Consequences

### Positive

- AIP becomes reachable from phones and other devices without fragmenting the corpus.
- Documents, notes, and transcripts can enter the same ingestion pipeline quickly.
- Messaging provides a practical dogfood interface before a desktop/mobile app exists.
- Transport adapters remain replaceable.
- URL security and provenance stay centralized in Web Source Acquisition.

### Costs

- Messaging providers are third-party trust surfaces.
- Attachment handling expands the attack surface.
- Long-running transport tasks require reliable lifecycle cleanup.
- Message length and formatting constraints require response adaptation.

## Rejected alternatives

### Build only the browser extension now

Rejected. It does not solve phone document delivery, generic notes, direct querying, or non-browser devices.

### Expose the full AIP API publicly

Rejected for dogfood. It increases authentication, TLS, network, and attack-surface work. Long polling provides useful reach without public inbound exposure.

### Put provider-specific logic in ingestion routes

Rejected. It would couple Telegram/other transport details to corpus parsing and make future transports costly.

### Auto-ingest everything from any sender

Rejected. Sender authentication, ambiguity handling, and corpus receipts are mandatory.

## Roadmap placement

- **Dogfood Phase D1:** canonical envelope, fake transport, ingress service/store, security and idempotency tests.
- **D1.1:** Telegram long-polling adapter, text/documents, `/ask`, `/status`, receipts.
- **D1.2:** URL handoff and `/ask-web` after ADR-017 WS-3.
- **D1.3:** richer transcript detection, voice/photo/OCR, outbound artifact delivery.
- **Later:** Matrix/WhatsApp/email adapters and ADR-010 browser capture using the same gateway.
