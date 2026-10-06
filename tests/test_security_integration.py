"""Application security checks with synthetic data and inspectable local models."""

import asyncio
from copy import deepcopy
import json
import sqlite3

import pytest

from retail_agent.analytics import ActorScope, AnalysisPlan, QuerySpec, SQLCompiler
from retail_agent.config import Settings
from retail_agent.gateways import OfflineGateway, QueryOutcome
from retail_agent.model import AnalystReport, Decision, OfflineModel
from retail_agent.reports import ReportStore
from retail_agent.safety import UnsafeOutput, validate_report
from retail_agent.service import AnalyticsService, Conversation
from retail_agent.telemetry import TraceRecorder


class RecordingModel(OfflineModel):
    def __init__(self, decision=None, bad_report=None):
        super().__init__()
        self.inputs = []
        self.decision = decision
        self.bad_report = bad_report
        self.on_plan = None
        self.on_report = None

    async def plan(self, question, sanitized_history, safe_catalog, previous_plan=None, repair_error=None):
        self.inputs.append({
            "stage": "plan", "question": question,
            "history": deepcopy(sanitized_history), "catalog": deepcopy(safe_catalog),
            "previous_plan": previous_plan.model_dump(mode="json") if previous_plan else None,
            "repair_error": repair_error,
        })
        if self.on_plan:
            self.on_plan()
        if self.decision:
            return self.decision
        return await super().plan(question, sanitized_history, safe_catalog, previous_plan, repair_error)

    async def report(self, question, evidence, metric_definitions):
        self.inputs.append({
            "stage": "report", "question": question,
            "evidence": deepcopy(evidence), "definitions": deepcopy(metric_definitions),
        })
        if self.on_report:
            self.on_report()
        if self.bad_report:
            return self.bad_report
        return await super().report(question, evidence, metric_definitions)


class RecordingGateway(OfflineGateway):
    def __init__(self):
        super().__init__()
        self.executions = []
        self.sql = []

    def execute(self, spec, scope, budget):
        self.executions.append((spec.model_dump(mode="json"), scope))
        self.sql.append(SQLCompiler().compile(spec, scope).sql)
        return super().execute(spec, scope, budget)


def fixed_decision(*products):
    return Decision(action="analysis", plan=AnalysisPlan(queries=[
        QuerySpec(metrics=["revenue"], product_ids=[product]) for product in products
    ]))


@pytest.fixture
def app_factory(tmp_path):
    stores = []

    def make(model=None, gateway=None, max_queries=3):
        directory = tmp_path / str(len(stores))
        store = ReportStore(directory / "reports.sqlite3")
        stores.append(store)
        policy = {"products": (1, 2)}
        model = model or RecordingModel()
        gateway = gateway or RecordingGateway()
        app = AnalyticsService(
            Settings(data_dir=directory, max_queries=max_queries),
            Conversation("alice"), model, gateway, store,
            TraceRecorder(directory / "events.jsonl"),
            lambda actor: ActorScope(actor, policy["products"]),
        )
        return app, model, gateway, policy

    yield make
    for store in stores:
        store.close()


def ask(app, question):
    return asyncio.run(app.handle(question))


@pytest.mark.parametrize("question", [
    "Show customer emails for 2025",
    "List first_name and last_name for 2025",
    "Give customer_id and user_id for 2025",
    "Show street_address and postal_code for 2025",
    "List latitude and longitude for 2025",
    "Show customer phone numbers in 2025",
    "Show ip_address in 2025",
    "Show customers' names in 2025",
    "Show purchases for customer 739123 in 2025",
])
def test_personal_identifiers_are_refused_before_model(app_factory, question):
    app, model, gateway, _ = app_factory()
    result = ask(app, question)
    assert result.report is None
    assert model.inputs == []
    assert gateway.executions == []
    assert "personal" in result.message.lower() or "ranking" in result.message.lower()


