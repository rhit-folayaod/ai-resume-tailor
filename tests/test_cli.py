import yaml
import pytest
from click.testing import CliRunner
from conftest import STORE_DATA, ScriptedClient

from resume_tailor import cli
from resume_tailor.compile import find_tectonic
from resume_tailor.errors import CompileError

JD_TEXT = (
    "Software Engineer Intern, Backend Platform. We are looking for someone with "
    "Python and PostgreSQL experience. Docker is a plus."
)

JD_RESPONSE = (
    '{"required_skills": ["Python", "PostgreSQL"], "preferred_skills": ["Docker"],'
    ' "role_flavor": "backend platform", "seniority": "intern"}'
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "projects.yaml").write_text(yaml.safe_dump(STORE_DATA), encoding="utf-8")
    (tmp_path / "jd.txt").write_text(JD_TEXT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def use_client(monkeypatch, *responses: str) -> ScriptedClient:
    client = ScriptedClient(*responses)
    monkeypatch.setattr(cli, "make_client", lambda model: client)
    return client


def test_dry_run_reports_selection_without_compiling(workspace, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(cli.main, ["--jd", "jd.txt", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "backend platform" in result.output
    assert "Inventory Service" in result.output
    assert "Built a Python service that cut request latency by 40%" in result.output
    assert not list(workspace.glob("*.pdf"))


def test_dry_run_shows_what_was_left_out_and_why(workspace, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(cli.main, ["--jd", "jd.txt", "--dry-run"])
    assert "Not selected:" in result.output
    assert "no bullets written yet" in result.output


def test_reads_the_posting_from_stdin(workspace, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(cli.main, ["--dry-run"], input=JD_TEXT)
    assert result.exit_code == 0, result.output
    assert "Inventory Service" in result.output


def test_max_bullets_is_respected(workspace, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(
        cli.main, ["--jd", "jd.txt", "--dry-run", "--max-bullets", "2"]
    )
    assert "Selected 2 bullets" in result.output


def test_missing_store_is_reported_not_raised(workspace, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(
        cli.main, ["--jd", "jd.txt", "--dry-run", "--projects", "nope.yaml"]
    )
    assert result.exit_code != 0
    assert "content store not found" in result.output
    assert "Traceback" not in result.output


def test_malformed_store_points_at_the_entry(workspace, monkeypatch):
    broken = {**STORE_DATA, "projects": [{"name": "Broken", "dates": {"start": "2024"}}]}
    (workspace / "projects.yaml").write_text(yaml.safe_dump(broken), encoding="utf-8")
    use_client(monkeypatch, JD_RESPONSE)

    result = CliRunner().invoke(cli.main, ["--jd", "jd.txt", "--dry-run"])

    assert result.exit_code != 0
    assert 'entry 1 ("Broken")' in result.output
    assert "Traceback" not in result.output


def test_empty_posting_file_is_reported(workspace, monkeypatch):
    (workspace / "jd.txt").write_text("   ", encoding="utf-8")
    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(cli.main, ["--jd", "jd.txt", "--dry-run"])
    assert result.exit_code != 0
    assert "empty" in result.output


def test_invalid_budget_is_reported(workspace, monkeypatch):
    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(
        cli.main, ["--jd", "jd.txt", "--dry-run", "--max-bullets", "0"]
    )
    assert result.exit_code != 0
    assert "max_bullets" in result.output


def test_llm_rank_flag_uses_the_model_for_ordering(workspace, monkeypatch):
    client = use_client(
        monkeypatch,
        JD_RESPONSE,
        '{"selected": [{"project_id": "catan", "bullet_index": 1}]}',
    )
    result = CliRunner().invoke(cli.main, ["--jd", "jd.txt", "--dry-run", "--llm-rank"])
    assert result.exit_code == 0, result.output
    assert "model-reranked" in result.output
    assert "Added a German locale from scratch" in result.output
    assert len(client.prompts) == 2


def test_end_to_end_produces_a_pdf(workspace, monkeypatch):
    try:
        find_tectonic()
    except CompileError:
        pytest.skip("tectonic is not installed")

    use_client(monkeypatch, JD_RESPONSE)
    result = CliRunner().invoke(
        cli.main, ["--jd", "jd.txt", "--out", "out/resume.pdf", "--emit-tex", "out/resume.tex"]
    )

    assert result.exit_code == 0, result.output
    pdf = workspace / "out" / "resume.pdf"
    assert pdf.read_bytes().startswith(b"%PDF-")
    assert (workspace / "out" / "resume.tex").exists()
    assert not list(workspace.glob("*.aux"))
    assert not list(workspace.glob("*.log"))
