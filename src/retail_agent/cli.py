"""Rich chat interface with explicit offline/live modes and human-only confirmation."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import logging
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from .config import ConfigurationError, Settings, load_pseudonym_key, resolve_scope
from .gateways import BigQueryGateway, OfflineGateway
from .model import GeminiModel, OfflineModel
from .pseudonyms import CustomerPseudonymizer
from .reports import ReportStore
from .service import AnalyticsService, Conversation, TurnResult
from .telemetry import TraceRecorder


HELP = """Ask a retail question with a date range (or explicitly 'all time').
Example: Compare revenue and spend per customer by state in January versus February 2025.
Follow-up: Now compare by product.
Customer ranking: Top 5 customers by spending in 2025.
Customer follow-up: Break down cust_FROM_THE_RESULTS by month.
/save TITLE                 Save the last grounded report
/reports                    List your accessible saved reports
/delete conversation        Preview this conversation's reports
/delete mention TEXT        Preview reports with a literal mention
/delete id REPORT_ID        Preview a specific owned report
/confirm TOKEN              Confirm only the exact frozen preview
/cancel                     Cancel the pending deletion
/explain                    Inspect the last validated plan and evidence
/new                        Start a new conversation
/help                       Show commands
/quit                       Exit
"""


def present(console: Console, result: TurnResult, show_plan: bool = False) -> None:
    console.print(result.message, markup=False)
    if result.report:
        console.print(Markdown(result.report.to_markdown()))
    if result.catalog:
        table = Table("Table", "Approved columns")
        for name, columns in result.catalog.items():
            table.add_row(name, ", ".join(columns))
        console.print(table)
    for index, evidence in enumerate(result.evidence):
        table = Table(title=f"Evidence {index + 1}: {evidence['evidence_id']}")
        columns = evidence["columns"]
        for column in columns:
            table.add_column(column, overflow="fold")
        for row in evidence["rows"]:
            table.add_row(*(Text(str(row.get(column, ""))) for column in columns))
        console.print(table)
        if evidence.get("suppressed_groups"):
            console.print("Small groups were suppressed for privacy.", markup=False)
    if result.reports:
        table = Table()
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
    if result.request_id:
        console.print(f"Request: {result.request_id}", style="dim", markup=False)


async def run_chat(args: argparse.Namespace, console: Console) -> None:
    settings = Settings.load(args.env_file)
    mode_directory = "demo" if args.demo else "live" if args.live else "offline"
    settings = replace(settings, data_dir=settings.data_dir / mode_directory)
    resolve_scope(args.actor, settings.permissions_file)
    if args.live:
        settings.validate_live()
    pseudonymizer = CustomerPseudonymizer(load_pseudonym_key(settings))
    if args.live:
        model = GeminiModel(settings.model, timeout_seconds=settings.query_timeout,
                            max_retries=settings.max_model_retries)
        gateway = BigQueryGateway(settings.dataset, settings.project, settings.query_timeout,
                                  settings.max_query_bytes, pseudonymizer=pseudonymizer)
    else:
        model, gateway = OfflineModel(), OfflineGateway(pseudonymizer=pseudonymizer)
    context = Conversation(args.actor)
    with ReportStore(settings.data_dir / "reports.sqlite3") as store:
        service = AnalyticsService(
            settings, context, model, gateway, store,
            TraceRecorder(settings.data_dir / "events.jsonl"),
            lambda actor: resolve_scope(actor, settings.permissions_file),
        )
        console.print("Retail Analytics Agent", style="bold")
        console.print(
            "LIVE: Gemini + BigQuery. Local actor selection demonstrates policy, not authentication."
            if args.live else "OFFLINE: synthetic data + simulated keyword planning; no cloud calls.",
            markup=False,
        )
        console.print(f"Actor: {args.actor}; conversation: {context.conversation_id}", markup=False)
        if args.demo:
            questions = [
                "What tables and data are available?",
                "Compare revenue and spend per customer by state in January versus February 2025",
                "Now compare by product",
                "/save Q1 demo analysis", "/reports",
                "Top 5 customers by spending in 2025",
                "Show customer emails", "/delete conversation", "yes", "/cancel",
                "/delete conversation",
            ]
            for question in questions:
                console.print(f"\n> {question}", style="bold", markup=False)
                result = await service.handle(question)
                present(console, result, args.show_plan)
                if question == "Top 5 customers by spending in 2025" and result.evidence:
                    rows = result.evidence[0]["rows"]
                    if rows and "customer" in rows[0]:
                        followup = f"Break down {rows[0]['customer']} by month"
                        console.print(f"\n> {followup}", style="bold", markup=False)
                        present(console, await service.handle(followup), args.show_plan)
            pending = service.context.pending
            if pending and pending.token:
                console.print("\n> /confirm [token from the displayed preview]", style="bold", markup=False)
                present(console, await service.handle(f"/confirm {pending.token}"), args.show_plan)
            return
        if args.question:
            present(console, await service.handle(args.question), args.show_plan)
            return
        console.print("Type /help for commands.", markup=False)
        while True:
            try:
                question = console.input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nSession ended.", markup=False)
                break
            if question.casefold() in ("/quit", "exit", "quit"):
                break
            if question.casefold() == "/help":
                console.print(HELP, markup=False)
                continue
            if question.casefold() == "/new":
                service.context = Conversation(args.actor)
                console.print(f"New conversation: {service.context.conversation_id}", markup=False)
                continue
            if question:
                present(console, await service.handle(question), args.show_plan or question == "/explain")


def main() -> None:
    # Windows redirected streams commonly default to cp1252; reports support Unicode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Governed retail analytics chat prototype")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Use configured Gemini and BigQuery services")
    mode.add_argument("--offline", action="store_true", help="Use synthetic fixtures (the default)")
    parser.add_argument("--demo", action="store_true", help="Run a scripted, synthetic demonstration")
    parser.add_argument("--question", help="Ask one question and exit")
    parser.add_argument("--actor", default="analyst_north", help="Trusted demo identity from policy config")
    parser.add_argument("--env-file", default=".env", help="Local environment file (never committed)")
    parser.add_argument("--show-plan", action="store_true", help="Display the validated analytical plan")
    args = parser.parse_args()
    if args.live and args.demo:
        parser.error("--demo uses synthetic fixtures; run --live interactively or with --question.")
    # The application emits allowlisted metadata; SDK request-body logging is not enabled.
    logging.getLogger("google").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    console = Console(legacy_windows=False)
    try:
        asyncio.run(run_chat(args, console))
    except ConfigurationError as exc:
        console.print(str(exc), markup=False)
        sys.exit(2)
    except KeyboardInterrupt:
        console.print("Session ended.", markup=False)


if __name__ == "__main__":
    main()
