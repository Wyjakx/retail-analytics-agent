import asyncio
from dataclasses import replace
import json
import os
import subprocess
import sys

import pytest

from retail_agent.adk_workflow import ModelFailure
from retail_agent.analytics import AnalysisPlan, QuerySpec
from retail_agent.config import ConfigurationError, Settings, load_pseudonym_key, resolve_scope
from retail_agent.gateways import OfflineGateway, QueryOutcome
from retail_agent.model import Decision, OfflineModel
from retail_agent.reports import ReportStore
from retail_agent.service import AnalyticsService, Conversation
from retail_agent.telemetry import TraceRecorder


@pytest.fixture
def service(tmp_path):
    with ReportStore(tmp_path / "reports.sqlite3") as reports:
        yield AnalyticsService(
            Settings(data_dir=tmp_path), Conversation("analyst_north"), OfflineModel(),
            OfflineGateway(), reports, TraceRecorder(tmp_path / "events.jsonl"), resolve_scope,
        )


def ask(service, question):
    return asyncio.run(service.handle(question))


def test_composed_comparison_followup_and_human_deletion(service):
    first = ask(service, "Compare revenue and spend per customer by state in January versus February 2025")
    assert first.report and len(first.evidence) == 2
    assert first.evidence[0]["rows"] == [
        {"state": "California", "spend_per_customer": 20.0, "revenue": 120.0},
        {"state": "Texas", "spend_per_customer": 30.0, "revenue": 180.0},
    ]
    second = ask(service, "Now compare by product")
    assert second.report and all(query.dimensions == ["product"] for query in second.plan.queries)
    assert [(q.start_date, q.end_date) for q in second.plan.queries] == [
        (q.start_date, q.end_date) for q in first.plan.queries
    ]
    assert "Saved report" in ask(service, "/save Q1 test").message
    preview = ask(service, "Delete all the reports we made in this conversation")
    assert preview.pending and len(preview.pending.targets) == 1
    assert "plain yes cannot" in ask(service, "yes").message
    assert len(service.reports.list_reports("analyst_north")) == 1
    assert "does not match" in ask(service, "/confirm incorrect").message
    assert len(service.reports.list_reports("analyst_north")) == 1
    deleted = ask(service, f"/confirm {preview.pending.token}")
    assert "Deleted 1" in deleted.message
    assert not service.reports.list_reports("analyst_north")
    assert ask(service, f"/confirm {preview.pending.token}").message == deleted.message


def test_no_date_is_clarified_schema_is_available_and_unrelated_is_refused(service):
    assert "date range" in ask(service, "Show monthly revenue").message
    assert ask(service, "What tables are available?").catalog["products"] == ["id", "name", "category"]
    assert ask(service, "Write a romantic poem").report is None


def test_result_cap_uses_rows_before_small_group_suppression(service):
    class LimitedModel(OfflineModel):
        async def plan(self, *args, **kwargs):
            return Decision(action="analysis", plan=AnalysisPlan(queries=[
                QuerySpec(metrics=["revenue"], dimensions=["state"], limit=2),
            ]))

    class LimitedGateway(OfflineGateway):
        def execute(self, spec, scope, budget):
            return QueryOutcome([
                {"state": "TX", "revenue": 100.0, "group_customer_count": 5},
                {"state": "CA", "revenue": 200.0, "group_customer_count": 1},
            ], ["state", "revenue", "group_customer_count"], "ev-limit", {}, True)

    service.model, service.gateway = LimitedModel(), LimitedGateway()
    result = ask(service, "Show revenue by state all time")
    assert result.report
    assert result.evidence[0]["result_limit"] == 2
    assert result.evidence[0]["limit_reached"] is True
    assert result.evidence[0]["rows"] == [{"state": "TX", "revenue": 100.0}]


class EmptyGateway(OfflineGateway):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def execute(self, spec, scope, budget):
        self.calls += 1
        result = super().execute(spec, scope, budget)
        return QueryOutcome([], result.columns, result.evidence_id, result.statistics, True)


