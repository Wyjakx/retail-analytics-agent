"""Test-owned inputs; expected business answers belong literally in each test."""

import asyncio
import inspect
import json
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

from retail_agent.gateways import OfflineGateway


def query(**changes):
    return {
        "metrics": ["revenue"], "start_date": "2025-01-01", "end_date": "2025-03-01",
        **changes,
    }


def plan(*queries):
    return {"action": "analysis", "plan": {"queries": list(queries or [query()])}}


def report(summary="Approved retail results."):
    return {"title": "Provider report", "summary": summary}


def ask(app, question="Revenue for January and February 2025"):
    return asyncio.run(app.handle(question))


class ScriptedProvider(BaseLlm):
    """Replace only provider generation; real ADK and application validation run."""

    model: str = "test-provider"
    responses: list[Any] = Field(default_factory=list)
    payloads: list[dict] = Field(default_factory=list)
    unexpected_calls: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        payload = json.loads(llm_request.contents[-1].parts[0].text)
        self.payloads.append(payload)
        if not self.responses:
            self.unexpected_calls += 1
            raise AssertionError("Unscripted provider call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(payload)
        if inspect.isawaitable(response):
            response = await response
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=json.dumps(response))]),
            turn_complete=True,
        )


class ObservedGateway(OfflineGateway):
    """Observe executions without replacing the real filtering or arithmetic."""

    def __init__(self, data, **kwargs):
        super().__init__(data, **kwargs)
        self.executions = []

    def execute(self, spec, scope, budget):
        self.executions.append(spec.model_dump(mode="json"))
        return super().execute(spec, scope, budget)
