# Initial decisions

Status: agreed framework and implemented local prototype. Offline behavior, the installed ADK runtime and one live Gemini/BigQuery CLI analysis are verified. Broader live scenarios remain to be validated; production infrastructure is a design proposal.

## Google ADK 2 with Gemini

Google ADK 2 with Gemini is the selected stack. Its graph workflows make analysis, validation, execution, repair and reporting explicit, combining Python functions and model-driven nodes. This fits the assignment's Gemini and BigQuery services. Custom tools keep authorization, SQL compilation and report operations in application code.

As of 5 October 2026, the installed stable Python package is `google-adk==2.11.0`, published on 2 October and requiring Python 3.10 or later. The application itself requires Python 3.11 for its deadline API. The complete suite and live CLI were verified on Python 3.12.10 on Windows. Dependency versions are frozen in `requirements-lock.txt`. ADK typed-output workflows use ephemeral sessions; real Gemini planning and reporting passed the recorded smoke test.

The author's prior work includes Python backends, Google ADK agents, Gemini and GCP. This prototype exercises ADK 2 typed-output workflows locally and through the configured live model. The framework choice fits that background and the assignment's Google services. The verified BigQuery path and remaining evaluation work are recorded in [evaluation](evaluation.md).

## A constrained analysis plan

The model emits a typed analysis plan; Python validates it and compiles SQL from approved metrics, dimensions, relationships and filters. Product entitlements and PII restrictions are supplied by trusted application state. Values are query parameters, not interpolated SQL strings.

This decision favors a demonstrable security boundary within a small prototype. Its cost is analytical breadth: a request outside the grammar needs clarification or a transparent unsupported response. The catalog composes metrics, grouping, dates and filters for product/region comparisons, monthly metrics, structure questions and multi-query follow-ups. Synthetic calculations and fake-client tests exercise these operations. A real six-metric product analysis matched independent BigQuery SQL; broader live coverage remains outstanding.

Unrestricted model-generated SQL is an alternative for broader analysis. It would require stronger restricted-data boundaries and validation; a SELECT check or an AST parser alone is not sufficient. Whether the constrained approach satisfies the evaluator's expected breadth is an open assumption to make explicit.

## SQLite locally, managed storage in production

SQLite keeps the local saved-report and confirmation demonstration reproducible. A repository interface allows a production relational database without changing orchestration. Sanitized conversational context is in memory and supplied explicitly to ephemeral ADK stages. Report ownership and confirmation outcomes persist independently; long-lived conversation recovery is a production extension.

The deletion service owns preview, expiry, confirmation and atomic execution. ADK 2.11.0 adds workflow tool confirmation, but the confirmation documentation still marks the feature experimental and lists session-service limitations. The prototype uses explicit CLI confirmation backed by application records. A SQLite memory service does not establish persistent-session confirmation compatibility.

## Credentials and model version

Gemini credentials and BigQuery application-default credentials are local setup concerns, not repository contents. Dependencies are pinned; `GEMINI_MODEL` is required explicitly in live mode so the operator chooses an available model. The recorded live CLI smoke test used `gemini-3.5-flash-lite` and application-default credentials. Each evaluator must configure their own available model and account access. A configurable model avoids embedding a changing provider alias into business logic.

## Client clarification

The response received on 6 October 2026 confirms two prototype choices:

- The client accepted predefined demo users with a config file or access table. The prototype's explicit actor-to-product mapping is sufficient; production scopes would arrive through a verified frontend JWT.
- The client accepted stable pseudonymous customer labels for individual rankings and multi-turn follow-ups. Python generates actor-scoped HMAC references and keeps raw linkage private. Names, emails, addresses and raw personal identifiers are excluded from model input and outputs. Aggregate segments remain supported.

## Remaining business assumptions

- Revenue calculations need documented order status, refund/cancellation handling, currency and time zone semantics.
- Customer inactivity can be measured from transaction history. It must not be labeled subscription churn without an agreed definition.
- Explanations of performance differences describe observed contributing factors; they do not establish causation from transactions alone.
- The golden corpus is theoretical and its workflow is design-only for the prototype.
- Query byte budgets, overall turn budgets and retry limits need separate enforcement. A row LIMIT alone does not cap scanned bytes.

## Sources

- [Google ADK graph workflows](https://adk.dev/graphs/)
- [Google ADK 2.11.0 package](https://pypi.org/project/google-adk/2.11.0/)
- [Google ADK 2.11.0 release notes](https://github.com/google/adk-python/releases/tag/v2.11.0)
- [ADK confirmation support and limitations](https://adk.dev/tools-custom/confirmation/)
- [BigQuery parameterized queries](https://docs.cloud.google.com/bigquery/docs/parameterized-queries)
- [BigQuery cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs)
