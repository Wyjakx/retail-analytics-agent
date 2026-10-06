import asyncio
import json
from types import SimpleNamespace
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import _common, models, types
from pydantic import BaseModel, Field
import pytest

from retail_agent.adk_workflow import AdkTypedRunner, ModelFailure, _provider_json_schema
from retail_agent.model import AnalystReport, Decision


class CapturingModel(BaseLlm):
    model: str = "fake-local-model"
    response: str
    requests: list[Any] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.response)]),
            turn_complete=True,
        )


@pytest.mark.parametrize("schema,response", [
    (Decision, {"action": "analysis", "plan": {"queries": [{"metrics": ["revenue"]}]}}),
    (AnalystReport, {"title": "Revenue", "summary": "Evidence supplied."}),
])
def test_adk_sends_json_schema_on_the_supported_sdk_wire_path(schema, response):
    fake = CapturingModel(response=json.dumps(response))
    runner = AdkTypedRunner("gemini-3.8-flash", model_override=fake, max_retries=0)
    result = asyncio.run(runner.run("test", "Return the supplied schema.", {}, schema))

    assert isinstance(result, schema)
    assert runner.last_metadata["model_calls"] == 1
    assert runner.turn_model_calls == 1
    request = fake.requests[0]
    assert request.config.response_schema is None
    assert request.config.response_json_schema == _provider_json_schema(schema)
    assert request.config.response_mime_type == "application/json"
    assert request.config.response_json_schema["additionalProperties"] is False

    # Exercise the installed SDK serializer, not only the callback's fields.
    # No Client is constructed and no credentials or network are used.
    wire = _common.convert_to_dict(models._GenerateContentParameters_to_mldev(
        SimpleNamespace(vertexai=False),
        types._GenerateContentParameters(
            model="gemini-3.8-flash", contents=request.contents, config=request.config,
        ),
    ))
    config = wire["generationConfig"]
    assert "responseSchema" not in config
    assert config["responseJsonSchema"] == _provider_json_schema(schema)
    assert "additional_properties" not in json.dumps(config)
    assert "minItems" not in json.dumps(config["responseJsonSchema"])
    assert "maxItems" not in json.dumps(config["responseJsonSchema"])
    if schema is Decision:
        definitions = config["responseJsonSchema"]["$defs"]
        assert definitions["AnalysisPlan"]["additionalProperties"] is False
        assert definitions["QuerySpec"]["additionalProperties"] is False
        query_properties = definitions["QuerySpec"]["properties"]
        assert query_properties["limit"]["maximum"] == 50
        assert query_properties["metrics"]["items"]["enum"] == [
            "revenue", "orders", "purchasing_customers", "units",
            "average_order_value", "spend_per_customer",
        ]
        assert query_properties["customer_refs"]["anyOf"][0]["items"]["pattern"] == (
            "^cust_[0-9a-f]{32}$"
        )


def test_provider_schema_copy_keeps_original_bounds_and_property_names():
    class BoundsNamedFields(BaseModel):
        minItems: list[str] = Field(min_length=1, max_length=2)
        maxItems: list[str] = Field(min_length=1, max_length=3)

    original = BoundsNamedFields.model_json_schema()
    adapted = _provider_json_schema(BoundsNamedFields)
    assert set(adapted["properties"]) == {"minItems", "maxItems"}
    assert adapted["properties"]["minItems"]["type"] == "array"
    assert "minItems" not in adapted["properties"]["minItems"]
    assert "maxItems" not in adapted["properties"]["maxItems"]
    assert BoundsNamedFields.model_json_schema() == original
    assert original["properties"]["minItems"]["minItems"] == 1
    assert original["properties"]["maxItems"]["maxItems"] == 3


@pytest.mark.parametrize("schema,response", [
    (Decision, {"action": "analysis", "plan": {"queries": [
        {"metrics": ["revenue"], "sql": "SELECT secret FROM users"},
    ]}}),
    (Decision, {"action": "analysis", "plan": {"queries": [
        {"metrics": ["revenue"], "customer_refs": ["cust_invalid"]},
    ]}}),
    (AnalystReport, {"title": "Revenue", "summary": "Evidence supplied.", "sql": "secret"}),
    (Decision, {"action": "analysis", "plan": {"queries": [
        {"metrics": ["revenue"]} for _ in range(4)
    ]}}),
    (Decision, {"action": "analysis", "plan": {"queries": [
        {"metrics": ["revenue"], "product_ids": list(range(1, 102))},
    ]}}),
    (Decision, {"action": "analysis", "plan": {"queries": [
        {"metrics": []},
    ]}}),
    (AnalystReport, {
        "title": "Revenue", "summary": "Evidence supplied.", "findings": ["Row"] * 11,
    }),
])
def test_json_schema_transport_keeps_strict_python_output_validation(schema, response):
    fake = CapturingModel(response=json.dumps(response))
    runner = AdkTypedRunner("gemini-3.8-flash", model_override=fake, max_retries=0)
    with pytest.raises(ModelFailure) as error:
        asyncio.run(runner.run("test", "Return the supplied schema.", {}, schema))
    assert error.value.code == "model_invalid_output"
    assert runner.last_metadata["model_calls"] == 1
    assert "secret" not in str(error.value)
