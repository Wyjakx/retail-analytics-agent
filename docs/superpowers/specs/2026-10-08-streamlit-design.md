# Streamlit interface for the retail analytics prototype

Status: approved by the user on 8 October 2026; [implementation plan](../plans/2026-10-08-streamlit-interface.md) prepared and awaiting review/execution-method selection.

## Purpose and agreed scope

The user wants to use Superpowers to develop the small Streamlit interface discussed in this chat. The purpose is to make the existing retail analytics prototype easier to demonstrate to nontechnical executives. The assignment still requires the CLI, which remains supported.

The proposed interface covers chat, approved evidence, simple charts, saved reports and explicit deletion confirmation. It reuses the existing application service and its policy controls. The clarification-context defect found during the repository review is included because it directly affects the chat experience.

Assumptions: this is a local demonstration; interface labels remain in English to match the existing CLI and assignment; offline mode is the default. Selecting a demo actor is visibly described as a policy demonstration. Deployment, production authentication, Golden retrieval, preference learning and persona administration remain outside this change.

Success means a reviewer can start the app, ask an analytical question, answer a clarification, follow up on results, inspect evidence, save and reopen a report, and cancel or confirm a deletion. These actions must preserve the existing product permissions and privacy controls.

## Approach

Use Streamlit as a presentation layer directly over `AnalyticsService`.

An HTTP API with a separate frontend would introduce another process and a new transport/authentication boundary. It is unnecessary for this local assignment. A mock-only UI would demonstrate presentation but would not exercise the implemented report and authorization flows. The direct service adapter supports both offline demonstrations and the existing configured Gemini/BigQuery mode.

No independent SQL, model prompts, authorization decisions or destructive-operation implementation belongs in the UI.

## User experience

- Sidebar: explicit Offline/Live mode, configured demo actor, current product scope and New conversation.
- Chat: retained messages, example-question buttons, a busy indicator and clear safe error messages. Example buttons submit only on a user click.
- Results: report text, approved evidence tables and optional charts. An expander shows the validated plan, evidence IDs, periods, scope and request ID.
- Saved reports: list accessible owned reports, open one, and save the latest report with a title. A saved report includes its body and retained evidence.
- Deletion: select an owned report, reports from this conversation, or a literal mention; show the exact frozen selection; then show separate Confirm deletion and Cancel actions.

The confirmation button uses the existing operation/token held privately in the session. A preview or normal Streamlit rerun cannot delete anything. The token does not appear in chat, model input, diagnostics or ordinary widget keys. Expiry, changed reports and replay keep the existing store semantics.

A new conversation cancels any pending confirmation and clears chat, analytical context and customer linkage. Changing actor or mode does the same and reloads accessible reports. Each rerun checks current permissions before displaying previous evidence, reports or charts; a changed or unavailable scope clears sensitive cached presentation state and requires a fresh analysis.

Raw question text is not retained in the UI transcript. Retained user messages use the application's redaction rules, including confirmation-token removal. Refused sensitive requests can use a neutral transcript label. Provider exception bodies are never displayed.

## Components and lifecycle

1. A small root `streamlit_app.py` provides the documented Streamlit entry point.
2. A package UI module renders widgets and translates deliberate user actions into service calls.
3. A presentation-independent session adapter owns the actor, mode, `Conversation`, model, gateway and sanitized display history for one browser session.
4. A shared construction helper reuses settings, mode-specific runtime paths, scope resolution and model/gateway selection across the CLI and web adapter where practical.
5. The existing service exposes a narrow owned-report read operation, applying the same current-product eligibility checks as listing. The UI never reads SQLite directly.

The adapter is stored in per-browser `st.session_state`, never in a global resource cache. Mutable conversations, model budgets, customer linkage and pending confirmations cannot be shared across browser sessions.

