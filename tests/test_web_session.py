import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from retail_agent.config import ConfigurationError, Settings
from retail_agent.reports import ReportStore
from retail_agent.web_session import WebSession


@pytest.fixture
def session(tmp_path):
    return WebSession(settings=Settings(data_dir=tmp_path))


def test_chat_open_does_not_cache_saved_body_or_evidence(session):
    session.submit("Show revenue in 2025")
    session.run_command("/save Shared")
    report_id = session.list_reports()[0]["id"]
    result = session.submit(f"/open {report_id}")
    assert result.saved_report
    retained = session.transcript[-1].result
    assert retained.saved_report is None and retained.evidence == []


def test_sqlite_store_is_owned_by_each_calling_thread(session):
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert worker.submit(session.submit, "Show revenue in 2025").result().report
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert "Saved report" in worker.submit(session.run_command, "/save Thread test").result().message
    assert len(session.run_command("/reports").reports) == 1


def test_reset_cancels_pending_and_clears_all_conversation_state(session):
    result = session.submit("Top 5 customers by spending in 2025")
    ref = result.evidence[0]["rows"][0]["customer"]
    session.run_command("/save Ranking")
    pending = session.run_command("/delete conversation").pending
    token = pending.token
    old_id = session.context.conversation_id
    data_dir = session.runtime.settings.data_dir
    session.reset()
    assert session.runtime.settings.data_dir == data_dir
    assert session.context.conversation_id != old_id
    assert session.transcript == []
    assert not session.context.customer_refs
    assert not session.context.pending_clarification
    assert session.context.pending is None
    assert session.submit(f"Break down {ref} by month").report is None
    with ReportStore(session.runtime.settings.data_dir / "reports.sqlite3") as store:
        assert store.confirm_delete("analyst_north", pending.operation_id, token).status == "cancelled"


def test_transcript_redacts_contacts_secrets_and_consumed_tokens(session, monkeypatch):
    secret = "synthetic-secret-api-value"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    session.submit("Show revenue in 2025")
    session.run_command("/save Private")
    pending = session.run_command("/delete conversation").pending
    session.run_command(f"/confirm {pending.token}")
    result = session.submit(f"Show revenue in 2025 {secret} {pending.token} bob@example.org")
    assert result.report
    assert all(turn.result.pending is None for turn in session.transcript)
    encoded = repr(session.transcript)
    assert all(value not in encoded for value in (secret, pending.token, "bob@example.org"))
    session.reset()
    session.submit(f"Show revenue in 2025 {pending.token}")
    assert pending.token not in repr(session.transcript)
    assert pending.token not in json.dumps(session.context.history)


def test_invalid_actor_reset_never_retains_previous_evidence(session):
    session.submit("Show revenue in 2025")
    with pytest.raises(ConfigurationError):
        session.reset(actor_id="unknown_actor")
    assert session.transcript == [] and session.context.last_evidence == []
    with pytest.raises(ConfigurationError):
        session.refresh_access()


def test_permission_change_before_presentation_discards_just_computed_result(tmp_path, monkeypatch):
    path = tmp_path / "permissions.json"
    path.write_text('{"analyst_north": [1, 2]}', encoding="utf-8")
    session = WebSession(settings=Settings(data_dir=tmp_path, permissions_file=path))
    refresh = session.refresh_access
    calls = []

    def revoke_before_second_check():
        calls.append(None)
        if len(calls) == 2:
            path.write_text('{"analyst_north": [1]}', encoding="utf-8")
        return refresh()

    monkeypatch.setattr(session, "refresh_access", revoke_before_second_check)
    result = session.submit("Show revenue in 2025")
    assert result.report is None and result.evidence == []
    assert all(turn.result.report is None for turn in session.transcript)


def test_overlapping_calls_do_not_clear_the_running_operation_flag(session):
    import threading
    started, release = threading.Event(), threading.Event()
    original = session.runtime.model.plan

    async def delayed(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return await original(*args, **kwargs)

    session.runtime.model.plan = delayed
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(session.submit, "Show revenue in 2025")
        assert started.wait(5)
        try:
            blocked = session.submit("Show units in 2025")
            assert blocked.report is None and session.busy
        finally:
            release.set()
        assert future.result().report
    assert not session.busy


def saved_id(session, title="Original"):
    session.submit("Show revenue in 2025")
    session.run_command(f"/save {title}")
    return session.run_command("/reports").reports[-1]["id"]


def test_open_report_preserves_latest_analysis_and_rechecks_owner(tmp_path):
    session = WebSession(settings=Settings(data_dir=tmp_path))
    report_id = saved_id(session)
    latest = session.submit("Show units in 2025").report
    opened = session.open_report(report_id)
    assert opened.saved_report.title == "Original"
    assert opened.saved_report.evidence["product_ids"] == [1, 2]
    assert session.context.last_report is latest
    south = WebSession("analyst_south", settings=Settings(data_dir=tmp_path))
    missing = session.open_report("0" * 32)
    foreign = south.open_report(report_id)
    assert foreign.saved_report is None and missing.saved_report is None
    assert foreign.message == missing.message


def test_open_report_rejects_persisted_unsafe_content(session):
    report_id = saved_id(session)
    with session._store() as store:
        store.update("analyst_north", report_id, body="Contact bob@example.org")
    result = session.open_report(report_id)
    assert result.saved_report is None
    assert "bob@example.org" not in result.message


def test_public_preview_and_confirmation_are_bound_to_current_operation(session):
    report_id = saved_id(session)
    session.run_command(f"/delete id {report_id}")
    preview = session.pending_preview
    assert not hasattr(preview, "token")
    assert "Deleted" not in session.confirm_delete("wrong-operation").message
    assert len(session.list_reports()) == 1
    assert "Deleted 1" in session.confirm_delete(preview.operation_id).message
    assert session.pending_preview is None
    session.confirm_delete(preview.operation_id)
    assert session.list_reports() == []
