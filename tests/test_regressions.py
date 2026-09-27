from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from memory_evolution_engine import *

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)

def item(**kwargs):
    return Experience(scope="a", content="lesson", created_at=NOW,
                      last_reinforced_at=NOW, **kwargs)

def test_new_experience_is_not_immediately_archivable():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        engine.remember(item())
        assert engine.maintain(scope="a").candidates == ()

def test_failed_reinforcement_does_not_write():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        x = engine.remember(item())
        with patch.object(store, "update", side_effect=RuntimeError("disk error")):
            with pytest.raises(RuntimeError):
                engine.reinforce(x.id, scope="a")
        assert store.get(x.id, scope="a") == x
        assert store.list_evidence(x.id, scope="a") == []

def test_failed_update_rolls_back_evidence():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        x = engine.remember(item())
        with patch.object(store, "_update_impact", side_effect=RuntimeError("disk error")):
            with pytest.raises(RuntimeError):
                engine.add_impact_evidence(x.id, ImpactEvidence(source="user_feedback", description="useful"), scope="a")
        assert store.list_evidence(x.id, scope="a") == []
        assert store.get(x.id, scope="a") == x

def test_recall_ranks_before_limit_and_filters_expiry():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        engine.remember(item(id="a"))
        best = engine.remember(item(id="b"))
        best = engine.add_impact_evidence(best.id, ImpactEvidence(source="user_feedback", description="useful", weight=1), scope="a")
        engine.remember(item(id="c", expires_at=NOW))
        assert engine.recall(scope="a", limit=1) == [best]
        assert engine.recall(scope="a", text="lesson", limit=1) == [best]

def test_search_treats_wildcards_as_text():
    with SQLiteStorage(":memory:") as store:
        store.add(item())
        assert store.search(scope="a", text="%") == []

@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_invalid_half_life(value):
    with pytest.raises(ValueError):
        LifecyclePolicy(half_life_days=value)

def test_enum_strings_are_normalized():
    assert item(kind="lesson").kind is ExperienceKind.LESSON
    assert ImpactEvidence(source="user_feedback", description="ok").source is ImpactSource.USER_FEEDBACK


def test_expired_related_lesson_is_excluded():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        failure = engine.remember(item(kind="failure_pattern"))
        engine.remember(item(kind="lesson", related_to=(failure.id,), expires_at=NOW))
        assert engine.recall(scope="a", kind=ExperienceKind.FAILURE_PATTERN,
                             include_related_lessons=True) == [failure]


def test_archive_batch_rolls_back_on_failure():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        first = engine.remember(item(id="a", expires_at=NOW))
        second = engine.remember(item(id="b", expires_at=NOW))
        original = store.update
        def fail_second(value):
            if value.id == second.id:
                raise RuntimeError("disk error")
            original(value)
        with patch.object(store, "update", side_effect=fail_second):
            with pytest.raises(RuntimeError):
                engine.maintain(scope="a", dry_run=False)
        assert store.get(first.id, scope="a").status == "active"
        assert store.get(second.id, scope="a").status == "active"


def test_duplicate_evidence_does_not_change_impact():
    import sqlite3
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        x = engine.remember(item())
        evidence = ImpactEvidence(source="user_feedback", description="ok")
        updated = engine.add_impact_evidence(x.id, evidence, scope="a")
        with pytest.raises(sqlite3.IntegrityError):
            engine.add_impact_evidence(x.id, evidence, scope="a")
        assert store.get(x.id, scope="a") == updated
        assert store.list_evidence(x.id, scope="a") == [evidence]


def test_dry_run_requires_boolean():
    with SQLiteStorage(":memory:") as store:
        with pytest.raises(ValueError):
            MemoryEngine(store).maintain(scope="a", dry_run=0)


def test_reinforce_does_not_change_impact_or_evidence():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store, clock=lambda: NOW)
        x = engine.remember(item())
        evidence = ImpactEvidence(source="prevented_failure", description="avoided repeat", reference="task-1")
        before = engine.add_impact_evidence(x.id, evidence, scope="a")
        for _ in range(3):
            after = engine.reinforce(x.id, scope="a")
        assert after.impact == before.impact
        assert store.list_evidence(x.id, scope="a") == [evidence]
        assert after.reinforcement_count == 3


