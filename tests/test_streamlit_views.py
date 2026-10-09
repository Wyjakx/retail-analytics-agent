import math

import pytest

pytest.importorskip("streamlit")
from retail_agent.streamlit_views import chart_data
from retail_agent.service import TurnResult
from streamlit.testing.v1 import AppTest
from retail_agent.model import AnalystReport, deterministic_report


def test_chart_sorts_months_without_recalculating_ratios():
    evidence = {"columns": ["month", "average_order_value"], "rows": [
        {"month": "2025-02", "average_order_value": 50.0},
        {"month": "2025-01", "average_order_value": 25.0},
    ]}
    chart = chart_data(evidence, "average_order_value")
    assert (chart.kind, chart.x, chart.y) == ("line", "month", "average_order_value")
    assert chart.rows == list(reversed(evidence["rows"]))
    assert evidence["rows"][0]["month"] == "2025-02"


def test_categorical_chart_keeps_each_returned_ratio():
    evidence = {"columns": ["state", "spend_per_customer", "revenue"], "rows": [
        {"state": "Texas", "spend_per_customer": 20.0, "revenue": 80},
        {"state": "California", "spend_per_customer": 30.0, "revenue": 90},
    ]}
    chart = chart_data(evidence, "spend_per_customer")
    assert chart.kind == "bar"
    assert chart.rows == [{"state": "Texas", "spend_per_customer": 20.0},
                          {"state": "California", "spend_per_customer": 30.0}]


@pytest.mark.parametrize("columns,rows,metric", [
    (["state", "month", "revenue"], [{"state": "TX", "month": "2025-01", "revenue": 3}], "revenue"),
    (["state", "revenue"], [{"state": "TX", "revenue": math.nan}], "revenue"),
    (["state", "revenue"], [{"state": "TX", "revenue": True}], "revenue"),
    (["state", "revenue"], [{"state": "TX", "revenue": 1}, {"state": "TX", "revenue": 2}], "revenue"),
], ids=["multiple-dimensions", "nonfinite-value", "boolean-value", "duplicate-labels"])
def test_ambiguous_or_non_numeric_data_has_no_chart(columns, rows, metric):
    assert chart_data({"columns": columns, "rows": rows}, metric) is None


def test_evidence_labels_period_scope_suppression_and_limit():
    at = AppTest.from_string('''
import streamlit as st
from retail_agent.streamlit_views import render_result
render_result(st.session_state["result"], key_prefix="test")
''')
    at.session_state["result"] = TurnResult("Approved", evidence=[{
        "columns": ["state", "revenue"], "rows": [], "product_ids": [1, 2],
        "period": {"start": "2025-01-01", "end_exclusive": "2025-02-01"},
        "simulated": True, "suppressed_groups": 2, "result_limit": 2, "limit_reached": True,
    }, {"columns": ["revenue"], "rows": [{"revenue": 50}]}])
    at.run()
    assert not at.exception
    assert len(at.dataframe) == 2
    assert any("Additional groups" in warning.value for warning in at.warning)
    assert any("suppressed" in warning.value for warning in at.warning)
    captions = " ".join(item.value for item in at.caption)
    assert "2025-01-01" in captions and "Products included: 1, 2" in captions
    assert "Synthetic" in captions and "Completeness is unknown" in captions


