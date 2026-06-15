-- AIP Seed Bootstrap SQL
-- Pre-populates a fresh db/state.db with graph nodes, edges, and default project.
-- Safe to run multiple times — all INSERTs use OR IGNORE.
--
-- Source: docs/entity_aliases.md (28 entities), 5 known domain bridge edges
-- Generated: 2026-06-06
-- DEFINER: B. Moses Jorgensen

-- ============================================================
-- 1. GRAPH NODES (28 entities from entity_aliases.md)
-- ============================================================

CREATE TABLE IF NOT EXISTS graph_nodes (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    domain TEXT,
    confidence REAL DEFAULT 1.0,
    source TEXT DEFAULT 'manual',
    aliases_json TEXT DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);

-- === PERSONS (5) ===

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('moses_jorgensen', 'PERSON', 'B. Moses Jorgensen', 'aip', 1.0, 'manual',
 '["Moses","Musa","Musa Messih","the user","the DEFINER","I","me","Moses Jorgensen"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('komal_jorgensen', 'PERSON', 'Komal Jorgensen', 'fg_education', 1.0, 'manual',
 '["Komal","the principal","my wife","Komal Messih"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('zaman', 'PERSON', 'Zaman', 'fg_ministry', 1.0, 'manual',
 '["brother Zaman","my friend Zaman"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('irfan', 'PERSON', 'Irfan', 'fg_ministry', 1.0, 'manual',
 '["brother Irfan"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('fulvio_balmelli', 'PERSON', 'Fulvio Balmelli', 'agri_tech', 1.0, 'manual',
 '["Balmelli","Fulvio","the Kyminasi contact"]',
 '{}', datetime('now'), datetime('now'));

-- === PROJECTS / SYSTEMS (11) ===

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('aip', 'PROJECT', 'AIP', 'aip', 1.0, 'manual',
 '["AI Poiesis","aip_brain","AIP Brain","AIP system","the system","my system","the knowledge engine","aip_methodology"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('aip_loom', 'PROJECT', 'AIP Loom', 'aip', 1.0, 'manual',
 '["loom","aip_loom","Loom"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('code_forge', 'PROJECT', 'CodeForge', 'aip', 1.0, 'manual',
 '["code_forge","Forge Suite coder","the coder"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('forge_suite', 'PROJECT', 'Forge Suite', 'aip', 1.0, 'manual',
 '["Forge","the forge suite","idea_forge + architect_forge + spec_forge + code_forge"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('beast', 'PROJECT', 'Beast', 'aip', 1.0, 'manual',
 '["Beast actor","the Beast","beast agent","the maintenance actor","beast orchestrator"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('sexton', 'PROJECT', 'Sexton', 'aip', 1.0, 'manual',
 '["Sexton orchestrator","sexton agent","the orchestrator"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('hydra', 'PROJECT', 'HYDRA', 'nbcm', 1.0, 'manual',
 '["HYDRA instrument","HYDRA detector","polarimetric detector"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('nbcm', 'PROJECT', 'NBCM', 'nbcm', 1.0, 'manual',
 '["Null-Boundary Constraint Manifold","null-boundary framework","the manifold framework","NBCM framework"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('gef', 'PROJECT', 'GEF', 'gef', 1.0, 'manual',
 '["Generational Energy Formations","GEF white paper","waste valorization project"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('freedom_generation_school', 'PROJECT', 'Freedom Generation School', 'fg_education', 1.0, 'manual',
 '["FG School","Freedom Gen","FGS","the school","Freedom Generation"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('fgwes', 'PROJECT', 'Freedom Generation Welfare and Education Society', 'fg_education', 1.0, 'manual',
 '["FGWES","the society","the NGO"]',
 '{}', datetime('now'), datetime('now'));

-- === PROJECTS / SYSTEMS continued (3) ===

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('f_airgo', 'PROJECT', 'F-AirGo', 'agri_tech', 1.0, 'manual',
 '["F-AirGo frost system","the frost protection system"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('kyminasi', 'PROJECT', 'Kyminasi', 'agri_tech', 1.0, 'manual',
 '["Kyminasi plant booster","Harvest Harmonics","the Kyminasi system"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('jorgensen_service_company', 'PROJECT', 'Jorgensen Service Company', 'aip', 1.0, 'manual',
 '["JSC","Jorgensen Service"]',
 '{}', datetime('now'), datetime('now'));

