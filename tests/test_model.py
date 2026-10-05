import asyncio
from datetime import date
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field, ValidationError
import pytest

from retail_agent.adk_workflow import ModelFailure
from retail_agent.model import Decision, GeminiModel, OfflineModel, deterministic_report


class FakeAdkModel(BaseLlm):
    model: str = "fake-local-model"
    responses: list[str] = Field(default_factory=list)
    requests: list[Any] = Field(default_factory=list)
    delay_seconds: float = 0
    transient_failures: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.transient_failures:
            self.transient_failures -= 1
            error = ConnectionError("private provider response must never be returned")
            raise error
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.responses.pop(0))]),
            turn_complete=True,
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=20, candidates_token_count=10, total_token_count=30
            ),
        )


VALID_PLAN = '{"action":"analysis","plan":{"queries":[{"metrics":["revenue"],"dimensions":["month"],"start_date":"2024-01-01","end_date":"2025-01-01"}]},"message":""}'


def test_real_adk_graph_with_fake_model_validates_typed_output_and_has_no_tools():
    fake = FakeAdkModel(responses=[VALID_PLAN])
    model = GeminiModel("fake-local-model", model_override=fake, max_retries=0)
    decision = asyncio.run(model.plan("Monthly revenue in 2024", [], {"metrics": ["revenue"]}))
    assert decision.plan.queries[0].start_date == date(2024, 1, 1)
    assert model.last_metadata["model_calls"] == 1
    assert model.last_metadata["total_tokens"] == 30
    assert model.last_metadata["status"] == "success"
    assert fake.requests[0].config.tools in (None, [])


def test_adk_graph_rejects_raw_sql_instead_of_executing_it():
    fake = FakeAdkModel(responses=['{"action":"analysis","plan":{"queries":[{"metrics":["revenue"],"sql":"SELECT email FROM users"}]},"message":""}'])
    model = GeminiModel("fake-local-model", model_override=fake, max_retries=0)
    with pytest.raises(ModelFailure) as error:
        asyncio.run(model.plan("Revenue all time", [], {}))
    assert "SELECT" not in str(error.value)
    assert model.last_metadata["model_calls"] == 1


def test_transient_failure_has_a_bounded_retry_budget():
    fake = FakeAdkModel(responses=[VALID_PLAN], transient_failures=1)
    model = GeminiModel("fake-local-model", model_override=fake, max_retries=1)
    decision = asyncio.run(model.plan("Revenue in 2024", [], {}))
    assert decision.action == "analysis"
    assert model.last_metadata["model_calls"] == 2
    assert model.last_metadata["retries"] == 1


def test_turn_call_budget_is_shared_between_planning_and_reporting_and_retries():
    async def run():
        fake = FakeAdkModel(responses=[VALID_PLAN], transient_failures=1)
        model = GeminiModel("fake-local-model", model_override=fake, max_retries=2)
        model.begin_turn(max_calls=2)
        assert (await model.plan("Revenue in 2024", [], {})).action == "analysis"
        assert model.runner.turn_model_calls == 2
        with pytest.raises(ModelFailure) as error:
            await model.report("Revenue in 2024", [], {})
        assert error.value.code == "model_call_budget_exceeded"
        assert len(fake.requests) == 2
        assert model.last_metadata["model_calls"] == 0
        fake.responses.append(VALID_PLAN)
        model.begin_turn(max_calls=1)
        assert (await model.plan("Revenue in 2024", [], {})).action == "analysis"
        assert model.runner.turn_model_calls == 1
    asyncio.run(run())


def test_stage_timeout_cancels_model_work():
    fake = FakeAdkModel(responses=[VALID_PLAN], delay_seconds=0.5)
    model = GeminiModel("fake-local-model", model_override=fake, timeout_seconds=0.05, max_retries=0)
    with pytest.raises(ModelFailure) as error:
        asyncio.run(model.plan("Revenue in 2024", [], {}))
    assert error.value.code == "model_timeout"
    assert len(fake.requests) == 1


def test_external_cancellation_is_preserved():
    async def run():
        fake = FakeAdkModel(responses=[VALID_PLAN], delay_seconds=1)
        model = GeminiModel("fake-local-model", model_override=fake, timeout_seconds=2)
        task = asyncio.create_task(model.plan("Revenue in 2024", [], {}))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert model.last_metadata["status"] == "cancelled"
    asyncio.run(run())


def test_offline_followup_keeps_period_metrics_and_product_filters():
    async def run():
        model = OfflineModel()
        first = await model.plan("Compare monthly sales of products 1 and 2 in 2024", [], {})
        assert first.action == "analysis"
        assert first.plan.queries[0].dimensions == ["month", "product"]
        followup = await model.plan("Only California", [], {}, previous_plan=first.plan)
        query = followup.plan.queries[0]
        assert query.product_ids == [1, 2]
        assert query.start_date == date(2024, 1, 1)
        assert query.end_date == date(2025, 1, 1)
        assert query.metrics == ["revenue"]
        assert query.states == ["California"]
    asyncio.run(run())


