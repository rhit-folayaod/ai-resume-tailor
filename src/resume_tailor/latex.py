"""LaTeX escaping and template rendering.

Escaping is applied by the Jinja environment's `finalize` hook rather than by a
filter, so every value that reaches the template is escaped whether or not the
template author remembered to ask. Forgetting `|e` on one field is exactly the
kind of bug that surfaces as an unreadable Tectonic error three steps later.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jinja2

from .errors import TemplateError
from .jd_parser import ParsedJobDescription
from .matching import matches_any
from .models import ResumeStore
from .ranking import Selection

TEMPLATE_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = "resume.tex"

# Order matters only in that this is a single pass: escaping character by
# character in sequence would turn "&" into "\&" and then the backslash rule
# would turn that into "\textbackslash{}&".
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "_": r"\_",
    "%": r"\%",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_ESCAPE_RE = re.compile("|".join(re.escape(character) for character in _ESCAPES))


class Raw(str):
    """A string that is already LaTeX and must not be escaped again."""


def escape_latex(value: Any) -> str:
    """Make arbitrary text safe to drop into a LaTeX document.

    `C++` and `C#` come through as `C++` and `C\\#`, which is what they should
    be: `+` is an ordinary character in text mode, `#` is not.
    """

    if value is None:
        return ""
    if isinstance(value, Raw):
        return str(value)
    if not isinstance(value, str):
        value = str(value)
    return _ESCAPE_RE.sub(lambda match: _ESCAPES[match.group()], value)


def build_environment(template_dir: Path | str = TEMPLATE_DIR) -> jinja2.Environment:
    """A Jinja environment whose delimiters do not collide with LaTeX.

    Uses the conventional LaTeX-friendly set: `\\VAR{}` for expressions,
    `\\BLOCK{}` for statements, `\\#{}` for comments, `%%` and `%#` for their
    line forms. Braces and backslashes then belong to LaTeX alone.
    """

    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        line_comment_prefix="%#",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        finalize=escape_latex,
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )


def build_context(
    store: ResumeStore,
    selection: Selection,
    jd: ParsedJobDescription | None = None,
    reorder_skills: bool = False,
) -> dict[str, Any]:
    """Shape the selection into what the template consumes.

    Every string in here originates in `projects.yaml`. Nothing is composed,
    summarized, or rephrased on the way through.
    """

    entries = {"experience": [], "project": []}
    for item in selection.projects:
        project = item.project
        entries[project.section].append(
            {
                "title": project.organization or project.name,
                "role": project.role,
                "location": project.location,
                "dates": project.dates.display,
                "technologies": ", ".join(project.technologies),
                "bullets": [bullet.text for bullet in item.bullets],
            }
        )

    skills = [
        {"category": group.category, "items": _order_skills(group.items, jd, reorder_skills)}
        for group in store.skills
        if group.items
    ]

    return {
        "profile": store.profile,
        "education": store.education,
        "skills": skills,
        "experience": entries["experience"],
        "projects": entries["project"],
        "leadership": store.leadership,
    }


def _order_skills(
    items: list[str],
    jd: ParsedJobDescription | None,
    reorder: bool,
) -> list[str]:
    """Optionally float skills the posting asked for to the front of their line.

    This reorders; it never adds. A skill the posting wants but you do not have
    does not appear, because it is not in your store.
    """

    if not reorder or jd is None:
        return list(items)
    wanted = jd.all_skills
    matched = [item for item in items if matches_any(item, wanted) or _is_wanted(item, wanted)]
    rest = [item for item in items if item not in matched]
    return matched + rest


def _is_wanted(item: str, wanted: list[str]) -> bool:
    return any(matches_any(term, [item]) for term in wanted)


def render_resume(
    store: ResumeStore,
    selection: Selection,
    jd: ParsedJobDescription | None = None,
    template_name: str = DEFAULT_TEMPLATE,
    template_dir: Path | str = TEMPLATE_DIR,
    reorder_skills: bool = False,
) -> str:
    """Render the filled `.tex` document."""

    environment = build_environment(template_dir)
    try:
        template = environment.get_template(template_name)
    except jinja2.TemplateNotFound as exc:
        raise TemplateError(f"template {template_name!r} not found in {template_dir}") from exc

    context = build_context(store, selection, jd, reorder_skills)
    try:
        return template.render(**context)
    except jinja2.UndefinedError as exc:
        raise TemplateError(f"template referenced a value that was not provided: {exc}") from exc
    except jinja2.TemplateSyntaxError as exc:
        raise TemplateError(f"{template_name} line {exc.lineno}: {exc.message}") from exc
