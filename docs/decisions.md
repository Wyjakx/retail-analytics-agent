# Initial decisions

Status: agreed framework and implemented local prototype. Offline behavior and the installed ADK runtime are tested; actual Gemini/BigQuery calls and production deployment remain unverified.

## Google ADK 2 with Gemini

Google ADK 2 with Gemini is the selected stack. Its graph workflows make analysis, validation, execution, repair and reporting explicit, combining Python functions and model-driven nodes. This fits the assignment's Gemini and BigQuery services. Custom tools keep authorization, SQL compilation and report operations in application code.

As of 5 October 2026, the installed stable Python package is `google-adk==2.11.0`, published on 2 October and requiring Python 3.10 or later. The application itself requires Python 3.11 for its deadline API and was tested on Python 3.12.14. Dependency versions are frozen in `requirements-lock.txt`. ADK typed-output workflows use ephemeral sessions; actual provider access remains to be verified.

The author's prior work includes Python backends, Google ADK agents, Gemini and GCP. This prototype exercises ADK 2 typed-output workflows locally; the configured live model and BigQuery path still need validation. The framework choice fits that background and the assignment's Google services.

## A constrained analysis plan

The model emits a typed analysis plan; Python validates it and compiles SQL from approved metrics, dimensions, relationships and filters. Product entitlements and PII restrictions are supplied by trusted application state. Values are query parameters, not interpolated SQL strings.

This decision favors a demonstrable security boundary within a small prototype. Its cost is analytical breadth: a request outside the grammar needs clarification or a transparent unsupported response. The catalog composes metrics, grouping, dates and filters for product/region comparisons, monthly metrics, structure questions and multi-query follow-ups. Synthetic calculations and fake-client tests exercise these operations; live BigQuery validation remains outstanding.

Unrestricted model-generated SQL is an alternative for broader analysis. It would require stronger restricted-data boundaries and validation; a SELECT check or an AST parser alone is not sufficient. Whether the constrained approach satisfies the evaluator's expected breadth is an open assumption to make explicit.

## SQLite locally, managed storage in production

SQLite keeps the local saved-report and confirmation demonstration reproducible. A repository interface allows a production relational database without changing orchestration. Sanitized conversational context is in memory and supplied explicitly to ephemeral ADK stages. Report ownership and confirmation outcomes persist independently; long-lived conversation recovery is a production extension.

The deletion service owns preview, expiry, confirmation and atomic execution. ADK 2.11.0 adds workflow tool confirmation, but the confirmation documentation still marks the feature experimental and lists session-service limitations. The prototype uses explicit CLI confirmation backed by application records. A SQLite memory service does not establish persistent-session confirmation compatibility.

## Credentials and model version

Gemini credentials and BigQuery application-default credentials are local setup concerns, not repository contents. Dependencies are pinned; `GEMINI_MODEL` is required explicitly in live mode so the operator chooses an available model. The chosen model and account access still need a live check. A configurable model avoids embedding a changing provider alias into business logic.

## Business assumptions to resolve

- Demo user-to-product entitlements will be explicitly seeded unless a mapping is supplied. They demonstrate access rules, not login security.
- Revenue calculations need documented order status, refund/cancellation handling, currency and time zone semantics.
- Customer inactivity can be measured from transaction history. It must not be labeled subscription churn without an agreed definition.
- Explanations of performance differences describe observed contributing factors; they do not establish causation from transactions alone.
- Customer-level analytics require a privacy-preserving output contract. Names, emails, addresses and raw personal identifiers are excluded.
- The golden corpus is theoretical and its workflow is design-only for the prototype.
- Query byte budgets, overall turn budgets and retry limits need separate enforcement. A row LIMIT alone does not cap scanned bytes.

## Sources

- [Google ADK graph workflows](https://adk.dev/graphs/)
- [Google ADK 2.11.0 package](https://pypi.org/project/google-adk/2.11.0/)
- [Google ADK 2.11.0 release notes](https://github.com/google/adk-python/releases/tag/v2.11.0)
- [ADK confirmation support and limitations](https://adk.dev/tools-custom/confirmation/)
- [BigQuery parameterized queries](https://docs.cloud.google.com/bigquery/docs/parameterized-queries)
- [BigQuery cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs)
