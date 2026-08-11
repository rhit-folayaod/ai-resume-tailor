"""Empty starter content for a brand-new hosted account.

Friends get structure they can fill in — never someone else's bullets.
"""

from __future__ import annotations

from .models import Profile, ResumeStore


def empty_store(*, email: str = "", name: str = "") -> ResumeStore:
    """Minimal valid store for first login."""

    return ResumeStore(
        profile=Profile(name=name or "Your Name", email=email, phone="", links=[]),
        education=[],
        skills=[],
        projects=[],
        leadership=[],
    )
