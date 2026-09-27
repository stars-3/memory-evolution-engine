from datetime import datetime, timezone

import pytest

from memory_evolution_engine.models import (
    Experience,
    ExperienceKind,
    ImpactEvidence,
    ImpactSource,
)


def test_experience_accepts_failure_pattern_and_lesson():
    failure = Experience(scope="agent:a", content="Retried a non-idempotent charge", kind=ExperienceKind.FAILURE_PATTERN)
    lesson = Experience(scope="agent:a", content="Check operation idempotency before retrying", kind=ExperienceKind.LESSON)

    assert failure.kind is ExperienceKind.FAILURE_PATTERN
    assert lesson.kind is ExperienceKind.LESSON
    assert failure.id != lesson.id


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_experience_rejects_out_of_range_scores(score):
    with pytest.raises(ValueError):
        Experience(scope="user:1", content="A lesson", impact=score)


def test_experience_requires_scope_and_content():
    with pytest.raises(ValueError):
        Experience(scope="", content="fact")
    with pytest.raises(ValueError):
        Experience(scope="user:1", content=" ")


def test_impact_evidence_records_provenance_and_validates_weight():
    evidence = ImpactEvidence(source=ImpactSource.PREVENTED_FAILURE, description="Avoided duplicate charge")
    assert evidence.source is ImpactSource.PREVENTED_FAILURE
    assert evidence.observed_at.tzinfo is not None

    with pytest.raises(ValueError):
        ImpactEvidence(source=ImpactSource.USER_FEEDBACK, description="Useful", weight=2)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        Experience(scope="user:1", content="x", created_at=datetime(2026, 1, 1))