def test_offline_requires_explicit_period_and_refuses_personal_rankings():
    async def run():
        model = OfflineModel()
        assert (await model.plan("Monthly sales", [], {})).action == "clarify"
        assert (await model.plan("Top customers all time", [], {})).action == "refuse"
        assert (await model.plan("Describe database tables", [], {})).action == "schema"
        assert (await model.plan("Revenue all time", [], {})).plan.queries[0].start_date is None
    asyncio.run(run())


def test_offline_multistep_comparison_and_report_label_simulation():
    async def run():
        model = OfflineModel()
        decision = await model.plan("Compare revenue in 2023 versus 2024", [], {})
        assert len(decision.plan.queries) == 2
        report = await model.report("Compare revenue", [{"evidence_id": "E1", "rows": [{"revenue": 15.5}], "simulated": True}], {})
        assert "[E1] revenue=15.5" in report.findings
        assert any("Synthetic offline" in caveat for caveat in report.caveats)
    asyncio.run(run())


def test_offline_requested_demo_and_geographical_comparisons():
    async def run():
        model = OfflineModel()
        decision = await model.plan("Compare revenue and spend per customer by state in January versus February 2025", [], {})
        assert len(decision.plan.queries) == 2
        assert decision.plan.queries[0].metrics == ["spend_per_customer", "revenue"]
        assert decision.plan.queries[1].start_date == date(2025, 2, 1)
        followup = await model.plan("Now compare by product", [], {}, previous_plan=decision.plan)
        assert all(query.dimensions == ["product"] for query in followup.plan.queries)
        states = await model.plan("Compare revenue in California versus Texas in 2024", [], {})
        assert states.plan.queries[0].dimensions == ["state"]
        assert states.plan.queries[0].states == ["California", "Texas"]
    asyncio.run(run())


def test_decision_contract_does_not_allow_non_analysis_to_carry_a_plan():
    with pytest.raises(ValidationError):
        Decision.model_validate_json(VALID_PLAN.replace('"analysis"', '"refuse"'))


@pytest.mark.parametrize("phrase,start,end", [
    ("last month", date(2026, 9, 1), date(2026, 10, 1)),
    ("this month", date(2026, 10, 1), date(2026, 10, 6)),
    ("year to date", date(2026, 1, 1), date(2026, 10, 6)),
    ("mois dernier", date(2026, 9, 1), date(2026, 10, 1)),
])
def test_offline_relative_periods_use_injected_utc_current_date(phrase, start, end):
    model = OfflineModel()
    decision = asyncio.run(model.plan(f"Revenue by product {phrase}", [], {"current_date": "2026-10-05"}))
    assert decision.plan.queries[0].start_date == start
    assert decision.plan.queries[0].end_date == end


def test_relative_month_year_boundary_and_no_implicit_timeframe():
    async def run():
        model = OfflineModel()
        prior = await model.plan("Revenue last month", [], {"current_date": "2026-01-01"})
        assert prior.plan.queries[0].start_date == date(2025, 12, 1)
        assert prior.plan.queries[0].end_date == date(2026, 1, 1)
        assert (await model.plan("Revenue by product", [], {"current_date": "2026-10-05"})).action == "clarify"
        assert (await model.plan("Revenue this month", [], {}, previous_plan=prior.plan)).action == "clarify"
    asyncio.run(run())


def test_up_to_date_requires_a_start_and_does_not_assume_all_time():
    async def run():
        model = OfflineModel()
        context = {"current_date": "2026-10-05"}
        assert (await model.plan("Up-to-date revenue by product", [], context)).action == "clarify"
        assert (await model.plan("Up-to-date revenue as of 2026-10-05", [], context)).action == "clarify"
        explicit = await model.plan("Up-to-date revenue from 2025-01-01", [], context)
        assert explicit.plan.queries[0].start_date == date(2025, 1, 1)
        assert explicit.plan.queries[0].end_date == date(2026, 10, 6)
        prior = await model.plan("Revenue in 2025", [], context)
        followup = await model.plan("Bring it up to date by product", [], context, previous_plan=prior.plan)
        assert followup.plan.queries[0].start_date == date(2025, 1, 1)
        assert followup.plan.queries[0].end_date == date(2026, 10, 6)
        all_time = await model.plan("Revenue all time", [], context)
        assert (await model.plan("Bring it up to date", [], context, previous_plan=all_time.plan)).action == "clarify"
    asyncio.run(run())


def test_deterministic_report_labels_supplied_periods_and_never_invents_them():
    report = deterministic_report([
        {"evidence_id": "E1", "period": {"start": "2025-01-01", "end_exclusive": "2025-02-01"}, "rows": [{"revenue": 100}]},
        {"evidence_id": "E2", "period": {"start": None, "end_exclusive": None}, "rows": [{"revenue": 200}]},
        {"evidence_id": "E3", "rows": [{"revenue": 300}]},
    ])
    assert report.findings[0] == "[E1] Period: 2025-01-01 (inclusive) to 2025-02-01 (exclusive). revenue=100"
    assert report.findings[1] == "[E2] Period: all time. revenue=200"
    assert report.findings[2] == "[E3] revenue=300"
