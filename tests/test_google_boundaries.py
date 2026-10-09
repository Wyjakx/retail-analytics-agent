"""Two real SDK boundary contracts; only network I/O is replaced."""
import asyncio
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace

from google import genai
from google.adk.models.google_llm import Gemini
from google.cloud import bigquery
from google.genai import types
import pytest

from retail_agent.analytics import ActorScope, BudgetExceeded, QuerySpec
from retail_agent import gateways
from retail_agent.gateways import BigQueryGateway, QueryBudget
from retail_agent.model import GeminiModel


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
    "state": "STRING",
    "revenue": "FLOAT", "group_customer_count": "INTEGER",
}
ACTOR = ActorScope("analyst", (17, 29))


class RemoteJob:
    def __init__(self, rows, *, billed=60):
        self.rows = rows
        self.total_bytes_processed = billed
        self.total_bytes_billed = billed
        self.job_id = None
        self.result_calls = []

    def result(self, **kwargs):
        self.result_calls.append(kwargs)
        return RemoteRows(self.rows)

class RemoteRows(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.schema = [bigquery.SchemaField(name, RESULT_TYPES[name]) for name in rows[0]]

class BigQueryBoundary:
    def __init__(self, *, estimates, jobs):
        self.estimates = iter(estimates)
        self.jobs = iter(jobs)
        self.calls = []

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
        job = next(self.jobs)
        job.job_id = kwargs["job_id"]
        return job

def january_spec(**overrides):
    return QuerySpec(
        **{
            "metrics": ["revenue"],
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 2, 1),
            **overrides,
        }
    )


def test_planning_and_reporting_use_the_supported_gemini_wire_contract(monkeypatch):
    requests = []
    responses = iter([
        {"action": "analysis", "plan": {"queries": [{"metrics": ["revenue"],
            "start_date": "2025-01-01", "end_date": "2025-02-01"}]}},
        {"title": "January sales", "summary": "Revenue is 125.", "findings": ["[E1] Revenue is 125."]},
    ])
    client = genai.Client(api_key="unused-local-test-key", vertexai=False)

    async def receive_request(method, path, body, http_options=None):
        requests.append(body)
        return types.HttpResponse(body=json.dumps({"candidates": [{
            "content": {"role": "model", "parts": [{"text": json.dumps(next(responses))}]},
            "finishReason": "STOP",
        }]}))

    monkeypatch.setattr(client._api_client, "async_request", receive_request)

    async def run():
        model = GeminiModel("gemini-2.5-flash", max_retries=0,
                            model_override=Gemini(model="gemini-2.5-flash", client=client))
        try:
            decision = await model.plan("January revenue", [], {})
            drafted = await model.report("January revenue", [
                {"evidence_id": "E1", "rows": [{"revenue": 125}]},
            ], {})
            return decision, drafted
        finally:
            await client.aio.aclose()
            client.close()

    decision, drafted = asyncio.run(run())
    assert decision.plan.queries[0].metrics == ["revenue"]
    assert decision.plan.queries[0].start_date == date(2025, 1, 1)
    assert decision.plan.queries[0].end_date == date(2025, 2, 1)
    assert drafted.summary == "Revenue is 125." and drafted.findings == ["[E1] Revenue is 125."]
    assert len(requests) == 2
    for body in requests:  # The same wire compatibility contract applies to both stages.
        config = body["generationConfig"]
        assert "responseSchema" not in config
        assert config["responseMimeType"] == "application/json"
        schema = config["responseJsonSchema"]
        assert schema["additionalProperties"] is False
        assert "minItems" not in json.dumps(schema) and "maxItems" not in json.dumps(schema)


def test_bigquery_binds_scope_and_filters_and_stops_when_actual_billing_exhausts_budget(monkeypatch):
    state = "O'Brien'); SELECT * FROM private --"
    job = RemoteJob([{"state": state, "revenue": Decimal("125.50"), "group_customer_count": 4}], billed=80)
    client = BigQueryBoundary(estimates=(60, 21), jobs=[
        job, RemoteJob([{"revenue": 125, "group_customer_count": 4}], billed=21),
    ])
    clock = [1000.0]
    monkeypatch.setattr(gateways, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    submit = client.query

    def slow_first_dry_run(*args, **kwargs):
        result = submit(*args, **kwargs)
        if len(client.calls) == 1:
            clock[0] += 1.5
        return result

    client.query = slow_first_dry_run
    gateway = BigQueryGateway(dataset="retail-project.analytics", client=client,
                              maximum_bytes_billed=100, timeout_seconds=30)
    budget = QueryBudget(max_cumulative_bytes=100, deadline=1002.0)
    outcome = gateway.execute(january_spec(dimensions=["state"], states=[state], limit=7), ACTOR, budget)
    assert outcome.rows == [{"state": state, "revenue": 125.5, "group_customer_count": 4}]
    dry, live = client.calls
    scoped, aggregate = live.sql.split("\n)\nSELECT ", 1)
    assert "oi.product_id IN UNNEST(@allowed_products)" in scoped
    assert "COALESCE(u.state, 'Unknown') IN UNNEST(@states)" in scoped
    assert "oi.created_at >= TIMESTAMP(@start_date)" in scoped
    assert "oi.created_at < TIMESTAMP(@end_date)" in scoped
    assert "SUM(scoped_price)" in aggregate and state not in live.sql
    assert live.sql.endswith("LIMIT @result_limit")
    parameters = {p.name: p.to_api_repr()["parameterValue"] for p in live.config.query_parameters}
    assert parameters == {
        "allowed_products": {"arrayValues": [{"value": "17"}, {"value": "29"}]},
        "start_date": {"value": "2025-01-01"}, "end_date": {"value": "2025-02-01"},
        "states": {"arrayValues": [{"value": state}]}, "result_limit": {"value": "7"},
    }
    assert dry.config.dry_run and dry.config.use_query_cache is False
    assert live.config.maximum_bytes_billed == 100 and live.options["location"] == "EU"
    assert live.config.to_api_repr()["jobTimeoutMs"] == "500"
    assert job.result_calls == [{"timeout": 0.5, "retry": None, "job_retry": None, "page_size": 7}]
    with pytest.raises(BudgetExceeded):
        gateway.execute(january_spec(), ACTOR, budget)
    assert budget.bytes_used == 80
    assert [bool(call.config.dry_run) for call in client.calls] == [True, False, True]
    assert client.calls[-1].config.maximum_bytes_billed == 20
