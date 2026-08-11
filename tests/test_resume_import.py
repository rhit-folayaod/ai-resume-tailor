"""Resume import: extract store draft from text/PDF without inventing claims."""

from __future__ import annotations

import json

import pytest
import yaml
from conftest import STORE_DATA, ScriptedClient
from fastapi.testclient import TestClient

from resume_tailor import web
from resume_tailor.models import ResumeStore
from resume_tailor.resume_parser import parse_resume_text
from resume_tailor.store import load_store


SAMPLE_RESUME = """
Ada Lovelace
ada@example.com | linkedin.com/in/ada
Education
Analytical Engine Institute London
B.S. Computing Expected 2027
• Coursework: Algorithms, Databases
Technical Skills
Languages: Python, SQL
Tools: Git, Docker
Experience
Acme Corp London
Software Intern Summer 2025
• Built a Python service that cut request latency by 40%
• Wrote the PostgreSQL schema and migrations
Projects
Settlers of Catan | Java
• Developed a Java implementation of Settlers of Catan
Leadership
Vice President, Computing Club – 2024–Present
"""


@pytest.fixture
def store_path(tmp_path):
    path = tmp_path / "projects.yaml"
    path.write_text(yaml.safe_dump(STORE_DATA), encoding="utf-8")
    return path


def test_projects_yaml_in_repo_is_valid():
    store = load_store("projects.yaml")
    assert store.profile.email == "timilehindfolayan@gmail.com"
    assert any(p.id == "emerson-ni" and p.bullets for p in store.projects)
    assert any(g.category.startswith("Languages") for g in store.skills)


def test_parse_resume_text_builds_store():
    draft = {
        "profile": {
            "name": "Ada Lovelace",
            "phone": "",
            "email": "ada@example.com",
            "links": ["linkedin.com/in/ada"],
        },
        "education": [
            {
                "school": "Analytical Engine Institute",
                "location": "London",
                "degree": "B.S. Computing",
                "graduation": "Expected 2027",
                "coursework": ["Algorithms", "Databases"],
            }
        ],
        "skills": [
            {"category": "Languages", "items": ["Python", "SQL"]},
            {"category": "Tools", "items": ["Git", "Docker"]},
        ],
        "projects": [
            {
                "name": "Software Intern",
                "organization": "Acme Corp",
                "role": "Software Intern",
                "location": "London",
                "section": "experience",
                "dates": {"start": "Summer 2025", "end": "Summer 2025"},
                "technologies": ["Python"],
                "domains": [],
                "keywords": [],
                "bullets": [
                    "Built a Python service that cut request latency by 40%",
                    "Wrote the PostgreSQL schema and migrations",
                ],
            },
            {
                "name": "Settlers of Catan",
                "role": "personal project",
                "section": "project",
                "dates": {"start": "2024", "end": "2024"},
                "technologies": ["Java"],
                "domains": [],
                "keywords": [],
                "bullets": ["Developed a Java implementation of Settlers of Catan"],
            },
        ],
        "leadership": ["Vice President, Computing Club – 2024–Present"],
    }
    store = parse_resume_text(SAMPLE_RESUME, ScriptedClient(json.dumps(draft)))
    assert store.profile.name == "Ada Lovelace"
    assert len(store.projects) == 2
    assert store.projects[0].id == "software-intern"
    assert "Python" in store.all_skills


def test_import_resume_endpoint_returns_draft(store_path, monkeypatch):
    draft = ResumeStore.model_validate(STORE_DATA).model_dump(mode="json")
    monkeypatch.setattr(web, "make_client", lambda model=None: ScriptedClient(json.dumps(draft)))
    client = TestClient(web.create_app(store_path=store_path, hosting=None))
    response = client.post(
        "/api/import-resume",
        data={"text": SAMPLE_RESUME},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] is False
    assert body["store"]["profile"]["email"] == "test@example.com"


def test_import_resume_apply_writes_store(store_path, monkeypatch):
    draft = ResumeStore.model_validate(STORE_DATA).model_dump(mode="json")
    draft["profile"]["name"] = "Imported Person"
    monkeypatch.setattr(web, "make_client", lambda model=None: ScriptedClient(json.dumps(draft)))
    client = TestClient(web.create_app(store_path=store_path, hosting=None))
    response = client.post(
        "/api/import-resume",
        data={"text": SAMPLE_RESUME, "apply": "1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    assert load_store(store_path).profile.name == "Imported Person"