def test_same_source_reference_or_description_cannot_double_count():
    import sqlite3
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store)
        x = engine.remember(item())
        first = ImpactEvidence(source="user_feedback", description="Useful", reference="task-1")
        before = engine.add_impact_evidence(x.id, first, scope="a")
        with pytest.raises(sqlite3.IntegrityError):
            engine.add_impact_evidence(x.id, ImpactEvidence(source="user_feedback", description="Different text", reference="task-1"), scope="a")
        assert engine.get(x.id, scope="a").impact == before.impact
        second = ImpactEvidence(source="manual_confirmation", description="Useful")
        engine.add_impact_evidence(x.id, second, scope="a")
        with pytest.raises(sqlite3.IntegrityError):
            engine.add_impact_evidence(x.id, ImpactEvidence(source="manual_confirmation", description=" useful "), scope="a")
        assert len(store.list_evidence(x.id, scope="a")) == 2


def test_scope_is_required_for_id_operations():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store)
        x = engine.remember(item())
        assert engine.get(x.id, scope="other") is None
        with pytest.raises(KeyError):
            engine.reinforce(x.id, scope="other")
        with pytest.raises(KeyError):
            engine.add_impact_evidence(x.id, ImpactEvidence(source="user_feedback", description="ok"), scope="other")
        engine.forget(x.id, scope="other")
        assert engine.get(x.id, scope="a") == x
        assert store.list_evidence(x.id, scope="other") == []
        with pytest.raises(TypeError):
            engine.get(x.id)
        engine.forget(x.id, scope="a")
        assert engine.get(x.id, scope="a") is None


def test_schema_version_and_reopen(tmp_path):
    import sqlite3
    path = tmp_path / "memory.db"
    with SQLiteStorage(path) as store:
        store.add(item())
        assert store._connection.execute("PRAGMA user_version").fetchone()[0] == 2
    with SQLiteStorage(path) as store:
        assert len(store.list_experiences(scope="a")) == 1
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="Unsupported"):
        SQLiteStorage(path)


def test_half_life_boundary():
    policy = LifecyclePolicy(half_life_days=30)
    x = Experience(scope="a", content="lesson", strength=1,
                   last_reinforced_at=NOW - timedelta(days=30))
    assert policy.decayed_strength(x, NOW) == 0.5
    assert policy.decayed_strength(x, NOW - timedelta(days=30)) == 1
    assert policy.decayed_strength(x, NOW - timedelta(days=31)) == 1


def test_impact_requires_evidence_at_creation():
    with SQLiteStorage(":memory:") as store:
        with pytest.raises(ValueError, match="ImpactEvidence"):
            MemoryEngine(store).remember(item(impact=0.7))


def test_legacy_schema_upgrades_without_losing_evidence(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    with SQLiteStorage(path) as store:
        x = item()
        store.add(x)
        original = ImpactEvidence(source="user_feedback", description="Useful", reference="task-7")
        MemoryEngine(store).add_impact_evidence(x.id, original, scope="a")
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX idx_evidence_fingerprint")
    conn.execute("ALTER TABLE impact_evidence DROP COLUMN fingerprint")
    conn.execute("PRAGMA user_version = 0")
    conn.close()
    with SQLiteStorage(path) as store:
        assert store._connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.list_evidence(x.id, scope="a") == [original]
        with pytest.raises(sqlite3.IntegrityError):
            MemoryEngine(store).add_impact_evidence(x.id, ImpactEvidence(source="user_feedback", description="Different", reference="task-7"), scope="a")


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_scores_reject_non_finite_values(value):
    with pytest.raises(ValueError):
        item(impact=value)
    with pytest.raises(ValueError):
        ImpactEvidence(source="user_feedback", description="ok", weight=value)
    with pytest.raises(ValueError):
        LifecyclePolicy(impact_prior=value)


def test_storage_cannot_change_impact_without_evidence():
    with SQLiteStorage(":memory:") as store:
        x = item()
        store.add(x)
        with pytest.raises(ValueError, match="impact"):
            store.update(x.model_copy(update={"impact": 0.9}))
        with pytest.raises(ValueError, match="impact"):
            store.add(item(id="other", impact=0.9))
        assert store.get(x.id, scope="a") == x


def test_evidence_changes_only_through_engine():
    with SQLiteStorage(":memory:") as store:
        engine = MemoryEngine(store)
        x = engine.remember(item())
        evidence = ImpactEvidence(source="user_feedback", description="Useful")
        assert not hasattr(store, "add_evidence")
        assert not hasattr(store, "delete_evidence")
        updated = engine.add_impact_evidence(x.id, evidence, scope="a")
        assert updated.impact > 0
        assert store.get(x.id, scope="a") == updated
        assert store.list_evidence(x.id, scope="a") == [evidence]
