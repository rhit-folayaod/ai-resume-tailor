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

## Phase 3 — Matching and ranking engine

Files: `src/resume_tailor/matching.py`, `src/resume_tailor/ranking.py`,
`tests/conftest.py`, `tests/test_matching.py`, `tests/test_ranking.py`.

**Matching is its own module** because it is the part most likely to be subtly
wrong. Term boundaries treat `+`, `#`, and `.` as part of a term, so "C" does not
match inside "C++" or "C#", and "R" does not match inside "React" — the exact
failure that would put the wrong project at the top of a resume. A small,
explicit alias table (`js`/`javascript`, `k8s`/`kubernetes`, `postgres`/
`postgresql`, and similar) handles the common spellings. It is deliberately not
fuzzy: a score you cannot explain is a score you cannot trust when deciding what
to send to an employer.

**Scoring is deterministic and takes no client.** `rank_projects` is a pure
function of the store, the parsed JD, and a `ScoringWeights` dataclass. Evidence
is weighted by how strong a claim it is: a required skill listed as a project's
technology (3.0) beats the same word appearing in tags (1.5) which beats it
appearing in prose (1.0), since prose mentions can be incidental. Bullets are
scored separately within each project so a project with eight bullets
contributes only its two most relevant. Small nudges for quantified bullets and
current roles. Every sort has an explicit tiebreak on the order written in
`projects.yaml`, so identical inputs always produce an identical resume.

**Budget is a parameter**, not a constant: `SelectionBudget(max_bullets,
max_projects, max_bullets_per_project, min_project_score)`. Allocation is
round-robin across ranked projects rather than depth-first, so the top project
cannot eat the entire budget and leave everything else invisible.

**The no-fabrication guarantee is enforced in three layers**, which is the part
worth reading the code for:

1. The candidate set is built from `projects.yaml` and frozen *before* the model
   is contacted.
2. The rerank response schema is `{project_id, bullet_index}` with extra keys
   forbidden — it carries no text field at all. Bullet text is looked up from
   the store after the call, so text the model emits is not merely rejected, it
   is never read. A model that tries to "improve" a bullet gets a validation
   error.
3. Every reference is checked against the frozen set via the `extra_validation`
   hook from Phase 2, which re-prompts once with the specific bad id and then
   fails loudly.

Per your call, the rerank is opt-in; deterministic scoring is the default, so
the tool works with no API key at all.

Tests: 32 passing, no network. They cover `C`/`C++`/`C#`/`R`/`React`
boundaries, ranking determinism, a hardware posting reordering the ranking,
budget spread, per-project caps, and four separate attempts by a mocked model to
escape the candidate set (unknown index, unknown project, duplicate, and a
response carrying invented bullet text).

## Phase 4 — LaTeX template

Files: `src/resume_tailor/latex.py`,
`src/resume_tailor/templates/resume.tex`, `tests/test_latex.py`.

**Delimiters.** The conventional LaTeX-friendly Jinja set: `\VAR{}` for values,
`\BLOCK{}` for statements, `\#{}` for comments, `%%` and `%#` for the line
forms, `trim_blocks` and `lstrip_blocks` on, autoescape off. Braces and
backslashes then belong to LaTeX alone.

Worth recording because it cost a test run: those delimiters are live inside
LaTeX comments too. A header comment in the template that *described* the syntax
by showing `\VAR{}` was parsed as an empty expression and failed to compile. The
comment now describes the delimiters without writing them, and says why.

**Escaping is not a filter, it is the `finalize` hook.** Every value that
reaches the template is escaped whether or not the template author asked for it.
A filter would work right up until someone adds a field and forgets `|e`, and
that bug surfaces as a Tectonic error several steps downstream. A `Raw` string
subclass exists as the deliberate opt-out; nothing currently uses it.

The escaper is one regex pass over an alternation of the special characters,
which is what keeps it correct: escaping character by character in sequence
turns `&` into `\&` and then the backslash rule turns that into
`\textbackslash{}&`. `C++` passes through unchanged (`+` is ordinary in text
mode) and `C#` becomes `C\#`; both are asserted directly, along with a realistic
bullet containing `%`, `&`, `$`, `#`, `+`, and `_` at once.

