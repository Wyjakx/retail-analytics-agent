# Retail Analytics Agent

A conversational analytics assistant for retail executives, using Python, Google ADK 2, Gemini and BigQuery.

**Status: runnable local prototype.** The offline CLI, analytical calculations, multi-turn follow-ups, saved reports and safety/error-handling boundaries are tested locally. Live Gemini and BigQuery adapters are implemented; cloud authentication and actual service calls have not been validated. The production architecture is a design proposal, not a deployment.

## Intended user experience

An executive asks a question such as "Compare monthly revenue for products X and Y, and explain the observed differences." The assistant clarifies ambiguous metrics, prepares a bounded analysis, queries authorized data, and explains the findings. Follow-up questions reuse the conversation's analytical context. On request, the assistant saves a report with evidence, limitations and recommended actions.

Report deletion follows a separate flow: identify the current user's matching reports, preview the exact selection, request explicit confirmation, then execute the approved operation. An AI-generated approval cannot confirm deletion.

## Implementation

| Component | Choice | Responsibility |
| --- | --- | --- |
| Chat interface | Rich-based Python CLI | Conversation, tables, progress and confirmation previews |
| Orchestration | Google ADK 2 | Typed workflows, explicit routes, bounded repair and tool execution |
| Model integration | Gemini through ADK | Analysis planning and grounded report generation |
| Analytics gateway | BigQuery Python client | Trusted product scope, controlled SQL generation, query budgets and safe results |
| Application storage | SQLite + in-memory conversation | Saved reports, ownership and pending confirmations; sanitized follow-up state |
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
| Quality and UX evaluation | Numerical, adversarial, ADK-runtime and CLI tests; scripted demonstration | Live analytical evaluation, analyst review and user studies |
| Persona management | Design only | Versioned nondeveloper configuration with rollback; security policy stays separate |

The local user selector demonstrates authorization rules using provisional fixture permissions. It is not production authentication. Individual customer rankings remain disabled pending the client's privacy clarification. Charts, email and web research are extension points in the design.

## Documents

- [Architecture and security boundaries](docs/architecture.md)
- [Implementation plan and acceptance checks](docs/implementation-plan.md)
- [Initial decisions and open questions](docs/decisions.md)
- [Implementation choices explained](docs/development-notes.md)
- [Evaluation and validation limits](docs/evaluation.md)

## Install and run

The application requires **Python 3.11 or later** and was verified on Python 3.12.14 on Windows. The selected ADK package is `google-adk==2.11.0`. Run these commands from the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\retail-agent.exe --demo --show-plan
```

On macOS/Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python` and the executable with `.venv/bin/retail-agent`. The pinned runtime/development versions are in `requirements-lock.txt`; `pip install -r requirements.txt` is the alternative for resolving dependencies from the declared ranges.

The default mode needs no API key. It computes synthetic transactions using the same validated plan contract, with a small keyword planner in place of Gemini. It does not execute SQL in BigQuery or measure real LLM quality.

```powershell
.\.venv\Scripts\retail-agent.exe
.\.venv\Scripts\retail-agent.exe --question "Compare revenue and spend per customer by state in January versus February 2025"
.\.venv\Scripts\retail-agent.exe --actor analyst_south --question "Show revenue by product in 2025"
```

The demo uses two queries for the monthly comparison, follows up by product, saves a report, refuses a PII request and demonstrates both cancellation and confirmed deletion. `/help` lists interactive commands:

```text
/save Q1 analysis
/reports
/delete conversation
/delete mention Q1
/delete id REPORT_ID
/confirm TOKEN_FROM_THE_PREVIEW
/cancel
/explain
/new
/quit
```

Natural-language deletion also accepts `Delete all reports mentioning Client X` and `Delete all the reports we made in this conversation`. A plain `yes` cannot execute deletion. The model never receives report confirmation tokens; the CLI handles them separately.

## Live Gemini and BigQuery

Copy `.env.example` to an ignored `.env` and set `GOOGLE_API_KEY`, `GEMINI_MODEL` and `GOOGLE_CLOUD_PROJECT`. Choose a Gemini model available to your account from the [official model list](https://ai.google.dev/gemini-api/docs/models). BigQuery also needs application-default credentials and query-job permission on the billing project. With the Google Cloud CLI installed, configure ADC yourself:

```text
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

See [BigQuery client authentication](https://docs.cloud.google.com/bigquery/docs/authentication) for supported alternatives. Keep all API keys and credential files outside Git.

```powershell
.\.venv\Scripts\retail-agent.exe --live --question "Show monthly revenue for product 1 in 2025"
```

Live mode checks the four required table schemas and dataset location, compiles SQL from approved expressions, dry-runs it and enforces per-query and per-turn byte caps. Defaults are 100 MB per query, 300 MB per turn, at most three query attempts, one correction cycle and six model calls including retries. These are prototype budgets, not account quotas or a pricing guarantee.

`analyst_north` has demo product IDs `[1, 2]`; `analyst_south` has `[3, 4]`. To use another **trusted demo** mapping, set `PRODUCT_PERMISSIONS_FILE` to a local JSON object such as `{"demo_user": [10, 20]}` and select that actor. The file is reloaded each request. This policy mapping remains provisional until the client responds; it is not a login mechanism.

## Validation and runtime data

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

The tests use independently specified numerical expectations, fake BigQuery clients, a fake model inside the installed ADK runtime, and the actual CLI/SQLite flow. [Evaluation](docs/evaluation.md) records the verified results and the remaining live/semantic review.

Runtime data goes under `APP_DATA_DIR` (default `runtime`), separated into `demo`, `offline` and `live`. Each mode has `reports.sqlite3` and `events.jsonl`. Conversations are in memory; saved reports and pending-operation outcomes persist. Traces include correlation IDs and usage metadata, not raw questions, SQL, data rows, report bodies or tokens. Runtime files, cloud credentials and local environment files are ignored by Git.

## Read the code

| File | Responsibility |
| --- | --- |
| `src/retail_agent/cli.py` | Chat, result presentation and human confirmation |
| `src/retail_agent/service.py` | Request orchestration, current permissions, bounded correction and follow-up state |
| `src/retail_agent/adk_workflow.py` | Actual ADK typed-output stages, model call budget and deadlines |
| `src/retail_agent/model.py` | Gemini contracts, offline simulation and grounded fallback reports |
| `src/retail_agent/analytics.py` | Validated analytical grammar and dynamic SQL compilation |
| `src/retail_agent/gateways.py` | BigQuery execution controls and computed synthetic fixtures |
| `src/retail_agent/reports.py` | Transactional report ownership and deletion confirmation |
| `src/retail_agent/safety.py` | Input/output checks and aggregate result contract |
| `src/retail_agent/telemetry.py` | Allowlisted structured events |
| `src/retail_agent/config.py` | Local settings and demo entitlement resolution |

## Primary technical references

- [Google ADK graph workflows](https://adk.dev/graphs/)
- [Google ADK 2.11.0 package](https://pypi.org/project/google-adk/2.11.0/)
- [Gemini documentation](https://ai.google.dev/gemini-api/docs)
- [BigQuery query cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs)
