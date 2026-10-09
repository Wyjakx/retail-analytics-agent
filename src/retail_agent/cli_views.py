"""Readable terminal answers, with complete reports available on demand."""

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .report_formatting import (
    DIMENSION_LABELS, METRIC_LABELS, format_metric, period_label, total_row, without_citations,
)
from .service import TurnResult


def _label(column: str) -> str:
    return {**DIMENSION_LABELS, **METRIC_LABELS}.get(column, column.replace("_", " ").capitalize())


def _cell(column: str, value) -> str:
    return format_metric(column, value) if column in METRIC_LABELS else str(value)


def _table(console: Console, item: dict, *, detailed: bool) -> None:
    columns, rows = item["columns"], item["rows"]
    if not rows:
        console.print("No eligible rows for this query.", style="yellow", markup=False)
        return
    labels = [_label(column) for column in columns]
    # Stack wide records rather than folding many headings and customer references
    # into narrow columns. No aggregation or display-row cap is applied here.
    width_needed = sum(max(len(label), *(len(_cell(column, row.get(column, "")))
                                         for row in rows))
                       for label, column in zip(labels, columns)) + 3 * len(columns) + 1
    is_total = total_row([item]) is not None
    if not detailed and (is_total or width_needed > console.width):
        for index, row in enumerate(rows):
            table = Table(box=box.SIMPLE, header_style="bold cyan", padding=(0, 1))
            table.add_column("Metric" if is_total else "Field", overflow="fold")
            table.add_column("Value", overflow="fold")
            for column, label in zip(columns, labels):
                table.add_row(Text(label), Text(_cell(column, row.get(column, "")), style="bold"))
            if not is_total and len(rows) > 1:
                console.print(f"Row {index + 1}", style="bold", markup=False)
            console.print(table)
        return
    table = Table(box=box.SIMPLE, header_style="bold cyan", padding=(0, 1))
    for column, label in zip(columns, labels):
        table.add_column(column if detailed else label, overflow="fold",
                         justify="right" if column in METRIC_LABELS else "left")
    for row in rows:
        table.add_row(*(Text(str(row.get(column, "")) if detailed else
                            _cell(column, row.get(column, ""))) for column in columns))
    console.print(table)


def _evidence(console: Console, result: TurnResult, *, detailed: bool) -> None:
    for index, item in enumerate(result.evidence):
        if len(result.evidence) > 1:
            console.rule(f"Result {index + 1}", style="cyan", align="left")
        console.print("Period: " + period_label(item), style="dim", markup=False)
        products = item.get("product_ids")
        if products is None and result.saved_report:
            products = result.saved_report.evidence.get("product_ids")
        if products:
            console.print("Products: " + ", ".join(map(str, products)), style="dim", markup=False)
        if item.get("simulated"):
            console.print("Synthetic demonstration data", style="yellow", markup=False)
        if item.get("suppressed_groups"):
            console.print("Small groups were suppressed. Displayed groups may not cover all data.",
                          style="yellow", markup=False)
        if item.get("limit_reached") is True:
            console.print(f"Result limit ({item.get('result_limit')}) reached; additional groups may be omitted.",
                          style="yellow", markup=False)
        elif "limit_reached" not in item:
            console.print("Completeness is unknown for this evidence.", style="yellow", markup=False)
        if detailed:
            console.print(f"Source: {item.get('evidence_id', 'Unavailable')}", style="dim", markup=False)
        _table(console, item, detailed=detailed)
    if not detailed and any(set(item.get("columns", [])) &
                            {"revenue", "average_order_value", "spend_per_customer"}
                            for item in result.evidence):
        console.print("Amounts use dataset price units; no currency is assumed.", style="dim", markup=False)


def present(console: Console, result: TurnResult, show_plan: bool = False) -> None:
    if result.report and result.report_fallback:
        console.print("Showing verified results; the generated explanation is unavailable.",
                      style="yellow", markup=False)
    if result.report and not show_plan:
        console.print(Panel(Text(without_citations(result.report.summary, result.evidence)),
                            title="Answer", title_align="left", border_style="cyan", padding=(1, 2)))
    else:
        console.print(result.message, markup=False)
        if result.report:
            console.print(Markdown(result.report.to_markdown()))
    if result.saved_report:
        console.print(result.saved_report.title, markup=False)
        console.print(Markdown(result.saved_report.body))
    if result.catalog:
        table = Table("Table", "Approved columns", box=box.SIMPLE, header_style="bold cyan")
        for name, columns in result.catalog.items():
            table.add_row(Text(name), Text(", ".join(columns)))
        console.print(table)
    _evidence(console, result, detailed=show_plan)
    if result.report and not show_plan:
        if total_row(result.evidence) is None and result.report.findings:
            console.print("Key observations", style="bold cyan")
            for finding in result.report.findings:
                console.print("• " + without_citations(finding, result.evidence), markup=False)
        if result.report.action_items:
            console.print("Suggested next steps", style="bold cyan")
            for action in result.report.action_items:
                console.print("• " + without_citations(action, result.evidence), markup=False)
        # Long application-owned definitions are in /explain. Keep other caveats,
        # including model-specific limitations and the summary-subset notice.
        notes = [note for note in result.report.caveats if not note.startswith((
            "Definitions: ", "Product scope: ", "Amounts use the dataset's price units;",
        ))]
        if notes:
            console.print("Notes", style="bold cyan")
            for note in notes:
                console.print("• " + without_citations(note, result.evidence), markup=False)
        console.print(Text.assemble(
            "\n", ("/explain", "bold bright_cyan"), "  Full report, definitions and sources    ",
            ("/save TITLE", "bold bright_cyan"), "  Save report",
        ))
    if result.reports:
        table = Table(header_style="bold cyan")
        table.add_column("Report ID", overflow="fold")
        table.add_column("Title", overflow="fold")
        for report in result.reports:
            table.add_row(Text(report["id"]), Text(report["title"]))
        console.print(table)
    if result.pending:
        table = Table()
        table.add_column("Report ID", overflow="fold")
        table.add_column("Exact title", overflow="fold")
        table.add_column("Version")
        for target in result.pending.targets:
            table.add_row(Text(target.report_id), Text(target.title), str(target.version))
        console.print(table)
        console.print(f"Type /confirm {result.pending.token} to delete this frozen selection.", markup=False)
        console.print("The confirmation expires after five minutes. /cancel leaves reports intact.", markup=False)
    if show_plan and result.plan:
        console.print_json(result.plan.model_dump_json(indent=2))
    if show_plan and result.request_id:
        console.print(f"Request: {result.request_id}", style="dim", markup=False)
