"""Repositories for the phase 2D3-B official announcement table (0008)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from ..domain.models import (
    OfficialAnnouncementCandidate,
    ensure_aware_utc,
    now_utc,
)
from .sqlite import StorageError


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _wrap(operation: str) -> StorageError:
    return StorageError("official announcement storage %s failed" % operation)


def _safe_execute(conn, sql: str, params, operation: str):
    try:
        return conn.execute(sql, params)
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as StorageError
        raise _wrap(operation) from exc


class OfficialAnnouncementCandidateRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(
        self, candidate: OfficialAnnouncementCandidate
    ) -> OfficialAnnouncementCandidate:
        _safe_execute(
            self.conn,
            "INSERT INTO official_announcement_candidate "
            "(id, source_name, title, url, published_at, summary, status, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_name, url) DO NOTHING",
            (
                candidate.id,
                candidate.source_name,
                candidate.title,
                candidate.url,
                candidate.published_at,
                candidate.summary,
                candidate.status,
                _iso(candidate.created_at),
                _iso(candidate.updated_at),
            ),
            "official candidate upsert",
        )
        stored = self.get(candidate.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("official candidate upsert")
        return stored

    def get(self, candidate_id: str) -> Optional[OfficialAnnouncementCandidate]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM official_announcement_candidate WHERE id = ?",
            (candidate_id,),
            "official candidate read",
        ).fetchone()
        return self._from_row(row) if row else None

    def list(self, status: Optional[str] = None, limit: int = 50) -> List[OfficialAnnouncementCandidate]:
        if status is not None and status not in ("new", "researched", "rejected"):
            raise ValueError("invalid status filter: %r" % status)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        sql = (
            "SELECT * FROM official_announcement_candidate"
            + (" WHERE status = ?" if status else "")
            + " ORDER BY published_at DESC, id LIMIT ?"
        )
        params: tuple = (status,) if status else ()
        params = params + (limit,)
        rows = _safe_execute(self.conn, sql, params, "official candidate list").fetchall()
        return [self._from_row(row) for row in rows]

    def update_status(self, candidate_id: str, status: str) -> None:
        if status not in ("new", "researched", "rejected"):
            raise ValueError("invalid status: %r" % status)
        _safe_execute(
            self.conn,
            "UPDATE official_announcement_candidate SET status = ?, "
            "updated_at = ? WHERE id = ?",
            (status, _iso(now_utc()), candidate_id),
            "official candidate status update",
        )

    @staticmethod
    def _from_row(row) -> OfficialAnnouncementCandidate:
        return OfficialAnnouncementCandidate(
            id=row["id"],
            source_name=row["source_name"],
            title=row["title"],
            url=row["url"],
            published_at=row["published_at"],
            summary=row["summary"],
            status=row["status"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )
