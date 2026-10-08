"""Thin Streamlit client for the same application service used by the CLI."""

import streamlit as st

from .web_session import WebSession


EXAMPLE = "Compare revenue and spend per customer by state in January versus February 2025"
SETUP_ERROR = "Session unavailable. Check the actor, product permissions and local live settings."
ACTION_ERROR = "The operation could not be completed. Please try again."


def _apply_session() -> None:
    st.session_state.pop("action_error", None)
    try:
        actor = st.session_state.actor_id.strip()
        mode = st.session_state.mode.lower()
        session = st.session_state.get("retail_session")
        if session is None:
            st.session_state.retail_session = WebSession(actor, mode)
        else:
            session.reset(actor_id=actor, mode=mode)
    except Exception:
        st.session_state.action_error = SETUP_ERROR


def _new_conversation() -> None:
    st.session_state.pop("action_error", None)
    try:
        st.session_state.retail_session.reset()
    except Exception:
        st.session_state.action_error = SETUP_ERROR


def _submit(question: str) -> None:
    st.session_state.pop("action_error", None)
    try:
        with st.spinner("Analysing authorized data…"):
            st.session_state.retail_session.submit(question)
    except Exception:
        st.session_state.action_error = ACTION_ERROR


def _on_question() -> None:
    question = st.session_state.question
    # Callbacks run before the next script pass; never retain raw input in widgets.
    st.session_state.question = None
    if question:
        _submit(question)


def main() -> None:
    st.set_page_config(page_title="Retail Analytics", page_icon="📊", layout="wide")
    st.title("Retail Analytics")
    st.caption("Ask a business question. Inspect the evidence. Keep the report.")
    if "retail_session" not in st.session_state:
        try:
            st.session_state.retail_session = WebSession()
        except Exception:
            st.session_state.action_error = SETUP_ERROR

    with st.sidebar:
        st.header("Session")
        with st.form("session_settings"):
            st.radio("Data source", ["Offline", "Live"], key="mode")
            st.text_input("Demo actor", value="analyst_north", key="actor_id")
            st.form_submit_button("Apply session", key="apply_session", on_click=_apply_session)
        st.caption("Demo actors illustrate product permissions; this is not a sign-in system.")
        st.button("New conversation", key="new_conversation", on_click=_new_conversation)

    session = st.session_state.get("retail_session")
    try:
        if session is None:
            raise ValueError("Session unavailable")
        scope = session.refresh_access()
    except Exception:
        st.error(SETUP_ERROR)
        st.stop()

    if session.mode == "offline":
        st.info("Offline · synthetic data for January and February 2025. No cloud calls.")
    else:
        st.info("Live · Gemini and BigQuery. Submitting a question can incur usage costs.")
    st.caption(f"Actor: {session.actor_id} · Authorized products: "
               + ", ".join(map(str, scope.allowed_product_ids)))
    if error := st.session_state.get("action_error"):
        st.error(error)
    st.button("Compare January and February 2025", key="example_comparison",
              on_click=_submit, args=(EXAMPLE,), disabled=session.busy)

    for turn in session.transcript:
        with st.chat_message("user"):
            st.text(turn.question)
        with st.chat_message("assistant"):
            st.text(turn.result.message)
            if turn.result.report:
                st.markdown(turn.result.report.to_markdown(), unsafe_allow_html=False)
            for evidence in turn.result.evidence:
                st.dataframe(evidence["rows"], hide_index=True)
    st.chat_input("Ask about revenue, orders or customers…", key="question",
                  on_submit=_on_question, disabled=session.busy)
