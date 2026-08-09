"""Loading and validating `projects.yaml`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .errors import StoreError
from .models import ResumeStore

DEFAULT_STORE_PATH = Path("projects.yaml")


def load_store(path: Path | str = DEFAULT_STORE_PATH) -> ResumeStore:
    """Read and validate the content store.

    Raises `StoreError` with a message that names the offending entry.
    """

    path = Path(path)
    if not path.exists():
        raise StoreError(
            f"content store not found at {path}. "
            "Create it (see README) or pass --projects with the right path."
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StoreError(f"{path} is not valid YAML.\n{_yaml_location(exc)}") from exc

    if raw is None:
        raise StoreError(f"{path} is empty.")
    if not isinstance(raw, dict):
        raise StoreError(
            f"{path} must be a mapping with a top-level `profile` and `projects` key, "
            f"got {type(raw).__name__}."
        )

    try:
        return ResumeStore.model_validate(raw)
    except ValidationError as exc:
        raise StoreError(f"{path} does not match the schema.\n{_format_errors(exc, raw)}") from exc


SAVED_HEADER = """\
# Content store for resume-tailor.
#
# This is the ONLY place resume content comes from. The tool selects, ranks, and
# reorders what is written here; it never rewrites a bullet or invents a new one.
#
# Written by the editor UI. Editing by hand is still fine, but note that saving
# from the UI rewrites this file and does not preserve comments you add. The
# previous version is kept alongside it as projects.yaml.bak.
"""


def save_store(store: ResumeStore, path: Path | str = DEFAULT_STORE_PATH) -> None:
    """Write the store back to YAML, keeping one backup.

    The write is atomic: a resume store is hand-written content that would be
    painful to lose, so a crash mid-write must not be able to truncate it.
    """

    path = Path(path)
    payload = store.model_dump(mode="json")
    body = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=88,
    )

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"{SAVED_HEADER}\n{body}", encoding="utf-8")

    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_bytes(path.read_bytes())
    temporary.replace(path)


def format_validation_error(exc: ValidationError, raw: Any) -> str:
    """Render pydantic errors against the raw document, for API responses."""

    return _format_errors(exc, raw)


def _yaml_location(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc)
    if mark is None:
        return str(exc)
    return f"line {mark.line + 1}, column {mark.column + 1}: {problem}"


def _format_errors(exc: ValidationError, raw: Any) -> str:
    lines = []
    for error in exc.errors():
        location = _describe_location(error["loc"], raw)
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)


def _describe_location(loc: tuple[Any, ...], raw: Any) -> str:
    """Turn a pydantic error location into something findable in the YAML file.

    `('projects', 3, 'bullets', 0)` becomes
    `projects -> entry 4 "RHV" -> bullets -> item 1`, because "index 3" is not
    how anyone reads a YAML file.
    """

    parts: list[str] = []
    cursor: Any = raw
    for key in loc:
        if isinstance(key, int):
            label = f"item {key + 1}"
            if isinstance(cursor, list) and key < len(cursor):
                cursor = cursor[key]
                name = cursor.get("name") if isinstance(cursor, dict) else None
                if name:
                    label = f'entry {key + 1} ("{name}")'
            else:
                cursor = None
            parts.append(label)
        else:
            parts.append(str(key))
            cursor = cursor.get(key) if isinstance(cursor, dict) else None
    return " -> ".join(parts) if parts else "(document root)"
