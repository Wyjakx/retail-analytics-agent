# Streamlit Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local Streamlit chat and report interface that reuses the retail analytics service, preserves the required CLI, and resolves clarification replies correctly.

**Architecture:** A per-browser adapter owns conversation state and opens a short-lived SQLite store for each action. Both interfaces use shared runtime construction; authorization, model orchestration and report mutations remain application services. Streamlit renders approved results and sends explicit user actions to the adapter.

**Tech Stack:** Python >=3.11 (existing requirement; verify on Python 3.12), Streamlit 1.65.0, existing Pydantic/Rich/SQLite stack, pinned Google ADK 2.11.0 and BigQuery client, pytest, Ruff, Streamlit AppTest.

**Spec:** [Approved design](../specs/2026-10-08-streamlit-design.md). Read it alongside this plan. User approved the spec on 8 October 2026; this plan awaits review and execution-method selection.

## Global Constraints

- "The assignment still requires the CLI, which remains supported."
- "This is a local demonstration; interface labels remain in English to match the existing CLI and assignment; offline mode is the default."
- "No independent SQL, model prompts, authorization decisions or destructive-operation implementation belongs in the UI."
- "The adapter is stored in per-browser `st.session_state`, never in a global resource cache."
- "Do not keep a SQLite connection across reruns or disable SQLite's thread checks."
- "No event loop or asynchronous task survives a rerun."
- "The token does not appear in chat, model input, diagnostics or ordinary widget keys."
- "Existing `APP_DATA_DIR/offline` and `APP_DATA_DIR/live` persistence is reused."
- "Pin a compatible UI dependency set without silently upgrading the already frozen Gemini/BigQuery stack."
- "Default verification uses no cloud calls. Any live validation must be clearly identified separately from synthetic results."
- Preserve the current 4,000-character question cap, last-six-message model history, configured query/model budgets, and privacy threshold. Product authentication, deployment and broader analytical-engine changes are outside this plan.

## Review Focus

- Permissions disappear between reruns: clear cached chat/results/open reports before any presentation, including when the permissions file becomes invalid (Task 2).
- Two browser sessions share an actor, but one deletes or changes a report: refresh the library/open report and reject stale confirmations (Tasks 3 and 5).
- A clarification reply follows an unrelated successful analysis: resolve the pending question without inheriting unrelated metrics or periods (Task 1).
- Widget reruns and repeated clicks: no implicit model/query invocation, no replayed save, no token leakage, and no deletion from a stale preview (Tasks 4 and 5).
- Empty, suppressed, capped or multidimensional evidence: present an honest table/notice and only chart supported cells without inventing totals or percentages (Task 5).

## Files and ownership

| File | Responsibility |
| --- | --- |
| `src/retail_agent/runtime.py` (new) | Shared mode-specific settings, model, gateway and trace construction. |
| `src/retail_agent/web_session.py` (new) | Session lifecycle, short-lived store access, sanitized transcript and UI action adapter. |
| `src/retail_agent/streamlit_ui.py` (new) | Sidebar, chat, action routing and top-level rendering. |
| `src/retail_agent/streamlit_views.py` (new) | Evidence, safe chart selection, saved-report and confirmation panels. |
| `streamlit_app.py` (new) | Thin executable entry point calling the UI's `main()`. |
| `src/retail_agent/service.py`, `model.py` | Pending clarification, public scope refresh, authorized saved-report reads and evidence cap metadata. |
| `src/retail_agent/cli.py` | Use shared construction; present the new saved-report read command. |
| `tests/test_web_session.py`, `test_streamlit_ui.py`, `test_streamlit_views.py` (new) | Adapter, AppTest and presentation behavior. |
| Existing service/model/security/analytics tests | Clarification and access regressions; deterministic deadline check. |
| `pyproject.toml`, `requirements-ui-lock.txt` (new), `.github/workflows/ci.yml` | Optional UI dependencies and credential-free CI. |
| `README.md`, `docs/architecture.md`, `docs/evaluation.md` | Run instructions, diagram and actual verification results. |

## Execution setup

At execution time use `using-git-worktrees`, preserving the approved spec and plan from branch `codex/streamlit-design`; do not start from a remote ref that omits them. Inspect attached worktrees before creating one. Install the existing lock and editable package into that checkout's environment. Commands below assume PowerShell at its repository root and `.venv/Scripts/python.exe`; use the platform-equivalent executable on Linux.

