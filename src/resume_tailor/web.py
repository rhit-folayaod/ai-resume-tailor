"""HTTP API behind the browser UI.

Local mode (no SUPABASE_URL): filesystem `projects.yaml`, no auth — same as
before. Hosted mode: Supabase JWT + allowlist + per-user JSONB store + rate
limits. Nothing here invents resume content either way.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .auth import (
    AuthUser,
    HostingConfig,
    extract_bearer,
    issue_admin_token,
    require_allowed_user,
    verify_access_token,
    verify_admin_password,
)
from .compile import compile_pdf, find_tectonic
from .db import load_or_create_store, save_user_store
from .errors import AuthError, CompileError, RateLimitError, ResumeTailorError
from .ingest import text_from_upload, text_from_url
from .jd_parser import parse_job_description
from .latex import render_resume
from .llm import DEFAULT_MODEL, LLMClient, OpenAIClient
from .models import ResumeStore
from .ranking import Selection, SelectionBudget, select
from .rate_limit import check_and_record
from .resume_parser import parse_resume_text
from .store import DEFAULT_STORE_PATH, format_validation_error, load_store, save_store

WEBUI_DIR = Path(__file__).parent / "webui"

# How many tailoring runs to keep around so the PDF can be compiled from the
# same selection the browser is looking at, without paying for a second parse.
MAX_CACHED_RUNS = 20


def make_client(model: str | None = None) -> LLMClient:
    """Indirection so tests can substitute a fake without touching the network."""

    return OpenAIClient(model=model)


class TailorRequest(BaseModel):
    jd_text: str
    max_bullets: int = Field(default=12, ge=1, le=60)
    max_projects: int = Field(default=6, ge=1, le=30)
    max_bullets_per_project: int = Field(default=3, ge=1, le=20)
    llm_rank: bool = False
    reorder_skills: bool = False


def create_app(
    store_path: Path | str = DEFAULT_STORE_PATH,
    model: str | None = None,
    tectonic: str | None = None,
    hosting: HostingConfig | None | object = ...,
) -> FastAPI:
    store_path = Path(store_path)
    if hosting is ...:
        hosting = HostingConfig.from_env()
    app = FastAPI(title="resume-tailor", docs_url=None, redoc_url=None)
    # run_id -> {tex, user_id}; user_id is None in local mode
    runs: dict[str, dict[str, Any]] = {}

    @app.exception_handler(AuthError)
    async def _auth_failure(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": str(exc)})

    @app.exception_handler(RateLimitError)
    async def _rate_limit(_: Request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"error": str(exc)})

    @app.exception_handler(ResumeTailorError)
    async def _expected_failure(_: Request, exc: ResumeTailorError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    def optional_user(request: Request) -> AuthUser | None:
        """Local mode: always None. Hosted: None when missing/invalid token.

        Soft-fails so /api/health stays public even if a stale Bearer is sent.
        """

        if hosting is None:
            return None
        header = request.headers.get("authorization")
        if not header:
            return None
        try:
            token = extract_bearer(header)
            user = verify_access_token(token, hosting)
            return require_allowed_user(user, hosting)
        except AuthError:
            return None

    def require_user(request: Request) -> AuthUser | None:
        if hosting is None:
            return None
        token = extract_bearer(request.headers.get("authorization"))
        user = verify_access_token(token, hosting)
        return require_allowed_user(user, hosting)

    def read_store_for(user: AuthUser | None) -> ResumeStore:
        if hosting is None or user is None:
            return load_store(store_path)
        return load_or_create_store(user, hosting)

    def write_store_for(user: AuthUser | None, store: ResumeStore) -> ResumeStore:
        if hosting is None or user is None:
            save_store(store, store_path)
            return store
        return save_user_store(user, store, hosting)

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        """Public client bootstrap. Anon key is safe to expose; service role is not."""

        if hosting is None:
            return {"auth_required": False}
        return {
            "auth_required": True,
            "supabase_url": hosting.supabase_url,
            "supabase_anon_key": hosting.supabase_anon_key,
            "daily_parse_limit": hosting.daily_parse_limit,
            "daily_compile_limit": hosting.daily_compile_limit,
            "admin_login_enabled": hosting.admin_login_enabled,
        }

    @app.post("/api/admin-login")
    def admin_login(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Password gate for owner testing when magic-link email is unavailable."""

        if hosting is None or not hosting.admin_login_enabled:
            raise AuthError("admin password login is disabled.")
        password = str(payload.get("password") or "")
        verify_admin_password(password, hosting)
        return issue_admin_token(hosting)

    @app.get("/api/health")
    def health(user: AuthUser | None = Depends(optional_user)) -> dict[str, Any]:
        try:
            tectonic_path: str | None = find_tectonic(tectonic)
        except CompileError:
            tectonic_path = None

        body: dict[str, Any] = {
            "auth_required": hosting is not None,
            "tectonic": tectonic_path,
            "model": model or os.environ.get("RESUME_TAILOR_MODEL") or DEFAULT_MODEL,
            "model_configured": bool(
                os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL")
            ),
        }
        if hosting is None:
            body["store_path"] = str(store_path.resolve())
            body["store_exists"] = store_path.exists()
        else:
            body["user"] = {"email": user.email, "id": user.id} if user else None
            body["daily_parse_limit"] = hosting.daily_parse_limit
            body["daily_compile_limit"] = hosting.daily_compile_limit
        return body

    @app.get("/api/me")
    def me(user: AuthUser | None = Depends(require_user)) -> dict[str, Any]:
        if hosting is None:
            return {"auth_required": False, "user": None}
        assert user is not None
        return {"auth_required": True, "user": {"email": user.email, "id": user.id}}

    @app.get("/api/store")
    def read_store(user: AuthUser | None = Depends(require_user)) -> dict[str, Any]:
        return read_store_for(user).model_dump(mode="json")

    @app.put("/api/store")
    def write_store(
        payload: dict[str, Any] = Body(...),
        user: AuthUser | None = Depends(require_user),
    ) -> dict[str, Any]:
        try:
            store = ResumeStore.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "that content is not valid.\n"
                    + format_validation_error(exc, payload)
                },
            )
        saved = write_store_for(user, store)
        return saved.model_dump(mode="json")

    @app.post("/api/ingest")
    async def ingest(
        user: AuthUser | None = Depends(require_user),
        url: str | None = Form(default=None),
        file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        if file is not None:
            data = await file.read()
            text = text_from_upload(file.filename or "upload", data)
            return {"text": text, "source": file.filename or "uploaded file"}
        if url:
            return {"text": text_from_url(url), "source": url}
        raise ResumeTailorError("give me a URL or a file.")

    @app.post("/api/import-resume")
    async def import_resume(
        user: AuthUser | None = Depends(require_user),
        file: UploadFile | None = File(default=None),
        text: str | None = Form(default=None),
        apply: str | None = Form(default=None),
    ) -> dict[str, Any]:
        """Parse an uploaded resume into a content-store draft.

        By default returns the draft for the browser to preview. Set apply=1 to
        replace the signed-in user's saved store immediately.
        """

        if hosting is not None and user is not None:
            check_and_record(user, "parse", hosting)

        if file is not None:
            data = await file.read()
            resume_text = text_from_upload(file.filename or "resume", data)
            source = file.filename or "uploaded resume"
        elif text and text.strip():
            resume_text = text
            source = "pasted resume"
        else:
            raise ResumeTailorError("drop a resume PDF/file or paste the resume text.")

        store = parse_resume_text(resume_text, make_client(model))
        applied = False
        if (apply or "").strip().lower() in {"1", "true", "yes", "on"}:
            write_store_for(user, store)
            applied = True
        return {
            "source": source,
            "applied": applied,
            "store": store.model_dump(mode="json"),
        }

    @app.post("/api/tailor")
    def tailor(
        request: TailorRequest,
        user: AuthUser | None = Depends(require_user),
    ) -> dict[str, Any]:
        if hosting is not None and user is not None:
            check_and_record(user, "parse", hosting)

        store = read_store_for(user)
        client = make_client(model)

        jd = parse_job_description(request.jd_text, client)
        selection = select(
            store,
            jd,
            budget=SelectionBudget(
                max_bullets=request.max_bullets,
                max_projects=request.max_projects,
                max_bullets_per_project=request.max_bullets_per_project,
            ),
            client=client if request.llm_rank else None,
        )

        run_id = uuid.uuid4().hex
        runs[run_id] = {
            "tex": render_resume(store, selection, jd, reorder_skills=request.reorder_skills),
            "user_id": user.id if user else None,
        }
        while len(runs) > MAX_CACHED_RUNS:
            runs.pop(next(iter(runs)))

        return {
            "run_id": run_id,
            "job": jd.model_dump(mode="json"),
            "selection": _selection_payload(selection),
        }

    @app.get("/api/resume/{run_id}")
    def resume_pdf(
        run_id: str,
        user: AuthUser | None = Depends(require_user),
    ) -> Response:
        run = _owned_run(runs, run_id, user)
        if hosting is not None and user is not None:
            check_and_record(user, "compile", hosting)
        pdf = compile_pdf(run["tex"], tectonic=tectonic)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="resume.pdf"'},
        )

    @app.get("/api/resume/{run_id}/tex")
    def resume_tex(
        run_id: str,
        user: AuthUser | None = Depends(require_user),
    ) -> Response:
        run = _owned_run(runs, run_id, user)
        return Response(content=run["tex"], media_type="text/plain; charset=utf-8")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(
            WEBUI_DIR / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def _no_store_webui(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")
    return app


def create_app_for_server() -> FastAPI:
    """Uvicorn factory entrypoint for Docker / Fly (reads hosting from the env)."""

    return create_app()


def _owned_run(
    runs: dict[str, dict[str, Any]],
    run_id: str,
    user: AuthUser | None,
) -> dict[str, Any]:
    run = runs.get(run_id)
    if run is None:
        raise ResumeTailorError("that result expired; tailor the resume again.")
    owner = run.get("user_id")
    if owner is not None and (user is None or user.id != owner):
        raise AuthError("that result belongs to another session; tailor again.")
    return run


def _selection_payload(selection: Selection) -> dict[str, Any]:
    chosen_ids = {item.project.id for item in selection.projects}
    return {
        "bullet_count": selection.bullet_count,
        "reranked_by_llm": selection.reranked_by_llm,
        "projects": [
            {
                "id": item.project.id,
                "name": item.project.name,
                "organization": item.project.organization,
                "section": item.project.section,
                "dates": item.project.dates.display,
                "score": item.score,
                "bullets": [
                    {"text": bullet.text, "score": bullet.score, "matched": bullet.matched}
                    for bullet in item.bullets
                ],
            }
            for item in selection.projects
        ],
        "skipped": [
            {
                "id": item.id,
                "name": item.project.name,
                "score": item.score,
                "reason": "no bullets written yet" if not item.bullets else "outscored",
            }
            for item in selection.ranked
            if item.id not in chosen_ids
        ],
    }
