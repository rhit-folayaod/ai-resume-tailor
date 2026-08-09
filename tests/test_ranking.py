import pytest

from resume_tailor.errors import LLMError, SelectionError
from resume_tailor.jd_parser import ParsedJobDescription
from resume_tailor.models import ResumeStore
from resume_tailor.ranking import SelectionBudget, rank_projects, select

from conftest import STORE_DATA, ScriptedClient


def all_bullet_texts(store: ResumeStore) -> set[str]:
    return {bullet for project in store.projects for bullet in project.bullets}


def selected_texts(selection) -> list[str]:
    return [bullet.text for item in selection.projects for bullet in item.bullets]


def test_ranking_puts_the_matching_project_first(store, backend_jd):
    ranked = rank_projects(store, backend_jd)
    assert ranked[0].project.id == "backend-service"
    assert ranked[0].score > ranked[1].score


def test_ranking_is_deterministic(store, backend_jd):
    first = [(item.id, item.score) for item in rank_projects(store, backend_jd)]
    second = [(item.id, item.score) for item in rank_projects(store, backend_jd)]
    assert first == second


def test_hardware_posting_reorders_the_ranking(store):
    jd = ParsedJobDescription(
        required_skills=["C#", "Modbus TCP/IP"],
        preferred_skills=[],
        role_flavor="embedded hardware test systems",
        seniority="intern",
    )
    ranked = rank_projects(store, jd)
    assert ranked[0].project.id == "valve-calibration"


def test_always_include_wins_over_score(store, backend_jd):
    data = {**STORE_DATA}
    data["projects"] = [dict(project) for project in STORE_DATA["projects"]]
    data["projects"][2] = {**data["projects"][2], "always_include": True}
    ranked = rank_projects(ResumeStore.model_validate(data), backend_jd)
    assert ranked[0].project.id == "catan"


def test_bullets_are_ranked_within_a_project(store, backend_jd):
    ranked = rank_projects(store, backend_jd)
    backend = next(item for item in ranked if item.id == "backend-service")
    assert "Ran the weekly team demo" == backend.bullets[-1].text
    assert backend.bullets[0].score >= backend.bullets[1].score


def test_selection_only_contains_bullets_from_the_store(store, backend_jd):
    selection = select(store, backend_jd)
    assert set(selected_texts(selection)) <= all_bullet_texts(store)


def test_selection_respects_the_bullet_budget(store, backend_jd):
    selection = select(store, backend_jd, SelectionBudget(max_bullets=3))
    assert selection.bullet_count == 3


def test_budget_is_spread_across_projects_not_eaten_by_one(store, backend_jd):
    selection = select(store, backend_jd, SelectionBudget(max_bullets=3))
    assert [len(item.bullets) for item in selection.projects] == [1, 1, 1]


def test_per_project_cap_is_honored(store, backend_jd):
    selection = select(
        store,
        backend_jd,
        SelectionBudget(max_bullets=20, max_bullets_per_project=1),
    )
    assert all(len(item.bullets) == 1 for item in selection.projects)


def test_project_cap_is_honored(store, backend_jd):
    selection = select(store, backend_jd, SelectionBudget(max_projects=1))
    assert len(selection.projects) == 1
    assert selection.projects[0].project.id == "backend-service"


def test_projects_without_bullets_are_never_selected(store, backend_jd):
    selection = select(store, backend_jd, SelectionBudget(max_bullets=50))
    assert "no-bullets-yet" not in {item.project.id for item in selection.projects}


def test_empty_store_raises_actionable_error(backend_jd):
    data = {**STORE_DATA, "projects": []}
    with pytest.raises(SelectionError, match="bullets"):
        select(ResumeStore.model_validate(data), backend_jd)


def test_invalid_budget_is_rejected():
    with pytest.raises(SelectionError, match="max_bullets"):
        SelectionBudget(max_bullets=0)


def test_llm_rerank_reorders_within_the_candidate_set(store, backend_jd):
    client = ScriptedClient(
        '{"selected": ['
        '{"project_id": "catan", "bullet_index": 1},'
        '{"project_id": "backend-service", "bullet_index": 2}]}'
    )
    selection = select(store, backend_jd, SelectionBudget(max_bullets=5), client=client)
    assert selection.reranked_by_llm
    assert selected_texts(selection) == [
        "Added a German locale from scratch",
        "Wrote the PostgreSQL schema and migrations",
    ]
    assert set(selected_texts(selection)) <= all_bullet_texts(store)


def test_llm_cannot_introduce_a_bullet_outside_the_candidate_set(store, backend_jd):
    fabricated = (
        '{"selected": [{"project_id": "backend-service", "bullet_index": 99}]}'
    )
    valid = '{"selected": [{"project_id": "backend-service", "bullet_index": 0}]}'
    client = ScriptedClient(fabricated, valid)

    selection = select(store, backend_jd, client=client)

    assert len(client.prompts) == 2
    assert "not in the candidate list" in client.prompts[1][1]
    assert selected_texts(selection) == [
        "Built a Python service that cut request latency by 40%"
    ]


def test_llm_cannot_introduce_an_unknown_project(store, backend_jd):
    response = '{"selected": [{"project_id": "made-up-job", "bullet_index": 0}]}'
    client = ScriptedClient(response, response)
    with pytest.raises(LLMError):
        select(store, backend_jd, client=client)


def test_llm_response_carrying_bullet_text_is_rejected(store, backend_jd):
    """The rerank schema has no text field, so a model that tries to write is refused."""

    writing = (
        '{"selected": [{"project_id": "backend-service", "bullet_index": 0,'
        ' "text": "Led a team of 12 engineers"}]}'
    )
    client = ScriptedClient(writing, writing)
    with pytest.raises(LLMError):
        select(store, backend_jd, client=client)


def test_llm_duplicate_selection_is_rejected(store, backend_jd):
    duplicate = (
        '{"selected": [{"project_id": "catan", "bullet_index": 0},'
        '{"project_id": "catan", "bullet_index": 0}]}'
    )
    valid = '{"selected": [{"project_id": "catan", "bullet_index": 0}]}'
    client = ScriptedClient(duplicate, valid)
    selection = select(store, backend_jd, client=client)
    assert selection.bullet_count == 1


def test_rerank_prompt_never_asks_for_text(store, backend_jd):
    client = ScriptedClient('{"selected": [{"project_id": "catan", "bullet_index": 0}]}')
    select(store, backend_jd, client=client)
    system = client.prompts[0][0]
    assert "not writing" in system.lower()
