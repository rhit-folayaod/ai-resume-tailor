from pathlib import Path

import pytest
import yaml
from conftest import STORE_DATA

from resume_tailor.errors import StoreError
from resume_tailor.models import ResumeStore
from resume_tailor.store import load_store

REPO_ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path: Path, data) -> Path:
    path = tmp_path / "projects.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def with_projects(*projects) -> dict:
    return {**STORE_DATA, "projects": list(projects)}


def test_loads_the_fixture(tmp_path):
    store = load_store(write(tmp_path, STORE_DATA))
    assert [project.id for project in store.projects] == [
        "backend-service",
        "valve-calibration",
        "catan",
        "no-bullets-yet",
    ]


def test_the_shipped_store_is_valid():
    """The projects.yaml in the repo must always load; it is the worked example."""

    store = load_store(REPO_ROOT / "projects.yaml")
    assert store.projects
    assert all(project.bullets == [] for project in store.projects), (
        "the shipped store is the sanitized placeholder; it should not carry real bullets"
    )


def test_missing_file_says_what_to_do(tmp_path):
    with pytest.raises(StoreError, match="content store not found"):
        load_store(tmp_path / "absent.yaml")


def test_empty_file(tmp_path):
    path = tmp_path / "projects.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(StoreError, match="is empty"):
        load_store(path)


def test_not_a_mapping(tmp_path):
    path = tmp_path / "projects.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(StoreError, match="must be a mapping"):
        load_store(path)


def test_invalid_yaml_reports_line_and_column(tmp_path):
    path = tmp_path / "projects.yaml"
    path.write_text("profile:\n  name: Test\n   bad: indent\n", encoding="utf-8")
    with pytest.raises(StoreError, match="not valid YAML") as info:
        load_store(path)
    assert "line 3, column" in str(info.value)


def test_missing_required_field_names_the_entry(tmp_path):
    data = with_projects({"name": "Half Written", "dates": {"start": "2024"}})
    with pytest.raises(StoreError) as info:
        load_store(write(tmp_path, data))
    message = str(info.value)
    assert 'entry 1 ("Half Written")' in message
    assert "role" in message


def test_typo_in_a_field_name_is_rejected(tmp_path):
    data = with_projects(
        {
            "name": "Typo",
            "role": "intern",
            "dates": {"start": "2024"},
            "tecnologies": ["Python"],
        }
    )
    with pytest.raises(StoreError, match="Extra inputs are not permitted") as info:
        load_store(write(tmp_path, data))
    assert "tecnologies" in str(info.value)


def test_empty_bullet_is_rejected_with_its_position(tmp_path):
    data = with_projects(
        {
            "name": "Blank Bullet",
            "role": "intern",
            "dates": {"start": "2024"},
            "bullets": ["a real accomplishment", "   "],
        }
    )
    with pytest.raises(StoreError, match="bullet 1 is empty") as info:
        load_store(write(tmp_path, data))
    assert 'entry 1 ("Blank Bullet")' in str(info.value)


def test_duplicate_ids_are_rejected(tmp_path):
    entry = {"name": "Same Name", "role": "intern", "dates": {"start": "2024"}}
    with pytest.raises(StoreError, match="duplicate project id"):
        load_store(write(tmp_path, with_projects(entry, dict(entry))))


def test_bad_section_value_is_rejected(tmp_path):
    data = with_projects(
        {
            "name": "Wrong Section",
            "role": "intern",
            "section": "employment",
            "dates": {"start": "2024"},
        }
    )
    with pytest.raises(StoreError, match="section"):
        load_store(write(tmp_path, data))


def test_ids_default_to_a_slug_of_the_name():
    store = ResumeStore.model_validate(
        with_projects({"name": "NBA Player Predictor!", "role": "personal project",
                       "dates": {"start": "2025"}})
    )
    assert store.projects[0].id == "nba-player-predictor"


def test_bullet_whitespace_is_normalized_but_wording_is_untouched():
    text = "Built   a thing\n  that worked well"
    store = ResumeStore.model_validate(
        with_projects({"name": "Spacing", "role": "intern", "dates": {"start": "2024"},
                       "bullets": [text]})
    )
    assert store.projects[0].bullets == ["Built a thing that worked well"]


def test_present_end_date_renders_as_present():
    store = ResumeStore.model_validate(
        with_projects({"name": "Ongoing", "role": "intern",
                       "dates": {"start": "Winter 2024", "end": "present"}})
    )
    dates = store.projects[0].dates
    assert dates.is_current
    assert dates.display == "Winter 2024 \u2013 Present"


def test_identical_start_and_end_collapse():
    store = ResumeStore.model_validate(
        with_projects({"name": "Single Term", "role": "intern",
                       "dates": {"start": "Summer 2025", "end": "Summer 2025"}})
    )
    assert store.projects[0].dates.display == "Summer 2025"


def test_lookup_by_id():
    store = ResumeStore.model_validate(STORE_DATA)
    assert store.by_id("catan").name == "Settlers of Catan"
    with pytest.raises(KeyError):
        store.by_id("nope")
