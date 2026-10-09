# Evaluation

The automated battery checks observable application behavior: correct retail answers, authorized data, bounded cloud work and safe persistence. It runs locally without cloud credentials. It does not measure Gemini's understanding of natural language or execute SQL on BigQuery.

## Run the battery

Install the frozen dependencies and local package as described in the [README](../README.md). The development and live SDK dependencies are needed: the tests exercise the installed ADK and Google SDK serialization even though network responses are simulated.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

The rebuilt battery passed 52 scenarios on Python 3.12.14 / Windows on 8 October 2026. Ruff also passed. CI runs pytest and Ruff on Python 3.12 / Ubuntu; this local run does not establish the current CI result.

| Test file | Scenarios | Observable contract |
| --- | ---: | --- |
| `tests/test_application.py` | 34 | Real ADK → service → arithmetic → SQLite; known answers, follow-ups, permissions, privacy, failures, save/delete and traces |
| `tests/test_cloud_contracts.py` | 9 | Real ADK/Gemini SDK serialization and BigQuery job configuration; parameter binding, cost caps, cancellation, ambiguous submission and customer references |
| `tests/test_storage_transactions.py` | 6 | Separate SQLite connections, racing confirmations, rollback, report revisions, restart/expiry and ownership |
| `tests/test_cli.py` | 3 | Actual subprocesses: interactive save/cancel/reset, the complete demo and a useful startup error without credentials |

Application tests substitute provider-generated JSON at the model boundary; ADK's graph, typed validation, service orchestration, filtering, arithmetic, permissions and SQLite remain real. An unexpected or unused provider response fails fixture cleanup so a fallback cannot conceal a broken script. BigQuery tests substitute the remote client/job boundary; the actual SQL compiler and SDK query parameters run. Two narrow application fault injections cover a failed second query and a known evidence-citation regression.

CLI tests start fresh Python processes with a temporary data directory, no local `.env` and isolated application settings. Storage tests use real files and multiple connections, including a database trigger that fails during deletion to verify rollback of both the reports and the confirmation.

## Independent known answers

`tests/conftest.py` owns a small relational dataset, separate from the application's demo-data generator. Six customers buy in January and February. Two authorized products can appear in one order, so item, order and customer counts differ. Every order also contains an unauthorized product costing 1,000.

| Authorized products 1 and 2, January–February 2025 | Expected answer |
| --- | ---: |
| Revenue | 500 |
| Orders | 12 |
| Purchasing customers | 6 |
| Units | 14 |
| Average order value | 41.67 |
| Spend per purchasing customer | 83.33 |
| January revenue / February revenue | 230 / 270 |
| California revenue / Texas revenue | 170 / 330 |
| Top three customer amounts | 130, 110, 90 |
| Highest customer's January / February spending | 60 / 70 |
| Unauthorized revenue that must be excluded | 12,000 |

Expected answers are literal, independently calculated values. A separate follow-up scenario asks the real offline planner to compare product 1 by state and month, then narrow to Texas. It must retain the original dates and product scope. Boundary transactions distinguish UTC from local dates and inclusive starts from exclusive ends. Returned items, negative prices, broken joins and cancelled orders must not inflate revenue.

The CLI demo still uses the application's own seeded data: product-1 revenue is 900 across 24 orders and 12 customers. Those demo figures are not the oracle for the independent application battery. Synthetic amounts have no asserted currency.

## Consequential failure scenarios