def test_pseudonymous_customer_ranking_and_followup_never_send_source_identity_to_model(app_factory):
    from retail_agent.gateways import seeded_data
    from retail_agent.pseudonyms import CustomerPseudonymizer

    data = seeded_data()
    raw_id = 739123
    private_name = "PRIVATE_CUSTOMER_NAME_MARKER"
    private_email = "private-customer@retail.invalid"
    private_address = "PRIVATE_ADDRESS_MARKER"
    data["users"][0].update(id=raw_id, first_name=private_name, email=private_email, street_address=private_address)
    for table in ("orders", "order_items"):
        for row in data[table]:
            if row["user_id"] == 1:
                row["user_id"] = raw_id
    key = b"private-customer-key-for-tests!!!"
    gateway = OfflineGateway(data, pseudonymizer=CustomerPseudonymizer(key))
    app, model, _, _ = app_factory(gateway=gateway)
    ranking = ask(app, "Top 12 customers by spending in 2025")
    assert ranking.report and len(ranking.evidence[0]["rows"]) == 12
    rows = ranking.evidence[0]["rows"]
    assert [row["revenue"] for row in rows] == [90.0] * 6 + [60.0] * 6
    reference = rows[0]["customer"]
    followup = ask(app, f"Break down {reference} by month")
    assert followup.report
    assert followup.evidence[0]["rows"] == [
        {"month": "2025-01", "revenue": 30.0},
        {"month": "2025-02", "revenue": 60.0},
    ]
    assert followup.plan.queries[0].customer_refs == [reference]
    assert followup.plan.queries[0].start_date == ranking.plan.queries[0].start_date
    ask(app, "/save pseudonymous followup")
    exposed = json.dumps({"model": model.inputs, "explain": ask(app, "/explain").evidence,
                          "saved": [report.evidence for report in app.reports.list_reports("alice")]})
    for private in (str(raw_id), private_name, private_email, private_address, key.decode()):
        assert private not in exposed
        assert private not in app.traces.path.read_text(encoding="utf-8")


def test_unknown_customer_reference_is_rejected_before_query_submission(app_factory):
    reference = "cust_" + "a" * 32
    decision = Decision(action="analysis", plan=AnalysisPlan(queries=[
        QuerySpec(metrics=["revenue"], customer_refs=[reference]),
    ]))
    app, _, gateway, _ = app_factory(model=RecordingModel(decision=decision))
    result = ask(app, "Customer spending all time")
    assert result.report is None
    assert "previous ranking" in result.message
    assert gateway.executions == []


def test_customer_reference_copied_to_other_actor_is_not_queryable(app_factory):
    first, _, _, _ = app_factory(gateway=OfflineGateway())
    ranking = ask(first, "Top 3 customers by spending in 2025")
    reference = ranking.evidence[0]["rows"][0]["customer"]
    other, _, gateway, _ = app_factory()
    other.context.actor_id = "bob"
    result = ask(other, f"Spending for {reference} in 2025")
    assert result.report is None
    assert gateway.executions == []


def test_permission_change_revokes_customer_followup_context(app_factory):
    app, _, _, policy = app_factory(gateway=OfflineGateway())
    ranking = ask(app, "Top 3 customers by spending in 2025")
    reference = ranking.evidence[0]["rows"][0]["customer"]
    assert reference in app.context.customer_refs
    policy["products"] = (3,)
    result = ask(app, f"Spending for {reference} in 2025")
    assert result.report is None
    assert app.context.customer_refs == set()
    assert app.context.previous_plan is None


def test_fabricated_customer_reference_in_report_uses_grounded_fallback(app_factory):
    reference = "cust_" + "f" * 32
    model = RecordingModel(bad_report=AnalystReport(title="Invented customer", summary=f"Review {reference}."))
    app, _, _, _ = app_factory(model=model, gateway=OfflineGateway())
    result = ask(app, "Top 3 customers by spending in 2025")
    assert result.report.title == "Retail analysis"
    assert reference not in result.report.to_markdown()


@pytest.mark.parametrize("summary", [
    "user_id=2. This customer placed 2 orders.",
    "Customer ID 2 placed 2 orders.",
    "Client #2 placed 2 orders.",
    "L'identifiant du client est 2.",
])
def test_raw_identity_claims_fail_even_when_number_matches_valid_metric(app_factory, summary):
    model = RecordingModel(bad_report=AnalystReport(title="Unsafe identity claim", summary=summary))
    app, _, _, _ = app_factory(model=model, gateway=OfflineGateway())
    result = ask(app, "Top 3 customers by orders in 2025")
    assert result.report and result.report.title == "Retail analysis"
    assert summary not in result.report.to_markdown()
    assert all(row["orders"] == 2 for row in result.evidence[0]["rows"])


