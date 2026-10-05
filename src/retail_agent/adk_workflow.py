"""Bounded, typed ADK 2 model stages. No database or deletion tools are exposed."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import json
import logging
import time
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

Output = TypeVar("Output", bound=BaseModel)


class ModelFailure(RuntimeError):
    """A stable error code, deliberately excluding provider response bodies."""

    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _silence_provider_logs() -> None:
    # ADK debug logs include prompts; runner failures can include raw SDK bodies.
    # The application emits its own redacted stage metrics instead.
    prefixes = ("google_adk", "google_genai", "google.genai", "httpx", "httpcore")
    for name in prefixes:
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False
        if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
            logger.addHandler(logging.NullHandler())
    for name, logger in tuple(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger) and name.startswith(prefixes):
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False


def _classify_failure(error: Exception) -> ModelFailure:
    if isinstance(error, ModelFailure):
        return error
    if isinstance(error, ValidationError):
        return ModelFailure("model_invalid_output")
    if isinstance(error, TimeoutError):
        return ModelFailure("model_timeout", retryable=True)
    # Never inspect/stringify messages: they may contain user input or credentials.
    status = getattr(error, "code", None) or getattr(error, "status_code", None)
    if status in (408, 429, 500, 502, 503, 504):
        return ModelFailure("model_temporarily_unavailable", retryable=True)
    if status in (401, 403):
        return ModelFailure("model_access_denied")
    if isinstance(error, (ConnectionError, OSError)):
        return ModelFailure("model_temporarily_unavailable", retryable=True)
    return ModelFailure("model_unavailable")


class AdkTypedRunner:
    """One ADK graph per stage: Gemini typed output -> Python validation.

    SDK retries are disabled so the explicit retry budget is the whole budget.
    Each stage uses a fresh in-memory session containing only approved context.
    The application stores conversation state and supplies sanitized history.
    """

    def __init__(
        self,
        model_name: str,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 2,
        max_output_tokens: int = 2048,
        model_override: Any = None,
    ):
        if not model_name or timeout_seconds <= 0 or not 0 <= max_retries <= 2:
            raise ValueError("Invalid model configuration")
        if not 128 <= max_output_tokens <= 8192:
            raise ValueError("max_output_tokens must be between 128 and 8192")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self.model_override = model_override
        self.last_metadata: dict[str, Any] = {}
        self.reset_budget()

    def reset_budget(self, max_calls: int = 6) -> None:
        """Start a turn budget shared by planning, correction, and reporting."""
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        self._remaining_calls = max_calls
        self.turn_model_calls = 0

    async def run(
        self,
        stage: str,
        instruction: str,
        payload: dict[str, Any],
        output_schema: type[Output],
    ) -> Output:
        metadata: dict[str, Any] = {
            "stage": stage,
            "model_calls": 0,
            "retries": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "status": "started",
        }
        self.last_metadata = metadata
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        if len(serialized) > 60_000:
            metadata["status"] = "model_input_too_large"
            raise ModelFailure("model_input_too_large")
        deadline = time.monotonic() + self.timeout_seconds
        for attempt in range(self.max_retries + 1):
            if self._remaining_calls <= 0:
                metadata["status"] = "model_call_budget_exceeded"
                raise ModelFailure("model_call_budget_exceeded")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                metadata["status"] = "model_timeout"
                raise ModelFailure("model_timeout")
            try:
                async with asyncio.timeout(remaining):
                    result = await self._run_once(
                        stage, instruction, serialized, output_schema, metadata
                    )
                metadata["status"] = "success"
                return result
            except asyncio.CancelledError:
                metadata["status"] = "cancelled"
                raise
            except Exception as error:
                failure = _classify_failure(error)
                metadata["status"] = failure.code
                if not failure.retryable or attempt == self.max_retries:
                    raise failure from None
                delay = min(0.25 * (2**attempt), max(0, deadline - time.monotonic()))
                if delay <= 0:
                    raise ModelFailure("model_timeout") from None
                metadata["retries"] += 1
                await asyncio.sleep(delay)
        raise ModelFailure("model_unavailable")

    async def _run_once(
        self,
        stage: str,
        instruction: str,
        serialized: str,
        output_schema: type[Output],
        metadata: dict[str, Any],
    ) -> Output:
        # Lazy imports keep the offline simulator free of API initialization.
        from google import genai
        from google.adk.models.google_llm import Gemini
        from google.genai import types

        _silence_provider_logs()
        model = self.model_override
        owned_client = None
        if model is None:
            owned_client = genai.Client(
                http_options=types.HttpOptions(
                    timeout=int(self.timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            model = Gemini(
                model=self.model_name,
                retry_options=types.HttpRetryOptions(attempts=1),
                client=owned_client,
            )
        try:
            return await self._run_graph(
                model, stage, instruction, serialized, output_schema, metadata
            )
        finally:
            if owned_client is not None:
                await owned_client.aio.aclose()
                owned_client.close()

    async def _run_graph(
        self, model: Any, stage: str, instruction: str, serialized: str,
        output_schema: type[Output], metadata: dict[str, Any],
    ) -> Output:
        from google.adk import Agent, Event, Runner, Workflow
        from google.adk.agents.run_config import RunConfig
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        def count_call(callback_context: Any, llm_request: Any) -> None:
            # Count actual provider attempts, including failed attempts. The
            # second check also protects against concurrent stage entry.
            if self._remaining_calls <= 0:
                raise ModelFailure("model_call_budget_exceeded")
            self._remaining_calls -= 1
            self.turn_model_calls += 1
            metadata["model_calls"] += 1

        agent = Agent(
            name=f"{stage}_model",
            model=model,
            instruction=instruction,
            mode="single_turn",
            include_contents="none",
            tools=[],
            output_schema=output_schema,
            generate_content_config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=self.max_output_tokens,
            ),
            before_model_callback=count_call,
        )

        def validate_output(node_input: Any) -> Event:
            if isinstance(node_input, str):
                parsed = output_schema.model_validate_json(node_input)
            elif isinstance(node_input, BaseModel):
                parsed = output_schema.model_validate(node_input.model_dump())
            else:
                parsed = output_schema.model_validate(node_input)
            return Event(output=parsed.model_dump(mode="json"))

        graph = Workflow(
            name=f"{stage}_workflow",
            edges=[("START", agent, validate_output)],
        )
        sessions = InMemorySessionService()
        app_name = "retail_model_stage"
        session_id = uuid4().hex
        await sessions.create_session(
            app_name=app_name, user_id="stage", session_id=session_id
        )
        output: Any = None
        observed_failure: ModelFailure | None = None
        async with Runner(
            node=graph, app_name=app_name, session_service=sessions
        ) as runner:
            async with aclosing(
                runner.run_async(
                    user_id="stage",
                    session_id=session_id,
                    new_message=types.Content(
                        role="user", parts=[types.Part(text=serialized)]
                    ),
                    run_config=RunConfig(max_llm_calls=1),
                )
            ) as events:
                async for event in events:
                    if event.error_code:
                        # Drain the graph: ADK raises the original exception at
                        # cleanup, preserving transient status/type for retry.
                        # A non-raising refusal still fails closed below.
                        observed_failure = ModelFailure(
                            "model_invalid_output" if event.error_code == "ValidationError"
                            else "model_rejected_request"
                        )
                    if event.usage_metadata and not event.partial:
                        usage = event.usage_metadata
                        metadata["input_tokens"] += usage.prompt_token_count or 0
                        metadata["output_tokens"] += (
                            (usage.candidates_token_count or 0)
                            + (usage.thoughts_token_count or 0)
                        )
                        metadata["total_tokens"] += usage.total_token_count or 0
                    if event.output is not None and not event.partial:
                        output = event.output
        if observed_failure:
            raise observed_failure
        if output is None:
            raise ModelFailure("model_empty_output")
        if isinstance(output, BaseModel):
            output = output.model_dump()
        return output_schema.model_validate(output)
