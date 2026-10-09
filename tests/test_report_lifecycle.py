"""Five report-lifecycle and data-loss contracts using actual SQLite files."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from threading import Barrier

import pytest

from retail_agent.reports import ReportStore, ReportStoreError
from tests.scenarios import ask, plan, report


def test_saved_report_survives_restart_but_requires_current_owner_permissions(application, policy_file):
    app, _ = application(plan(), report("Revenue was 500."))
    ask(app)
    ask(app, "/save Quarterly review")
    saved = app.reports.list_reports("alice")[0]
    restarted, _ = application()
    assert ask(restarted, f"/open {saved.report_id}").saved_report == saved
    assert saved.evidence["queries"][0]["rows"] == [{"revenue": 500}]
    other, provider = application(actor="bob")
    assert ask(other, "/reports").reports == []
    assert ask(other, f"/open {saved.report_id}").saved_report is None
    assert ask(other, f"/delete id {saved.report_id}").pending is None
    assert provider.payloads == []

    pending = ask(app, "/delete conversation").pending
    policy_file.write_text('{"alice": [3], "bob": [3]}', encoding="utf-8")
    explained = ask(app, "/explain")
    assert explained.report is None and explained.evidence == []
    assert app.context.history == [] and app.context.previous_plan is None
    assert ask(app, "/reports").reports == []
    assert ask(app, f"/open {saved.report_id}").saved_report is None
    ask(app, f"/confirm {pending.token}")
    assert app.reports.list_reports("alice") == [saved]


def test_delete_requires_fresh_consent_for_the_exact_preview(application):
    app, provider = application(plan(), report())
    ask(app)
    ask(app, "/save Budget 100%_done")
    ask(app, "/save Other report")
    pending = ask(app, "Delete all reports mentioning 100%_done").pending
    assert [target.title for target in pending.targets] == ["Budget 100%_done"]
    ask(app, "yes")
    ask(app, "/confirm wrong-token")
    assert len(app.reports.list_reports("alice")) == 2
    ask(app, "/cancel")
    ask(app, f"/confirm {pending.token}")
    assert len(app.reports.list_reports("alice")) == 2
    fresh = ask(app, "Delete all reports mentioning 100%_done").pending
    ask(app, "/save Created after preview")
    deleted = ask(app, f"/confirm {fresh.token}")
    assert deleted.deletion_status == "deleted"
    assert ask(app, f"/confirm {fresh.token}").message == deleted.message
    assert {row.title for row in app.reports.list_reports("alice")} == {
        "Other report", "Created after preview",
    }
    assert len(provider.payloads) == 2
    assert fresh.token not in json.dumps(provider.payloads) + app.traces.path.read_text(encoding="utf-8")


@pytest.fixture
def store(tmp_path):
    with ReportStore(tmp_path / "reports.sqlite", now=lambda: 1000) as reports:
        yield reports


def test_concurrent_confirmations_commit_the_frozen_batch_once(store):
    """Racing callers must share one outcome without deleting a later report."""
    first = store.save("alice", "sales", "January", "January findings")
    second = store.save("alice", "sales", "February", "February findings")
    pending = store.preview_delete("alice", conversation_id="sales")
    later = store.save("alice", "sales", "March", "Saved after the preview")
    ready = Barrier(2)

    def confirm_on_separate_connection():
        with ReportStore(store.path, now=lambda: 1000) as connection:
            ready.wait(timeout=5)
            return connection.confirm_delete("alice", pending.operation_id, pending.token)

    with ThreadPoolExecutor(max_workers=2) as workers:
        calls = [workers.submit(confirm_on_separate_connection) for _ in range(2)]
        outcomes = [call.result(timeout=10) for call in calls]

    assert outcomes[0] == outcomes[1]
    assert outcomes[0].status == "deleted"
    assert set(outcomes[0].report_ids) == {first.id, second.id}
    assert store.list_reports("alice") == [later]


def test_failure_at_token_consumption_rolls_back_reports_and_allows_retry(store):
    """A failed audit write must neither lose reports nor spend confirmation."""
    first = store.save("alice", "sales", "January", "January findings")
    second = store.save("alice", "sales", "February", "February findings")
    pending = store.preview_delete("alice")
    with sqlite3.connect(store.path) as database:
        database.execute(
            """CREATE TRIGGER fail_consumption BEFORE DELETE ON report_deletion_targets
               BEGIN SELECT RAISE(ABORT, 'injected disk failure'); END"""
        )

    with pytest.raises(ReportStoreError):
        store.confirm_delete("alice", pending.operation_id, pending.token)

    assert {report.id: report for report in store.list_reports("alice")} == {
        first.id: first,
        second.id: second,
    }
    with sqlite3.connect(store.path) as database:
        database.execute("DROP TRIGGER fail_consumption")

    retried = store.confirm_delete("alice", pending.operation_id, pending.token)
    assert retried.status == "deleted"
    assert set(retried.report_ids) == {first.id, second.id}
    assert store.list_reports("alice") == []


def test_restart_preserves_confirmation_but_expiry_prevents_deletion(tmp_path):
    """Restart must preserve valid consent without extending its deadline."""
    database_path = tmp_path / "reports.sqlite"
    timestamp = [1000]
    with ReportStore(database_path, now=lambda: timestamp[0]) as store:
        valid_report = store.save("alice", "sales", "January", "Delete before deadline")
        expiring_report = store.save("alice", "sales", "February", "Keep after deadline")
        valid = store.preview_delete("alice", report_ids=[valid_report.id], ttl_seconds=10)
        expiring = store.preview_delete("alice", report_ids=[expiring_report.id], ttl_seconds=10)

    timestamp[0] = 1009
    with ReportStore(database_path, now=lambda: timestamp[0]) as restarted:
        outcome = restarted.confirm_delete("alice", valid.operation_id, valid.token)
        assert outcome.status == "deleted"
        assert outcome.report_ids == (valid_report.id,)

    timestamp[0] = 1010
    with ReportStore(database_path, now=lambda: timestamp[0]) as restarted:
        expired = restarted.confirm_delete("alice", expiring.operation_id, expiring.token)
        assert expired.status == "expired"
        assert expired.report_ids == ()
        assert restarted.list_reports("alice") == [expiring_report]