def test_contact_values_are_redacted_before_question_and_history_reach_model(app_factory):
    app, model, _, _ = app_factory(model=RecordingModel(decision=fixed_decision(1)))
    contact = "private-marker@retail.invalid"
    phone = "+33 6 12 34 56 78"
    first = ask(app, f"Revenue all time; contact {contact} {phone}")
    assert first.report is not None
    followup = ask(app, "Revenue all time")
    assert followup.report is not None
    payloads = json.dumps(model.inputs)
    history = json.dumps(app.context.history)
    for raw in (contact, phone):
        assert raw not in payloads
        assert raw not in history
    assert "[REDACTED_EMAIL]" in payloads
    assert "[REDACTED_PHONE]" in payloads


@pytest.fixture(params=[
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "CUSTOMER_PSEUDONYM_KEY", "unconfigured_google_key",
])
def synthetic_secret(request, monkeypatch):
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "CUSTOMER_PSEUDONYM_KEY"):
        monkeypatch.delenv(name, raising=False)
    if request.param == "unconfigured_google_key":
        # Construct an unmistakably synthetic value; test the final hyphen boundary.
        return "AI" + "za" + "aB_" * 11 + "c-"
    value = "b7" * 32 if request.param == "CUSTOMER_PSEUDONYM_KEY" else (
        "local-private-" + "987654321012" + "-marker"
    )
    monkeypatch.setenv(request.param, value)
    return value


def assert_secret_absent_from_app(app, model, secret):
    with sqlite3.connect(app.reports.path) as connection:
        saved_state = "\n".join(connection.iterdump())
    for content in (
        json.dumps(model.inputs), json.dumps(app.context.history), saved_state,
        app.traces.path.read_text(encoding="utf-8"),
    ):
        assert secret not in content


def test_pasted_secret_is_redacted_before_model_history_and_saved_state(
    app_factory, synthetic_secret,
):
    app, model, _, _ = app_factory(model=RecordingModel(decision=fixed_decision(1)))
    baseline = ask(app, "Revenue all time")
    result = ask(app, f"Revenue all time; credential ({synthetic_secret})")
    followup = ask(app, "Revenue all time")
    assert baseline.report and result.report and followup.report
    assert result.evidence == baseline.evidence == followup.evidence
    assert "[REDACTED_SECRET]" in json.dumps(model.inputs)
    assert "[REDACTED_SECRET]" in json.dumps(app.context.history)
    assert synthetic_secret not in result.report.to_markdown()
    assert "Saved report" in ask(app, "/save Revenue summary").message
    assert_secret_absent_from_app(app, model, synthetic_secret)


def test_secret_in_model_report_uses_safe_fallback_before_display_and_save(
    app_factory, synthetic_secret,
):
    model = RecordingModel(bad_report=AnalystReport(
        title="Unverified report", summary=f"Credential ({synthetic_secret}).",
    ))
    app, _, _, _ = app_factory(model=model)
    result = ask(app, "Revenue in 2025")
    assert result.report and result.report.title == "Retail analysis"
    assert synthetic_secret not in result.report.to_markdown()
    assert "revenue=900" in result.report.to_markdown()
    assert "Saved report" in ask(app, "/save Safe fallback").message
    events = [json.loads(line) for line in app.traces.path.read_text().splitlines()]
    assert any(event["stage"] == "report_fallback" for event in events)
    assert_secret_absent_from_app(app, model, synthetic_secret)


def test_secret_in_save_title_is_refused_without_persistence_or_model_call(
    app_factory, synthetic_secret,
):
    app, model, _, _ = app_factory()
    assert ask(app, "Revenue in 2025").report
    calls = len(model.inputs)
    result = ask(app, f"/save Summary ({synthetic_secret})")
    assert "could not be completed safely" in result.message
    assert synthetic_secret not in result.message
    assert app.reports.list_reports("alice") == []
    assert len(model.inputs) == calls
    assert_secret_absent_from_app(app, model, synthetic_secret)


def test_secret_in_evidence_is_refused_before_reporting_model(
    app_factory, synthetic_secret,
):
    class CredentialGateway(RecordingGateway):
        def execute(self, spec, scope, budget):
            outcome = super().execute(spec, scope, budget)
            rows = [{**row, "product": synthetic_secret} for row in outcome.rows]
            return QueryOutcome(rows, outcome.columns, outcome.evidence_id, outcome.statistics, True)

    decision = Decision(action="analysis", plan=AnalysisPlan(queries=[
        QuerySpec(metrics=["revenue"], dimensions=["product"]),
    ]))
    app, model, _, _ = app_factory(
        model=RecordingModel(decision=decision), gateway=CredentialGateway(),
    )
    result = ask(app, "Revenue by product all time")
    assert result.report is None and result.evidence == []
    assert not any(item["stage"] == "report" for item in model.inputs)
    assert synthetic_secret not in result.message
    assert app.context.history == []
    assert_secret_absent_from_app(app, model, synthetic_secret)