def test_empty_query_has_only_one_equivalent_correction(service):
    service.gateway = EmptyGateway()
    result = ask(service, "Show revenue in 2025")
    assert result.report and "No eligible" in result.report.summary
    assert service.gateway.calls == 2
    events = [json.loads(line) for line in service.traces.path.read_text().splitlines()]
    assert len([event for event in events if event["stage"] == "correction" and event["status"] == "started"]) == 1


class WideningRepair(OfflineModel):
    async def plan(self, question, sanitized_history, safe_catalog, previous_plan=None, repair_error=None):
        if repair_error:
            return Decision(action="analysis", plan=AnalysisPlan(queries=[QuerySpec(metrics=["revenue"])]))
        return await super().plan(question, sanitized_history, safe_catalog, previous_plan, repair_error)


def test_empty_correction_cannot_silently_expand_dates(service):
    service.gateway = EmptyGateway()
    service.model = WideningRepair()
    result = ask(service, "Show revenue in 2025")
    assert service.gateway.calls == 1
    assert result.plan.queries[0].start_date.year == 2025


def test_query_budget_refuses_entire_oversized_plan_before_spending(service):
    service.settings = replace(service.settings, max_queries=1)
    service.gateway = EmptyGateway()
    result = ask(service, "Compare revenue in January versus February 2025")
    assert "limit" in result.message
    assert service.gateway.calls == 0


class UnavailableModel(OfflineModel):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def plan(self, *args, **kwargs):
        self.calls += 1
        raise ModelFailure("model_access_denied")


def test_access_failure_does_not_enter_another_model_retry_cycle(service):
    service.model = UnavailableModel()
    assert "unavailable" in ask(service, "Show revenue in 2025").message
    assert service.model.calls == 1
    service.model = OfflineModel()
    assert ask(service, "Show revenue in 2025").report


class SlowModel(OfflineModel):
    async def plan(self, *args, **kwargs):
        await asyncio.sleep(0.1)
        return await super().plan(*args, **kwargs)


def test_request_deadline_records_failure_without_crashing_interface(service):
    service.settings = replace(service.settings, turn_timeout=0.01)
    service.model = SlowModel()
    assert "timed out" in ask(service, "Show revenue in 2025").message
    events = [json.loads(line) for line in service.traces.path.read_text().splitlines()]
    assert events[-1]["stage"] == "request" and events[-1]["status"] == "failed"


def test_plain_literal_mention_does_not_treat_wildcards_as_sql(service):
    ask(service, "Show revenue in 2025")
    ask(service, "/save Budget 100%_done")
    ask(service, "/save Different report")
    result = ask(service, "Delete all reports mentioning 100%_done")
    assert [target.title for target in result.pending.targets] == ["Budget 100%_done"]
    assert "cancelled" in ask(service, "/cancel").message
    assert len(service.reports.list_reports("analyst_north")) == 2


def test_scope_configuration_reloads_file_and_rejects_implicit_access(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"demo": [1, 2]}')
    assert resolve_scope("demo", policy).allowed_product_ids == (1, 2)
    policy.write_text('{"demo": [3]}')
    assert resolve_scope("demo", policy).allowed_product_ids == (3,)
    with pytest.raises(ConfigurationError):
        resolve_scope("unknown", policy)
    policy.write_text('{"demo": [true]}')
    with pytest.raises(ConfigurationError):
        resolve_scope("demo", policy)


def test_env_budgets_cannot_disable_required_privacy_threshold(monkeypatch):
    monkeypatch.setenv("MIN_GROUP_CUSTOMERS", "1")
    with pytest.raises(ConfigurationError):
        Settings.load(env_file=None)


def test_local_customer_key_persists_and_keeps_modes_separate(tmp_path):
    settings = Settings(data_dir=tmp_path / "offline")
    key = load_pseudonym_key(settings)
    assert len(key) == 32
    assert load_pseudonym_key(settings) == key
    assert load_pseudonym_key(replace(settings, data_dir=tmp_path / "live")) != key
    (settings.data_dir / "customer-pseudonym.key").write_bytes(b"damaged")
    with pytest.raises(ConfigurationError):
        load_pseudonym_key(settings)


