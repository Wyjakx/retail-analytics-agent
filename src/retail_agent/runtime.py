"""Shared dependency construction for CLI and per-browser sessions."""

from dataclasses import dataclass, replace
from typing import Any, Literal

from .config import ConfigurationError, Settings, load_pseudonym_key, resolve_scope
from .gateways import BigQueryGateway, OfflineGateway
from .model import AnalyticalModel, GeminiModel, OfflineModel
from .pseudonyms import CustomerPseudonymizer
from .telemetry import TraceRecorder


@dataclass
class RuntimeDependencies:
    settings: Settings
    model: AnalyticalModel
    gateway: Any
    traces: TraceRecorder


def build_runtime(
    actor_id: str, mode: Literal["demo", "offline", "live"], settings: Settings | None = None,
) -> RuntimeDependencies:
    if mode not in ("demo", "offline", "live"):
        raise ConfigurationError("Choose offline or live mode.")
    base = settings if settings is not None else Settings.load()
    configured = replace(base, data_dir=base.data_dir / mode)
    resolve_scope(actor_id, configured.permissions_file)
    if mode == "live":
        configured.validate_live()
    pseudonyms = CustomerPseudonymizer(load_pseudonym_key(configured))
    if mode == "live":
        model = GeminiModel(configured.model, timeout_seconds=configured.query_timeout,
                            max_retries=configured.max_model_retries)
        gateway = BigQueryGateway(
            configured.dataset, configured.project, configured.query_timeout,
            configured.max_query_bytes, pseudonymizer=pseudonyms,
        )
    else:
        model, gateway = OfflineModel(), OfflineGateway(pseudonymizer=pseudonyms)
    return RuntimeDependencies(
        configured, model, gateway, TraceRecorder(configured.data_dir / "events.jsonl"),
    )