The earlier audit observed 181 passing tests, 5 passing subtests and one failing floating-point deadline assertion. Task 6 diagnoses and stabilizes that test; do not silently ignore it or claim a green baseline. No implementation or UI installation occurred while writing this plan.

---

### Task 1: Preserve and resolve clarification context

**Files:** Modify `src/retail_agent/service.py` (`Conversation`, `_scope`, `_handle`, `_analyze`), `src/retail_agent/model.py` (planning instructions and offline parsing); tests in `tests/test_service.py`, `tests/test_model.py`, `tests/test_security_integration.py`.

**Interfaces:**
- Consumes the existing `AnalyticalModel.plan(...)`, `report(...)`, `Conversation` and `Decision`; keep their public call signatures compatible.
- Produces `Conversation.pending_clarification: list[dict[str, str]]` with user/assistant records; planning receives an explicitly labeled copy at `safe_catalog['pending_clarification']`.
- Pending context contains at most six records and 4,000 characters in total. If the budget would be exceeded, clear it and ask for one complete question rather than truncating a meaningful filter.

- [ ] **Step 1: Add failing conversation regressions.** Reuse the existing `service` and `ask` helpers:

```python
def test_clarification_reply_completes_original_question(service):
    assert ask(service, "Show revenue by product").report is None
    result = ask(service, "2025")
    spec = result.plan.queries[0]
    assert result.report is not None
    assert spec.metrics == ["revenue"] and spec.dimensions == ["product"]
    assert (str(spec.start_date), str(spec.end_date)) == ("2025-01-01", "2026-01-01")
    assert service.context.pending_clarification == []
```

Also test an ambiguous `Top customers` request after an unrelated analysis, resolved with `By spending in 2025` (customer dimension and revenue ranking); a new complete `Show units by state in 2024` request overrides pending revenue/product constraints. Test `/reports` neither consumes nor becomes a clarification answer. A recording model must observe redacted secrets/tokens only; refusal and scope revocation must leave `pending_clarification == []`. Overflow must produce clarification with no gateway call.

- [ ] **Step 2: Run the new tests red.** Run `.venv/Scripts/python.exe -m pytest tests/test_service.py tests/test_model.py tests/test_security_integration.py -k clarification -q`; expect missing context/plan assertions to fail, not dependency errors.
- [ ] **Step 3: Implement the context lifecycle.** Record only sanitized questions and validated clarification text. Supply unresolved context distinctly from successful history to both planning and report synthesis; the latest complete request takes precedence. Update offline parsing to use pending context only to fill missing information, while retaining its existing limits. Do not infer all-time data, concatenate a year into a product-ID list, or inherit an unrelated successful plan. Clear pending context after successful analysis/refusal and scope reset. Commands continue to route before analytical planning.
- [ ] **Step 4: Run the three affected test modules.** Expect all tests to pass and ordinary follow-ups to preserve their existing behavior.
- [ ] **Step 5: Commit only this task's source/tests.** Message: `fix: retain analytical context across clarification replies`.

### Task 2: Share runtime setup and isolate browser sessions

**Files:** Create `src/retail_agent/runtime.py`, `src/retail_agent/web_session.py`, `tests/test_web_session.py`; modify `src/retail_agent/cli.py` and `src/retail_agent/service.py`.

**Interfaces:**
- `runtime.RuntimeDependencies`: dataclass with `settings: Settings`, `model: AnalyticalModel`, `gateway: Any`, `traces: TraceRecorder`.
- `runtime.build_runtime(actor_id: str, mode: Literal['demo', 'offline', 'live'], settings: Settings | None = None) -> RuntimeDependencies`. Input `settings.data_dir` is the base directory; append the mode once. Validate live configuration without making a cloud call.
- `AnalyticsService.refresh_scope() -> ActorScope`: public policy refresh using the existing invalidation behavior; migrate internal `_scope()` callers consistently.
- `web_session.TranscriptTurn`: `question: str`, `result: TurnResult`. A recorded result has `pending=None`; confirmation secrets stay in `Conversation`.
- `WebSession(actor_id: str = 'analyst_north', mode: Literal['offline', 'live'] = 'offline', settings: Settings | None = None)` exposes `context: Conversation`, `runtime: RuntimeDependencies`, `transcript: list[TranscriptTurn]`, `busy: bool`.
- Methods: `refresh_access() -> ActorScope`, `submit(question: str) -> TurnResult`, `run_command(command: str) -> TurnResult`, `reset(*, actor_id: str | None = None, mode: Literal['offline', 'live'] | None = None) -> None`. `run_command` accepts slash commands only and does not append them to analytical chat.

