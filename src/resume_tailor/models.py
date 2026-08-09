"""Pydantic schemas for the content store.

The store is the single source of truth for every factual claim that can appear
in a generated resume. Nothing downstream may introduce text that did not come
from here; see `resume_tailor.ranking` for how that is enforced.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Section = Literal["experience", "project"]

_PRESENT_VALUES = {"present", "current", "now"}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "entry"


class StrictModel(BaseModel):
    """Base model that rejects unknown keys.

    A typo'd key in `projects.yaml` should be a loud error, not silently
    dropped content.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DateRange(StrictModel):
    start: str
    end: str = "present"

    @field_validator("start", "end")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @property
    def is_current(self) -> bool:
        return self.end.strip().lower() in _PRESENT_VALUES

    @property
    def display(self) -> str:
        end = "Present" if self.is_current else self.end
        if end.lower() == self.start.lower():
            return self.start
        return f"{self.start} \u2013 {end}"


class ProjectEntry(StrictModel):
    """One job, internship, or project.

    `bullets` are written by hand by the resume owner. The tool selects and
    orders them; it never rewrites them.
    """

    id: str = ""
    name: str
    role: str
    organization: str = ""
    location: str = ""
    section: Section = "project"
    dates: DateRange
    technologies: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    always_include: bool = False

    @field_validator("name", "role")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @field_validator("technologies", "domains", "keywords")
    @classmethod
    def _clean_tags(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values if v.strip()]
        return cleaned

    @field_validator("bullets")
    @classmethod
    def _clean_bullets(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for index, value in enumerate(values):
            text = " ".join(value.split())
            if not text:
                raise ValueError(f"bullet {index} is empty; remove it instead")
            cleaned.append(text)
        return cleaned

    @model_validator(mode="after")
    def _default_id(self) -> "ProjectEntry":
        if not self.id:
            object.__setattr__(self, "id", slugify(self.name))
        return self

    @property
    def display_title(self) -> str:
        return self.organization or self.name


class SkillGroup(StrictModel):
    category: str
    items: list[str] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def _clean_items(cls, values: list[str]) -> list[str]:
        return [v.strip() for v in values if v.strip()]


class EducationEntry(StrictModel):
    school: str
    location: str = ""
    degree: str
    graduation: str = ""
    coursework: list[str] = Field(default_factory=list)


class Profile(StrictModel):
    name: str
    phone: str = ""
    email: str = ""
    links: list[str] = Field(default_factory=list)


class ResumeStore(StrictModel):
    """The whole `projects.yaml` file."""

    profile: Profile
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    leadership: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "ResumeStore":
        seen: dict[str, int] = {}
        for index, project in enumerate(self.projects):
            if project.id in seen:
                raise ValueError(
                    f"duplicate project id {project.id!r} "
                    f"(entries {seen[project.id]} and {index}); "
                    "set an explicit unique `id` on one of them"
                )
            seen[project.id] = index
        return self

    def by_id(self, project_id: str) -> ProjectEntry:
        for project in self.projects:
            if project.id == project_id:
                return project
        raise KeyError(project_id)

    @property
    def all_skills(self) -> list[str]:
        return [item for group in self.skills for item in group.items]
