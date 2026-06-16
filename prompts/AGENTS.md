# ============================================================

# Prompts — Agent Navigation
> Actor prompt templates. Changes here affect model behavior system-wide.

## Purpose
Prompt templates define the instructions given to LLM models for each actor
role. They are the primary tuning surface for model behavior — small changes
here can have outsized effects on output quality, safety, and coherence.

## Contracts (What This Module Promises to Consumers)

### Prompt Loading Contract
- Prompts are loaded by their owner actor at runtime
- Path references in `config/aip.config.toml` (e.g., `[chat].system_prompt_path`)
  must point to files in this directory or be absolute paths
- Prompt content is treated as Jinja2 templates where `{{ variable }}` syntax
  is supported for dynamic substitution

### Prompt Ownership Contract
| Prompt File | Owner Actor | Config Section |
|-------------|-------------|----------------|
| `synthesis.md` | Ask pipeline | `[chat].system_prompt_path` |
| `faithfulness.md` | Evaluation nodes | `[review].faithfulness_threshold` |
| `domain_coherence.md` | Evaluation nodes | `[review].coherence_threshold` |
| `adversarial_eval.md` | Evaluation nodes | — |

## Data Flows (In / Out)

### In
- Template variables from pipeline context (turns, sources, artifacts)
- Config references to prompt file paths

### Out
- Formatted prompts sent to LLM via `ModelSlotResolver`
- Evaluation criteria for review pipeline

## Known Gotchas
- **Prompt changes are behavior changes**: Even small wording tweaks can change
  model output significantly. Always test after prompt edits.
- **Template syntax errors are silent**: A malformed `{{ variable }}` may not
  raise an error — it may just render as empty string. Verify rendering.
- **Prompt length affects token budget**: Longer prompts consume more of the
  context window. Check budget after extending prompts.

## Last Cycle
- No changes. Prompts were stable during operator console debugging.

## Key Files
| File | Role |
|------|------|
| `synthesis.md` | System prompt for synthesis/chat pipeline |
| `faithfulness.md` | Faithfulness evaluation prompt |
| `domain_coherence.md` | Domain coherence evaluation prompt |
| `adversarial_eval.md` | Adversarial evaluation prompt |
| `README.md` | Prompt template documentation |

## Work Guidance
- Editing a prompt: make the smallest change, test with real queries, compare
  output quality before and after
- Adding a prompt: create the `.md` file, register the path in config,
  add the loading logic in the consuming actor/pipeline, update this AGENTS.md

## How to Test
```bash
# Prompts are tested indirectly through pipeline tests
uv run pytest tests/test_ask.py -k "synthesis"
uv run pytest tests/test_evaluation_pipeline.py
```


# ============================================================
