# Memory Evolution Engine

**An experience evolution engine for AI agents.** Learn what is worth remembering from failures, successes, feedback, and repeated use.

Agents often repeat mistakes because useful experience is buried in conversation history. The goal is to help an Agent decide what deserves long-term retention based on success, failure, feedback, and validation. `failure_pattern` and `lesson` are core experience types: one records how a task failed, the other what behavior should change. Memory Evolution Engine tracks evidence of value, recent activity, decay, and candidates for archival.

Phase 1 is a standalone library. It does not run agents, call models, or require a vector database.

## Install

```bash
pip install .
```

## Quick start

```python
from memory_evolution_engine import (
    Experience,
    ExperienceKind,
    ImpactEvidence,
    ImpactSource,
    MemoryEngine,
    SQLiteStorage,
)

with SQLiteStorage("agent-memory.db") as storage:
    memory = MemoryEngine(storage)
    failure = memory.remember(Experience(
        scope="agent:payments",
        kind=ExperienceKind.FAILURE_PATTERN,
        content="Retrying a charge without an idempotency key caused a duplicate payment.",
    ))
    lesson = memory.remember(Experience(
        scope="agent:payments",
        kind=ExperienceKind.LESSON,
        content="Check idempotency before retrying a charge.",
        related_to=(failure.id,),
    ))

    # Record why the lesson mattered after it prevents a repeated failure.
    memory.add_impact_evidence(
        lesson.id,
        ImpactEvidence(
            source=ImpactSource.PREVENTED_FAILURE,
            description="Prevented a duplicate charge on a retry.",
            reference="payment-task-42",
        ),
        scope="agent:payments",
    )
    memory.reinforce(lesson.id, scope="agent:payments")

    # Maintenance is a report by default. Nothing is archived in dry-run mode.
    report = memory.maintain(scope="agent:payments")
    print(report.candidates)
```

## Core concepts

- `Experience` stores a scoped fact, preference, procedure, `failure_pattern`, or `lesson`.
- `ImpactEvidence` records why an experience matters: user feedback, prevented failure, repeated success, or manual confirmation.
- `strength` measures recent activity and changes through reinforcement and time decay. Reinforcement does not change impact.
- `impact` measures long-term value and changes only when `add_impact_evidence()` accepts new evidence. `confidence` measures how trustworthy the content is.
- `maintain()` is non-destructive by default. Pass `dry_run=False` to archive candidates explicitly.
- SQLite and Python's standard library are the only runtime requirements.

## Development

```bash
pip install -e ".[test]"
pytest
```

This package is in early development; the v0.1 API is subject to change.

## Lifecycle semantics and limits

Effective score is `confidence * (impact_prior + (1 - impact_prior) * impact) * decayed_strength`.
The configurable prior defaults to 0.5: a new experience with default strength and confidence
scores 0.25 instead of zero. This prior expresses uncertainty, not observed impact evidence.
Set `impact_prior=0` to use the original purely multiplicative rule.
Decay uses `strength * 2 ** (-elapsed_days / half_life_days)`, anchored to the last reinforcement.
Reinforcement adds its step to the decayed strength, capped at one; it need not exceed
historical stored strength. Evidence impact is `min(1, sum(weights) * evidence_impact_step)`.
Adding evidence recalculates impact from evidence; `remember()` requires initial impact to be zero.

Engine reinforcement, evidence updates, and explicit archival batches are atomic. Failed
operations roll back all writes. Evidence IDs are stable. A unique fingerprint per experience
also rejects repeated `source + reference`; without a reference it rejects repeated
`source + normalized description`. Duplicate submissions raise `sqlite3.IntegrityError`
and do not change impact. A reference should identify the actual task or event; this simple
rule cannot prove two different references describe the same real-world event.
Use engine operations to keep evidence and the cached impact score consistent; storage is
also exposed as a low-level repository and does not run lifecycle policy by itself. Evidence
can only be added through the engine's public API in v0.1. There is no public evidence deletion
operation; `forget()` removes the complete experience and its evidence in one transaction.

Recall filters expired records and ranks all matching records before applying `limit`.
Text search is literal substring matching (SQLite's built-in ASCII case folding), not semantic
search. Optional related lessons are appended after the primary result limit and therefore
may increase the returned count. Archived and expired lessons are excluded.
This implementation scans matching scope records in memory; large-scale retrieval is deferred.

Every ID-based engine and storage method requires `scope`; an ID from another scope is not
returned or changed. This is data isolation, not caller authentication. Use one SQLiteStorage
connection per thread. Lifecycle writes use transactions and roll back on failure. v0.1 is
intended for single-process, low-concurrency use; it does not promise high-throughput writes
across multiple workers. The SQLite schema uses `PRAGMA user_version=2`; opening a newer
schema fails explicitly. Older v0.1 databases are upgraded in place with an evidence
fingerprint index. Back up an existing database before an application upgrade.
If old data contains multiple evidence rows with the same new fingerprint, startup fails
without deleting them; those records need manual review before migration can complete.

Frozen `Experience` prevents field reassignment, but `metadata` remains a mutable JSON
dictionary, including nested containers. Do not mutate it after storing; use a validated copy
and a storage update when changing metadata. Store sensitive data only under application policy.

Local validation uses `python -m pytest`. Build with
`python -m pip wheel . --no-deps --wheel-dir dist`, then install the generated wheel in a
fresh virtual environment. Building requires setuptools; the installed package has no runtime dependencies.
