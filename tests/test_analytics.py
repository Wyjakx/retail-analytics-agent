from datetime import date
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import time

import pytest
from pydantic import ValidationError

from retail_agent.analytics import (
    ActorScope, AnalysisPlan, BudgetExceeded, QuerySpec, QueryTimeout,
    SAFE_COLUMNS, SQLCompiler, SchemaViolation, ScopeViolation,
)
from retail_agent.gateways import BigQueryGateway, OfflineGateway, QueryBudget, QueryOutcome, seeded_data
from retail_agent.pseudonyms import CustomerPseudonymizer


def query(**overrides):
    values = dict(metrics=["revenue", "orders", "purchasing_customers", "units", "average_order_value", "spend_per_customer"],
                  start_date="2025-01-01", end_date="2025-02-01")
    return QuerySpec(**{**values, **overrides})


def scope(*products):
    return ActorScope("demo-a", products or (1,))


def test_mixed_order_revenue_counts_only_entitled_items():
    result = OfflineGateway().execute(query(), scope(1), QueryBudget())
    assert result.simulated is True
    assert result.statistics["source"] == "synthetic_fixture"
    assert result.rows == [{"revenue": 300.0, "orders": 12, "purchasing_customers": 12, "units": 12,
                            "average_order_value": 25.0, "spend_per_customer": 25.0, "group_customer_count": 12}]
    lamps = OfflineGateway().execute(query(), scope(3), QueryBudget())
    assert lamps.rows[0]["revenue"] == 6000.0
    assert lamps.evidence_id != result.evidence_id


def test_state_period_comparison_and_filtered_followup_are_computed():
    gateway = OfflineGateway()
    first = gateway.execute(query(dimensions=["state"]), scope(), QueryBudget())
    second = gateway.execute(query(dimensions=["state"], start_date="2025-02-01", end_date="2025-03-01"), scope(), QueryBudget())
    assert [row["revenue"] for row in first.rows] == [120.0, 180.0]
    assert [row["revenue"] for row in second.rows] == [240.0, 360.0]
    followup = gateway.execute(query(states=["Texas"], metrics=["spend_per_customer"]), scope(), QueryBudget())
    assert followup.rows == [{"spend_per_customer": 30.0, "group_customer_count": 6}]


def test_repeated_buyers_have_correct_denominator():
    result = OfflineGateway().execute(query(end_date="2025-03-01"), scope(), QueryBudget())
    assert result.rows[0]["revenue"] == 900
    assert result.rows[0]["orders"] == 24
    assert result.rows[0]["purchasing_customers"] == 12
    assert result.rows[0]["average_order_value"] == 37.5
    assert result.rows[0]["spend_per_customer"] == 75


@pytest.mark.parametrize("dates,expected", [
    (("2025-01-01", "2025-01-15"), 0),
    (("2025-01-15", "2025-01-16"), 1),
    (("2025-03-01", "2025-04-01"), 0),
])
def test_exclusive_end_date_and_empty_groups(dates, expected):
    result = OfflineGateway().execute(query(start_date=dates[0], end_date=dates[1]), scope(), QueryBudget())
    assert len(result.rows) == expected


def test_all_time_is_supported_and_missing_single_bound_is_rejected():
    spec = query(start_date=None, end_date=None)
    assert OfflineGateway().execute(spec, scope(), QueryBudget()).rows[0]["revenue"] == 900
    assert "@start_date" not in SQLCompiler().compile(spec, scope()).sql
    with pytest.raises(ValidationError):
        query(start_date=None)


@pytest.mark.parametrize("value", [[3], [1, 3]])
def test_explicit_unauthorized_product_filters_fail_before_execution(value):
    budget = QueryBudget()
    with pytest.raises(ScopeViolation):
        OfflineGateway().execute(query(product_ids=value), scope(), budget)
    assert budget.queries_used == 0
    with pytest.raises(ScopeViolation):
        SQLCompiler().compile(query(product_ids=value), scope())


def test_narrowing_entitlements_and_no_entitlements():
    spec = query(product_ids=[1])
    assert OfflineGateway().execute(spec, scope(1, 3), QueryBudget()).rows[0]["revenue"] == 300
    with pytest.raises(ScopeViolation):
        OfflineGateway().execute(query(), ActorScope("demo-a", ()), QueryBudget())