-- === CONCEPTS (14) ===

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('definer', 'CONCEPT', 'DEFINER', 'aip', 1.0, 'manual',
 '["the DEFINER","definer gate","DEFINER gate","human-in-the-loop authority","the sovereign"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('ecs_lifecycle', 'CONCEPT', 'ECS lifecycle', 'aip', 1.0, 'manual',
 '["ECS","artifact lifecycle","GENERATED/APPROVED lifecycle","state machine"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('ai_poiesis_methodology', 'CONCEPT', 'AI Poiesis methodology', 'aip', 1.0, 'manual',
 '["AI Poiesis","the methodology","poiesis methodology","expert-directed synthesis"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('openrouter', 'CONCEPT', 'OpenRouter', 'aip', 1.0, 'manual',
 '["open router","the universal endpoint","model router"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('chromadb', 'CONCEPT', 'ChromaDB', 'aip', 1.0, 'manual',
 '["chroma","vector db","vector store","vectordb"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('fts5', 'CONCEPT', 'FTS5', 'aip', 1.0, 'manual',
 '["full-text search","SQLite FTS5","fts","keyword search"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('hybrid_retrieval', 'CONCEPT', 'Hybrid retrieval', 'aip', 1.0, 'manual',
 '["RRF","reciprocal rank fusion","hybrid search","FTS5+vector"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('ez_water', 'CONCEPT', 'EZ water', 'nbcm', 1.0, 'manual',
 '["exclusion zone water","structured water","fourth phase water","Pollack water"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('nbcm_framework', 'CONCEPT', 'NBCM framework', 'nbcm', 1.0, 'manual',
 '["null-boundary constraint manifold","boundary-first physics","null geodesic geometry"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('new_covenant', 'CONCEPT', 'New Covenant', 'theology', 1.0, 'manual',
 '["the new covenant","new covenant theology","covenant of the Spirit"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('definer_profile', 'CONCEPT', 'DEFINER profile', 'aip', 1.0, 'manual',
 '["definer profile","user profile","identity profile","Moses profile"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('seed_corpus', 'CONCEPT', 'Seed corpus', 'aip', 1.0, 'manual',
 '["the seed corpus","AIP seed","foundation corpus","self-knowledge corpus"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('context_advisory', 'CONCEPT', 'Context advisory', 'aip', 1.0, 'manual',
 '["context_advisory","augmented context","beast context advisory","ContextAdvisory"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('domain_registry', 'CONCEPT', 'Domain registry', 'aip', 1.0, 'manual',
 '["beast domain registry","domain taxonomy","the registry"]',
 '{}', datetime('now'), datetime('now'));

-- === MANUSCRIPTS / DOCUMENTS (6) ===

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('architecture_of_mercy', 'MANUSCRIPT', 'Architecture of Mercy', 'theology', 1.0, 'manual',
 '["AoM","the soteriology series"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('covenant_man_fractured_week', 'MANUSCRIPT', 'Covenant Man and the Fractured Week', 'theology', 1.0, 'manual',
 '["covenant man paper","fractured week thesis","Adam as covenant man"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('new_covenant_displaced', 'MANUSCRIPT', 'The New Covenant Displaced', 'theology', 1.0, 'manual',
 '["NCD paper","tithing critique","institutional Christianity paper"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('sparkle_thirst', 'MANUSCRIPT', 'The Sparkle Thirst', 'fiction', 1.0, 'manual',
 '["Sparkle Thirst","the novel","the science fiction novel"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('set_oppressed_free', 'MANUSCRIPT', 'Set the Oppressed Free', 'policy', 1.0, 'manual',
 '["brick kiln paper","bonded labor paper","PKR 896 billion proposal"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('gef_white_paper', 'MANUSCRIPT', 'GEF White Paper', 'gef', 1.0, 'manual',
 '["GEF paper","waste valorization paper","the GEF manuscript"]',
 '{}', datetime('now'), datetime('now'));

-- === PLACES / ORGANIZATIONS (3) ===

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('faisalabad', 'PLACE', 'Faisalabad', 'fg_ministry', 1.0, 'manual',
 '["Faisalabad Pakistan","FSB","the city"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('pnnl', 'PLACE', 'Pacific Northwest National Laboratory', 'chemistry', 1.0, 'manual',
 '["PNNL","Battelle","the laboratory"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('anthropic', 'ORGANIZATION', 'Anthropic', 'aip', 1.0, 'manual',
 '["Anthropic AI","Claude''s maker"]',
 '{}', datetime('now'), datetime('now'));

