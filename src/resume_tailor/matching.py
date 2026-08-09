"""Term normalization and matching.

Kept separate from scoring because this is the part that is easy to get subtly
wrong: "C" must not match "C++", "C#" must not match "C", and "JS" should match
"JavaScript". All of it is pure and deterministic, so it can be tested directly.
"""

from __future__ import annotations

import re

# Variants that should score as the same skill. Deliberately small and explicit:
# a fuzzy matcher here would make scores hard to explain, and an unexplainable
# score is one you cannot trust when deciding what goes on your resume.
ALIASES: dict[str, tuple[str, ...]] = {
    "javascript": ("js",),
    "typescript": ("ts",),
    "postgresql": ("postgres", "psql"),
    "kubernetes": ("k8s",),
    "microsoft sql server": ("mssql", "sql server", "t-sql"),
    "c#": ("csharp", "c sharp", ".net"),
    "c++": ("cpp", "cplusplus"),
    "continuous integration": ("ci", "ci/cd"),
    "machine learning": ("ml",),
    "amazon web services": ("aws",),
    "google cloud platform": ("gcp", "gke"),
    "user interface": ("ui",),
    "object-oriented programming": ("oop", "object oriented"),
}

# Words that match everything and mean nothing. Dropped from role_flavor before
# it is used for semantic overlap.
STOPWORDS = frozenset(
    """
    a an and the or of for to in on with using strong excellent good ability
    experience knowledge skills skill work working role position team teams
    candidate candidates you your our we is are be been being will must should
    plus preferred required nice have has related similar etc engineer
    engineering software developer development
    """.split()
)

# Characters that can be part of a technology name. Used to build match
# boundaries so "C" does not match inside "C++" and "R" does not match "React".
_TERM_CHARS = r"a-z0-9+#\."


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def variants(term: str) -> set[str]:
    """A term plus its known aliases, in both directions."""

    key = normalize(term)
    forms = {key}
    for canonical, aliases in ALIASES.items():
        if key == canonical or key in aliases:
            forms.add(canonical)
            forms.update(aliases)
    return {form for form in forms if form}


def term_matches(term: str, text: str) -> bool:
    """True if `term` (or an alias) appears in `text` as a whole term.

    Boundaries treat `+`, `#`, and `.` as part of a term, which is the whole
    point: `C` in "C++ developer" is not a match for C, and `R` in "React" is
    not a match for R.
    """

    haystack = normalize(text)
    if not haystack:
        return False
    for form in variants(term):
        pattern = rf"(?<![{_TERM_CHARS}]){re.escape(form)}(?![{_TERM_CHARS}])"
        if re.search(pattern, haystack):
            return True
    return False


def matches_any(term: str, texts: list[str]) -> bool:
    return any(term_matches(term, text) for text in texts)


def significant_words(text: str) -> set[str]:
    """Content words of a phrase, for loose semantic overlap on role flavor."""

    words = re.findall(rf"[{_TERM_CHARS}]+", normalize(text))
    return {word for word in words if len(word) > 2 and word not in STOPWORDS}
