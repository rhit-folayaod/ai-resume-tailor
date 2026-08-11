"""Parse a pasted or uploaded resume into a `ResumeStore` draft.

Hard rule: extract only what is written in the resume. Do not invent jobs,
bullets, skills, or degrees. Downstream still persists only after the user
reviews and saves (or explicitly applies the import).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from pydantic import ValidationError

from .errors import LLMError, ResumeTailorError
from .llm import LLMClient
from .models import ResumeStore, slugify

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

# PDF extractors often emit Latin ligatures that confuse models / terminals.
_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "-",
    }
)

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
can be the role title or a short label. role must be the job title (never empty).
- For project entries without an employer, organization may be "".
- dates must be an object {"start": string, "end": string}. Use the resume's \
wording (e.g. "Summer 2025", "2024", "present"). If only one date is given, \
use it for both start and end.
- skills MUST be an array of objects: \
[{"category": "Languages & Technologies", "items": ["Python", "Java"]}, ...]. \
Never return a bare string list or a dict-of-lists for skills.
- Include every skill group on the resume (Languages, Tools, etc.).
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


def normalize_resume_text(resume_text: str) -> str:
    """Clean PDF/OCR quirks before sending text to the model."""

    text = unicodedata.normalize("NFKC", resume_text or "")
    text = text.translate(_LIGATURES)
    text = text.replace("\x00", "")
    return "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()


def parse_resume_text(resume_text: str, client: LLMClient, attempts: int = 3) -> ResumeStore:
    """Turn raw resume text into a validated `ResumeStore` draft."""

    text = normalize_resume_text(resume_text)
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
            payload = _coerce_store_payload(payload)
            return ResumeStore.model_validate(payload)
        except (ValueError, ValidationError, TypeError) as exc:
            last_error = _short_validation_error(exc)
            prompt = (
                f"{user}\n\n"
                "Your previous response was rejected. Fix it and return only valid "
                "JSON matching the schema. skills MUST be "
                '[{"category": string, "items": [string]}, ...]. '
                "Every project needs non-empty name, role, and dates.start/dates.end.\n\n"
                f"Error:\n{last_error}"
            )
            if attempt == attempts - 1:
                break
    raise ResumeTailorError(
        "could not parse that resume into the editor after several tries. "
        "Paste the resume text instead of uploading, or simplify the PDF. "
        f"({last_error})"
    )


def _short_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for error in exc.errors()[:8]:
            loc = ".".join(str(part) for part in error["loc"]) or "(root)"
            parts.append(f"{loc}: {error['msg']}")
        return "; ".join(parts)
    text = str(exc).strip()
    return text if len(text) <= 400 else text[:400] + "…"


def _coerce_store_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Repair common model shape mistakes before Pydantic validation."""

    profile = payload.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    payload["profile"] = {
        "name": str(profile.get("name") or "Your Name").strip() or "Your Name",
        "phone": str(profile.get("phone") or "").strip(),
        "email": str(profile.get("email") or "").strip(),
        "links": _as_str_list(profile.get("links")),
    }

    payload["education"] = _coerce_education(payload.get("education"))
    payload["skills"] = _coerce_skills(payload.get("skills"))
    payload["projects"] = _coerce_projects(payload.get("projects"))
    payload["leadership"] = _as_str_list(payload.get("leadership"))
    _assign_unique_ids(payload)
    return payload


