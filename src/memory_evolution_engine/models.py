"""Validated data objects used by the lifecycle engine."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
import math
from typing import Any
from uuid import uuid4


class ExperienceKind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    PROCEDURE = "procedure"
    EVENT = "event"
    FAILURE_PATTERN = "failure_pattern"
    LESSON = "lesson"


class ImpactSource(StrEnum):
    USER_FEEDBACK = "user_feedback"
    PREVENTED_FAILURE = "prevented_failure"
    REPEATED_SUCCESS = "repeated_success"
    MANUAL_CONFIRMATION = "manual_confirmation"


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ImpactEvidence:
    source: ImpactSource
    description: str
    weight: float = 0.5
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reference: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", ImpactSource(self.source))
        if not self.description.strip():
            raise ValueError("evidence description cannot be empty")
        if not math.isfinite(self.weight) or not 0 <= self.weight <= 1:
            raise ValueError("evidence weight must be between 0 and 1")
        _aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class Experience:
    scope: str
    content: str
    kind: ExperienceKind = ExperienceKind.FACT
    id: str = field(default_factory=lambda: str(uuid4()))
    tags: tuple[str, ...] = ()
    source: str | None = None
    confidence: float = 1.0
    impact: float = 0.0
    strength: float = 0.5
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_reinforced_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reinforcement_count: int = 0
    status: str = "active"
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    related_to: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ExperienceKind(self.kind))
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "related_to", tuple(self.related_to))
        if not self.scope.strip():
            raise ValueError("scope cannot be empty")
        if not self.content.strip():
            raise ValueError("content cannot be empty")
        for name in ("confidence", "impact", "strength"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.reinforcement_count < 0:
            raise ValueError("reinforcement_count cannot be negative")
        if self.status not in {"active", "archived", "superseded", "deleted"}:
            raise ValueError("invalid experience status")
        for name in ("created_at", "updated_at", "last_reinforced_at"):
            _aware(getattr(self, name), name)
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")

    def model_copy(self, *, update: dict[str, Any]) -> "Experience":
        """Return a validated immutable copy, similar to Pydantic's model_copy."""
        return replace(self, **update)


@dataclass(frozen=True, slots=True)
class MaintenanceCandidate:
    id: str
    content: str
    effective_score: float
    reason: str


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    scope: str
    dry_run: bool
    candidates: tuple[MaintenanceCandidate, ...]
    archived_ids: tuple[str, ...] = ()

