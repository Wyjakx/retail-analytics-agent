"""Execution boundaries for live BigQuery and explicitly simulated fixture data."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
import secrets
import threading
import time
from typing import Any
from uuid import uuid4

from .analytics import ActorScope, BudgetExceeded, QuerySpec, QueryTimeout, SAFE_COLUMNS, SQLCompiler, SchemaViolation, permitted_products
from .pseudonyms import CustomerPseudonymizer


@dataclass
class QueryBudget:
    max_queries: int = 3
    max_cumulative_bytes: int = 300_000_000
    deadline: float | None = None
    queries_used: int = field(default=0, init=False)
    bytes_used: int = field(default=0, init=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _job_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _active_job: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_queries < 1 or self.max_cumulative_bytes < 1:
            raise ValueError("Query and byte budgets must be positive.")

    @property
    def remaining_bytes(self) -> int:
        return self.max_cumulative_bytes - self.bytes_used

    def check_active(self) -> None:
        if self._cancelled.is_set() or (self.deadline is not None and time.monotonic() >= self.deadline):
            raise QueryTimeout("The analytical request was cancelled or reached its deadline.")

    def rpc_timeout(self, configured_seconds: float) -> float:
        self.check_active()
        if self.deadline is None:
            return configured_seconds
        return max(0.001, min(configured_seconds, self.deadline - time.monotonic()))

    @staticmethod
    def _cancel_job(job: Any) -> None:
        if job is not None:
            try:
                job.cancel(timeout=1, retry=None)
            except Exception:
                pass  # Cancellation is best effort; the server deadline still applies.

    def cancel(self) -> None:
        self._cancelled.set()
        with self._job_lock:
            job = self._active_job
        self._cancel_job(job)

    def register_job(self, job: Any) -> None:
        with self._job_lock:
            self._active_job = job
        if self._cancelled.is_set():
            self._cancel_job(job)
        self.check_active()

    def finish_job(self) -> None:
        with self._job_lock:
            self._active_job = None

    def begin_query(self) -> None:
        self.check_active()
        if self.queries_used >= self.max_queries or self.remaining_bytes <= 0:
            raise BudgetExceeded("The request's query or byte budget has been exhausted.")
        self.queries_used += 1

    def reserve_bytes(self, amount: int) -> None:
        if amount < 0 or amount > self.remaining_bytes:
            raise BudgetExceeded("The query exceeds the request's remaining byte budget.")
        self.bytes_used += amount

    def reconcile_bytes(self, estimated: int, actual: int) -> None:
        # Keep conservative estimates, but account for higher actual billing too.
        self.reserve_bytes(max(0, actual - estimated))


@dataclass(frozen=True)
class QueryOutcome:
    rows: list[dict[str, Any]]
    columns: list[str]
    evidence_id: str
    statistics: dict[str, Any]
    simulated: bool
    suppressed_groups: int = 0

    def approve_for_model(self, spec: QuerySpec, min_customers: int = 3) -> QueryOutcome:
        """Apply the explicit customer-reference policy or normal segment suppression."""
        spec = QuerySpec.model_validate(spec.model_dump())
        if "customer" not in spec.dimensions and spec.customer_refs is None:
            return self.suppress_small_groups(min_customers)
        if min_customers < 1:
            raise ValueError("A positive customer threshold is required.")
        if "group_customer_count" not in self.columns:
            if self.statistics.get("privacy_applied") and self.statistics.get("pseudonymous_customers"):
                return self
            raise SchemaViolation("Aggregate customer counts are required for privacy approval.")
        retained = []
        for row in self.rows:
            count = row.get("group_customer_count")
            if type(count) is not int or count < 0:
                raise SchemaViolation("Invalid aggregate customer count.")
            if "customer" in spec.dimensions and (not isinstance(row.get("customer"), str)
                                                  or not re.fullmatch(r"cust_[0-9a-f]{32}", row["customer"])):
                raise SchemaViolation("Customer statistics require opaque customer references.")
            retained.append({key: value for key, value in row.items() if key != "group_customer_count"})
        return QueryOutcome(
            retained, [key for key in self.columns if key != "group_customer_count"], self.evidence_id,
            {**self.statistics, "privacy_applied": True, "pseudonymous_customers": True,
             "privacy_mode": "pseudonymous_customer"}, self.simulated, self.suppressed_groups,
        )

    def suppress_small_groups(self, min_customers: int = 3) -> QueryOutcome:
        if min_customers < 1:
            raise ValueError("A positive customer threshold is required.")
        if "group_customer_count" not in self.columns:
            if self.statistics.get("privacy_applied") and self.statistics.get("minimum_customers", 0) >= min_customers:
                return self
            raise SchemaViolation("Aggregate customer counts are required for suppression.")
        retained = []
        suppressed = self.suppressed_groups
        for row in self.rows:
            count = row.get("group_customer_count")
            if type(count) is not int or count < 0:
                raise SchemaViolation("Invalid aggregate customer count.")
            if count < min_customers:
                suppressed += 1
            else:
                retained.append({key: value for key, value in row.items() if key != "group_customer_count"})
        return QueryOutcome(
            retained, [key for key in self.columns if key != "group_customer_count"], self.evidence_id,
            {**self.statistics, "privacy_applied": True, "minimum_customers": min_customers}, self.simulated, suppressed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"rows": deepcopy(self.rows), "columns": list(self.columns), "evidence_id": self.evidence_id,
                "statistics": dict(self.statistics), "simulated": self.simulated, "suppressed_groups": self.suppressed_groups}


def _evidence(spec: QuerySpec, scope: ActorScope, prefix: str) -> str:
    encoded = json.dumps({"spec": spec.model_dump(mode="json"), "actor": scope.actor_id, "products": scope.allowed_product_ids}, sort_keys=True)
    return f"{prefix}-{hashlib.sha256(encoded.encode()).hexdigest()[:16]}"


def _columns(spec: QuerySpec) -> list[str]:
    return list(spec.dimensions) + list(spec.metrics) + ["group_customer_count"]


def _public_customer_rows(rows: list[dict[str, Any]], scope: ActorScope,
                          pseudonymizer: CustomerPseudonymizer) -> list[dict[str, Any]]:
    """Transform trusted internal IDs before the gateway returns any rows."""
    if any("_customer_id" in row and (type(row["_customer_id"]) is not int or row["_customer_id"] <= 0)
           for row in rows):
        raise SchemaViolation("The query returned an invalid internal customer dimension.")
    public = []
    for row in rows:
        safe = dict(row)
        if "_customer_id" in safe:
            safe["customer"] = pseudonymizer.label(scope.actor_id, safe.pop("_customer_id"))
        public.append(safe)
    return public


class BigQueryGateway:
    def __init__(self, dataset: str = "bigquery-public-data.thelook_ecommerce", billing_project: str | None = None,
                 timeout_seconds: float = 30, maximum_bytes_billed: int = 100_000_000,
                 client: Any = None, location: str | None = None,
                 pseudonymizer: CustomerPseudonymizer | None = None) -> None:
        if timeout_seconds <= 0 or maximum_bytes_billed <= 0:
            raise ValueError("Timeout and per-query byte limit must be positive.")
        self.compiler = SQLCompiler(dataset)
        self.billing_project = billing_project
        self.timeout_seconds = timeout_seconds
        self.maximum_bytes_billed = maximum_bytes_billed
        self.client = client
        self.location = location
        self._schema_checked = False
        self.last_metadata: dict[str, Any] = {}
        self.pseudonymizer = pseudonymizer if pseudonymizer is not None else CustomerPseudonymizer(secrets.token_bytes(32))

    def schema_catalog(self) -> dict[str, list[str]]:
        return {name: list(columns) for name, columns in SAFE_COLUMNS.items()}

    def _get_client(self) -> Any:
        if self.client is None:
            from google.cloud import bigquery
            self.client = bigquery.Client(project=self.billing_project)
        return self.client

    def validate_schema(self, budget: QueryBudget | None = None) -> dict[str, list[str]]:
        if not self._schema_checked:
            if budget:
                budget.check_active()
            client = self._get_client()
            for table, required in SAFE_COLUMNS.items():
                timeout = budget.rpc_timeout(self.timeout_seconds) if budget else self.timeout_seconds
                metadata = client.get_table(f"{self.compiler.dataset}.{table}", timeout=timeout, retry=None)
                available = {column.name for column in metadata.schema}
                if not set(required).issubset(available):
                    raise SchemaViolation(f"The restricted schema for {table} is incompatible.")
                found_location = getattr(metadata, "location", None)
                if found_location:
                    if self.location and self.location.lower() != found_location.lower():
                        raise SchemaViolation("The configured query location does not match the dataset.")
                    self.location = found_location
            self._schema_checked = True
        return self.schema_catalog()

    def execute(self, spec: QuerySpec, scope: ActorScope, budget: QueryBudget) -> QueryOutcome:
        self.last_metadata = {"stage": "validation", "status": "running"}
        try:
            return self._execute(spec, scope, budget)
        except Exception:
            if self.last_metadata.get("status") != "submission_uncertain":
                self.last_metadata["status"] = "failed"
            raise

    def _reconcile_submission(self, client: Any, job_id: str) -> None:
        """Cleanup may outlive the turn deadline, but cannot create a new query."""
        try:
            job = client.get_job(job_id, location=self.location, timeout=1, retry=None)
        except Exception as error:
            self.last_metadata.update(reconciliation="lookup_failed", reconciliation_error=type(error).__name__)
            return
        self.last_metadata.update(reconciliation="job_found", cancellation="requested")
        QueryBudget._cancel_job(job)

    def _execute(self, spec: QuerySpec, scope: ActorScope, budget: QueryBudget) -> QueryOutcome:
        spec = QuerySpec.model_validate(spec.model_dump())
        permitted_products(spec, scope)
        customer_ids = self.pseudonymizer.resolve(scope.actor_id, spec.customer_refs) if spec.customer_refs else None
        compiled = self.compiler.compile(spec, scope, customer_ids=customer_ids)
        budget.begin_query()
        self.last_metadata["stage"] = "schema_validation"
        self.validate_schema(budget)
        from google.api_core.exceptions import DeadlineExceeded, InternalServerError, ServiceUnavailable
        from google.auth.exceptions import TransportError
        from google.cloud import bigquery
        parameters = [
            bigquery.ArrayQueryParameter(p.name, p.data_type, list(p.value)) if p.is_array
            else bigquery.ScalarQueryParameter(p.name, p.data_type, p.value)
            for p in compiled.parameters
        ]
        cap = min(self.maximum_bytes_billed, budget.remaining_bytes)
        options = dict(query_parameters=parameters, maximum_bytes_billed=cap, use_legacy_sql=False,
                       job_timeout_ms=max(1, int(budget.rpc_timeout(self.timeout_seconds) * 1000)))
        client = self._get_client()
        self.last_metadata.update(stage="dry_run", maximum_bytes_billed=cap)
        dry = client.query(compiled.sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, **options),
                           location=self.location, timeout=budget.rpc_timeout(self.timeout_seconds), retry=None, job_retry=None)
        budget.check_active()
        estimated = int(dry.total_bytes_processed or 0)
        self.last_metadata["estimated_bytes"] = estimated
        if estimated > cap:
            raise BudgetExceeded("The query exceeds its byte limit.")
        budget.reserve_bytes(estimated)
        # Disabling submission retries avoids unknowingly launching duplicate jobs.
        options["job_timeout_ms"] = max(1, int(budget.rpc_timeout(self.timeout_seconds) * 1000))
        job_id = "retail_" + uuid4().hex
        self.last_metadata.update(stage="query_submission", job_id=job_id)
        try:
            job = client.query(compiled.sql, job_config=bigquery.QueryJobConfig(**options), job_id=job_id,
                               location=self.location, timeout=budget.rpc_timeout(self.timeout_seconds), retry=None, job_retry=None)
        except (TimeoutError, DeadlineExceeded, InternalServerError, ServiceUnavailable, TransportError, OSError) as error:
            self.last_metadata["status"] = "submission_uncertain"
            self._reconcile_submission(client, job_id)
            raise QueryTimeout("BigQuery submission outcome is uncertain; reconciliation was attempted without resubmission.") from error
        self.last_metadata["stage"] = "query_execution"
        try:
            budget.register_job(job)
            result = job.result(timeout=budget.rpc_timeout(self.timeout_seconds), retry=None, job_retry=None,
                                page_size=spec.limit)
            budget.check_active()
        except (TimeoutError, DeadlineExceeded, QueryTimeout) as error:
            self.last_metadata["cancellation"] = "requested"
            budget._cancel_job(job)
            raise QueryTimeout("The analytical query timed out; cancellation was requested.") from error
        finally:
            budget.finish_job()
        self.last_metadata["stage"] = "result_validation"
        allowed = ["_customer_id" if name == "customer" else name for name in _columns(spec)]
        if {column.name for column in result.schema} != set(allowed):
            raise SchemaViolation("The query returned an unexpected result schema.")
        rows = []
        for record in result:
            budget.check_active()
            row = dict(record)
            if set(row) != set(allowed):
                raise SchemaViolation("The query returned unexpected columns.")
            rows.append({key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()})
            if len(rows) > spec.limit:
                raise SchemaViolation("The query returned more rows than requested.")
        billed = int(job.total_bytes_billed or 0)
        self.last_metadata.update(bytes_processed=int(job.total_bytes_processed or 0), bytes_billed=billed)
        budget.reconcile_bytes(estimated, billed)
        budget.check_active()
        public_rows = _public_customer_rows(rows, scope, self.pseudonymizer)
        self.last_metadata.update(stage="complete", status="succeeded")
        return QueryOutcome(public_rows, _columns(spec), _evidence(spec, scope, "bq"),
                            {"job_id": job.job_id, "estimated_bytes": estimated, "bytes_processed": int(job.total_bytes_processed or 0),
                             "bytes_billed": billed, "source": "bigquery", "reporting_timezone": "UTC"}, False)


def seeded_data() -> dict[str, list[dict[str, Any]]]:
    """Synthetic relational fixtures; never a snapshot of the live retail dataset."""
    data: dict[str, list[dict[str, Any]]] = {
        "users": [], "orders": [], "order_items": [],
        "products": [{"id": 1, "name": "Demo tee", "category": "Clothing"},
                     {"id": 2, "name": "Demo coat", "category": "Clothing"},
                     {"id": 3, "name": "Demo lamp", "category": "Home"}],
    }
    for customer in range(1, 13):
        data["users"].append({"id": customer, "state": "California" if customer <= 6 else "Texas", "country": "United States"})
        for month in (1, 2):
            order = customer * 10 + month
            data["orders"].append({"order_id": order, "user_id": customer, "status": "Complete"})
            for product, price in [(1, (20 if customer <= 6 else 30) * month), (3, 500)]:
                data["order_items"].append({"id": len(data["order_items"]) + 1, "order_id": order, "user_id": customer,
                                            "product_id": product, "status": "Complete", "created_at": f"2025-{month:02d}-15T12:00:00+00:00", "sale_price": price})
    return data


class OfflineGateway:
    def __init__(self, data: dict[str, list[dict[str, Any]]] | None = None,
                 pseudonymizer: CustomerPseudonymizer | None = None) -> None:
        self.pseudonymizer = pseudonymizer if pseudonymizer is not None else CustomerPseudonymizer(secrets.token_bytes(32))
        self.data = deepcopy(seeded_data() if data is None else data)
        for table, required in SAFE_COLUMNS.items():
            if table not in self.data or any(not set(required).issubset(row) for row in self.data[table]):
                raise SchemaViolation(f"The fixture schema for {table} is incompatible.")
        for table, key in [("orders", "order_id"), ("products", "id"), ("users", "id"), ("order_items", "id")]:
            if len({row[key] for row in self.data[table]}) != len(self.data[table]):
                raise SchemaViolation(f"Duplicate fixture keys in {table}.")

    def schema_catalog(self) -> dict[str, list[str]]:
        return {name: list(columns) for name, columns in SAFE_COLUMNS.items()}

    def execute(self, spec: QuerySpec, scope: ActorScope, budget: QueryBudget) -> QueryOutcome:
        spec = QuerySpec.model_validate(spec.model_dump())
        products = set(permitted_products(spec, scope))
        customer_ids = set(self.pseudonymizer.resolve(scope.actor_id, spec.customer_refs)) if spec.customer_refs else None
        budget.begin_query()
        orders = {row["order_id"]: row for row in self.data["orders"]}
        users = {row["id"]: row for row in self.data["users"]}
        catalog = {row["id"]: row for row in self.data["products"]}
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for item in self.data["order_items"]:
            budget.check_active()
            order, user, product = orders.get(item["order_id"]), users.get(item["user_id"]), catalog.get(item["product_id"])
            if not order or not user or not product or order["user_id"] != item["user_id"]:
                continue
            if item["product_id"] not in products or item["status"] not in ("Complete", "Shipped") or order["status"] not in ("Complete", "Shipped"):
                continue
            if customer_ids is not None and item["user_id"] not in customer_ids:
                continue
            price = Decimal(str(item["sale_price"]))
            if price < 0:
                continue
            timestamp = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            day = timestamp.astimezone(timezone.utc).date()
            if spec.start_date is not None and not spec.start_date <= day < spec.end_date:
                continue
            labels = {"month": day.strftime("%Y-%m"), "state": user["state"] or "Unknown", "country": user["country"] or "Unknown",
                      "category": product["category"] or "Unknown", "product": f"{product['id']}: {product['name']}",
                      "customer": item["user_id"]}
            if any(getattr(spec, field_name) is not None and labels[label] not in getattr(spec, field_name)
                   for field_name, label in [("states", "state"), ("countries", "country"), ("categories", "category")]):
                continue
            groups.setdefault(tuple(labels[name] for name in spec.dimensions), []).append(item)
        rows = []
        dimension_aliases = ["_customer_id" if name == "customer" else name for name in spec.dimensions]
        for labels, items in sorted(groups.items()):
            revenue = sum((Decimal(str(item["sale_price"])) for item in items), Decimal(0))
            order_count = len({item["order_id"] for item in items})
            customer_count = len({item["user_id"] for item in items})
            def money(value: Decimal) -> float:
                return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            values = {"revenue": money(revenue), "orders": order_count, "purchasing_customers": customer_count, "units": len(items),
                      "average_order_value": money(revenue / order_count), "spend_per_customer": money(revenue / customer_count)}
            rows.append({**dict(zip(dimension_aliases, labels)), **{metric: values[metric] for metric in spec.metrics}, "group_customer_count": customer_count})
        # Stable sorting preserves ascending dimension ties, including numeric customer IDs.
        if spec.order_by:
            rows.sort(key=lambda row: row[spec.order_by], reverse=spec.order_direction == "desc")
        budget.check_active()
        public_rows = _public_customer_rows(rows[:spec.limit], scope, self.pseudonymizer)
        return QueryOutcome(public_rows, _columns(spec), _evidence(spec, scope, "fixture"),
                            {"source": "synthetic_fixture", "estimated_bytes": 0, "bytes_processed": 0, "bytes_billed": 0,
                             "reporting_timezone": "UTC", "fixture_period": "2025-01 through 2025-02"}, True)
