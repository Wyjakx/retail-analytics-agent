# Proposed architecture

**Status: local prototype implemented and offline-tested. Live integrations remain unverified; the production HLD is a design proposal and no deployment is claimed.**

The assistant translates retail questions into bounded analyses and explains computed results. Gemini proposes plans and wording; Python enforces permissions, privacy, budgets and report operations. The stack is Google ADK 2, Gemini, BigQuery, SQLite and Rich. ADK provides explicit graph workflows combining Python functions and model-driven steps, fitting the assignment's Google services. The installed baseline is `google-adk==2.11.0`; typed-output workflows were exercised with a fake ADK model, while real service calls remain unverified. [ADK graph workflows](https://adk.dev/graphs/), [released package](https://pypi.org/project/google-adk/2.11.0/).

## Prototype scope

The local prototype implements analysis, owned-report confirmation, bounded failures and correlated metadata in a CLI. Numerical and adversarial tests check these behaviors. Golden retrieval, preference learning and persona administration remain HLD-only, alongside production authentication, hosting and future tools. Session context supports follow-ups without claiming preference learning.

The live gateway queries configured tables in `bigquery-public-data.thelook_ecommerce`, validating required schema fields and location before execution. Actual joins, types and usable dates still need live verification. SQLite holds report ownership and pending confirmations; sanitized conversation state remains in application memory. Each ADK model stage uses an ephemeral session with approved context. Rich handles presentation. Application policy controls every operation.

```mermaid
flowchart LR
    U["Demo user"] --> CLI["Rich CLI"]
    CLI --> APP["Application: actor and policy"]
    APP --> P["ADK planning graph: Gemini then validation"]
    P --> APP
    APP --> Q["Validated plan and SQL compiler"]
    Q --> BQ["Restricted BigQuery gateway"]
    BQ --> A["Approved aggregates"]
    A --> APP
    APP --> S["ADK reporting graph: Gemini then validation"]
    S --> APP
    APP --> R["Reports and confirmation"]
    R --> DB[("SQLite")]
    APP --> T["Redacted events"]
    R --> T
```

## Analysis workflow

