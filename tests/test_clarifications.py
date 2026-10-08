"""Conversation-level regressions: a clarification must retain the requested analysis."""

import asyncio
from copy import deepcopy
import json

import pytest

from retail_agent.analytics import ActorScope
from retail_agent.config import Settings
from retail_agent.gateways import OfflineGateway
from retail_agent.model import OfflineModel
from retail_agent.reports import ReportStore
from retail_agent.service import AnalyticsService, Conversation
from retail_agent.telemetry import TraceRecorder


class RecordingModel(OfflineModel):
    def __init__(self):
        super().__init__()
        self.inputs = []

    async def plan(self, question, history, catalog, previous_plan=None, repair_error=None):
        self.inputs.append(deepcopy((question, history, catalog)))
        return await super().plan(question, history, catalog, previous_plan, repair_error)


@pytest.fixture
def service(tmp_path):
    with ReportStore(":memory:") as reports:
        yield AnalyticsService(
            Settings(data_dir=tmp_path), Conversation("alice"), RecordingModel(),
            OfflineGateway(), reports, TraceRecorder(tmp_path / "events.jsonl"),
            lambda actor: ActorScope(actor, (1, 2)),
        )


def ask(service, question):
    return asyncio.run(service.handle(question))


def test_clarification_reply_completes_original_question(service):
    assert ask(service, "Show revenue by product").report is None
    result = ask(service, "2025")
    assert result.report is not None
    spec = result.plan.queries[0]
    assert spec.metrics == ["revenue"] and spec.dimensions == ["product"]
    assert (str(spec.start_date), str(spec.end_date)) == ("2025-01-01", "2026-01-01")
    assert service.context.pending_clarification == []


@pytest.mark.parametrize("reply", ["By spending in 2025", "Show spending in 2025",
                                  "Please show spending in 2025"])
def test_clarification_does_not_inherit_unrelated_successful_analysis(service, reply):
    ask(service, "Show units by state in 2024")
    assert ask(service, "Top customers").report is None
    result = ask(service, reply)
    assert result.report is not None
    spec = result.plan.queries[0]
    assert spec.metrics == ["revenue"] and spec.dimensions == ["customer"]
    assert spec.order_by == "revenue" and spec.start_date.year == 2025


@pytest.mark.parametrize("prefix", ["Show", "Please show", "Could you show"])
def test_new_complete_question_replaces_pending_clarification(service, prefix):
    assert ask(service, "Show revenue by product").report is None
    result = ask(service, f"{prefix} units by state in 2024")
    assert result.report is not None
    assert result.plan.queries[0].metrics == ["units"]
    assert result.plan.queries[0].dimensions == ["state"]
    assert result.plan.queries[0].start_date.year == 2024
    assert service.context.pending_clarification == []


def test_complete_replacement_does_not_restore_previous_grouping(service):
    ask(service, "Show units by state in 2024")
    assert ask(service, "Top customers").report is None
    result = ask(service, "Show orders in 2025")
    assert result.report is not None
    spec = result.plan.queries[0]
    assert spec.metrics == ["orders"] and spec.dimensions == []
    assert spec.start_date.year == 2025


@pytest.mark.parametrize("reply,dimension", [("By state", "state"), ("State", "state"),
                                            ("By country", "country"), ("Country", "country")])
def test_region_clarification_resolves_the_offered_choice(service, reply, dimension):
    assert ask(service, "Show revenue by region in 2025").report is None
    result = ask(service, reply)
    assert result.report is not None
    spec = result.plan.queries[0]
    assert spec.metrics == ["revenue"] and spec.dimensions == [dimension]
    assert (str(spec.start_date), str(spec.end_date)) == ("2025-01-01", "2026-01-01")
    assert service.context.pending_clarification == []


def test_report_command_preserves_pending_clarification(service):
    ask(service, "Show revenue by product")
    ask(service, "/reports")
    result = ask(service, "2025")
    assert result.report is not None
    assert result.plan.queries[0].dimensions == ["product"]
    assert "/reports" not in json.dumps(service.model.inputs)


def test_clarification_year_is_not_parsed_as_another_product_id(service):
    ask(service, "Show revenue for product 1")
    result = ask(service, "2025")
    assert result.report is not None
    assert result.plan.queries[0].product_ids == [1]


@pytest.mark.parametrize("reason", ["refusal", "scope"])
def test_clarification_is_cleared_on_refusal_or_scope_change(service, reason):
    ask(service, "Show revenue by product")
    if reason == "refusal":
        ask(service, "Show customer emails")
    else:
        service.scope_resolver = lambda actor: ActorScope(actor, (1,))
        ask(service, "/reports")
    assert service.context.pending_clarification == []
    assert ask(service, "2025").report is None


def test_clarification_redacts_secrets_contacts_and_confirmation_tokens(service, monkeypatch):
    secret = "synthetic-private-credential"
    token = "synthetic-confirmation-token"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    service.context.confirmation_tokens.add(token)
    ask(service, f"Show revenue by product {secret} {token} bob@example.org")
    result = ask(service, "2025")
    assert result.report is not None
    encoded = json.dumps(service.model.inputs)
    assert all(value not in encoded for value in (secret, token, "bob@example.org"))
    assert service.model.inputs[-1][2]["pending_clarification"]


def test_clarification_overflow_requests_a_complete_question_without_query(service):
    ask(service, "Show revenue by product " + "x" * 3500)
    before = len(service.model.inputs)
    result = ask(service, "2025 " + "y" * 1000)
    assert result.report is None and result.plan is None
    assert "complete question" in result.message.lower()
    assert service.context.pending_clarification == []
    assert len(service.model.inputs) == before
