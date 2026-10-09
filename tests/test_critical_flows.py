"""Ten consequential analytical journeys; no cloud calls or mirrored arithmetic."""
import json
import sqlite3
from dataclasses import replace

from tests.scenarios import ask, plan, query, report


def test_financial_totals_exclude_unauthorized_returned_and_invalid_items(application, transactions):
    # One order may have several items; one customer may have several orders.
    valid = transactions["order_items"][0]
    transactions["orders"].append({"order_id": 999, "user_id": 910001, "status": "Cancelled"})
    transactions["order_items"].extend([
        {**valid, "id": 901, "status": "Returned", "sale_price": 9000},
        {**valid, "id": 902, "sale_price": -9000},
        {**valid, "id": 903, "order_id": 999, "sale_price": 9000},
        {**valid, "id": 904, "user_id": 910002, "sale_price": 9000},
    ])
    app, _ = application(plan(query(metrics=[
        "revenue", "orders", "purchasing_customers", "units",
        "average_order_value", "spend_per_customer",
    ])), report("Revenue was 500 from 12 orders and 6 buyers."))
    result = ask(app)
    assert result.evidence[0]["rows"] == [{
        "revenue": 500.0, "orders": 12, "purchasing_customers": 6,
        "units": 14, "average_order_value": 41.67, "spend_per_customer": 83.33,
    }]
    assert result.report.summary == "Revenue was 500 from 12 orders and 6 buyers."
    assert not result.report_fallback


def test_clarification_and_followup_keep_the_original_product_and_period(application):
    app, provider = application(
        {"action": "clarify", "message": "Which period?"},
        plan(query(product_ids=[1])), report("Revenue was 480."),
        plan(query(product_ids=[1], dimensions=["month"])), report(),
    )
    assert ask(app, "Show revenue for product 1").report is None
    assert app.gateway.executions == []
    ask(app, "/reports")
    first = ask(app, "January and February 2025")
    assert first.evidence[0]["rows"] == [{"revenue": 480}]
    pending = provider.payloads[1]["catalog"]["pending_clarification"]
    assert pending[0]["content"] == "Show revenue for product 1"
    assert "Show revenue for product 1" in provider.payloads[2]["question"]
    assert "January and February 2025" in provider.payloads[2]["question"]
    followup = ask(app, "Now break it down by month")
    assert provider.payloads[3]["previous_plan"]["queries"][0]["product_ids"] == [1]
    assert followup.evidence[0]["rows"] == [
        {"month": "2025-01", "revenue": 210}, {"month": "2025-02", "revenue": 270},
    ]
    assert followup.evidence[0]["period"] == {"start": "2025-01-01", "end_exclusive": "2025-03-01"}
    assert followup.evidence[0]["product_ids"] == [1]
    assert not app.context.pending_clarification


def test_comparison_includes_last_day_and_keeps_approved_calendar_dates(application, transactions):
    for item in transactions["order_items"]:
        item["created_at"] = item["created_at"].replace("2025-01-15", "2024-01-31").replace("2025-02-15", "2024-02-29")
    summary = ("January 1, 2024 through January 31, 2024: revenue 230. "
               "February 1, 2024 through February 29, 2024: revenue 270.")
    app, provider = application(plan(
        query(start_date="2024-01-01", end_date="2024-01-31"),
        query(start_date="2024-02-01", end_date="2024-03-01"),
    ), report(summary))
    result = ask(app, "Compare revenue from 2024-01-01 through 2024-01-31 "
                 "versus 2024-02-01 through 2024-02-29.")
    assert [item["rows"] for item in result.evidence] == [[{"revenue": 230}], [{"revenue": 270}]]
    assert [item["period_label"] for item in provider.payloads[-1]["evidence"]] == [
        "2024-01-01 through 2024-01-31 (both inclusive)",
        "2024-02-01 through 2024-02-29 (both inclusive)",
    ]
    assert result.report.summary == summary and not result.report_fallback


