"""Deterministic, configurable lifecycle scoring policy."""

from dataclasses import dataclass
from datetime import datetime
import math

from .models import Experience, _aware


@dataclass(frozen=True, slots=True)
class LifecyclePolicy:
    half_life_days: float = 90.0
    archive_below: float = 0.15
    reinforcement_step: float = 0.1
    evidence_impact_step: float = 0.1
    impact_prior: float = 0.5

    def __post_init__(self) -> None:
        if not math.isfinite(self.half_life_days) or self.half_life_days <= 0:
            raise ValueError("half_life_days must be positive")
        for name in ("archive_below", "reinforcement_step", "evidence_impact_step", "impact_prior"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")

    def decayed_strength(self, experience: Experience, now: datetime) -> float:
        _aware(now, "now")
        age_seconds = max(0.0, (now - experience.last_reinforced_at).total_seconds())
        age_days = age_seconds / 86400
        return experience.strength * math.pow(2.0, -age_days / self.half_life_days)

    def effective_score(self, experience: Experience, now: datetime) -> float:
        impact_factor = self.impact_prior + (1 - self.impact_prior) * experience.impact
        return experience.confidence * impact_factor * self.decayed_strength(experience, now)

