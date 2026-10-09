"""Clarification journeys retain intent without reviving stale or private context."""

import json

import pytest

from retail_agent.model import OfflineModel
from tests.scenarios import ask, plan, report


def test_year_reply_completes_the_product_request_after_a_report_command(application):
    app, _ = application(model=OfflineModel())
    assert ask(app, "Show revenue for product 1").report is None
    assert app.gateway.executions == []
    assert ask(app, "/reports").reports == []

    result = ask(app, "2025")
    assert result.evidence[0]["rows"] == [{"revenue": 480}]
    assert result.evidence[0]["product_ids"] == [1]
    assert result.evidence[0]["period"] == {
        "start": "2025-01-01", "end_exclusive": "2026-01-01",
    }
    assert app.context.pending_clarification == []


def test_pending_exchange_reaches_adk_and_saved_report_only_after_redaction(application, monkeypatch):
    secret = "PRIVATE_SYNTHETIC_CREDENTIAL"
    token = "PRIVATE_CONFIRMATION_TOKEN"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    app, provider = application(
        {"action": "clarify", "message": "Which period should I analyze?"},
        plan(), report("Revenue was 500."),
    )
    app.context.confirmation_tokens.add(token)
    ask(app, f"Show revenue {secret} {token} private@example.invalid")
    assert app.gateway.executions == []
    ask(app, "/reports")
    result = ask(app, "January and February 2025")
    assert result.evidence[0]["rows"] == [{"revenue": 500}]
    pending = provider.payloads[1]["catalog"]["pending_clarification"]
    assert pending == [
        {"role": "user", "content":
         "Show revenue [REDACTED_SECRET] [REDACTED_CONFIRMATION] [REDACTED_EMAIL]"},
        {"role": "assistant", "content": "Which period should I analyze?"},
    ]
    assert provider.payloads[1]["previous_plan"] is None
    assert "January and February 2025" in provider.payloads[2]["question"]
    assert "Show revenue" in provider.payloads[2]["question"]
    assert app.context.pending_clarification == []
    ask(app, "/save Clarified revenue")
    stored = app.reports.list_reports("alice")[0]
    exposed = json.dumps(provider.payloads) + stored.body + json.dumps(stored.evidence)
    exposed += app.traces.path.read_text(encoding="utf-8")
    assert all(private not in exposed for private in (secret, token, "private@example.invalid"))
    assert "/reports" not in json.dumps(provider.payloads)


def test_ranking_clarification_does_not_inherit_the_previous_metric_or_grouping(application):
    app, _ = application(model=OfflineModel())
    first = ask(app, "Show units by state in January 2025")
    assert first.evidence[0]["rows"] == [
        {"state": "California", "units": 5}, {"state": "Texas", "units": 3},
    ]
    assert ask(app, "Top customers").report is None
    result = ask(app, "Show spending in 2025")
    assert [row["revenue"] for row in result.evidence[0]["rows"]] == [130, 110, 90, 70, 65, 35]
    assert all(set(row) == {"customer", "revenue"} for row in result.evidence[0]["rows"])
    assert result.evidence[0]["period"] == {
        "start": "2025-01-01", "end_exclusive": "2026-01-01",
    }


def test_complete_question_replaces_pending_and_previously_successful_context(application):
    app, _ = application(model=OfflineModel())
    ask(app, "Show units by state in January 2025")
    assert ask(app, "Top customers").report is None
    result = ask(app, "Show orders in 2025")
    assert result.evidence[0]["rows"] == [{"orders": 12}]
    assert app.context.pending_clarification == []


def test_region_answer_resolves_the_original_grouping_without_losing_its_period(application):
    app, _ = application(model=OfflineModel())
    assert ask(app, "Show revenue by region in 2025").report is None
    assert app.gateway.executions == []
    result = ask(app, "State")
    assert result.evidence[0]["rows"] == [
        {"state": "California", "revenue": 170}, {"state": "Texas", "revenue": 330},
    ]
    assert result.evidence[0]["period"] == {
        "start": "2025-01-01", "end_exclusive": "2026-01-01",
    }
    assert app.context.pending_clarification == []


@pytest.mark.parametrize("reset", ["privacy-refusal", "permission-change"])
def test_refusal_or_permission_change_discards_the_unresolved_request(application, policy_file, reset):
    app, provider = application(
        {"action": "clarify", "message": "Which period should I analyze?"},
        {"action": "clarify", "message": "Which metric should I analyze?"},
    )
    ask(app, "Show revenue by product")
    if reset == "privacy-refusal":
        assert "personal" in ask(app, "Show customer emails").message
    else:
        policy_file.write_text('{"alice": [1]}', encoding="utf-8")
        ask(app, "/reports")
    assert app.context.pending_clarification == []
    assert ask(app, "2025").report is None
    assert provider.payloads[-1]["catalog"]["pending_clarification"] == []
    assert provider.payloads[-1]["previous_plan"] is None
    assert app.gateway.executions == []


def test_overlong_exchange_stops_before_the_provider_and_allows_a_fresh_question(application):
    app, provider = application(
        {"action": "clarify", "message": "Which period should I analyze?"},
        plan(), report(),
    )
    ask(app, "Show revenue " + "x" * 3500)
    rejected = ask(app, "2025 " + "y" * 1000)
    assert "complete question" in rejected.message
    assert rejected.report is None and app.gateway.executions == []
    assert len(provider.payloads) == 1
    assert app.context.pending_clarification == []
    assert ask(app).evidence[0]["rows"] == [{"revenue": 500}]
    assert provider.payloads[1]["catalog"]["pending_clarification"] == []
