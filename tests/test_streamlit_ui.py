from pathlib import Path
from dataclasses import replace

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from retail_agent.config import Settings
from retail_agent.gateways import OfflineGateway
from retail_agent.model import OfflineModel
from retail_agent import runtime
from retail_agent.reports import ReportStore


APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


@pytest.fixture(autouse=True)
def isolated_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT", "GEMINI_MODEL",
                 "PRODUCT_PERMISSIONS_FILE", "CUSTOMER_PSEUDONYM_KEY"):
        monkeypatch.delenv(name, raising=False)
    load = Settings.load
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: load(env_file=None)))


def app():
    return AppTest.from_file(str(APP), default_timeout=10).run()


def test_chat_clarifies_and_reruns_without_resubmitting(monkeypatch):
    calls = []
    execute = OfflineGateway.execute

    def record(self, *args):
        calls.append(args)
        return execute(self, *args)

    monkeypatch.setattr(OfflineGateway, "execute", record)
    at = app()
    assert not at.exception
    assert at.radio(key="mode").value == "Offline"
    assert not calls
    at.chat_input(key="question").set_value("Show revenue by product").run()
    assert not calls
    at.chat_input(key="question").set_value("2025").run()
    assert len(at.dataframe) >= 1
    assert len(calls) == 1
    before = len(at.session_state["retail_session"].transcript)
    at.run()
    assert len(at.session_state["retail_session"].transcript) == before
    assert len(calls) == 1
    assert not at.exception


def test_example_runs_once_and_apply_does_not_plan(monkeypatch):
    calls = []
    plan = OfflineModel.plan

    async def record(self, *args, **kwargs):
        calls.append(args)
        return await plan(self, *args, **kwargs)

    monkeypatch.setattr(OfflineModel, "plan", record)
    at = app()
    at.button(key="apply_session").click().run()
    at.run()
    assert calls == []
    at.button(key="example_comparison").click().run()
    at.run()
    assert len(calls) == 1
    assert len(at.session_state["retail_session"].transcript) == 1
    assert not at.exception


def test_live_setup_failure_and_provider_failure_are_safe(monkeypatch):
    at = app()
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    assert at.dataframe
    at.radio(key="mode").set_value("Live")
    at.button(key="apply_session").click().run()
    assert at.error and not at.exception
    assert not at.dataframe and not at.chat_message
    assert not at.session_state["retail_session"].transcript
    at.radio(key="mode").set_value("Offline")
    at.button(key="apply_session").click().run()

    async def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_PROVIDER_DETAILS")

    monkeypatch.setattr(OfflineModel, "plan", fail)
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    assert not at.exception
    assert "PRIVATE_PROVIDER_DETAILS" not in str(at)


