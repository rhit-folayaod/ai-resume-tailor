"""Parse a pasted or uploaded resume into a `ResumeStore` draft.

Hard rule: extract only what is written in the resume. Do not invent jobs,
bullets, skills, or degrees. Downstream still persists only after the user
reviews and saves (or explicitly applies the import).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .errors import LLMError, ResumeTailorError
from .llm import LLMClient
from .models import ResumeStore, slugify

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

SYSTEM_PROMPT = """\
You are a precise resume extractor. You read resume text and return structured \
JSON that mirrors ONLY what the resume itself says.

Rules:
- Extract ONLY facts present in the resume. Never invent employers, titles, \
degrees, skills, dates, or accomplishment bullets.
- Copy bullet text nearly verbatim (fix obvious OCR glitches like a leading \
garbled character). Do not rewrite bullets to sound better.
- Put jobs/internships/TA roles in projects with section "experience". Put \
personal/course/side projects in section "project".
- For experience entries, set organization to the employer when present; name \
can be the role title or a short label. role must be the job title.
- For project entries without an employer, organization may be "".
- dates.start and dates.end are required strings (use the resume's wording, \
e.g. "Summer 2025", "2024", "present"). If only one date is given, use it for \
both start and end.
- skills must be grouped by category when the resume groups them; otherwise use \
sensible category labels that appear on the resume (e.g. "Languages").
- leadership is a list of short strings from leadership/involvement sections.
- id fields: leave empty or omit; the server will slugify names.
- Do not invent domains or keywords; leave those lists empty.
- Return only JSON. No prose, no code fences.
"""

USER_TEMPLATE = """\
Extract the resume below into this JSON shape:

{{
  "profile": {{
    "name": string,
    "phone": string,
    "email": string,
    "links": [string]
  }},
  "education": [
    {{
      "school": string,
      "location": string,
      "degree": string,
      "graduation": string,
      "coursework": [string]
    }}
  ],
  "skills": [
    {{
      "category": string,
      "items": [string]
    }}
  ],
  "projects": [
    {{
      "id": string,
      "name": string,
      "role": string,
      "organization": string,
      "location": string,
      "section": "experience" | "project",
      "dates": {{ "start": string, "end": string }},
      "technologies": [string],
      "domains": [],
      "keywords": [],
      "bullets": [string],
      "always_include": false
    }}
  ],
  "leadership": [string]
}}

Resume text:
---
{resume_text}
---
"""


def parse_resume_text(resume_text: str, client: LLMClient, attempts: int = 2) -> ResumeStore:
    """Turn raw resume text into a validated `ResumeStore` draft."""

    text = resume_text.strip()
    if not text:
        raise ResumeTailorError("the resume is empty; nothing to parse.")
    if len(text) < 80:
        raise ResumeTailorError(
            "the resume is only "
            f"{len(text)} characters. Upload the full PDF or paste the full text."
        )

    user = USER_TEMPLATE.format(resume_text=text)
    prompt = user
    last_error = ""
    for attempt in range(attempts):
        raw = client.complete_json(SYSTEM_PROMPT, prompt)
        try:
            payload = _parse_json(raw)
            if not isinstance(payload, dict):
                raise ValueError("response root must be a JSON object")
            _assign_unique_ids(payload)
            return ResumeStore.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)
            prompt = (
                f"{user}\n\n"
                "Your previous response was rejected. Fix it and return only valid "
                f"JSON matching the schema.\n\nPrevious response:\n{raw}\n\n"
                f"Error:\n{last_error}"
            )
            if attempt == attempts - 1:
                break
    raise LLMError(
        f"the model returned output that failed validation {attempts} times.\n"
        f"Last error:\n{last_error}"
    )


def _assign_unique_ids(payload: dict[str, Any]) -> None:
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return
    used: set[str] = set()
    for entry in projects:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "entry")
        base = str(entry.get("id") or "").strip() or slugify(name)
        candidate = base
        n = 2
        while candidate in used:
            candidate = f"{base}-{n}"
            n += 1
        used.add(candidate)
        entry["id"] = candidate
        entry.setdefault("domains", [])
        entry.setdefault("keywords", [])
        entry.setdefault("technologies", [])
        entry.setdefault("bullets", [])
        entry.setdefault("organization", "")
        entry.setdefault("location", "")
        entry.setdefault("always_include", False)


def _parse_json(raw: str) -> object:
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response was not valid JSON: {exc}") from exc
