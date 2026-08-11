# resume-tailor

A command line tool that tailors a resume to a specific job posting, then
compiles it to a PDF with LaTeX.

It does not write your resume. You write every bullet yourself, once, in
`projects.yaml`. Given a job description, the tool decides which of those
bullets belong on this particular application and in what order, fills a LaTeX
template with them, and compiles it.

```
uv run resume-tailor serve                              # browser interface (local)
uv run resume-tailor --jd posting.txt --out resume.pdf  # command line
```

For phone / friends access, deploy the same app to Fly.io with Supabase Auth
(see [Hosted mode for friends](#hosted-mode-for-friends)). Do not put the
Tectonic pipeline on Vercel — serverless cannot run it reliably.

## The no-fabrication guarantee

The tool cannot put a sentence on your resume that you did not write. Not
"is instructed not to" — cannot. That is a property of how the pipeline is
built, and it is worth describing precisely, because the whole design bends
around it.

**The store is the only source of text.** Every bullet, technology, school, and
skill originates in your content store (`projects.yaml` locally, or your
per-user row in Supabase when hosted). The renderer reads from it directly.

**There is exactly one place a model can speak into the process.** All model
access goes through a single method, `LLMClient.complete_json(system, user)`
(`llm.py`). There is no other network call and no other way for generated text
to enter. Two callers use it:

1. **The job description parser** (`jd_parser.py`) turns the posting into
   required skills, preferred skills, a role characterization, and a seniority.
   None of this is ever printed. It exists only to produce terms to score your
   content against. A hallucinated skill here can change which of your real
   bullets get picked; it cannot change what any of them say.

2. **The optional rerank** (`ranking.py`) reorders a shortlist. Its response
   schema is `{"project_id": str, "bullet_index": int}` with extra keys
   forbidden. There is no text field. Bullet text is looked up from the store
   *after* the call returns, so text the model emits is not merely rejected —
   it is never read. A model that tries to improve a bullet gets a validation
   error instead.

**The candidate set is frozen before the call.** Selection scores and shortlists
deterministically first; only then is the model contacted, and only with the
resulting list. Every reference in the response is checked against that frozen
set. A reference to a project or bullet index that is not in it fails validation,
which re-prompts once with the specific bad id and then raises rather than
silently dropping.

**The rerank is off by default.** Deterministic scoring runs unless you pass
`--llm-rank`, so the default path involves no model at all beyond parsing the
posting.

**Every boundary is schema-validated.** The store, the parsed posting, and the
rerank response are Pydantic models that reject unknown keys. Malformed model
output produces a clear validation error, not a quietly wrong resume.

What the tool *does* change: which entries appear, which of their bullets
appear, what order they are in, and — with `--reorder-skills` — the order of
skills within their line. Reordering is not rewriting. If a posting wants a
skill you do not have, it does not appear, because it is not in your store.

## Setup

Requires Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and Tectonic.

```
git clone https://github.com/rhit-folayaod/ai-resume-tailor
cd ai-resume-tailor
uv sync
```

### Tectonic

Tectonic is a self-contained LaTeX engine: one binary that downloads only the
packages a document actually needs, rather than a multi-gigabyte TeX Live
install. The first compile takes a while as it fetches packages; later ones are
fast.

Install it whichever way fits your platform ([full
instructions](https://tectonic-typesetting.github.io/book/latest/installation/)):

```
brew install tectonic                     # macOS
curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh
conda install -c conda-forge tectonic     # any platform, no admin rights
```

On Windows there is no reliable package manager entry, so download the
`x86_64-pc-windows-msvc` zip from the
[releases page](https://github.com/tectonic-typesetting/tectonic/releases) and
unpack `tectonic.exe`. Put it on your `PATH`, or drop it in a `.tools/` folder
inside this repo, or point at it explicitly:

```
$env:RESUME_TAILOR_TECTONIC = "C:\path\to\tectonic.exe"
```

The tool looks in `RESUME_TAILOR_TECTONIC`, then `PATH`, then `.tools/`.

### Model access

Any OpenAI-compatible endpoint works.

```
$env:OPENAI_API_KEY = "sk-..."
$env:RESUME_TAILOR_MODEL = "gpt-4o-mini"   # optional, this is the default
$env:OPENAI_BASE_URL = "http://localhost:11434/v1"   # optional: Ollama, OpenRouter, etc.
```

## The browser interface

```
uv run resume-tailor serve
```

Serves on `http://127.0.0.1:8765` and opens a browser. Two tabs:

**Tailor.** Give it a posting three ways — paste a URL and hit Fetch, drop a PDF
anywhere on the page, or paste the text. Set the bullet budget, and it shows
every entry it picked with the score behind the pick, each chosen bullet with the
requirements it matched, and what was left out and why. Then Build PDF compiles
and previews inline, with download links for the PDF and the generated `.tex`.

URL fetching is best-effort. LinkedIn, Workday, and Greenhouse block automated
requests or render postings with JavaScript, and when that happens the page says
so and points you at the PDF or paste route rather than silently ranking against
half a page of navigation text.

**Content.** A full editor for `projects.yaml`: profile, skill groups, education,
every experience and project entry with its bullets, and leadership lines. Skills
and tags are chips you add with Enter and remove with the ×; bullets can be
added, reordered, and deleted. Save validates before writing and rewrites the
file atomically, keeping the previous version as `projects.yaml.bak`.

**Import from resume.** On the Content tab, drop a resume PDF (or paste text).
The model extracts profile, education, skills, experience, projects, and
leadership into the editor as a draft — only claims present on the page, never
invented bullets. Review the fields, then Save. That uses the same validated
`ResumeStore` path as typing by hand.

Saving from the UI does not preserve comments you have hand-written in the YAML.
The header block is regenerated; inline notes are not.

Nothing about the UI changes the guarantee above. Text only enters the store
through a validated `ResumeStore` (`PUT /api/store` after you review an import
or type by hand).

## Hosted mode for friends

Local `serve` still uses `projects.yaml` on disk when `SUPABASE_URL` is unset.
With Supabase configured, the same UI becomes invite-only and multi-user: each
friend gets their own content store in Postgres, signs in with a magic link, and
hits daily caps so one person cannot burn the shared OpenAI key.

Stack (locked):

| Piece | Choice |
| --- | --- |
| App | Fly.io Docker image — FastAPI + UI + Tectonic |
| Auth + DB | Supabase Auth (magic link) + Postgres |
| Access | `allowed_users` email allowlist |
| Model | Shared `OPENAI_API_KEY`, default `gpt-4o-mini` |
| Limits | 30 JD parses / 20 PDF compiles per user per UTC day (tunable) |

Vercel is intentionally not used for the tailor pipeline. Tectonic needs a real
process, disk, and enough time for package downloads.

### 1. Supabase

1. Create a free Supabase project.
2. Run [`supabase/migrations/001_hosting.sql`](supabase/migrations/001_hosting.sql)
   in the SQL editor.
3. Invite yourself (and friends later) — emails are lowercased:

```sql
insert into public.allowed_users (email, note)
values ('you@example.com', 'owner');
```

4. Auth → URL configuration: add your Fly URL (e.g.
   `https://ai-resume-tailor.fly.dev`) to Redirect URLs.
5. Copy from Project Settings → API:
   - Project URL → `SUPABASE_URL`
   - `anon` `public` key → `SUPABASE_ANON_KEY`
   - `service_role` key → `SUPABASE_SERVICE_ROLE_KEY` (server only)
6. Project Settings → API → JWT Secret → `SUPABASE_JWT_SECRET`
7. **Set up custom SMTP** — Authentication → Emails → SMTP Settings. This is
   required, not optional. Supabase's built-in sender only delivers to addresses
   that belong to your Supabase organization, and caps you at ~2 emails/hour.
   Without it, magic links to anyone else silently never arrive, and your own
   address starts failing with `over_email_send_rate_limit` after two tries.
   Any transactional provider works (Resend, SendGrid, Postmark, Mailgun); plug
   its host/port/user/password in, and set a sender address on a domain you have
   verified with that provider.

### 2. Fly.io

```
fly auth login
# App name in fly.toml is ai-resume-tailor (ord). Create only if it does not exist yet:
# fly apps create ai-resume-tailor
fly volumes create tectonic_cache --region ord --size 1
fly secrets set \
  OPENAI_API_KEY=sk-... \
  SUPABASE_URL=https://YOUR_PROJECT.supabase.co \
  SUPABASE_ANON_KEY=... \
  SUPABASE_SERVICE_ROLE_KEY=... \
  SUPABASE_JWT_SECRET=... \
  RESUME_TAILOR_MODEL=gpt-4o-mini
fly deploy
```

If deploy fails with a mount error, the volume is missing — create it in the same
region as `primary_region` in `fly.toml` (`ord`), then deploy again.

Optional caps:

```
fly secrets set RESUME_TAILOR_DAILY_PARSE_LIMIT=30 RESUME_TAILOR_DAILY_COMPILE_LIMIT=20
```

Open `https://<app>.fly.dev` on your phone, request a magic link to an allowlisted
email, then fill the Content tab (first login seeds an empty store). You can also
drop a resume PDF on Content → Import to populate it.

To push a local `projects.yaml` into a hosted user's store (they must have signed
in once so Auth has a user row):

```
uv run resume-tailor seed-store --email you@example.com --projects projects.yaml
```

### 3. Invite a friend

```sql
insert into public.allowed_users (email, note)
values ('friend@example.com', 'classmate');
```

Tell them the Fly URL. They sign in with that email. Their bullets never mix with
yours — stores are keyed by `auth.users.id`.

See [`.env.example`](.env.example) for every variable. Local CLI usage is
unchanged and does not need Supabase.

## Command line usage

`uv sync` installs the `resume-tailor` command into the project environment; run
it with `uv run resume-tailor`, or activate `.venv` and drop the prefix.

Check what would be selected before trusting it:

```
uv run resume-tailor --jd posting.txt --dry-run
```

That prints the parsed posting, each selected entry with its score, each chosen
bullet with its score and the terms it matched, and what was left out — marked
either "outscored" or "no bullets written yet", which are different problems.

Produce the PDF:

```
uv run resume-tailor --jd posting.txt --out resume.pdf
cat posting.txt | uv run resume-tailor --out resume.pdf
```

| Option | Purpose |
| --- | --- |
| `--jd PATH` | The posting. Omit to read stdin. |
| `--out PATH` | Where to write the PDF. Default `resume.pdf`. |
| `--projects PATH` | Content store to use. Default `projects.yaml`. |
| `--max-bullets N` | Total bullet budget. Default 12. |
| `--max-projects N` | Entries to include. Default 6. |
| `--max-bullets-per-project N` | Cap per entry, so one project cannot fill the page. Default 3. |
| `--dry-run` | Print the selection and scores, compile nothing. |
| `--llm-rank` | Let the model reorder the shortlist. It cannot add to it. |
| `--reorder-skills` | Move skills the posting asks for to the front of their line. |
| `--emit-tex PATH` | Also write the generated `.tex`, for debugging the template. |
| `--model NAME` | Override the model. |
| `--tectonic PATH` | Override the Tectonic binary. |

`resume-tailor serve` takes `--projects`, `--host`, `--port`, `--model`,
`--tectonic`, and `--no-open`.

## Maintaining projects.yaml

This is the part that determines whether the output is any good. The tool can
only choose among sentences you have already written, so a thin store produces a
thin resume no matter how good the ranking is.

The Content tab of `resume-tailor serve` edits all of this, which is usually
easier than editing YAML by hand. Each entry looks like this:

```yaml
- id: rhv-gas-valve
  name: RHV - Gas Valve Calibration System
  role: Software Engineer Intern
  location: Terre Haute, IN
  section: experience          # `experience` or `project`; decides where it renders
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

Guidance that matters in practice:

- **Write more bullets than fit.** Selection only helps if there is something to
  select from. Eight bullets on a project you care about is not excessive; the
  budget decides how many appear.
- **Each bullet must stand alone.** They get reordered and printed
  independently, so one that only makes sense after the previous one will read
  strangely.
- **`technologies` prints; `domains` and `keywords` do not.** The latter two are
  matching metadata. Use them for the vocabulary a posting might use that your
  bullets do not — "customer-facing", "CI/CD", "real-time". They are what let an
  entry match a posting whose wording differs from yours.
- **`always_include: true`** forces an entry through selection regardless of
  score. Use sparingly.
- **`organization` overrides the heading.** If set, the heading shows it instead
  of `name`. Leave it out when the entry reads better under its full name.
- The loader validates on every run and rejects unknown keys, so a typo like
  `tecnologies:` is a loud error naming the entry, not silently missing content.

### Before making this repo public

The `projects.yaml` committed here is a sanitized placeholder: real structure,
empty `bullets`, no phone number. Keep it that way. Maintain your real content
in a copy the repo does not track, and point at it:

```
uv run resume-tailor --jd posting.txt --projects ~/private/projects.yaml
```

`.gitignore` already excludes `*.pdf`, so generated resumes and your source
resume stay out of the repo.

## Editing the template

`src/resume_tailor/templates/resume.tex` is plain LaTeX with Jinja placeholders,
single column, no graphics, in reading order for ATS parsers. Two things to know
before editing:

- Jinja uses LaTeX-friendly delimiters here (`\VAR{}`, `\BLOCK{}`), so braces and
  backslashes still mean what they mean in LaTeX. A line starting with two
  percent signs is a Jinja line statement, and the delimiters are live inside
  LaTeX comments too.
- Injected values are escaped automatically by the environment's `finalize`
  hook. You do not need an escape filter, and you cannot forget one.

`--emit-tex out/resume.tex` writes the generated source when you are chasing a
compile error.

## Development

```
uv run pytest
```

105+ tests, no network access. An autouse fixture makes any test that tries to
construct a real LLM client fail, so the suite cannot start making live calls by
accident. The end-to-end tests skip themselves if Tectonic is not installed.
Hosted-mode tests fake JWTs and Supabase HTTP.

The browser UI is plain HTML, CSS, and DOM JavaScript under
`src/resume_tailor/webui/` — no build step, no bundler, no CDN. Edit the files
and reload the page.
