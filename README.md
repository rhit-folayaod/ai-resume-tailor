# resume-tailor

A CLI and browser app that tailors a resume to a specific job posting, then
compiles it to PDF with LaTeX.

It does not write your resume. You write every bullet yourself (by hand or by
importing a resume you already wrote). Given a job description, the tool decides
which of those bullets belong on this application and in what order, fills a
LaTeX template, and compiles it.

```
uv run resume-tailor serve                              # browser UI (local)
uv run resume-tailor --jd posting.txt --out resume.pdf  # CLI
```

**Live (invite-only):** [https://ai-resume-tailor.fly.dev](https://ai-resume-tailor.fly.dev)
— Fly.io + Supabase Auth. Do not put the Tectonic pipeline on Vercel; serverless
cannot run it reliably.

## The no-fabrication guarantee

The tool cannot put a sentence on your resume that did not come from your content
store. Not "is instructed not to" — cannot. That is a property of how the
pipeline is built.

**The store is the only source of printed text.** Every bullet, technology,
school, and skill originates in your content store (`projects.yaml` locally, or
your per-user row in Supabase when hosted). The LaTeX renderer reads from it
directly.

**All model access goes through one method.**
`LLMClient.complete_json(system, user)` in `llm.py` is the only network path for
model output. Three callers use it:

1. **Job description parser** (`jd_parser.py`) — extracts required/preferred
   skills and role flavor from the posting. None of this is printed; it only
   produces terms to score your content against.
2. **Optional rerank** (`ranking.py`) — reorders a shortlist. The response schema
   is `{"project_id": str, "bullet_index": int}` with no text field. Bullet text
   is looked up from the store *after* the call, so model prose is never read.
3. **Resume import** (`resume_parser.py`) — maps an uploaded/pasted resume into a
   `ResumeStore` draft. It is instructed to copy only what is on the page (no
   invented jobs or bullets). The draft loads into the Content editor; nothing is
   persisted until you review and Save through the same validated store path.

**The candidate set is frozen before rerank.** Selection scores and shortlists
deterministically first; only then is the model contacted. Every reference in the
response is checked against that frozen set.

**Rerank is off by default.** Deterministic scoring runs unless you pass
`--llm-rank` (or check the UI box).

**Every boundary is schema-validated.** The store, parsed posting, and rerank
response are Pydantic models that reject unknown keys.

What the tool *does* change: which entries appear, which of their bullets appear,
what order they are in, and — with `--reorder-skills` — the order of skills
within their line. If a posting wants a skill you do not have, it does not
appear.

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and Tectonic.

```
git clone https://github.com/rhit-folayaod/ai-resume-tailor
cd ai-resume-tailor
uv sync
```

### Tectonic

Tectonic is a self-contained LaTeX engine: one binary that downloads only the
packages a document needs. The first compile is slow; later ones are fast.

```
brew install tectonic                     # macOS
curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh
conda install -c conda-forge tectonic     # any platform, no admin rights
```

On Windows, download the `x86_64-pc-windows-msvc` zip from the
[releases page](https://github.com/tectonic-typesetting/tectonic/releases), unpack
`tectonic.exe`, and put it on `PATH`, in `.tools/`, or:

```
$env:RESUME_TAILOR_TECTONIC = "C:\path\to\tectonic.exe"
```

The tool looks in `RESUME_TAILOR_TECTONIC`, then `PATH`, then `.tools/`.

### Model access

Any OpenAI-compatible endpoint works.

```
$env:OPENAI_API_KEY = "sk-..."
$env:RESUME_TAILOR_MODEL = "gpt-4o-mini"   # optional; this is the default
$env:OPENAI_BASE_URL = "http://localhost:11434/v1"   # optional: Ollama, etc.
```

## The browser interface

```
uv run resume-tailor serve
```

Serves on `http://127.0.0.1:8765` and opens a browser. Local mode (no
`SUPABASE_URL`) edits `projects.yaml` with no login. Hosted mode shows a sign-in
gate first.

### Tailor tab

Give it a posting three ways — URL + Fetch, drop a PDF, or paste text. Set the
bullet budget; it shows every entry it picked with scores, which requirements
each bullet matched, and what was left out and why. **Build PDF** compiles and
previews inline, with downloads for the PDF and `.tex`.

URL fetching is best-effort. LinkedIn, Workday, and Greenhouse often block
automated fetches or render with JavaScript; use PDF or paste when that happens.

### Content tab

Full editor for the store: profile, skill groups, education, experience/project
entries with bullets, and leadership. Skills and tags are chips; bullets can be
added, reordered, and deleted. **Save** validates, then writes (local: atomic
YAML + `.bak`; hosted: upserts your Postgres row).

**Import from resume.** Drop a PDF or paste text. The model extracts profile,
education, skills, experience, projects, and leadership into the editor as a
draft — only claims present on the page. Review, then Save.

Saving from the UI does not preserve hand-written YAML comments.

## Hosted mode for friends

With Supabase configured, the same UI is invite-only and multi-user: each person
gets their own content store, signs in with a magic link (or the owner admin
password), and hits daily caps so one user cannot burn the shared OpenAI key.

| Piece | Choice |
| --- | --- |
| App | [ai-resume-tailor.fly.dev](https://ai-resume-tailor.fly.dev) — Docker, FastAPI + UI + Tectonic |
| Region | `iad` (see `fly.toml`; volume must match) |
| Auth + DB | Supabase Auth (magic link / PKCE) + Postgres |
| Access | `allowed_users` email allowlist |
| Owner bypass | Optional admin password login (`ADMIN_PASSWORD`, default `glorytothemoon`; set `off` to disable) |
| Model | Shared `OPENAI_API_KEY`, default `gpt-4o-mini` |
| Limits | 30 parses / 20 PDF compiles per user per UTC day (tunable; import counts as a parse) |

### 1. Supabase

1. Create a free Supabase project.
2. Run [`supabase/migrations/001_hosting.sql`](supabase/migrations/001_hosting.sql)
   in the SQL editor.
3. Invite yourself (and friends later) — emails are lowercased:

```sql
insert into public.allowed_users (email, note)
values ('you@example.com', 'owner');
```

4. Authentication → URL configuration:
   - Site URL: `https://ai-resume-tailor.fly.dev`
   - Redirect URLs: `https://ai-resume-tailor.fly.dev` and
     `https://ai-resume-tailor.fly.dev/**`
5. Project Settings → API — copy:
   - Project URL → `SUPABASE_URL`
   - `anon` `public` key → `SUPABASE_ANON_KEY`
   - `service_role` key → `SUPABASE_SERVICE_ROLE_KEY` (server only)
6. Project Settings → API → JWT Secret → `SUPABASE_JWT_SECRET` (optional
   fallback; the server primarily verifies sessions via Supabase Auth
   `GET /auth/v1/user`).
7. **Custom SMTP (required for friends).** Authentication → Emails → SMTP
   Settings. Supabase’s built-in sender only delivers reliably to org-member
   addresses and caps you at roughly two emails/hour
   (`over_email_send_rate_limit`). Use Resend, SendGrid, Postmark, Mailgun, etc.,
   with a verified sender domain.

Magic links use PKCE: request the link and open it in the **same browser** where
you clicked “Send magic link”. A different phone/browser cannot finish the
exchange.

### 2. Fly.io

```
fly auth login
# App name in fly.toml is ai-resume-tailor. Create only if it does not exist yet:
# fly apps create ai-resume-tailor
fly volumes create tectonic_cache --region iad --size 1
fly secrets set \
  OPENAI_API_KEY=sk-... \
  SUPABASE_URL=https://YOUR_PROJECT.supabase.co \
  SUPABASE_ANON_KEY=... \
  SUPABASE_SERVICE_ROLE_KEY=... \
  SUPABASE_JWT_SECRET=... \
  RESUME_TAILOR_MODEL=gpt-4o-mini \
  ADMIN_PASSWORD=glorytothemoon
fly deploy
```

If deploy fails with a mount error, create the volume in the same region as
`primary_region` in `fly.toml` (`iad`), then deploy again.

Optional caps:

```
fly secrets set RESUME_TAILOR_DAILY_PARSE_LIMIT=30 RESUME_TAILOR_DAILY_COMPILE_LIMIT=20
```

To disable the admin password form:

```
fly secrets set ADMIN_PASSWORD=off
```

Open [https://ai-resume-tailor.fly.dev](https://ai-resume-tailor.fly.dev), sign
in (magic link or admin password), then use Content → Import or the editor.
First login seeds an empty store.

Push a local `projects.yaml` into a hosted user’s store (they must have signed
in once so Auth has a user row). Needs Supabase env vars locally:

```
uv run resume-tailor seed-store --email you@example.com --projects projects.yaml
```

### 3. Invite a friend

```sql
insert into public.allowed_users (email, note)
values ('friend@example.com', 'classmate');
```

Tell them the Fly URL. They sign in with that email. Stores are keyed by
`auth.users.id` — content never mixes.

See [`.env.example`](.env.example) for every variable. Local CLI usage does not
need Supabase.

## Command line usage

`uv sync` installs the `resume-tailor` command; run it with `uv run resume-tailor`.

```
uv run resume-tailor --jd posting.txt --dry-run
uv run resume-tailor --jd posting.txt --out resume.pdf
cat posting.txt | uv run resume-tailor --out resume.pdf
```

| Option / command | Purpose |
| --- | --- |
| `--jd PATH` | Posting file. Omit to read stdin. |
| `--out PATH` | PDF output. Default `resume.pdf`. |
| `--projects PATH` | Content store. Default `projects.yaml`. |
| `--max-bullets N` | Total bullet budget. Default 12. |
| `--max-projects N` | Entries to include. Default 6. |
| `--max-bullets-per-project N` | Cap per entry. Default 3. |
| `--dry-run` | Print selection and scores; compile nothing. |
| `--llm-rank` | Let the model reorder the shortlist (cannot add to it). |
| `--reorder-skills` | Float matching skills to the front of their line. |
| `--emit-tex PATH` | Also write the generated `.tex`. |
| `--model NAME` | Override the model. |
| `--tectonic PATH` | Override the Tectonic binary. |
| `serve` | Browser UI (`--host`, `--port`, `--projects`, `--model`, `--tectonic`, `--no-open`). |
| `seed-store` | Upload `--projects` into the hosted store for `--email` (Supabase env required). |

## Maintaining the content store

The ranking can only choose among sentences already in the store, so a thin store
produces a thin resume. Prefer the Content tab (or Import) over hand-editing
YAML.

Each entry looks like this:

```yaml
- id: rhv-gas-valve
  name: RHV - Gas Valve Calibration System
  role: Software Engineer Intern
  location: Terre Haute, IN
  section: experience          # `experience` or `project`
  dates:
    start: Summer 2025
    end: Summer 2025           # or `present`
  technologies: [C#, WinForms, Modbus TCP/IP]
  domains: [hardware, embedded]
  keywords: [multithreading, real-time]
  bullets:
    - Built a multithreaded C# WinForms application serving as the central
      control interface for a six-unit calibration system
```

Guidance:

- **Write more bullets than fit.** Selection only helps if there is something to
  select from.
- **Each bullet must stand alone.** They get reordered independently.
- **`technologies` prints; `domains` and `keywords` do not.** Use the latter for
  matching vocabulary that does not appear in the bullet text.
- **`always_include: true`** forces an entry through selection. Use sparingly.
- **`organization` overrides the heading** when set; omit it when the entry reads
  better under `name`.
- Unknown keys are rejected loudly (e.g. `tecnologies:`).

### Privacy note

The committed `projects.yaml` currently holds real profile content (name, phone,
email, bullets) for local/demo use. Before making the repo public, strip or
replace that file with a sanitized placeholder and keep your real store outside
the repo:

```
uv run resume-tailor --jd posting.txt --projects ~/private/projects.yaml
```

`.gitignore` already excludes `*.pdf`.

## Editing the template

`src/resume_tailor/templates/resume.tex` is plain LaTeX with Jinja placeholders
(`\VAR{}`, `\BLOCK{}`), single column, ATS-friendly. Injected values are escaped
by the environment’s `finalize` hook — you do not need an escape filter.

`--emit-tex out/resume.tex` writes the generated source when debugging compiles.

## Development

```
uv run pytest
```

~116 tests, no network. An autouse fixture fails any test that constructs a real
LLM client. End-to-end compile tests skip if Tectonic is missing. Hosted-mode
tests fake JWTs and Supabase HTTP.

The browser UI is plain HTML/CSS/JS under `src/resume_tailor/webui/` — no build
step. Edit and reload.
