"""Matching, ranking, and selection.

The no-fabrication guarantee is enforced structurally here, in three layers:

1. The candidate set is built from `projects.yaml` and frozen *before* any model
   call happens. Nothing can be appended to it afterwards.
2. The optional LLM rerank returns *references* — a project id and a bullet
   index — never text. Bullet text is looked up from the store after the call,
   so text the model emits is not merely rejected, it is never read.
3. Every reference is checked against the frozen candidate set. A reference to
   something that is not in it is a validation failure, which re-prompts once
   and then fails loudly rather than silently dropping.

Scoring itself is deterministic and takes no client, so it is testable without
an LLM and produces the same ranking for the same inputs every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import SelectionError
from .jd_parser import ParsedJobDescription
from .llm import LLMClient, request_validated_json
from .matching import matches_any, significant_words, term_matches
from .models import ProjectEntry, ResumeStore, StrictModel


@dataclass(frozen=True)
class ScoringWeights:
    """How much each kind of evidence is worth.

    Required skills outweigh preferred ones, and a skill listed as a project's
    technology outweighs the same word appearing in prose, because the former is
    a claim about the project and the latter might be incidental.
    """

    required_technology: float = 3.0
    preferred_technology: float = 1.5
    required_domain: float = 1.5
    preferred_domain: float = 0.75
    required_in_bullet: float = 1.0
    preferred_in_bullet: float = 0.5
    role_flavor_overlap: float = 0.5
    current_role: float = 0.5
    bullet_required: float = 2.0
    bullet_preferred: float = 1.0
    bullet_project_technology: float = 0.5
    bullet_quantified: float = 0.25


@dataclass(frozen=True)
class SelectionBudget:
    """The page budget, expressed in bullets."""

    max_bullets: int = 12
    max_projects: int = 6
    max_bullets_per_project: int = 3
    min_project_score: float = 0.0

    def __post_init__(self) -> None:
        for name in ("max_bullets", "max_projects", "max_bullets_per_project"):
            if getattr(self, name) < 1:
                raise SelectionError(f"{name} must be at least 1")


@dataclass
class ScoredBullet:
    project_id: str
    index: int
    text: str
    score: float
    matched: list[str] = field(default_factory=list)


@dataclass
class ScoredProject:
    project: ProjectEntry
    score: float
    matched: list[str]
    bullets: list[ScoredBullet]

    @property
    def id(self) -> str:
        return self.project.id


@dataclass
class SelectedProject:
    project: ProjectEntry
    score: float
    bullets: list[ScoredBullet]


@dataclass
class Selection:
    """What made the cut, plus the full ranking for `--dry-run` inspection."""

    projects: list[SelectedProject]
    ranked: list[ScoredProject]
    reranked_by_llm: bool = False

    @property
    def bullet_count(self) -> int:
        return sum(len(item.bullets) for item in self.projects)


class BulletRef(StrictModel):
    """A pointer into the candidate set. Deliberately carries no text."""

    project_id: str
    bullet_index: int


class RerankResponse(StrictModel):
    selected: list[BulletRef]


def score_bullet(
    bullet: str,
    project: ProjectEntry,
    jd: ParsedJobDescription,
    weights: ScoringWeights,
) -> tuple[float, list[str]]:
    score = 0.0
    matched: list[str] = []
    for skill in jd.required_skills:
        if term_matches(skill, bullet):
            score += weights.bullet_required
            matched.append(skill)
    for skill in jd.preferred_skills:
        if term_matches(skill, bullet):
            score += weights.bullet_preferred
            matched.append(skill)
    for technology in project.technologies:
        if term_matches(technology, bullet) and matches_any(technology, jd.all_skills):
            score += weights.bullet_project_technology
    if any(character.isdigit() for character in bullet):
        score += weights.bullet_quantified
    return score, matched


def score_project(
    project: ProjectEntry,
    jd: ParsedJobDescription,
    weights: ScoringWeights,
) -> ScoredProject:
    score = 0.0
    matched: list[str] = []

    tags = project.domains + project.keywords

    for skill in jd.required_skills:
        if matches_any(skill, project.technologies):
            score += weights.required_technology
            matched.append(skill)
        elif matches_any(skill, tags):
            score += weights.required_domain
            matched.append(skill)
        elif matches_any(skill, project.bullets):
            score += weights.required_in_bullet
            matched.append(skill)

    for skill in jd.preferred_skills:
        if matches_any(skill, project.technologies):
            score += weights.preferred_technology
            matched.append(skill)
        elif matches_any(skill, tags):
            score += weights.preferred_domain
            matched.append(skill)
        elif matches_any(skill, project.bullets):
            score += weights.preferred_in_bullet
            matched.append(skill)

    flavor_words = significant_words(jd.role_flavor)
    tag_words = significant_words(" ".join(tags))
    overlap = flavor_words & tag_words
    score += weights.role_flavor_overlap * len(overlap)
    matched.extend(sorted(overlap))

    if project.dates.is_current:
        score += weights.current_role

    scored_bullets = []
    for index, text in enumerate(project.bullets):
        bullet_score, bullet_matched = score_bullet(text, project, jd, weights)
        scored_bullets.append(
            ScoredBullet(
                project_id=project.id,
                index=index,
                text=text,
                score=round(bullet_score, 4),
                matched=bullet_matched,
            )
        )
    # Ties fall back to the order written in projects.yaml, so the same inputs
    # always produce the same resume.
    scored_bullets.sort(key=lambda bullet: (-bullet.score, bullet.index))

    return ScoredProject(
        project=project,
        score=round(score, 4),
        matched=_dedupe(matched),
        bullets=scored_bullets,
    )


def rank_projects(
    store: ResumeStore,
    jd: ParsedJobDescription,
    weights: ScoringWeights | None = None,
) -> list[ScoredProject]:
    """Score every project and sort. Pure; no LLM involved."""

    weights = weights or ScoringWeights()
    scored = [score_project(project, jd, weights) for project in store.projects]
    order = {project.id: index for index, project in enumerate(store.projects)}
    scored.sort(
        key=lambda item: (
            not item.project.always_include,
            -item.score,
            order[item.id],
        )
    )
    return scored


def select(
    store: ResumeStore,
    jd: ParsedJobDescription,
    budget: SelectionBudget | None = None,
    weights: ScoringWeights | None = None,
    client: LLMClient | None = None,
) -> Selection:
    """Pick what goes on the resume.

    Deterministic by default. Passing `client` adds an LLM reordering pass over
    the already-fixed candidate set; it can change what is chosen from that set
    and in what order, and nothing else.
    """

    budget = budget or SelectionBudget()
    ranked = rank_projects(store, jd, weights)

    eligible = [
        item
        for item in ranked
        if item.bullets and (item.project.always_include or item.score >= budget.min_project_score)
    ]
    if not eligible:
        raise SelectionError(
            "no projects have any bullets to select from. Fill in the `bullets` "
            "lists in projects.yaml — the tool can only choose among sentences "
            "you have already written."
        )

    chosen = eligible[: budget.max_projects]

    if client is not None:
        ordered = _llm_rerank(chosen, jd, budget, client)
        return Selection(projects=ordered, ranked=ranked, reranked_by_llm=True)

    return Selection(projects=_allocate(chosen, budget), ranked=ranked)


def _allocate(
    chosen: list[ScoredProject],
    budget: SelectionBudget,
) -> list[SelectedProject]:
    """Round-robin bullets across projects so one project cannot eat the budget."""

    taken: dict[str, list[ScoredBullet]] = {item.id: [] for item in chosen}
    remaining = budget.max_bullets
    for depth in range(budget.max_bullets_per_project):
        if remaining <= 0:
            break
        for item in chosen:
            if remaining <= 0:
                break
            if depth < len(item.bullets):
                taken[item.id].append(item.bullets[depth])
                remaining -= 1

    return [
        SelectedProject(project=item.project, score=item.score, bullets=taken[item.id])
        for item in chosen
        if taken[item.id]
    ]


RERANK_SYSTEM = """\
You are ranking a fixed list of resume bullets by relevance to a job posting.