def test_changed_actor_scope_changes_result_and_evidence():
    gateway = OfflineGateway()
    assert gateway.execute(query(), scope(1), QueryBudget()).rows[0]["revenue"] == 300
    assert gateway.execute(query(), scope(2), QueryBudget()).rows == []
    other = ActorScope("demo-b", (1,))
    assert gateway.execute(query(), other, QueryBudget()).evidence_id != gateway.execute(query(), scope(), QueryBudget()).evidence_id


def test_status_price_and_join_integrity_exclusions():
    data = seeded_data()
    data["order_items"][0]["status"] = "Returned"        # Exclude one $20 item.
    data["orders"][2]["status"] = "Cancelled"          # Customer 2 January order.
    data["order_items"][8]["sale_price"] = -20         # Customer 3 January.
    data["order_items"][12]["user_id"] = 999           # Customer 4 January invalid join.
    result = OfflineGateway(data).execute(query(), scope(), QueryBudget())
    assert result.rows[0]["revenue"] == 220
    assert result.rows[0]["purchasing_customers"] == 8


def test_suppression_removes_small_groups_and_internal_counts():
    data = seeded_data()
    data["users"][0]["state"] = "Sparse state"
    raw = OfflineGateway(data).execute(query(dimensions=["state"]), scope(), QueryBudget())
    public = raw.suppress_small_groups(3)
    assert public.suppressed_groups == 1
    assert [row["state"] for row in public.rows] == ["California", "Texas"]
    assert "group_customer_count" not in public.columns
    assert all("group_customer_count" not in row for row in public.rows)
    assert public.statistics["minimum_customers"] == 3
    assert raw.suppressed_groups == 0


def test_compiler_applies_scope_in_cte_and_binds_untrusted_values():
    malicious = "Texas' OR 1=1 --"
    compiled = SQLCompiler().compile(query(states=[malicious], dimensions=["state"]), scope())
    assert compiled.sql.index("oi.product_id IN UNNEST(@allowed_products)") < compiled.sql.index("FROM scoped_items")
    assert malicious not in compiled.sql
    assert any(p.name == "states" and p.value == (malicious,) for p in compiled.parameters)
    assert {p.name: p.value for p in compiled.parameters}["allowed_products"] == (1,)
    for identifier in ("first_name", "last_name", "email", "street_address", "latitude", "longitude"):
        assert identifier not in compiled.sql
    with pytest.raises(SchemaViolation):
        SQLCompiler("project.dataset`; DROP TABLE users; --")


@pytest.mark.parametrize("bad", [
    {"metrics": ["email"]}, {"dimensions": ["user_id"]}, {"sql": "SELECT * FROM users"},
    {"actor_id": "admin"}, {"product_ids": [True]}, {"limit": 51}, {"metrics": ["revenue", "revenue"]},
    {"start_date": "2025-02-01", "end_date": "2025-01-01"},
    {"metrics": ["revenue"], "order_by": "orders"}, {"order_direction": "random"},
    {"customer_refs": ["7"]}, {"customer_refs": ["cust_" + "A" * 32]},
    {"customer_refs": ["cust_" + "a" * 32, "cust_" + "a" * 32]},
])
def test_restricted_plan_shape(bad):
    with pytest.raises(ValidationError):
        query(**bad)


def test_plan_query_count_and_fixture_schema():
    with pytest.raises(ValidationError):
        AnalysisPlan(queries=[])
    with pytest.raises(ValidationError):
        AnalysisPlan(queries=[query()] * 4)
    data = seeded_data()
    del data["users"][0]["state"]
    with pytest.raises(SchemaViolation):
        OfflineGateway(data)


def test_offline_query_budget():
    budget = QueryBudget(max_queries=1)
    gateway = OfflineGateway()
    gateway.execute(query(), scope(), budget)
    with pytest.raises(BudgetExceeded):
        gateway.execute(query(), scope(), budget)


