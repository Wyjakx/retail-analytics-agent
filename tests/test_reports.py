"""Known-answer checks for the destructive report boundary, without live APIs."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from retail_agent.reports import (
    InvalidConfirmationError,
    ReportAccessError,
    ReportStore,
    ReportStoreError,
)


class ReportStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "reports.sqlite"
        self.clock = 1000.0
        self.store = ReportStore(self.path, now=lambda: self.clock)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def save(self, title="Monthly revenue", actor="alice", conversation="first", body="Approved aggregates"):
        return self.store.save(actor, conversation, title, body, {"query_id": "q1"})

    def db_rows(self, table):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(f"SELECT * FROM {table}").fetchall()

    def test_save_and_list_scope_actor_conversation_and_literal_substring(self):
        expected = self.save(title="Margin 50%_growth", body="Validated")
        self.save(title="Margin 50abgrowth")
        self.save(title="Other", actor="bob")
        self.save(title="Margin 50%_growth", conversation="second")
        reports = self.store.list_reports("alice", "first", "50%_growth")
        self.assertEqual(reports, [expected])
        self.assertEqual(expected.version, 1)
        self.assertEqual(expected.evidence, {"query_id": "q1"})
        self.assertEqual(len(self.store.list_reports("alice", "first")), 2)
        self.assertEqual(len(self.store.list_reports("bob")), 1)

    def test_mention_is_casefolded_literal_text_in_title_or_body(self):
        report = self.save(title="Revenue", body="STRASSE basket")
        self.assertEqual(self.store.list_reports("alice", literal_mention="Straße"), [report])
        self.assertEqual(self.store.list_reports("alice", literal_mention="' OR 1=1 --"), [])

    def test_preview_freezes_exact_targets_and_does_not_delete(self):
        first = self.save()
        other = self.save(actor="bob")
        pending = self.store.preview_delete("alice", "first")
        self.assertEqual([(target.report_id, target.title, target.version) for target in pending.targets],
                         [(first.report_id, first.title, 1)])
        self.assertEqual(len(self.store.list_reports("alice")), 1)
        self.assertEqual(self.store.list_reports("bob"), [other])
        self.assertNotIn(pending.token, repr(pending))
        row_text = json.dumps(self.db_rows("report_deletions"))
        self.assertNotIn(pending.token, row_text)
        self.assertNotIn(first.title, row_text)

    def test_empty_and_explicit_empty_selection_leave_no_operation(self):
        self.save()
        for args in ({"literal_mention": "nothing matches"}, {"report_ids": []}, {"conversation_id": "missing"}):
            pending = self.store.preview_delete("alice", **args)
            self.assertEqual(pending.status, "no_match")
            self.assertIsNone(pending.operation_id)
            self.assertIsNone(pending.token)
            self.assertEqual(pending.targets, ())
        self.assertEqual(self.db_rows("report_deletions"), [])
        self.assertEqual(self.db_rows("report_deletion_targets"), [])
        self.assertEqual(len(self.store.list_reports("alice")), 1)

    def test_invalid_ttl_or_blank_selection_never_creates_operation(self):
        self.save()
        for value in (0, -1, float("inf"), float("nan"), True):
            with self.subTest(ttl=value), self.assertRaises(ValueError):
                self.store.preview_delete("alice", ttl_seconds=value)
        with self.assertRaises(ValueError):
            self.store.preview_delete("alice", literal_mention="")
        with self.assertRaises(ValueError):
            self.store.preview_delete("alice", report_ids="accidental string")
        self.assertEqual(self.db_rows("report_deletions"), [])

    def test_explicit_selection_cannot_include_another_actors_reports(self):
        mine = self.save()
        theirs = self.save(actor="bob")
        with self.assertRaises(ReportAccessError):
            self.store.preview_delete("alice", report_ids=[mine.report_id, theirs.report_id])
        self.assertEqual(self.db_rows("report_deletions"), [])
        self.assertEqual(self.store.list_reports("alice"), [mine])
        self.assertEqual(self.store.list_reports("bob"), [theirs])

    def test_wrong_actor_and_token_do_not_consume_valid_confirmation(self):
        report = self.save()
        pending = self.store.preview_delete("alice")
        with self.assertRaises(ReportAccessError):
            self.store.confirm_delete("bob", pending.operation_id, pending.token)
        with self.assertRaises(InvalidConfirmationError):
            self.store.confirm_delete("alice", pending.operation_id, "not the token")
        self.assertEqual(self.store.list_reports("alice"), [report])
        self.assertEqual(self.store.confirm_delete("alice", pending.operation_id, pending.token).status, "deleted")

    def test_new_report_survives_confirm_and_replay_is_idempotent(self):
        original = self.save()
        other = self.save(actor="bob")
        pending = self.store.preview_delete("alice", literal_mention="revenue")
        created_after_preview = self.save()
        result = self.store.confirm_delete("alice", pending.operation_id, pending.token)
        self.assertEqual(result.status, "deleted")
        self.assertEqual(result.report_ids, (original.report_id,))
        self.assertEqual(result.count, 1)
        created_after_deletion = self.save()
        replay = self.store.confirm_delete("alice", pending.operation_id, pending.token)
        self.assertEqual(result, replay)
        self.assertEqual({report.report_id for report in self.store.list_reports("alice")},
                         {created_after_preview.report_id, created_after_deletion.report_id})
        self.assertEqual(self.store.list_reports("bob"), [other])

    def test_expiry_boundary_consumes_operation_without_deleting_reports(self):
        report = self.save()
        pending = self.store.preview_delete("alice", ttl_seconds=10)
        self.clock = pending.expires_at
        result = self.store.confirm_delete("alice", pending.operation_id, pending.token)
        self.assertEqual(result.status, "expired")
        self.assertEqual(result.count, 0)
        self.assertEqual(self.store.list_reports("alice"), [report])
        self.clock = 1001.0  # Even a backwards clock cannot resurrect a consumed operation.
        self.assertEqual(self.store.confirm_delete("alice", pending.operation_id, pending.token), result)

    def test_cancel_requires_owner_and_prevents_later_deletion(self):
        report = self.save()
        pending = self.store.preview_delete("alice")
        with self.assertRaises(ReportAccessError):
            self.store.cancel_delete("bob", pending.operation_id)
        result = self.store.cancel_delete("alice", pending.operation_id)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.store.confirm_delete("alice", pending.operation_id, pending.token), result)
        self.assertEqual(self.store.cancel_delete("alice", pending.operation_id), result)
        self.assertEqual(self.store.list_reports("alice"), [report])

    def test_revision_from_second_connection_rejects_entire_frozen_batch(self):
        first = self.save()
        changed = self.save(title="Second report")
        pending = self.store.preview_delete("alice")
        with ReportStore(self.path, now=lambda: self.clock) as second:
            revised = second.update("alice", changed.report_id, body="New approved aggregate")
        self.assertEqual(revised.version, 2)
        result = self.store.confirm_delete("alice", pending.operation_id, pending.token)
        self.assertEqual(result.status, "stale")
        self.assertEqual(result.count, 0)
        self.assertEqual({report.report_id for report in self.store.list_reports("alice")},
                         {first.report_id, changed.report_id})
        self.assertEqual(self.store.confirm_delete("alice", pending.operation_id, pending.token), result)

    def test_overlapping_previews_cannot_delete_partial_remaining_batch(self):
        first = self.save(title="First report")
        second = self.save(title="Second report")
        all_reports = self.store.preview_delete("alice")
        only_first = self.store.preview_delete("alice", report_ids=[first.report_id])
        self.store.confirm_delete("alice", only_first.operation_id, only_first.token)
        result = self.store.confirm_delete("alice", all_reports.operation_id, all_reports.token)
        self.assertEqual(result.status, "stale")
        self.assertEqual(self.store.list_reports("alice"), [second])

    def test_owner_is_rechecked_even_if_database_changes_after_preview(self):
        report = self.save()
        pending = self.store.preview_delete("alice")
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute("UPDATE reports SET actor_id = ? WHERE report_id = ?", ("bob", report.report_id))
        result = self.store.confirm_delete("alice", pending.operation_id, pending.token)
        self.assertEqual(result.status, "stale")
        self.assertEqual(self.store.list_reports("bob")[0].report_id, report.report_id)

    def test_confirmation_survives_restart_and_audit_retains_no_deleted_content(self):
        secret_title = "Report title should not stay in audit"
        secret_body = "Body should not stay in audit"
        self.save(title=secret_title, body=secret_body)
        pending = self.store.preview_delete("alice")
        self.store.close()
        self.store = ReportStore(self.path, now=lambda: self.clock)
        self.store.confirm_delete("alice", pending.operation_id, pending.token)
        audit = json.dumps(self.db_rows("report_deletions"))
        self.assertNotIn(secret_title, audit)
        self.assertNotIn(secret_body, audit)
        self.assertNotIn("q1", audit)
        self.assertNotIn(pending.token, audit)
        self.assertEqual(self.db_rows("report_deletion_targets"), [])
        self.assertEqual(self.db_rows("reports"), [])

    def test_update_cannot_change_ownership_or_access_another_actors_report(self):
        report = self.save(actor="bob")
        with self.assertRaises(ReportAccessError):
            self.store.update("alice", report.report_id, title="Taken")
        self.assertEqual(self.store.list_reports("bob"), [report])

    def test_failure_to_consume_confirmation_rolls_back_report_deletion(self):
        first = self.save()
        second = self.save(title="Second report")
        pending = self.store.preview_delete("alice")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""CREATE TRIGGER fail_confirmation_consume
                BEFORE UPDATE ON report_deletions WHEN NEW.status = 'deleted'
                BEGIN SELECT RAISE(ABORT, 'simulated database failure'); END""")
        with self.assertRaises(ReportStoreError):
            self.store.confirm_delete("alice", pending.operation_id, pending.token)
        self.assertEqual({report.report_id for report in self.store.list_reports("alice")},
                         {first.report_id, second.report_id})
        self.assertEqual(self.db_rows("report_deletions")[0][5], "pending")
        self.assertEqual(len(self.db_rows("report_deletion_targets")), 2)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TRIGGER fail_confirmation_consume")
        result = self.store.confirm_delete("alice", pending.operation_id, pending.token)
        self.assertEqual(result.status, "deleted")
        self.assertEqual(result.count, 2)

    def test_simultaneous_confirmations_return_one_committed_outcome(self):
        first = self.save()
        pending = self.store.preview_delete("alice")
        created_later = self.save(title="Created after preview")
        ready = threading.Barrier(2)

        def confirm(_):
            # Each worker owns its connection, as it would in separate requests.
            with ReportStore(self.path, now=lambda: 1000.0) as store:
                ready.wait(timeout=5)
                return store.confirm_delete("alice", pending.operation_id, pending.token)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(confirm, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].status, "deleted")
        self.assertEqual(results[0].report_ids, (first.report_id,))
        self.assertEqual(self.store.list_reports("alice"), [created_later])
        self.assertEqual(len(self.db_rows("report_deletions")), 1)


if __name__ == "__main__":
    unittest.main()