def test_report_commands_and_confirmation_tokens_never_reach_model(app_factory):
    app, model, _, _ = app_factory()
    assert ask(app, "Revenue in 2025").report is not None
    ask(app, "/save Summary")
    call_count = len(model.inputs)
    preview = ask(app, "/delete conversation")
    token = preview.pending.token
    assert ask(app, "yes").pending is None
    ask(app, f"/confirm {token}")
    assert len(model.inputs) == call_count
    assert token not in json.dumps(model.inputs)
    assert token not in json.dumps(app.context.history)


def test_pasted_pending_or_consumed_token_is_redacted_from_ordinary_analysis(app_factory):
    app, model, _, _ = app_factory()
    ask(app, "Revenue in 2025")
    ask(app, "/save Summary")
    pending = ask(app, "/delete conversation").pending
    ask(app, f"Revenue in 2025, confirmation secret {pending.token}")
    ask(app, "/cancel")
    ask(app, f"Revenue in 2025, old secret {pending.token}")
    assert pending.token not in json.dumps(model.inputs)
    assert pending.token not in json.dumps(app.context.history)


def test_revoked_scope_discards_previous_plan_history_report_and_evidence(app_factory):
    app, model, _, policy = app_factory()
    assert ask(app, "Revenue for product 1 in 2025").report is not None
    assert app.context.history
    policy["products"] = (3,)
    ask(app, "/explain")
    assert app.context.history == []
    assert app.context.previous_plan is None
    assert app.context.last_report is None
    assert app.context.last_evidence == []
    assert app.context.last_products == ()
    ask(app, "Revenue all time")
    planning = [item for item in model.inputs if item["stage"] == "plan"][-1]
    assert planning["history"] == []
    assert planning["previous_plan"] is None
    assert planning["catalog"]["allowed_product_ids"] == [3]


def test_one_unauthorized_step_prevents_spending_on_an_authorized_first_step(app_factory):
    app, model, gateway, _ = app_factory(model=RecordingModel(decision=fixed_decision(1, 3)))
    result = ask(app, "Compare products all time")
    assert result.report is None
    assert "permissions" in result.message
    assert gateway.executions == []
    assert len([item for item in model.inputs if item["stage"] == "plan"]) == 1
    assert not any(item["stage"] == "report" for item in model.inputs)


def test_plan_larger_than_configured_query_budget_is_rejected_before_spending(app_factory):
    app, _, gateway, _ = app_factory(
        model=RecordingModel(decision=fixed_decision(1, 2)), max_queries=1,
    )
    result = ask(app, "Compare permitted products all time")
    assert result.report is None
    assert gateway.executions == []
    assert "limit" in result.message


def test_scope_change_during_planning_prevents_first_query(app_factory):
    model = RecordingModel(decision=fixed_decision(1))
    app, _, gateway, policy = app_factory(model=model)
    model.on_plan = lambda: policy.update(products=(3,))
    result = ask(app, "Revenue all time")
    assert result.report is None
    assert "permissions" in result.message
    assert gateway.executions == []
    assert app.context.previous_plan is None


def test_scope_change_during_reporting_prevents_display_and_save(app_factory):
    model = RecordingModel(decision=fixed_decision(1))
    app, _, _, policy = app_factory(model=model)
    model.on_report = lambda: policy.update(products=(3,))
    result = ask(app, "Revenue all time")
    assert result.report is None
    assert result.evidence == []
    assert app.context.last_report is None
    assert app.context.last_evidence == []
    assert app.context.history == []
    ask(app, "/save Should not exist")
    assert app.reports.list_reports("alice") == []


