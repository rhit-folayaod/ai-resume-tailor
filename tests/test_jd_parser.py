import pytest

from resume_tailor.errors import LLMError, ResumeTailorError
from resume_tailor.jd_parser import ParsedJobDescription, parse_job_description

JD = """
Software Engineer Intern, Backend Platform.
We are looking for someone with Python and SQL experience. Familiarity with
Docker is a plus. You will work on internal services.
""".strip()


class ScriptedClient:
    """Returns canned responses in order and records the prompts it saw."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        return self.responses.pop(0)


def test_parses_valid_response():
    client = ScriptedClient(
        '{"required_skills": ["Python", "SQL"], "preferred_skills": ["Docker"],'
        ' "role_flavor": "backend platform", "seniority": "intern"}'
    )
    parsed = parse_job_description(JD, client)
    assert parsed.required_skills == ["Python", "SQL"]
    assert parsed.preferred_skills == ["Docker"]
    assert parsed.seniority == "intern"
    assert len(client.prompts) == 1


def test_strips_code_fences_and_dedupes():
    client = ScriptedClient(
        '```json\n{"required_skills": ["Python", "python", " SQL "],'
        ' "preferred_skills": [], "role_flavor": "", "seniority": ""}\n```'
    )
    parsed = parse_job_description(JD, client)
    assert parsed.required_skills == ["Python", "SQL"]
    assert parsed.role_flavor == "unspecified"
    assert parsed.seniority == "unspecified"


def test_retries_once_with_validation_error_in_prompt():
    client = ScriptedClient(
        '{"required_skills": "Python"}',
        '{"required_skills": ["Python"], "preferred_skills": [],'
        ' "role_flavor": "backend", "seniority": "intern"}',
    )
    parsed = parse_job_description(JD, client)
    assert parsed.required_skills == ["Python"]
    assert len(client.prompts) == 2
    retry_prompt = client.prompts[1][1]
    assert "rejected" in retry_prompt
    assert "required_skills" in retry_prompt


def test_gives_up_after_two_failures():
    client = ScriptedClient("not json", "still not json")
    with pytest.raises(LLMError, match="failed validation 2 times"):
        parse_job_description(JD, client)


def test_rejects_empty_job_description():
    with pytest.raises(ResumeTailorError, match="empty"):
        parse_job_description("   ", ScriptedClient())


def test_rejects_unknown_fields():
    client = ScriptedClient(
        '{"required_skills": [], "preferred_skills": [], "role_flavor": "x",'
        ' "seniority": "intern", "salary": "100k"}',
        '{"required_skills": [], "preferred_skills": [], "role_flavor": "x",'
        ' "seniority": "intern", "salary": "100k"}',
    )
    with pytest.raises(LLMError):
        parse_job_description(JD, client)


def test_schema_defaults():
    parsed = ParsedJobDescription()
    assert parsed.required_skills == []
    assert parsed.role_flavor == "unspecified"