- [ ] **Step 1: Add failing adapter tests.** Create `make_session` fixtures using unscoped `Settings(data_dir=tmp_path)` and optionally a temporary permissions file. Test separate sessions/actors (`a.context is not b.context`; `b.transcript == []` after `a.submit(...)`), shared persistence for the same actor, and reset clearing customer linkage/pending clarification/transcript. Invoke the same adapter sequentially from two different threads; `submit` must work with no retained SQLite connection. Remove or corrupt the permissions file after successful analysis: `refresh_access()` must raise `ConfigurationError` after clearing cached context and transcript. Test revoked product scope likewise clears evidence. Verify sanitized transcript excludes a synthetic configured key, contact values and both pending/consumed confirmation tokens.

```python
def test_two_sessions_do_not_share_analysis(tmp_path):
    settings = Settings(data_dir=tmp_path)
    a = WebSession(settings=settings)
    b = WebSession(settings=settings)
    assert a.submit('Show revenue in 2025').report is not None
    assert b.transcript == [] and b.context.last_evidence == []
    assert a.context.conversation_id != b.context.conversation_id
```

- [ ] **Step 2: Run `.venv/Scripts/python.exe -m pytest tests/test_web_session.py -q`.** Expect imports/new-interface assertions to fail.
- [ ] **Step 3: Implement the runtime factory and adapter.** Extract CLI construction into `build_runtime`, preserving all three mode directories and CLI behavior. For every action open/close `ReportStore` on the calling thread, construct the service around the session context, and run its async handler synchronously. Reset a busy flag in `finally`; reject overlapping actions. Recheck permissions before submission and rendering. On reset or access failure cancel an outstanding operation with the original actor, then clear display/context even if cleanup fails. Recreate model/gateway on reset. Retain issued tokens privately for session-lifetime redaction, including after reset; no global cache. Keep at most the latest 20 transcript turns. `run_command` rejects ordinary questions so automatic library refresh cannot reach the model.
- [ ] **Step 4: Run `.venv/Scripts/python.exe -m pytest tests/test_web_session.py tests/test_service.py -q`.** Expect PASS, including the existing CLI subprocess demo and missing-live-settings tests.
- [ ] **Step 5: Commit this adapter and CLI integration.** Message: `refactor: share runtime setup with isolated web sessions`.

### Task 3: Expose authorized saved-report reads and safe confirmation actions

**Files:** Modify `src/retail_agent/service.py`, `src/retail_agent/web_session.py`, `src/retail_agent/cli.py`, `src/retail_agent/telemetry.py`; tests in `tests/test_service.py`, `tests/test_security_integration.py`, `tests/test_web_session.py`.

**Interfaces:**
- Add `TurnResult.saved_report: Report | None = None` and `/open REPORT_ID`. It returns only an eligible owned report, revalidates title/body/evidence, and does not replace `context.last_report` or invoke the model. Use the same unavailable message for absent, revoked and foreign IDs.
- `WebSession.list_reports() -> list[dict[str, str]]`, `open_report(report_id: str) -> TurnResult` route through service commands and refreshed scope.
- `DeletionPreview`: dataclass with `operation_id: str`, `targets: tuple[ReportTarget, ...]`, `expires_at: float`; deliberately no token.
- `WebSession.pending_preview -> DeletionPreview | None`, `confirm_delete(operation_id: str) -> TurnResult`, `cancel_delete(operation_id: str) -> TurnResult`. An ID must match the current preview; retrieve the token only inside the adapter.

- [ ] **Step 1: Add failing access and deletion tests.** Save a report through analysis then `/save`, and assert `/open ID` returns identical body/evidence for its owner. Assert `saved_report is None` for another actor, revoked permissions, deleted IDs and unsafe stored content. Open a saved report after another analysis and assert `context.last_report` still identifies the latter. Two same-actor sessions should see newly saved reports but have distinct pending operations; updating/deleting a target through a second `ReportStore` must prevent an old preview from committing. Cover wrong operation ID, expired token, cancel and repeat-confirm with unchanged report count. Assert `not hasattr(session.pending_preview, 'token')` and recorded transcript results contain no pending tokens.

