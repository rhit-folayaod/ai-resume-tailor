# Build log

A record of what was built in each phase, the decisions made, and anything left
open. Newest phase at the bottom.

## Phase 0 — Scaffolding

- `uv init --package` layout: `src/resume_tailor/`, entry point `resume-tailor = resume_tailor.cli:main`.
- Runtime dependencies: `click`, `jinja2`, `pydantic`, `pyyaml`. `openai` is an
  optional extra so the package installs and the test suite runs with no LLM
  dependency at all.
- Dev group: `pytest`.
- `.gitignore` excludes `*.pdf`, including the source resume, since it carries a
  phone number and this repo may go public.

## Phase 1 — Project/skill schema and data store

Files: `src/resume_tailor/models.py`, `src/resume_tailor/store.py`,
`src/resume_tailor/errors.py`, `projects.yaml`.

**Schema.** `ProjectEntry` holds the fields from the spec (`name`, `role`,
`dates`, `technologies`, `domains`, `bullets`) plus a few the resume layout
needs: `organization`, `location`, and `section` (`experience` vs `project`,
which decides where an entry renders). Two optional fields exist purely for
ranking: `keywords` (extra match terms that should not print) and
`always_include` (an entry that should survive selection regardless of score).
`id` defaults to a slug of `name` and is checked for uniqueness, so selection
output can refer to entries by a stable handle.

`dates` is a `DateRange` of free-text `start`/`end` strings rather than parsed
dates, because the resume uses forms like "Summer 2025" and "Winter 2024 –
Present". `end: present` renders as "Present".

The whole file is one `ResumeStore`: `profile`, `education`, `skills`,
`projects`, `leadership`. The template needs all of it, and keeping it in one
file matches the spec's "single `projects.yaml`".

**Strictness.** Every model forbids extra keys. A typo'd key such as
`tecnologies:` is a hard error rather than silently dropped content — the
failure mode being designed against is a resume that is quietly missing things.
Empty bullets and empty `name`/`role` are rejected; bullet whitespace is
normalized but wording is never touched.

**Error reporting.** `load_store` distinguishes three failures: file missing,
invalid YAML (reported with line and column), and schema mismatch. Pydantic
error locations are rewritten into something findable by hand, e.g.
`projects -> entry 2 ("Bad Entry") -> bullets: bullet 1 is empty`, instead of
`projects.1.bullets`.

**Seed data.** `projects.yaml` is seeded with the five entries named in the spec
(Emerson-NI, RHV gas valve, jetpack DAQ demo, TA work, Ask Rose) plus the three
projects already on the resume (NBA Player Predictor, Settlers of Catan, Lost
and Found Database), so the shape is visible across both `section` types.

All `bullets` lists are empty, as specified — that content gets written by hand.
`profile`, `education`, and `skills` are filled in from the existing resume,
since the template needs real values to render against. `phone` is left blank
so it is not committed.

Reviewed: `domains` and `keywords` were initially inferred from the existing
resume text, and have been emptied at your request — nothing in the store is
inferred on your behalf. `technologies` stays populated where the resume already
lists it verbatim. An entry with no `domains` still matches on `technologies`.

## Phase 2 — Job description parser

Files: `src/resume_tailor/llm.py`, `src/resume_tailor/jd_parser.py`,
`tests/test_jd_parser.py`.

**The LLM boundary is one method.** `LLMClient` is a `Protocol` with a single
`complete_json(system, user) -> str`. Keeping it that narrow is what makes the
no-fabrication argument checkable: there is exactly one place model text enters
the process, and every caller validates before using the result. Tests mock this
protocol with a scripted fake, so the suite never touches the network.

`OpenAIClient` is the real implementation. It targets OpenAI-compatible chat
completions, so `OPENAI_BASE_URL` can point at Ollama, OpenRouter, or Groq
instead. Model comes from `RESUME_TAILOR_MODEL` (default `gpt-4o-mini`),
temperature is 0, and `response_format` is forced to `json_object`. The `openai`
import is lazy so importing the package costs nothing and a missing key produces
an instruction rather than a traceback.

**Validation with one retry** lives in `request_validated_json`, shared with
Phase 3 rather than written twice. It strips code fences, parses JSON, validates
against a Pydantic model, and on failure re-prompts once with the original
request plus the bad response and the specific error. Two failures raise
`LLMError` carrying the last error.

It also takes an optional `extra_validation` callback that runs after schema
validation and can raise `ValueError` to trigger the same retry path. That is
the hook Phase 3 uses to enforce "every id you returned already existed" — a
constraint a schema cannot express, wired into the retry loop rather than
bolted on after.

**The extraction prompt** is constrained to extraction: copy skills as written,
never infer related technologies ("Python" must not become "Django"), classify
required vs preferred by how the posting frames it, and emit `"unspecified"`
rather than guessing. `ParsedJobDescription` forbids extra keys, drops blanks,
and dedupes case-insensitively.

Worth being explicit about: nothing the parser returns is ever printed on the
resume. The parse only produces scoring terms for content that already exists in
`projects.yaml`, so even a badly hallucinated parse can only change which of your
real bullets get picked.

Tests cover the happy path, fenced JSON, dedupe, retry-with-error-in-prompt,
give-up-after-two, extra keys rejected, and empty input.