class FakeResult(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.schema = [SimpleNamespace(name=name) for name in rows[0]]


class FakeJob:
    def __init__(self, rows, estimated=6, billed=5, timed_out=False):
        self.rows = rows
        self.total_bytes_processed = estimated
        self.total_bytes_billed = billed
        self.job_id = "fake-job"
        self.timed_out = timed_out
        self.cancelled = False

    def result(self, **kwargs):
        if self.timed_out:
            raise TimeoutError("fixture timeout")
        return FakeResult(self.rows)

    def cancel(self, **kwargs):
        self.cancelled = True


class FakeClient:
    def __init__(self, rows=None, estimated=6, timed_out=False, incompatible_table=None):
        self.metadata_calls = []
        self.query_calls = []
        self.estimated = estimated
        self.job = FakeJob(rows or [{"revenue": 300.0, "group_customer_count": 12}], estimated, timed_out=timed_out)
        self.incompatible_table = incompatible_table

    def get_table(self, table, **kwargs):
        self.metadata_calls.append(table)
        name = table.rsplit(".", 1)[1]
        fields = list(SAFE_COLUMNS[name]) + ["email", "first_name", "street_address"]
        if name == self.incompatible_table:
            fields.remove(SAFE_COLUMNS[name][0])
        return SimpleNamespace(schema=[SimpleNamespace(name=field) for field in fields], location="US")

    def query(self, sql, **kwargs):
        self.query_calls.append((sql, kwargs))
        if kwargs.get("job_id"):
            self.job.job_id = kwargs["job_id"]
        return FakeJob([], estimated=self.estimated) if kwargs["job_config"].dry_run else self.job


def test_mocked_bigquery_caps_schema_filtering_and_typed_parameters():
    client = FakeClient()
    gateway = BigQueryGateway(client=client, maximum_bytes_billed=10, timeout_seconds=2)
    spec = query(metrics=["revenue"])
    result = gateway.execute(spec, scope(), QueryBudget(max_cumulative_bytes=20))
    assert result.rows == [{"revenue": 300.0, "group_customer_count": 12}]
    assert result.simulated is False
    assert result.statistics["bytes_billed"] == 5
    assert len(client.metadata_calls) == 4
    assert "email" not in repr(gateway.schema_catalog())
    live_config = client.query_calls[1][1]["job_config"]
    assert live_config.maximum_bytes_billed == 10
    assert int(live_config.job_timeout_ms) == 2000
    parameters = {p.name: p for p in live_config.query_parameters}
    assert parameters["allowed_products"].values == [1]
    assert parameters["start_date"].value == date(2025, 1, 1)
    assert client.query_calls[1][1]["retry"] is None
    assert client.query_calls[1][1]["job_id"].startswith("retail_")
    assert result.statistics["job_id"] == gateway.last_metadata["job_id"]
    assert gateway.last_metadata["status"] == "succeeded"


def test_mocked_bigquery_cumulative_cap_stops_before_second_live_job():
    client = FakeClient()
    gateway = BigQueryGateway(client=client, maximum_bytes_billed=10)
    budget = QueryBudget(max_cumulative_bytes=10)
    gateway.execute(query(metrics=["revenue"]), scope(), budget)
    with pytest.raises(BudgetExceeded):
        gateway.execute(query(metrics=["revenue"]), scope(), budget)
    assert sum(not call[1]["job_config"].dry_run for call in client.query_calls) == 1


def test_mocked_bigquery_timeout_requests_cancellation():
    client = FakeClient(timed_out=True)
    with pytest.raises(QueryTimeout):
        BigQueryGateway(client=client).execute(query(metrics=["revenue"]), scope(), QueryBudget())
    assert client.job.cancelled is True


def test_mocked_bigquery_rejects_sensitive_results_and_incompatible_metadata():
    with pytest.raises(SchemaViolation):
        BigQueryGateway(client=FakeClient(rows=[{"email": "sensitive@example.test"}])).execute(query(metrics=["revenue"]), scope(), QueryBudget())
    client = FakeClient(incompatible_table="users")
    with pytest.raises(SchemaViolation):
        BigQueryGateway(client=client).execute(query(), scope(), QueryBudget())
    assert not client.query_calls


def test_cancelled_or_expired_budget_does_not_submit_queries():
    for budget in [QueryBudget(deadline=time.monotonic() - 1), QueryBudget()]:
        if budget.deadline is None:
            budget.cancel()
        client = FakeClient()
        with pytest.raises(QueryTimeout):
            BigQueryGateway(client=client).execute(query(), scope(), budget)
        assert client.metadata_calls == []
        assert client.query_calls == []


def test_cancel_during_dry_run_never_launches_live_query():
    budget = QueryBudget()
    class CancellingClient(FakeClient):
        def query(self, sql, **kwargs):
            result = super().query(sql, **kwargs)
            budget.cancel()
            return result
    client = CancellingClient()
    with pytest.raises(QueryTimeout):
        BigQueryGateway(client=client).execute(query(), scope(), budget)
    assert len(client.query_calls) == 1
    assert client.query_calls[0][1]["job_config"].dry_run is True


def test_active_job_cancellation_and_remaining_rpc_timeout(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("retail_agent.gateways.time.monotonic", lambda: clock[0])
    budget = QueryBudget(deadline=100.125)
    job = FakeJob([])
    budget.register_job(job)
    assert budget.rpc_timeout(30) == 0.125
    clock[0] = 100.0625
    assert budget.rpc_timeout(30) == 0.0625
    clock[0] = 100.125
    with pytest.raises(QueryTimeout):
        budget.rpc_timeout(30)
    budget.cancel()
    assert job.cancelled
    with pytest.raises(QueryTimeout):
        budget.check_active()


def test_cannot_tighten_suppression_after_internal_counts_discarded():
    public = OfflineGateway().execute(query(), scope(), QueryBudget()).suppress_small_groups(1)
    with pytest.raises(SchemaViolation):
        public.suppress_small_groups(3)


def test_uncertain_submission_reconciles_same_id_without_resubmission():
    class UncertainClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.lookups = []

        def query(self, sql, **kwargs):
            result = super().query(sql, **kwargs)
            if not kwargs["job_config"].dry_run:
                raise TimeoutError("Submission response was lost.")
            return result

        def get_job(self, job_id, **kwargs):
            self.lookups.append((job_id, kwargs))
            return self.job

    client = UncertainClient()
    gateway = BigQueryGateway(client=client)
    with pytest.raises(QueryTimeout, match="without resubmission"):
        gateway.execute(query(metrics=["revenue"]), scope(), QueryBudget())
    live_calls = [call for call in client.query_calls if not call[1]["job_config"].dry_run]
    assert len(live_calls) == 1
    submitted_id = live_calls[0][1]["job_id"]
    assert client.lookups[0][0] == submitted_id
    assert client.lookups[0][1]["timeout"] == 1
    assert client.job.cancelled
    assert gateway.last_metadata["job_id"] == submitted_id
    assert gateway.last_metadata["status"] == "submission_uncertain"
    assert gateway.last_metadata["reconciliation"] == "job_found"


def test_offline_gateway_import_does_not_require_google_packages():
    script = """
import importlib.abc
import sys
class BlockGoogle(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'google' or fullname.startswith('google.'):
            raise ImportError('live extras deliberately unavailable')
sys.meta_path.insert(0, BlockGoogle())
from retail_agent.analytics import ActorScope, QuerySpec
from retail_agent.gateways import OfflineGateway, QueryBudget
result = OfflineGateway().execute(QuerySpec(metrics=['revenue']), ActorScope('demo', (1,)), QueryBudget())
assert result.simulated and result.rows[0]['revenue'] == 900
"""
    completed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_pseudonyms_are_key_stable_actor_bound_and_resolve_only_exposed_references():
    first = CustomerPseudonymizer(b"a" * 32)
    restarted = CustomerPseudonymizer(b"a" * 32)
    reference = first.label("actor-a", 7)
    assert reference.startswith("cust_") and len(reference) == 37
    assert first.resolve("actor-a", [reference]) == (7,)
    with pytest.raises(ScopeViolation, match="rerun the ranking"):
        restarted.resolve("actor-a", [reference])
    assert restarted.label("actor-a", 7) == reference
    assert restarted.resolve("actor-a", [reference]) == (7,)
    assert first.label("actor-b", 7) != reference
    assert CustomerPseudonymizer(b"b" * 32).label("actor-a", 7) != reference
    with pytest.raises(ScopeViolation, match="unavailable for this actor"):
        first.resolve("actor-b", [reference])
    with pytest.raises(ScopeViolation) as failure:
        first.label("actor-a", 987654321.0)
    assert "987654321" not in str(failure.value)
    with pytest.raises(ValueError):
        CustomerPseudonymizer(b"too-short")


def test_customer_ranking_sorts_metrics_before_limit_with_numeric_ties():
    pseudonymizer = CustomerPseudonymizer(b"a" * 32)
    gateway = OfflineGateway(pseudonymizer=pseudonymizer)
    spec = query(metrics=["revenue", "spend_per_customer"], dimensions=["customer"],
                 end_date="2025-03-01", order_by="revenue", limit=3)
    result = gateway.execute(spec, scope(), QueryBudget())
    expected_labels = [pseudonymizer.label("demo-a", customer) for customer in (7, 8, 9)]
    assert [row["customer"] for row in result.rows] == expected_labels
    assert [row["revenue"] for row in result.rows] == [90, 90, 90]
    assert [row["spend_per_customer"] for row in result.rows] == [90, 90, 90]
    assert all(row["group_customer_count"] == 1 for row in result.rows)
    assert "_customer_id" not in result.to_dict()["columns"]
    assert all("_customer_id" not in row for row in result.to_dict()["rows"])
    public = result.approve_for_model(spec)
    assert len(public.rows) == 3 and public.suppressed_groups == 0
    assert "group_customer_count" not in public.columns
    assert all("group_customer_count" not in row for row in public.rows)
    assert public.statistics["privacy_applied"] and public.statistics["pseudonymous_customers"]
    assert public.approve_for_model(spec) is public


def test_customer_ranking_ascending_and_mixed_product_revenue_isolation():
    pseudonymizer = CustomerPseudonymizer(b"a" * 32)
    gateway = OfflineGateway(pseudonymizer=pseudonymizer)
    spec = query(metrics=["revenue"], dimensions=["customer"], order_by="revenue", limit=1)
    permitted = gateway.execute(spec, scope(1), QueryBudget())
    assert permitted.rows[0]["revenue"] == 30
    assert permitted.rows[0]["customer"] == pseudonymizer.label("demo-a", 7)
    # The $500 lamp in every mixed order never inflates the product-1 ranking.
    broader = gateway.execute(spec, scope(1, 3), QueryBudget())
    assert broader.rows[0]["revenue"] == 530
    ascending = gateway.execute(spec.model_copy(update={"order_direction": "asc"}), scope(1), QueryBudget())
    assert ascending.rows[0]["revenue"] == 20
    assert ascending.rows[0]["customer"] == pseudonymizer.label("demo-a", 1)
    segment = gateway.execute(query(metrics=["revenue"], dimensions=["state"], order_by="revenue", limit=1), scope(), QueryBudget())
    assert segment.rows == [{"state": "Texas", "revenue": 180, "group_customer_count": 6}]


def test_customer_followups_resolve_references_and_reapply_current_product_scope():
    gateway = OfflineGateway(pseudonymizer=CustomerPseudonymizer(b"a" * 32))
    ranked = gateway.execute(query(metrics=["revenue"], dimensions=["customer"], order_by="revenue", limit=1), scope(), QueryBudget())
    reference = ranked.rows[0]["customer"]
    spec = query(metrics=["revenue", "orders"], customer_refs=[reference], start_date="2025-02-01", end_date="2025-03-01")
    result = gateway.execute(spec, scope(), QueryBudget()).approve_for_model(spec)
    assert result.rows == [{"revenue": 60, "orders": 1}]
    assert result.statistics["pseudonymous_customers"] is True
    assert gateway.execute(spec, scope(2), QueryBudget()).rows == []
    assert gateway.execute(spec, scope(3), QueryBudget()).rows[0]["revenue"] == 500
    with pytest.raises(ScopeViolation):
        gateway.execute(spec, ActorScope("other-actor", (1,)), QueryBudget())
    # Limiting a ranking exposes no reference for omitted customers.
    omitted = CustomerPseudonymizer(b"a" * 32).label("demo-a", 8)
    with pytest.raises(ScopeViolation):
        gateway.execute(query(customer_refs=[omitted]), scope(), QueryBudget())


def test_unknown_customer_references_fail_before_any_metadata_or_query():
    spec = query(customer_refs=["cust_" + "0" * 32])
    for gateway in (OfflineGateway(), BigQueryGateway(client=FakeClient())):
        budget = QueryBudget()
        with pytest.raises(ScopeViolation, match="rerun the ranking"):
            gateway.execute(spec, scope(), budget)
        assert budget.queries_used == 0
        if isinstance(gateway, BigQueryGateway):
            assert gateway.client.metadata_calls == [] and gateway.client.query_calls == []
    with pytest.raises(ScopeViolation, match="trusted resolution"):
        SQLCompiler().compile(spec, scope())


def test_compiler_uses_only_trusted_parameterized_customer_resolution_and_metric_sort():
    spec = query(metrics=["revenue"], dimensions=["customer"], customer_refs=["cust_" + "a" * 32],
                 order_by="revenue", limit=2)
    compiled = SQLCompiler().compile(spec, scope(), customer_ids=(7,))
    assert "oi.user_id IN UNNEST(@resolved_customers)" in compiled.sql
    assert "oi.user_id AS _customer_id" in compiled.sql
    assert "ORDER BY revenue DESC, _customer_id" in compiled.sql
    assert compiled.sql.index("ORDER BY") < compiled.sql.index("LIMIT")
    assert "cust_" not in compiled.sql
    assert {p.name: p.value for p in compiled.parameters}["resolved_customers"] == (7,)
    assert {p.name: p.value for p in compiled.parameters}["allowed_products"] == (1,)
    for invalid in ((True,), (-1,), (7, 8)):
        with pytest.raises(ScopeViolation):
            SQLCompiler().compile(spec, scope(), customer_ids=invalid)
    with pytest.raises(ScopeViolation):
        SQLCompiler().compile(query(), scope(), customer_ids=(7,))


def test_mocked_bigquery_transforms_raw_customer_ids_before_return_and_binds_followup():
    pseudonymizer = CustomerPseudonymizer(b"a" * 32)
    client = FakeClient(rows=[{"_customer_id": 7, "revenue": 30.0, "group_customer_count": 1}])
    gateway = BigQueryGateway(client=client, pseudonymizer=pseudonymizer)
    spec = query(metrics=["revenue"], dimensions=["customer"], order_by="revenue", limit=1)
    ranked = gateway.execute(spec, scope(), QueryBudget())
    reference = ranked.rows[0]["customer"]
    assert ranked.columns == ["customer", "revenue", "group_customer_count"]
    assert ranked.rows == [{"customer": reference, "revenue": 30, "group_customer_count": 1}]
    assert reference == pseudonymizer.label("demo-a", 7)
    assert "_customer_id" not in repr(ranked.to_dict())
    assert len(ranked.approve_for_model(spec).rows) == 1
    client.job = FakeJob([{"revenue": 60.0, "group_customer_count": 1}])
    followup = query(metrics=["revenue"], customer_refs=[reference])
    result = gateway.execute(followup, scope(), QueryBudget()).approve_for_model(followup)
    assert result.rows == [{"revenue": 60}]
    bound = {p.name: p for p in client.query_calls[-1][1]["job_config"].query_parameters}
    assert bound["resolved_customers"].values == [7]
    assert bound["allowed_products"].values == [1]
    assert "cust_" not in client.query_calls[-1][0]


def test_privacy_approval_keeps_segment_suppression_and_rejects_raw_customer_labels():
    data = seeded_data()
    data["users"][0]["state"] = "Sparse state"
    spec = query(dimensions=["state"])
    public = OfflineGateway(data).execute(spec, scope(), QueryBudget()).approve_for_model(spec)
    assert public.suppressed_groups == 1 and len(public.rows) == 2
    raw = QueryOutcome([{"customer": 7, "revenue": 30, "group_customer_count": 1}],
                       ["customer", "revenue", "group_customer_count"], "test-evidence", {}, True)
    with pytest.raises(SchemaViolation, match="opaque customer"):
        raw.approve_for_model(query(metrics=["revenue"], dimensions=["customer"]))
    client = FakeClient(rows=[{"_customer_id": "7", "revenue": 30, "group_customer_count": 1}])
    with pytest.raises(SchemaViolation, match="invalid internal customer"):
        BigQueryGateway(client=client).execute(query(metrics=["revenue"], dimensions=["customer"]), scope(), QueryBudget())
