"""Browser-local state and synchronous actions, without Streamlit dependencies."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
import threading
from typing import Literal

from .analytics import ActorScope
from .config import ConfigurationError, Settings, resolve_scope
from .reports import ReportStore, ReportTarget
from .runtime import build_runtime
from .safety import privacy_refusal, sanitize_input
from .service import AnalyticsService, Conversation, TurnResult


@dataclass
class TranscriptTurn:
    question: str
    result: TurnResult


@dataclass(frozen=True)
class DeletionPreview:
    operation_id: str
    targets: tuple[ReportTarget, ...]
    expires_at: float


class WebSession:
    def __init__(
        self, actor_id: str = "analyst_north", mode: Literal["offline", "live"] = "offline",
        settings: Settings | None = None,
    ):
        self._settings = settings if settings is not None else Settings.load()
        self._lock = threading.RLock()
        self._issued_tokens: set[str] = set()
        self._available = False
        self._scope_snapshot: tuple[int, ...] = ()
        self.busy = False
        self.revision = 0
        self.transcript: list[TranscriptTurn] = []
        self.actor_id, self.mode = actor_id, mode
        self.context = Conversation(actor_id)
        self.runtime = build_runtime(actor_id, mode, self._settings)
        self._available = True
        self.refresh_access()

    def _service(self, store: ReportStore) -> AnalyticsService:
        return AnalyticsService(
            self.runtime.settings, self.context, self.runtime.model, self.runtime.gateway,
            store, self.runtime.traces,
            lambda actor: resolve_scope(actor, self._settings.permissions_file),
        )

    def _store(self) -> ReportStore:
        return ReportStore(self.runtime.settings.data_dir / "reports.sqlite3")

    def _discard_context(self) -> None:
        pending = self.context.pending
        self._issued_tokens.update(self.context.confirmation_tokens)
        if pending and pending.token:
            self._issued_tokens.add(pending.token)
        try:
            if pending and pending.operation_id:
                with self._store() as store:
                    store.cancel_delete(self.context.actor_id, pending.operation_id)
        except Exception:
            # Cleanup failure cannot justify retaining now-inaccessible browser data.
            self.runtime.traces.event(stage="web_reset", status="failed", reason="cleanup")
        finally:
            self.transcript.clear()
            self.context = Conversation(self.actor_id, confirmation_tokens=set(self._issued_tokens))
            self._scope_snapshot = ()
            self.revision += 1

    def refresh_access(self) -> ActorScope:
        with self._lock:
            if not self._available:
                raise ConfigurationError("Apply a valid actor and mode to start a session.")
            try:
                scope = resolve_scope(self.actor_id, self._settings.permissions_file)
                if self._scope_snapshot and self._scope_snapshot != scope.allowed_product_ids:
                    self._discard_context()
                    self.runtime = build_runtime(self.actor_id, self.mode, self._settings)
                with self._store() as store:
                    scope = self._service(store).refresh_scope()
                self._scope_snapshot = scope.allowed_product_ids
                return scope
            except Exception:
                self._discard_context()
                raise

    def reset(
        self, *, actor_id: str | None = None, mode: Literal["offline", "live"] | None = None,
    ) -> None:
        with self._lock:
            self._available = False
            self._discard_context()
            self.actor_id = actor_id if actor_id is not None else self.actor_id
            self.mode = mode if mode is not None else self.mode
            self.context = Conversation(self.actor_id, confirmation_tokens=set(self._issued_tokens))
            self.runtime = build_runtime(self.actor_id, self.mode, self._settings)
            self._available = True
            self.refresh_access()

    def _redact(self, text: str) -> str:
        for token in self._issued_tokens | self.context.confirmation_tokens:
            text = text.replace(token, "[REDACTED_CONFIRMATION]")
        return sanitize_input(text)

    def _execute(self, text: str, *, record: bool) -> TurnResult:
        if not self._lock.acquire(blocking=False):
            return TurnResult("An operation is already running. Please wait.")
        if self.busy:
            self._lock.release()
            return TurnResult("An operation is already running. Please wait.")
        try:
            self.busy = True
            initial_scope = self.refresh_access()
            # Commands have their own trusted parser. Analytical input is scrubbed
            # again in the service, including all issued tokens retained after reset.
            self.context.confirmation_tokens.update(self._issued_tokens)
            with self._store() as store:
                result = asyncio.run(self._service(store).handle(text))
            self._issued_tokens.update(self.context.confirmation_tokens)
            if result.deletion_status in ("deleted", "cancelled", "expired", "stale"):
                self.context.pending = None
            if self.refresh_access() != initial_scope:
                result = TurnResult("Product permissions changed. Please run a fresh analysis.")
            if record:
                if text.strip().startswith("/"):
                    label = "[Report command]"
                elif len(text) > 4000:
                    label = "[Question exceeds the length limit]"
                elif privacy_refusal(text):
                    label = "[Sensitive request withheld]"
                else:
                    label = self._redact(text)
                self.transcript.append(TranscriptTurn(
                    label, deepcopy(replace(result, pending=None)),
                ))
                self.transcript = self.transcript[-20:]
            return result
        finally:
            self.busy = False
            self._lock.release()

    def submit(self, question: str) -> TurnResult:
        return self._execute(question, record=True)

    def run_command(self, command: str) -> TurnResult:
        if not command.strip().startswith("/"):
            raise ValueError("The command channel accepts slash commands only.")
        return self._execute(command, record=False)

    def list_reports(self) -> list[dict[str, str]]:
        return self.run_command("/reports").reports

    def open_report(self, report_id: str) -> TurnResult:
        return self.run_command(f"/open {report_id}")

    @property
    def pending_preview(self) -> DeletionPreview | None:
        self.refresh_access()
        pending = self.context.pending
        if pending and pending.operation_id and pending.expires_at is not None:
            return DeletionPreview(pending.operation_id, pending.targets, pending.expires_at)
        return None

    def confirm_delete(self, operation_id: str) -> TurnResult:
        with self._lock:
            self.refresh_access()
            pending = self.context.pending
            if not pending or pending.operation_id != operation_id:
                return TurnResult("This preview is no longer active. Preview the selection again.")
            return self.run_command(f"/confirm {pending.token}")

    def cancel_delete(self, operation_id: str) -> TurnResult:
        with self._lock:
            self.refresh_access()
            pending = self.context.pending
            if not pending or pending.operation_id != operation_id:
                return TurnResult("This preview is no longer active. Preview the selection again.")
            return self.run_command("/cancel")
