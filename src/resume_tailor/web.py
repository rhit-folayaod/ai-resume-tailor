"""HTTP API behind the browser UI.

The same pipeline the CLI runs, exposed over localhost. Nothing here can
introduce resume content either: `/api/tailor` returns selections drawn from the
store, and the only way text enters the store is `PUT /api/store`, which is you
typing it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .compile import compile_pdf, find_tectonic
from .errors import CompileError, ResumeTailorError
from .ingest import text_from_upload, text_from_url
from .jd_parser import parse_job_description
from .latex import render_resume
from .llm import DEFAULT_MODEL, LLMClient, OpenAIClient
from .models import ResumeStore
from .ranking import Selection, SelectionBudget, select
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
) -> FastAPI:
    store_path = Path(store_path)
    app = FastAPI(title="resume-tailor", docs_url=None, redoc_url=None)
    runs: dict[str, dict[str, Any]] = {}

    @app.exception_handler(ResumeTailorError)
    async def _expected_failure(_: Request, exc: ResumeTailorError) -> JSONResponse:
        # Every anticipated failure reaches the browser as a sentence, not a 500.
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        try:
            tectonic_path: str | None = find_tectonic(tectonic)
        except CompileError:
            tectonic_path = None
        import os

        return {
            "store_path": str(store_path.resolve()),
            "store_exists": store_path.exists(),
            "tectonic": tectonic_path,
            "model": model or os.environ.get("RESUME_TAILOR_MODEL") or DEFAULT_MODEL,
            "model_configured": bool(
                os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL")
            ),
        }

    @app.get("/api/store")
    def read_store() -> dict[str, Any]:
        return load_store(store_path).model_dump(mode="json")

    @app.put("/api/store")
    def write_store(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            store = ResumeStore.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"error": "that content is not valid.\n" + format_validation_error(exc, payload)},
            )
        save_store(store, store_path)
        return store.model_dump(mode="json")

    @app.post("/api/ingest")
    async def ingest(
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

    @app.post("/api/tailor")
    def tailor(request: TailorRequest) -> dict[str, Any]:
        store = load_store(store_path)
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
        }
        while len(runs) > MAX_CACHED_RUNS:
            runs.pop(next(iter(runs)))

        return {
            "run_id": run_id,
            "job": jd.model_dump(mode="json"),
            "selection": _selection_payload(selection),
        }

    @app.get("/api/resume/{run_id}")
    def resume_pdf(run_id: str) -> Response:
        run = runs.get(run_id)
        if run is None:
            raise ResumeTailorError("that result expired; tailor the resume again.")
        pdf = compile_pdf(run["tex"], tectonic=tectonic)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="resume.pdf"'},
        )

    @app.get("/api/resume/{run_id}/tex")
    def resume_tex(run_id: str) -> Response:
        run = runs.get(run_id)
        if run is None:
            raise ResumeTailorError("that result expired; tailor the resume again.")
        return Response(content=run["tex"], media_type="text/plain; charset=utf-8")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEBUI_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(WEBUI_DIR)), name="static")
    return app


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
