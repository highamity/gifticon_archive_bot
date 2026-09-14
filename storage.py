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
    title: str | None
    status: str
    used_by: str | None
    used_at: str | None
    expiry_date: str | None


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool
    days: int
    send_time: str


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
                        title TEXT,
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
                if "title" not in columns:
                    conn.execute("ALTER TABLE gifticons ADD COLUMN title TEXT")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS expiry_notifications (
                        source_message_id INTEGER NOT NULL,
                        notice_date TEXT NOT NULL,
                        PRIMARY KEY (source_message_id, notice_date)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_settings (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        enabled INTEGER NOT NULL DEFAULT 1,
                        days INTEGER NOT NULL DEFAULT 7,
                        send_time TEXT NOT NULL DEFAULT '09:00'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS custom_brands (
                        name TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_settings (id, enabled, days, send_time)
                    VALUES (1, 1, 7, '09:00')
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

    async def set_expiry(self, source_id: int, expiry_date: str | None) -> bool:
        return await asyncio.to_thread(self._set_expiry_sync, source_id, expiry_date)

    def _set_expiry_sync(self, source_id: int, expiry_date: str | None) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE gifticons SET expiry_date = ? WHERE source_message_id = ?",
                    (expiry_date, source_id),
                )
                if cur.rowcount == 1:
                    conn.execute(
                        "DELETE FROM expiry_notifications WHERE source_message_id = ?",
                        (source_id,),
                    )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    async def set_title(self, source_id: int, title: str | None) -> bool:
        return await asyncio.to_thread(self._set_title_sync, source_id, title)

    def _set_title_sync(self, source_id: int, title: str | None) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE gifticons SET title = ? WHERE source_message_id = ?",
                    (title, source_id),
                )
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    async def set_title_and_expiry(
        self, source_id: int, title: str | None, expiry_date: str | None
    ) -> bool:
        return await asyncio.to_thread(
            self._set_title_and_expiry_sync, source_id, title, expiry_date
        )

    def _set_title_and_expiry_sync(
        self, source_id: int, title: str | None, expiry_date: str | None
    ) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE gifticons SET title = ?, expiry_date = ? WHERE source_message_id = ?",
                    (title, expiry_date, source_id),
                )
                if cur.rowcount == 1:
                    conn.execute(
                        "DELETE FROM expiry_notifications WHERE source_message_id = ?",
                        (source_id,),
                    )
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
            clauses.append("(description LIKE ? OR title LIKE ?)")
            params.extend((f"%{query}%", f"%{query}%"))
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

    async def list_brands(self) -> list[str]:
        return await asyncio.to_thread(self.list_brands_sync)

    def list_brands_sync(self) -> list[str]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT name FROM custom_brands ORDER BY name COLLATE NOCASE"
                ).fetchall()
            finally:
                conn.close()
        return [str(row["name"]) for row in rows]

    async def add_brand(self, name: str) -> bool:
        return await asyncio.to_thread(self._add_brand_sync, name)

    def _add_brand_sync(self, name: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("INSERT OR IGNORE INTO custom_brands (name) VALUES (?)", (name,))
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

    async def remove_brand(self, name: str) -> bool:
        return await asyncio.to_thread(self._remove_brand_sync, name)

    def _remove_brand_sync(self, name: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM custom_brands WHERE name = ?", (name,))
                conn.commit()
                return cur.rowcount == 1
            finally:
                conn.close()

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

    async def get_notification_settings(self) -> NotificationSettings:
        return await asyncio.to_thread(self._get_notification_settings_sync)

    def _get_notification_settings_sync(self) -> NotificationSettings:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT enabled, days, send_time FROM notification_settings WHERE id = 1"
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return NotificationSettings(enabled=True, days=7, send_time="09:00")
        return NotificationSettings(
            enabled=bool(row["enabled"]),
            days=int(row["days"]),
            send_time=str(row["send_time"]),
        )

    async def set_notification_settings(
        self, enabled: bool, days: int, send_time: str
    ) -> None:
        await asyncio.to_thread(
            self._set_notification_settings_sync, enabled, days, send_time
        )

    def _set_notification_settings_sync(
        self, enabled: bool, days: int, send_time: str
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO notification_settings (id, enabled, days, send_time)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        enabled = excluded.enabled,
                        days = excluded.days,
                        send_time = excluded.send_time
                    """,
                    (int(enabled), days, send_time),
                )
                conn.commit()
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
            title=str(row["title"]) if row["title"] else None,
            status=str(row["status"]),
            used_by=str(row["used_by"]) if row["used_by"] else None,
            used_at=str(row["used_at"]) if row["used_at"] else None,
            expiry_date=str(row["expiry_date"]) if row["expiry_date"] else None,
        )
