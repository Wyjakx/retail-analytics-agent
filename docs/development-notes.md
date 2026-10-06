# Development notes

The prototype answers retail analysis questions through a Python application. Gemini proposes a plan and report wording; Python decides what can be queried, computes the results and controls saved reports. The local CLI flow is implemented and offline-tested. These notes explain the choices reflected in the code and the remaining live validation.

## ADK model stages and Python controls

`AdkTypedRunner` uses an ADK 2 workflow for each planning or reporting stage: a model node produces structured output, followed by Python validation. The stage has a fresh in-memory session and receives approved context from the application. Model calls, retries, output size and time are bounded. The model has no SQL, database or deletion tools.

This keeps the orchestration close to Gemini while placing permissions and side effects in ordinary Python services that can be tested without a model. Conversational context comes from the application's sanitized history and previous plan. It does not implement learned user preferences. The ADK graph is tested with a local fake model and has completed real Gemini planning/reporting calls; see the bounded live results in [evaluation](evaluation.md).

The provider receives a JSON Schema copy with array length constraints removed to avoid a Gemini request rejection seen in the live test. The original Pydantic contract remains strict inside ADK and Python, so an oversized or empty array is rejected before it can drive an analysis. Unknown fields, approved enums, customer-reference patterns and numeric limits remain in the provider schema. Google's [structured-output limitations](https://ai.google.dev/gemini-api/docs/generate-content/structured-output#limitations) describe possible rejection of complex schemas; provider acceptance does not replace application validation.

## An analysis plan becomes SQL

The chosen contract is `AnalysisPlan`, containing up to three `QuerySpec` operations. Each operation combines approved metrics, dimensions, date bounds and filters. The current catalog supports revenue, orders, purchasing customers, units, average order value and spend per customer, grouped by month, state, country, category, product or pseudonymous customer. A selected metric can order the results before the row limit is applied.

Python validates the plan, resolves product permissions and compiles SQL against four fixed join paths. Table names, column expressions and joins come from code. Filter values become query parameters. Unsupported operations need clarification or an explicit catalog extension; the application has no raw model-SQL fallback.

This is a deliberate restriction on expressiveness. It makes a requested calculation and its product scope reviewable. Queries remain dynamic because the plan composes metrics, grouping and filters rather than selecting canned questions.

The present revenue definition includes nonnegative item prices for Complete or Shipped items and orders. An order containing both permitted and unauthorized products contributes only its permitted items. Average order value and spend per purchasing customer therefore describe the permitted product scope. They do not represent complete baskets or all customer spending. Dates use UTC and an exclusive end bound. Confirm these business definitions with the client before treating them as production rules.

## Credentials and accidental disclosure

API keys are loaded from the process environment or the ignored local `.env`; they are not embedded in source, prompts or reports. Google application-default credentials stay in the user's local Google configuration. SDK authentication sends credentials to the intended Google service, separately from the model's conversational input. Provider body logging is disabled, and application traces use a metadata allowlist.

Questions are scrubbed for the configured `GOOGLE_API_KEY`, `GEMINI_API_KEY` and optional `CUSTOMER_PSEUDONYM_KEY` before the model or conversation history receives them. Standard Google API-key patterns are also masked when the key is not configured locally. Output validation refuses these secrets in evidence, reports and saved titles; an invalid model report falls back to approved facts. The checks deliberately use synthetic secrets in tests and do not read ADC files.

The 6 October 2026 audit found no configured API key or Google credential value in the checked Git history, tracked files or runtime outputs. Sensitive local files are excluded from Git. The application does not encrypt `.env`, ADC files or SQLite itself, and the redaction rules do not cover every possible credential format. These are prototype boundaries, not a guarantee of zero disclosure risk.

## Reports require an independent confirmation

`ReportStore` uses SQLite for report ownership, versions and pending deletion operations. A preview returns exact report titles, IDs and versions, plus an expiring token for the CLI. The model must never receive that token.

