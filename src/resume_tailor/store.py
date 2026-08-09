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


def _yaml_location(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc)
    if mark is None:
        return str(exc)
    return f"line {mark.line + 1}, column {mark.column + 1}: {problem}"


def _format_errors(exc: ValidationError, raw: dict[str, Any]) -> str:
    lines = []
    for error in exc.errors():
        location = _describe_location(error["loc"], raw)
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)


def _describe_location(loc: tuple[Any, ...], raw: dict[str, Any]) -> str:
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
