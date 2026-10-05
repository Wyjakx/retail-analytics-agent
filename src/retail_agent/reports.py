"""Owned reports and transactional, CLI-controlled deletion confirmations.

The caller supplies a trusted actor ID. Selecting an actor in the demonstration
CLI is not authentication. Confirmation tokens must stay outside model context.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any, Callable, Iterable, Iterator
from uuid import uuid4


class ReportStoreError(Exception):
    """A report operation could not be performed."""


class ReportAccessError(ReportStoreError):
    """The actor cannot access the requested resource, or it does not exist."""


class InvalidConfirmationError(ReportStoreError):
    """The provided confirmation token does not match the pending operation."""


@dataclass(frozen=True)
class Report:
    report_id: str
    actor_id: str
    conversation_id: str
    title: str
    body: str = field(repr=False)
    evidence: Any = field(repr=False)
    version: int
    created_at: float
    updated_at: float

    @property
    def id(self) -> str:
        return self.report_id


@dataclass(frozen=True)
class ReportTarget:
    report_id: str
    title: str
    version: int


@dataclass(frozen=True)
class PendingDeletion:
    operation_id: str | None
    token: str | None = field(repr=False)
    targets: tuple[ReportTarget, ...]
    expires_at: float | None
    status: str = "pending"

    @property
    def confirmation_token(self) -> str | None:
        return self.token


@dataclass(frozen=True)
class DeletionOutcome:
    operation_id: str
    status: str
    report_ids: tuple[str, ...]
    message: str

    @property
    def count(self) -> int:
        return len(self.report_ids)

    @property
    def deleted_count(self) -> int:
        return self.count


_UNSET = object()


class ReportStore:
    """SQLite store with ownership checks and atomic deletion consumption.

    Use one instance per CLI/thread. Separate instances sharing a file serialize
    writes using SQLite transactions; confirmations remain valid across restart.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.path = str(path)
        self._required(self.path, "path")
        self._now = now
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS reports_actor_conversation
                ON reports(actor_id, conversation_id);
            CREATE TABLE IF NOT EXISTS report_deletions (
                operation_id TEXT PRIMARY KEY,
                actor_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                status TEXT NOT NULL,
                outcome_json TEXT
            );
            CREATE TABLE IF NOT EXISTS report_deletion_targets (
                operation_id TEXT NOT NULL REFERENCES report_deletions(operation_id),
                report_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                PRIMARY KEY (operation_id, report_id)
            );
            """
        )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> ReportStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield self._db
            self._db.commit()
        except BaseException as exc:
            self._db.rollback()
            if isinstance(exc, sqlite3.Error):
                raise ReportStoreError("Report storage transaction failed; no changes committed") from exc
            raise

    @staticmethod
    def _required(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
        return value

    def _timestamp(self) -> float:
        timestamp = float(self._now())
        if not math.isfinite(timestamp):
            raise ValueError("Clock must return a finite timestamp")
        return timestamp

    @staticmethod
    def _report(row: sqlite3.Row) -> Report:
        return Report(
            report_id=row["report_id"],
            actor_id=row["actor_id"],
            conversation_id=row["conversation_id"],
            title=row["title"],
            body=row["body"],
            evidence=json.loads(row["evidence_json"]),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save(
        self,
        actor_id: str,
        conversation_id: str,
        title: str,
        body: str,
        evidence: Any = None,
    ) -> Report:
        self._required(actor_id, "actor_id")
        self._required(conversation_id, "conversation_id")
        self._required(title, "title")
        if not isinstance(body, str):
            raise ValueError("body must be a string")
        evidence_json = json.dumps(evidence, ensure_ascii=False, allow_nan=False)
        timestamp = self._timestamp()
        report_id = uuid4().hex
        with self._transaction() as db:
            db.execute(
                "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, actor_id, conversation_id, title, body, evidence_json,
                 1, timestamp, timestamp),
            )
            row = db.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
            return self._report(row)

    def update(
        self,
        actor_id: str,
        report_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        evidence: Any = _UNSET,
    ) -> Report:
        """Revise an owned report; any revision invalidates an older preview."""
        self._required(actor_id, "actor_id")
        self._required(report_id, "report_id")
        if title is not None:
            self._required(title, "title")
        if body is not None and not isinstance(body, str):
            raise ValueError("body must be a string")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM reports WHERE report_id = ? AND actor_id = ?",
                (report_id, actor_id),
            ).fetchone()
            if row is None:
                raise ReportAccessError("Report unavailable for this actor")
            evidence_json = row["evidence_json"] if evidence is _UNSET else json.dumps(
                evidence, ensure_ascii=False, allow_nan=False
            )
            db.execute(
                """UPDATE reports SET title = ?, body = ?, evidence_json = ?,
                   version = version + 1, updated_at = ? WHERE report_id = ? AND actor_id = ?""",
                (row["title"] if title is None else title,
                 row["body"] if body is None else body, evidence_json,
                 self._timestamp(), report_id, actor_id),
            )
            return self._report(db.execute(
                "SELECT * FROM reports WHERE report_id = ?", (report_id,)
            ).fetchone())

    def _select(
        self,
        db: sqlite3.Connection,
        actor_id: str,
        conversation_id: str | None,
        literal_mention: str | None,
        report_ids: tuple[str, ...] | None = None,
    ) -> list[Report]:
        if conversation_id is not None:
            self._required(conversation_id, "conversation_id")
        if literal_mention is not None:
            self._required(literal_mention, "literal_mention")
        # Explicit IDs are all-or-nothing: never silently omit another owner's ID.
        if report_ids is not None:
            for report_id in report_ids:
                self._required(report_id, "report_id")
                exists = db.execute(
                    "SELECT 1 FROM reports WHERE report_id = ? AND actor_id = ?",
                    (report_id, actor_id),
                ).fetchone()
                if exists is None:
                    raise ReportAccessError("Requested reports unavailable for this actor")
        rows = db.execute(
            "SELECT * FROM reports WHERE actor_id = ? ORDER BY created_at, report_id",
            (actor_id,),
        ).fetchall()
        results = [self._report(row) for row in rows]
        if conversation_id is not None:
            results = [report for report in results if report.conversation_id == conversation_id]
        if literal_mention is not None:
            needle = literal_mention.casefold()
            results = [report for report in results
                       if needle in report.title.casefold() or needle in report.body.casefold()]
        if report_ids is not None:
            selected = set(report_ids)
            results = [report for report in results if report.report_id in selected]
        return results

    def list_reports(
        self,
        actor_id: str,
        conversation_id: str | None = None,
        literal_mention: str | None = None,
    ) -> list[Report]:
        self._required(actor_id, "actor_id")
        return self._select(self._db, actor_id, conversation_id, literal_mention)

    def preview_delete(
        self,
        actor_id: str,
        conversation_id: str | None = None,
        literal_mention: str | None = None,
        report_ids: Iterable[str] | None = None,
        ttl_seconds: float = 300,
    ) -> PendingDeletion:
        self._required(actor_id, "actor_id")
        if isinstance(report_ids, (str, bytes)):
            raise ValueError("report_ids must be a collection of IDs")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ValueError("ttl_seconds must be a positive finite number")
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive finite number")
        selected_ids = None if report_ids is None else tuple(dict.fromkeys(report_ids))
        with self._transaction() as db:
            reports = self._select(db, actor_id, conversation_id, literal_mention, selected_ids)
            if not reports:
                return PendingDeletion(None, None, (), None, "no_match")
            operation_id = uuid4().hex
            token = secrets.token_urlsafe(32)
            timestamp = self._timestamp()
            expires_at = timestamp + ttl_seconds
            if not math.isfinite(expires_at):
                raise ValueError("Expiry must be a finite timestamp")
            db.execute(
                "INSERT INTO report_deletions VALUES (?, ?, ?, ?, ?, 'pending', NULL)",
                (operation_id, actor_id, self._token_hash(token), timestamp, expires_at),
            )
            db.executemany(
                "INSERT INTO report_deletion_targets VALUES (?, ?, ?)",
                [(operation_id, report.report_id, report.version) for report in reports],
            )
            targets = tuple(ReportTarget(report.report_id, report.title, report.version)
                            for report in reports)
            return PendingDeletion(operation_id, token, targets, expires_at)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _operation(self, db: sqlite3.Connection, actor_id: str, operation_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM report_deletions WHERE operation_id = ? AND actor_id = ?",
            (operation_id, actor_id),
        ).fetchone()
        if row is None:
            raise ReportAccessError("Deletion operation unavailable for this actor")
        return row

    @staticmethod
    def _recorded(row: sqlite3.Row) -> DeletionOutcome:
        data = json.loads(row["outcome_json"])
        return DeletionOutcome(data["operation_id"], data["status"],
                               tuple(data["report_ids"]), data["message"])

    @staticmethod
    def _finish(
        db: sqlite3.Connection,
        operation_id: str,
        status: str,
        report_ids: tuple[str, ...],
        message: str,
    ) -> DeletionOutcome:
        outcome = DeletionOutcome(operation_id, status, report_ids, message)
        # Audit outcomes retain identifiers/counts only, never deleted text/evidence.
        data = {"operation_id": operation_id, "status": status,
                "report_ids": list(report_ids), "message": message}
        db.execute(
            "UPDATE report_deletions SET status = ?, outcome_json = ? WHERE operation_id = ?",
            (status, json.dumps(data), operation_id),
        )
        db.execute("DELETE FROM report_deletion_targets WHERE operation_id = ?", (operation_id,))
        return outcome

    def confirm_delete(self, actor_id: str, operation_id: str, token: str) -> DeletionOutcome:
        self._required(actor_id, "actor_id")
        self._required(operation_id, "operation_id")
        self._required(token, "token")
        with self._transaction() as db:
            operation = self._operation(db, actor_id, operation_id)
            if not hmac.compare_digest(operation["token_hash"], self._token_hash(token)):
                raise InvalidConfirmationError("Confirmation token does not match")
            if operation["status"] != "pending":
                return self._recorded(operation)
            if self._timestamp() >= operation["expires_at"]:
                return self._finish(db, operation_id, "expired", (),
                                    "Confirmation expired; preview the reports again.")
            targets = db.execute(
                "SELECT report_id, version FROM report_deletion_targets WHERE operation_id = ?",
                (operation_id,),
            ).fetchall()
            for target in targets:
                report = db.execute(
                    "SELECT actor_id, version FROM reports WHERE report_id = ?",
                    (target["report_id"],),
                ).fetchone()
                if (report is None or report["actor_id"] != actor_id
                        or report["version"] != target["version"]):
                    return self._finish(db, operation_id, "stale", (),
                                        "A selected report changed; preview the reports again.")
            if not targets:
                return self._finish(db, operation_id, "stale", (), "No frozen targets remain.")
            for target in targets:
                deleted = db.execute(
                    "DELETE FROM reports WHERE report_id = ? AND actor_id = ? AND version = ?",
                    (target["report_id"], actor_id, target["version"]),
                )
                if deleted.rowcount != 1:
                    raise ReportStoreError("Deletion could not be committed; reports remain unchanged")
            report_ids = tuple(target["report_id"] for target in targets)
            return self._finish(db, operation_id, "deleted", report_ids,
                                f"Deleted {len(report_ids)} selected report(s).")

    def cancel_delete(self, actor_id: str, operation_id: str) -> DeletionOutcome:
        self._required(actor_id, "actor_id")
        self._required(operation_id, "operation_id")
        with self._transaction() as db:
            operation = self._operation(db, actor_id, operation_id)
            if operation["status"] != "pending":
                return self._recorded(operation)
            if self._timestamp() >= operation["expires_at"]:
                return self._finish(db, operation_id, "expired", (),
                                    "Confirmation expired; preview the reports again.")
            return self._finish(db, operation_id, "cancelled", (), "Deletion cancelled.")
