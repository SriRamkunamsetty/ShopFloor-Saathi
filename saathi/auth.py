"""Authentication and role-based access.

dev mode      : roles come from the X-Saathi-Role header. Local development ONLY; refused when
                SAATHI_ENV=production (see Settings.validate).
firebase mode : verifies a Firebase Auth ID token (Authorization: Bearer ...) and reads the custom
                claim `role` ("operator" | "supervisor"). Users without the claim are operators.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

from saathi.config import Settings

log = logging.getLogger(__name__)
Role = Literal["operator", "supervisor"]
_USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: Role

    @property
    def is_supervisor(self) -> bool:
        return self.role == "supervisor"


def _dev_principal(request: Request) -> Principal:
    role = request.headers.get("x-saathi-role", "operator").lower()
    user = request.headers.get("x-saathi-user", "dev-user")
    if role not in ("operator", "supervisor"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if not _USER_RE.match(user):
        raise HTTPException(status_code=400, detail="Invalid user id")
    return Principal(user_id=user, role=role)  # type: ignore[arg-type]


def _firebase_principal(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = header[7:].strip()
    try:
        import firebase_admin  # optional dependency (extra: gcp)
        from firebase_admin import auth as fb_auth

        if not firebase_admin._apps:  # noqa: SLF001
            firebase_admin.initialize_app()
        claims = fb_auth.verify_id_token(token)
    except Exception as exc:  # noqa: BLE001 - any verification failure is a 401
        log.warning("token verification failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    role = claims.get("role", "operator")
    if role not in ("operator", "supervisor"):
        role = "operator"
    return Principal(user_id=str(claims.get("uid") or claims.get("sub")), role=role)


def make_principal_dependency(settings: Settings):
    resolver = _firebase_principal if settings.auth_mode == "firebase" else _dev_principal

    def current_principal(request: Request) -> Principal:
        return resolver(request)

    return current_principal


def require_supervisor(principal: Principal) -> Principal:
    if not principal.is_supervisor:
        raise HTTPException(status_code=403, detail="Supervisor role required")
    return principal