```python
def test_open_report_preserves_the_latest_analysis(service):
    ask(service, 'Show revenue in 2025')
    ask(service, '/save Original report')
    report_id = ask(service, '/reports').reports[0]['id']
    latest = ask(service, 'Show units in 2025').report
    opened = ask(service, f'/open {report_id}')
    assert opened.saved_report.title == 'Original report'
    assert opened.saved_report.evidence['product_ids'] == [1, 2]
    assert service.context.last_report is latest
```

- [ ] **Step 2: Run the affected new tests red.** Run `.venv/Scripts/python.exe -m pytest tests/test_service.py tests/test_security_integration.py tests/test_web_session.py -k 'open_report or preview or confirmation' -q`; inspect the intended missing-behavior failures.
- [ ] **Step 3: Implement the narrow command and adapter methods.** Preserve store ownership/version/expiry transactions. Freeze targets only via existing preview commands; never use a button label, report title or model response as authorization. Superseding/resetting a preview cancels the old pending operation. After a terminal confirmation/cancellation hide its controls. Emit allowlisted read/preview/confirmation metadata, with no body or token. Add `/open` to CLI help and render the validated saved body and evidence.
- [ ] **Step 4: Run `.venv/Scripts/python.exe -m pytest tests/test_reports.py tests/test_service.py tests/test_security_integration.py tests/test_web_session.py -q`.** Expect PASS for existing store semantics and new cross-session cases.
- [ ] **Step 5: Commit.** Message: `feat: expose scoped report reads and web confirmation actions`.

### Task 4: Deliver the runnable Streamlit chat with optional dependencies

**Files:** Create `streamlit_app.py`, `src/retail_agent/streamlit_ui.py`, `tests/test_streamlit_ui.py`, `requirements-ui-lock.txt`; modify `pyproject.toml`, `.github/workflows/ci.yml`, `README.md`.

**Interfaces:**
- `streamlit_ui.main() -> None`; root script imports/calls it. UI imports stay outside CLI/core import paths.
- `st.session_state['retail_session']` holds one `WebSession`. Widget keys: `mode`, `actor_id`, `apply_session`, `new_conversation`, `question`, `example_comparison`.
- Sidebar mode labels `Offline` and `Live`; actor text field defaults to `analyst_north`. Apply changes together through `apply_session`; invalid selection clears old displayed state and shows a safe setup error.

- [ ] **Step 1: Prepare optional dependency resolution.** Add `ui = ['streamlit==1.65.0']`. Generate a separate lock containing `-r requirements-lock.txt` plus every additional pinned transitive package, resolving with the original lock as constraints. Planning dry-run already resolved this version successfully on Python 3.12; actual installation still needs validation. Keep original lock entries unchanged. Install the UI lock and editable package into the isolated execution environment; verify `pip check`.
- [ ] **Step 2: Add failing AppTest checks with temporary runtime storage and fake live dependencies.** Use an absolute root entry-point path and `default_timeout=10`:

```python
at = AppTest.from_file(str(repo_root / 'streamlit_app.py'), default_timeout=10).run()
assert not at.exception
assert at.radio(key='mode').value == 'Offline'
at.chat_input(key='question').set_value('Show revenue by product').run()
at.chat_input(key='question').set_value('2025').run()
assert len(at.dataframe) >= 1
before = len(at.session_state['retail_session'].transcript)
at.run()
assert len(at.session_state['retail_session'].transcript) == before
```

Also assert a recording model/gateway receives zero calls on page load, sidebar application and ordinary reruns; an example click makes exactly one analysis. Missing live settings and fake provider failure produce a safe error, never `at.exception` or raw provider text. Two AppTest instances have independent conversations.

Name the main test `test_chat_clarifies_and_reruns_without_resubmitting`; create its isolated environment with `monkeypatch.setenv('APP_DATA_DIR', str(tmp_path))`. UI-dependent test modules use `pytest.importorskip('streamlit')` before importing UI modules, so the existing CLI-only development installation remains usable; UI-enabled CI must run these tests without skips. Override live dependencies in tests so local credentials cannot cause an accidental cloud call.
- [ ] **Step 3: Run `.venv/Scripts/python.exe -m pytest tests/test_streamlit_ui.py -q`.** Expect absent entry point/widgets or conversation assertions to fail before UI implementation.
- [ ] **Step 4: Implement the entry point and chat shell.** Use native chat widgets, sidebar form, example buttons, spinner, plain validated report rendering and evidence tables. Refresh access before rendering any retained state, halt safely on access failure, and sanitize submitted widget values before retaining them. Example, form and chat actions run only on their own submit event; display loops never call `submit`. Store no secrets in widget keys or callback arguments. Expose no API-key entry field and never auto-submit live example questions.
- [ ] **Step 5: Verify UI tests and installation paths.** Run `tests/test_streamlit_ui.py` and `tests/test_service.py`. Update CI to install the UI lock, include both locks in the cache key, run the full suite with empty cloud credentials, and lint `streamlit_app.py` as well as `src tests`. README adds the exact installation/run commands while preserving the CLI-only path.
- [ ] **Step 6: Commit.** Message: `feat: add optional Streamlit chat interface`.

