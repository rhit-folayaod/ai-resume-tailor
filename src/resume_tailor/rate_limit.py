"""Per-user daily rate limits for hosted mode."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import httpx

from .auth import AuthUser, HostingConfig
from .db import DatabaseError
from .errors import RateLimitError

Kind = Literal["parse", "compile"]


def check_and_record(user: AuthUser, kind: Kind, config: HostingConfig) -> None:
    """Raise RateLimitError if the user is over their daily cap; otherwise record one use."""

    limit = (
        config.daily_parse_limit if kind == "parse" else config.daily_compile_limit
    )
    used = count_today(user, kind, config)
    if used >= limit:
        raise RateLimitError(
            f"daily {kind} limit reached ({limit}/day). Try again tomorrow, or ask "
            "the owner to raise RESUME_TAILOR_DAILY_"
            f"{'PARSE' if kind == 'parse' else 'COMPILE'}_LIMIT."
        )
    record(user, kind, config)


def count_today(user: AuthUser, kind: Kind, config: HostingConfig) -> int:
    start = _start_of_utc_day().isoformat()
    rows = _get(
        config,
        "usage_events",
        {
            "user_id": f"eq.{user.id}",
            "kind": f"eq.{kind}",
            "created_at": f"gte.{start}",
            "select": "id",
        },
    )
    return len(rows)


def record(user: AuthUser, kind: Kind, config: HostingConfig) -> None:
    headers = {
        "apikey": config.supabase_service_role_key,
        "Authorization": f"Bearer {config.supabase_service_role_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        response = httpx.post(
            f"{config.rest_url}/usage_events",
            headers=headers,
            json={"user_id": user.id, "kind": kind},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise DatabaseError(f"could not reach Supabase: {exc}") from exc
    if response.status_code >= 400:
        raise DatabaseError(
            f"usage_events insert failed ({response.status_code}): {response.text[:200]}"
        )


def _start_of_utc_day() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def _get(config: HostingConfig, table: str, params: dict[str, str]) -> list:
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
        raise DatabaseError(f"could not reach Supabase: {exc}") from exc
    if response.status_code >= 400:
        raise DatabaseError(
            f"Supabase {table} query failed ({response.status_code}): {response.text[:200]}"
        )
    data = response.json()
    if not isinstance(data, list):
        raise DatabaseError(f"unexpected Supabase response for {table}.")
    return data
