from datetime import datetime, timezone

from memory_evolution_engine.models import Experience, ExperienceKind, ImpactEvidence, ImpactSource
from memory_evolution_engine.storage import SQLiteStorage
from memory_evolution_engine.engine import MemoryEngine


def test_sqlite_round_trip_preserves_experience_and_evidence(tmp_path):
    db = tmp_path / "memory.db"
    experience = Experience(scope="agent:a", content="Check idempotency", kind=ExperienceKind.LESSON)
    evidence = ImpactEvidence(source=ImpactSource.MANUAL_CONFIRMATION, description="Confirmed by operator")

    with SQLiteStorage(db) as storage:
        storage.add(experience)
        updated = MemoryEngine(storage).add_impact_evidence(experience.id, evidence, scope=experience.scope)

    with SQLiteStorage(db) as storage:
        loaded = storage.get(experience.id, scope=experience.scope)
        assert loaded == updated
        assert storage.list_evidence(experience.id, scope=experience.scope) == [evidence]


def test_storage_scopes_listing_and_text_search(tmp_path):
    with SQLiteStorage(tmp_path / "memory.db") as storage:
        a = Experience(scope="agent:a", content="Avoid retrying charge operations", kind=ExperienceKind.FAILURE_PATTERN)
        b = Experience(scope="agent:b", content="Avoid retrying charge operations")
        storage.add(a)
        storage.add(b)

        assert storage.list_experiences(scope="agent:a") == [a]
        assert storage.search(scope="agent:a", text="retrying") == [a]
        assert storage.search(scope="agent:a", text="retrying", kind=ExperienceKind.LESSON) == []


def test_storage_updates_and_deletes(tmp_path):
    with SQLiteStorage(tmp_path / "memory.db") as storage:
        experience = Experience(scope="user:1", content="Prefers concise answers")
        storage.add(experience)
        updated = experience.model_copy(update={"strength": 0.8})
        storage.update(updated)
        assert storage.get(experience.id, scope=experience.scope).strength == 0.8
        storage.delete(experience.id, scope=experience.scope)
        assert storage.get(experience.id, scope=experience.scope) is None

