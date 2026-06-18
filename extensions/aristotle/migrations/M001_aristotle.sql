-- ADR-014 §6.3: migrations are relative to the extension directory.
-- ADR-014 §9: extension migrations use a SEPARATE `extension_applied_migrations`
-- table (keyed by ext_id + name) so the core CorpusMigrationRunner's fingerprint
-- check on `applied_migrations` is not contaminated.
--
-- This migration creates ARISTOTLE's Phase A tables in the `aristotle:textbook`
-- corpus (the extension's contributed corpus). Per ADR-014 §1, the progress
-- store is "tables in the definer corpus with aristotle_* naming" — but for
-- pre-alpha dogfood, applying to the extension's own corpus is simpler and
-- matches the migration_loader's behavior. Revisit at Phase B (teacher
-- dashboard) when cross-corpus aggregation matters.

-- aristotle_concept: concept-aware chunks per ADR-ARISTOTLE §4.
-- Standard RAG token-chunking is pedagogically wrong; ARISTOTLE chunks by
-- concept with a prerequisite DAG. The bilingual columns match ADR-014 §1:
-- content_primary + content_alt + content_alt_lang (ISO 639-1).
CREATE TABLE IF NOT EXISTS aristotle_concept (
    id TEXT PRIMARY KEY,
    textbook_chapter TEXT NOT NULL,
    topic TEXT NOT NULL,
    subtopic TEXT,
    bloom_target INTEGER NOT NULL DEFAULT 3,
    content_primary TEXT NOT NULL,
    content_alt TEXT,
    content_alt_lang TEXT,
    prerequisite_concept_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- aristotle_struggle_pattern: one persistent AI-written diagnostic sentence
-- per student (ADR-ARISTOTLE §2 MENTOR role). Feeds every REMEDIATE prompt.
-- Pre-alpha single-tenant: student_id defaults to 'definer'.
CREATE TABLE IF NOT EXISTS aristotle_struggle_pattern (
    student_id TEXT NOT NULL DEFAULT 'definer',
    pattern_text TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (student_id)
);