def test_saved_report_visibility_uses_current_entitlements_and_owner(app_factory):
    app, model, _, policy = app_factory()
    mine_one = app.reports.save("alice", "old", "Product one", "Approved body", {"product_ids": [1]})
    mine_three = app.reports.save("alice", "old", "Product three", "Approved body", {"product_ids": [3]})
    app.reports.save("alice", "old", "Mixed scope", "Hidden", {"product_ids": [1, 3]})
    app.reports.save("alice", "old", "Unknown provenance", "Hidden", None)
    app.reports.save("bob", "old", "Other actor", "Hidden", {"product_ids": [1]})
    assert [item["id"] for item in ask(app, "/reports").reports] == [mine_one.report_id]
    assert ask(app, f"/delete id {mine_three.report_id}").pending is None
    policy["products"] = (3,)
    assert [item["id"] for item in ask(app, "/reports").reports] == [mine_three.report_id]
    assert ask(app, f"/delete id {mine_one.report_id}").pending is None
    assert model.inputs == []


def test_scope_change_invalidates_pending_deletion_of_now_inaccessible_report(app_factory):
    app, _, _, policy = app_factory()
    report = app.reports.save("alice", app.context.conversation_id, "Product one", "Body", {"product_ids": [1]})
    pending = ask(app, "/delete conversation").pending
    policy["products"] = (3,)
    result = ask(app, f"/confirm {pending.token}")
    assert app.context.pending is None
    assert "deleted" not in result.message.casefold()
    assert app.reports.list_reports("alice")[0].report_id == report.report_id
    assert app.reports.confirm_delete("alice", pending.operation_id, pending.token).status == "cancelled"


@pytest.mark.parametrize("summary", [
    "Contact private-output@retail.invalid for the results.",
    "Revenue is 9898.73 according to the analysis.",
])
def test_unsafe_or_unsupported_report_uses_only_approved_evidence_fallback(app_factory, summary):
    model = RecordingModel(bad_report=AnalystReport(title="Unverified report", summary=summary))
    app, _, _, _ = app_factory(model=model)
    result = ask(app, "Revenue in 2025")
    assert result.report is not None
    assert result.report.title == "Retail analysis"
    assert summary not in result.report.to_markdown()
    assert "revenue=900" in result.report.to_markdown()
    assert "generated summary could not be verified" in result.message
    events = [json.loads(line) for line in app.traces.path.read_text(encoding="utf-8").splitlines()]
    assert any(event["stage"] == "report_fallback" for event in events)
    assert app.context.last_report == result.report


def test_verified_digit_leading_citation_keeps_model_report_and_normal_message(app_factory):
    class DigitLeadingGateway(RecordingGateway):
        def execute(self, spec, scope, budget):
            outcome = super().execute(spec, scope, budget)
            return QueryOutcome(
                outcome.rows, outcome.columns, "bq-7655e3b16480f29b",
                outcome.statistics, outcome.simulated, outcome.suppressed_groups,
            )

    class CitationModel(RecordingModel):
        async def report(self, question, evidence, metric_definitions):
            await super().report(question, evidence, metric_definitions)
            item = evidence[0]
            return AnalystReport(
                title="Verified revenue report", summary="Approved aggregate results.",
                findings=[f"[{item['evidence_id']}] Revenue was {item['rows'][0]['revenue']}."],
            )

    app, _, _, _ = app_factory(model=CitationModel(), gateway=DigitLeadingGateway())
    result = ask(app, "Revenue in 2025")
    assert result.report.title == "Verified revenue report"
    assert result.message == "Analysis completed. Use /save TITLE to keep this report."
    events = [json.loads(line) for line in app.traces.path.read_text(encoding="utf-8").splitlines()]
    assert not any(event["stage"] == "report_fallback" for event in events)


DIGIT_LEADING_EVIDENCE_ID = "bq-7655e3b16480f29b"


@pytest.fixture
def approved_analysis():
    evidence = [{
        "evidence_id": DIGIT_LEADING_EVIDENCE_ID,
        "rows": [{
            "product": "1: Seven7 Women's Long Sleeve Stripe Belted Top",
            "revenue": 147.0, "orders": 3, "purchasing_customers": 3,
            "units": 3, "average_order_value": 49.0, "spend_per_customer": 49.0,
        }],
    }]
    plan = AnalysisPlan(queries=[QuerySpec(
        metrics=["revenue", "orders", "purchasing_customers", "units",
                 "average_order_value", "spend_per_customer"],
        dimensions=["product"], product_ids=[1, 2], limit=10,
    )])
    return evidence, plan


