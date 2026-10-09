"""Observable application behavior using real ADK, arithmetic, policy and SQLite."""

import asyncio
import json
import sqlite3

import pytest

from retail_agent.model import OfflineModel
from tests.scenarios import ask, plan, query, report


def test_analysis_computes_all_six_metrics_from_authorized_items(application):
    app, _ = application(plan(query(metrics=[
        "revenue", "orders", "purchasing_customers", "units",
        "average_order_value", "spend_per_customer",
    ])), report("Revenue was 500 from 12 orders and 6 buyers."))
    result = ask(app)
    assert result.evidence[0]["rows"] == [{
        "revenue": 500.0, "orders": 12, "purchasing_customers": 6,
        "units": 14, "average_order_value": 41.67, "spend_per_customer": 83.33,
    }]
    assert result.report.title == "Provider report"
    assert "500" in result.report.summary


def test_comparison_followup_keeps_dates_and_product_scope(application):
    app, _ = application(model=OfflineModel())
    first = ask(app, "Compare revenue by state for product 1 in January versus February 2025")
    assert [item["rows"] for item in first.evidence] == [
        [{"state": "California", "revenue": 60}, {"state": "Texas", "revenue": 150}],
        [{"state": "California", "revenue": 90}, {"state": "Texas", "revenue": 180}],
    ]
    followup = ask(app, "Only Texas")
    assert [item["rows"] for item in followup.evidence] == [
        [{"state": "Texas", "revenue": 150}], [{"state": "Texas", "revenue": 180}],
    ]
    assert [item["period"] for item in followup.evidence] == [
        {"start": "2025-01-01", "end_exclusive": "2025-02-01"},
        {"start": "2025-02-01", "end_exclusive": "2025-03-01"},
    ]


def test_period_uses_utc_with_inclusive_start_and_exclusive_end(application, transactions):
    for item in transactions["order_items"]:
        if item["product_id"] != 1:
            continue
        first_half = item["user_id"] <= 910003
        if "-01-15" in item["created_at"]:
            # Both are outside January in UTC, despite their local January date.
            item["created_at"] = (
                "2025-01-01T00:30:00+01:00" if first_half
                else "2025-01-31T23:30:00-01:00"
            )
        else:
            item["created_at"] = (
                "2025-01-01T00:00:00Z" if first_half else "2025-02-01T00:00:00Z"
            )
    app, _ = application(plan(query(product_ids=[1], end_date="2025-02-01")), report())
    assert ask(app).evidence[0]["rows"] == [{"revenue": 90}]


def test_returns_and_invalid_order_items_do_not_inflate_revenue(application, transactions):
    valid = transactions["order_items"][0]
    for changes in [
        {"status": "Returned"}, {"sale_price": -1000}, {"order_id": 999999},
        {"user_id": 910002},
    ]:
        transactions["order_items"].append({
            **valid, "sale_price": 9999, **changes, "id": len(transactions["order_items"]) + 1,
        })
    cancelled_order = {"order_id": 999, "user_id": 910001, "status": "Cancelled"}
    transactions["orders"].append(cancelled_order)
    transactions["order_items"].append({
        **valid, "id": 999, "order_id": 999, "sale_price": 9999,
    })
    app, _ = application(plan(), report())
    assert ask(app).evidence[0]["rows"] == [{"revenue": 500}]


def test_customer_ranking_and_followup_expose_only_scoped_pseudonyms(application):
    app, provider = application(
        plan(query(dimensions=["customer"], order_by="revenue", limit=3)), report(),
    )
    ranked = ask(app, "Top three customers by spending")
    rows = ranked.evidence[0]["rows"]
    assert [row["revenue"] for row in rows] == [130, 110, 90]
    reference = rows[0]["customer"]
    provider.responses.extend([
        plan(query(customer_refs=[reference], dimensions=["month"])),
        report(f"Spending for {reference} follows the supplied monthly evidence."),
    ])
    followup = ask(app, f"Break down {reference} by month")
    assert followup.evidence[0]["rows"] == [
        {"month": "2025-01", "revenue": 60}, {"month": "2025-02", "revenue": 70},
    ]
    ask(app, "/save Customer analysis")
    stored = app.reports.list_reports("alice")[0]
    exposed = json.dumps(provider.payloads) + stored.body + json.dumps(stored.evidence)
    for private in ("910006", "PRIVATE_BUYER_NAME", "private-buyer@example.invalid", "k" * 32):
        assert private not in exposed
        assert private not in app.traces.path.read_text(encoding="utf-8")
    assert reference in stored.body or reference in json.dumps(stored.evidence)


