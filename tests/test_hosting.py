"""Tests for hosted auth, allowlist, rate limits, and per-user stores.

No live Supabase or OpenAI — HTTP and JWT are faked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
import yaml
from conftest import STORE_DATA, ScriptedClient
from fastapi.testclient import TestClient

from resume_tailor import auth as auth_mod
from resume_tailor import web
from resume_tailor.auth import AuthUser, HostingConfig, verify_access_token
from resume_tailor.errors import AuthError, RateLimitError
from resume_tailor.rate_limit import check_and_record
from resume_tailor.seed import empty_store
from resume_tailor.store import load_store

JWT_SECRET = "test-jwt-secret-for-resume-tailor"
JD_TEXT = (
    "Software Engineer Intern, Backend Platform. We are looking for someone with "
    "Python and PostgreSQL experience. Docker is a plus."
)
JD_RESPONSE = (
    '{"required_skills": ["Python", "PostgreSQL"], "preferred_skills": ["Docker"],'
    ' "role_flavor": "backend platform", "seniority": "intern"}'
)


def make_config(**overrides) -> HostingConfig:
    base = dict(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key",
        supabase_service_role_key="service-key",
        jwt_secret=JWT_SECRET,
        admin_password="",
        daily_parse_limit=30,
        daily_compile_limit=20,
    )
    base.update(overrides)
    return HostingConfig(**base)


def make_token(email: str = "friend@example.com", sub: str = "user-1", **claims) -> str:
    payload = {
        "sub": sub,
        "email": email,
        "aud": "authenticated",
        "role": "authenticated",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        **claims,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture
def store_path(tmp_path):
    path = tmp_path / "projects.yaml"
    path.write_text(yaml.safe_dump(STORE_DATA), encoding="utf-8")
    return path


@pytest.fixture
def hosting():
    return make_config()


@pytest.fixture
def client(store_path, hosting, monkeypatch):
    monkeypatch.setattr(auth_mod, "is_email_allowed", lambda email, config: True)
    return TestClient(web.create_app(store_path=store_path, hosting=hosting))


def auth_header(email: str = "friend@example.com", sub: str = "user-1") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(email=email, sub=sub)}"}


def test_verify_access_token_reads_email_and_id(monkeypatch):
    # Auth API is tried first; force it to fail so the JWT fallback is exercised.
    monkeypatch.setattr(
        auth_mod,
        "_user_from_auth_api",
        lambda token, config: (_ for _ in ()).throw(AuthError("unreachable")),
    )
    user = verify_access_token(make_token(), make_config())
    assert user.email == "friend@example.com"
    assert user.id == "user-1"


def test_verify_uses_auth_api_when_available(monkeypatch):
    monkeypatch.setattr(
        auth_mod,
        "_user_from_auth_api",
        lambda token, config: AuthUser(id="api-user", email="api@example.com"),
    )
    user = verify_access_token("any-token", make_config(jwt_secret=""))
    assert user.id == "api-user"
    assert user.email == "api@example.com"


def test_verify_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(
        auth_mod,
        "_user_from_auth_api",
        lambda token, config: (_ for _ in ()).throw(
            AuthError("your session is invalid or expired; sign in again.")
        ),
    )
    with pytest.raises(AuthError, match="invalid or expired"):
        verify_access_token("not.a.jwt", make_config())


def test_config_exposes_anon_key_not_service_role(client):
    body = client.get("/api/config").json()
    assert body["auth_required"] is True
    assert body["supabase_anon_key"] == "anon-key"
    assert body["admin_login_enabled"] is False
    assert "service_role" not in body


def test_admin_login_issues_token_and_opens_store(store_path, monkeypatch):
    hosting = make_config(admin_password="glorytothemoon")
    monkeypatch.setattr(auth_mod, "is_email_allowed", lambda email, config: False)
    monkeypatch.setattr(
        web,
        "load_or_create_store",
        lambda user, config: empty_store(email=user.email),
    )
    client = TestClient(web.create_app(store_path=store_path, hosting=hosting))

    assert client.get("/api/config").json()["admin_login_enabled"] is True
    bad = client.post("/api/admin-login", json={"password": "nope"})
    assert bad.status_code == 401

    ok = client.post("/api/admin-login", json={"password": "glorytothemoon"})
    assert ok.status_code == 200, ok.text
    token = ok.json()["access_token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "admin@resume-tailor.local"
    store = client.get("/api/store", headers={"Authorization": f"Bearer {token}"})
    assert store.status_code == 200
    assert store.json()["profile"]["email"] == "admin@resume-tailor.local"


def test_store_requires_auth_when_hosted(store_path, hosting):
    client = TestClient(web.create_app(store_path=store_path, hosting=hosting))
    response = client.get("/api/store")
    assert response.status_code == 401
    assert "sign in" in response.json()["error"]


def test_unlisted_email_is_rejected(store_path, hosting, monkeypatch):
    monkeypatch.setattr(auth_mod, "is_email_allowed", lambda email, config: False)
    client = TestClient(web.create_app(store_path=store_path, hosting=hosting))
    response = client.get("/api/store", headers=auth_header())
    assert response.status_code == 401
    assert "invite list" in response.json()["error"]


def test_allowed_user_loads_seeded_store(client, monkeypatch):
    monkeypatch.setattr(
        web,
        "load_or_create_store",
        lambda user, config: empty_store(email=user.email),
    )
    body = client.get("/api/store", headers=auth_header()).json()
    assert body["profile"]["email"] == "friend@example.com"
    assert body["projects"] == []


def test_tailor_records_parse_usage(client, monkeypatch, store_path):
    recorded: list[str] = []

    def fake_check(user, kind, config):
        recorded.append(kind)

    monkeypatch.setattr(web, "check_and_record", fake_check)
    monkeypatch.setattr(
        web,
        "load_or_create_store",
        lambda user, config: load_store(store_path),
    )
    monkeypatch.setattr(web, "make_client", lambda model=None: ScriptedClient(JD_RESPONSE))

    response = client.post(
        "/api/tailor",
        headers=auth_header(),
        json={"jd_text": JD_TEXT, "max_bullets": 3},
    )
    assert response.status_code == 200, response.text
    assert recorded == ["parse"]
    assert response.json()["selection"]["bullet_count"] == 3


def test_rate_limit_blocks_when_over_cap(monkeypatch):
    config = make_config(daily_parse_limit=2)
    user = AuthUser(id="user-1", email="friend@example.com")
    monkeypatch.setattr("resume_tailor.rate_limit.count_today", lambda *a, **k: 2)
    with pytest.raises(RateLimitError, match="daily parse limit"):
        check_and_record(user, "parse", config)


def test_empty_store_seed_is_valid():
    store = empty_store(email="a@b.com", name="Friend")
    assert store.profile.email == "a@b.com"
    assert store.projects == []


def test_local_mode_config_does_not_require_auth(store_path):
    client = TestClient(web.create_app(store_path=store_path, hosting=None))
    assert client.get("/api/config").json() == {"auth_required": False}
    assert client.get("/api/store").status_code == 200


def test_health_is_public_in_hosted_mode(client):
    body = client.get("/api/health").json()
    assert body["auth_required"] is True
    assert body["user"] is None
