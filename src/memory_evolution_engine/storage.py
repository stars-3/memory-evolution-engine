"""SQLite persistence for experiences and their impact provenance."""

from __future__ import annotations

import json
import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from functools import wraps

from .models import Experience, ExperienceKind, ImpactEvidence, ImpactSource


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _atomic(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.transaction():
            return method(self, *args, **kwargs)
    return wrapped


class SQLiteStorage:
    """Small synchronous SQLite repository; pass ':memory:' for ephemeral use."""

    def __init__(self, path: str | Path = "memory.db") -> None:
        self.path = str(path)
        self._connection = sqlite3.connect(self.path, isolation_level=None)
        self._transaction_depth = 0
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    def _initialize(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise RuntimeError(f"Unsupported SQLite schema version: {version}")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiences (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, content TEXT NOT NULL,
                kind TEXT NOT NULL, tags TEXT NOT NULL, source TEXT,
                confidence REAL NOT NULL, impact REAL NOT NULL, strength REAL NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_reinforced_at TEXT NOT NULL, reinforcement_count INTEGER NOT NULL,
                status TEXT NOT NULL, expires_at TEXT, metadata TEXT NOT NULL,
                related_to TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_experiences_scope_status
                ON experiences(scope, status);
            CREATE TABLE IF NOT EXISTS impact_evidence (
                id TEXT PRIMARY KEY, experience_id TEXT NOT NULL,
                source TEXT NOT NULL, description TEXT NOT NULL, weight REAL NOT NULL,
                observed_at TEXT NOT NULL, reference TEXT, fingerprint TEXT,
                FOREIGN KEY(experience_id) REFERENCES experiences(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_experience
                ON impact_evidence(experience_id);
            """
        )
        if version < 2:
            columns = {row[1] for row in self._connection.execute("PRAGMA table_info(impact_evidence)")}
            if "fingerprint" not in columns:
                self._connection.execute("ALTER TABLE impact_evidence ADD COLUMN fingerprint TEXT")
            rows = self._connection.execute("SELECT id, experience_id, source, description, reference FROM impact_evidence")
            for row in rows:
                fingerprint = self._fingerprint(row["source"], row["description"], row["reference"])
                self._connection.execute("UPDATE impact_evidence SET fingerprint=? WHERE id=?", (fingerprint, row["id"]))
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_fingerprint ON impact_evidence(experience_id, fingerprint)"
        )
        if version < 2:
            self._connection.execute("PRAGMA user_version = 2")

    @staticmethod
    def _fingerprint(source: str, description: str, reference: str | None) -> str:
        # A source/reference pair identifies an event. Without a reference, use
        # normalized source/description to stop a replay with a new random ID.
        parts = (source, reference.strip() if reference is not None else None)
        if not parts[1]:
            parts = (source, description.strip().casefold())
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()

    @contextmanager
    def transaction(self):
        """Serialize read-modify-write operations; nested calls use savepoints."""
        depth = self._transaction_depth
        savepoint = f"memory_tx_{depth}"
        self._connection.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
        self._transaction_depth += 1
        try:
            yield self
            self._connection.execute("COMMIT" if depth == 0 else f"RELEASE {savepoint}")
        except BaseException:
            if depth == 0:
                self._connection.rollback()
            else:
                self._connection.execute(f"ROLLBACK TO {savepoint}")
                self._connection.execute(f"RELEASE {savepoint}")
            raise
        finally:
            self._transaction_depth -= 1

    def __enter__(self) -> "SQLiteStorage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _values(item: Experience) -> tuple[object, ...]:
        return (
            item.id, item.scope, item.content, item.kind.value,
            json.dumps(item.tags), item.source, item.confidence, item.impact,
            item.strength, _dt(item.created_at), _dt(item.updated_at),
            _dt(item.last_reinforced_at), item.reinforcement_count, item.status,
            _dt(item.expires_at), json.dumps(item.metadata), json.dumps(item.related_to),
        )

    @staticmethod
    def _experience(row: sqlite3.Row) -> Experience:
        return Experience(
            id=row["id"], scope=row["scope"], content=row["content"],
            kind=ExperienceKind(row["kind"]), tags=tuple(json.loads(row["tags"])),
            source=row["source"], confidence=row["confidence"], impact=row["impact"],
            strength=row["strength"], created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            last_reinforced_at=_parse_dt(row["last_reinforced_at"]),
            reinforcement_count=row["reinforcement_count"], status=row["status"],
            expires_at=_parse_dt(row["expires_at"]), metadata=json.loads(row["metadata"]),
            related_to=tuple(json.loads(row["related_to"])),
        )

    @staticmethod
    def _evidence(row: sqlite3.Row) -> ImpactEvidence:
        return ImpactEvidence(
            id=row["id"], source=ImpactSource(row["source"]),
            description=row["description"], weight=row["weight"],
            observed_at=_parse_dt(row["observed_at"]), reference=row["reference"],
        )

    @_atomic
    def add(self, item: Experience) -> None:
        if item.impact != 0:
            raise ValueError("impact must start at zero and be derived from evidence")
        self._connection.execute(
            "INSERT INTO experiences VALUES (" + ",".join("?" for _ in range(17)) + ")",
            self._values(item),
        )

    @_atomic
    def update(self, item: Experience) -> None:
        current = self.get(item.id, scope=item.scope)
        if current is None:
            raise KeyError(item.id)
        if item.impact != current.impact:
            raise ValueError("impact can only be changed through evidence")
        self._write_update(item)

    def _write_update(self, item: Experience) -> None:
        values = self._values(item)
        assignments = "scope=?, content=?, kind=?, tags=?, source=?, confidence=?, impact=?, strength=?, created_at=?, updated_at=?, last_reinforced_at=?, reinforcement_count=?, status=?, expires_at=?, metadata=?, related_to=?"
        cursor = self._connection.execute(
            f"UPDATE experiences SET {assignments} WHERE id=? AND scope=?", values[1:] + (item.id, item.scope)
        )
        if cursor.rowcount == 0:
            raise KeyError(item.id)

    def _update_impact(self, item: Experience) -> None:
        """Used by the engine inside its evidence transaction."""
        self._write_update(item)

    def get(self, experience_id: str, *, scope: str) -> Experience | None:
        row = self._connection.execute("SELECT * FROM experiences WHERE id=? AND scope=?", (experience_id, scope)).fetchone()
        return self._experience(row) if row else None

    def list_experiences(
        self, *, scope: str, kind: ExperienceKind | None = None, status: str = "active"
    ) -> list[Experience]:
        query = "SELECT * FROM experiences WHERE scope=? AND status=?"
        params: list[object] = [scope, status]
        if kind is not None:
            query += " AND kind=?"
            params.append(ExperienceKind(kind).value)
        query += " ORDER BY created_at, id"
        return [self._experience(row) for row in self._connection.execute(query, params)]

    def search(
        self, *, scope: str, text: str, kind: ExperienceKind | None = None, limit: int | None = 20
    ) -> list[Experience]:
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer")
        query = "SELECT * FROM experiences WHERE scope=? AND status='active' AND instr(lower(content), lower(?)) > 0"
        params: list[object] = [scope, text]
        if kind is not None:
            query += " AND kind=?"
            params.append(ExperienceKind(kind).value)
        query += " ORDER BY created_at DESC, id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [self._experience(row) for row in self._connection.execute(query, params)]

    @_atomic
    def _add_evidence(self, experience_id: str, evidence: ImpactEvidence, *, scope: str) -> None:
        if self.get(experience_id, scope=scope) is None:
            raise KeyError(experience_id)
        self._connection.execute(
            "INSERT INTO impact_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (evidence.id, experience_id, evidence.source.value, evidence.description,
             evidence.weight, _dt(evidence.observed_at), evidence.reference,
             self._fingerprint(evidence.source.value, evidence.description, evidence.reference)),
        )

    def list_evidence(self, experience_id: str, *, scope: str) -> list[ImpactEvidence]:
        rows = self._connection.execute(
            "SELECT e.* FROM impact_evidence e JOIN experiences x ON x.id=e.experience_id "
            "WHERE e.experience_id=? AND x.scope=? ORDER BY e.observed_at, e.id",
            (experience_id, scope),
        )
        return [self._evidence(row) for row in rows]

    @_atomic
    def delete(self, experience_id: str, *, scope: str) -> None:
        self._connection.execute("DELETE FROM experiences WHERE id=? AND scope=?", (experience_id, scope))