def test_small_segments_never_reach_the_reporter(application, transactions):
    transactions["users"][0]["state"] = "Private sparse region"
    app, provider = application(plan(query(dimensions=["state"])), report())
    result = ask(app)
    assert result.evidence[0]["rows"] == [{"state": "Texas", "revenue": 330}]
    assert result.evidence[0]["suppressed_groups"] == 2
    assert "Private sparse region" not in json.dumps(provider.payloads[-1])
    assert "group_customer_count" not in json.dumps(provider.payloads[-1])


@pytest.mark.parametrize("widen,executions", [(False, 2), (True, 1)], ids=["one-retry", "no-widening"])
def test_empty_results_allow_only_one_equivalent_correction(application, widen, executions):
    requested = query(start_date="2040-01-01", end_date="2041-01-01")
    correction = query(start_date=None, end_date=None) if widen else requested
    app, _ = application(plan(requested), plan(correction), report("No eligible results."))
    result = ask(app, "Revenue in 2040")
    assert result.evidence[0]["rows"] == []
    assert [(call["start_date"], call["end_date"]) for call in app.gateway.executions] == [
        ("2040-01-01", "2041-01-01"),
    ] * executions


def test_clarification_does_not_submit_a_query(application):
    app, _ = application({"action": "clarify", "message": "Which period should I analyze?"})
    result = ask(app, "Revenue")
    assert "period" in result.message
    assert result.report is None and result.evidence == []
    assert app.gateway.executions == []


@pytest.mark.parametrize("unsafe", [
    "Revenue was 9999.", "Customer ID 6 is the best buyer.",
    "Review cust_ffffffffffffffffffffffffffffffff.", "Contact private@example.invalid.",
    "PRIVATE_SYNTHETIC_CREDENTIAL",
], ids=["invented-amount", "raw-id-matches-valid-number", "invented-customer", "personal-contact", "credential"])
def test_unsafe_report_is_replaced_before_display_and_save(application, monkeypatch, unsafe):
    monkeypatch.setenv("GOOGLE_API_KEY", "PRIVATE_SYNTHETIC_CREDENTIAL")
    app, _ = application(plan(query(metrics=["revenue", "purchasing_customers"])), report(unsafe))
    result = ask(app)
    assert result.report.title == "Retail analysis"
    assert unsafe not in result.report.to_markdown()
    assert "revenue=500" in result.report.to_markdown()
    assert "could not be verified" in result.message
    ask(app, "/save Verified fallback")
    stored = app.reports.list_reports("alice")[0]
    assert "revenue=500" in stored.body and unsafe not in stored.body


def test_digit_leading_evidence_citation_keeps_the_valid_model_report(application):
    def cite(payload):
        evidence_id = payload["evidence"][0]["evidence_id"]
        return report(f"[{evidence_id}] Revenue was 500.")

    app, _ = application(plan(), cite)
    # Choose an opaque ID without replacing the gateway's calculations.
    execute = app.gateway.execute

    def with_citation(*args):
        from dataclasses import replace
        return replace(execute(*args), evidence_id="bq-7655e3b16480f29b")

    app.gateway.execute = with_citation
    result = ask(app)
    assert result.report.title == "Provider report"
    assert "[bq-7655e3b16480f29b] Revenue was 500." == result.report.summary


def test_provider_outage_is_bounded_and_next_question_still_works(application):
    private = "PRIVATE_PROVIDER_FAILURE"
    app, provider = application(ConnectionError(private), ConnectionError(private), retries=1)
    failed = ask(app)
    assert failed.report is None and app.gateway.executions == []
    assert len(provider.payloads) == 2
    assert private not in failed.message + app.traces.path.read_text(encoding="utf-8")
    provider.responses.extend([plan(), report()])
    assert ask(app).evidence[0]["rows"] == [{"revenue": 500}]


def test_request_deadline_cancels_the_provider(application):
    cancelled = []

    async def hang(payload):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    app, _ = application(hang, turn_timeout=1)
    result = ask(app)
    assert "timed out" in result.message
    assert result.report is None and app.gateway.executions == []
    assert cancelled == [True]


def test_model_attempt_budget_is_shared_by_planning_correction_and_reporting(application):
    # Two transient failures followed by invalid output, then the same retry
    # pattern followed by a valid correction: all six allowed calls are spent.
    app, provider = application(
        ConnectionError(), ConnectionError(), plan(query(sql="SELECT forbidden")),
        ConnectionError(), ConnectionError(), plan(), retries=2,
    )
    result = ask(app)
    assert len(provider.payloads) == 6
    assert result.evidence[0]["rows"] == [{"revenue": 500}]
    assert result.report.title == "Retail analysis"
    assert "could not be verified" in result.message
    # The budget belongs to one request, so a later question can succeed.
    provider.responses.extend([plan(), report()])
    assert ask(app).report.title == "Provider report"
    assert len(provider.payloads) == 8


