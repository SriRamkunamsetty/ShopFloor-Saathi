"""FastAPI application: REST API, security middleware and the static PWA."""

import logging
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from saathi import __version__
from saathi.auth import Principal, make_principal_dependency, require_supervisor
from saathi.chat import ChatResponse, ChatService
from saathi.config import Settings
from saathi.guardrails import GuardrailError, sanitize_text
from saathi.logging_config import configure_logging
from saathi.models import (
    ChatRequest,
    CompleteRequest,
    DecisionRequest,
    DiagnoseRequest,
    DiagnosisReport,
    MachineStatus,
    WorkOrder,
    WorkOrderStatus,
)
from saathi.services import Services, build_services
from saathi.tools.workorders import WorkOrderError

log = logging.getLogger("saathi.api")
WEB_DIR = Path(os.getenv("SAATHI_WEB_DIR") or Path(__file__).resolve().parent.parent / "web")
MAX_BODY_BYTES = 8 * 1024 * 1024
MachineId = Annotated[str, PathParam(pattern=r"^M-\d{2}$")]
WorkOrderId = Annotated[str, PathParam(pattern=r"^WO-\d{8}-[A-F0-9]{6}$")]

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


class DiagnoseResponse(BaseModel):
    report: DiagnosisReport
    work_order: WorkOrder | None = None
    work_order_created: bool = False


class RateLimiter:
    """Per-client sliding window. In-memory, so per Cloud Run instance (use Cloud Armor for global)."""

    def __init__(self, per_minute: int):
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        if self._limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        if len(self._hits) > 10_000:  # bound memory under address churn
            self._hits.clear()
        window = self._hits[key]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= self._limit:
            return False
        window.append(now)
        return True


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: RateLimiter):
        super().__init__(app)
        self._limiter = limiter

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:12])[:64]
        started = time.perf_counter()
        client = request.client.host if request.client else "unknown"
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            response = JSONResponse({"detail": "Request body too large"}, status_code=413)
        elif request.url.path.startswith("/api/") and not self._limiter.allow(client):
            response = JSONResponse({"detail": "Too many requests"}, status_code=429)
            response.headers["Retry-After"] = "30"
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response


def create_app(services: Services | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or (services.settings if services else Settings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        app.state.services = services or build_services(settings)
        app.state.chat = ChatService(app.state.services)
        log.info("started", extra={"version": __version__, "auth_mode": settings.auth_mode})
        yield

    app = FastAPI(
        title="ShopFloor Saathi",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.add_middleware(SecurityMiddleware, limiter=RateLimiter(settings.rate_limit_per_minute))
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    principal_dep = make_principal_dependency(settings)
    PrincipalDep = Annotated[Principal, Depends(principal_dep)]

    def svc(request: Request) -> Services:
        return request.app.state.services

    ServicesDep = Annotated[Services, Depends(svc)]

    # ---------- error handling ----------
    @app.exception_handler(GuardrailError)
    async def _guardrail(_: Request, exc: GuardrailError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(WorkOrderError)
    async def _work_order(_: Request, exc: WorkOrderError):
        code = 404 if "Unknown" in str(exc) else 409
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.exception_handler(KeyError)
    async def _missing(_: Request, exc: KeyError):
        return JSONResponse(
            {"detail": str(exc.args[0]) if exc.args else "Not found"}, status_code=404
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    # ---------- health ----------
    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request):
        ready = hasattr(request.app.state, "services")
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    # ---------- read APIs ----------
    @app.get("/api/me")
    async def me(principal: PrincipalDep, services: ServicesDep):
        from saathi.chat import llm_configured

        return {
            "user_id": principal.user_id,
            "role": principal.role,
            "auth_mode": settings.auth_mode,
            "assistant_mode": "gemini" if llm_configured(services) else "offline",
        }

    @app.get("/api/machines", response_model=list[MachineStatus])
    async def machines(_: PrincipalDep, services: ServicesDep):
        return services.telemetry.fleet_status()

    @app.get("/api/machines/{machine_id}", response_model=MachineStatus)
    async def machine(machine_id: MachineId, _: PrincipalDep, services: ServicesDep):
        return services.telemetry.status(machine_id)

    @app.get("/api/assumptions")
    async def assumptions(_: PrincipalDep, services: ServicesDep):
        s = services.settings
        return {
            "downtime_cost_inr_per_hour": s.downtime_cost_inr_per_hour,
            "energy_tariff_inr_per_kwh": s.energy_tariff_inr_per_kwh,
            "grid_emission_factor_kg_per_kwh": s.grid_ef_kg_per_kwh,
            "note": "Impact figures are projections under these assumptions; replace them with your plant's numbers.",
        }

    # ---------- diagnosis and chat ----------
    @app.post("/api/diagnose", response_model=DiagnoseResponse)
    async def diagnose(body: DiagnoseRequest, principal: PrincipalDep, services: ServicesDep):
        symptom = sanitize_text(body.symptom)
        report = services.engine.diagnose(body.machine_id, symptom)
        order, created = None, False
        if body.create_work_order and report.recommended:
            order, created = services.work_orders.create_draft(
                report.recommended, principal.user_id
            )
        return DiagnoseResponse(report=report, work_order=order, work_order_created=created)

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, principal: PrincipalDep, request: Request):
        return await request.app.state.chat.respond(principal, body)

    # ---------- work orders ----------
    @app.get("/api/workorders", response_model=list[WorkOrder])
    async def list_work_orders(
        _: PrincipalDep,
        services: ServicesDep,
        status: Annotated[WorkOrderStatus | None, Query()] = None,
    ):
        return services.work_orders.list(status)

    @app.post("/api/workorders/{work_order_id}/decision", response_model=WorkOrder)
    async def decide(
        work_order_id: WorkOrderId,
        body: DecisionRequest,
        principal: PrincipalDep,
        services: ServicesDep,
    ):
        require_supervisor(principal)
        note = sanitize_text(body.note, 500)
        order = services.work_orders.decide(
            work_order_id, body.decision == "approve", principal.user_id, note
        )
        log.info(
            "work order decision",
            extra={"work_order": order.id, "decision": body.decision, "by": principal.user_id},
        )
        return order

    @app.post("/api/workorders/{work_order_id}/complete", response_model=WorkOrder)
    async def complete(
        work_order_id: WorkOrderId,
        body: CompleteRequest,
        principal: PrincipalDep,
        services: ServicesDep,
    ):
        require_supervisor(principal)
        return services.work_orders.complete(
            work_order_id, body.actual_downtime_min, principal.user_id
        )

    @app.get("/api/impact/summary")
    async def impact_summary(_: PrincipalDep, services: ServicesDep):
        return services.work_orders.impact_summary()

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app
