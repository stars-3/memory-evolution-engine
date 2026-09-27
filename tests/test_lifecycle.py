from datetime import datetime, timedelta, timezone

from memory_evolution_engine.engine import MemoryEngine
from memory_evolution_engine.models import Experience, ExperienceKind, ImpactEvidence, ImpactSource
from memory_evolution_engine.policy import LifecyclePolicy
from memory_evolution_engine.storage import SQLiteStorage


def test_reinforce_increases_strength_only(tmp_path):
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    with SQLiteStorage(tmp_path / "memory.db") as storage:
        engine = MemoryEngine(storage, clock=lambda: now)
        item = engine.remember(Experience(scope="agent:a", content="Check idempotency", kind=ExperienceKind.LESSON, strength=0.3))
        reinforced = engine.reinforce(item.id, scope="agent:a")

        assert reinforced.strength > item.strength
        assert reinforced.reinforcement_count == 1
        assert storage.list_evidence(item.id, scope="agent:a") == []
        assert reinforced.impact == item.impact


def test_decay_is_deterministic_and_non_mutating(tmp_path):
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    item = Experience(scope="agent:a", content="A lesson", strength=1, last_reinforced_at=now - timedelta(days=30))
    policy = LifecyclePolicy(half_life_days=30)
    assert policy.decayed_strength(item, now) == 0.5
    assert item.strength == 1


def test_maintain_dry_run_reports_without_mutating(tmp_path):
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    with SQLiteStorage(tmp_path / "memory.db") as storage:
        engine = MemoryEngine(storage, policy=LifecyclePolicy(half_life_days=1, archive_below=0.4), clock=lambda: now)
        item = engine.remember(Experience(scope="agent:a", content="Old lesson", strength=0.5, last_reinforced_at=now - timedelta(days=4)))

        report = engine.maintain(scope="agent:a")

        assert report.dry_run is True
        assert [candidate.id for candidate in report.candidates] == [item.id]
        assert storage.get(item.id, scope="agent:a").status == "active"


def test_maintain_can_archive_only_when_explicit(tmp_path):
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    with SQLiteStorage(tmp_path / "memory.db") as storage:
        engine = MemoryEngine(storage, policy=LifecyclePolicy(half_life_days=1, archive_below=0.4), clock=lambda: now)
        item = engine.remember(Experience(scope="agent:a", content="Old lesson", strength=0.5, last_reinforced_at=now - timedelta(days=4)))
        engine.maintain(scope="agent:a", dry_run=False)
        assert storage.get(item.id, scope="agent:a").status == "archived"


def test_failure_pattern_recall_can_include_linked_lesson(tmp_path):
    with SQLiteStorage(tmp_path / "memory.db") as storage:
        engine = MemoryEngine(storage)
        failure = engine.remember(Experience(scope="agent:a", content="Retrying charge caused duplicate payment", kind=ExperienceKind.FAILURE_PATTERN))
        lesson = engine.remember(Experience(scope="agent:a", content="Check idempotency before retry", kind=ExperienceKind.LESSON, related_to=(failure.id,)))
        results = engine.recall(scope="agent:a", text="charge", include_related_lessons=True)
        assert [x.id for x in results] == [failure.id, lesson.id]

