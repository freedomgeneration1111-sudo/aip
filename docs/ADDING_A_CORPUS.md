# Adding a Corpus — Operator Guide

> **QW12 (2026-07-23)** — Onboarding guide for adding a new corpus to AIP Brain.
> Covers programmatic registration, TOML config (planned), migration patterns,
> the sensitive flag, and the MAX_CORPORA budget.

This guide walks through everything you need to add a new corpus to AIP Brain.
A corpus is a separate SQLite database (`{corpus_id}.db`) that holds knowledge
artifacts (turns, documents, code, etc.) and is searchable via the multi-corpus
retrieval pipeline (ADR-008).

---

## Quick Reference

| Step | What | Where |
|------|------|-------|
| 1 | Pick a `corpus_id` + `CorpusType` | `src/aip/foundation/corpus_types.py` |
| 2 | Register at startup | `src/aip/adapter/api/app.py` lifespan |
| 3 | (Optional) Ingest content | CLI: `aip corpus ingest` or `aip corpus ingest-code` |
| 4 | (Optional) Mark sensitive | `sensitive=True` in `register()` |
| 5 | Verify | `GET /api/v1/corpus-registry/corpora` |

---

## Step 1: Pick a corpus_id and CorpusType

AIP supports 4 corpus types (defined in `src/aip/foundation/corpus_types.py`):

| CorpusType | Value | Use Case | Example corpus_id |
|------------|-------|----------|-------------------|
| `CONVERSATION` | `"conversation"` | AI conversation turns (chat exports) | `definer` |
| `CODE` | `"code"` | Python source code (AST-parsed) | `codeforge` |
| `DOCUMENT` | `"document"` | Markdown, text, PDF documents | `branham` |
| `BOOK` | `"book"` | Manuscript chapters | `sparkle_thirst` |

**Naming rules:**
- `corpus_id` must not contain `:` (reserved for `{ext_id}:{role}` namespacing)
- `corpus_id` must not be `"definer"` (reserved core anchor corpus)
- Use lowercase, underscores OK (e.g. `theology_research`, `codeforge`)

---

## Step 2: Register at startup

Corpora are registered in the `app.py` lifespan. Edit
`src/aip/adapter/api/app.py` around line 482:

```python
from aip.foundation.corpus_types import CorpusType

_db_dir = _Path(db_path).parent
_codeforge_db_path = _db_dir / "codeforge.db"
_my_corpus_db_path = _db_dir / "my_corpus.db"  # NEW

_registry = CorpusRegistry(max_corpora=MAX_CORPORA)
await _registry.startup(
    corpora_to_register=[
        ("definer", CorpusType.CONVERSATION, _Path(db_path)),
        ("codeforge", CorpusType.CODE, _codeforge_db_path),
        ("my_corpus", CorpusType.DOCUMENT, _my_corpus_db_path),  # NEW
    ],
)
```

The `db_path` for each corpus is derived from the main `db_path`'s parent
directory (typically `db/`). Each corpus gets its own SQLite file
(`db/my_corpus.db`).

### TOML config (planned — not yet shipped)

In the future, corpora will be declared in `config/aip.config.toml`:

```toml
[corpora.my_corpus]
type = "document"
sensitive = false
access_note = "Research documents for the theology project"
```

And the lifespan will read this section and populate `corpora_to_register`
automatically. Until then, edit `app.py` directly.

---

## Step 3: (Optional) Ingest content

### For CODE corpora
```bash
# One-time ingest
aip corpus ingest-code /path/to/python/source

# Real-time watcher (run alongside the server)
aip corpus watch-code /path/to/python/source &
```

Defaults to `src/aip/` if no path given. The AST parser extracts
functions, classes, and module-level registration calls. Skip rules:
`.pyi`, `test_*.py`, `*_test.py`. Stale detection via `content_hash`.

### For DOCUMENT / CONVERSATION / BOOK corpora
```bash
# Ingest a single file
aip corpus ingest path/to/document.md --source-model document

# Ingest a directory recursively
aip corpus ingest path/to/docs/ --recursive
```

See `aip corpus ingest --help` for source-model options (claude, gpt,
deepseek, glm, gemini, grok, document).

---

## Step 4: (Optional) Mark a corpus as sensitive

Sensitive corpora require session opt-in via `allowed_restricted_corpora`.
This is a 4-layer defense (ADR-008 Rev 3.1 §3.4):

```python
await registry.register(
    corpus_id="branham",
    corpus_type=CorpusType.DOCUMENT,
    db_path=_db_dir / "branham.db",
    sensitive=True,  # NEW — requires session opt-in
    access_note="Restricted research corpus — requires explicit session allowlist",
)
```

When `sensitive=True`:
- `GET /corpus-registry/corpora` returns `"sensitive": true` for the corpus
- The GUI `corpus_selector.py` shows an amber "⚠ sensitive" tag
- `registry.get_stores()` raises `RestrictedCorpusAccessViolation` unless
  the session's `allowed_restricted_corpora` includes the corpus_id