def test_failed_second_query_cannot_be_saved_as_a_complete_comparison(application):
    from google.api_core.exceptions import BadRequest

    first = query(end_date="2025-02-01")
    second = query(start_date="2025-02-01")
    app, _ = application(plan(first, second))
    execute = app.gateway.execute

    def fail_second(spec, scope, budget):
        if spec.start_date.month == 2:
            budget.begin_query()
            raise BadRequest("PRIVATE_QUERY_ERROR")
        return execute(spec, scope, budget)

    app.gateway.execute = fail_second
    result = ask(app)
    assert result.report is None
    assert result.evidence[0]["rows"] == [{"revenue": 230}]
    ask(app, "/save Incomplete comparison")
    assert app.reports.list_reports("alice") == []


@pytest.mark.parametrize("products,max_queries", [([1, 3], 3), ([1, 2], 1)], ids=["permissions", "budget"])
def test_entire_plan_is_checked_before_spending_on_its_first_query(application, products, max_queries):
    app, _ = application(plan(*(query(product_ids=[p]) for p in products)), max_queries=max_queries)
    result = ask(app)
    assert result.report is None
    assert app.gateway.executions == []


@pytest.mark.parametrize("injection", [
    {"sql": "SELECT email FROM private_users"},
    {"order_by": "revenue", "order_direction": "DESC; DELETE FROM private_users; --"},
], ids=["raw-sql", "sort-injection"])
def test_untrusted_model_plan_is_rejected_with_bounded_correction(application, injection):
    malicious = plan(query(**injection))
    app, provider = application(malicious, malicious)
    result = ask(app)
    assert result.report is None and app.gateway.executions == []
    assert len(provider.payloads) == 2
    assert "private_users" not in result.message + app.traces.path.read_text(encoding="utf-8")


def test_personal_identity_request_is_refused_before_the_provider(application):
    app, provider = application()
    result = ask(app, "Give me customer emails")
    assert result.report is None and "personal" in result.message.lower()
    assert provider.payloads == [] and app.gateway.executions == []


def test_pasted_credentials_and_contacts_never_reach_provider_history_or_storage(application, monkeypatch):
    secret = "PRIVATE_SYNTHETIC_CREDENTIAL"
    pasted_key = "AI" + "za" + "aB_" * 11 + "c-"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    app, provider = application(plan(), report(), plan(), report())
    ask(app, f"Revenue in 2025; {secret} {pasted_key} contact private@example.invalid")
    ask(app, "And revenue again?")
    ask(app, "/save Safe report")
    with sqlite3.connect(app.reports.path) as connection:
        stored = "\n".join(connection.iterdump())
    exposed = json.dumps(provider.payloads) + stored + app.traces.path.read_text(encoding="utf-8")
    assert secret not in exposed and pasted_key not in exposed and "private@example.invalid" not in exposed
    assert "[REDACTED_SECRET]" in json.dumps(provider.payloads[-2])


def test_secret_in_source_evidence_stops_before_reporting(application, transactions, monkeypatch):
    secret = "PRIVATE_SYNTHETIC_CREDENTIAL"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    transactions["products"][0]["name"] = secret
    app, provider = application(plan(query(dimensions=["product"])))
    result = ask(app)
    assert result.report is None and result.evidence == []
    assert len(provider.payloads) == 1
    assert secret not in result.message + json.dumps(provider.payloads)


def test_revoking_permissions_invalidates_previous_context_and_pending_delete(application, policy_file):
    app, provider = application(plan(query(dimensions=["customer"], order_by="revenue", limit=1)), report())
    ranked = ask(app)
    reference = ranked.evidence[0]["rows"][0]["customer"]
    ask(app, "/save Original scope")
    pending = ask(app, "/delete conversation").pending
    policy_file.write_text('{"alice": [3], "bob": [3]}', encoding="utf-8")
    assert ask(app, "/reports").reports == []
    ask(app, f"/confirm {pending.token}")
    assert len(app.reports.list_reports("alice")) == 1
    provider.responses.append(plan(query(customer_refs=[reference])))
    assert ask(app, f"Spending for {reference}").report is None
    sent = provider.payloads[-1]
    assert sent["history"] == [] and sent["previous_plan"] is None
    assert sent["catalog"]["allowed_customer_refs"] == []
    assert len(app.gateway.executions) == 1
    ask(app, "/save Revoked context")
    assert len(app.reports.list_reports("alice")) == 1