Confirmation checks the actor, token hash, expiry and every frozen target inside one transaction. Any changed or missing target stops the whole deletion. Deletion and confirmation consumption commit together. New reports created after preview survive, and a replay returns the recorded outcome. Audit rows retain IDs and outcomes without deleted titles, bodies or evidence.

This service is independent of ADK sessions so a model response cannot authorize destruction. The CLI supports `/save TITLE`, `/reports`, `/delete conversation`, `/delete mention TEXT`, `/confirm TOKEN` and `/cancel`; `/explain` exposes the latest analytical plan and approved evidence. Report service tests verify ownership, expiry, cancellation, revision conflicts, restart, replay, concurrent confirmations and transaction rollback. A change in product permissions cancels pending previews and clears analytical context. Known confirmation tokens pasted into ordinary questions are redacted before model input.

## What the offline mode establishes

The offline gateway evaluates the same approved query specification against synthetic relational fixtures. The offline planner/reporter simulates supported interactions without calling Gemini. The default CLI mode and scripted `--demo` make calculations, follow-ups, privacy suppression and report operations inspectable without cloud setup. The parser supports declared metric/grouping keywords, common explicit periods, UTC-relative periods and selected state/country labels; it is not a general language model.

This mode does not execute the generated SQL in BigQuery or assess Gemini's ability to interpret an unfamiliar question. Live mode separately uses the ADK model stages and BigQuery gateway. That gateway checks required schema fields, performs a dry run, enforces per-query and cumulative byte limits, and restricts result columns. Explicit job IDs allow bounded reconciliation/cancellation without blind resubmission. Query submissions, timeouts and failures still require cloud validation. See [evaluation](evaluation.md) for the checks and the [architecture](architecture.md) for the production design.

Invalid model plans can be corrected once. Empty or rejected queries permit one equivalent-plan retry, within the shared query budget; this rebuilds the same trusted SQL and is not general SQL repair. A compiler defect requires a code change. Partial comparisons are labeled incomplete and do not produce a new report. Failed model synthesis falls back to approved aggregate facts, with scope, period and definitions appended by the application. Trace-file I/O failures emit a diagnostic warning without changing committed report outcomes.

## Provisional rules and assignment scope

The client confirmed that predefined demo users with a config or access table are sufficient. The prototype uses configurable actor-to-product IDs, with a small example file. A selectable actor is not authentication. Production scopes arrive through a verified frontend JWT and are resolved to product IDs by trusted application policy.

The client also approved stable pseudonymous individual customers for rankings and follow-up questions, while excluding names, emails and addresses from both model context and output. The gateway groups using internal IDs, replaces those IDs with actor-scoped HMAC labels in Python, and returns only approved result fields. A secret key prevents dictionary lookup of predictable numeric IDs; including the actor avoids sharing the same label between demo users. Product permissions are applied before aggregation and ranking.

The gateway keeps label-to-ID linkage only in process memory. The application admits follow-up references only after a successful ranking in the current conversation. Unknown or cross-actor labels cannot fall back to an unfiltered query. A permission change clears conversation references; the new query still applies current product permissions. The local key persists separately from source control, so rerunning a ranking recreates the same labels. Restart and `/new` require a new ranking to establish usable context. Production would store the key in Secret Manager and use a restricted linkage service if long-lived follow-ups are required.

Customer-level queries are deliberately exempt from the minimum-customer threshold: suppressing every single-customer row would defeat the explicitly approved capability. The minimum still applies to ordinary aggregate segments. Pseudonymization is not anonymization; repeated-query inference and semantic report accuracy require further production controls and evaluation.

Golden Question → SQL → Analyst Report retrieval, preference learning, system learning and nondeveloper persona updates are design deliverables for this assignment. Their proposed storage, review and update flows are documented in the architecture; the prototype does not implement them. Charts, email and web tools are also future extensions. The prototype work concentrates on the required CLI, safety, strict report confirmation, bounded error handling and redacted observability.
