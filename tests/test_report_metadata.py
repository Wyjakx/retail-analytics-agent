"""Reporting must preserve legitimate context without turning metadata into sales."""

from dataclasses import replace

import pytest

from tests.scenarios import ask, plan, query, report


def test_report_can_explain_effective_scope_and_actual_privacy_rules_then_be_saved(application):
    summary = "Products 1 and 2 generated revenue of 500."
    caveat = "The minimum group size is 4 customers; 0 groups were suppressed."
    app, provider = application(
        plan(), {**report(summary), "caveats": [caveat]}, min_group_customers=4,
    )
    result = ask(app)
    assert result.evidence[0]["rows"] == [{"revenue": 500}]
    assert result.evidence[0]["product_ids"] == [1, 2]
    assert provider.payloads[-1]["evidence"][0]["statistics"]["minimum_customers"] == 4
    assert result.report.summary == summary and not result.report_fallback
    assert caveat in result.report.caveats
    ask(app, "/save Privacy-aware totals")
    stored = app.reports.list_reports("alice")[0]
    assert summary in stored.body and caveat in stored.body


def test_billing_metadata_cannot_be_reported_or_saved_as_revenue(application):
    claim = "Revenue was 987654."
    app, provider = application(plan(), report(claim))
    execute = app.gateway.execute

    def with_billing_metadata(*args):
        outcome = execute(*args)
        return replace(outcome, statistics={**outcome.statistics, "bytes_billed": 987654})

    app.gateway.execute = with_billing_metadata
    result = ask(app)
    assert provider.payloads[-1]["evidence"][0]["statistics"]["bytes_billed"] == 987654
    assert result.evidence[0]["rows"] == [{"revenue": 500}]
    assert result.report_fallback and claim not in result.report.to_markdown()
    assert "500" in result.report.summary
    ask(app, "/save Verified totals")
    stored = app.reports.list_reports("alice")[0]
    assert claim not in stored.body and "500" in stored.body


def test_comparison_report_accepts_inclusive_dates_and_leap_day_without_fallback(application, transactions):
    for item in transactions["order_items"]:
        item["created_at"] = item["created_at"].replace("2025-", "2024-")
    summary = (
        "January 1, 2024 through January 31, 2024: revenue 230. "
        "February 1, 2024 through February 29, 2024: revenue 270."
    )
    app, provider = application(plan(
        query(start_date="2024-01-01", end_date="2024-02-01"),
        query(start_date="2024-02-01", end_date="2024-03-01"),
    ), report(summary))
    result = ask(app, "Compare revenue in January and February 2024")
    assert [item["rows"] for item in result.evidence] == [[{"revenue": 230}], [{"revenue": 270}]]
    assert [item["period_label"] for item in provider.payloads[-1]["evidence"]] == [
        "2024-01-01 through 2024-01-31 (both inclusive)",
        "2024-02-01 through 2024-02-29 (both inclusive)",
    ]
    assert result.report.summary == summary and not result.report_fallback
    ask(app, "/save Leap-year comparison")
    assert summary in app.reports.list_reports("alice")[0].body


@pytest.mark.parametrize("claim,all_time", [
    ("Through February 28, 2025, revenue was 28.", False),
    ("The period ended February 27, 2025.", False),
    ("The period ended February 28, 2025.", True),
], ids=["date-is-not-an-amount", "invented-end-date", "all-time-has-no-end-date"])
def test_report_dates_cannot_supply_unapproved_amounts_or_periods(application, claim, all_time):
    spec = query(start_date=None, end_date=None) if all_time else query()
    app, _ = application(plan(spec), report(claim))
    result = ask(app, "Revenue for all time" if all_time else "Revenue in January and February 2025")
    assert result.evidence[0]["rows"] == [{"revenue": 500}]
    assert result.report_fallback
    assert claim not in result.report.to_markdown()
    assert "500" in result.report.summary
