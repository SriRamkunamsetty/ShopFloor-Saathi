# ShopFloor Saathi

A voice-first, multilingual, multi-agent copilot for small factories with older machines.
An operator says or types what is wrong ("M-07 garam ho raha hai"), optionally with a photo.
Saathi checks the machine's data, finds the matching manual section and past repairs, ranks the likely
causes **with citations**, drafts a work order, checks spare parts, and shows the projected rupee, energy
and CO₂ impact. **A supervisor must approve every work order. Agents can never approve, start or close work.**

Built for the Google "Manufacturing: Intelligent Operations & Industrial Efficiency" challenge with
Gemini, the Agent Development Kit (ADK), and Cloud Run.

## Tech Stack

| Layer | Technologies & Tools | Purpose |
|---|---|---|
| **AI & Agent Orchestration** | **Google Agent Development Kit (ADK)**, **Google GenAI SDK** | Multi-agent orchestration, dynamic tool-calling graph, session management |
| **Foundation Models** | **Gemini 2.5 Pro** (`shift_lead`), **Gemini 2.5 Flash** (Specialists) | Multimodal visual inspection (photos/HMI gauges), vernacular reasoning, high-speed specialist delegation |
| **Backend Framework** | **FastAPI**, **Uvicorn**, **Pydantic v2** | Async REST API, strict request/response data contracts, rate limiting, security middleware |
| **Data & Diagnostics** | **NumPy**, **Pandas** | Time-series telemetry analysis, AI4I 2020 predictive maintenance rules, baseline drift detection |
| **Retrieval & NLP** | **TF-IDF & Cosine Similarity**, Vernacular Domain Lexicon | Grounded manual & maintenance log retrieval with Hinglish & Devanagari technical query expansion |
| **Frontend / PWA** | **HTML5, CSS3, Modern ES6+ JavaScript** | Dependency-free, buildless Progressive Web App; zero-`innerHTML` XSS immunity; Web Speech API (EN, HI, TE, TA); camera capture |
| **Persistence & State** | **Google Cloud Firestore**, Local JSON / In-Memory | Work-order lifecycle persistence with concurrency locking and machine/mode idempotency |
| **Security & Auth** | **Firebase Authentication**, Custom Claims RBAC | Cryptographic JWT verification separating `operator` actions from `supervisor` approval authority |
| **Cloud & Deployment** | **Google Cloud Run**, **Artifact Registry**, **Cloud Build** | Serverless private microservice, unprivileged Docker containers, automated canary deployments with smoke testing |
| **Infrastructure as Code** | **Terraform** | Declarative least-privilege IAM, service accounts, Firestore database, and registry provisioning |
| **Testing & Code Quality** | **Pytest**, **pytest-asyncio**, **Ruff**, **GitHub Actions** | 84 unit & golden scenario tests, strict linting/formatting, Python matrix CI (3.11, 3.12, 3.13) |

## What is verified and what is not

| Area | Status |
|---|---|
| Telemetry rules, retrieval (English, Hinglish, Devanagari), impact maths, work-order lifecycle | Unit tested (84 tests), lint clean |
| Deterministic diagnosis pipeline, 7 golden scenarios | Tested in CI, no LLM needed |
| REST API: roles, validation, rate limit, security headers, body limit | Tested |
| Gemini path through ADK (tool calls, session, final answer, fallback) | Tested with a **scripted fake model**. **Not run against real Gemini** (no credentials in the build environment) |
| `eval/saathi.evalset.json` | Schema-validated against ADK. **Not run** (needs Gemini) |
| Firestore repository | Tested against a fake client. **Not run against Firestore/emulator** |
| Firebase token verification | Written, **not tested** |
| Dockerfile, `cloudbuild.yaml`, Terraform | Package install verified in a clean venv. **Image not built, Terraform not validated** (no Docker daemon or Terraform here) |
| Browser UI | JavaScript syntax-checked and served by the API. **Not exercised in a real browser** |

Telemetry is **synthetic** (generated with AI4I-2020-style operating points and injected faults). The
manuals and maintenance logs are original demo text. Replace them with plant data before real use.

