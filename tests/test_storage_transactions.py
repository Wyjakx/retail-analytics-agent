"""Data-loss and ownership contracts that require a real SQLite database."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier

import pytest

from retail_agent.reports import ReportAccessError, ReportStore, ReportStoreError


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


def test_revision_on_another_connection_preserves_the_entire_previewed_batch(store):
    """One revised report invalidates consent for all frozen targets."""
    unchanged = store.save("alice", "sales", "January", "Keep this too")
    original = store.save("alice", "sales", "February", "Old findings")
    pending = store.preview_delete("alice")
    with ReportStore(store.path, now=lambda: 1001) as other_connection:
        revised = other_connection.update("alice", original.id, body="Corrected findings")

    outcome = store.confirm_delete("alice", pending.operation_id, pending.token)

    assert outcome.status == "stale"
    assert outcome.report_ids == ()
    assert {report.id: report for report in store.list_reports("alice")} == {
        unchanged.id: unchanged,
        revised.id: revised,
    }


@pytest.mark.parametrize("expiry_offset", [0, 1], ids=["at-expiry", "after-expiry"])
def test_restart_preserves_confirmation_but_expiry_prevents_deletion(tmp_path, expiry_offset):
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

    timestamp[0] = 1010 + expiry_offset
    with ReportStore(database_path, now=lambda: timestamp[0]) as restarted:
        expired = restarted.confirm_delete("alice", expiring.operation_id, expiring.token)
        assert expired.status == "expired"
        assert expired.report_ids == ()
        assert restarted.list_reports("alice") == [expiring_report]


def test_another_actor_cannot_execute_or_consume_a_known_confirmation(store):
    """A leaked operation and token must not let another actor delete or cancel."""
    owned = store.save("alice", "sales", "January", "Alice's findings")
    other_owned = store.save("bob", "sales", "January", "Bob's findings")
    pending = store.preview_delete("alice")
    with ReportStore(store.path, now=lambda: 1000) as other_actor:
        with pytest.raises(ReportAccessError):
            other_actor.confirm_delete("bob", pending.operation_id, pending.token)
        with pytest.raises(ReportAccessError):
            other_actor.cancel_delete("bob", pending.operation_id)

    assert store.list_reports("alice") == [owned]
    outcome = store.confirm_delete("alice", pending.operation_id, pending.token)
    assert outcome.status == "deleted"
    assert outcome.report_ids == (owned.id,)
    assert store.list_reports("alice") == []
    assert store.list_reports("bob") == [other_owned]
