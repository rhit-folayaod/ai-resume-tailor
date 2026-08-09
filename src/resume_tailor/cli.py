"""Command line interface.

This is a tool you run under deadline pressure, so every failure it can
anticipate exits with one line saying what to fix, not a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .compile import compile_pdf, find_tectonic
from .errors import ResumeTailorError
from .jd_parser import ParsedJobDescription, parse_job_description
from .latex import render_resume
from .llm import LLMClient, OpenAIClient
from .ranking import Selection, SelectionBudget, select
from .store import DEFAULT_STORE_PATH, load_store


def make_client(model: str | None) -> LLMClient:
    """Indirection so tests can substitute a fake without touching the network."""

    return OpenAIClient(model=model)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--jd",
    "jd_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="File containing the job description. Omit to read from stdin.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default="resume.pdf",
    show_default=True,
    help="Where to write the compiled PDF.",
)
@click.option(
    "--projects",
    "store_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_STORE_PATH,
    show_default=True,
    help="The content store to select from.",
)
@click.option("--max-bullets", type=int, default=12, show_default=True, help="Total bullet budget.")
@click.option("--max-projects", type=int, default=6, show_default=True, help="Entries to include.")
@click.option(
    "--max-bullets-per-project",
    type=int,
    default=3,
    show_default=True,
    help="Cap per entry, so one project cannot fill the page.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be selected, with scores, and stop before compiling.",
)
@click.option(
    "--llm-rank",
    is_flag=True,
    help="Let the model reorder the selection. It can only reorder what deterministic "
    "scoring already shortlisted; it cannot add anything.",
)
@click.option(
    "--reorder-skills",
    is_flag=True,
    help="Move skills the posting asks for to the front of their line. Reorders only.",
)
@click.option("--model", default=None, help="Model name. Defaults to $RESUME_TAILOR_MODEL.")
@click.option("--tectonic", default=None, help="Path to the tectonic binary.")
@click.option(
    "--emit-tex",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also write the generated .tex, for debugging the template.",
)
def main(
    jd_path: Path | None,
    out_path: Path,
    store_path: Path,
    max_bullets: int,
    max_projects: int,
    max_bullets_per_project: int,
    dry_run: bool,
    llm_rank: bool,
    reorder_skills: bool,
    model: str | None,
    tectonic: str | None,
    emit_tex: Path | None,
) -> None:
    """Tailor your resume to a job description.

    Selects and reorders bullets you have already written in projects.yaml.
    It never writes a new one.
    """

    try:
        _run(
            jd_path=jd_path,
            out_path=out_path,
            store_path=store_path,
            budget=SelectionBudget(
                max_bullets=max_bullets,
                max_projects=max_projects,
                max_bullets_per_project=max_bullets_per_project,
            ),
            dry_run=dry_run,
            llm_rank=llm_rank,
            reorder_skills=reorder_skills,
            model=model,
            tectonic=tectonic,
            emit_tex=emit_tex,
        )
    except ResumeTailorError as exc:
        raise click.ClickException(str(exc)) from exc


def _run(
    jd_path: Path | None,
    out_path: Path,
    store_path: Path,
    budget: SelectionBudget,
    dry_run: bool,
    llm_rank: bool,
    reorder_skills: bool,
    model: str | None,
    tectonic: str | None,
    emit_tex: Path | None,
) -> None:
    store = load_store(store_path)
    jd_text = _read_job_description(jd_path)

    # Fail on a missing binary before spending a model call on the parse.
    if not dry_run:
        tectonic = find_tectonic(tectonic)

    client = make_client(model)
    _note(f"Parsing job description ({len(jd_text)} characters)...")
    jd = parse_job_description(jd_text, client)

    _note("Ranking your projects...")
    selection = select(
        store,
        jd,
        budget=budget,
        client=client if llm_rank else None,
    )

    _report(jd, selection, budget)

    if dry_run:
        _note("Dry run: nothing compiled.")
        return

    tex = render_resume(store, selection, jd, reorder_skills=reorder_skills)
    if emit_tex is not None:
        emit_tex.parent.mkdir(parents=True, exist_ok=True)
        emit_tex.write_text(tex, encoding="utf-8")
        _note(f"Wrote {emit_tex}")

    _note("Compiling with tectonic...")
    pdf = compile_pdf(tex, tectonic=tectonic)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pdf)
    click.secho(f"Wrote {out_path} ({len(pdf) // 1024} KB)", fg="green")


def _read_job_description(jd_path: Path | None) -> str:
    if jd_path is not None:
        text = jd_path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            raise ResumeTailorError(f"{jd_path} is empty.")
        return text

    if sys.stdin.isatty():
        raise ResumeTailorError(
            "no job description given. Pass --jd path/to/jd.txt, or pipe the posting "
            "in on stdin."
        )
    text = sys.stdin.read()
    if not text.strip():
        raise ResumeTailorError("nothing arrived on stdin.")
    return text


def _report(jd: ParsedJobDescription, selection: Selection, budget: SelectionBudget) -> None:
    """Show what was chosen and why, so selection can be sanity-checked."""

    click.echo()
    click.secho(f"Posting: {jd.role_flavor} ({jd.seniority})", bold=True)
    click.echo(f"  required:  {', '.join(jd.required_skills) or '(none stated)'}")
    click.echo(f"  preferred: {', '.join(jd.preferred_skills) or '(none stated)'}")

    ordering = "model-reranked" if selection.reranked_by_llm else "score order"
    click.echo()
    click.secho(
        f"Selected {selection.bullet_count} bullets from {len(selection.projects)} entries "
        f"(budget {budget.max_bullets}, {ordering}):",
        bold=True,
    )

    for item in selection.projects:
        project = item.project
        header = f"  [{item.score:5.2f}] {project.name}"
        if project.organization:
            header += f" - {project.organization}"
        click.echo(f"{header}  ({project.dates.display})")
        for bullet in item.bullets:
            click.echo(f"      [{bullet.score:4.1f}] {_truncate(bullet.text)}")
            if bullet.matched:
                click.secho(f"             matched: {', '.join(bullet.matched)}", dim=True)

    chosen = {item.project.id for item in selection.projects}
    skipped = [item for item in selection.ranked if item.id not in chosen]
    if skipped:
        click.echo()
        click.secho("Not selected:", dim=True)
        for item in skipped:
            reason = "no bullets written yet" if not item.bullets else "outscored"
            click.secho(f"  [{item.score:5.2f}] {item.project.name} ({reason})", dim=True)
    click.echo()


def _truncate(text: str, width: int = 96) -> str:
    return text if len(text) <= width else text[: width - 1] + "\u2026"


def _note(message: str) -> None:
    click.secho(message, dim=True, err=True)


if __name__ == "__main__":
    main()