## Quick start (local, no cloud account)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # optional
pytest                          # 84 tests
uvicorn saathi.main:app --reload
# open http://localhost:8000   (use the Role switcher to act as supervisor)
```

Without Gemini credentials the app runs in **offline rules mode**: the same evidence pipeline answers
chat messages with a deterministic summary. Photos need Gemini mode.

### Enable Gemini

```bash
export SAATHI_LLM_ENABLED=true
export GOOGLE_API_KEY=...                 # local dev, or on Google Cloud:
# export GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=... GOOGLE_CLOUD_LOCATION=...
export SAATHI_FAST_MODEL=gemini-2.5-flash   # verify current model IDs; they change quickly
export SAATHI_PRO_MODEL=gemini-2.5-pro
adk web adk_apps                            # ADK dev UI for the agent graph
adk eval adk_apps/saathi eval/saathi.evalset.json --config_file_path eval/test_config.json
```

If a Gemini call fails or times out (60 s), chat falls back to the rule-based answer and says so.

## How it works

```
Phone PWA (web/)  --photo, voice transcript, text-->  FastAPI (saathi/api.py) on Cloud Run
                                                        |
          Gemini path (ADK)                             |        Deterministic path
   shift_lead (Pro model, sees photos)                  |   DiagnosisEngine (saathi/engine.py)
    |- telemetry_analyst   -> rule findings             |    telemetry rules -> retrieval ->
    |- knowledge_specialist-> cited manuals + incidents |    ranked hypotheses -> work-order draft
    '- action_planner      -> parts, impact, DRAFT order|    -> impact (Python, not LLM)
                         \__ same tools (saathi/agent_tools.py) __/
   Work orders: Firestore (or JSON/in-memory locally). Supervisor approval = separate role-checked API call.
```

Key design choices:

- **The model never does the maths and never writes the work order.** `draft_work_order` takes only a
  machine id and the operator's words; steps, parts and impact come from the evidence pipeline.
- **No evidence, no work order.** A complaint on a healthy machine gives manual-based advice only, and
  nonsense input makes Saathi say it does not know.
- **No approval tool exists** for agents. Approve/reject/complete require the `supervisor` role.
- **Confidence is a documented heuristic**, not a calibrated probability.
- **Impact is a projection** under visible assumptions (downtime cost, tariff, grid factor, repair and
  failure durations in `saathi/domain.py`). Replace them with the plant's own numbers.

## API

| Method | Path | Role |
|---|---|---|
| GET | `/healthz`, `/readyz` | none |
| GET | `/api/me`, `/api/machines`, `/api/machines/{id}`, `/api/assumptions` | any |
| POST | `/api/diagnose` `{machine_id, symptom}` | any (drafts a pending work order) |
| POST | `/api/chat` `{message, session_id?, image_base64?, image_mime?}` | any |
| GET | `/api/workorders?status=`, `/api/impact/summary` | any |
| POST | `/api/workorders/{id}/decision` `{decision: approve\|reject, note}` | supervisor |
| POST | `/api/workorders/{id}/complete` `{actual_downtime_min}` | supervisor |

Interactive docs at `/docs` outside production.

## Deploy to Cloud Run

1. `terraform -chdir=infra/terraform init && terraform apply -var project_id=...` (review the plan first;
   it creates Artifact Registry, Firestore, the least-privilege runtime service account and IAM).
2. Enable Firebase Authentication and give supervisors a custom claim `role=supervisor`.
3. `gcloud builds submit --config cloudbuild.yaml`. It runs lint and tests, builds the image, deploys a
   no-traffic revision, smoke tests it, then shifts traffic. The service is private by default
   (`--no-allow-unauthenticated`); grant `roles/run.invoker` deliberately.
4. The container starts with `SAATHI_ENV=production`, Firebase auth and Firestore. The app **refuses to start**
   in production with header-based dev auth.

## Google standards mapping

- **Well-Architected / security:** dedicated service account with least privilege, no keys, private service,
  CSP and security headers, input and image validation, rate limiting, production disables API docs.
- **Reliability:** idempotent work-order creation, timeouts and graceful fallback from Gemini, atomic
  file writes, health and readiness endpoints, revisions with no-traffic canary and smoke test.
- **Observability:** structured JSON logs (Cloud Logging format) with request ids; the app's own logs omit
  request bodies and photos.
- **Cost:** Flash model for specialists, Pro only for the lead, short outputs, bounded images.
- **SAIF / Responsible AI:** see `docs/RESPONSIBLE_AI.md`.
- **Code style:** `ruff` (pycodestyle, pyflakes, bugbear, bandit, isort, pyupgrade) with `ruff format`; type hints throughout; docstrings on every agent tool because ADK turns them into the tool schema.

## Known limitations and next steps

- Replace synthetic data with a real telemetry source (BigQuery table with the same columns, fed by
  Pub/Sub or Manufacturing Data Engine) and real manuals indexed in Vertex AI RAG Engine. The
  `KnowledgeBase.search` contract is the swap point.
- Use a database-backed ADK session service for multi-instance Cloud Run (currently in-memory per instance).
- Live voice uses the browser's Web Speech API (language support varies); Gemini Live API is not integrated.
- The rate limiter is per instance; put Cloud Armor in front for global limits.
- No monthly carbon ledger or CBAM export yet. Only per-action CO₂ estimates exist.
- Run the ADK evalset in CI with Workload Identity Federation (a commented job is in `.github/workflows/ci.yml`).
