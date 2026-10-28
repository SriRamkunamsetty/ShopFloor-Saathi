# Responsible AI and SAIF mapping

Google's Secure AI Framework (SAIF) 2.0 asks agents to have well-defined human controllers, carefully
limited powers, and observable actions and planning. This is how ShopFloor Saathi implements that, and
where the gaps are.

| Principle | Implementation | Gap |
|---|---|---|
| Human controller | Every work order is `pending_approval`. Approve, reject and complete are separate API calls that require the `supervisor` role (`saathi/api.py`). Agents have no such tool (`tests/test_agents.py::test_agents_have_no_approval_capability`). | Role comes from a Firebase custom claim; claim management is the deployer's job. |
| Limited powers | Tools are read-only except `draft_work_order`, which can only create a pending record. Nothing controls a machine. Service account has only Vertex AI, Firestore and logging roles. | Terraform not yet applied or reviewed in a real project. |
| Observable planning | Every diagnosis returns an ordered `trace`; findings, thresholds and citations are shown in the UI; logs carry request ids. | Agent-level traces are not yet exported to Cloud Trace. |
| Prompt injection | Input patterns blocked before the model; instructions state that photos, manuals, logs and tool output are data; the model cannot reach privileged actions even if fooled. | Pattern matching is a weak layer. Add Model Armor on the Gemini calls in production. |
| Input safety | Length limits, control-character stripping, image type, magic-byte and size checks, strict machine-id format at API and tool layers. | |
| Grounding and honesty | Hypotheses carry citations; no telemetry evidence means no work order; unknown or nonsense input leads to "I don't know". Confidence is labelled a heuristic. | Citations come from a small demo corpus. |
| Safety | Every repair advice includes lockout/tagout, and the system prompt tells the model to stop and escalate on smoke, fire, shock or injury. | Not a substitute for site safety procedures. |
| Privacy | The app's own request logs omit photos and message bodies (check exception traces from model errors in your environment). The app records no worker performance metrics. | Review India's DPDP Act obligations before processing worker data. |
| Transparency of numbers | Impact is deterministic Python with all assumptions returned and shown. Labelled "projection, not audited". | Defaults are assumptions, not plant data. |

## Test and red-team checklist before production

1. Run `adk eval` against real Gemini and review failures.
2. Try prompt injection in photos (text on a sign), in manual PDFs, and in log notes.
3. Confirm a supervisor-only action fails with an operator token in the deployed service.
4. Review Cloud Logging to confirm no photo or free-text content is stored.
5. Replace assumptions in `saathi/domain.py` and the env vars with the plant's real figures.