Open a `ReportStore` inside each synchronous action on the current script thread, construct the service with the session's context, handle the action, and close the store. Do not keep a SQLite connection across reruns or disable SQLite's thread checks. No event loop or asynchronous task survives a rerun. One deliberate action is processed at a time; model clients retain their existing per-call lifecycle.

Existing `APP_DATA_DIR/offline` and `APP_DATA_DIR/live` persistence is reused. CLI and web sessions for the same demo actor may see the same saved reports. Separate actors remain isolated by ownership and current entitlements.

## Clarification handling

Preserve a bounded, sanitized pending analytical request when the planner returns `clarify`. Pass the pending request and its clarification exchange to subsequent planning. This state is separate from the last successful analysis so a clarification cannot accidentally reuse a different report's context.

Gemini receives this context through its existing planning contract. The offline planner must also resolve the supported short-answer flow, rather than merely recording history it never reads. At minimum, `Show revenue by product` followed by `2025` must produce that requested analysis.

Use the existing input-size, redaction and bounded-history limits. Consume pending clarification after a successful analysis; replace it for a clearly new analytical request; reset it on refusal, actor/mode change, new conversation or permission invalidation. Report commands must not be interpreted as clarification answers. If intent remains ambiguous, ask again rather than silently expanding the query.

## Charts and report presentation

Charts use approved evidence rows only. For a single month dimension, allow a chronological line chart; for one categorical dimension, allow a bar chart. Select only numerical metrics already returned by the service. Customer references remain opaque labels.

Do not sum ratios, merge unrelated query periods, infer missing rows, invent percentage changes or claim causality. For multidimensional evidence, show the table unless an unambiguous chart is available. Label scope, period, simulation and suppression. When a result reaches its configured row cap, explain that additional groups may be omitted; do not label the evidence as a complete population.

Keep the existing text report fallback. Improving general semantic verification and moving group suppression before the SQL limit are separate findings from the repository review, outside this UI change.

## Dependencies and documentation

Add Streamlit as an optional UI dependency and document a reproducible installation and `python -m streamlit run streamlit_app.py` launch. Preserve the existing CLI installation path and command. Pin a compatible UI dependency set without silently upgrading the already frozen Gemini/BigQuery stack.

Document offline and live startup, session lifetime, demo identity limitations, shared saved-report storage, and a short demonstration script. Cloud settings are read using the existing configuration; the page does not collect or display API keys. Missing live settings result in a clear setup message, with no automatic model or query call on page load.

## Verification and acceptance

- First reproduce clarification failure, then add a regression proving the complete question/short-answer flow in the service and offline planner.
- Exercise two sessions and two actors: no conversation, evidence, customer-reference or confirmation leakage; current permissions govern reopening reports and rerendering history.
- Verify save, list, open, preview, cancel and confirm through the adapter, including expiration, stale selection, replay and changes made by another session.
- Verify repeated rendering, example buttons and mode changes do not rerun paid analyses or commit deletions.
- Use Streamlit's application test facilities with offline data/fake dependencies for the UI. Inspect the rendered local page for readable layout, chat history, evidence and confirmation flow.
- Run the existing test suite, new targeted tests, Ruff and dependency checks. Retain the CLI demo smoke test.
- Record the existing floating-point deadline assertion failure from the audit separately; diagnose it before changing its expectation, and do not weaken runtime timeout controls to obtain a pass.
- Default verification uses no cloud calls. Any live validation must be clearly identified separately from synthetic results.

## Superpowers workflow state

- [x] Read the assignment, existing code, documentation and prior review findings.
- [x] Classify the new web interface as an architectural addition.
- [x] Carry the discussed minimal scope into this written design and compare implementation approaches.
- [x] Review this specification for scope, contradictions and unresolved placeholders.
- [x] Obtain user review of the written specification.
- [x] Prepare the implementation plan using `writing-plans`.
- [ ] Obtain plan review and select the execution method.
- [ ] Implement with regression tests, code review and final verification.