-- === AI TOOLS / MODELS (7) ===

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('github', 'TOOL', 'GitHub', 'aip', 1.0, 'manual',
 '["github.com","the repo","the repository"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('aip_brain_repo', 'TOOL', 'AIP_Brain repo', 'aip', 1.0, 'manual',
 '["AIP_Brain","freedomgeneration1111-sudo/AIP_Brain","the codebase"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('claude', 'TOOL', 'Claude', 'aip', 1.0, 'manual',
 '["Claude AI","Anthropic''s Claude","Claude Sonnet","Claude Opus"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('deepseek', 'TOOL', 'DeepSeek', 'aip', 1.0, 'manual',
 '["DeepSeek AI","deepseek","DS"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('gpt4o', 'TOOL', 'GPT-4o', 'aip', 1.0, 'manual',
 '["ChatGPT","GPT","GPT4","OpenAI model"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('grok', 'TOOL', 'Grok', 'aip', 1.0, 'manual',
 '["xAI Grok","Grok 3","the Grok agent"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('gemini', 'TOOL', 'Gemini', 'aip', 1.0, 'manual',
 '["Google Gemini","Gemini Pro"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('glm', 'TOOL', 'GLM', 'aip', 1.0, 'manual',
 '["ChatGLM","GLM-4","Zhipu AI"]',
 '{}', datetime('now'), datetime('now'));

INSERT OR IGNORE INTO graph_nodes
(id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
VALUES
('ollama', 'TOOL', 'Ollama', 'aip', 1.0, 'manual',
 '["Ollama local","local inference","the local runner"]',
 '{}', datetime('now'), datetime('now'));

-- ============================================================
-- 2. GRAPH EDGES (5 domain bridge connections)
-- ============================================================

CREATE TABLE IF NOT EXISTS graph_edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    bridge_tag TEXT,
    confidence REAL DEFAULT 1.0,
    evidence_turn_ids_json TEXT DEFAULT '[]',
    weight REAL DEFAULT 1.0,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_domain ON graph_nodes(domain);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes(entity_type);

-- Edge 1: Moses → AIP (author/creator)
INSERT OR IGNORE INTO graph_edges
(id, source_id, target_id, relationship_type, bridge_tag, confidence, evidence_turn_ids_json, weight, created_at)
VALUES
('moses_jorgensen__AUTHORED__aip', 'moses_jorgensen', 'aip', 'AUTHORED', 'person→project:author', 1.0,
 '[]', 1.0, datetime('now'));

-- Edge 2: Moses → DEFINER (identity)
INSERT OR IGNORE INTO graph_edges
(id, source_id, target_id, relationship_type, bridge_tag, confidence, evidence_turn_ids_json, weight, created_at)
VALUES
('moses_jorgensen__RELATES_TO__definer', 'moses_jorgensen', 'definer', 'RELATES_TO', 'person→concept:identity', 1.0,
 '[]', 1.0, datetime('now'));

-- Edge 3: Komal → Freedom Generation School (works on)
INSERT OR IGNORE INTO graph_edges
(id, source_id, target_id, relationship_type, bridge_tag, confidence, evidence_turn_ids_json, weight, created_at)
VALUES
('komal_jorgensen__WORKS_ON__freedom_generation_school', 'komal_jorgensen', 'freedom_generation_school', 'WORKS_ON', 'person→project:principal', 1.0,
 '[]', 1.0, datetime('now'));

-- Edge 4: Fulvio → Kyminasi (contact/researcher)
INSERT OR IGNORE INTO graph_edges
(id, source_id, target_id, relationship_type, bridge_tag, confidence, evidence_turn_ids_json, weight, created_at)
VALUES
('fulvio_balmelli__CONNECTS__kyminasi', 'fulvio_balmelli', 'kyminasi', 'CONNECTS', 'person→project:researcher', 1.0,
 '[]', 1.0, datetime('now'));

-- Edge 5: AIP → Beast (contains/subsystem)
INSERT OR IGNORE INTO graph_edges
(id, source_id, target_id, relationship_type, bridge_tag, confidence, evidence_turn_ids_json, weight, created_at)
VALUES
('aip__CONTAINS__beast', 'aip', 'beast', 'CONNECTS', 'project→project:subsystem', 1.0,
 '[]', 1.0, datetime('now'));

-- ============================================================
-- 3. DEFAULT PROJECT
-- ============================================================

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    domain TEXT,
    created_at TEXT,
    updated_at TEXT
);

INSERT OR IGNORE INTO projects (project_id, name, status, domain, created_at, updated_at)
VALUES ('proj-seed-default', 'default', 'active', 'aip', datetime('now'), datetime('now'));

-- ============================================================
-- 4. EXTRACTION LOG TABLE (for Beast graph extraction tracking)
-- ============================================================

CREATE TABLE IF NOT EXISTS graph_extraction_log (
    turn_id TEXT PRIMARY KEY,
    extracted_at TEXT NOT NULL,
    entities_found INTEGER DEFAULT 0,
    relationships_found INTEGER DEFAULT 0
);
