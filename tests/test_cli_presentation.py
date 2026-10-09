from copy import deepcopy
from io import StringIO

import pytest
from rich.console import Console

from retail_agent.analytics import AnalysisPlan, QuerySpec
from retail_agent.cli import present
from retail_agent.model import AnalystReport
from retail_agent.service import TurnResult


def render(result, *, details=False, width=80):
    output = StringIO()
    present(Console(file=output, width=width, color_system=None), result, details)
    return output.getvalue()


@pytest.fixture
def totals():
    return TurnResult(
        "Analysis completed. Use /save TITLE to keep this report.",
        report=AnalystReport(
            title="Product totals", summary="Revenue was 237.0 [bq-123abc].",
            findings=["There were 4 orders [bq-123abc]."],
            caveats=["Definitions: revenue: Approved definition.",
                     "Product scope: 1, 2. Periods (UTC): explicit all-time."]
        ),
        evidence=[{
            "evidence_id": "bq-123abc", "columns": ["revenue", "orders", "purchasing_customers",
                "units", "average_order_value", "spend_per_customer"],
            "rows": [{"revenue": 237.0, "orders": 4, "purchasing_customers": 4,
                      "units": 4, "average_order_value": 59.25, "spend_per_customer": 59.25}],
            "product_ids": [1, 2], "period": {"start": None, "end_exclusive": None},
            "result_limit": 50, "limit_reached": False,
        }],
        plan=AnalysisPlan(queries=[QuerySpec(metrics=["revenue"], product_ids=[1, 2])]),
        request_id="request-test",
    )


@pytest.mark.parametrize("width", [60, 80, 120])
def test_default_totals_are_readable_and_keep_technical_details_optional(totals, width):
    output = render(totals, width=width)
    assert "Revenue was 237.0." in output
    for label in ("Revenue", "Orders", "Purchasing customers", "Units sold",
                  "Average order value", "Spend per customer"):
        assert label in output
    assert "237.00" in output and "59.25" in output
    assert "Products: 1, 2" in output and "All time" in output
    assert "/explain" in output and "/save" in output
    assert "bq-123abc" not in output and "request-test" not in output
    assert "purchasing_customers" not in output and "Definitions:" not in output
    assert "Findings" not in output and "Caveats" not in output


def test_detailed_output_retains_report_sources_plan_and_request(totals):
    output = render(totals, details=True)
    assert "Approved definition" in output and "Findings" in output
    assert "bq-123abc" in output and "request-test" in output
    assert '"queries"' in output and '"product_ids"' in output


def test_fallback_scope_and_data_warnings_remain_visible(totals):
    totals.report_fallback = True
    totals.evidence[0].update(simulated=True, suppressed_groups=2, limit_reached=True)
    totals.report.caveats += ["This summary displays a subset of the approved rows.",
                             "This comparison does not establish causality."]
    output = render(totals)
    assert "generated explanation is unavailable" in output
    assert "Synthetic" in output and "suppressed" in output
    assert "limit (50)" in output and "subset" in output
    assert "does not establish causality" in output


def test_grouped_results_keep_each_ratio_and_comparison_period(totals):
    totals.report = None
    totals.message = "Analysis incomplete: a later query failed."
    totals.evidence = [{
        "evidence_id": "bq-grouped", "columns": ["state", "average_order_value"],
        "rows": [{"state": "Texas", "average_order_value": 25.12},
                 {"state": "California", "average_order_value": 90.78}],
        "product_ids": [1, 2], "limit_reached": False,
        "period": {"start": "2025-01-01", "end_exclusive": "2025-02-01"},
    }]
    before = deepcopy(totals.evidence)
    output = render(totals)
    assert "incomplete" in output
    assert "Texas" in output and "California" in output
    assert "25.12" in output and "90.78" in output and "115.90" not in output
    assert "2025-01-01" in output and "2025-02-01" in output
    assert "Average order value" in output and totals.evidence == before


def test_empty_results_do_not_invent_zero_totals_or_unknown_periods(totals):
    totals.evidence[0]["rows"] = []
    totals.evidence[0].pop("period")
    totals.report = None
    output = render(totals)
    assert "No eligible rows" in output and "Period not specified" in output
    assert "All time" not in output and "0.00" not in output


@pytest.mark.parametrize("width", [50, 60, 70])
def test_wide_customer_results_preserve_literal_references_without_ellipsis(totals, width):
    reference = "cust_" + "ab" * 16
    totals.evidence[0]["columns"].insert(0, "customer")
    totals.evidence[0]["rows"][0]["customer"] = reference
    output = render(totals, width=width)
    assert reference in "".join(output.split()) and "237.00" in output and "59.25" in output
    assert "Purchasing customers" in output and "…" not in output


def test_model_text_cannot_be_interpreted_as_rich_markup(totals):
    totals.report.summary = "Literal [bold]text[/bold] [bq-123abc]."
    output = render(totals)
    assert "[bold]text[/bold]" in output
    assert "bq-123abc" not in output


def test_narrow_report_library_keeps_full_ids_and_literal_titles():
    report_id = "1234567890abcdef1234567890abcdef"
    title = "[bold]literal[/bold] title of a saved report"
    output = render(TurnResult("Your reports", reports=[{"id": report_id, "title": title}]), width=60)
    cells = [line.split("│") for line in output.splitlines() if "│" in line]
    identifiers = "".join("".join(row[1].split()) for row in cells)
    titles = "".join("".join(row[2].split()) for row in cells)
    assert report_id in identifiers and "[bold]literal[/bold]" in titles
    assert "…" not in output
