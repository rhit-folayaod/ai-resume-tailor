"""Per-user resume store backed by Supabase Postgres (PostgREST)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError

from .auth import AuthUser, HostingConfig
from .errors import ResumeTailorError, StoreError
from .models import ResumeStore
from .seed import empty_store
from .store import format_validation_error


class DatabaseError(ResumeTailorError):
    """Supabase REST call failed."""


def load_or_create_store(user: AuthUser, config: HostingConfig) -> ResumeStore:
    """Return the user's store, seeding an empty one on first login."""

    rows = _get(
        config,
        "resume_stores",
        {"user_id": f"eq.{user.id}", "select": "data"},
    )
    if not rows:
        store = empty_store(email=user.email)
        save_user_store(user, store, config)
        return store

    raw = rows[0].get("data")
    if not isinstance(raw, dict):
        raise StoreError("resume_stores.data must be a JSON object.")
    try:
        return ResumeStore.model_validate(raw)
    except ValidationError as exc:
        raise StoreError(
            "your saved content is invalid.\n" + format_validation_error(exc, raw)
        ) from exc


def save_user_store(user: AuthUser, store: ResumeStore, config: HostingConfig) -> ResumeStore:
    payload = {
        "user_id": user.id,
        "data": store.model_dump(mode="json"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _upsert(config, "resume_stores", payload, on_conflict="user_id")
    return store


def find_auth_user_by_email(email: str, config: HostingConfig) -> AuthUser:
    """Look up a Supabase Auth user id by email (service role)."""

    normalized = email.strip().lower()
    if not normalized:
        raise DatabaseError("email is required.")
    headers = _headers(config)
    try:
        response = httpx.get(
            f"{config.auth_url}/admin/users",
            params={"page": "1", "per_page": "200"},
            headers=headers,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise DatabaseError(f"could not reach Supabase Auth admin: {exc}") from exc
    if response.status_code >= 400:
        raise DatabaseError(
            f"Supabase Auth admin lookup failed ({response.status_code}): "
            f"{response.text[:200]}"
        )
    body = response.json()
    users = body.get("users") if isinstance(body, dict) else body
    if not isinstance(users, list):
        raise DatabaseError("unexpected Supabase Auth admin response.")
    for row in users:
        if not isinstance(row, dict):
            continue
        row_email = (row.get("email") or "").strip().lower()
        if row_email == normalized and row.get("id"):
            return AuthUser(id=str(row["id"]), email=row_email)
    raise DatabaseError(
        f"no auth user found for {normalized}. They must sign in once (magic link) "
        "so Supabase creates the account, then seed again."
    )


def seed_store_for_email(email: str, store: ResumeStore, config: HostingConfig) -> AuthUser:
    """Upsert `store` for the Auth user with this email."""

    user = find_auth_user_by_email(email, config)
    save_user_store(user, store, config)
    return user


def _headers(config: HostingConfig, *, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": config.supabase_service_role_key,
        "Authorization": f"Bearer {config.supabase_service_role_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _get(config: HostingConfig, table: str, params: dict[str, str]) -> list[Any]:
    try:
        response = httpx.get(
            f"{config.rest_url}/{table}",
            params=params,
            headers=_headers(config),
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise DatabaseError(f"could not reach Supabase: {exc}") from exc
    if response.status_code >= 400:
        raise DatabaseError(
            f"Supabase {table} query failed ({response.status_code}): {response.text[:200]}"
        )
    data = response.json()
    if not isinstance(data, list):
        raise DatabaseError(f"unexpected Supabase response for {table}.")
    return data


def _upsert(
    config: HostingConfig,
    table: str,
    payload: dict[str, Any],
    *,
    on_conflict: str,
) -> None:
    try:
        response = httpx.post(
            f"{config.rest_url}/{table}",
            params={"on_conflict": on_conflict},
            headers=_headers(config, prefer="resolution=merge-duplicates,return=minimal"),
            json=payload,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise DatabaseError(f"could not reach Supabase: {exc}") from exc
    if response.status_code >= 400:
        raise DatabaseError(
            f"Supabase {table} upsert failed ({response.status_code}): {response.text[:200]}"
        )