- The `gather_corpus_results` retrieval helper silently suppresses
  sensitive corpora that aren't allowlisted (graceful degrade, audited)

---

## Step 5: Verify

### Via API
```bash
curl http://localhost:8000/api/v1/corpus-registry/corpora | jq
```

Returns a list of dicts:
```json
[
  {"corpus_id": "definer", "corpus_type": "conversation", "sensitive": false, ...},
  {"corpus_id": "codeforge", "corpus_type": "code", "sensitive": false, ...},
  {"corpus_id": "my_corpus", "corpus_type": "document", "sensitive": false, ...}
]
```

### Via GUI
Open the Ask page → expand "Corpus Selection" → your new corpus should
appear as a checkbox.

### Via search
After ingesting content, search the corpus:
```python
from aip.adapter.corpus_turn_store import CorpusTurnStore

turn_store = CorpusTurnStore("db/my_corpus.db")
await turn_store.initialize()
results = await turn_store.search("your query", limit=10)
```

---

## MAX_CORPORA Budget

The connection budget is enforced at `corpus_registry.py:867-890`:

| Constant | Value | Meaning |
|----------|-------|---------|
| `MAX_CONNECTIONS` | 64 | Hard cap on total SQLite connections |
| `KNOWN_NON_CORPUS_DB_FILES` | 7 | state.db, lexical.db, vectors.db, etc. |
| `NON_CORPUS_READ_POOL_SIZE` | 3 | Read pool per non-corpus DB |
| `CORPUS_READ_POOL_SIZE` | 2 | Read pool per corpus (shared across 6 stores) |
| `MAX_CORPORA` | 8 | Conservative cap (theoretical max is 12) |

**Budget arithmetic:**
```
non_corpus_budget = 7 × (1+3) = 28
available = 64 - 28 = 36
per_corpus = 1 + 2 = 3  (shared write + shared read pool)
theoretical_max = floor(36/3) = 12
shipped_cap = 8  (leaves 12 connections of headroom)
```

If you hit `ConnectionBudgetExceeded`, raise `MAX_CORPORA` in
`src/aip/foundation/corpus_constants.py` (verify the arithmetic first).

---

## Extension-Contributed Corpora

Extensions register corpora via their manifest (`extension.yaml`):

```yaml
manifest_version: 1
id: aristotle
name: ARISTOTLE
version: 0.1.0
contributes:
  corpora:
    - role: textbook
      type: document
      sensitive: false
  workflows_dir: workflows
  migrations: migrations
```

The ExtensionHost registers these at startup as `{ext_id}:{role}`
(e.g. `aristotle:textbook`). See ADR-014 for the full extension platform
contract.

---

## Migration Patterns

Each corpus type has a set of core migrations defined in
`MIGRATIONS_FOR_CORPUS_TYPE` (`foundation/corpus_types.py`):

| Type | Migrations |
|------|-----------|
| CONVERSATION | M001, M002 (definer only), M003, M004, M005 (definer only) |
| CODE | M001, M003, M004 |
| DOCUMENT | M001, M003, M004 |
| BOOK | M001, M003, M004 |

Migrations are fingerprinted (`sha256` of names in applied order). The
`CorpusMigrationRunner` raises on body change, reordering, or unknown names.
To add a new migration, see `adapter/corpus_store_factory.py` and
`adapter/corpus_migration_runner.py`.

---

## Common Pitfalls

1. **Forgetting to register at startup**: the corpus exists on disk but
   isn't in the registry, so retrieval can't see it. Always edit `app.py`.

2. **Hardcoding `max_corpora=4`**: use `MAX_CORPORA` constant, not a
   literal number. The constant was raised from 4 to 8 in QW10.

3. **Not calling `initialize()`**: if you open a `CorpusTurnStore` directly
   (e.g. in a CLI command), call `await turn_store.initialize()` first to
   ensure the schema exists. The registry does this automatically at startup.

4. **Sensitive flag + retrieval**: a sensitive corpus won't appear in
   retrieval results unless the session's `allowed_restricted_corpora`
   includes it. Use `PATCH /sessions/{id}` with
   `{"allowed_restricted_corpora": ["branham"]}` to opt in.

---

## Cross-References

- **ADR-008**: Multi-Corpus Architecture (the governing decision)
- **ADR-004**: Original multi-corpus design (superseded by ADR-008)
- **`PLANNED_FEATURES.md`**: Phase 1.6 Codebase-as-Corpus status
- **`src/aip/adapter/corpus_registry.py`**: CorpusRegistry implementation
- **`src/aip/foundation/corpus_types.py`**: CorpusType enum + migration map
- **`src/aip/foundation/corpus_constants.py`**: MAX_CORPORA + budget constants
- **`tests/acceptance/test_multi_corpus.py`**: AC-01 through AC-09
- **`tests/acceptance/test_codeforge_e2e.py`**: AC-10 (QW14 — codeforge E2E)