def test_permissions_changed_during_reporting_prevent_display_and_save(application, policy_file):
    def revoke(payload):
        policy_file.write_text('{"alice": [3]}', encoding="utf-8")
        return report("Revenue was 500.")

    app, _ = application(plan(), revoke)
    result = ask(app)
    assert result.report is None and result.evidence == []
    ask(app, "/save Revoked while running")
    assert app.reports.list_reports("alice") == []


def test_saved_report_survives_restart_but_is_hidden_from_another_actor(application):
    app, _ = application(plan(), report("Revenue was 500."))
    ask(app)
    ask(app, "/save Quarterly review")
    restarted, _ = application()
    assert [row["title"] for row in ask(restarted, "/reports").reports] == ["Quarterly review"]
    saved = restarted.reports.list_reports("alice")[0]
    assert saved.evidence["queries"][0]["rows"] == [{"revenue": 500}]
    other, provider = application(actor="bob")
    assert ask(other, "/reports").reports == []
    assert ask(other, f"/delete id {saved.report_id}").pending is None
    assert provider.payloads == []


def test_save_rejects_a_title_containing_credentials(application, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "PRIVATE_SYNTHETIC_CREDENTIAL")
    app, provider = application(plan(), report())
    ask(app)
    result = ask(app, "/save PRIVATE_SYNTHETIC_CREDENTIAL")
    assert app.reports.list_reports("alice") == []
    assert "PRIVATE_SYNTHETIC_CREDENTIAL" not in result.message
    assert len(provider.payloads) == 2


def test_delete_requires_exact_confirmation_and_keeps_reports_outside_the_preview(application):
    app, provider = application(plan(), report(), plan(), report())
    ask(app)
    ask(app, "/save Budget 100%_done")
    ask(app, "/save Other report")
    pending = ask(app, "Delete all reports mentioning 100%_done").pending
    assert [target.title for target in pending.targets] == ["Budget 100%_done"]
    assert len(app.reports.list_reports("alice")) == 2
    ask(app, f"Revenue for 2025; copied preview token {pending.token}")
    assert "[REDACTED_CONFIRMATION]" in provider.payloads[2]["question"]
    ask(app, "yes")
    ask(app, "/confirm wrong-token")
    assert len(app.reports.list_reports("alice")) == 2
    ask(app, "/save Created after preview")
    deleted = ask(app, f"/confirm {pending.token}")
    assert ask(app, f"/confirm {pending.token}").message == deleted.message
    assert {row.title for row in app.reports.list_reports("alice")} == {
        "Other report", "Created after preview",
    }
    assert len(provider.payloads) == 4 and pending.token not in json.dumps(provider.payloads)


def test_cancelled_delete_cannot_be_confirmed_later(application):
    app, _ = application()
    saved = app.reports.save("alice", app.context.conversation_id, "Keep", "Body", {"product_ids": [1]})
    pending = ask(app, "/delete conversation").pending
    ask(app, "/cancel")
    ask(app, f"/confirm {pending.token}")
    assert app.reports.list_reports("alice") == [saved]


def test_sensitive_content_is_absent_from_correlated_application_traces(application):
    app, _ = application(plan(), report("PRIVATE_REPORT_BODY"))
    ask(app, "Revenue in 2025 PRIVATE_QUESTION")
    ask(app, "/save PRIVATE_TITLE")
    pending = ask(app, "/delete conversation").pending
    ask(app, f"/confirm {pending.token}")
    with pytest.raises(ValueError):
        app.traces.event(raw_prompt="PRIVATE_QUESTION")
    raw = app.traces.path.read_text(encoding="utf-8")
    for private in ("PRIVATE_QUESTION", "PRIVATE_REPORT_BODY", "PRIVATE_TITLE", pending.token):
        assert private not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    assert all(event.get("request_id") for event in events)
    assert any(event.get("operation_id") == pending.operation_id for event in events)


def test_unwritable_traces_cannot_misreport_a_committed_deletion(application, tmp_path, caplog):
    app, _ = application()
    app.reports.save("alice", app.context.conversation_id, "Delete me", "Body", {"product_ids": [1]})
    pending = ask(app, "/delete conversation").pending
    # Opening a directory as a trace file fails on both Windows and Unix.
    app.traces.path = tmp_path
    result = ask(app, f"/confirm {pending.token}")
    assert "Deleted 1" in result.message
    assert app.reports.list_reports("alice") == []
    assert "Trace recording is unavailable" in caplog.text
