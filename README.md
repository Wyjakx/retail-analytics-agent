# Retail Analytics Agent

A conversational analytics assistant for retail executives, using Python, Google ADK 2, Gemini and BigQuery.

**Status: runnable local prototype with a verified live CLI smoke test.** With application-default credentials, Gemini planning, BigQuery execution and Gemini report generation completed one real analysis; all six metrics matched independent SQL. The offline CLI, multi-turn follow-ups, saved reports and safety/error-handling boundaries are tested locally. Broader live scenarios still need validation. The production architecture is a design proposal, not a deployment.

## Intended user experience

An executive asks a question such as "Compare monthly revenue for products X and Y, and explain the observed differences." The assistant clarifies ambiguous metrics, prepares a bounded analysis, queries authorized data, and explains the findings. Follow-up questions reuse the conversation's analytical context. On request, the assistant saves a report with evidence, limitations and recommended actions.

Report deletion follows a separate flow: identify the current user's matching reports, preview the exact selection, request explicit confirmation, then execute the approved operation. An AI-generated approval cannot confirm deletion.

## Implementation

| Component | Choice | Responsibility |
| --- | --- | --- |
| Chat interface | Rich-based Python CLI | Conversation, tables, progress and confirmation previews |
| Model orchestration | Google ADK 2 | Typed model-stage workflows, explicit routes and bounded model calls |
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

The client accepted predefined demo users and stable pseudonymous customer analyses. The local selector and explicit product mapping demonstrate authorization; production scopes would arrive through a verified frontend JWT. Customer rankings use actor-scoped opaque references, with raw IDs retained inside the Python gateway. Charts, email and web research are extension points in the design.

## Documents

- [Architecture and security boundaries](docs/architecture.md)
- [Implementation plan and acceptance checks](docs/implementation-plan.md)
- [Initial decisions and open questions](docs/decisions.md)
- [Implementation choices explained](docs/development-notes.md)
- [Evaluation and validation limits](docs/evaluation.md)

## Install and run

The application requires **Python 3.11 or later** and was verified on Python 3.12 on Windows. The selected ADK package is `google-adk==2.11.0`. Run these commands from the repository directory:

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
.\.venv\Scripts\retail-agent.exe --question "Top 5 customers by spending in 2025"
```

The demo uses two queries for the monthly comparison, follows up by product, saves a report, ranks pseudonymous customers, breaks down one customer's spending by month, refuses a PII request and demonstrates both cancellation and confirmed deletion. In an interactive session, a customer ranking returns references such as `cust_<32 hex characters>`; follow up with `Break down cust_REFERENCE_FROM_RESULTS by month`. Spending always covers authorized products only. `/help` lists interactive commands:

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

## Optional Streamlit interface

Install the separate UI lock into the same environment, then launch from the repository:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ui-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Open the local URL printed by Streamlit. Offline is selected by default and uses synthetic
data. Ask `Show revenue by product`, then answer `2025` when prompted. The sidebar applies
actor and mode changes together and starts a fresh conversation. Live uses the configuration
below and only submits an analysis when you send a question or click an example.
The original CLI installation remains valid without Streamlit; UI tests are skipped when
that optional dependency is absent. CI installs the UI lock to exercise both interfaces.

## Live Gemini and BigQuery

Copy `.env.example` to an ignored `.env` and set `GOOGLE_API_KEY`, `GEMINI_MODEL` and `GOOGLE_CLOUD_PROJECT`. Choose a Gemini model available to your account from the [official model list](https://ai.google.dev/gemini-api/docs/models); the recorded smoke test used `gemini-3.5-flash-lite`. BigQuery also needs application-default credentials and query-job permission on the execution project. With the Google Cloud CLI installed, configure ADC yourself:

```text
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

