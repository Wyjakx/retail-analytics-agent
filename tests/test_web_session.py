import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from retail_agent.config import ConfigurationError, Settings
from retail_agent.reports import ReportStore
from retail_agent.web_session import WebSession


@pytest.fixture
def session(tmp_path):
    return WebSession(settings=Settings(data_dir=tmp_path))


def test_two_sessions_do_not_share_analysis(tmp_path):
    a = WebSession(settings=Settings(data_dir=tmp_path))
    b = WebSession(settings=Settings(data_dir=tmp_path))
    assert a.submit("Show revenue in 2025").report is not None
    assert b.transcript == [] and b.context.last_evidence == []
    assert a.context.conversation_id != b.context.conversation_id
    south = WebSession("analyst_south", settings=Settings(data_dir=tmp_path))
    assert south.submit("Show revenue in 2025").evidence[0]["rows"] == [{"revenue": 12000.0}]
    assert a.context.last_evidence[0]["rows"] == [{"revenue": 900.0}]


def test_reports_are_shared_only_with_same_actor(tmp_path):
    a = WebSession(settings=Settings(data_dir=tmp_path))
    a.submit("Show revenue in 2025")
    a.run_command("/save Shared")
    b = WebSession(settings=Settings(data_dir=tmp_path))
    c = WebSession("analyst_south", settings=Settings(data_dir=tmp_path))
    assert len(b.run_command("/reports").reports) == 1
    assert c.run_command("/reports").reports == []
    assert b.transcript == []


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
    session.reset()
    assert session.context.conversation_id != old_id
    assert session.transcript == []
    assert not session.context.customer_refs
    assert not session.context.pending_clarification
    assert session.context.pending is None
    assert session.submit(f"Break down {ref} by month").report is None
    with ReportStore(session.runtime.settings.data_dir / "reports.sqlite3") as store:
        assert store.confirm_delete("analyst_north", pending.operation_id, token).status == "cancelled"


@pytest.mark.parametrize("change", ["narrow", "missing", "corrupt"])
def test_permission_change_clears_retained_presentation_before_rerender(tmp_path, change):
    path = tmp_path / "permissions.json"
    path.write_text('{"analyst_north": [1, 2]}', encoding="utf-8")
    session = WebSession(settings=Settings(data_dir=tmp_path, permissions_file=path))
    session.submit("Show revenue in 2025")
    if change == "narrow":
        path.write_text('{"analyst_north": [1]}', encoding="utf-8")
        assert session.refresh_access().allowed_product_ids == (1,)
    else:
        if change == "missing":
            path.unlink()
        else:
            path.write_text("{", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            session.refresh_access()
    assert session.transcript == []
    assert session.context.last_report is None
    assert session.context.last_evidence == []


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


def test_commands_only_channel_rejects_analytical_questions(session):
    with pytest.raises(ValueError):
        session.run_command("Show revenue in 2025")
    assert session.context.last_report is None


def test_invalid_actor_reset_never_retains_previous_evidence(session):
    session.submit("Show revenue in 2025")
    with pytest.raises(ConfigurationError):
        session.reset(actor_id="unknown_actor")
    assert session.transcript == [] and session.context.last_evidence == []
    with pytest.raises(ConfigurationError):
        session.refresh_access()


def test_mode_reset_uses_base_directory_once(session):
    session.reset()
    assert session.runtime.settings.data_dir.name == "offline"
    assert session.runtime.settings.data_dir.parent.name != "offline"


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