def test_customer_ranking_and_monthly_report_keep_personal_details_private(application):
    app, provider = application(plan(query(dimensions=["customer"], order_by="revenue", limit=3)), report())
    rows = ask(app, "Top three customers by spending").evidence[0]["rows"]
    assert [row["revenue"] for row in rows] == [130, 110, 90]
    reference = rows[0]["customer"]
    assert reference.startswith("cust_") and len(reference) == 37
    provider.responses.extend([
        plan(query(customer_refs=[reference], dimensions=["month"])),
        report("Contact private-buyer@example.invalid."),
    ])
    monthly = ask(app, f"Break down {reference} by month")
    assert monthly.evidence[0]["rows"] == [
        {"month": "2025-01", "revenue": 60}, {"month": "2025-02", "revenue": 70},
    ]
    assert monthly.report_fallback
    ask(app, "/save Customer analysis")
    stored = app.reports.list_reports("alice")[0]
    exposed = json.dumps(provider.payloads) + stored.body + json.dumps(stored.evidence)
    exposed += app.traces.path.read_text(encoding="utf-8")
    assert "910006" not in exposed
    assert "private-buyer@example.invalid" not in exposed
    assert "PRIVATE_BUYER_NAME" not in exposed
    assert app.gateway.executions[-1]["customer_refs"] == [reference]


def test_private_input_never_reaches_provider_history_storage_or_traces(application, monkeypatch):
    secret = "PRIVATE_SYNTHETIC_CREDENTIAL"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    app, provider = application(plan(), report(), plan(), report())
    refused = ask(app, "Show customer names and email addresses")
    assert refused.report is None and not provider.payloads and not app.gateway.executions
    ask(app, f"Revenue in 2025; {secret} contact private@example.invalid")
    ask(app, "And revenue again?")
    ask(app, "/save Safe report")
    with sqlite3.connect(app.reports.path) as db:
        stored = "\n".join(db.iterdump())
    exposed = json.dumps(provider.payloads) + stored + app.traces.path.read_text(encoding="utf-8")
    assert secret not in exposed and "private@example.invalid" not in exposed
    assert "[REDACTED_SECRET]" in json.dumps(provider.payloads[-2])
    assert app.reports.list_reports("alice")[0].evidence["queries"][0]["rows"] == [{"revenue": 500}]


def test_unauthorized_second_query_blocks_the_entire_plan_before_spending(application):
    app, provider = application(plan(query(product_ids=[1]), query(product_ids=[3])))
    result = ask(app)
    assert result.report is None and result.evidence == []
    assert app.gateway.executions == [] and len(provider.payloads) == 1
    ask(app, "/save Unauthorized comparison")
    assert app.reports.list_reports("alice") == []


def test_report_accepts_real_metadata_but_cannot_turn_billing_into_revenue(application):
    summary = "[bq-7655e3b16480f29b] Products 1 and 2 generated revenue of 500."
    caveat = "Privacy minimum is 4 customers; 0 groups were suppressed."
    app, _ = application(plan(), {**report(summary), "caveats": [caveat]},
                         plan(), report("Revenue was 987654."), min_group_customers=4)
    execute = app.gateway.execute

    def with_metadata(*args):
        outcome = execute(*args)
        return replace(outcome, evidence_id="bq-7655e3b16480f29b",
                       statistics={**outcome.statistics, "bytes_billed": 987654})

    app.gateway.execute = with_metadata
    valid = ask(app)
    assert valid.report.summary == summary and caveat in valid.report.caveats
    assert not valid.report_fallback
    invalid = ask(app)
    assert invalid.report_fallback and "987654" not in invalid.report.to_markdown()
    assert "500" in invalid.report.summary
    ask(app, "/save Verified fallback")
    assert "987654" not in app.reports.list_reports("alice")[0].body
    explained = ask(app, "/explain")
    assert explained.report_fallback and explained.evidence == invalid.evidence


def test_small_segments_never_reach_the_reporter(application, transactions):
    transactions["users"][0]["state"] = "Private sparse region"
    app, provider = application(plan(query(dimensions=["state"])), report())
    result = ask(app)
    assert result.evidence[0]["rows"] == [{"state": "Texas", "revenue": 330}]
    assert result.evidence[0]["suppressed_groups"] == 2
    assert "Private sparse region" not in json.dumps(provider.payloads[-1])
    assert "group_customer_count" not in json.dumps(provider.payloads[-1])


def test_provider_outage_is_bounded_and_next_question_still_works(application):
    private = "PRIVATE_PROVIDER_FAILURE"
    app, provider = application(ConnectionError(private), ConnectionError(private), retries=1)
    failed = ask(app)
    assert failed.report is None and app.gateway.executions == []
    assert len(provider.payloads) == 2
    assert private not in failed.message + app.traces.path.read_text(encoding="utf-8")
    provider.responses.extend([plan(), report()])
    assert ask(app).evidence[0]["rows"] == [{"revenue": 500}]


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
