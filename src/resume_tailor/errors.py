"""Exception types.

Every failure the user can plausibly cause is one of these, carrying a message
that says what to fix. The CLI prints them without a traceback.
"""


class ResumeTailorError(Exception):
    """Base class for expected, user-actionable failures."""


class StoreError(ResumeTailorError):
    """`projects.yaml` is missing, unparseable, or does not match the schema."""


class LLMError(ResumeTailorError):
    """The LLM was unreachable, or kept returning output that failed validation."""


class SelectionError(ResumeTailorError):
    """Selection produced nothing usable, or tried to leave the candidate set."""


class TemplateError(ResumeTailorError):
    """The LaTeX template could not be rendered."""


class CompileError(ResumeTailorError):
    """Tectonic is missing or the document failed to compile."""
