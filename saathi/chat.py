"""Conversational entry point: Gemini agents when configured, deterministic engine otherwise."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Any

from pydantic import BaseModel

from saathi.auth import Principal
from saathi.guardrails import (
    GuardrailError,
    decode_image,
    looks_like_injection,
    sanitize_text,
)
from saathi.models import ChatRequest, DiagnosisReport
from saathi.services import Services

log = logging.getLogger(__name__)
APP_NAME = "saathi"
LLM_TIMEOUT_S = 60

_MACHINE_RE = re.compile(r"(?:\bM[-\s]?|machine\s+|mc\s+|मशीन\s+)0*(\d{1,2})\b", re.IGNORECASE)

BLOCKED_REPLY = (
    "I can only help with machine troubleshooting, work orders and plant efficiency. "
    "I can't follow instructions that try to change my rules."
)


class ChatResponse(BaseModel):
    reply: str
    mode: str  # gemini | offline | offline-fallback | blocked
    session_id: str
    work_order_id: str | None = None
    notice: str | None = None


def extract_machine_id(text: str) -> str | None:
    match = _MACHINE_RE.search(text)
    return f"M-{int(match.group(1)):02d}" if match else None


def llm_configured(services: Services) -> bool:
    if not services.settings.llm_enabled:
        return False
    return bool(
        os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"1", "true"}
    )


def render_report(report: DiagnosisReport, work_order_id: str | None) -> str:
    """Plain-text summary of a deterministic diagnosis (used when Gemini is unavailable)."""
    if report.abstained:
        return f"{report.machine_id}: {report.abstain_reason}"
    top = report.hypotheses[0]
    lines = [
        f"{report.machine_id} ({report.machine_name}): {top.label}, confidence {top.confidence:.0%}."
    ]
    lines += [f"- {r}" for r in top.reasoning[:2]]
    if report.recommended:
        rec = report.recommended
        lines.append(f"Priority {rec.priority}. Next steps (apply lockout/tagout first):")
        lines += [f"{i}. {s}" for i, s in enumerate(rec.steps[:5], start=1)]
        missing = [p.part for p in rec.parts if not p.in_stock]
        lines.append(
            "Parts: " + ("missing " + ", ".join(missing) if missing else "all in stock") + "."
        )
        lines.append(
            f"Projected impact: INR {rec.impact.total_inr:,.0f} and {rec.impact.co2_kg_avoided} kg CO2 "
            "(estimate under stated assumptions)."
        )
        if work_order_id:
            lines.append(f"Work order {work_order_id} drafted; a supervisor must approve it.")
    elif report.advisory_note:
        lines.append(report.advisory_note)
    if top.citations:
        c = top.citations[0]
        lines.append(f"Source: {c.source} > {c.heading}")
    return "\n".join(lines)


class ChatService:
    def __init__(self, services: Services):
        self._svc = services
        self._runner: Any = None
        self._runner_lock = asyncio.Lock()

    async def respond(self, principal: Principal, req: ChatRequest) -> ChatResponse:
        session_id = req.session_id or uuid.uuid4().hex[:16]
        message = sanitize_text(req.message)  # GuardrailError (a ValueError) -> HTTP 422
        image = None
        if req.image_base64:
            if not req.image_mime:
                raise GuardrailError("image_mime is required with an image")
            image = decode_image(
                req.image_base64, req.image_mime, self._svc.settings.max_image_bytes
            )

        if looks_like_injection(message):
            log.warning("prompt injection pattern blocked", extra={"user": principal.user_id})
            return ChatResponse(reply=BLOCKED_REPLY, mode="blocked", session_id=session_id)

        if llm_configured(self._svc):
            try:
                reply = await asyncio.wait_for(
                    self._run_gemini(principal, session_id, message, image, req.image_mime),
                    timeout=LLM_TIMEOUT_S,
                )
                return ChatResponse(reply=reply, mode="gemini", session_id=session_id)
            except Exception:  # noqa: BLE001 - degrade gracefully instead of failing the operator
                log.exception("gemini path failed, using deterministic fallback")
                resp = self._offline(principal, session_id, message, image is not None)
                resp.mode = "offline-fallback"
                resp.notice = "The AI assistant is unavailable; showing the rule-based answer."
                return resp
        return self._offline(principal, session_id, message, image is not None)

    # ---- deterministic path ----
    def _offline(
        self, principal: Principal, session_id: str, message: str, had_image: bool
    ) -> ChatResponse:
        notice = None
        if had_image:
            notice = (
                "Photo reading needs the Gemini mode. Please type what the display or gauge shows."
            )
        machine_id = extract_machine_id(message)
        if machine_id is None:
            attention = [s for s in self._svc.telemetry.fleet_status() if s.status != "healthy"]
            if attention:
                names = ", ".join(f"{s.machine_id} ({s.status})" for s in attention)
                reply = f"Which machine? These need attention right now: {names}. Tell me the machine id, e.g. M-07."
            else:
                reply = (
                    "Which machine? Tell me the machine id, e.g. M-07. All machines look healthy."
                )
            return ChatResponse(reply=reply, mode="offline", session_id=session_id, notice=notice)
        try:
            report = self._svc.engine.diagnose(machine_id, message)
        except (KeyError, ValueError) as exc:
            return ChatResponse(
                reply=str(exc.args[0]), mode="offline", session_id=session_id, notice=notice
            )
        work_order_id = None
        if report.recommended:
            order, _ = self._svc.work_orders.create_draft(report.recommended, principal.user_id)
            work_order_id = order.id
        return ChatResponse(
            reply=render_report(report, work_order_id),
            mode="offline",
            session_id=session_id,
            work_order_id=work_order_id,
            notice=notice,
        )

    # ---- Gemini / ADK path ----
    async def _get_runner(self) -> Any:
        async with self._runner_lock:
            if self._runner is None:
                from google.adk.runners import Runner
                from google.adk.sessions import InMemorySessionService

                from saathi.agents import build_root_agent

                # InMemorySessionService is per-instance. For multi-instance Cloud Run use a
                # database-backed session service (see docs/ARCHITECTURE.md).
                self._runner = Runner(
                    app_name=APP_NAME,
                    agent=build_root_agent(self._svc.settings),
                    session_service=InMemorySessionService(),
                    auto_create_session=True,
                )
            return self._runner

    async def _run_gemini(
        self,
        principal: Principal,
        session_id: str,
        message: str,
        image: bytes | None,
        mime: str | None,
    ) -> str:
        from google.genai import types

        runner = await self._get_runner()
        parts = [types.Part(text=message)]
        if image is not None and mime:
            parts.append(types.Part.from_bytes(data=image, mime_type=mime))
        content = types.Content(role="user", parts=parts)
        reply = ""
        async for event in runner.run_async(
            user_id=principal.user_id, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                reply = "".join(
                    p.text or "" for p in event.content.parts if getattr(p, "text", None)
                )
        if not reply.strip():
            raise RuntimeError("Gemini returned an empty reply")
        return reply.strip()