def _coerce_education(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        school = str(entry.get("school") or entry.get("institution") or "").strip()
        degree = str(entry.get("degree") or entry.get("program") or "").strip()
        if not school and not degree:
            continue
        out.append(
            {
                "school": school or "School",
                "location": str(entry.get("location") or "").strip(),
                "degree": degree or "Degree",
                "graduation": str(
                    entry.get("graduation") or entry.get("dates") or entry.get("year") or ""
                ).strip(),
                "coursework": _as_str_list(
                    entry.get("coursework") or entry.get("courses") or entry.get("relevant_coursework")
                ),
            }
        )
    return out


def _coerce_skills(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        # {"Languages": ["Python"], "Tools": ["Git"]} or {"category": "...", "items": [...]}
        if "category" in raw and ("items" in raw or "skills" in raw):
            items = _as_str_list(raw.get("items") or raw.get("skills"))
            category = str(raw.get("category") or "Skills").strip() or "Skills"
            return [{"category": category, "items": items}] if items else []
        groups = []
        for key, value in raw.items():
            items = _as_str_list(value)
            if items:
                groups.append({"category": str(key).strip() or "Skills", "items": items})
        return groups
    if isinstance(raw, list):
        if not raw:
            return []
        if all(isinstance(item, str) for item in raw):
            items = _as_str_list(raw)
            return [{"category": "Skills", "items": items}] if items else []
        groups = []
        flat: list[str] = []
        for item in raw:
            if isinstance(item, str):
                if item.strip():
                    flat.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            category = str(
                item.get("category") or item.get("name") or item.get("group") or "Skills"
            ).strip()
            items = _as_str_list(
                item.get("items") or item.get("skills") or item.get("technologies") or item.get("list")
            )
            if items:
                groups.append({"category": category or "Skills", "items": items})
        if flat:
            groups.append({"category": "Skills", "items": flat})
        return groups
    if isinstance(raw, str) and raw.strip():
        parts = [part.strip() for part in re.split(r"[,;|/]", raw) if part.strip()]
        return [{"category": "Skills", "items": parts}] if parts else []
    return []


def _coerce_projects(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(
            entry.get("name") or entry.get("title") or entry.get("project") or ""
        ).strip()
        role = str(entry.get("role") or entry.get("position") or "").strip()
        # Only fall back to title for role when name came from somewhere else.
        if not role and entry.get("title") and entry.get("name"):
            role = str(entry.get("title") or "").strip()
        organization = str(
            entry.get("organization")
            or entry.get("company")
            or entry.get("employer")
            or ""
        ).strip()
        if not name:
            name = organization or "Untitled entry"
        if not role:
            role = "contributor"
        section = str(entry.get("section") or "").strip().lower()
        if section not in {"experience", "project"}:
            section = "experience" if organization else "project"
        dates = _coerce_dates(entry.get("dates") or entry.get("date") or entry.get("period"))
        bullets = _coerce_bullets(entry.get("bullets") or entry.get("responsibilities"))
        out.append(
            {
                "id": str(entry.get("id") or "").strip(),
                "name": name,
                "role": role,
                "organization": organization,
                "location": str(entry.get("location") or "").strip(),
                "section": section,
                "dates": dates,
                "technologies": _as_str_list(
                    entry.get("technologies") or entry.get("tech") or entry.get("stack")
                ),
                "domains": [],
                "keywords": [],
                "bullets": bullets,
                "always_include": bool(entry.get("always_include") or False),
            }
        )
    return out


def _coerce_dates(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        start = str(raw.get("start") or raw.get("from") or raw.get("begin") or "").strip()
        end = str(raw.get("end") or raw.get("to") or raw.get("until") or "").strip()
        if start and not end:
            end = start
        if end and not start:
            start = end
        if start and end:
            return {"start": start, "end": end}
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        for sep in ("–", "—", "-", " to ", " until "):
            if sep in text:
                left, right = text.split(sep, 1)
                start, end = left.strip(), right.strip()
                if start and end:
                    return {"start": start, "end": end}
        return {"start": text, "end": text}
    return {"start": "n/a", "end": "n/a"}


def _coerce_bullets(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            text = " ".join(item.split())
            if text:
                out.append(text)
        elif isinstance(item, dict):
            text = " ".join(str(item.get("text") or item.get("bullet") or "").split())
            if text:
                out.append(text)
    return out


def _as_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [part.strip() for part in re.split(r"[,;|/]", raw) if part.strip()]
        return parts
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                text = str(item.get("name") or item.get("text") or item.get("skill") or "").strip()
                if text:
                    out.append(text)
        return out
    return []


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


def _parse_json(raw: str) -> object:
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response was not valid JSON: {exc}") from exc
