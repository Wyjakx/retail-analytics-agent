# Evaluation

The evaluation checks calculations, access controls and destructive actions before assessing report quality. The CLI and application integration are implemented and tested locally. An offline pass does not verify Gemini or BigQuery access.

## Reproduce the checks

Follow the [setup instructions](../README.md) from the repository root. The complete suite requires the development dependencies and the live SDK dependencies, because ADK graph tests substitute a local fake model inside the real framework.

On Windows, using the repository virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\retail-agent.exe --demo
```

With that environment activated, the equivalent commands are `python -m pytest` and `retail-agent --demo`. The demo uses synthetic transactions and a simulated planner/reporter. It requires no API key or cloud credentials. It is a walkthrough of the application flow, not an evaluation of Gemini's language understanding or BigQuery's SQL engine.

| Run | Evidence at this development stage |
| --- | --- |
| Saved-report service | 17 tests verified locally, including concurrent confirmations and rollback |
| Complete test suite | 159 tests and 5 subtests passed on Python 3.12.10 / Windows, 6 October 2026 |
| Static checks and dependencies | Ruff passes; `pip check` reports no broken requirements |
| Scripted CLI demo | Verified: comparison, follow-up, save, pseudonymous customer ranking and monthly follow-up, PII refusal, preview, plain-yes refusal, cancel and explicit confirmation |
| Live Gemini stages | Real ADK planning and reporting passed with `gemini-3.5-flash-lite` after the structured-output compatibility correction |
| Live BigQuery calculations | Four table schemas and US location verified; all six metrics matched independent SQL for authorized products 1 and 2, explicitly all time |
| Live application service | One complete question → plan → parameterized BigQuery query → Gemini report passed with two model calls and no report fallback |
| Live CLI | Normal ADC-based startup and the same six-metric all-time analysis passed with a Gemini report |
| Broader live scenarios | Live follow-ups, multi-query comparisons and failure scenarios remain unverified |

The live calculations returned one approved product group and suppressed another with fewer than three purchasers. Each of the application and reference queries processed 13,418,810 bytes and recorded 41,943,040 billed bytes, within the 100 MB per-query cap. A separate product-1 query for 2025 returned no rows; that case was retained rather than silently broadening its period.

The initial live Python checks used a short-lived credential from an existing authenticated `gcloud` session, supplied directly to the test client. Google consent was then completed, ADC configured with the execution project's quota context, and the normal `retail-agent --live` CLI passed the same analysis. No credential was saved in source control. These checks establish a working analytical path for one case, not broad natural-language accuracy or production readiness.

Gemini initially rejected the legacy schema's `additional_properties` field, then rejected the full JSON schema with array bounds. The runner now sends JSON Schema with `minItems`/`maxItems` omitted only from the provider copy. ADK and application Pydantic validation retain every original array bound and reject excess queries, products and report findings. This behavior is covered by the installed SDK wire-format and ADK graph tests.

## Scenarios and expected results

Use fixture calculations as independent expected answers, rather than checking only that a generated query or report exists. The seeded fixture has 12 purchasers, two monthly orders each, and both product 1 and product 3 in every order. Product 3 costs 500 per item and is outside the north analyst's permissions.

For product 1 across January and February 2025, expect revenue 900, 24 orders, 12 purchasing customers, 24 units, scoped average order value 37.50 and spend per purchasing customer 75. January revenue is 300 and February revenue is 600. California contributes 360 and Texas 540 across both months. These amounts are synthetic and have no asserted currency. In particular, north-analyst revenue must exclude the 12,000 from product 3.

For customer spending in 2025, six customers each contribute 90 and six each contribute 60 within the permitted products. A top-five query returns five 90-valued rows, ordered before the limit; unauthorized product spending cannot affect their amounts or ranking. A selected 90-valued customer's monthly follow-up returns January 30 and February 60 using only its opaque reference.

| Scenario | Expected check | Evaluation layer |
| --- | --- | --- |
| Arithmetic and dates | Verify the fixture answers above, UTC month grouping, inclusive start and exclusive end, status exclusions and empty periods. | Compiler and gateway tests; manual SQL comparison in a live run |
| Mixed-product orders | Filter unauthorized items before summing revenue or counting scoped orders/customers. An explicit unauthorized product request fails. | Known-answer fixtures and scope tests |
| Plan injection | Reject unknown metrics, identifier dimensions, raw SQL and extra fields. Treat filter strings as parameter values. | Typed-contract and compiler tests |
| PII and small groups | Refuse identity/contact requests; omit raw customer IDs; suppress aggregate segments below the configured minimum. Explicitly approved pseudonymous individual analyses retain their scoped statistics. Reject contact patterns or identifier fields in returned evidence. | Adversarial fixtures, output checks and human review |
| Pseudonymous customers | Verify stable keyed labels, actor separation, metric ordering before LIMIT and scoped monthly follow-ups. Unknown, copied or revoked references cannot broaden a query. Raw IDs, source PII and the key are absent from model payloads, saved evidence, explanations and traces. | HMAC/gateway tests, fake BigQuery rows and recording-model integration |
| Identifier claims in reports | Raw-ID narrative and fabricated customer references trigger grounded fallback, even when the claimed number matches a valid metric. | Fake-reporter application regression tests |
| Report grounding | Reject unsupported numerical claims. Review whether supported numbers are attributed to the correct metric, segment and period, and whether findings cite their evidence. | Output tests plus analyst review |
| Multi-step and follow-up | Compare periods or segments using several bounded queries; a follow-up preserves prior dates/product filters unless explicitly changed. | Application integration and intent review |
| Empty-result correction | Permit at most one equivalent correction; preserve permissions and requested scope. Distinguish no data from privacy suppression, then explain the limitation. | Injected empty returns and integration tests |
| Syntax/schema failures | Stop safely on incompatible schemas. An erroneous compiled query cannot trigger unrestricted model SQL. Compiler defects require a code correction. | Injected gateway errors; live schema/query smoke check |
| Dependency failures and costs | Bound transient model retries, request deadlines, query count and byte budgets. Keep the CLI usable after timeout, access denial or outage. | Fake model/client failures and application tests |
| Partial comparisons | A later failed query cannot turn earlier evidence into a completed multi-query report. | Application regression test |
| Trace-file failures | Logging I/O errors cannot crash the chat or misreport a committed deletion; emit a diagnostic warning instead. | Application regression test |
| Saved reports | Check actor/conversation/mention selection; preview exact targets; require a separate confirmation; verify cancel, expiry, wrong actor/token and changed versions. | SQLite service tests and CLI walkthrough |
| Concurrent report actions | New reports survive an old preview. Overlapping or simultaneous confirmations cannot broaden deletion. A commit failure rolls back deletion and token consumption together. | Separate SQLite connections and forced transaction failures |
| Correlated traces | Link request, conversation, stage, evidence/query, report and deletion operation IDs; record outcomes, timings, retries and usage. Raw prompts, rows, report bodies and confirmation tokens must be absent. | Trace assertions using distinctive sensitive test markers |

Numerical-token validation is a limited check: a number present in the evidence can still be attached to the wrong fact, or described with the wrong unit. Contact-pattern detectors also do not prove that every possible personal detail is recognized. The application reduces exposure through approved aggregate fields, keyed customer pseudonyms and withholding raw identities; reviewer checks cover the remaining semantic gaps. Pseudonyms remain linkable. Empty/rejected-query correction is an equivalent-plan retry, not arbitrary SQL repair; compiler defects require source changes.

## Human review and live validation

Ask an analyst to evaluate whether each report answers the requested question, states metric definitions and scope, distinguishes observed contributions from causes, and gives action items supported by the evidence. For churn, require an agreed definition and observation window before accepting a result. A plausible narrative alone is insufficient.

Ask representative nontechnical users to find available analyses, clarify a missing period, narrow a follow-up, save a report and cancel or confirm deletion. Observe whether they understand synthetic-data labels, permissions, suppressed results and the exact deletion targets. Record task completion, mistakes and confusing wording; set performance targets from measured runs.

A live smoke run must separately verify the configured Gemini model, authentication, four dataset schemas, dataset location, parameterized SQL, dry-run estimates and actual job statistics. Compare a small approved query with independently computed SQL, then test the multi-step and follow-up cases. Preserve only sanitized evaluation evidence. Golden retrieval, preference learning and persona administration require separate evaluation before their proposed production implementation is released.
