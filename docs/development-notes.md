# Development notes

The prototype answers retail analysis questions through a Python application. Gemini proposes a plan and report wording; Python decides what can be queried, computes the results and controls saved reports. The local CLI flow is implemented and offline-tested. These notes explain the choices reflected in the code and the remaining live validation.

## ADK model stages and Python controls

`AdkTypedRunner` uses an ADK 2 workflow for each planning or reporting stage: a model node produces structured output, followed by Python validation. The stage has a fresh in-memory session and receives approved context from the application. Model calls, retries, output size and time are bounded. The model has no SQL, database or deletion tools.

This keeps the orchestration close to Gemini while placing permissions and side effects in ordinary Python services that can be tested without a model. Conversational context comes from the application's sanitized history and previous plan. It does not implement learned user preferences. The ADK graph can be tested with a local fake model; actual Gemini access still needs a live run.

## An analysis plan becomes SQL

The chosen contract is `AnalysisPlan`, containing up to three `QuerySpec` operations. Each operation combines approved metrics, dimensions, date bounds and filters. The current catalog supports revenue, orders, purchasing customers, units, average order value and spend per customer, grouped by month, state, country, category or product.

Python validates the plan, resolves product permissions and compiles SQL against four fixed join paths. Table names, column expressions and joins come from code. Filter values become query parameters. Unsupported operations need clarification or an explicit catalog extension; the application has no raw model-SQL fallback.

This is a deliberate restriction on expressiveness. It makes a requested calculation and its product scope reviewable. Queries remain dynamic because the plan composes metrics, grouping and filters rather than selecting canned questions.

The present revenue definition includes nonnegative item prices for Complete or Shipped items and orders. An order containing both permitted and unauthorized products contributes only its permitted items. Average order value and spend per purchasing customer therefore describe the permitted product scope. They do not represent complete baskets or all customer spending. Dates use UTC and an exclusive end bound. Confirm these business definitions with the client before treating them as production rules.

## Reports require an independent confirmation

`ReportStore` uses SQLite for report ownership, versions and pending deletion operations. A preview returns exact report titles, IDs and versions, plus an expiring token for the CLI. The model must never receive that token.

Confirmation checks the actor, token hash, expiry and every frozen target inside one transaction. Any changed or missing target stops the whole deletion. Deletion and confirmation consumption commit together. New reports created after preview survive, and a replay returns the recorded outcome. Audit rows retain IDs and outcomes without deleted titles, bodies or evidence.

This service is independent of ADK sessions so a model response cannot authorize destruction. The CLI supports `/save TITLE`, `/reports`, `/delete conversation`, `/delete mention TEXT`, `/confirm TOKEN` and `/cancel`; `/explain` exposes the latest analytical plan and approved evidence. Report service tests verify ownership, expiry, cancellation, revision conflicts, restart, replay, concurrent confirmations and transaction rollback. A change in product permissions cancels pending previews and clears analytical context. Known confirmation tokens pasted into ordinary questions are redacted before model input.

## What the offline mode establishes

The offline gateway evaluates the same approved query specification against synthetic relational fixtures. The offline planner/reporter simulates supported interactions without calling Gemini. The default CLI mode and scripted `--demo` make calculations, follow-ups, privacy suppression and report operations inspectable without cloud setup. The parser supports declared metric/grouping keywords, common explicit periods, UTC-relative periods and selected state/country labels; it is not a general language model.

This mode does not execute the generated SQL in BigQuery or assess Gemini's ability to interpret an unfamiliar question. Live mode separately uses the ADK model stages and BigQuery gateway. That gateway checks required schema fields, performs a dry run, enforces per-query and cumulative byte limits, and restricts result columns. Explicit job IDs allow bounded reconciliation/cancellation without blind resubmission. Query submissions, timeouts and failures still require cloud validation. See [evaluation](evaluation.md) for the checks and the [architecture](architecture.md) for the production design.

Invalid model plans can be corrected once. Empty or rejected queries permit one equivalent-plan retry, within the shared query budget; this rebuilds the same trusted SQL and is not general SQL repair. A compiler defect requires a code change. Partial comparisons are labeled incomplete and do not produce a new report. Failed model synthesis falls back to approved aggregate facts, with scope, period and definitions appended by the application. Trace-file I/O failures emit a diagnostic warning without changing committed report outcomes.

## Provisional rules and assignment scope

The supplied dataset contains no client-approved mapping from executives to products. Demo actors and configurable product IDs demonstrate authorization while that mapping is clarified. A selectable actor is not authentication. Production needs a trusted identity-to-entitlement source and restricted analytics views or columns.

Individual customer rankings remain disabled pending the client's privacy decision. Aggregate segments, spend per purchasing customer and customer counts are available within the permitted product scope. Minimum-group suppression and input/output checks add protection, but repeated-query inference and semantic report accuracy need further controls and evaluation.

Golden Question → SQL → Analyst Report retrieval, preference learning, system learning and nondeveloper persona updates are design deliverables for this assignment. Their proposed storage, review and update flows are documented in the architecture; the prototype does not implement them. Charts, email and web tools are also future extensions. The prototype work concentrates on the required CLI, safety, strict report confirmation, bounded error handling and redacted observability.
