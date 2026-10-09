"""Smoke tests through the actual process, parser, terminal and on-disk state."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


@pytest.fixture
def cli(tmp_path):
    # Never load a developer's credentials, permissions, budgets or runtime data.
    environment = {key: value for key, value in os.environ.items() if not key.startswith((
        "GOOGLE_", "GEMINI_", "BIGQUERY_", "MAX_", "QUERY_TIMEOUT_", "TURN_TIMEOUT_",
        "MIN_GROUP_", "PRODUCT_PERMISSIONS_", "CUSTOMER_PSEUDONYM_",
    ))}
    environment.update(APP_DATA_DIR=str(tmp_path), PYTHONIOENCODING="utf-8", COLUMNS="160")

    def run(*args, commands=""):
        return subprocess.run(
            [sys.executable, "-m", "retail_agent.cli", "--env-file", str(tmp_path / "absent.env"), *args],
            input=commands, capture_output=True, encoding="utf-8", timeout=30,
            env=environment, cwd=Path(__file__).resolve().parents[1],
        )

    return run


def test_interactive_cli_saves_literal_unicode_title_and_resets_conversation(cli, tmp_path):
    title = "Étude [red]été[/red]"
    result = cli("--offline", commands="\n".join([
        "Revenue for product 1 in 2025", f"/save {title}", "/reports",
        "/delete conversation", "yes", "/cancel", "/new", "/save Stale analysis", "/quit", "",
    ]))

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert title in result.stdout
    assert "Type /confirm " in result.stdout and "New conversation:" in result.stdout
    with sqlite3.connect(tmp_path / "offline" / "reports.sqlite3") as database:
        saved = database.execute("SELECT title, evidence_json FROM reports").fetchall()
    # Plain 'yes' and cancellation preserve the report; /new cannot save stale evidence.
    assert len(saved) == 1 and saved[0][0] == title
    assert json.loads(saved[0][1])["queries"][0]["rows"] == [{"revenue": 900.0}]


def test_demo_runs_to_completion_and_confirms_its_preview(cli, tmp_path):
    result = cli("--demo", "--show-plan")
    assert result.returncode == 0, result.stderr
    assert "cust_" in result.stdout and "Deleted 1" in result.stdout
    assert "Traceback" not in result.stderr
    with sqlite3.connect(tmp_path / "demo" / "reports.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_live_startup_without_credentials_exits_with_actionable_error(cli, tmp_path):
    result = cli("--live", "--question", "Revenue all time")
    assert result.returncode == 2
    assert "Set GOOGLE_API_KEY" in result.stdout
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "live" / "reports.sqlite3").exists()