def test_approved_digit_leading_evidence_id_is_an_opaque_citation(approved_analysis):
    evidence, plan = approved_analysis
    report = AnalystReport(
        title="Product performance",
        summary=f"Approved results [{DIGIT_LEADING_EVIDENCE_ID}].",
        findings=[f"[{DIGIT_LEADING_EVIDENCE_ID}] Product 1 revenue was 147.0, "
                  "from 3 orders and 3 purchasing customers. Average order value "
                  "and spending per customer were 49.0."],
    )
    validate_report(report, evidence, plan)


def test_citation_digits_do_not_authorize_a_business_claim(approved_analysis):
    evidence, plan = approved_analysis
    report = AnalystReport(
        title="Invented amount",
        summary=f"[{DIGIT_LEADING_EVIDENCE_ID}] Revenue was 7655.",
    )
    with pytest.raises(UnsafeOutput, match="unverified numerical"):
        validate_report(report, evidence, plan)


@pytest.mark.parametrize("citation", [
    "bq-9898e3b16480f29b",
    DIGIT_LEADING_EVIDENCE_ID + "0",
    DIGIT_LEADING_EVIDENCE_ID + "-fake",
    "prefix-" + DIGIT_LEADING_EVIDENCE_ID,
])
def test_unknown_or_extended_citation_retains_numerical_validation(
    approved_analysis, citation,
):
    evidence, plan = approved_analysis
    report = AnalystReport(
        title="Unverified citation", summary=f"[{citation}] Revenue was 147.0.",
    )
    with pytest.raises(UnsafeOutput, match="unverified numerical"):
        validate_report(report, evidence, plan)


def test_forbidden_gateway_identifier_is_rejected_before_report_model(app_factory):
    class IdentifierGateway(RecordingGateway):
        def execute(self, spec, scope, budget):
            outcome = super().execute(spec, scope, budget)
            rows = [{**row, "email": "raw-source@retail.invalid", "user_id": 123} for row in outcome.rows]
            return QueryOutcome(rows, outcome.columns, outcome.evidence_id, outcome.statistics, True)

    app, model, _, _ = app_factory(gateway=IdentifierGateway())
    result = ask(app, "Revenue in 2025")
    assert result.report is None
    assert not any(item["stage"] == "report" for item in model.inputs)
    assert "raw-source@retail.invalid" not in json.dumps(model.inputs)
    assert "raw-source@retail.invalid" not in result.message
    assert app.context.history == []


def test_raw_prompt_sql_confirmation_and_report_content_are_absent_from_traces(app_factory):
    app, _, gateway, _ = app_factory()
    prompt_marker = "RAW_PROMPT_PRIVATE_MARKER"
    title_marker = "REPORT_TITLE_PRIVATE_MARKER"
    result = ask(app, f"Revenue in 2025 {prompt_marker}")
    assert result.report is not None
    report_body = result.report.to_markdown()
    ask(app, f"/save {title_marker}")
    pending = ask(app, "/delete conversation").pending
    ask(app, f"/confirm {pending.token}")
    raw_trace = app.traces.path.read_text(encoding="utf-8")
    for private in (prompt_marker, title_marker, report_body, pending.token, *gateway.sql):
        assert private not in raw_trace
    events = [json.loads(line) for line in raw_trace.splitlines()]
    assert all(event["actor_id"] == "alice" for event in events)
    assert all(event["conversation_id"] == app.context.conversation_id for event in events)
    assert all(event.get("request_id") for event in events)
    assert any(event.get("operation_id") == pending.operation_id for event in events)
    assert any(event.get("report_id") for event in events)
    assert any(event.get("evidence_id") for event in events)


@pytest.mark.parametrize("dependency", ["model", "gateway"])
def test_private_dependency_exception_body_is_absent_from_output_and_trace(app_factory, dependency):
    private_error = "PROVIDER_ERROR_PRIVATE_MARKER SELECT private_source FROM private_table"

    class FailedModel(RecordingModel):
        async def plan(self, *args, **kwargs):
            raise RuntimeError(private_error)

    class FailedGateway(RecordingGateway):
        def execute(self, *args, **kwargs):
            raise RuntimeError(private_error)

    app, _, _, _ = app_factory(
        model=FailedModel() if dependency == "model" else RecordingModel(),
        gateway=FailedGateway() if dependency == "gateway" else RecordingGateway(),
    )
    result = ask(app, "Revenue in 2025")
    assert result.report is None
    assert private_error not in result.message
    assert private_error not in app.traces.path.read_text(encoding="utf-8")
    assert app.context.history == []
