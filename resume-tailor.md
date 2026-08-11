## Context for the agent

I'm building a personal CLI tool that tailors my resume to a specific job description.
It reads a structured store of my projects/skills, parses a pasted job description,
ranks and selects the most relevant content, fills a LaTeX resume template, and
compiles it to a PDF.

**Hard constraint, most important thing in this whole project: the tool must never
invent content.** It selects, ranks, and reorders bullets I've already written — it
never generates a new claim, technology, or outcome I didn't supply. This is a resume;
a hallucinated accomplishment is a real problem, not a cosmetic one. Every prompt to
the LLM in this pipeline must be constrained to selection/ranking, never generation of
new factual content. Bake this into the design, don't just mention it in a comment.

Stack:
- Python 3.11+, `uv` for dependency management.
- **Tectonic** for LaTeX compilation, not a full TeX Live install — it's a single
  self-contained binary that pulls in only the packages the document needs. Verify
  installation instructions for the current version before writing setup docs.
- Pydantic for schema validation on every structured data boundary (project store, JD
  parse output, selection output) — if the LLM returns malformed JSON, I want a clear
  validation error, not a silent bad resume.
- Jinja2 for the LaTeX template, using a custom delimiter set that doesn't collide with
  LaTeX's own `{ }` and `\` syntax (this is a well-known gotcha — look up the standard
  Jinja2-for-LaTeX delimiter convention before implementing).
- Click for the CLI.

Check in with me at the end of each phase.

## Phase 1 — Project/skill schema and data store

Design a Pydantic schema for a single project entry:
- `name`, `role` (e.g. "intern", "TA", "personal project")
- `dates` (start/end, allow "present")
- `technologies: list[str]`
- `domains: list[str]` (e.g. "backend", "hardware", "data", "customer-facing")
- `bullets: list[str]` — pre-written by me, in my own words, each one a complete
  accomplishment statement. The tool never edits the wording of a bullet, only
  selects which ones to include and in what order.

Store all entries in a single `projects.yaml` (human-editable, this is content I'll
maintain by hand). Write a loader that validates the file against the schema on load
and gives a clear error pointing at the specific entry if something's malformed.

Seed the file with placeholder entries (empty bullets, just structure) for: the RHV gas
valve calibration internship, the current Emerson/NI internship, the jetpack DAQ demo,
TA work, and the Ask Rose learning advisor role — so I can see the shape and fill in
real content myself.

## Phase 2 — Job description parser

A function that takes raw pasted JD text and an LLM call, returns a validated Pydantic
object:
- `required_skills: list[str]`
- `preferred_skills: list[str]`
- `role_flavor: str` — short characterization, e.g. "SWE-heavy backend" vs
  "customer-facing solutions engineering"
- `seniority: str`

The prompt to the LLM here must instruct it to **extract only what's stated in the JD**
— no inferring skills the posting doesn't mention. Force JSON output matching the
schema; validate on the way back and retry once with the validation error appended to
the prompt if it fails.

## Phase 3 — Matching and ranking engine

This is the core engineering, not a single LLM call:

- Score each project against the parsed JD: keyword/technology overlap first (cheap,
  deterministic, easy to unit test), then optionally an embedding-similarity pass
  across `domains` + `role_flavor` for semantic overlap keyword matching misses.
- Produce a ranked list of projects, and within each selected project, a ranked list of
  its bullets — not just project-level, since a project might have eight bullets and
  I only want the two most relevant to this specific posting.
- Given a target bullet count or page budget, select the top-N across the ranked
  output. Make the budget a parameter, not a hardcoded number.
- **No LLM call in this phase should be able to add a bullet that isn't already in
  `projects.yaml`.** The ranking can use an LLM to help order/score, but the candidate
  set is fixed before that call happens and the output is validated to be a subset of
  it — reject and retry if the model returns anything not in the candidate set.
- Write this so the deterministic scoring logic is unit-testable without hitting an
  LLM at all.

## Phase 4 — LaTeX template

- Write ONE resume template by hand, in valid LaTeX, styled the way I want my resume
  to look — treat this as content I'm providing, not something the agent invents.
  Use a clean, ATS-reasonable single-column layout, moderate margins. I'll iterate on
  the actual visual design with you once the structure works.
- Use Jinja2 to fill it: project names, dates, selected bullets, skills section.
- Write a robust LaTeX special-character escaper for injected content — `&`, `%`, `#`,
  `_`, `$`, `{`, `}`, `~`, `^`, and `\`, plus handle `C++` and `C#` correctly since
  those appear in my actual skill list and are the kind of thing that silently breaks
  a naive escaper. Unit test the escaper directly with these exact strings.

## Phase 5 — Compilation

- Wrap Tectonic invocation in a function that takes the filled `.tex` string, compiles
  it, and returns the PDF bytes.
- On compile failure, parse enough of Tectonic's output to surface a clear, specific
  error (which line/package/macro) rather than dumping the raw log. This matters
  because template + escaping bugs will show up here first.
- Clean up intermediate build files (aux, log) after each run, whether it succeeds or
  fails.

## Phase 6 — CLI

`resume-tailor --jd path/to/jd.txt --out resume.pdf [--max-bullets N]`

- Reads the JD file (or accepts piped stdin), runs the pipeline end to end, writes the
  PDF.
- Add `--dry-run` that prints the selected projects/bullets and match scores without
  compiling — this is what I'll use to sanity-check selection before trusting it.
- Clear error messages at every stage; this is a tool I'll run under time pressure
  before an application deadline, so failures need to be immediately actionable, not
  a stack trace.

## Phase 7 — Tests

`pytest`, no live LLM calls in the suite — mock the LLM boundary:
- Schema validation rejects malformed `projects.yaml` entries with a clear error.
- LaTeX escaper handles every special character plus `C++`/`C#` correctly.
- Ranking engine: given a fixed project set and a fixed parsed JD, output is
  deterministic and only includes bullets that exist in the input.
- Ranking engine rejects/retries correctly when a mocked LLM response includes content
  outside the candidate set.
- End-to-end: a fixed `projects.yaml` + fixed JD produces a PDF that compiles
  successfully.

## Phase 8 — Documentation

- `README.md`: what it does, the no-fabrication guarantee and how it's enforced
  architecturally (this is the interesting part, explain it precisely), setup
  (including Tectonic install), usage examples, and how to maintain `projects.yaml`.
- Don't claim capabilities it doesn't have. No badge walls, no emoji headers.
- Note clearly that `projects.yaml` as shipped in the repo (if I make this public)
  should be the placeholder/sanitized version, n