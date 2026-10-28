# Architecture notes

## Components

| Layer | Module | Notes |
|---|---|---|
| Config | `saathi/config.py` | Env-driven, validated; refuses dev auth in production |
| Data | `saathi/data/` | Synthetic telemetry generator (one RNG stream per machine), demo manuals, logs, spare parts |
| Tools | `saathi/tools/` | Telemetry rules, TF-IDF retrieval with Hinglish/Devanagari expansion, impact maths, spares, work orders (memory / JSON / Firestore) |
| Evidence pipeline | `saathi/engine.py` | Deterministic diagnosis, golden-tested |
| Agents | `saathi/agents.py`, `saathi/agent_tools.py` | ADK agent graph (lead + 3 specialists as `AgentTool`s); tools never raise |
| Chat | `saathi/chat.py` | Gemini path via ADK Runner, 60 s timeout, falls back to the engine |
| API | `saathi/api.py`, `saathi/auth.py` | FastAPI, role checks, rate limit, CSP, structured logs |
| UI | `web/` | Dependency-free PWA, all dynamic text via `textContent` |

## Why a deterministic pipeline next to the LLM agents

The pipeline is the source of truth for evidence, steps, parts and impact. It makes the system testable
in CI without credentials, gives a safe fallback when the model is down, and keeps the LLM away from
arithmetic and from inventing maintenance actions. The LLM adds what rules cannot: reading photos, handling
free-form multilingual speech, and explaining results in the operator's language.

## Production swap points

| Today | Production |
|---|---|
| Generated telemetry DataFrame | BigQuery table with the same columns, fed from PLC/historian (Manufacturing Data Engine, Pub/Sub). Implement `TelemetryStore` over BigQuery or load recent windows from it. |
| TF-IDF over demo manuals | Vertex AI RAG Engine / Vertex AI Search over the plant's PDFs and logs, keeping `KnowledgeBase.search` -> `Hit` -> `Citation`. |
| In-memory ADK sessions | ADK database-backed session service (needed once Cloud Run runs more than one instance). |
| JSON or Firestore work orders | Firestore (provided) with Firestore security rules if clients ever read it directly. |
| Pattern-based injection check | Add Model Armor / AI Protection on model calls. |

## Reasoning behind the confidence score

`0.55` (warning) or `0.70` (critical) base, plus up to `0.15` for how long the rule has been violated, plus
`0.05` if the operator's words match the failure mode, plus `0.05` if the manual backs it, capped at `0.95`.
It is a ranking aid, not a probability. Manual-only hypotheses (no telemetry) are capped at `0.40` and never
create a work order.