def test_customer_key_environment_is_validated_without_secret_disclosure(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOMER_PSEUDONYM_KEY", "ab" * 32)
    settings = Settings.load(env_file=None)
    assert load_pseudonym_key(replace(settings, data_dir=tmp_path)) == bytes.fromhex("ab" * 32)
    assert settings.customer_pseudonym_key not in repr(settings)
    assert not (tmp_path / "customer-pseudonym.key").exists()
    monkeypatch.setenv("CUSTOMER_PSEUDONYM_KEY", "PRIVATE_INVALID_SECRET")
    with pytest.raises(ConfigurationError) as exc:
        Settings.load(env_file=None)
    assert "PRIVATE_INVALID_SECRET" not in str(exc.value)


def test_cli_demo_runs_in_a_fresh_directory_with_unicode_output(tmp_path):
    environment = {**os.environ, "APP_DATA_DIR": str(tmp_path / "state")}
    result = subprocess.run(
        [sys.executable, "-m", "retail_agent", "--demo", "--show-plan", "--env-file", str(tmp_path / "absent.env")],
        env=environment, capture_output=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "OFFLINE: synthetic data" in result.stdout
    assert "spend_per_customer" in result.stdout
    assert "Approved customer results" in result.stdout
    assert "cust_" in result.stdout
    assert "Break down cust_" in result.stdout
    assert "A plain yes cannot delete reports" in result.stdout
    assert "Deleted 1 selected report(s)" in result.stdout
    assert "Traceback" not in result.stderr
    assert (tmp_path / "state/demo/events.jsonl").is_file()


def test_live_setup_error_is_explicit_without_api_calls(tmp_path):
    environment = dict(os.environ)
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "GOOGLE_CLOUD_PROJECT"):
        environment.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-m", "retail_agent", "--live", "--question", "Show revenue in 2025", "--env-file", str(tmp_path / "absent.env")],
        env=environment, capture_output=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 2
    assert "Set GOOGLE_API_KEY locally" in result.stdout
    assert "Traceback" not in result.stderr


class LaterQueryFailure(OfflineGateway):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def execute(self, spec, scope, budget):
        from google.api_core.exceptions import BadRequest

        self.calls += 1
        if self.calls == 2:
            budget.begin_query()
            raise BadRequest("private diagnostic text")
        return super().execute(spec, scope, budget)


def test_partial_comparison_is_not_presented_as_a_completed_report(service):
    service.gateway = LaterQueryFailure()
    result = ask(service, "Compare revenue in January versus February 2025")
    assert "incomplete" in result.message
    assert result.report is None and service.context.last_report is None
    assert len(result.evidence) == 1
    assert "Run an analysis" in ask(service, "/save failed comparison").message


def test_trace_disk_failure_does_not_reverse_a_committed_deletion(service):
    service.traces = TraceRecorder(service.settings.data_dir)  # A directory cannot be opened as a file.
    assert ask(service, "Show revenue in 2025").report
    assert "Saved report" in ask(service, "/save trace outage").message
    preview = ask(service, "/delete conversation")
    deleted = ask(service, f"/confirm {preview.pending.token}")
    assert "Deleted 1" in deleted.message
    assert not service.reports.list_reports("analyst_north")
    assert service.traces.dropped_events > 0


def test_trace_allowlist_programming_errors_are_not_silenced(service):
    with pytest.raises(ValueError):
        service.traces.event(raw_prompt="must never be accepted")


def test_report_preview_renders_literal_titles_without_markup_or_truncation(service):
    from io import StringIO
    from rich.console import Console
    from retail_agent.cli import present

    title = ("[bold]literal[/bold] " + "Long title " * 8).rstrip()
    ask(service, "Show revenue in 2025")
    ask(service, f"/save {title}")
    preview = ask(service, "/delete conversation")
    assert preview.pending and preview.pending.targets[0].title == title
    output = StringIO()
    present(Console(file=output, width=80), preview)
    assert "[bold]literal[/bold]" in output.getvalue()
    assert "…" not in output.getvalue()
