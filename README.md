# Retail Analytics Agent

A conversational analytics assistant for retail executives, using Python, LangGraph, Gemini and BigQuery.

**Status: design and repository foundation.** The analytical workflow and chat CLI have not been implemented yet. This repository records the intended solution and its acceptance criteria; proposed controls are not claims about working software.

## Intended user experience

An executive asks a question such as "Compare monthly revenue for products X and Y, and explain the observed differences." The assistant clarifies ambiguous metrics, prepares a bounded analysis, queries authorized data, and explains the findings. Follow-up questions reuse the conversation's analytical context. On request, the assistant saves a report with evidence, limitations and recommended actions.

Report deletion follows a separate flow: identify the current user's matching reports, preview the exact selection, request explicit confirmation, then execute the approved operation. An AI-generated approval cannot confirm deletion.

## Proposed implementation

| Component | Choice | Responsibility |
| --- | --- | --- |
| Chat interface | Rich-based Python CLI | Conversation, tables, progress and confirmation previews |
| Orchestration | LangGraph | Typed state, explicit routes, bounded repair and tool dispatch |
| Model adapter | Google GenAI SDK with Gemini | Analysis planning and grounded report generation |
| Analytics gateway | BigQuery Python client | Trusted product scope, controlled SQL generation, query budgets and safe results |
| Application storage | SQLite | Conversations, saved reports, ownership and pending confirmations |
| Diagnostics | Redacted structured events | Correlated stage timings, failures, retries and query metadata |

The model proposes analysis; application code decides what is authorized. A validated analytical plan is compiled dynamically into parameterized SQL. The supported plan grammar must cover the demonstration's comparisons, time series and multi-step analyses. Unsupported requests receive an explicit limitation or clarification.

The dataset is `bigquery-public-data.thelook_ecommerce`; the required tables are `orders`, `order_items`, `products` and `users`. Customer identities are treated as sensitive, including when the source is a public demonstration dataset.

## Scope

| Requirement | Prototype commitment | Production design commitment |
| --- | --- | --- |
| Natural-language analysis and reports | CLI, multi-turn context, bounded multi-step SQL analysis, evidence and action items | Reusable application service and tool contracts |
| Safety and PII | Analysis-only routing, trusted demo entitlements, safe query/result contracts | Verified identity, restricted analytics data, defense in depth |
| Destructive oversight | Owned reports, exact preview, explicit one-use confirmation | Auditable approval and transactional execution |
| Resilience | Bounded corrections, API retries and graceful failures | Deadlines, circuit breaking, degradation and recovery |
| Observability | Redacted correlated traces and cost/query metrics | Central telemetry and operational investigation |
| Golden knowledge | Design only | Scoped retrieval and analyst-approved ingestion of Question / SQL / Report trios |
| User and system learning | Design only | Isolated preferences, curated feedback and evaluated releases |
| Quality and UX evaluation | Critical local tests and a demonstration script are planned | Offline analytical evaluation, adversarial cases and user studies |
| Persona management | Design only | Versioned nondeveloper configuration with rollback; security policy stays separate |

The local user selector will demonstrate authorization behavior. It will not be described as production authentication. Charts, email and web research are extension points in the design.

## Documents

- [Architecture and security boundaries](docs/architecture.md)
- [Implementation plan and acceptance checks](docs/implementation-plan.md)
- [Initial decisions and open questions](docs/decisions.md)

## Development setup status

No live API access or deployed infrastructure is required to review this foundation. `.env.example` lists the intended configuration without credentials. Executable installation instructions, pinned dependencies and an example run will be added with the implementation.

Runtime databases, generated reports, traces, cloud credentials and local environment files stay outside version control.

## Primary technical references

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Google GenAI SDK](https://ai.google.dev/gemini-api/docs/libraries)
- [BigQuery query cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs)