| Risk | Required outcome |
| --- | --- |
| Unauthorized product or excessive query plan | Reject the complete plan before its first query |
| Raw SQL or injected sort direction in model output | Reject with bounded correction; never execute the supplied SQL |
| Changed permissions | Clear prior analytical context, hide inaccessible reports and invalidate pending deletion; also check permissions after reporting |
| Small aggregate groups | Remove them before the reporter sees evidence |
| Customer analysis | Use actor-bound opaque references; follow-up selects the intended customer without exposing raw IDs, source contact details or the key |
| Pasted credentials/contact details | Mask both configured secrets and unrelated Google-key patterns before provider input and history; exclude them from persisted evidence and traces |
| Unsafe evidence/report/title | Stop unsafe evidence, replace an unsafe report with a visible grounded fallback, or refuse the save |
| Valid opaque citation containing digits | Retain the verified report instead of rejecting citation digits as a business number |
| Empty period | Permit one equivalent retry; never silently broaden dates |
| Provider outage or deadline | Bound retries, share the six-call cap across planning/correction/reporting, cancel outstanding generation and allow the next question |
| Failed second comparison query | Never save a partial result as a completed comparison |
| Excessive cloud estimate or actual billing | Stop before submitting an unaffordable live job |
| Remote timeout or ambiguous submission | Pass the remaining request deadline to BigQuery, cancel the identified job without a duplicate, and submit no billable job after cancellation during dry-run |
| Destructive report command | Preview exact literal targets, require the issued token, mask it if pasted into a question, honor cancellation and keep reports created after preview |
| Races, revision or expired consent | Commit one frozen outcome; reject changed or expired targets and enforce actor ownership |
| Database failure during deletion | Roll back report removal and token consumption together; a valid retry can still succeed |
| Trace I/O failure | Report the committed business outcome correctly and emit a diagnostic warning |

## Verify that tests detect defects

Run the optional effectiveness probes from the repository root:

```powershell
.\.venv\Scripts\python.exe -m tests.effectiveness
```

The command first requires a passing baseline. It then introduces one deliberate in-memory defect in a fresh process and runs the relevant acceptance test. It never edits production files. Collection, fixture or teardown errors do not count as successful detection.

The recorded run detected all 11 selected regressions: unauthorized revenue included, spend divided by orders instead of customers, small groups exposed, credential redaction removed, invented amounts accepted, revoked history retained, plan-budget preflight skipped, unsupported provider schema sent, BigQuery product predicate removed, partial deletion committed after failure, and confirmation accepted exactly at expiry. These probes demonstrate specific detection capabilities, not an exhaustive mutation score or proof that every defect is covered.

## Admission criteria and deliberate omissions

The earlier 121-case suite was replaced rather than merely regrouped. A new test must name a plausible regression and assert a consequence for answers, access, cost, recoverability or saved data. Keep one scenario per consequential behavior; parameterize genuinely distinct boundaries, and exercise related assertions in the same user flow. Do not derive expected answers with production helpers, snapshot incidental object structure or repeat every input spelling across layers.

Dedicated simulator synonyms, ordinary Pydantic type/length checks, malformed internal fixture arguments, invalid internal deletion TTL values and exhaustive credential/citation spelling variants are intentionally omitted. The automated suite also does not claim complete schema-drift coverage or every cloud failure mode. The new battery does not preserve every branch covered by the deleted tests. Test count and line coverage are not measures of usefulness.

## Live and human validation remain separate

Historical live checks, recorded before this reconstruction, verified a complete ADK/Gemini → BigQuery → report → CLI path with `gemini-3.5-flash-lite`. Four table schemas and US location were checked. All six all-time metrics for authorized products 1 and 2 matched independent SQL; one group was suppressed for fewer than three purchasers. Each application/reference query processed 13,418,810 bytes and recorded 41,943,040 billed bytes, within the 100 MB per-query cap. A product-1 query limited to 2025 returned no rows and retained its requested period. ADC-based CLI startup also succeeded. No new live calls were made during the test reconstruction.

Those live checks exposed unsupported provider-schema serialization and the false rejection of a digit-leading citation (`bq-7655e3b16480f29b`). The new battery retains both regressions. Live follow-ups, multi-query comparisons and cloud failure scenarios remain unverified against the actual services.

Before relying on a live deployment, verify authentication, configured model, dataset schemas/location, dry-run estimates and actual billing. Compare approved calculations with independently written SQL, then exercise comparisons and follow-ups. Keep only sanitized evidence.

An analyst must still assess whether the report answers the question, assigns amounts to the correct metric/segment/period, states scope and definitions, distinguishes observations from causes and supports its proposed actions. Numerical-token validation cannot establish those semantic claims. Contact/credential detectors do not recognize every possible sensitive representation, and customer pseudonyms remain linkable.

With representative users, observe clarification, narrowing a follow-up, saving, and cancelling/confirming deletion. Check comprehension of synthetic-data labels, permissions, suppressed results and exact deletion targets. Natural-language accuracy, usability and production authentication require separate evaluation.