### Task 5: Add evidence charts and the saved-report library

**Files:** Create `src/retail_agent/streamlit_views.py`, `tests/test_streamlit_views.py`; modify `src/retail_agent/streamlit_ui.py`, `src/retail_agent/service.py`, `tests/test_streamlit_ui.py` and `tests/test_service.py`.

**Interfaces:**
- Evidence dictionaries gain `result_limit: int` and `limit_reached: bool`, calculated from raw gateway row count before suppression; no change to SQL or approval rules.
- `ChartData`: `kind: Literal['line', 'bar']`, `x: str`, `y: str`, `rows: list[dict[str, Any]]`.
- `chart_data(evidence: dict[str, Any], metric: str) -> ChartData | None` accepts one supported dimension and an existing finite numeric metric; empty/multiple-dimension evidence returns `None`.
- `render_result(result: TurnResult, *, key_prefix: str) -> None` and `render_report_library(session: WebSession) -> None` use only approved service outputs.
- Widget keys: `save_title`, `save_report`, `report_id`, `open_report`, `delete_kind`, `delete_mention`, `preview_delete`, plus `confirm_delete:<operation_id>` and `cancel_delete:<operation_id>`; never use a token as a key.

- [ ] **Step 1: Add failing data-shape and UI tests.** Table-drive `chart_data`: unsorted months become chronological with unchanged cell values; categorical labels produce a bar chart; empty/all-suppressed rows, absent metric, nonfinite values, duplicate x labels or multiple dimensions produce no chart. Assert ratio values are copied rather than summed. Test raw result count equal to `spec.limit` sets `limit_reached` even if suppression reduces visible rows. In AppTest save/open a report, then preview/cancel/delete it; reruns between those steps must preserve the report until the confirm click. Include conversation/mention/ID selection, literal wildcard mentions, duplicate titles, expired/stale/superseded previews, and a second session deleting an open report. Scan displayed text for the private token: assert absent.

```python
def test_chart_sorts_months_without_recalculating_ratios():
    evidence = {'columns': ['month', 'average_order_value'], 'rows': [
        {'month': '2025-02', 'average_order_value': 50.0},
        {'month': '2025-01', 'average_order_value': 25.0},
    ]}
    chart = chart_data(evidence, 'average_order_value')
    assert (chart.kind, chart.x, chart.y) == ('line', 'month', 'average_order_value')
    assert chart.rows == list(reversed(evidence['rows']))
```

- [ ] **Step 2: Run `.venv/Scripts/python.exe -m pytest tests/test_streamlit_views.py tests/test_streamlit_ui.py tests/test_service.py -q`.** Expect new view/metadata assertions to fail.
- [ ] **Step 3: Implement evidence presentation.** Select the metric from returned columns, chart each evidence item independently, and always retain the table. Show periods, product scope, synthetic labels, suppression and a possible-truncation notice when `limit_reached` is true. Older saved evidence without this flag uses available `result_limit`, otherwise labels completeness as unknown. Use an expander for plans, evidence IDs and request ID. Render plain report Markdown with unsafe HTML disabled; treat titles as literal text.
- [ ] **Step 4: Implement the library and frozen confirmation panel.** Read current accessible reports on every rerun; re-open the selected report through the service before display. Save only on the form submit; open/delete actions use IDs even when titles duplicate. Show every frozen target and expiry, then explicit confirm/cancel buttons tied to the operation ID. After a terminal outcome refresh the library and clear vanished selections. Token-bearing service results are never rendered or serialized wholesale.
- [ ] **Step 5: Run view, UI, service and adapter modules.** Expect PASS, including the Task 2 access-revocation cases; ordinary rendering must not increment model/query/save counts.
- [ ] **Step 6: Commit.** Message: `feat: present approved evidence and manage saved reports in Streamlit`.

