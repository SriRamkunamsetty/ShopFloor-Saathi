"""ADK wiring tests, including the Gemini code path driven by a scripted fake model."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.agents import Agent
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types

import saathi.agent_tools as agent_tools
from saathi.agents import SHIFT_LEAD_INSTRUCTION, _guard_machine_ids, build_root_agent
from saathi.auth import Principal
from saathi.chat import ChatService
from saathi.config import Settings
from saathi.models import ChatRequest


@pytest.fixture(autouse=True)
def use_test_services(monkeypatch, services):
    monkeypatch.setattr(agent_tools, "_svc", lambda: services)


def ctx(user="op-1"):
    return SimpleNamespace(user_id=user)


def test_agent_graph_structure():
    root = build_root_agent(Settings(fast_model="fast-x", pro_model="pro-x"))
    assert root.name == "shift_lead" and root.model == "pro-x"
    names = {getattr(t, "name", "") for t in root.tools}
    assert {
        "telemetry_analyst",
        "knowledge_specialist",
        "action_planner",
        "list_machines_status",
    } <= names
    specialists = [t.agent for t in root.tools if hasattr(t, "agent")]
    assert {a.model for a in specialists} == {
        "fast-x"
    }  # cheap model for specialists, pro for the lead


def test_agents_have_no_approval_capability():
    root = build_root_agent()
    everything = []
    for tool in root.tools:
        everything.append(getattr(tool, "name", ""))
        if hasattr(tool, "agent"):
            everything += [getattr(t, "name", getattr(t, "__name__", "")) for t in tool.agent.tools]
    assert not any("approve" in n or "decide" in n or "complete" in n for n in everything)
    assert "cannot approve" in SHIFT_LEAD_INSTRUCTION.lower()


def test_tool_schemas_expose_inputs_but_hide_tool_context():
    schema = FunctionTool(agent_tools.draft_work_order)._get_declaration().parameters_json_schema
    assert set(schema["properties"]) == {"machine_id", "symptom"}
    assert set(schema["required"]) == {"machine_id", "symptom"}


def test_tools_return_errors_instead_of_raising():
    assert "error" in agent_tools.get_telemetry_findings("bad")
    assert "error" in agent_tools.get_telemetry_findings("M-99")
    assert "error" in agent_tools.calculate_impact("M-07", "XXX")
    assert "error" in agent_tools.search_manuals("x" * 600)
    assert agent_tools.search_manuals("zzqx wvvk plorb") == {"results": []}
    assert agent_tools.search_manuals("machine garam ho raha hai")["results"][0][
        "heading"
    ].startswith("Heat")


def test_guard_callback_blocks_malformed_machine_ids():
    assert _guard_machine_ids(None, {"machine_id": "M-7; DROP"}, None)["error"]
    assert _guard_machine_ids(None, {"machine_id": "M-07"}, None) is None
    assert _guard_machine_ids(None, {"query": "fan"}, None) is None


def test_draft_work_order_tool_only_drafts(services):
    out = agent_tools.draft_work_order("M-07", "garam", ctx())
    assert out["created"] and out["status"] == "pending_approval"
    assert services.work_orders.get(out["work_order_id"]).created_by == "op-1"
    assert agent_tools.draft_work_order("M-07", "garam", ctx())["already_open"]
    none = agent_tools.draft_work_order("M-01", "", ctx())
    assert none["created"] is False and none["reason"]


# ---- Gemini path exercised end to end with a scripted model (no network) ----


class ScriptedLlm(BaseLlm):
    """Calls draft_work_order once, then answers with text built from the tool result."""

    model: str = "scripted"

    async def generate_content_async(self, llm_request, stream=False):
        last = llm_request.contents[-1]
        response_part = next((p for p in last.parts if p.function_response), None)
        if response_part is None:
            call = types.Part.from_function_call(
                name="draft_work_order", args={"machine_id": "M-07", "symptom": "garam"}
            )
            yield LlmResponse(content=types.Content(role="model", parts=[call]))
        else:
            wid = response_part.function_response.response["work_order_id"]
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=f"Drafted {wid}")])
            )


@pytest.mark.asyncio
async def test_gemini_path_runs_tools_and_returns_final_text(services, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    settings = Settings(llm_enabled=True, rate_limit_per_minute=0)
    services.settings = settings
    chat = ChatService(services)
    chat._runner = Runner(
        app_name="saathi",
        agent=Agent(
            name="shift_lead",
            model=ScriptedLlm(),
            instruction="test",
            tools=[FunctionTool(agent_tools.draft_work_order)],
        ),
        session_service=InMemorySessionService(),
        auto_create_session=True,
    )
    resp = await chat.respond(
        Principal("op-9", "operator"), ChatRequest(message="M-07 garam ho raha hai")
    )
    assert resp.mode == "gemini" and resp.reply.startswith("Drafted WO-")
    order = services.work_orders.list()[0]
    assert order.status.value == "pending_approval" and order.created_by == "op-9"


@pytest.mark.asyncio
async def test_gemini_failure_falls_back_to_deterministic_answer(services, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    services.settings = Settings(llm_enabled=True, rate_limit_per_minute=0)
    chat = ChatService(services)

    class Boom:
        async def run_async(self, **_):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover

    chat._runner = Boom()
    resp = await chat.respond(
        Principal("op-1", "operator"), ChatRequest(message="M-03 tool wear high")
    )
    assert resp.mode == "offline-fallback" and "Tool wear failure" in resp.reply and resp.notice
