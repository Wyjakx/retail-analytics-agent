"""Optional regression probes: python -m tests.effectiveness.

Each fresh process changes one function in memory, then runs its acceptance test.
Source files stay untouched. A setup/collection/teardown error never counts as
a detected regression. These selected probes are not exhaustive mutation coverage.
"""

import importlib
import inspect
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


# Module, function, exact deliberate defect, and the behavior expected to fail.
PROBES = [
    ("unauthorized-revenue", "gateways", "OfflineGateway.execute",
     'item["product_id"] not in products', "False",
     "test_critical_flows.py::test_financial_totals_exclude_unauthorized_returned_and_invalid_items"),
    ("wrong-spend-denominator", "gateways", "OfflineGateway.execute",
     "revenue / customer_count", "revenue / order_count",
     "test_critical_flows.py::test_financial_totals_exclude_unauthorized_returned_and_invalid_items"),
    ("small-group-leak", "gateways", "QueryOutcome.suppress_small_groups",
     "if count < min_customers:", "if False:",
     "test_critical_flows.py::test_small_segments_never_reach_the_reporter"),
    ("unredacted-credential", "safety", "sanitize_input",
     'text = text.replace(secret, "[REDACTED_SECRET]")', "text = text",
     "test_critical_flows.py::test_private_input_never_reaches_provider_history_storage_or_traces"),
    ("invented-report-number", "safety", "validate_report",
     "if numeric not in allowed:", "if False:",
     "test_critical_flows.py::test_report_accepts_real_metadata_but_cannot_turn_billing_into_revenue"),
    ("revoked-history-retained", "service", "AnalyticsService.refresh_scope",
     "self.context.history.clear()", "pass",
     "test_report_lifecycle.py::test_saved_report_survives_restart_but_requires_current_owner_permissions"),
    ("missing-plan-scope-preflight", "service", "AnalyticsService._analyze",
     "used_products.update(permitted_products(spec, scope))", "pass",
     "test_critical_flows.py::test_unauthorized_second_query_blocks_the_entire_plan_before_spending"),
    ("unsupported-provider-schema", "adk_workflow", "_provider_json_schema",
     "    remove_array_bounds(schema)", "    pass",
     "test_google_boundaries.py::test_planning_and_reporting_use_the_supported_gemini_wire_contract"),
    ("unscoped-bigquery-sql", "analytics", "SQLCompiler.compile",
     '"oi.product_id IN UNNEST(@allowed_products)"', '"TRUE"',
     "test_google_boundaries.py::test_bigquery_binds_scope_and_filters_and_stops_when_actual_billing_exhausts_budget"),
    ("partial-deletion-commit", "reports", "ReportStore._transaction",
     "self._db.rollback()", "self._db.commit()",
     "test_report_lifecycle.py::test_failure_at_token_consumption_rolls_back_reports_and_allows_retry"),
    ("confirmation-valid-at-expiry", "reports", "ReportStore.confirm_delete",
     'self._timestamp() >= operation["expires_at"]', 'self._timestamp() > operation["expires_at"]',
     "test_report_lifecycle.py::test_restart_preserves_confirmation_but_expiry_prevents_deletion"),
    ("last-calendar-day-omitted", "service", "AnalyticsService._handle",
     "plan = enforce_explicit_periods(report_question, plan)", "plan = plan",
     "test_critical_flows.py::test_comparison_includes_last_day_and_keeps_approved_calendar_dates"),
    ("actual-billing-ignored", "gateways", "QueryBudget.reconcile_bytes",
     "self.reserve_bytes(max(0, actual - estimated))", "pass",
     "test_google_boundaries.py::test_bigquery_binds_scope_and_filters_and_stops_when_actual_billing_exhausts_budget"),
    ("generated-email-disclosed", "safety", "validate_text",
     "if EMAIL.search(text) or PHONE.search(text):", "if PHONE.search(text):",
     "test_critical_flows.py::test_customer_ranking_and_monthly_report_keep_personal_details_private"),
    ("browser-keeps-revoked-chat", "web_session", "WebSession._discard_context",
     "self.transcript.clear()", "pass",
     "test_streamlit_workflows.py::test_browser_permission_change_removes_old_chat_and_open_report_before_new_analysis"),
]


class Outcomes:
    def __init__(self):
        self.reports = []

    def pytest_runtest_logreport(self, report):
        self.reports.append(report)


def probe(index):
    name, module, attribute, original, defect, node = PROBES[index]
    owner = importlib.import_module(f"retail_agent.{module}")
    for part in attribute.split("."):
        owner = getattr(owner, part)
    function = inspect.unwrap(owner)
    source = textwrap.dedent(inspect.getsource(function))
    if source.count(original) != 1:
        raise RuntimeError(f"Review outdated probe: {name}")
    namespace = dict(function.__globals__)
    exec(compile(source.replace(original, defect), f"<probe:{name}>", "exec"), namespace)
    # Preserve module aliases and decorators by changing the original function.
    function.__code__ = inspect.unwrap(namespace[function.__name__]).__code__
    outcomes = Outcomes()
    status = pytest.main([f"tests/{node}", "-q", "--tb=short"], plugins=[outcomes])
    calls = [report for report in outcomes.reports if report.when == "call"]
    infrastructure_failed = any(
        report.failed for report in outcomes.reports if report.when != "call"
    )
    detected = status == 1 and len(calls) == 1 and calls[0].failed and not infrastructure_failed
    return 0 if detected else 1


def main():
    root = Path(__file__).resolve().parents[1]
    baseline = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root)
    if baseline.returncode:
        return baseline.returncode
    failures = []
    for index, (name, *_) in enumerate(PROBES):
        result = subprocess.run(
            [sys.executable, "-m", "tests.effectiveness", str(index)], cwd=root,
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        )
        print(f"{name}: {'DETECTED' if result.returncode == 0 else 'FAILED PROBE'}", flush=True)
        if result.returncode:
            failures.append(name)
            print(result.stdout + result.stderr)
    print(f"{len(PROBES) - len(failures)}/{len(PROBES)} deliberate regressions detected.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(probe(int(sys.argv[1])) if len(sys.argv) == 2 else main())
