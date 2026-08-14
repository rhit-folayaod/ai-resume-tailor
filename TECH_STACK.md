# Tech Stack — resume-tailor

Portfolio-ready overview of what powers
[ai-resume-tailor](https://ai-resume-tailor.fly.dev): an invite-only web app +
CLI that ranks **pre-written** resume bullets against a job posting and compiles
a PDF with LaTeX — without inventing new claims.

**Live:** https://ai-resume-tailor.fly.dev  
**Repo:** https://github.com/rhit-folayaod/ai-resume-tailor

---

## One-liner (resume / LinkedIn)

> Full-stack Python app: FastAPI + plain JS UI, Supabase Auth/Postgres, OpenAI
> structured extraction, Tectonic/LaTeX PDF compile, Docker on Fly.io — with a
> schema-enforced no-fabrication pipeline for resume content.

---

## Architecture (at a glance)

```
Browser (HTML/CSS/JS)
        │  REST + Bearer JWT
        ▼
FastAPI (Uvicorn) ──► OpenAI-compatible LLM (JSON only)
        │                    ▲
        ├─ content store ────┘  (JD parse / optional rerank / resume import)
        ├─ Jinja2 → LaTeX template
        └─ Tectonic → PDF
                │
        Supabase Auth + Postgres (hosted)
        Fly.io Docker + volume (TeX cache)
```

---

## Stack by layer

| Layer | Technologies |
| --- | --- |
| **Language / runtime** | Python 3.11+, [uv](https://docs.astral.sh/uv/) for deps & lockfile |
| **API** | FastAPI, Uvicorn, Pydantic v2, httpx, PyJWT |
| **CLI** | Click (`resume-tailor serve`, `tailor`, `seed-store`) |
| **Frontend** | Vanilla HTML / CSS / JS (no React/build step), Google Fonts |
| **AI / LLM** | OpenAI API (`gpt-4o-mini` default), OpenAI-compatible `base_url` (Ollama, etc.) |
| **Documents** | pypdf, BeautifulSoup4 (JD ingest), Jinja2 + Tectonic (LaTeX → PDF) |
| **Data (local)** | YAML content store (`projects.yaml`) |
| **Data (hosted)** | Supabase Postgres (JSONB stores, allowlist, usage events), PostgREST via service role |
| **Auth** | Supabase Auth magic link (PKCE), optional admin password session, email allowlist |
| **Deploy** | Docker (python:3.11-slim + Tectonic), Fly.io (`iad`), persistent volume for TeX cache |
| **Testing** | pytest (~100+ tests), scripted LLM fakes, no live network in CI suite |

---

## Backend & domain

- **Content model:** strict Pydantic `ResumeStore` (profile, education, skills, projects/experience, leadership) — unknown keys rejected
- **Ranking:** deterministic skill/keyword matching first; optional LLM **rerank** that can only reorder IDs, never emit bullet text
- **JD parse:** LLM extracts required/preferred skills for scoring only (not printed on the resume)
- **Resume import:** PDF/text → structured store draft → user review → save
- **Rate limits:** per-user daily parse / compile caps in hosted mode
- **PDF pipeline:** Jinja2 LaTeX template → Tectonic compile → inline preview + download

---

## Frontend

- Two-tab UI: **Tailor** (JD ingest + selection + PDF) and **Content** (editor + resume drop-import)
- Hosted sign-in gate (magic link + admin password)
- FormData uploads, drag-and-drop, localStorage session for tokens
- No SPA framework — intentional for a small deployable surface

---

## Infrastructure & security

| Concern | Approach |
| --- | --- |
| Hosting | Fly.io Machines, HTTPS, auto start/stop |
| Secrets | Fly secrets (`OPENAI_*`, `SUPABASE_*`, `ADMIN_PASSWORD`) |
| AuthZ | Allowlisted emails; service role never exposed to the browser |
| Session verify | Supabase Auth `/user` (+ JWT fallback) |
| Isolation | Per-user `resume_stores` keyed by `auth.users.id` |
| Build | Multi-stage-ish Docker image with uv sync + baked Tectonic binary |

---

## Notable packages (`pyproject.toml`)

`fastapi` · `uvicorn` · `pydantic` · `openai` · `httpx` · `pyjwt` · `jinja2` · `pyyaml` · `pypdf` · `beautifulsoup4` · `click` · `python-multipart` · `pytest` (dev)

---

## Skills keywords (copy/paste)

`Python` · `FastAPI` · `Pydantic` · `REST APIs` · `OpenAI API` · `LLM structured output` · `Supabase` · `PostgreSQL` · `JWT / Auth` · `Docker` · `Fly.io` · `LaTeX / Tectonic` · `Jinja2` · `Click CLI` · `pytest` · `Vanilla JavaScript` · `HTML/CSS` · `PDF parsing` · `uv`

---

## What makes the design interesting (talking points)

1. **No-fabrication invariant** — printed resume text only comes from a validated store; the LLM never has a free-text path into the PDF.
2. **Dual mode** — same codebase runs as local YAML CLI/UI or multi-user hosted SaaS-style app.
3. **Heavyweight PDF on a thin host** — Tectonic in Docker on Fly with a cache volume, not serverless.
4. **Schema-first AI** — every model response is JSON-validated (and coerced for import) before use.
