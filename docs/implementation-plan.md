# Proposed implementation plan

**Status: planning only. The prototype is not built, live services are unverified, and no deployment is claimed.**

Build a local Google ADK 2 + Gemini CLI using BigQuery, SQLite and Rich. Implement safety, report confirmation, resilience and observability. Keep Golden retrieval, preference learning, persona administration and production infrastructure design-only. See [architecture.md](architecture.md).

## Estimated sequence

Allow **6–12 hours of focused implementation**, assuming working credentials and a known schema. Provisioning and deployment are excluded; this is an estimate, not a guarantee.

| Elapsed time | Work and acceptance |
| --- | --- |
| 0–1 h | Package, settings, contracts and fixtures; verify ADK 2.11.0 workflow/session compatibility, clean installation, explicit missing-setting errors, no tracked secrets/runtime data |
| 1–3 h | Typed plan, metric catalog, compiler and gateway; parameterized values, approved joins, product scope, dry run and byte limits |
| 3–5 h | Gemini, ADK workflows and Rich; end-to-end analysis and follow-up, validated model output, clear limitations |
| 5–6.5 h | Owned reports and confirmation; exact preview, actor-bound expiry, transactional deletion and cancellation |
| 6.5–8 h | Correction, dependency failures and events; bounded attempts/deadlines and identifiable failure stages |
| 8–10 h | Numerical and adversarial evaluation; fixture answers and authorization/PII/confirmation checks |
| 10–12 h | Opt-in live smoke test, actual usage instructions and demo; reproducible implemented behavior, honest validation status |

For six hours, reduce metric breadth and presentation polish. Preserve safety and confirmation checks.

## Build priorities

Define validated actor, plan, query, aggregate-result, report and pending-deletion contracts. Keep model/data adapters replaceable for offline testing. Configure Gemini model, BigQuery project/location, limits and SQLite path without committing credentials.

Compile analytical operations dynamically from trusted metric definitions. Test mixed-product orders and composed comparisons before polishing chat output. No unrestricted SQL fallback. Structured model output helps shape validation but cannot enforce permissions. [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output), [BigQuery parameters](https://docs.cloud.google.com/bigquery/docs/parameterized-queries).

Use explicit ADK workflow stages and custom tools that enforce application policy on every invocation. Preserve sanitized follow-up context; resolve current entitlements on every request. Bound correction and transient attempts under one deadline. Pin `google-adk==2.11.0` and lock its compatible dependencies after the installation smoke test. [ADK graph workflows](https://adk.dev/graphs/), [released package](https://pypi.org/project/google-adk/2.11.0/).

Build report ownership before deletion selection. Freeze IDs/versions at preview, then validate actor, expiry and state at commit in the independent SQLite report service. Keep approval separate from ADK session persistence. Log correlation metadata, not raw records. Enforce query-byte limits as well as result limits. [BigQuery cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs).

## Acceptance checks

- Known-answer aggregates, date/status handling and ratios match fixtures without join double-counting.
- A multi-step cohort comparison and follow-up use executed evidence rather than fixed prompt matching.
- Two actors see different products; mixed orders, session reuse and entitlement changes preserve isolation.
- Identifier requests and injected policy changes cannot expose PII or change access. Customer-ranking pseudonyms require an explicit privacy decision.
- Deletion handles cancel, expiry, wrong actor, replay, changed targets and reports created after preview.
- Syntax errors, empty results and provider failures stop or correct safely within budgets; permission failures never broaden scope.
- Traces link messages to failed stages/jobs without publishing prompts, credentials or source rows.
- Reports state scope/period/definitions and distinguish measured contributions from causal hypotheses.

Numerical/security checks are deterministic; human review assesses intent and UX. Live validation is separate from fixture tests and must be reported accurately.

## Demonstration and next milestones

Demonstrate analysis, comparison/follow-up, PII refusal, save/delete/cancel and a controlled failure. Use sanitized examples and demo identities. Keep local databases, traces and generated reports outside version control.

Later milestones add verified identity, restricted analytics policies, production storage/workers, Golden retrieval, reviewed learning and persona publishing. Each needs evaluation and rollback. The GCP proposal remains a candidate until residency, scale, retention and recovery requirements are agreed.
