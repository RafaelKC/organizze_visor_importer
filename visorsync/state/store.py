"""State store local (SQLite): mapeamento organizze_id <-> visor_id.

Este é o único lugar que sabe "o que este script criou no Visor". O comando
`reset-visor` depende inteiramente destes registros para saber o que pode
apagar com segurança — nunca tocar em algo que não está aqui.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_type      TEXT NOT NULL,
    organizze_id     TEXT,
    visor_id         TEXT NOT NULL,
    content_hash     TEXT,
    last_synced_at   TEXT NOT NULL,
    created_by_tool  INTEGER NOT NULL DEFAULT 1,
    extra_json       TEXT,
    PRIMARY KEY (entity_type, visor_id)
);
CREATE INDEX IF NOT EXISTS idx_entities_organizze_id ON entities (entity_type, organizze_id);
"""


@dataclass(frozen=True)
class EntityRecord:
    entity_type: str
    organizze_id: Optional[str]
    visor_id: str
    content_hash: Optional[str]
    last_synced_at: str
    created_by_tool: bool
    extra_json: Optional[str]


class StateStore:
    """Wrapper fino sobre o SQLite usado como state store do visorsync."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def upsert(
        self,
        entity_type: str,
        visor_id: str,
        organizze_id: Optional[str] = None,
        content_hash: Optional[str] = None,
        created_by_tool: bool = True,
        extra_json: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO entities
                    (entity_type, organizze_id, visor_id, content_hash,
                     last_synced_at, created_by_tool, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (entity_type, visor_id) DO UPDATE SET
                    organizze_id = excluded.organizze_id,
                    content_hash = excluded.content_hash,
                    last_synced_at = excluded.last_synced_at,
                    created_by_tool = excluded.created_by_tool,
                    extra_json = excluded.extra_json
                """,
                (
                    entity_type,
                    organizze_id,
                    visor_id,
                    content_hash,
                    now,
                    1 if created_by_tool else 0,
                    extra_json,
                ),
            )

    def get_by_organizze_id(self, entity_type: str, organizze_id: str) -> Optional[EntityRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? AND organizze_id = ?",
                (entity_type, organizze_id),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_visor_id(self, entity_type: str, visor_id: str) -> Optional[EntityRecord]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? AND visor_id = ?",
                (entity_type, visor_id),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_by_type(self, entity_type: str) -> list[EntityRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE entity_type = ? ORDER BY last_synced_at",
                (entity_type,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_all(self) -> list[EntityRecord]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM entities ORDER BY entity_type, last_synced_at").fetchall()
        return [self._row_to_record(r) for r in rows]

    def delete(self, entity_type: str, visor_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM entities WHERE entity_type = ? AND visor_id = ?",
                (entity_type, visor_id),
            )

    def needs_resync(self, entity_type: str, organizze_id: str, content_hash: str) -> bool:
        """True se a entidade não existe ainda, ou se o conteúdo mudou desde o último sync."""
        existing = self.get_by_organizze_id(entity_type, organizze_id)
        return existing is None or existing.content_hash != content_hash

    def wipe(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM entities")

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> EntityRecord:
        return EntityRecord(
            entity_type=row["entity_type"],
            organizze_id=row["organizze_id"],
            visor_id=row["visor_id"],
            content_hash=row["content_hash"],
            last_synced_at=row["last_synced_at"],
            created_by_tool=bool(row["created_by_tool"]),
            extra_json=row["extra_json"],
        )
