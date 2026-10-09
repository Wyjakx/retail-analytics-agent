"""Mixed comparisons preserve requested periods before any query is executed."""

from tests.scenarios import ask, plan, query, report


def test_explicit_range_can_be_compared_with_a_separately_requested_year(
    application, transactions,
):
    for item in transactions["order_items"]:
        item["created_at"] = item["created_at"].replace(
            "2025-01-15", "2025-01-31",
        ).replace("2025-02-15", "2024-06-15")
    app, _ = application(plan(
        query(start_date="2025-01-01", end_date="2025-01-31"),
        query(start_date="2024-01-01", end_date="2025-01-01"),
    ), report())

    result = ask(app, "Compare revenue from 2025-01-01 through 2025-01-31 "
                 "with revenue in 2024.")

    assert result.report is not None
    assert [item["rows"] for item in result.evidence] == [
        [{"revenue": 230}], [{"revenue": 270}],
    ]
    assert [item["period"] for item in result.evidence] == [
        {"start": "2025-01-01", "end_exclusive": "2025-02-01"},
        {"start": "2024-01-01", "end_exclusive": "2025-01-01"},
    ]


def test_explicit_range_can_be_compared_with_explicit_all_time(application, transactions):
    for item in transactions["order_items"]:
        item["created_at"] = item["created_at"].replace("2025-01-15", "2025-01-31")
    app, _ = application(plan(
        query(start_date="2025-01-01", end_date="2025-01-31"),
        query(start_date=None, end_date=None),
    ), report())

    result = ask(app, "Compare revenue from 2025-01-01 through 2025-01-31 "
                 "versus revenue for all time.")

    assert result.report is not None
    assert [item["rows"] for item in result.evidence] == [
        [{"revenue": 230}], [{"revenue": 500}],
    ]
    assert result.evidence[1]["period"] == {"start": None, "end_exclusive": None}


def test_mixed_comparison_rejects_a_mismatched_extra_year_before_querying(application):
    app, _ = application(plan(
        query(start_date="2025-01-01", end_date="2025-02-01"),
        query(start_date="2023-01-01", end_date="2024-01-01"),
    ))

    result = ask(app, "Revenue from 2025-01-01 through 2025-01-31 "
                 "versus revenue in 2024")

    assert result.report is None and result.evidence == []
    assert app.gateway.executions == []


def test_mixed_relative_period_cannot_be_silently_omitted(application):
    app, _ = application(plan(
        query(start_date="2025-01-01", end_date="2025-02-01"),
    ))

    result = ask(app, "Revenue from 2025-01-01 through 2025-01-31 "
                 "versus revenue last month")

    assert result.report is None and result.evidence == []
    assert app.gateway.executions == []
    assert "calendar" in result.message.lower(), "The user needs guidance to restate the periods"
