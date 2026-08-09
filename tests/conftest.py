from __future__ import annotations

import pytest

from resume_tailor.jd_parser import ParsedJobDescription
from resume_tailor.models import ResumeStore

STORE_DATA = {
    "profile": {
        "name": "Test Person",
        "phone": "(555) 010-0000",
        "email": "test@example.com",
        "links": ["linkedin.com/in/testperson"],
    },
    "education": [
        {
            "school": "Rose-Hulman Institute of Technology",
            "location": "Terre Haute, IN",
            "degree": "B.S. Software Engineering",
            "graduation": "Expected 2027",
            "coursework": ["Data Structures & Algorithms", "Databases"],
        }
    ],
    "skills": [
        {"category": "Languages", "items": ["Python", "C#", "C++", "Java", "SQL"]},
        {"category": "Tools", "items": ["Docker", "PostgreSQL", "Git"]},
    ],
    "projects": [
        {
            "id": "backend-service",
            "name": "Inventory Service",
            "organization": "Acme",
            "role": "intern",
            "location": "Austin, TX",
            "section": "experience",
            "dates": {"start": "Summer 2025", "end": "Summer 2025"},
            "technologies": ["Python", "FastAPI", "PostgreSQL", "Docker"],
            "domains": ["backend", "data"],
            "keywords": ["REST"],
            "bullets": [
                "Built a Python service that cut request latency by 40%",
                "Containerized the service with Docker for repeatable deploys",
                "Wrote the PostgreSQL schema and migrations",
                "Ran the weekly team demo",
            ],
        },
        {
            "id": "valve-calibration",
            "name": "Gas Valve Calibration System",
            "organization": "RHV",
            "role": "intern",
            "section": "experience",
            "dates": {"start": "Summer 2024", "end": "Summer 2024"},
            "technologies": ["C#", "WinForms", "Modbus TCP/IP"],
            "domains": ["hardware", "embedded"],
            "keywords": [],
            "bullets": [
                "Built a multithreaded C# WinForms control interface",
                "Implemented Modbus TCP/IP communication across six units",
            ],
        },
        {
            "id": "catan",
            "name": "Settlers of Catan",
            "role": "course project",
            "section": "project",
            "dates": {"start": "2024", "end": "2024"},
            "technologies": ["Java", "Maven"],
            "domains": ["software-design"],
            "keywords": [],
            "bullets": [
                "Implemented game logic across a multi-package Maven project",
                "Added a German locale from scratch",
            ],
        },
        {
            "id": "no-bullets-yet",
            "name": "Unwritten Project",
            "role": "personal project",
            "section": "project",
            "dates": {"start": "2026", "end": "present"},
            "technologies": ["Python"],
            "domains": [],
            "keywords": [],
            "bullets": [],
        },
    ],
    "leadership": ["Vice President, Test Club - 2024-Present"],
}


@pytest.fixture
def store() -> ResumeStore:
    return ResumeStore.model_validate(STORE_DATA)


@pytest.fixture
def backend_jd() -> ParsedJobDescription:
    return ParsedJobDescription(
        required_skills=["Python", "PostgreSQL"],
        preferred_skills=["Docker"],
        role_flavor="backend data services",
        seniority="intern",
    )


class ScriptedClient:
    """A fake `LLMClient` that replays canned responses and records prompts."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if not self.responses:
            raise AssertionError("the client was called more times than expected")
        return self.responses.pop(0)
