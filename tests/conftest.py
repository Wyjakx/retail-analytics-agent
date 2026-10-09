import json

import pytest

from retail_agent.config import Settings, resolve_scope
from retail_agent.model import GeminiModel
from retail_agent.pseudonyms import CustomerPseudonymizer
from retail_agent.reports import ReportStore
from retail_agent.service import AnalyticsService, Conversation
from retail_agent.telemetry import TraceRecorder
from tests.scenarios import ObservedGateway, ScriptedProvider


@pytest.fixture
def transactions():
    """Six repeat buyers; mixed orders distinguish items, orders and customers.

    Authorized products: January 230, February 270; 14 items, 12 orders,
    six buyers. The same orders include 12,000 of unauthorized product 3.
    These records are independent of the application's demo-data generator.
    """
    data = {
        "products": [
            {"id": 1, "name": "Tee", "category": "Clothes"},
            {"id": 2, "name": "Cap", "category": "Clothes"},
            {"id": 3, "name": "Restricted lamp", "category": "Home"},
        ],
        "users": [], "orders": [], "order_items": [],
    }
    for buyer, state, january, february in [
        (910001, "California", 10, 20), (910002, "California", 20, 30),
        (910003, "California", 30, 40), (910004, "Texas", 40, 50),
        (910005, "Texas", 50, 60), (910006, "Texas", 60, 70),
    ]:
        data["users"].append({
            "id": buyer, "state": state, "country": "US",
            "first_name": "PRIVATE_BUYER_NAME", "email": "private-buyer@example.invalid",
        })
        for month, price in [(1, january), (2, february)]:
            order = len(data["orders"]) + 1
            data["orders"].append({"order_id": order, "user_id": buyer, "status": "Complete"})
            products = [(1, price), (3, 1000)]
            if month == 1 and buyer in (910001, 910002):
                products.append((2, 5 if buyer == 910001 else 15))
            for product, amount in products:
                data["order_items"].append({
                    "id": len(data["order_items"]) + 1, "order_id": order, "user_id": buyer,
                    "product_id": product, "status": "Complete", "sale_price": amount,
                    "created_at": f"2025-{month:02d}-15T12:00:00+00:00",
                })
    return data


@pytest.fixture
def policy_file(tmp_path):
    path = tmp_path / "permissions.json"
    path.write_text(json.dumps({"alice": [1, 2], "bob": [3]}), encoding="utf-8")
    return path


@pytest.fixture
def application(tmp_path, transactions, policy_file):
    stores, providers = [], []

    def create(*responses, actor="alice", model=None, gateway=None, retries=0, **settings):
        provider = None
        if model is None:
            provider = ScriptedProvider(responses=list(responses))
            providers.append(provider)
            model = GeminiModel("test-provider", model_override=provider, max_retries=retries)
        store = ReportStore(tmp_path / "reports.sqlite3")
        stores.append(store)
        gateway = gateway if gateway is not None else ObservedGateway(
            transactions, pseudonymizer=CustomerPseudonymizer(b"k" * 32),
        )
        app = AnalyticsService(
            Settings(data_dir=tmp_path, **settings), Conversation(actor), model,
            gateway, store, TraceRecorder(tmp_path / "events.jsonl"),
            lambda who: resolve_scope(who, policy_file),
        )
        return app, provider

    yield create
    for store in stores:
        store.close()
    # Application fallback must not hide mistakes in the test's provider script.
    for provider in providers:
        assert provider.unexpected_calls == 0, "Application made an unplanned provider call"
        assert not provider.responses, "Application skipped a planned provider interaction"