Route requests to analysis, clarification, report actions or refusal. Gemini produces a typed `AnalysisPlan`: metrics, dimensions, dates, filters and dependent operations. Python validates it against the catalog, actor entitlements and budget, then dynamically compiles parameterized SQL. Table identifiers, expressions and joins come from trusted code. [BigQuery parameters](https://docs.cloud.google.com/bigquery/docs/parameterized-queries).

The operation catalog should compose aggregates, comparisons and contribution breakdowns. For a spending comparison between states, compute scoped revenue, purchasing-customer count, order frequency, basket value and product mix before explanation.

This restricts expressiveness compared with unrestricted model SQL, but makes calculations and authorization auditable. Unsupported operations require clarification or a limitation; there is no raw-SQL fallback. **Acceptance requires a genuine multi-step comparison and follow-up, not fixed prompt-to-query mappings.** Expand the catalog if it cannot express the agreed evaluation questions.

Use a configurable Gemini model through ADK with typed planning/report contracts. The Python application rechecks actor scope, validates every planned operation and enforces budgets before calling the gateway. No SQL, database or deletion tools are exposed to the model. Each ADK graph runs a model node followed by Python output validation. Correction and transient attempts have explicit limits; graph routing alone does not impose a retry or cost limit. [ADK graph workflows](https://adk.dev/graphs/), [graph routes](https://adk.dev/graphs/routes/).

## Safety and PII

Resolve product entitlements from trusted storage, never prompts. Apply scope before aggregation and preserve it through every join. Mixed-product order totals must not include unauthorized items. Scope report searches, sessions and future retrieval/cache entries too.

Return approved aggregates, coarse dimensions and explicitly permitted pseudonymous customer statistics. Names, emails, addresses, exact locations and raw customer identifiers must not reach model context or output. Internal IDs support distinct counts and customer grouping only inside the gateway. Intake redaction, minimum-group suppression for aggregate segments and output validation add protection; repeated-query inference needs further production controls.

The client explicitly approved stable pseudonymous customer rankings and predefined demo permissions. The prototype ranks within permitted product items before limiting rows, replaces internal customer IDs with actor-scoped HMAC labels in Python, and keeps linkage in gateway memory. Follow-up plans contain only previously supplied references; Python resolves them and adds parameterized internal-ID filters. A private local key preserves labels across rerun rankings. New conversations or restarts require a fresh ranking before reference resolution. Pseudonymous individual statistics are exempt from segment-size suppression under the agreed policy. Pseudonymization does not make customer outputs anonymous; production needs key management and controls over longitudinal linkage.

A selectable local actor demonstrates authorization rules, not authentication. Shared BigQuery credentials identify the backend principal, not the executive. Production needs restricted analytics views/columns and a reviewed identity-to-policy design. [BigQuery row security](https://docs.cloud.google.com/bigquery/docs/row-level-security-intro).

## Report deletion boundary

Store owner, conversation, content, evidence references and version. Support owned-report selection by literal mention or current conversation. Resolve ambiguity before preview.

Preview exact titles/count and freeze IDs/versions in an expiring pending operation. Require a distinct confirmation identifier through the CLI, outside model control. Recheck actor, ownership, versions, expiry and unused state transactionally; delete only those targets and consume confirmation atomically. Changed targets require a new preview. New reports created after preview survive. Cancel, expiry and no-match leave reports unchanged; replay returns the recorded outcome. Audit metadata must not retain deleted content.

Keep this approval transaction in an application service independent of ADK session state. ADK 2.11.0 adds workflow tool confirmation, while its confirmation documentation still describes experimental support and session-service limitations. Native confirmation can be evaluated later without changing the report-ownership boundary. [ADK 2.11.0 release notes](https://github.com/google/adk-python/releases/tag/v2.11.0), [confirmation documentation](https://adk.dev/tools-custom/confirmation/).

## Grounding and definitions

Define statuses, returns, currency, dates and denominators in the metric catalog. Spend per customer must state whether it covers purchasing customers within permitted products. Inactivity-based churn is a proposed proxy requiring a cohort, threshold and sufficient observation window.

Report observed contributions separately from hypotheses. Transactional associations do not establish causation. Empty results never justify silently changing dates or permissions. Show explicit periods, scope, evidence and limitations.

## Candidate production HLD

GCP is proposed because the source is BigQuery and the model family is Gemini. It is not deployed. Provider interfaces preserve alternatives.

```mermaid
flowchart TB
    U["Executive client"] --> API["OIDC-authenticated API on Cloud Run"]
    API --> APP["Policy and ADK workflow worker"]
    APP --> M["Approved managed Gemini endpoint"]
    APP --> Q["Restricted analytics gateway"]
    Q --> BQ["Controlled BigQuery dataset"]
    APP --> DB[("Cloud SQL: ownership, entitlements, state")]
    APP --> OBJ[("Cloud Storage: reports")]
    APP --> RET["Scoped Golden retrieval"]
    RET --> IDX[("PostgreSQL text/vector index")]
    RET --> GOLD[("Cloud Storage: vetted trios")]
    ING["Ingestion and analyst review"] --> GOLD
    ING --> IDX
    ADMIN["Role-restricted configuration admin"] --> DB
    APP --> JOB["Cloud Tasks and Cloud Run workers"]
    APP --> OBS["OpenTelemetry, Logging, Monitoring"]
```

Cloud SQL provides transactional ownership and configuration; Cloud Storage holds artifacts. Scoped service identities and Secret Manager govern credentials. Verify the managed model endpoint, residency, retention, backup/restore and availability requirements before deployment.

Cloud Tasks would dispatch rendering and cleanup work to authenticated Cloud Run HTTP workers. Each worker uses a persisted operation ID to make repeated delivery safe and records the final outcome before acknowledging success. This is part of the HLD, not the CLI implementation. [Cloud Tasks HTTP targets](https://docs.cloud.google.com/tasks/docs/creating-http-target-tasks).

### Hybrid intelligence

Version trios with question, SQL, report, schema/metric versions, scope and reviewer provenance. Ingestion removes sensitive details and publishes reviewed records. At query time, filter by permissions and compatibility, then combine text/vector relevance. Supply relevant methodology with source IDs, treating retrieved text as evidence rather than instructions. Revalidate historical SQL through current policy. Analyst-approved corrections update the index and trigger regression evaluation.

### Continuous improvement and persona management

Store per-user presentation preferences separately from policy; explicit choices override inference and users can reset them. System feedback enters a redacted review queue. Changes to knowledge, metrics or prompts require evaluation, approval and rollback.

A nondeveloper admin interface publishes versioned tone/format settings without redeployment. Preview changes against fixed examples; pin the version per request. Security rules and tool permissions remain outside editable persona text.

### Resilience and cost

The prototype caps each turn at three query attempts, one correction cycle, six model calls including retries, and a request deadline. A model stage permits up to two transient retries under its deadline. Dry-run queries, enforce `maximum_bytes_billed` plus a cumulative turn budget, and track actual statistics; `LIMIT` is not a general scan-cost control. A canceled budget blocks new query submissions and requests best-effort job cancellation. [BigQuery cost guidance](https://docs.cloud.google.com/bigquery/docs/best-practices-costs).

Invalid plans, rejected queries and empty results enter at most one correction cycle. Query corrections must preserve the validated filters, period and metrics. Privacy-suppressed results do not trigger attempts to widen the query. Compiler defects require code repair and incompatible schemas stop safely. Permission/budget failures stop safely. Uncertain submissions reconcile by an explicit job ID and request cancellation without resubmitting. Report synthesis can fall back to approved aggregate facts. Deletion succeeds only after commit. Production adds circuit breakers and background workers.

### Observability and quality assurance

Link message, request, workflow stage, query/job and report/operation IDs. Track latency, outcomes, repairs, refusals, tokens, bytes and dependency failures. Default logs exclude raw prompts, source rows and report bodies. Restricted diagnostics retain sanitized plan/query provenance for reproduction.

Use known-answer fixtures for arithmetic and authorization, adversarial cases for privacy/confirmation, and human review for intent and clarity. Gate release on passing supported analytical cases and no known safety failures in the suite. UX sessions assess comprehension, clarification and deletion recovery. Establish performance targets after measurement; a passing suite is not universal safety proof.

Future tools declare permissions, typed contracts, budgets and side-effect policy. Charts consume validated aggregates; email requires recipient/report checks; web evidence remains untrusted.
