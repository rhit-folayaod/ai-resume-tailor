import pytest
import yaml
from conftest import STORE_DATA, ScriptedClient
from fastapi.testclient import TestClient

from resume_tailor import web
from resume_tailor.compile import find_tectonic
from resume_tailor.errors import CompileError
from resume_tailor.store import load_store

JD_TEXT = (
    "Software Engineer Intern, Backend Platform. We are looking for someone with "
    "Python and PostgreSQL experience. Docker is a plus."
)

JD_RESPONSE = (
    '{"required_skills": ["Python", "PostgreSQL"], "preferred_skills": ["Docker"],'
    ' "role_flavor": "backend platform", "seniority": "intern"}'
)


@pytest.fixture
def store_path(tmp_path):
    path = tmp_path / "projects.yaml"
    path.write_text(yaml.safe_dump(STORE_DATA), encoding="utf-8")
    return path


@pytest.fixture
def client(store_path):
    return TestClient(web.create_app(store_path=store_path))


def use_client(monkeypatch, *responses: str) -> ScriptedClient:
    scripted = ScriptedClient(*responses)
    monkeypatch.setattr(web, "make_client", lambda model=None: scripted)
    return scripted


def test_health_reports_what_is_configured(client, store_path):
    body = client.get("/api/health").json()
    assert body["store_exists"] is True
    assert body["store_path"] == str(store_path.resolve())
    assert "model" in body


def test_reads_the_store(client):
    body = client.get("/api/store").json()
    assert [project["id"] for project in body["projects"]] == [
        "backend-service",
        "valve-calibration",
        "catan",
        "no-bullets-yet",
    ]


def test_writes_the_store_and_keeps_a_backup(client, store_path):
    payload = client.get("/api/store").json()
    payload["skills"][0]["items"].append("Rust")
    payload["projects"][0]["bullets"].append("Wrote the deployment runbook")

    response = client.put("/api/store", json=payload)

    assert response.status_code == 200
    saved = load_store(store_path)
    assert "Rust" in saved.skills[0].items
    assert "Wrote the deployment runbook" in saved.projects[0].bullets
    assert store_path.with_suffix(".yaml.bak").exists()


def test_rejecting_invalid_content_names_the_entry(client):
    payload = client.get("/api/store").json()
    payload["projects"][1]["role"] = ""

    response = client.put("/api/store", json=payload)

    assert response.status_code == 422
    assert 'entry 2 ("Gas Valve Calibration System")' in response.json()["error"]


def test_rejecting_invalid_content_leaves_the_file_alone(client, store_path):
    before = store_path.read_text(encoding="utf-8")
    payload = client.get("/api/store").json()
    payload["projects"][0]["bullets"] = ["  "]

    assert client.put("/api/store", json=payload).status_code == 422
    assert store_path.read_text(encoding="utf-8") == before


def test_tailor_returns_scored_selection(client, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)

    body = client.post("/api/tailor", json={"jd_text": JD_TEXT, "max_bullets": 4}).json()

    assert body["job"]["required_skills"] == ["Python", "PostgreSQL"]
    assert body["selection"]["bullet_count"] == 4
    assert body["selection"]["projects"][0]["name"] == "Inventory Service"
    assert any(item["reason"] == "no bullets written yet" for item in body["selection"]["skipped"])
    assert body["run_id"]


def test_tailored_bullets_all_come_from_the_store(client, monkeypatch, store_path):
    use_client(monkeypatch, JD_RESPONSE)
    known = {bullet for project in load_store(store_path).projects for bullet in project.bullets}

    body = client.post("/api/tailor", json={"jd_text": JD_TEXT}).json()

    returned = {
        bullet["text"]
        for project in body["selection"]["projects"]
        for bullet in project["bullets"]
    }
    assert returned <= known


def test_expected_failures_arrive_as_a_sentence(client, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)
    response = client.post("/api/tailor", json={"jd_text": "too short"})
    assert response.status_code == 400
    assert "job description" in response.json()["error"]


def test_unknown_run_id_is_reported(client):
    response = client.get("/api/resume/does-not-exist")
    assert response.status_code == 400
    assert "expired" in response.json()["error"]


def test_ingest_requires_a_source(client):
    assert client.post("/api/ingest", data={}).status_code == 400


def test_ingest_reads_an_uploaded_text_file(client):
    response = client.post(
        "/api/ingest",
        files={"file": ("posting.txt", JD_TEXT.encode() * 3, "text/plain")},
    )
    assert response.status_code == 200
    assert "PostgreSQL" in response.json()["text"]


def test_ingest_rejects_a_page_with_no_readable_text(client):
    response = client.post(
        "/api/ingest",
        files={"file": ("posting.html", b"<html><body><p>hi</p></body></html>", "text/html")},
    )
    assert response.status_code == 400
    assert "JavaScript" in response.json()["error"]


def test_ingest_rejects_a_non_http_url(client):
    response = client.post("/api/ingest", data={"url": "file:///etc/passwd"})
    assert response.status_code == 400
    assert "not an http(s) URL" in response.json()["error"]


def test_compiles_the_pdf_for_a_run(client, monkeypatch):
    try:
        find_tectonic()
    except CompileError:
        pytest.skip("tectonic is not installed")

    use_client(monkeypatch, JD_RESPONSE)
    run_id = client.post("/api/tailor", json={"jd_text": JD_TEXT}).json()["run_id"]

    response = client.get(f"/api/resume/{run_id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_serves_the_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
