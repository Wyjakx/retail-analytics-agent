"""Application orchestration: actor policy and side effects never belong to the model."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import time
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from .adk_workflow import ModelFailure
from .analytics import (
    ActorScope, AnalysisPlan, BudgetExceeded, METRIC_DEFINITIONS, QueryTimeout,
    ScopeViolation, permitted_products,
)
from .config import ConfigurationError, Settings
from .date_constraints import enforce_explicit_periods
from .gateways import QueryBudget
from .model import AnalystReport, AnalyticalModel, deterministic_report, starts_new_request
from .reports import InvalidConfirmationError, PendingDeletion, Report, ReportStore, ReportStoreError
from .safety import (
    UnsafeOutput, privacy_refusal, sanitize_input, validate_evidence, validate_report, validate_text,
)
from .telemetry import TraceRecorder


@dataclass
class Conversation:
    actor_id: str
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    history: list[dict[str, str]] = field(default_factory=list)
    pending_clarification: list[dict[str, str]] = field(default_factory=list)
    previous_plan: AnalysisPlan | None = None
    last_report: AnalystReport | None = None
    last_report_fallback: bool = False
    last_evidence: list[dict[str, Any]] = field(default_factory=list)
    last_products: tuple[int, ...] = ()
    scope_snapshot: tuple[int, ...] = ()
    pending: PendingDeletion | None = field(default=None, repr=False)
    confirmation_tokens: set[str] = field(default_factory=set, repr=False)
    customer_refs: set[str] = field(default_factory=set, repr=False)


@dataclass
class TurnResult:
    message: str
    report: AnalystReport | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    plan: AnalysisPlan | None = None
    pending: PendingDeletion | None = field(default=None, repr=False)
    reports: list[dict[str, str]] = field(default_factory=list)
    catalog: dict[str, list[str]] | None = None
    request_id: str = ""
    saved_report: Report | None = None
    deletion_status: str | None = None
    report_fallback: bool = False


class AnalyticsService:
    def __init__(
        self, settings: Settings, conversation: Conversation, model: AnalyticalModel,
        gateway: Any, reports: ReportStore, traces: TraceRecorder,
        scope_resolver: Callable[[str], ActorScope],
    ):
        self.settings = settings
        self.context = conversation
        self.model = model
        self.gateway = gateway
        self.reports = reports
        self.traces = traces
        self.scope_resolver = scope_resolver

    def refresh_scope(self) -> ActorScope:
        scope = self.scope_resolver(self.context.actor_id)
        if scope.actor_id != self.context.actor_id:
            raise ScopeViolation("The policy actor does not match the conversation actor.")
        if self.context.scope_snapshot != scope.allowed_product_ids:
            # Revoke cached analytical context when policy changes.
            self.context.history.clear()
            self.context.pending_clarification.clear()
            self.context.previous_plan = None
            self.context.last_report = None
            self.context.last_report_fallback = False
            self.context.last_evidence.clear()
            self.context.last_products = ()
            self.context.customer_refs.clear()
            pending = self.context.pending
            self.context.pending = None
            if pending and pending.operation_id:
                self.reports.cancel_delete(scope.actor_id, pending.operation_id)
            self.context.scope_snapshot = scope.allowed_product_ids
        return scope

    def _record_model(self, common: dict[str, Any]) -> None:
        metadata = getattr(self.model, "last_metadata", {})
        values = {key: metadata[key] for key in ("model_calls", "input_tokens", "output_tokens")
                  if key in metadata}
        self.traces.event(stage="model_usage", status=metadata.get("status", "completed"),
                          simulated=bool(getattr(self.model, "simulated", False)), **common, **values)

    async def handle(self, text: str) -> TurnResult:
        request_id = uuid4().hex
        common = dict(request_id=request_id, conversation_id=self.context.conversation_id,
                      actor_id=self.context.actor_id)
        budget = QueryBudget(self.settings.max_queries, self.settings.max_turn_bytes,
                             deadline=time.monotonic() + self.settings.turn_timeout)
        # The gateway uses this deadline before cloud submissions, even after coroutine cancellation.
        started = time.monotonic()
        terminal_status = "completed"
        if hasattr(self.model, "begin_turn"):
            self.model.begin_turn(max_calls=6)
        self.traces.event(stage="request", status="started", **common)
        try:
            result = await asyncio.wait_for(
                self._handle(text, budget, common), timeout=self.settings.turn_timeout,
            )
        except (asyncio.TimeoutError, QueryTimeout):
            terminal_status = "failed"
            result = TurnResult("The request timed out. No further query attempts will be started.")
            self.traces.event(stage="request", status="failed", error_type="QueryTimeout", **common)
        except (ScopeViolation, BudgetExceeded) as exc:
            terminal_status = "refused"
            result = TurnResult(
                "The requested products exceed your configured permissions."
                if isinstance(exc, ScopeViolation) else "The request stopped at its query or cost limit."
            )
            self.traces.event(stage="request", status="refused", error_type=type(exc).__name__, **common)
        except InvalidConfirmationError:
            terminal_status = "refused"
            result = TurnResult("The confirmation token does not match. Reports remain unchanged.")
            self.traces.event(stage="confirmation", status="refused", reason="invalid_token", **common)
        except (ConfigurationError, ReportStoreError, UnsafeOutput, ValueError) as exc:
            terminal_status = "failed"
            result = TurnResult("The operation could not be completed safely. Check the inputs or configuration.")
            self.traces.event(stage="request", status="failed", error_type=type(exc).__name__, **common)
        except Exception as exc:
            terminal_status = "failed"
            # Never put provider exception strings, SQL or request contents into user output/logs.
            result = TurnResult("A dependency failed. The interface remains available; try again later.")
            self.traces.event(stage="request", status="failed", error_type=type(exc).__name__, **common)
        finally:
            if hasattr(budget, "cancel"):
                budget.cancel()
        result.request_id = request_id
        self.traces.event(
            stage="request", status=terminal_status, query_count=budget.queries_used,
            budget_bytes=budget.bytes_used, duration_ms=round((time.monotonic() - started) * 1000),
            **common,
        )
        return result

    async def _handle(self, text: str, budget: QueryBudget, common: dict[str, Any]) -> TurnResult:
        scope = self.refresh_scope()
        if len(text) > 4000:
            return TurnResult("Please keep questions under 4,000 characters.")
        command = self._command(text, scope, common)
        if command is not None:
            return command
        refusal = privacy_refusal(text)
        if refusal:
            self.context.pending_clarification.clear()
            self.traces.event(stage="input_policy", status="refused", reason="privacy", **common)
            return TurnResult(refusal)
        question = sanitize_input(text)
        for token in self.context.confirmation_tokens:
            question = question.replace(token, "[REDACTED_CONFIRMATION]")
        had_pending_clarification = bool(self.context.pending_clarification)
        if starts_new_request(question):
            self.context.pending_clarification.clear()
        pending = self.context.pending_clarification
        if pending and (len(pending) + 2 > 6
                        or sum(len(item["content"]) for item in pending) + len(question) > 4000):
            pending.clear()
            return TurnResult("Please provide one complete question, including the metric and period.")
        catalog = {
            "pending_clarification": [dict(item) for item in pending],
            "tables": self.gateway.schema_catalog(), "metrics": METRIC_DEFINITIONS,
            "dimensions": ["month", "state", "country", "category", "product", "customer"],
            "allowed_product_ids": list(scope.allowed_product_ids),
            "allowed_customer_refs": sorted(self.context.customer_refs),
            "customer_semantics": "Actor-scoped opaque references only; spending covers authorized products, never all purchases.",
            "current_date": datetime.now(timezone.utc).date().isoformat(),
            "date_semantics": "UTC; start inclusive, end exclusive; all-time must be explicit",
        }
        decision = None
        for attempt in range(self.settings.max_corrections + 1):
            try:
                with self.traces.stage("planning", attempt=attempt, **common):
                    decision = await self.model.plan(
                        question, self.context.history, catalog,
                        None if had_pending_clarification or pending else self.context.previous_plan,
                        repair_error="invalid_plan" if attempt else None,
                    )
                break
            except (ModelFailure, ValidationError) as exc:
                if isinstance(exc, ModelFailure) and exc.code not in (
                    "model_invalid_output", "model_empty_output",
                ):
                    return TurnResult("The model is unavailable or its request limit was reached. Try again later.")
                if attempt == self.settings.max_corrections:
                    return TurnResult("The model could not produce a valid analysis plan within the attempt limit.")
            finally:
                self._record_model(common)
        if decision is None:
            return TurnResult("No valid analysis plan was produced.")
        if decision.action in ("refuse", "clarify"):
            validate_text(decision.message)
            if decision.action == "clarify":
                exchange = [
                    *pending, {"role": "user", "content": question},
                    {"role": "assistant", "content": decision.message},
                ]
                if sum(len(item["content"]) for item in exchange) <= 4000 and len(exchange) <= 6:
                    self.context.pending_clarification = exchange
                else:
                    pending.clear()
                    return TurnResult("Please provide one complete question, including the metric and period.")
            else:
                pending.clear()
            return TurnResult(decision.message)
        if decision.action == "schema":
            if hasattr(self.gateway, "validate_schema"):
                schema = await asyncio.to_thread(self.gateway.validate_schema, budget)
            else:
                schema = self.gateway.schema_catalog()
            return TurnResult(
                "Approved analytics columns and joins: orders → order_items → products, "
                "with users for coarse geographic grouping. Personal identity columns are unavailable. "
                "The offline catalog is simulated." if getattr(self.model, "simulated", False) else
                "Verified analytics columns; joins use order, product and internal customer keys. "
                "Personal identity values are unavailable.", catalog=schema,
            )
        plan = AnalysisPlan.model_validate(decision.plan.model_dump())
        report_question = question
        if pending:
            report_question = "\nReply: ".join([
                *(item["content"] for item in pending if item["role"] == "user"), question,
            ])
        plan = enforce_explicit_periods(report_question, plan)
        return await self._analyze(report_question, plan, scope, budget, catalog, common)

    async def _analyze(
        self, question: str, plan: AnalysisPlan, scope: ActorScope, budget: QueryBudget,
        catalog: dict[str, Any], common: dict[str, Any],
    ) -> TurnResult:
        evidence: list[dict[str, Any]] = []
        used_products: set[int] = set()
        if len(plan.queries) > budget.max_queries:
            raise BudgetExceeded("The analysis requires more queries than this request permits.")
        for spec in plan.queries:
            used_products.update(permitted_products(spec, scope))
            if not set(spec.customer_refs or []).issubset(self.context.customer_refs):
                return TurnResult("Use a customer reference from a previous ranking in this conversation, or rerun the ranking.")
            for labels in (spec.states, spec.countries, spec.categories):
                for label in labels or []:
                    validate_text(label)
        # Validate every step before running the first; partial authorization failures spend nothing.
        for attempt in range(self.settings.max_corrections + 1):
            evidence = []
            repairable = False
            for index, spec in enumerate(plan.queries):
                if self.refresh_scope() != scope:
                    raise ScopeViolation("Product permissions changed during the request.")
                try:
                    with self.traces.stage("query", attempt=attempt, **common):
                        outcome = await asyncio.to_thread(self.gateway.execute, spec, scope, budget)
                except Exception as exc:
                    metadata = getattr(self.gateway, "last_metadata", {})
                    values = {key: metadata[key] for key in (
                        "job_id", "estimated_bytes", "bytes_billed", "bytes_processed",
                    ) if key in metadata}
                    if values:
                        self.traces.event(stage="query_job", status="failed", **common, **values)
                    if type(exc).__name__ == "BadRequest" and attempt < self.settings.max_corrections:
                        repairable = True
                        break
                    raise
                if self.refresh_scope() != scope:
                    raise ScopeViolation("Product permissions changed during the request.")
                raw_empty = not outcome.rows
                approved = outcome.approve_for_model(spec, self.settings.min_group_customers)
                item = {**approved.to_dict(), "query_index": index,
                        "result_limit": spec.limit, "limit_reached": len(outcome.rows) >= spec.limit,
                        "product_ids": list(permitted_products(spec, scope)),
                        "period": {"start": str(spec.start_date) if spec.start_date else None,
                                   "end_exclusive": str(spec.end_date) if spec.end_date else None}}
                evidence.append(item)
                stats = {key: outcome.statistics[key] for key in (
                    "job_id", "estimated_bytes", "bytes_processed", "bytes_billed",
                ) if key in outcome.statistics}
                self.traces.event(
                    stage="query_result", status="empty" if raw_empty else "completed",
                    evidence_id=approved.evidence_id, row_count=len(approved.rows),
                    suppressed_groups=approved.suppressed_groups, simulated=approved.simulated,
                    **common, **stats,
                )
                repairable |= raw_empty
            if not repairable or attempt == self.settings.max_corrections:
                break
            if budget.queries_used + len(plan.queries) > budget.max_queries:
                break
            try:
                with self.traces.stage("correction", attempt=attempt + 1, **common):
                    repair = await self.model.plan(
                        question, self.context.history, catalog, plan,
                        repair_error="empty_or_rejected_query; preserve every filter, period and metric",
                    )
            except (ModelFailure, ValidationError):
                break
            finally:
                self._record_model(common)
            if repair.action != "analysis" or repair.plan.model_dump() != plan.model_dump():
                # A wider period/filter is a new user decision, never an automatic correction.
                break
            plan = repair.plan
        if self.refresh_scope() != scope:
            raise ScopeViolation("Product permissions changed during analysis.")
        validate_evidence(evidence)
        if len(evidence) != len(plan.queries):
            return TurnResult(
                "Analysis incomplete: a planned query failed. No new report was created.",
                evidence=evidence, plan=plan,
            )
        if not evidence:
            return TurnResult("The query could not be executed after a bounded correction attempt.")
        report_fallback = False
        try:
            with self.traces.stage("reporting", **common):
                report = await self.model.report(question, evidence, METRIC_DEFINITIONS)
                validate_report(report, evidence, plan)
        except (ModelFailure, ValidationError, UnsafeOutput):
            report_fallback = True
            report = deterministic_report(evidence)
            validate_text(report.to_markdown())
            self.traces.event(stage="report_fallback", status="completed", reason="unverified_synthesis", **common)
        finally:
            self._record_model(common)
        if self.refresh_scope() != scope:
            raise ScopeViolation("Product permissions changed during reporting.")
        # Keep essential definitions in every saved report, regardless of model wording.
        product_list = ", ".join(str(product) for product in sorted(used_products)[:20])
        if len(used_products) > 20:
            product_list += f", and remaining configured products ({len(used_products)} in total)"
        periods = list(dict.fromkeys(
            f"{spec.start_date} to before {spec.end_date}" if spec.start_date else "explicit all-time"
            for spec in plan.queries
        ))
        metrics = list(dict.fromkeys(metric for spec in plan.queries for metric in spec.metrics))
        context_caveats = [
            f"Product scope: {product_list}. Periods (UTC): {'; '.join(periods)}.",
            "Definitions: " + " ".join(f"{metric}: {METRIC_DEFINITIONS[metric]}" for metric in metrics),
            "Amounts use the dataset's price units; currency is not inferred. Orders and spending cover permitted items only.",
        ]
        report = report.model_copy(update={"caveats": (context_caveats + report.caveats)[:8]})
        validate_text(report.to_markdown())
        self.context.previous_plan = plan
        self.context.pending_clarification.clear()
        self.context.last_report = report
        self.context.last_report_fallback = report_fallback
        self.context.last_evidence = evidence
        self.context.last_products = tuple(sorted(used_products))
        self.context.customer_refs.update(
            row["customer"] for item in evidence for row in item["rows"] if "customer" in row
        )
        self.context.history.extend([
            {"role": "user", "content": question},
            {"role": "assistant", "content": report.to_markdown()[:4000]},
        ])
        self.context.history = self.context.history[-6:]
        message = (
            "Analysis completed. The generated summary could not be verified; "
            "this report shows the approved results. Use /save TITLE to keep it."
            if report_fallback else "Analysis completed. Use /save TITLE to keep this report."
        )
        return TurnResult(message,
                          report=report, evidence=evidence, plan=plan,
                          report_fallback=report_fallback)

    def _eligible_reports(self, scope: ActorScope, **filters: Any):
        reports = self.reports.list_reports(scope.actor_id, **filters)
        eligible = []
        for report in reports:
            evidence = report.evidence if isinstance(report.evidence, dict) else {}
            products = evidence.get("product_ids", [])
            if products and set(products).issubset(scope.allowed_product_ids):
                validate_text(report.title)
                eligible.append(report)
        return eligible

    def _command(self, raw: str, scope: ActorScope, common: dict[str, Any]) -> TurnResult | None:
        text = raw.strip()
        lower = text.casefold()
        if lower in ("/explain", "explain last analysis"):
            return TurnResult(
                "The validated plan controls metrics, dates and grouping. Product permissions were "
                "added by Python; the model did not supply executable SQL.",
                report=self.context.last_report,
                report_fallback=self.context.last_report_fallback,
                plan=self.context.previous_plan, evidence=self.context.last_evidence,
            )
        save = re.fullmatch(r"/save(?:\s+(.+))?", text, re.I)
        natural_save = re.fullmatch(r"(?:save|enregistre)(?: this| the| ce)? report(?: as (.+))?", text, re.I)
        if save or natural_save:
            if self.context.last_report is None:
                return TurnResult("Run an analysis before saving a report.")
            title = (save or natural_save).group(1) or self.context.last_report.title
            if not 1 <= len(title) <= 120:
                return TurnResult("Choose a report title between 1 and 120 characters.")
            if any(token in title for token in self.context.confirmation_tokens):
                raise UnsafeOutput("A confirmation token cannot be used in a report title.")
            validate_text(title)
            report = self.reports.save(
                scope.actor_id, self.context.conversation_id, title,
                self.context.last_report.to_markdown(),
                {"product_ids": list(self.context.last_products), "queries": self.context.last_evidence},
            )
            self.traces.event(stage="save_report", status="completed", report_id=report.report_id, **common)
            return TurnResult(f"Saved report: {report.title} ({report.report_id}).")
        if lower in ("/reports", "list reports", "show my reports"):
            reports = self._eligible_reports(scope)
            return TurnResult("Your saved reports within current product permissions.", reports=[
                {"id": report.report_id, "title": report.title, "conversation": report.conversation_id}
                for report in reports
            ])
        open_report = re.fullmatch(r"/open\s+([a-f0-9]{32})", text, re.I)
        if open_report:
            matches = [report for report in self._eligible_reports(scope)
                       if report.report_id == open_report.group(1).lower()]
            if not matches:
                return TurnResult("Report unavailable within current product permissions.")
            report = matches[0]
            validate_text(report.body)
            queries = report.evidence.get("queries")
            if not isinstance(queries, list):
                raise UnsafeOutput("Saved evidence is invalid.")
            validate_evidence(queries)
            self.traces.event(stage="open_report", status="completed",
                              report_id=report.report_id, **common)
            return TurnResult("Saved report.", saved_report=report, evidence=queries)
        if lower.startswith("/confirm"):
            parts = text.split(maxsplit=1)
            pending = self.context.pending
            if len(parts) != 2 or pending is None or not pending.operation_id:
                return TurnResult("Preview a deletion first, then use /confirm followed by its exact token.")
            outcome = self.reports.confirm_delete(scope.actor_id, pending.operation_id, parts[1])
            self.traces.event(stage="delete_report", status=outcome.status,
                              operation_id=outcome.operation_id, **common)
            return TurnResult(outcome.message, deletion_status=outcome.status)
        if lower in ("/cancel", "cancel deletion"):
            pending = self.context.pending
            if pending is None or pending.operation_id is None:
                return TurnResult("There is no pending deletion.")
            outcome = self.reports.cancel_delete(scope.actor_id, pending.operation_id)
            self.traces.event(stage="delete_report", status=outcome.status,
                              operation_id=outcome.operation_id, **common)
            return TurnResult(outcome.message, deletion_status=outcome.status)
        conversation_delete = lower == "/delete conversation" or bool(re.fullmatch(
            r"delete all (?:the )?reports (?:we made )?in this conversation", lower,
        ))
        mention_delete = re.fullmatch(
            r"(?:/delete mention|delete all reports mentioning)\s+(.+)", text, re.I,
        )
        id_delete = re.fullmatch(r"/delete id\s+([a-f0-9]{32})", text, re.I)
        if conversation_delete or mention_delete or id_delete:
            filters: dict[str, str] = {}
            if conversation_delete:
                filters["conversation_id"] = self.context.conversation_id
            if mention_delete:
                filters["literal_mention"] = mention_delete.group(1)
            matches = self._eligible_reports(scope, **filters)
            if id_delete:
                matches = [report for report in matches if report.report_id == id_delete.group(1)]
            if not matches:
                return TurnResult("No matching owned reports within current product permissions.")
            previous = self.context.pending
            if previous and previous.operation_id:
                self.reports.cancel_delete(scope.actor_id, previous.operation_id)
            pending = self.reports.preview_delete(scope.actor_id, report_ids=[r.report_id for r in matches])
            self.context.pending = pending
            if pending.token:
                self.context.confirmation_tokens.add(pending.token)
            self.traces.event(stage="delete_preview", status="pending",
                              operation_id=pending.operation_id, **common)
            return TurnResult("Review the exact reports below. Confirmation is required before deletion.", pending=pending)
        if lower.startswith(("/delete", "delete all reports", "/")):
            return TurnResult("Use /help for supported commands. Deletion needs a conversation, literal mention or report ID.")
        if lower in ("yes", "oui", "confirm", "confirm deletion") and self.context.pending:
            return TurnResult("A plain yes cannot delete reports. Use /confirm with the displayed token, or /cancel.")
        return None