Complete the Google consent page, including its requested Google Cloud access. A normal `gcloud auth login` does not by itself configure ADC for the Python client. See [BigQuery client authentication](https://docs.cloud.google.com/bigquery/docs/authentication) for supported alternatives. Keep all API keys and credential files outside Git.

```powershell
.\.venv\Scripts\retail-agent.exe --live
.\.venv\Scripts\retail-agent.exe --live --question "Show revenue, orders, purchasing customers, units, average order value, and spend per customer by product for products 1 and 2, all time."
```

The first command starts an interactive chat. The second runs the question used in the verified live smoke test and exits. In that run, one product row was visible and another was suppressed by the minimum-customer rule. Results depend on the public dataset at query time. A product-1 query restricted to 2025 returned no rows in the recorded test; the application kept that period rather than silently broadening it.

Live mode checks the four required table schemas and dataset location, compiles SQL from approved expressions, dry-runs it and enforces per-query and per-turn byte caps. Defaults are 100 MB per query, 300 MB per turn, at most three query attempts, one correction cycle and six model calls including retries. These are prototype budgets, not account quotas or a pricing guarantee.

`analyst_north` has demo product IDs `[1, 2]`; `analyst_south` has `[3, 4]`. The accepted lightweight approach is illustrated by `config/demo-permissions.json`. Set `PRODUCT_PERMISSIONS_FILE` to that path or another local JSON object such as `{"demo_user": [10, 20]}` and select that actor. The file is reloaded each request. Direct product IDs provide explicit demo entitlements; a production brand-to-product resolver would use trusted catalog data after JWT verification.

Customer references are HMAC-derived from an internal customer ID, the actor and a private key. A random key is created automatically in each mode's runtime directory; an optional `CUSTOMER_PSEUDONYM_KEY` supplies a persistent 64-character hexadecimal secret. Keep it private and stable. Different actors receive different labels. Raw-ID linkage stays in gateway memory and never enters the model, reports or traces. After restart or `/new`, rerun a ranking before referring to a previous label. Minimum-group suppression remains active for aggregate segments; explicitly requested pseudonymous individual analyses are exempt under the client's clarification. Pseudonyms are linkable, not anonymous.

## Validation and runtime data

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

The tests use independently specified numerical expectations, fake BigQuery clients, a fake model inside the installed ADK runtime, and the actual CLI/SQLite flow. [Evaluation](docs/evaluation.md) records the verified results and the remaining live/semantic review.

GitHub Actions runs the same pytest and Ruff checks on pushes and pull requests using Python 3.12 and the frozen dependencies. The workflow has read-only repository permissions and uses no Gemini or Google Cloud credentials. Database execution and saved-report operations remain Python application services; the ADK stages expose no database or deletion tools.

Runtime data goes under `APP_DATA_DIR` (default `runtime`), separated into `demo`, `offline` and `live`. Each mode has `reports.sqlite3`, `events.jsonl` and, unless configured externally, `customer-pseudonym.key`. Conversations and raw-ID linkage are in memory; saved reports and pending-operation outcomes persist. Traces include correlation IDs and usage metadata, not raw questions, SQL, data rows, report bodies, pseudonyms or tokens. Runtime files, cloud credentials and local environment files are ignored by Git.

## Read the code

| File | Responsibility |
| --- | --- |
| `src/retail_agent/cli.py` | Chat, result presentation and human confirmation |
| `src/retail_agent/service.py` | Request orchestration, current permissions, bounded correction and follow-up state |
| `src/retail_agent/adk_workflow.py` | Actual ADK typed-output stages, model call budget and deadlines |
| `src/retail_agent/model.py` | Gemini contracts, offline simulation and grounded fallback reports |
| `src/retail_agent/analytics.py` | Validated analytical grammar and dynamic SQL compilation |
| `src/retail_agent/gateways.py` | BigQuery execution controls and computed synthetic fixtures |
| `src/retail_agent/pseudonyms.py` | Actor-scoped customer labels and private in-memory reference resolution |
| `src/retail_agent/reports.py` | Transactional report ownership and deletion confirmation |
| `src/retail_agent/safety.py` | Input/output checks and approved result contract |
| `src/retail_agent/telemetry.py` | Allowlisted structured events |
| `src/retail_agent/config.py` | Local settings and demo entitlement resolution |

## Primary technical references

- [Google ADK graph workflows](https://adk.dev/graphs/)
- [Google ADK 2.11.0 package](https://pypi.org/project/google-adk/2.11.0/)
- [Gemini documentation](https://ai.google.dev/gemini-api/docs)
- [BigQuery query cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs)
