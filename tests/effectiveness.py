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
     "test_application.py::test_analysis_computes_all_six_metrics_from_authorized_items"),
    ("wrong-spend-denominator", "gateways", "OfflineGateway.execute",
     "revenue / customer_count", "revenue / order_count",
     "test_application.py::test_analysis_computes_all_six_metrics_from_authorized_items"),
    ("small-group-leak", "gateways", "QueryOutcome.suppress_small_groups",
     "if count < min_customers:", "if False:",
     "test_application.py::test_small_segments_never_reach_the_reporter"),
    ("unredacted-credential", "safety", "sanitize_input",
     'text = text.replace(secret, "[REDACTED_SECRET]")', "text = text",
     "test_application.py::test_pasted_credentials_and_contacts_never_reach_provider_history_or_storage"),
    ("invented-report-number", "safety", "validate_report",
     "if numeric not in allowed:", "if False:",
     "test_application.py::test_unsafe_report_is_replaced_before_display_and_save[invented-amount]"),
    ("revoked-history-retained", "service", "AnalyticsService.refresh_scope",
     "self.context.history.clear()", "pass",
     "test_application.py::test_revoking_permissions_invalidates_previous_context_and_pending_delete"),
    ("missing-plan-budget-preflight", "service", "AnalyticsService._analyze",
     "if len(plan.queries) > budget.max_queries:", "if False:",
     "test_application.py::test_entire_plan_is_checked_before_spending_on_its_first_query[budget]"),
    ("unsupported-provider-schema", "adk_workflow", "_provider_json_schema",
     "    remove_array_bounds(schema)", "    pass",
     "test_cloud_contracts.py::test_adk_output_schema_serializes_on_supported_gemini_json_wire_path[Decision]"),
    ("unscoped-bigquery-sql", "analytics", "SQLCompiler.compile",
     '"oi.product_id IN UNNEST(@allowed_products)"', '"TRUE"',
     "test_cloud_contracts.py::test_bigquery_submits_scoped_parameterized_sql_with_bounded_job_settings"),
    ("partial-deletion-commit", "reports", "ReportStore._transaction",
     "self._db.rollback()", "self._db.commit()",
     "test_storage_transactions.py::test_failure_at_token_consumption_rolls_back_reports_and_allows_retry"),
    ("confirmation-valid-at-expiry", "reports", "ReportStore.confirm_delete",
     'self._timestamp() >= operation["expires_at"]', 'self._timestamp() > operation["expires_at"]',
     "test_storage_transactions.py::test_restart_preserves_confirmation_but_expiry_prevents_deletion[at-expiry]"),
    ("last-calendar-day-omitted", "service", "AnalyticsService._handle",
     "plan = enforce_explicit_periods(report_question, plan)", "plan = plan",
     "test_application.py::test_explicit_comparison_keeps_last_included_day_in_calculations[inclusive-conversion]"),
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
