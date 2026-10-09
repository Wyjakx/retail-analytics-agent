"""Cloud wire contracts exercised locally at the external network boundaries."""

import asyncio
from datetime import date
from decimal import Decimal
import json
import re
from types import SimpleNamespace

from google import genai
from google.adk.models.google_llm import Gemini
from google.api_core.exceptions import ServiceUnavailable
from google.cloud import bigquery
from google.genai import types
import pytest

from retail_agent.analytics import (
    ActorScope,
    BudgetExceeded,
    QuerySpec,
    QueryTimeout,
    ScopeViolation,
)
from retail_agent import gateways
from retail_agent.gateways import BigQueryGateway, QueryBudget
from retail_agent.model import AnalystReport, Decision, GeminiModel
from retail_agent.pseudonyms import CustomerPseudonymizer


# Independent representation of the four remote tables, including unused PII.
REMOTE_COLUMNS = {
    "orders": (("order_id", "INTEGER"), ("user_id", "INTEGER"), ("status", "STRING")),
    "order_items": (
        ("id", "INTEGER"), ("order_id", "INTEGER"), ("user_id", "INTEGER"),
        ("product_id", "INTEGER"), ("status", "STRING"), ("created_at", "TIMESTAMP"),
        ("sale_price", "FLOAT"),
    ),
    "products": (("id", "INTEGER"), ("name", "STRING"), ("category", "STRING")),
    "users": (
        ("id", "INTEGER"), ("state", "STRING"), ("country", "STRING"),
        ("email", "STRING"), ("first_name", "STRING"), ("last_name", "STRING"),
    ),
}
RESULT_TYPES = {
    "state": "STRING", "month": "STRING", "_customer_id": "INTEGER",
    "revenue": "FLOAT", "group_customer_count": "INTEGER",
}
ACTOR = ActorScope("analyst", (17, 29))


