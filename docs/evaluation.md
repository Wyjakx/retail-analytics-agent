# Evaluation

The suite contains **20 tests**, chosen for consequences users would notice: incorrect answers, unauthorized data, exposed personal details, wasted cloud budget or lost reports. They exercise real application code with independently calculated answers. Cloud responses are simulated; these tests do not establish Gemini's understanding of natural language or execute SQL on BigQuery.

## Run

Install `requirements-ui-lock.txt` and the local package as described in the [README](../README.md). Streamlit, development dependencies and the live Google SDKs are required even though the suite makes no network calls.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests streamlit_app.py
```

On 9 October 2026, Python 3.12.14 / Windows: **20 passed, zero skips, in 4.97 seconds**; Ruff passed. Immediately before this rewrite, the merged suite had **121 tests and took 22.10 seconds**. The older Streamlit branch's 305 tests had already been reduced by previous work. The current reduction is 121 to 20, not a new claim to have removed 305 tests.

There are 20 test functions across five files, without parametrized cases or hidden batches of independent scenarios. Related assertions follow one consequential workflow. CI runs pytest and Ruff on Python 3.12 / Ubuntu; local results do not establish the current CI result.

## Selected coverage

| File | Tests | What a regression would break |
| --- | ---: | --- |
| `tests/test_critical_flows.py` | 10 | Six financial metrics; clarification and follow-up context; inclusive comparison dates; private customer analysis; input redaction; whole-plan authorization; grounded reporting; small-group suppression; recovery after provider outage; refusal to save incomplete comparisons |
| `tests/test_report_lifecycle.py` | 5 | Persistence and current owner permissions; exact deletion consent and cancellation; simultaneous confirmations; transactional rollback and retry; consent surviving restart without extending expiry |
| `tests/test_google_boundaries.py` | 2 | Real ADK/Gemini SDK wire compatibility; parameterized, scoped BigQuery submission with remaining deadline and cumulative actual billing |
| `tests/test_streamlit_workflows.py` | 2 | Analysis, rerun, save, open, cancel and confirm through actual callbacks; removal of chat and open reports after permission revocation, followed by analysis under the new scope |
| `tests/test_cli_workflow.py` | 1 | A real interactive subprocess can analyze, save a literal Unicode title, preserve reports on cancellation and prevent saving stale context after reset |

Application tests replace generated JSON at the provider boundary. ADK, typed validation, orchestration, filtering, arithmetic, policies and SQLite remain real. Fixture cleanup rejects unexpected or unused provider responses so fallback cannot conceal a broken test script. BigQuery tests use real SDK configuration and the production SQL compiler, with a simulated remote client/job. A trigger in an actual SQLite database makes deletion fail during token consumption; reports and consent must roll back together.

Streamlit AppTest runs actual widgets and callbacks. The CLI launches a new Python process with temporary storage and an absent `.env` path. Both use the offline data source intentionally; their purpose is checking the interface and persistence, while the separate Google boundary tests check SDK integration.

## Independent known answers

`tests/conftest.py` owns a relational dataset separate from the application's demo-data generator. Six customers buy in January and February. Some orders contain two authorized items, and customers place several orders. Every order also contains an unauthorized product costing 1,000.

| Products 1 and 2, January–February 2025 | Expected answer |
| --- | ---: |
| Revenue | 500 |
| Orders | 12 |
| Purchasing customers | 6 |
| Units | 14 |
| Average order value | 41.67 |
| Spend per purchasing customer | 83.33 |
| January / February revenue | 230 / 270 |
| Top three customer amounts | 130, 110, 90 |
| Highest customer's January / February spending | 60 / 70 |
| Unauthorized revenue to exclude | 12,000 |

These expected values are literals, not results from production helpers. Returned items, negative prices, a cancelled order and an inconsistent customer join must not inflate the totals. A comparison moves transactions onto 31 January and leap day to expose omission of the requested final day. Other scenarios verify suppression of sparse groups and ensure operational billing statistics cannot become a supported revenue claim.

The interface tests use the application's seeded data, with product-1 revenue of 900 in 2025. That amount is not the independent oracle for the analytical tests. No currency is assumed.

## Verify that the tests detect defects

```powershell
.\.venv\Scripts\python.exe -m tests.effectiveness
```

This optional developer check first runs the normal suite, then introduces one defect at a time in memory in a fresh process and reruns its relevant test. It never edits production files. Collection, setup or teardown errors do not count as a detection. These are replays of the existing tests, not additional scenarios in the regular suite.

The verified run detected **15/15 selected defects**:

- Including unauthorized revenue or dividing customer spend by the order count.
- Revealing small groups, retaining a pasted secret or accepting a generated email address.
- Accepting an invented report amount or retaining conversation history after permissions change.
- Skipping whole-plan scope validation before starting the first query.
- Sending an unsupported Gemini schema or omitting BigQuery's product predicate.
- Ignoring actual billing when deciding whether another query fits the budget.
- Committing a partial deletion or accepting consent exactly at its expiry.
- Omitting the final requested calendar day or retaining revoked browser chat.

This demonstrates detection of those specific defects. It is not an exhaustive mutation score or proof of equivalent coverage to the deleted suite.

## Selection rules and deliberate omissions

Following Superpowers' testing criteria, each retained test must name a plausible defect and check an observable consequence for answers, access, spending, recoverability or persisted data. Prefer real integration at stable boundaries and independent expected answers. Do not add cases merely to mirror implementation branches, repeat ordinary library validation or test every spelling of the same input.

The rewrite removes repetitive unit tests, presentation snapshots and overlapping checks at several layers. It intentionally does **not** preserve every old branch. Dedicated tests for all clarification variants, chart layouts, narrow-terminal formatting, demo/startup errors, report revision conflicts, mid-response permission races, ambiguous cloud submission, cancellation, schema drift, trace I/O failures and every malformed model output are omitted. Core permissions, privacy, costs and consent retain concrete checks, but the suite does not claim complete security or failure-mode coverage. If one of the omitted behaviors causes a real regression, replace a lower-value scenario or justify adding a focused test.

## Live and human validation

No new cloud calls were made for this test rewrite. [Live validation on 9 October](live-validation-2026-10-09.md) records separate checks against Gemini and BigQuery: follow-ups, comparisons, persisted reports, Streamlit and independent reference SQL. The SDK serialization, evidence-citation and date-boundary regressions found during previous Live work remain represented in this suite.

A live release still needs checks of configured credentials/model, remote schemas, estimates and actual billing, plus independently written SQL for representative answers. An analyst must assess whether prose assigns each amount to the correct metric, segment and period, answers the question and avoids unsupported causal claims. Numeric-token validation and scripted provider responses cannot establish that. Production authentication, natural-language accuracy and usability require separate evaluation.