**The template** follows the layout of the existing resume: centered name and
contact line, then Education, Technical Skills, Experience, Projects, and
Leadership. Single column, 10pt, roughly half-inch margins, bold section
headings with a rule under them, organization and location on one line with role
and dates italicized beneath. No graphics, no multi-column tricks, nothing an
ATS parser has to guess at. Only `geometry`, `titlesec`, `enumitem`,
`hyperref`, and `lmodern` are used, which keeps what Tectonic has to fetch
small.

One addition worth flagging: `reorder_skills` floats skills the posting asked
for to the front of their line. It is off by default and it only reorders —
a skill the posting wants but you do not have cannot appear, because it is not
in your store.

Still to verify: how it actually looks. That needs Tectonic, which is Phase 5.

## Phase 5 — Compilation

Files: `src/resume_tailor/compile.py`, `tests/test_compile.py`.

**Getting Tectonic.** It was not installed, and `winget` has no `tectonic`
package on this machine — the install docs list Homebrew, MacPorts, the Linux
shell script, conda, and direct download, and the Windows path in practice is
the direct download. The current release is 0.17.0, and the
`x86_64-pc-windows-msvc` build now sits in a gitignored `.tools/` next to the
repo. `find_tectonic` looks in `RESUME_TAILOR_TECTONIC`, then `PATH`, then
`.tools/`, and otherwise raises a message that says how to install it rather
than an OSError.

**Cleanup is structural.** Compilation happens inside a
`TemporaryDirectory`; the `.tex` goes in, the PDF bytes come out, and the
directory is deleted on the way out regardless of success or failure. There is
no cleanup path that can be skipped by an early return, and no aux or log file
can appear next to your resume. A test asserts the working directory is empty
afterwards. `--untrusted` is passed, which disables shell-escape.

**Error surfacing.** `_summarize` pulls the real error lines out of the output
and, when TeX reports `l.<n>`, prints that line of the *generated* `.tex`
alongside it — which is what you need, since the bug is usually in the template
or the escaper rather than in anything you wrote. Tectonic's closing "the TeX
engine had an unrecoverable error" summary is dropped as noise because the
specific error above it is the useful one. A missing package gets its own hint,
since first-run failures are usually the package fetch. If nothing parses, it
falls back to the last few non-empty lines instead of dumping the whole log.

Verified end to end: the Phase 4 template compiles to a real PDF (first run
took ~50s while Tectonic downloaded packages; cached runs are fast), and a
deliberately broken document produces "Undefined control sequence" with the
offending line rather than a wall of log.

## Phase 6 — CLI

Files: `src/resume_tailor/cli.py`, `tests/test_cli.py`.

```
resume-tailor --jd jd.txt --out resume.pdf [--max-bullets N] [--dry-run]
cat jd.txt | resume-tailor --dry-run
```

Beyond the spec's flags: `--max-projects` and `--max-bullets-per-project` (the
budget is three numbers, not one), `--llm-rank` to opt into the model reorder,
`--reorder-skills`, `--projects` to point at a different store, `--model`,
`--tectonic`, and `--emit-tex` to keep the generated `.tex` for debugging.

**`--dry-run`** prints the parsed posting, then each selected entry with its
score, each chosen bullet with its own score and the terms it matched, and then
what was left out with the reason — "outscored" versus "no bullets written yet",
which are very different problems. That last distinction is the one that catches
a half-filled `projects.yaml` before it costs you an application.

**Failures.** Every expected failure is a `ResumeTailorError` subclass, and
`main` converts them to `click.ClickException`, so a missing store, a malformed
entry, an empty posting, a bad budget, or a missing Tectonic all exit with one
actionable line and no traceback. Tests assert on that directly, including that
"Traceback" never appears. Tectonic is located *before* the model is called, so
a missing binary does not cost an API call to discover.

`make_client` is a module-level indirection purely so tests can substitute a
scripted fake; the CLI suite runs the full pipeline, PDF included, with no
network access.

**Visual check.** Rendered a preview from fixture data (`out/preview.pdf`, not
committed). Text extracts in clean reading order, which is the thing that
matters for ATS parsing, and `C#`, `C++`, and `40%` all survive escaping.

One layout fix it surfaced: the heading uses `organization` when set and `name`
otherwise, which silently dropped "Gas Valve Calibration System" from the RHV
entry. That entry no longer sets `organization`, with a comment saying why. Also
removed a `subtitle` value that `build_context` computed but the template never
used.
