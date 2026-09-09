"""SQLite persistence for archived gifticon messages."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Gifticon:
    source_message_id: int
    archive_message_id: int
    description: str
    status: str
    used_by: str | None
    used_at: str | None
    expiry_date: str | None


class GifticonStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gifticons (
                        source_message_id INTEGER PRIMARY KEY,
                        archive_message_id INTEGER NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'available'
                            CHECK(status IN ('available', 'used')),
                        used_by TEXT,
                        used_at TEXT,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                columns = {row[1] for row in conn.execute("PRAGMA table_info(gifticons)")}
                if "expiry_date" not in columns:
                    conn.execute("ALTER TABLE gifticons ADD COLUMN expiry_date TEXT")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS expiry_notifications (
                        source_message_id INTEGER NOT NULL,
                        notice_date TEXT NOT NULL,
                        PRIMARY KEY (source_message_id, notice_date)
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    async def add(
        self, source_id: int, archive_id: int, description: str, expiry_date: str | None = None
    ) -> None:
        await asyncio.to_thread(self._add_sync, source_id, archive_id, description, expiry_date)

    def _add_sync(
        self, source_id: int, archive_id: int, description: str, expiry_date: str | None
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO gifticons
                        (source_message_id, archive_message_id, description, expiry_date)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_message_id) DO UPDATE SET
                        archive_message_id = excluded.archive_message_id,
                        description = excluded.description,
                        expiry_date = excluded.expiry_date
                    """,
                    (source_id, archive_id, description, expiry_date),
                )
                conn.commit()
            finally:
                conn.close()

    async def get_by_source(self, source_id: int) -> Gifticon | None:
        return await asyncio.to_thread(self._get_by_source_sync, source_id)

    def _get_by_source_sync(self, source_id: int) -> Gifticon | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM gifticons WHERE source_message_id = ?", (source_id,)
                ).fetchone()
            finally:
                conn.close()
        return self._row_to_gifticon(row)

    async def get_by_archive(self, archive_id: int) -> Gifticon | None:
        return await asyncio.to_thread(self._get_by_archive_sync, archive_id)

    def _get_by_archive_sync(self, archive_id: int) -> Gifticon | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM gifticons WHERE archive_message_id = ?", (archive_id,)
                ).fetchone()
            finally:
                conn.close()
        return self._row_to_gifticon(row)

    async def mark_used(self, source_id: int, used_by: str) -> bool:
        return await asyncio.to_thread(self._mark_used_sync, source_id, used_by)

    def _mark_used_sync(self, source_id: int, used_by: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE gifticons
                    SET status = 'used', used_by = ?, used_at = datetime('now')
                    WHERE source_message_id = ? AND status = 'available'
                    """,
                    (used_by, source_id),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    async def delete(self, source_id: int) -> bool:
        return await asyncio.to_thread(self._delete_sync, source_id)

    def _delete_sync(self, source_id: int) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM gifticons WHERE source_message_id = ?", (source_id,))
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    async def clear(self) -> int:
        return await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM gifticons")
                conn.execute("DELETE FROM expiry_notifications")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    async def restore(self, source_id: int, new_source_id: int) -> None:
        await asyncio.to_thread(self._restore_sync, source_id, new_source_id)

    def _restore_sync(self, source_id: int, new_source_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE gifticons
                    SET source_message_id = ?, status = 'available', used_by = NULL, used_at = NULL
                    WHERE source_message_id = ?
                    """,
                    (new_source_id, source_id),
                )
                conn.commit()
            finally:
                conn.close()

    async def list(self, status: str | None = None, query: str = "") -> list[Gifticon]:
        return await asyncio.to_thread(self._list_sync, status, query)

    def _list_sync(self, status: str | None, query: str) -> list[Gifticon]:
        clauses: list[str] = []
        params: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            clauses.append("description LIKE ?")
            params.append(f"%{query}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT * FROM gifticons {where} ORDER BY created_at DESC", params
                ).fetchall()
            finally:
                conn.close()
        return [self._row_to_gifticon(row) for row in rows if row]

    async def claim_expiry_notification(self, source_id: int, notice_date: str) -> bool:
        return await asyncio.to_thread(self._claim_expiry_notification_sync, source_id, notice_date)

    def _claim_expiry_notification_sync(self, source_id: int, notice_date: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO expiry_notifications (source_message_id, notice_date)
                    VALUES (?, ?)
                    """,
                    (source_id, notice_date),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    @staticmethod
    def _row_to_gifticon(row: sqlite3.Row | None) -> Gifticon | None:
        if row is None:
            return None
        return Gifticon(
            source_message_id=int(row["source_message_id"]),
            archive_message_id=int(row["archive_message_id"]),
            description=str(row["description"]),
            status=str(row["status"]),
            used_by=str(row["used_by"]) if row["used_by"] else None,
            used_at=str(row["used_at"]) if row["used_at"] else None,
            expiry_date=str(row["expiry_date"]) if row["expiry_date"] else None,
        )
