"""Allowlisted correlation metadata; no questions, SQL, data rows or confirmation secrets."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any


ALLOWED = {
    "request_id", "conversation_id", "actor_id", "stage", "status", "error_type", "duration_ms",
    "attempt", "query_count", "bytes_processed", "bytes_billed", "estimated_bytes", "job_id",
    "evidence_id", "report_id", "operation_id", "row_count", "suppressed_groups", "simulated",
    "model_calls", "input_tokens", "output_tokens", "reason", "mode", "budget_bytes",
}


class TraceRecorder:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.dropped_events = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._write_failed()

    def _write_failed(self) -> None:
        self.dropped_events += 1
        if self.dropped_events == 1:
            logging.getLogger(__name__).warning(
                "Trace recording is unavailable. Business operations retain their SQLite outcomes."
            )

    def event(self, **fields: Any) -> None:
        unknown = set(fields) - ALLOWED
        if unknown:
            raise ValueError("Trace fields must come from the metadata allowlist.")
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **fields}
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError:
            self._write_failed()

    @contextmanager
    def stage(self, stage: str, **fields: Any):
        started = time.monotonic()
        self.event(stage=stage, status="started", **fields)
        try:
            yield
        except Exception as exc:
            self.event(
                stage=stage, status="failed", error_type=type(exc).__name__,
                duration_ms=round((time.monotonic() - started) * 1000), **fields,
            )
            raise
        else:
            self.event(
                stage=stage, status="completed",
                duration_ms=round((time.monotonic() - started) * 1000), **fields,
            )
