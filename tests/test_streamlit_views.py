import math

import pytest

pytest.importorskip("streamlit")
from retail_agent.streamlit_views import chart_data
from retail_agent.service import TurnResult
from streamlit.testing.v1 import AppTest


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
    (["state", "revenue"], [], "revenue"),
    (["revenue"], [{"revenue": 20}], "revenue"),
    (["state", "month", "revenue"], [{"state": "TX", "month": "2025-01", "revenue": 3}], "revenue"),
    (["state", "orders"], [{"state": "TX", "orders": 3}], "revenue"),
    (["state", "revenue"], [{"state": "TX", "revenue": math.inf}], "revenue"),
    (["state", "revenue"], [{"state": "TX", "revenue": math.nan}], "revenue"),
    (["state", "revenue"], [{"state": "TX", "revenue": True}], "revenue"),
    (["state", "revenue"], [{"state": "TX", "revenue": 1}, {"state": "TX", "revenue": 2}], "revenue"),
    (["state", "revenue"], [{"state": "TX"}], "revenue"),
])
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
