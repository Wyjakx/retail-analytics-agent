from copy import deepcopy

import pytest

from retail_agent.analytics import AnalysisPlan, QuerySpec
from retail_agent.model import AnalystReport
from retail_agent.safety import UnsafeOutput, validate_report


@pytest.fixture
def analysis():
    evidence = [{
        "evidence_id": "bq-9016977c3a896563",
        "rows": [{"revenue": 237.0, "orders": 4, "purchasing_customers": 4,
                  "units": 4, "average_order_value": 59.25, "spend_per_customer": 59.25}],
        "product_ids": [1, 2], "result_limit": 10, "suppressed_groups": 0,
        "statistics": {"privacy_applied": True, "minimum_customers": 3,
                       "bytes_billed": 987654},
    }]
    plan = AnalysisPlan(queries=[QuerySpec(metrics=list(evidence[0]["rows"][0]), limit=10)])
    return evidence, plan


@pytest.mark.parametrize("minimum,suppressed", [(3, 0), (7, 2)])
def test_report_accepts_actual_privacy_metadata_and_effective_product_scope(
    analysis, minimum, suppressed,
):
    evidence, plan = analysis
    evidence[0]["statistics"]["minimum_customers"] = minimum
    evidence[0]["suppressed_groups"] = suppressed
    report = AnalystReport(
        title="Product totals", summary="Products 1 and 2 generated revenue of 237.0.",
        caveats=[f"The minimum group size is {minimum} customers; {suppressed} groups were suppressed."],
    )
    validate_report(report, evidence, plan)


@pytest.mark.parametrize("claim", [
    "Revenue was 999.0.", "The minimum group size was 8 customers.",
    "There were 9 suppressed groups.", "Revenue was 987654.",
])
def test_metadata_does_not_authorize_invented_numbers_or_operational_statistics(analysis, claim):
    evidence, plan = analysis
    with pytest.raises(UnsafeOutput, match="unverified numerical"):
        validate_report(AnalystReport(title="Totals", summary=claim), evidence, plan)


@pytest.mark.parametrize("metadata", [
    {"privacy_applied": False, "minimum_customers": 3},
    {"privacy_applied": True, "minimum_customers": "3"},
    {},
])
def test_unapproved_or_malformed_privacy_metadata_does_not_supply_numbers(analysis, metadata):
    evidence, plan = deepcopy(analysis)
    evidence[0]["statistics"] = metadata
    with pytest.raises(UnsafeOutput, match="unverified numerical"):
        validate_report(AnalystReport(title="Totals", summary="Minimum size: 3."), evidence, plan)


@pytest.fixture
def comparison():
    plan = AnalysisPlan(queries=[
        QuerySpec(metrics=["revenue", "orders"], product_ids=[3, 4], limit=10,
                  start_date="2020-01-01", end_date="2024-01-01"),
        QuerySpec(metrics=["revenue", "orders"], product_ids=[3, 4], limit=10,
                  start_date="2024-01-01", end_date="2027-01-01"),
    ])
    evidence = [
        {"rows": [{"revenue": 285.5, "orders": 3}]},
        {"rows": [{"revenue": 571.0, "orders": 6}]},
    ]
    return evidence, plan


@pytest.mark.parametrize("periods", [
    ("January 1, 2020 through December 31, 2023", "January 1, 2024 through December 31, 2026"),
    ("2020-01-01 through 2023-12-31", "2024-01-01 through 2026-12-31"),
    ("1 January 2020 through 31 December 2023", "1 January 2024 through 31 December 2026"),
    ("1 janvier 2020 au 31 décembre 2023", "1 janvier 2024 au 31 décembre 2026"),
])
def test_comparison_accepts_equivalent_inclusive_calendar_dates(comparison, periods):
    evidence, plan = comparison
    report = AnalystReport(
        title="Products 3 and 4",
        summary=f"From {periods[0]}: revenue 285.5, 3 orders. "
                f"From {periods[1]}: revenue 571.0, 6 orders.",
    )
    validate_report(report, evidence, plan)


@pytest.mark.parametrize("claim", [
    "Revenue was 31.", "Revenue was 2023.", "Revenue grew by 100%.",
    "The period ended December 30, 2023.",
    "The period ended December 31, 2025.",
    "The period ended December 31, 20230.",
])
def test_calendar_aliases_do_not_authorize_new_business_numbers_or_dates(comparison, claim):
    evidence, plan = comparison
    report = AnalystReport(
        title="Comparison", summary="Through December 31, 2023, revenue was 285.5. " + claim,
    )
    with pytest.raises(UnsafeOutput, match="unverified numerical"):
        validate_report(report, evidence, plan)


def test_inclusive_date_uses_calendar_arithmetic_for_leap_years():
    plan = AnalysisPlan(queries=[QuerySpec(
        metrics=["revenue"], start_date="2024-02-01", end_date="2024-03-01",
    )])
    evidence = [{"rows": [{"revenue": 285.5}]}]
    report = AnalystReport(title="Revenue", summary="Through February 29, 2024: revenue 285.5.")
    validate_report(report, evidence, plan)


def test_all_time_does_not_supply_calendar_dates(analysis):
    evidence, plan = analysis
    with pytest.raises(UnsafeOutput, match="unverified numerical"):
        validate_report(AnalystReport(title="Revenue", summary="Through December 31, 2023."), evidence, plan)
