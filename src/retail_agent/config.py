"""Local configuration. Demo identities are policy fixtures, not authentication."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    pass


def _positive(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive integer.") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be an integer of at least {minimum}.")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("runtime")
    dataset: str = "bigquery-public-data.thelook_ecommerce"
    project: str | None = None
    model: str | None = None
    query_timeout: int = 30
    turn_timeout: int = 120
    max_queries: int = 3
    max_query_bytes: int = 100_000_000
    max_turn_bytes: int = 300_000_000
    max_corrections: int = 1
    max_model_retries: int = 2
    min_group_customers: int = 3
    permissions_file: Path | None = None

    @classmethod
    def load(cls, env_file: str | None = ".env") -> Settings:
        if env_file:
            load_dotenv(env_file, override=False)
        settings = cls(
            data_dir=Path(os.environ.get("APP_DATA_DIR", "runtime")),
            dataset=os.environ.get("BIGQUERY_DATASET", cls.dataset),
            project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None,
            model=os.environ.get("GEMINI_MODEL") or None,
            query_timeout=_positive("QUERY_TIMEOUT_SECONDS", 30),
            turn_timeout=_positive("TURN_TIMEOUT_SECONDS", 120),
            max_queries=_positive("MAX_ANALYSIS_QUERIES", 3),
            max_query_bytes=_positive("BIGQUERY_MAX_BYTES_BILLED", 100_000_000),
            max_turn_bytes=_positive("BIGQUERY_MAX_TURN_BYTES_BILLED", 300_000_000),
            max_corrections=_positive("MAX_PLAN_CORRECTIONS", 1, 0),
            max_model_retries=_positive("MAX_MODEL_RETRIES", 2, 0),
            min_group_customers=_positive("MIN_GROUP_CUSTOMERS", 3),
            permissions_file=(
                Path(os.environ["PRODUCT_PERMISSIONS_FILE"])
                if os.environ.get("PRODUCT_PERMISSIONS_FILE") else None
            ),
        )
        if settings.max_queries > 3 or settings.max_corrections > 1:
            raise ConfigurationError("Prototype caps: at most 3 queries and 1 correction cycle.")
        if settings.max_model_retries > 2:
            raise ConfigurationError("Prototype cap: at most 2 transient model retries.")
        if settings.min_group_customers < 3:
            raise ConfigurationError("MIN_GROUP_CUSTOMERS must be at least 3.")
        return settings

    def validate_live(self) -> None:
        if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
            raise ConfigurationError("Set GOOGLE_API_KEY locally before using --live.")
        if not self.model:
            raise ConfigurationError("Set GEMINI_MODEL to an available Gemini model before --live.")
        if not self.project:
            raise ConfigurationError("Set GOOGLE_CLOUD_PROJECT to a BigQuery billing project.")


DEMO_PERMISSIONS = {"analyst_north": [1, 2], "analyst_south": [3, 4]}


def resolve_scope(actor_id: str, permissions_file: Path | None = None):
    from retail_agent.analytics import ActorScope

    permissions = DEMO_PERMISSIONS
    if permissions_file:
        try:
            permissions = json.loads(permissions_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigurationError("Cannot read the configured product permissions file.") from exc
    if not isinstance(permissions, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", actor_id):
        raise ConfigurationError("Invalid demo actor or permissions configuration.")
    products = permissions.get(actor_id)
    if (
        not isinstance(products, list) or not products or len(products) > 1000
        or any(type(item) is not int or item <= 0 for item in products)
    ):
        raise ConfigurationError("The demo actor must have explicitly assigned positive product IDs.")
    return ActorScope(actor_id=actor_id, allowed_product_ids=tuple(sorted(set(products))))