Rules you must follow exactly:
- You may ONLY return items from the candidate list, identified by their \
project_id and bullet_index. Never invent an id or an index.
- You are selecting and ordering. You are not writing. Do not return bullet \
text, edits, suggestions, or commentary of any kind.
- Do not return the same item twice.
- Order the items you return from most relevant to least relevant.
- Return only JSON. No prose, no code fences.
"""

RERANK_USER = """\
Job posting summary:
- role: {role_flavor}
- seniority: {seniority}
- required skills: {required}
- preferred skills: {preferred}

Candidate bullets:
{candidates}

Select at most {limit} items, at most {per_project} from any single project, \
ordered most relevant first.

Return JSON of exactly this shape:
{{"selected": [{{"project_id": "<id from the list>", "bullet_index": <int from the list>}}]}}
"""


def _llm_rerank(
    chosen: list[ScoredProject],
    jd: ParsedJobDescription,
    budget: SelectionBudget,
    client: LLMClient,
) -> list[SelectedProject]:
    # The candidate set is frozen here, before the model is contacted.
    candidates: dict[tuple[str, int], ScoredBullet] = {
        (bullet.project_id, bullet.index): bullet
        for item in chosen
        for bullet in item.bullets
    }

    def check_subset(response: RerankResponse) -> None:
        seen: set[tuple[str, int]] = set()
        for ref in response.selected:
            key = (ref.project_id, ref.bullet_index)
            if key not in candidates:
                raise ValueError(
                    f"item {{project_id: {ref.project_id!r}, bullet_index: "
                    f"{ref.bullet_index}}} is not in the candidate list. Only "
                    "return items that appear in it."
                )
            if key in seen:
                raise ValueError(
                    f"item {ref.project_id!r}/{ref.bullet_index} was returned twice."
                )
            seen.add(key)

    response = request_validated_json(
        client=client,
        system=RERANK_SYSTEM,
        user=RERANK_USER.format(
            role_flavor=jd.role_flavor,
            seniority=jd.seniority,
            required=", ".join(jd.required_skills) or "(none stated)",
            preferred=", ".join(jd.preferred_skills) or "(none stated)",
            candidates=_render_candidates(chosen),
            limit=budget.max_bullets,
            per_project=budget.max_bullets_per_project,
        ),
        schema=RerankResponse,
        extra_validation=check_subset,
    )

    per_project: dict[str, list[ScoredBullet]] = {item.id: [] for item in chosen}
    total = 0
    for ref in response.selected:
        if total >= budget.max_bullets:
            break
        bucket = per_project[ref.project_id]
        if len(bucket) >= budget.max_bullets_per_project:
            continue
        # Text comes from the store, never from the response.
        bucket.append(candidates[(ref.project_id, ref.bullet_index)])
        total += 1

    if total == 0:
        raise SelectionError(
            "the rerank returned nothing usable. Re-run without --llm-rank to use "
            "deterministic scoring."
        )

    selected = [
        SelectedProject(project=item.project, score=item.score, bullets=per_project[item.id])
        for item in chosen
        if per_project[item.id]
    ]
    # Projects follow the best rank their surviving bullets achieved.
    first_seen = {
        ref.project_id: position for position, ref in reversed(list(enumerate(response.selected)))
    }
    selected.sort(key=lambda item: first_seen.get(item.project.id, len(response.selected)))
    return selected


def _render_candidates(chosen: list[ScoredProject]) -> str:
    lines: list[str] = []
    for item in chosen:
        technologies = ", ".join(item.project.technologies) or "n/a"
        lines.append(f'project_id: {item.id} | {item.project.name} | tech: {technologies}')
        for bullet in sorted(item.bullets, key=lambda b: b.index):
            lines.append(f"  bullet_index {bullet.index}: {bullet.text}")
    return "\n".join(lines)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result
