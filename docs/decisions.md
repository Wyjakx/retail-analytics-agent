# Initial decisions

Status: agreed framework, proposed implementation details. No runtime has been built yet.

## LangGraph with Gemini

LangGraph with Gemini is the selected stack. Explicit nodes make analysis, validation, execution, repair and reporting easy to inspect. LangGraph can combine deterministic and model-driven steps without requiring the full LangChain stack. A small Google GenAI SDK adapter keeps provider configuration separate from business policy.

The implementation explanation will describe the author's actual prior experience with the framework. No tenure, production scale or benchmark is asserted by this repository.

## A constrained analysis plan

The model emits a typed analysis plan; Python validates it and compiles SQL from approved metrics, dimensions, relationships and filters. Product entitlements and PII restrictions are supplied by trusted application state. Values are query parameters, not interpolated SQL strings.

This decision favors a demonstrable security boundary within a small prototype. Its cost is analytical breadth: a request outside the grammar needs clarification or a transparent unsupported response. The grammar must still support product and region comparisons, monthly metrics, database structure questions and multi-step analysis. The implementation will validate these against live BigQuery rather than calling static canned reports an agent.

Unrestricted model-generated SQL is an alternative for broader analysis. It would require stronger restricted-data boundaries and validation; a SELECT check or an AST parser alone is not sufficient. Whether the constrained approach satisfies the evaluator's expected breadth is an open assumption to make explicit.

## SQLite locally, managed storage in production

SQLite keeps the local saved-report and confirmation demonstration reproducible. A repository interface allows a production relational database without changing orchestration. Add a separate graph checkpoint store only if recovery or pause/resume needs justify it.

## Credentials and model version

Gemini credentials and BigQuery application-default credentials are local setup concerns, not repository contents. The exact supported Gemini model and dependency versions will be verified and pinned during implementation. A configurable model is preferable to embedding a changing provider alias into business logic.

## Business assumptions to resolve

- Demo user-to-product entitlements will be explicitly seeded unless a mapping is supplied. They demonstrate access rules, not login security.
- Revenue calculations need documented order status, refund/cancellation handling, currency and time zone semantics.
- Customer inactivity can be measured from transaction history. It must not be labeled subscription churn without an agreed definition.
- Explanations of performance differences describe observed contributing factors; they do not establish causation from transactions alone.
- Customer-level analytics require a privacy-preserving output contract. Names, emails, addresses and raw personal identifiers are excluded.
- The golden corpus is theoretical and its workflow is design-only for the prototype.
- Query byte budgets, overall turn budgets and retry limits need separate enforcement. A row LIMIT alone does not cap scanned bytes.

## Sources

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Google GenAI SDK](https://ai.google.dev/gemini-api/docs/libraries)
- [BigQuery parameterized queries](https://docs.cloud.google.com/bigquery/docs/parameterized-queries)
- [BigQuery cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs)
