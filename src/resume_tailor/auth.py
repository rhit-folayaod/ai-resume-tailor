"""Supabase Auth verification and invite allowlist.

Hosted mode only. Local `serve` without Supabase env vars skips this path.

Tokens are validated by asking Supabase Auth who the bearer is
(`GET /auth/v1/user`). That survives JWT secret / signing-key changes in the
dashboard. Local HS256 decode remains as a fallback for tests.

Optional admin password login issues a short-lived local JWT (not Supabase)
so the owner can test without magic-link email delivery.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from jwt import PyJWTError

from .errors import AuthError

ALGORITHM = "HS256"
ADMIN_AUDIENCE = "resume-tailor-admin"
ADMIN_USER_ID = "00000000-0000-4000-8000-000000000001"
ADMIN_EMAIL = "admin@resume-tailor.local"
ADMIN_TOKEN_HOURS = 72
# Testing convenience. Override with ADMIN_PASSWORD; set to empty/`off` to disable.
DEFAULT_ADMIN_PASSWORD = "glorytothemoon"


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str


@dataclass(frozen=True)
class HostingConfig:
    """Env-driven hosted-mode settings. Missing URL means local filesystem mode."""

    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    jwt_secret: str = ""
    admin_password: str = ""
    daily_parse_limit: int = 30
    daily_compile_limit: int = 20

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"

    @property
    def auth_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def admin_login_enabled(self) -> bool:
        return bool(self.admin_password)

    @classmethod
    def from_env(cls) -> HostingConfig | None:
        url = os.environ.get("SUPABASE_URL", "").strip()
        if not url:
            return None
        anon = os.environ.get("SUPABASE_ANON_KEY", "").strip()
        service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
        missing = [
            name
            for name, value in (
                ("SUPABASE_ANON_KEY", anon),
                ("SUPABASE_SERVICE_ROLE_KEY", service),
            )
            if not value
        ]
        if missing:
            raise AuthError(
                "hosted mode is partially configured. Set "
                + ", ".join(missing)
                + ", or unset SUPABASE_URL for local filesystem mode."
            )
        return cls(
            supabase_url=url,
            supabase_anon_key=anon,
            supabase_service_role_key=service,
            jwt_secret=secret,
            admin_password=_admin_password_from_env(),
            daily_parse_limit=_int_env("RESUME_TAILOR_DAILY_PARSE_LIMIT", 30),
            daily_compile_limit=_int_env("RESUME_TAILOR_DAILY_COMPILE_LIMIT", 20),
        )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AuthError(f"{name} must be an integer, got {raw!r}.") from exc
    if value < 1:
        raise AuthError(f"{name} must be at least 1.")
    return value


def _admin_password_from_env() -> str:
    if "ADMIN_PASSWORD" not in os.environ:
        return DEFAULT_ADMIN_PASSWORD
    raw = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not raw or raw.lower() in {"off", "false", "0", "disabled"}:
        return ""
    return raw


def _admin_signing_key(config: HostingConfig) -> str:
    return config.jwt_secret or config.supabase_service_role_key


def issue_admin_token(config: HostingConfig) -> dict[str, Any]:
    """Mint a local admin session. Caller must have already checked the password."""

    if not config.admin_login_enabled:
        raise AuthError("admin password login is disabled.")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=ADMIN_TOKEN_HOURS)
    token = jwt.encode(
        {
            "sub": ADMIN_USER_ID,
            "email": ADMIN_EMAIL,
            "aud": ADMIN_AUDIENCE,
            "rt_admin": True,
            "iat": now,
            "exp": expires,
        },
        _admin_signing_key(config),
        algorithm=ALGORITHM,
    )
    return {
        "access_token": token,
        "refresh_token": "",
        "expires_in": ADMIN_TOKEN_HOURS * 3600,
        "user": {"id": ADMIN_USER_ID, "email": ADMIN_EMAIL},
    }


def verify_admin_password(password: str, config: HostingConfig) -> None:
    if not config.admin_login_enabled:
        raise AuthError("admin password login is disabled.")
    if not password or password.strip() != config.admin_password:
        raise AuthError("wrong admin password.")


def verify_access_token(token: str, config: HostingConfig) -> AuthUser:
    """Validate a Supabase or local admin access token and return the user."""

    if not token:
        raise AuthError("sign in required.")

    try:
        return _user_from_admin_jwt(token, config)
    except AuthError:
        pass

    auth_error: AuthError | None = None
    try:
        return _user_from_auth_api(token, config)
    except AuthError as exc:
        auth_error = exc

    if config.jwt_secret:
        try:
            return _user_from_jwt(token, config)
        except AuthError:
            pass

    raise auth_error or AuthError("your session is invalid or expired; sign in again.")


def _user_from_admin_jwt(token: str, config: HostingConfig) -> AuthUser:
    if not config.admin_login_enabled:
        raise AuthError("admin login is not configured.")
    try:
        payload = jwt.decode(
            token,
            _admin_signing_key(config),
            algorithms=[ALGORITHM],
            audience=ADMIN_AUDIENCE,
        )
    except PyJWTError as exc:
        raise AuthError("your session is invalid or expired; sign in again.") from exc
    if not payload.get("rt_admin"):
        raise AuthError("your session is invalid or expired; sign in again.")
    return AuthUser(id=ADMIN_USER_ID, email=ADMIN_EMAIL)


def _user_from_auth_api(token: str, config: HostingConfig) -> AuthUser:
    headers = {
        "apikey": config.supabase_anon_key,
        "Authorization": f"Bearer {token}",
    }
    try:
        response = httpx.get(f"{config.auth_url}/user", headers=headers, timeout=20.0)
    except httpx.HTTPError as exc:
        raise AuthError(f"could not reach Supabase Auth: {exc}") from exc
    if response.status_code in (401, 403):
        raise AuthError("your session is invalid or expired; sign in again.")
    if response.status_code >= 400:
        raise AuthError(
            f"Supabase Auth lookup failed ({response.status_code}): {response.text[:200]}"
        )
    data = response.json()
    user_id = data.get("id")
    email = (data.get("email") or "").strip().lower()
    if not user_id or not email:
        raise AuthError("your session is missing an email; sign in again.")
    return AuthUser(id=str(user_id), email=email)


def _user_from_jwt(token: str, config: HostingConfig) -> AuthUser:
    try:
        payload = jwt.decode(
            token,
            config.jwt_secret,
            algorithms=[ALGORITHM],
            audience="authenticated",
        )
    except PyJWTError as exc:
        raise AuthError("your session is invalid or expired; sign in again.") from exc

    user_id = payload.get("sub")
    email = (payload.get("email") or "").strip().lower()
    if not user_id or not email:
        raise AuthError("your session is missing an email; sign in again.")
    return AuthUser(id=str(user_id), email=email)


def extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise AuthError("send an Authorization: Bearer <access_token> header.")
    return value.strip()


def is_email_allowed(email: str, config: HostingConfig) -> bool:
    """True if `email` appears in allowed_users (case-insensitive)."""

    normalized = email.strip().lower()
    rows = _rest_get(
        config,
        "allowed_users",
        params={"email": f"eq.{normalized}", "select": "email"},
    )
    return bool(rows)


def require_allowed_user(user: AuthUser, config: HostingConfig) -> AuthUser:
    if user.id == ADMIN_USER_ID and user.email == ADMIN_EMAIL:
        return user
    if not is_email_allowed(user.email, config):
        raise AuthError(
            f"{user.email} is not on the invite list. Ask the owner to add your "
            "email to allowed_users, then try again."
        )
    return user


def _rest_get(config: HostingConfig, table: str, params: dict[str, str]) -> list[Any]:
    headers = {
        "apikey": config.supabase_service_role_key,
        "Authorization": f"Bearer {config.supabase_service_role_key}",
    }
    try:
        response = httpx.get(
            f"{config.rest_url}/{table}",
            params=params,
            headers=headers,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise AuthError(f"could not reach Supabase: {exc}") from exc
    if response.status_code >= 400:
        raise AuthError(
            f"Supabase {table} query failed ({response.status_code}): {response.text[:200]}"
        )
    data = response.json()
    if not isinstance(data, list):
        raise AuthError(f"unexpected Supabase response for {table}.")
    return data
