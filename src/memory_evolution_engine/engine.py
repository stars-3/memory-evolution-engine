"""Public orchestration API for experience lifecycle operations."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
from typing import Callable

from .models import (
    Experience,
    ExperienceKind,
    ImpactEvidence,
    ImpactSource,
    MaintenanceCandidate,
    MaintenanceReport,
)
from .policy import LifecyclePolicy
from .storage import SQLiteStorage


class MemoryEngine:
    def __init__(
        self,
        storage: SQLiteStorage,
        *,
        policy: LifecyclePolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.storage = storage
        self.policy = policy or LifecyclePolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def remember(self, experience: Experience) -> Experience:
        if experience.impact != 0:
            raise ValueError("impact is derived from ImpactEvidence; new experiences must start at 0")
        self.storage.add(experience)
        return experience

    def get(self, experience_id: str, *, scope: str) -> Experience | None:
        return self.storage.get(experience_id, scope=scope)

    def recall(
        self,
        *,
        scope: str,
        text: str = "",
        kind: ExperienceKind | None = None,
        limit: int = 20,
        include_related_lessons: bool = False,
    ) -> list[Experience]:
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        if text:
            found = self.storage.search(scope=scope, text=text, kind=kind, limit=None)
        else:
            found = self.storage.list_experiences(scope=scope, kind=kind)
        now = self._clock()
        found = [
            item for item in found
            if item.expires_at is None or item.expires_at > now
        ]
        found.sort(key=lambda item: self.policy.effective_score(item, now), reverse=True)
        found = found[:limit]
        if include_related_lessons:
            ids = {item.id for item in found}
            failures = {item.id for item in found if item.kind is ExperienceKind.FAILURE_PATTERN}
            if failures:
                for lesson in self.storage.list_experiences(scope=scope, kind=ExperienceKind.LESSON):
                    if (failures.intersection(lesson.related_to) and lesson.id not in ids
                            and (lesson.expires_at is None or lesson.expires_at > now)):
                        found.append(lesson)
                        ids.add(lesson.id)
        return found

    def add_impact_evidence(self, experience_id: str, evidence: ImpactEvidence, *, scope: str) -> Experience:
        with self.storage.transaction():
            item = self._require(experience_id, scope=scope)
            self.storage._add_evidence(experience_id, evidence, scope=scope)
            evidences = self.storage.list_evidence(experience_id, scope=scope)
            # Saturating evidence aggregation is deterministic and remains interpretable.
            impact = min(1.0, sum(e.weight for e in evidences) * self.policy.evidence_impact_step)
            updated = item.model_copy(update={"impact": impact, "updated_at": self._clock()})
            self.storage._update_impact(updated)
            return updated

    def reinforce(
        self,
        experience_id: str,
        *,
        scope: str,
    ) -> Experience:
        with self.storage.transaction():
            item = self._require(experience_id, scope=scope)
            now = self._clock()
            current = self.policy.decayed_strength(item, now)
            updated = item.model_copy(update={
                "strength": min(1.0, current + self.policy.reinforcement_step),
                "updated_at": now,
                "last_reinforced_at": now,
                "reinforcement_count": item.reinforcement_count + 1,
            })
            self.storage.update(updated)
            return updated

    def maintain(self, *, scope: str, dry_run: bool = True) -> MaintenanceReport:
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be a boolean")
        with nullcontext() if dry_run else self.storage.transaction():
            now = self._clock()
            candidates: list[MaintenanceCandidate] = []
            archived: list[str] = []
            for item in self.storage.list_experiences(scope=scope):
                score = self.policy.effective_score(item, now)
                expired = item.expires_at is not None and item.expires_at <= now
                if expired or score < self.policy.archive_below:
                    reason = "expired" if expired else "below_archive_threshold"
                    candidates.append(MaintenanceCandidate(item.id, item.content, score, reason))
                    if not dry_run:
                        self.storage.update(item.model_copy(update={"status": "archived", "updated_at": now}))
                        archived.append(item.id)
            return MaintenanceReport(scope, dry_run, tuple(candidates), tuple(archived))

    def forget(self, experience_id: str, *, scope: str) -> None:
        self.storage.delete(experience_id, scope=scope)

    def _require(self, experience_id: str, *, scope: str) -> Experience:
        item = self.storage.get(experience_id, scope=scope)
        if item is None:
            raise KeyError(experience_id)
        if item.status != "active":
            raise ValueError("only active experiences can be changed")
        return item