def test_applying_configured_live_mode_does_not_invoke_provider(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-construction-only")
    load = Settings.load
    monkeypatch.setattr(Settings, "load", classmethod(
        lambda cls: replace(load(), project="test-project", model="fake-model")
    ))

    class LazyProvider:
        def __init__(self, *args, **kwargs):
            pass

        def begin_turn(self, **kwargs):
            pass  # Local counter reset, not a provider request.

        def __getattr__(self, name):
            pytest.fail(f"Provider invoked during setup: {name}")

    monkeypatch.setattr(runtime, "GeminiModel", LazyProvider)
    monkeypatch.setattr(runtime, "BigQueryGateway", LazyProvider)
    at = app()
    at.radio(key="mode").set_value("Live")
    at.button(key="apply_session").click().run()
    at.run()
    assert at.session_state["retail_session"].mode == "live"
    assert not at.exception and not at.error


def test_browser_sessions_share_only_owned_reports_and_reset_independently():
    first, second = app(), app()
    first.chat_input(key="question").set_value("Show revenue in 2025").run()
    first_session = first.session_state["retail_session"]
    second_session = second.session_state["retail_session"]
    assert first_session.context.conversation_id != second_session.context.conversation_id
    assert first_session.context.last_evidence[0]["rows"] == [{"revenue": 900.0}]
    assert not second_session.transcript and not second_session.context.last_evidence
    save_report(first, "Shared with this actor")
    second.run()
    assert len(second.selectbox(key="report_id").options) == 1
    assert not second.chat_message

    second.text_input(key="actor_id").set_value("analyst_south")
    second.button(key="apply_session").click().run()
    assert not second.selectbox(key="report_id").options
    second.chat_input(key="question").set_value("Show revenue in 2025").run()
    assert second_session.context.last_evidence[0]["rows"] == [{"revenue": 12000.0}]
    assert first_session.context.last_evidence[0]["rows"] == [{"revenue": 900.0}]

    first.button(key="new_conversation").click().run()
    assert not first_session.transcript and not first_session.context.last_evidence
    assert len(first.selectbox(key="report_id").options) == 1
    assert second_session.transcript
    assert not first.exception and not second.exception


@pytest.mark.parametrize("change", ["narrow", "revoke", "missing", "corrupt"])
def test_permission_change_hides_chat_and_open_saved_report(tmp_path, monkeypatch, change):
    policy = tmp_path / "policy.json"
    policy.write_text('{"analyst_north": [1, 2]}', encoding="utf-8")
    monkeypatch.setenv("PRODUCT_PERMISSIONS_FILE", str(policy))
    at = app()
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    report_id = save_report(at)
    at.selectbox(key="report_id").set_value(report_id).run()
    at.button(key="open_report").click().run()
    assert at.session_state["opened_report_id"] == report_id
    assert at.dataframe
    if change == "missing":
        policy.unlink()
    else:
        policy.write_text({"narrow": '{"analyst_north": [1]}',
                           "revoke": "{}", "corrupt": "{"}[change], encoding="utf-8")
    at.run()
    session = at.session_state["retail_session"]
    assert not at.dataframe
    assert not at.chat_message
    assert not session.transcript and not session.context.last_evidence
    assert session.context.last_report is None
    assert not at.exception
    if change == "narrow":
        assert not at.error and not at.selectbox(key="report_id").options
        assert at.session_state.get("opened_report_id") is None
        assert session.refresh_access().allowed_product_ids == (1,)
    else:
        assert at.error


def test_raw_input_is_not_retained_in_widget_or_transcript(monkeypatch):
    secret = "synthetic-private-key-value"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    at = app()
    at.chat_input(key="question").set_value(f"Show revenue in 2025 {secret}").run()
    assert secret not in str(at)
    assert secret not in str(at.session_state["question"])
    assert secret not in repr(at.session_state["retail_session"].transcript)


def save_report(at, title="Quarterly review"):
    at.text_input(key="save_title").set_value(title)
    at.button(key="save_report").click().run()
    return at.session_state["retail_session"].list_reports()[-1]["id"]


def test_save_open_cancel_and_confirm_only_on_click():
    at = app()
    at.chat_input(key="question").set_value("Show revenue by product in 2025").run()
    report_id = save_report(at)
    at.run()
    session = at.session_state["retail_session"]
    assert len(session.list_reports()) == 1
    at.selectbox(key="report_id").set_value(report_id).run()
    at.button(key="open_report").click().run()
    assert at.session_state["opened_report_id"] == report_id
    at.selectbox(key="delete_kind").set_value("Selected report")
    at.button(key="preview_delete").click().run()
    pending = session.context.pending
    assert pending.token not in str(at)
    at.run()
    assert len(session.list_reports()) == 1
    at.button(key=f"cancel_delete:{pending.operation_id}").click().run()
    assert session.context.pending is None and len(session.list_reports()) == 1
    at.button(key="preview_delete").click().run()
    pending = session.context.pending
    at.button(key=f"confirm_delete:{pending.operation_id}").click().run()
    assert not session.list_reports() and session.context.pending is None
    assert at.session_state.get("opened_report_id") is None
    assert not at.exception


def test_literal_wildcards_duplicate_titles_and_superseding_selection():
    at = app()
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    save_report(at, "100%_review")
    save_report(at, "100%_review")
    save_report(at, "Unrelated")
    session = at.session_state["retail_session"]
    assert len(at.selectbox(key="report_id").options) == 3
    at.selectbox(key="delete_kind").set_value("This conversation")
    at.button(key="preview_delete").click().run()
    first = session.context.pending
    assert len(first.targets) == 3
    at.selectbox(key="delete_kind").set_value("Literal mention")
    at.text_input(key="delete_mention").set_value("%_")
    at.button(key="preview_delete").click().run()
    second = session.context.pending
    assert len(second.targets) == 2 and second.operation_id != first.operation_id
    assert f"confirm_delete:{first.operation_id}" not in [b.key for b in at.button]
    with ReportStore(session.runtime.settings.data_dir / "reports.sqlite3") as store:
        assert store.confirm_delete(session.actor_id, first.operation_id, first.token).status == "cancelled"
    session.confirm_delete(first.operation_id)
    assert len(session.list_reports()) == 3
    at.button(key=f"confirm_delete:{second.operation_id}").click().run()
    assert [r["title"] for r in session.list_reports()] == ["Unrelated"]
    assert not at.exception


@pytest.mark.parametrize("outcome", ["expired", "stale", "deleted"])
def test_changed_reports_and_expired_previews_never_delete_again(outcome, monkeypatch):
    clock = [1000.0]
    at = app()
    session = at.session_state["retail_session"]
    path = session.runtime.settings.data_dir / "reports.sqlite3"
    monkeypatch.setattr(session, "_store", lambda: ReportStore(path, now=lambda: clock[0]))
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    report_id = save_report(at)
    at.selectbox(key="report_id").set_value(report_id).run()
    at.button(key="open_report").click().run()
    at.selectbox(key="delete_kind").set_value("Selected report")
    at.button(key="preview_delete").click().run()
    preview = session.context.pending
    if outcome == "expired":
        clock[0] += 301
    else:
        with ReportStore(session.runtime.settings.data_dir / "reports.sqlite3") as other:
            if outcome == "stale":
                other.update(session.actor_id, report_id, title="Changed elsewhere")
            else:
                delete = other.preview_delete(session.actor_id, report_ids=[report_id])
                other.confirm_delete(session.actor_id, delete.operation_id, delete.token)
    at.run()
    if outcome == "deleted":
        assert at.session_state.get("opened_report_id") is None
    at.button(key=f"confirm_delete:{preview.operation_id}").click().run()
    assert session.context.pending is None
    assert len(session.list_reports()) == (0 if outcome == "deleted" else 1)
    assert not at.exception


@pytest.mark.parametrize("token_state", ["active", "consumed", "reset"])
@pytest.mark.parametrize("channel", ["chat", "form"])
def test_confirmation_tokens_cannot_be_saved_as_titles(token_state, channel):
    at = app()
    at.chat_input(key="question").set_value("Show revenue in 2025").run()
    save_report(at, "Original")
    session = at.session_state["retail_session"]
    pending = session.run_command("/delete conversation").pending
    token = pending.token
    if token_state == "consumed":
        session.cancel_delete(pending.operation_id)
    elif token_state == "reset":
        session.reset()
        session.submit("Show revenue in 2025")
    at.run()
    if channel == "chat":
        at.chat_input(key="question").set_value(f"/save {token}").run()
    else:
        at.text_input(key="save_title").set_value(token)
        at.button(key="save_report").click().run()
    assert len(session.list_reports()) == 1
    assert token not in str(at)
    assert token not in repr(session.transcript)
    assert not at.exception
