"""Two user journeys through actual Streamlit callbacks, service and SQLite."""
import os
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="Install requirements-ui-lock.txt to run the optional UI tests")
from streamlit.testing.v1 import AppTest

from retail_agent.config import Settings
from retail_agent.gateways import OfflineGateway


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    for name in tuple(os.environ):
        if name.startswith(("GOOGLE_", "GEMINI_", "BIGQUERY_", "MAX_", "QUERY_TIMEOUT_",
                            "TURN_TIMEOUT_", "MIN_GROUP_", "PRODUCT_PERMISSIONS_",
                            "CUSTOMER_PSEUDONYM_")):
            monkeypatch.delenv(name)
    load = Settings.load
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: load(env_file=None)))
    return lambda: AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=10,
    ).run()


def test_browser_analysis_runs_once_and_report_deletion_requires_its_confirmation(ui, monkeypatch):
    executions = []
    execute = OfflineGateway.execute

    def observe(self, *args):
        executions.append(args)
        return execute(self, *args)

    monkeypatch.setattr(OfflineGateway, "execute", observe)
    at = ui()
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    session = at.session_state["retail_session"]
    assert session.context.last_evidence[0]["rows"] == [{"revenue": 900}]
    assert float(at.metric[0].value.replace(",", "")) == 900
    at.run()
    assert len(executions) == 1 and len(session.transcript) == 1
    at.text_input(key="save_title").set_value("Quarterly review")
    at.button(key="save_report").click().run()
    report_id = session.list_reports()[0]["id"]
    at.selectbox(key="report_id").set_value(report_id).run()
    at.button(key="open_report").click().run()
    assert at.session_state["opened_report_id"] == report_id
    at.selectbox(key="delete_kind").set_value("Selected report")
    at.button(key="preview_delete").click().run()
    pending = session.context.pending
    assert [target.report_id for target in pending.targets] == [report_id]
    assert pending.token not in str(at)
    at.run()
    assert len(session.list_reports()) == 1
    at.button(key=f"cancel_delete:{pending.operation_id}").click().run()
    assert len(session.list_reports()) == 1 and session.context.pending is None
    at.button(key="preview_delete").click().run()
    fresh = session.context.pending
    at.button(key=f"confirm_delete:{fresh.operation_id}").click().run()
    assert not session.list_reports() and session.context.pending is None
    assert at.session_state.get("opened_report_id") is None
    assert len(executions) == 1 and not at.exception


def test_browser_permission_change_removes_old_chat_and_open_report_before_new_analysis(ui, tmp_path, monkeypatch):
    policy = tmp_path / "policy.json"
    policy.write_text('{"analyst_north": [1, 2]}', encoding="utf-8")
    monkeypatch.setenv("PRODUCT_PERMISSIONS_FILE", str(policy))
    at = ui()
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    session = at.session_state["retail_session"]
    at.text_input(key="save_title").set_value("Original scope")
    at.button(key="save_report").click().run()
    report_id = session.list_reports()[0]["id"]
    at.selectbox(key="report_id").set_value(report_id).run()
    at.button(key="open_report").click().run()
    assert at.dataframe and at.chat_message
    policy.write_text('{"analyst_north": [3]}', encoding="utf-8")
    at.run()
    assert not at.dataframe and not at.chat_message and not at.metric
    assert not session.transcript and session.context.last_report is None
    assert not at.selectbox(key="report_id").options
    assert at.session_state.get("opened_report_id") is None
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    assert session.context.last_evidence[0]["rows"] == [{"revenue": 12000}]
    assert session.context.last_evidence[0]["product_ids"] == [3]
    assert not at.exception