### Task 6: Verify the full demonstration and document actual results

**Files:** Modify `tests/test_analytics.py` only as justified below, `README.md`, `docs/architecture.md`, `docs/evaluation.md` and the two Superpowers documents' status/checklists.

**Interfaces:** Consumes the entry point, CLI, locked installation and behaviors delivered by Tasks 1-5. Produces documented commands and recorded validation outcomes; no new application interface.

- [ ] **Step 1: Diagnose the existing deadline test using `systematic-debugging`.** Reproduce `test_active_job_cancellation_and_remaining_rpc_timeout`. Its observed failure was `0.1000000000003638 <= 0.1`, caused by floating-point subtraction with an effectively unchanged clock sample. Establish this cause before editing. Replace real-time timing in this specific test with a monkeypatched `retail_agent.gateways.time.monotonic`; set a deadline of `100.125` and clock values `100.0`, `100.0625`, `100.125`. Assert exact remaining limits `0.125`, `0.0625`, then `QueryTimeout`; retain the active-job cancellation assertions. Do not alter production timeout behavior or add sleeps/tolerance that hide it.
- [ ] **Step 2: Run that focused test.** Run `.venv/Scripts/python.exe -m pytest tests/test_analytics.py::test_active_job_cancellation_and_remaining_rpc_timeout -q`; expect PASS. Commit this isolated change as `test: make query deadline checks deterministic`.
- [ ] **Step 3: Run final automated checks once on the integrated change.** Run `.venv/Scripts/python.exe -m pytest -q`, `.venv/Scripts/python.exe -m ruff check src tests streamlit_app.py`, `.venv/Scripts/python.exe -m pip check` and `git diff --check`. Expect all tests/checks to pass, with zero skipped UI tests in the UI-enabled CI environment. If changes follow a failure, rerun affected checks before claiming success.
- [ ] **Step 4: Verify installation and inspect the actual UI.** Reproduce README installation in a fresh ignored environment and launch a local, loopback-only Streamlit server. Use the browser tools for the offline sequence: comparison, follow-up, save/open, customer ranking and breakdown, PII refusal, cancel, confirmed deletion, new conversation and actor switch. Verify readable tables, literal titles, complete previews, visible offline mode and no stale content after a scope change. Stop the test server afterward. No real cloud calls are necessary.
- [ ] **Step 5: Update documentation with measured results.** Add the optional UI to the architecture diagram; explain session reset/disconnect, shared saved reports for the same actor and local-only demo identity. Record actual counts, platform, UI walkthrough and remaining semantic/SQL-limit findings in evaluation. Update the spec/plan to reflect completed work only. Commit as `docs: document and verify the Streamlit demonstration`.
- [ ] **Step 6: Obtain the final code review and hand off.** Follow the selected execution skill and `requesting-code-review`/`verification-before-completion`. Address actionable findings, then report changed behavior, exact checks and limitations. Publishing, pushing or merging is not part of this plan.

## Planning evidence and references

- On 8 October 2026, `pip install --dry-run --ignore-installed --report .venv/streamlit-plan-dependencies.json -c requirements-lock.txt streamlit==1.65.0` resolved successfully. This is a compatibility probe, not an installation or runtime test.
- [AppTest API](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest): drive widget changes and explicit reruns with pytest.
- [Session state](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state): retain per-session state across reruns; browser reconnects must not be described as durable conversations.
- [Streamlit threading](https://docs.streamlit.io/develop/concepts/design/multithreading): keep UI execution and store ownership on the current script thread.

## Plan self-review and handoff

- [x] Spec coverage: clarification (1), runtime/isolation (2), report access/confirmation (3), runnable chat/dependencies/CI (4), charts/library/privacy presentation (5), reproducibility and final evidence (6).
- [x] Interface names and types checked across tasks; no service-side Streamlit dependency or persistent SQLite connection.
- [x] Each Review Focus item is assigned explicit behavioral tests; no placeholder implementation steps.
- [x] Documentation and setup are attached to their deliverables; changes outside the approved UI/clarification scope are excluded except the diagnosed pre-existing test failure.
- [ ] User reviews this plan and selects Native or Subagent-driven execution.

Recommended execution: **Native**, with one final independent review. The six tasks share service/session interfaces and form a mostly sequential integration; implementing them in this chat avoids repeated context transfer while retaining a separate final review.
