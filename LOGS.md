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

Open question for review: `domains` and `keywords` on the seeded entries were
inferred from the existing resume text as matching metadata. They never print
(except `technologies` on project entries), but they do steer ranking, so they
are worth a look.
