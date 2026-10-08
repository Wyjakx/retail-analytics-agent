"""Presentation of approved evidence and deliberate saved-report actions."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Literal

import streamlit as st

from .analytics import METRIC_DEFINITIONS
from .service import TurnResult
from .web_session import WebSession


DIMENSIONS = {"month", "state", "country", "category", "product", "customer"}


@dataclass(frozen=True)
class ChartData:
    kind: Literal["line", "bar"]
    x: str
    y: str
    rows: list[dict[str, Any]]


def chart_data(evidence: dict[str, Any], metric: str) -> ChartData | None:
    columns = evidence.get("columns", [])
    dimensions = [column for column in columns if column in DIMENSIONS]
    rows = evidence.get("rows", [])
    if len(dimensions) != 1 or metric not in METRIC_DEFINITIONS or metric not in columns or not rows:
        return None
    dimension = dimensions[0]
    labels = [row.get(dimension) for row in rows]
    if any(not isinstance(label, (str, int)) for label in labels) or len(set(labels)) != len(labels):
        return None
    values = [row.get(metric) for row in rows]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        return None
    selected = [{dimension: row[dimension], metric: row[metric]} for row in rows]
    if dimension == "month":
        selected.sort(key=lambda row: row[dimension])
    return ChartData("line" if dimension == "month" else "bar", dimension, metric, selected)


def render_result(result: TurnResult, *, key_prefix: str) -> None:
    st.text(result.message)
    if result.report:
        st.markdown(result.report.to_markdown(), unsafe_allow_html=False)
    if result.saved_report:
        st.text(result.saved_report.title)
        st.markdown(result.saved_report.body, unsafe_allow_html=False)
    if result.catalog:
        st.json(result.catalog)
    for index, item in enumerate(result.evidence):
        st.subheader(f"Evidence {index + 1}")
        period = item.get("period", {})
        start, end = period.get("start"), period.get("end_exclusive")
        st.caption(f"{start} (inclusive) → {end} (exclusive)" if start and end else "Period: all time")
        products = item.get("product_ids")
        if products is None and result.saved_report:
            products = result.saved_report.evidence.get("product_ids")
        if products:
            st.caption("Products included: " + ", ".join(map(str, products)))
        if item.get("simulated"):
            st.caption("Synthetic demonstration data")
        if item.get("suppressed_groups"):
            st.warning("Small groups were suppressed. Displayed groups may not cover all data.")
        cap = item.get("result_limit")
        if item.get("limit_reached") is True:
            st.warning(f"Result limit ({cap}) reached. Additional groups may be omitted.")
        elif "limit_reached" not in item:
            if cap is not None:
                st.caption(f"Result limit: {cap}. Completeness is unknown for this saved evidence.")
            else:
                st.caption("Completeness is unknown for this saved evidence.")
        rows = item.get("rows", [])
        st.dataframe(rows, hide_index=True, width="stretch")
        if not rows:
            st.info("No eligible rows for this query.")
        metrics = [name for name in item.get("columns", []) if name in METRIC_DEFINITIONS]
        if metrics and len([c for c in item.get("columns", []) if c in DIMENSIONS]) == 1 and rows:
            metric = st.selectbox("Chart metric", metrics, key=f"{key_prefix}:metric:{index}")
            chart = chart_data(item, metric)
            if chart:
                if chart.kind == "line":
                    st.line_chart(chart.rows, x=chart.x, y=chart.y)
                else:
                    st.bar_chart(chart.rows, x=chart.x, y=chart.y)
    if result.request_id or result.plan or result.evidence:
        with st.expander("Analysis details"):
            if result.request_id:
                st.text(f"Request: {result.request_id}")
            if result.plan:
                st.json(result.plan.model_dump(mode="json"))
            for item in result.evidence:
                st.text(f"Evidence: {item.get('evidence_id', 'unavailable')}")


def _library_action(action: str, operation_id: str = "") -> None:
    session = st.session_state.retail_session
    try:
        if action == "save":
            title = st.session_state.save_title.strip()
            st.session_state.save_title = ""
            result = session.run_command(f"/save {title}")
        elif action == "preview":
            kind = st.session_state.delete_kind
            mention = st.session_state.delete_mention.strip()
            st.session_state.delete_mention = ""
            if kind == "Selected report":
                command = f"/delete id {st.session_state.report_id or ''}"
            elif kind == "This conversation":
                command = "/delete conversation"
            elif mention:
                command = f"/delete mention {mention}"
            else:
                st.session_state.library_message = "Enter a literal mention before previewing."
                return
            result = session.run_command(command)
        elif action == "confirm":
            result = session.confirm_delete(operation_id)
        else:
            result = session.cancel_delete(operation_id)
        st.session_state.library_message = result.message
    except Exception:
        st.session_state.library_message = "The report operation could not be completed safely."


def _open_selection() -> None:
    st.session_state.opened_report_id = st.session_state.report_id


def render_report_library(session: WebSession) -> None:
    session.refresh_access()
    if st.session_state.get("library_revision") != session.revision:
        for key in ("opened_report_id", "report_id", "save_title", "delete_mention", "library_message"):
            st.session_state.pop(key, None)
        st.session_state.library_revision = session.revision
    st.subheader("Saved reports")
    st.caption("Reports are shared with other local sessions using the same actor and data source.")
    with st.form("save_report_form"):
        st.text_input("Title for the latest analysis", key="save_title", max_chars=120)
        st.form_submit_button("Save report", key="save_report", on_click=_library_action,
                              args=("save",), disabled=session.context.last_report is None or session.busy)

    reports = session.list_reports()
    by_id = {report["id"]: report for report in reports}
    if st.session_state.get("report_id") not in by_id:
        st.session_state.report_id = None
    if st.session_state.get("opened_report_id") not in by_id:
        st.session_state.pop("opened_report_id", None)
    if message := st.session_state.get("library_message"):
        st.text(message)
    if not reports:
        st.info("No saved reports within current product permissions.")
    st.selectbox("Owned report", list(by_id), index=None, key="report_id",
                 format_func=lambda rid: f"{by_id[rid]['title']} · {rid}")
    st.button("Open report", key="open_report", on_click=_open_selection,
              disabled=not st.session_state.report_id or session.busy)
    if report_id := st.session_state.get("opened_report_id"):
        # Re-read on every rerun. Never retain report bodies in widget/session state.
        opened = session.open_report(report_id)
        if opened.saved_report is None:
            st.session_state.pop("opened_report_id", None)
        render_result(opened, key_prefix=f"saved:{report_id}")

    with st.expander("Delete reports"):
        with st.form("delete_preview_form"):
            st.selectbox("Selection", ["Selected report", "This conversation", "Literal mention"],
                         key="delete_kind")
            st.text_input("Literal mention (for mention selection)", key="delete_mention", max_chars=120)
            st.form_submit_button("Preview deletion", key="preview_delete", on_click=_library_action,
                                  args=("preview",), disabled=session.busy)
        preview = session.pending_preview
        if preview:
            st.warning(f"Confirm deletion of exactly {len(preview.targets)} saved report(s).")
            for target in preview.targets:
                st.text(f"{target.title} · {target.report_id} · version {target.version}")
            expires = datetime.fromtimestamp(preview.expires_at, timezone.utc)
            st.caption(f"Confirmation expires at {expires:%Y-%m-%d %H:%M:%S} UTC.")
            confirm, cancel = st.columns(2)
            confirm.button("Confirm deletion", key=f"confirm_delete:{preview.operation_id}",
                           on_click=_library_action, args=("confirm", preview.operation_id),
                           type="primary", disabled=session.busy)
            cancel.button("Cancel", key=f"cancel_delete:{preview.operation_id}",
                          on_click=_library_action, args=("cancel", preview.operation_id),
                          disabled=session.busy)