def test_fallback_totals_show_readable_exact_values_and_collapsed_sources():
    evidence = [{
        "evidence_id": "bq-9016977c3a896563",
        "columns": ["revenue", "orders", "purchasing_customers", "units",
                    "average_order_value", "spend_per_customer"],
        "rows": [{"revenue": 237.0, "orders": 4, "purchasing_customers": 4,
                  "units": 4, "average_order_value": 59.25, "spend_per_customer": 59.25}],
        "product_ids": [1, 2], "period": {"start": None, "end_exclusive": None},
        "limit_reached": False,
    }]
    at = AppTest.from_string('''
import streamlit as st
from retail_agent.streamlit_views import render_result
render_result(st.session_state["result"], key_prefix="test")
''')
    at.session_state["result"] = TurnResult(
        "Analysis completed. Use /save TITLE to keep this report.",
        report=deterministic_report(evidence), evidence=evidence, report_fallback=True,
    )
    at.run()
    assert not at.exception
    assert [(m.label, m.value) for m in at.metric] == [
        ("Revenue", "237.00"), ("Orders", "4"), ("Purchasing customers", "4"),
        ("Units sold", "4"), ("Average order value", "59.25"), ("Spend per customer", "59.25"),
    ]
    panels = {panel.label: panel for panel in at.expander}
    assert "View data and sources" in panels and "Scope and definitions" in panels
    assert not panels["View data and sources"].proto.expanded
    assert not panels["Scope and definitions"].proto.expanded
    assert list(panels["View data and sources"].dataframe[0].value["revenue"]) == [237.0]
    assert not any("/save" in item.value for item in at.text)
    summary = at.markdown[0].value
    assert "Revenue was 237.00." in summary
    assert "4 orders" in summary and "4 purchasing customers" in summary
    assert "4 units sold" in summary and "59.25" in summary
    assert "revenue=" not in summary and "average_order_value" not in summary
    assert "Suggested next steps" not in panels
    assert any("generated explanation is unavailable" in item.value for item in at.caption)


def test_fallback_is_disclosed_and_empty_or_grouped_results_do_not_invent_totals():
    at = AppTest.from_string('''
import streamlit as st
from retail_agent.streamlit_views import render_result
render_result(st.session_state["result"], key_prefix="test")
''')
    evidence = [{"columns": ["state", "average_order_value"],
                 "rows": [{"state": "Texas", "average_order_value": 25.0}],
                 "limit_reached": False}]
    at.session_state["result"] = TurnResult(
        "Analysis completed.", report=deterministic_report(evidence),
        evidence=evidence, report_fallback=True,
    )
    at.run()
    assert not at.exception and not at.metric
    assert any("generated explanation is unavailable" in item.value for item in at.caption)
    assert any("Period not specified" in item.value for item in at.caption)
    assert not any("All time" in item.value for item in at.caption)
    evidence[0]["rows"] = []
    at.session_state["result"] = TurnResult(
        "Analysis completed.", report=deterministic_report(evidence), evidence=evidence,
        report_fallback=True,
    )
    at.run()
    assert not at.exception and not at.metric
    assert any("No eligible rows" in item.value for item in at.info)


def test_rendered_prose_moves_known_citations_to_sources_without_erasing_other_text():
    at = AppTest.from_string('''
import streamlit as st
from retail_agent.streamlit_views import render_result
render_result(st.session_state["result"], key_prefix="test")
''')
    at.session_state["result"] = TurnResult("Approved", report=AnalystReport(
        title="Totals",
        summary="Revenue was 237 [bq-123]. Orders were 4 (bq-123). "
                "Units were 4 (evidence_id: bq-123). Literal [bq-123-other].",
    ), evidence=[{"evidence_id": "bq-123", "columns": ["revenue"],
                  "rows": [{"revenue": 237}]}])
    at.run()
    assert not at.exception
    assert at.markdown[0].value == (
        "Revenue was 237. Orders were 4. Units were 4. Literal [bq-123-other]."
    )
    sources = next(panel for panel in at.expander if panel.label == "View data and sources")
    assert any(caption.value == "Source: bq-123" for caption in sources.caption)


def test_fallback_summary_subset_warning_is_visible_even_without_query_truncation():
    evidence = [{
        "evidence_id": "bq-test", "columns": ["state", "revenue"],
        "rows": [{"state": f"State {index}", "revenue": index * 10} for index in range(11)],
        "result_limit": 50, "limit_reached": False,
    }]
    report = deterministic_report(evidence)
    assert len(report.findings) == 10
    at = AppTest.from_string('''
import streamlit as st
from retail_agent.streamlit_views import render_result
render_result(st.session_state["result"], key_prefix="test")
''')
    at.session_state["result"] = TurnResult(
        "Analysis completed.", report=report, evidence=evidence, report_fallback=True,
    )
    at.run()
    assert not at.exception
    assert any("summary displays a subset" in warning.value for warning in at.warning)
    assert not any(panel.warning for panel in at.expander)
    assert len(at.dataframe[0].value) == 11
