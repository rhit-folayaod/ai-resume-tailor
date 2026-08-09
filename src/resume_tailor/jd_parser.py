"""Job description parsing.

The model's only job here is extraction: pull skills the posting actually names
into a structured form. It is told not to infer, not to expand abbreviations
into related technologies, and not to add anything a reader could not point to
in the text. Nothing it returns is ever printed on the resume — the parse is
used only to score content that already exists in `projects.yaml`.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from .errors import ResumeTailorError
from .llm import LLMClient, request_validated_json
from .models import StrictModel

SYSTEM_PROMPT = """\
You are a precise information extractor. You read a job posting and return \
structured JSON describing what the posting itself says.

Rules:
- Extract ONLY what is explicitly stated in the posting. Do not infer, expand, \
or add related technologies. If the posting says "Python", do not add "Django". \
If it says "cloud", do not add "AWS".
- Copy skill names as the posting writes them (keep "C++", "C#", "CI/CD" as-is).
- A skill is "required" only if the posting frames it as a requirement, a \
must-have, or a core responsibility. Everything framed as nice-to-have, \
preferred, bonus, or plus is "preferred".
- Never repeat the same skill in both lists.
- If the posting does not state something, use an empty list or the string \
"unspecified". Do not guess.
- Return only JSON. No prose, no code fences.
"""

USER_TEMPLATE = """\
Extract the following fields from the job posting below.

JSON schema:
{{
  "required_skills": [string],   // skills, tools, or technologies the posting requires
  "preferred_skills": [string],  // skills the posting lists as preferred or nice-to-have
  "role_flavor": string,         // one short phrase characterizing the work, e.g.
                                 // "SWE-heavy backend" or "customer-facing solutions engineering"
  "seniority": string            // e.g. "intern", "new grad", "senior", or "unspecified"
}}

Job posting:
---
{jd_text}
---
"""


class ParsedJobDescription(StrictModel):
    """What a posting asks for, as stated by the posting."""

    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    role_flavor: str = "unspecified"
    seniority: str = "unspecified"

    @field_validator("required_skills", "preferred_skills")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for value in values:
            text = " ".join(value.split())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    @field_validator("role_flavor", "seniority")
    @classmethod
    def _default_unspecified(cls, value: str) -> str:
        return " ".join(value.split()) or "unspecified"

    @property
    def all_skills(self) -> list[str]:
        return self.required_skills + self.preferred_skills


def parse_job_description(jd_text: str, client: LLMClient) -> ParsedJobDescription:
    """Turn raw posting text into a validated `ParsedJobDescription`."""

    text = jd_text.strip()
    if not text:
        raise ResumeTailorError("the job description is empty; nothing to parse.")
    if len(text) < 40:
        raise ResumeTailorError(
            "the job description is only "
            f"{len(text)} characters. Paste the full posting so the ranking has "
            "something to work with."
        )

    return request_validated_json(
        client=client,
        system=SYSTEM_PROMPT,
        user=USER_TEMPLATE.format(jd_text=text),
        schema=ParsedJobDescription,
    )