class RemoteJob:
    def __init__(self, rows, *, billed=60, result_error=None):
        self.rows = rows
        self.total_bytes_processed = billed
        self.total_bytes_billed = billed
        self.job_id = None
        self.result_error = result_error
        self.result_calls = []
        self.cancel_calls = []

    def result(self, **kwargs):
        self.result_calls.append(kwargs)
        if self.result_error:
            raise self.result_error
        return RemoteRows(self.rows)

    def cancel(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return True


class RemoteRows(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.schema = [bigquery.SchemaField(name, RESULT_TYPES[name]) for name in rows[0]]


class BigQueryBoundary:
    def __init__(self, *, estimates=(60,), jobs=None, submission_error=None):
        self.estimates = iter(estimates)
        self.jobs = iter(jobs or [RemoteJob([{"revenue": 125, "group_customer_count": 4}])])
        self.submission_error = submission_error
        self.calls = []
        self.lookups = []
        self.recovered_job = None

    def get_table(self, table_id, **kwargs):
        return SimpleNamespace(
            schema=[bigquery.SchemaField(name, data_type)
                    for name, data_type in REMOTE_COLUMNS[table_id.rsplit(".", 1)[1]]],
            location="EU",
        )

    def query(self, sql, *, job_config, **kwargs):
        self.calls.append(SimpleNamespace(sql=sql, config=job_config, options=kwargs))
        if job_config.dry_run:
            return SimpleNamespace(total_bytes_processed=next(self.estimates))
        if self.submission_error:
            raise self.submission_error
        job = next(self.jobs)
        job.job_id = kwargs["job_id"]
        return job

    def get_job(self, job_id, **kwargs):
        self.lookups.append((job_id, kwargs))
        return self.recovered_job


def january_spec(**overrides):
    return QuerySpec(
        **{
            "metrics": ["revenue"],
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 2, 1),
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "stage,response,expected_type",
    [
        (
            "planner",
            {"action": "analysis", "plan": {"queries": [{
                "metrics": ["revenue"], "start_date": "2025-01-01",
                "end_date": "2025-02-01",
            }]}, "message": ""},
            Decision,
        ),
        (
            "reporter",
            {"title": "January sales", "summary": "Scoped revenue is 125.",
             "findings": ["[E1] Revenue is 125."], "action_items": [], "caveats": []},
            AnalystReport,
        ),
    ],
    ids=["Decision", "AnalystReport"],
)
def test_adk_output_schema_serializes_on_supported_gemini_json_wire_path(
    monkeypatch, stage, response, expected_type,
):
    # Regression: retaining ADK's legacy response_schema sends unsupported
    # additional_properties. Keep the real ADK and SDK serialization path.
    requests = []
    client = genai.Client(api_key="unused-local-test-key", vertexai=False)

    async def receive_request(method, path, body, http_options=None):
        requests.append((method, path, body))
        return types.HttpResponse(body=json.dumps({
            "candidates": [{"content": {"role": "model", "parts": [{
                "text": json.dumps(response),
            }]}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 20, "candidatesTokenCount": 10, "totalTokenCount": 30,
            },
        }))

    monkeypatch.setattr(client._api_client, "async_request", receive_request)

    async def run():
        model = GeminiModel(
            "gemini-2.5-flash", max_retries=0,
            model_override=Gemini(model="gemini-2.5-flash", client=client),
        )
        try:
            if stage == "planner":
                return await model.plan("January revenue", [], {})
            return await model.report(
                "January revenue", [{"evidence_id": "E1", "rows": [{"revenue": 125}]}], {},
            )
        finally:
            await client.aio.aclose()
            client.close()

    output = asyncio.run(run())
    assert isinstance(output, expected_type)
    if stage == "planner":
        assert output.action == "analysis"
        assert output.plan.queries[0].metrics == ["revenue"]
        assert output.plan.queries[0].start_date == date(2025, 1, 1)
        assert output.plan.queries[0].end_date == date(2025, 2, 1)
    else:
        assert output.summary == "Scoped revenue is 125."
        assert output.findings == ["[E1] Revenue is 125."]
    assert len(requests) == 1
    method, path, body = requests[0]
    assert method == "post"
    assert path.endswith("models/gemini-2.5-flash:generateContent")
    config = body["generationConfig"]
    assert "responseSchema" not in config
    schema = config["responseJsonSchema"]
    assert schema["additionalProperties"] is False
    assert config["responseMimeType"] == "application/json"
    assert "minItems" not in json.dumps(schema)
    assert "maxItems" not in json.dumps(schema)
    if stage == "planner":
        assert schema["properties"]["action"]["enum"] == [
            "analysis", "schema", "clarify", "refuse",
        ]
        query = schema["$defs"]["QuerySpec"]
        assert query["additionalProperties"] is False
        assert query["properties"]["start_date"]["anyOf"][0]["format"] == "date"
        assert "customer" in query["properties"]["dimensions"]["items"]["enum"]
    else:
        assert set(schema["properties"]) == {
            "title", "summary", "findings", "action_items", "caveats",
        }
        assert schema["properties"]["findings"]["items"]["type"] == "string"


def test_bigquery_submits_scoped_parameterized_sql_with_bounded_job_settings(monkeypatch):
    # Regression: interpolated filters, scoping after aggregation, or removing
    # BigQuery's independent billing/time/retry controls.
    state = "O'Brien'); SELECT * FROM private --"
    job = RemoteJob([{"state": state, "revenue": Decimal("125.50"), "group_customer_count": 4}])
    client = BigQueryBoundary(jobs=[job])
    clock = [1000.0]
    monkeypatch.setattr(gateways, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    submit = client.query

    def slow_dry_run(*args, **kwargs):
        result = submit(*args, **kwargs)
        if kwargs["job_config"].dry_run:
            clock[0] += 1.5
        return result

    client.query = slow_dry_run
    gateway = BigQueryGateway(
        dataset="retail-project.analytics", client=client,
        maximum_bytes_billed=100, timeout_seconds=30,
    )
    outcome = gateway.execute(
        january_spec(dimensions=["state"], states=[state], limit=7), ACTOR,
        QueryBudget(max_cumulative_bytes=80, deadline=1002.0),
    )

    assert outcome.rows == [{"state": state, "revenue": 125.5, "group_customer_count": 4}]
    assert outcome.simulated is False
    assert len(client.calls) == 2
    dry, live = client.calls
    assert dry.sql == live.sql
    scoped, aggregate = live.sql.split("\n)\nSELECT ", 1)
    assert "oi.product_id IN UNNEST(@allowed_products)" in scoped
    assert "COALESCE(u.state, 'Unknown') IN UNNEST(@states)" in scoped
    assert "oi.created_at >= TIMESTAMP(@start_date)" in scoped
    assert "oi.created_at < TIMESTAMP(@end_date)" in scoped
    assert "SUM(scoped_price)" in aggregate
    assert "FROM scoped_items" in aggregate
    assert state not in live.sql
    assert live.sql.endswith("LIMIT @result_limit")
    expected_parameters = [
        {"name": "allowed_products", "parameterType": {"type": "ARRAY", "arrayType": {
            "type": "INT64"}}, "parameterValue": {"arrayValues": [{"value": "17"}, {"value": "29"}]}},
        {"name": "start_date", "parameterType": {"type": "DATE"},
         "parameterValue": {"value": "2025-01-01"}},
        {"name": "end_date", "parameterType": {"type": "DATE"},
         "parameterValue": {"value": "2025-02-01"}},
        {"name": "states", "parameterType": {"type": "ARRAY", "arrayType": {
            "type": "STRING"}}, "parameterValue": {"arrayValues": [{"value": state}]}},
        {"name": "result_limit", "parameterType": {"type": "INT64"},
         "parameterValue": {"value": "7"}},
    ]
    for call, remaining in ((dry, 2), (live, 0.5)):
        assert isinstance(call.config, bigquery.QueryJobConfig)
        wire = call.config.to_api_repr()
        assert wire["query"]["queryParameters"] == expected_parameters
        assert wire["query"]["maximumBytesBilled"] == "80"
        assert wire["query"]["useLegacySql"] is False
        assert wire["jobTimeoutMs"] == str(int(remaining * 1000))
        assert call.options["location"] == "EU"
        assert call.options["timeout"] == remaining
        assert call.options["retry"] is None
        assert call.options["job_retry"] is None
    assert dry.config.dry_run is True
    assert dry.config.use_query_cache is False
    assert live.options["job_id"].startswith("retail_")
    assert outcome.statistics["job_id"] == live.options["job_id"]
    assert job.result_calls == [{"timeout": 0.5, "retry": None, "job_retry": None, "page_size": 7}]


def test_cancellation_during_dry_run_prevents_a_billable_submission():
    budget = QueryBudget()
    client = BigQueryBoundary()
    submit = client.query

    def cancelled_while_waiting(*args, **kwargs):
        result = submit(*args, **kwargs)
        budget.cancel()
        return result

    client.query = cancelled_while_waiting
    with pytest.raises(QueryTimeout):
        BigQueryGateway(client=client).execute(january_spec(), ACTOR, budget)
    assert len(client.calls) == 1
    assert client.calls[0].config.dry_run is True


def test_dry_run_over_per_query_byte_cap_never_submits_live_job():
    # Regression: a dry-run estimate is checked only against the larger turn cap.
    client = BigQueryBoundary(estimates=(101,))
    gateway = BigQueryGateway(client=client, maximum_bytes_billed=100)
    with pytest.raises(BudgetExceeded):
        gateway.execute(january_spec(), ACTOR, QueryBudget(max_cumulative_bytes=500))
    assert len(client.calls) == 1
    assert client.calls[0].config.dry_run is True
    assert client.calls[0].config.maximum_bytes_billed == 100


def test_actual_billing_reduces_turn_budget_before_second_live_job():
    # Regression: reserving only estimates lets the second job exceed the turn cap.
    job = RemoteJob([{"revenue": 125, "group_customer_count": 4}], billed=80)
    client = BigQueryBoundary(estimates=(60, 21), jobs=[job])
    gateway = BigQueryGateway(client=client, maximum_bytes_billed=100)
    budget = QueryBudget(max_cumulative_bytes=100)
    gateway.execute(january_spec(), ACTOR, budget)
    with pytest.raises(BudgetExceeded):
        gateway.execute(january_spec(), ACTOR, budget)
    assert budget.bytes_used == 80
    assert len(client.calls) == 3
    assert [bool(call.config.dry_run) for call in client.calls] == [True, False, True]
    assert client.calls[-1].config.maximum_bytes_billed == 20


def test_bigquery_result_timeout_cancels_the_submitted_job():
    # Regression: reporting a timeout while the billable job keeps running.
    job = RemoteJob([], result_error=TimeoutError("remote job is still executing"))
    client = BigQueryBoundary(jobs=[job])
    gateway = BigQueryGateway(client=client, timeout_seconds=2)
    with pytest.raises(QueryTimeout):
        gateway.execute(january_spec(), ACTOR, QueryBudget())
    assert len(client.calls) == 2
    assert job.cancel_calls == [{"timeout": 1, "retry": None}]
    assert job.result_calls[0]["timeout"] == 2


def test_uncertain_submission_reconciles_exact_job_id_without_duplicate_query():
    # Regression: retrying an ambiguous jobs.insert launches a duplicate charge.
    client = BigQueryBoundary(submission_error=ServiceUnavailable("response lost after acceptance"))
    recovered = RemoteJob([])
    client.recovered_job = recovered
    gateway = BigQueryGateway(client=client)
    with pytest.raises(QueryTimeout):
        gateway.execute(january_spec(), ACTOR, QueryBudget())
    assert len(client.calls) == 2
    submitted = client.calls[1]
    assert submitted.config.dry_run is not True
    assert client.lookups == [(
        submitted.options["job_id"], {"location": "EU", "timeout": 1, "retry": None},
    )]
    assert recovered.cancel_calls == [{"timeout": 1, "retry": None}]
    assert recovered.result_calls == []


def test_customer_ids_leave_gateway_as_actor_bound_refs_and_followup_uses_int_parameter():
    # Regression: a raw ID leaks into public rows, or follow-up references become
    # interpolated SQL or resolve for a different actor.
    rank_job = RemoteJob([{"_customer_id": 1042, "revenue": 125, "group_customer_count": 1}])
    followup_job = RemoteJob([{"month": "2025-01", "revenue": 125, "group_customer_count": 1}])
    client = BigQueryBoundary(estimates=(60, 60), jobs=[rank_job, followup_job])
    gateway = BigQueryGateway(client=client, pseudonymizer=CustomerPseudonymizer(b"k" * 32))
    budget = QueryBudget()
    ranked = gateway.execute(january_spec(dimensions=["customer"]), ACTOR, budget)
    assert ranked.columns == ["customer", "revenue", "group_customer_count"]
    reference = ranked.rows[0]["customer"]
    assert re.fullmatch(r"cust_[0-9a-f]{32}", reference)
    assert ranked.rows == [{"customer": reference, "revenue": 125, "group_customer_count": 1}]
    followup = january_spec(dimensions=["month"], customer_refs=[reference])
    outcome = gateway.execute(followup, ACTOR, budget)
    assert outcome.rows == [{"month": "2025-01", "revenue": 125, "group_customer_count": 1}]
    submitted = client.calls[-1]
    assert "oi.user_id IN UNNEST(@resolved_customers)" in submitted.sql
    assert reference not in submitted.sql
    assert "1042" not in submitted.sql
    parameters = {p["name"]: p for p in submitted.config.to_api_repr()["query"]["queryParameters"]}
    assert parameters["resolved_customers"] == {
        "name": "resolved_customers",
        "parameterType": {"type": "ARRAY", "arrayType": {"type": "INT64"}},
        "parameterValue": {"arrayValues": [{"value": "1042"}]},
    }
    with pytest.raises(ScopeViolation):
        gateway.execute(followup, ActorScope("another-analyst", (17, 29)), QueryBudget())
    assert len(client.calls) == 4
