"""Typed analytics plans and a compiler whose SQL never comes from the model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


Metric = Literal["revenue", "orders", "purchasing_customers", "units", "average_order_value", "spend_per_customer"]
Dimension = Literal["month", "state", "country", "category", "product"]

SAFE_COLUMNS: dict[str, tuple[str, ...]] = {
    "orders": ("order_id", "user_id", "status"),
    "order_items": ("id", "order_id", "user_id", "product_id", "status", "created_at", "sale_price"),
    "products": ("id", "name", "category"),
    "users": ("id", "state", "country"),
}

METRIC_DEFINITIONS = {
    "revenue": "Sum of nonnegative item sale_price for Complete or Shipped items and orders.",
    "orders": "Distinct orders containing included, permitted product items.",
    "purchasing_customers": "Distinct purchasers of included, permitted product items.",
    "units": "Included order-item rows; the dataset represents one unit per row.",
    "average_order_value": "Scoped revenue divided by distinct included orders.",
    "spend_per_customer": "Scoped revenue divided by distinct included purchasing customers.",
}


class AnalyticsError(ValueError):
    """An expected analytics failure safe to classify at the application boundary."""


class ScopeViolation(AnalyticsError):
    pass


class SchemaViolation(AnalyticsError):
    pass


class BudgetExceeded(AnalyticsError):
    pass


class QueryTimeout(AnalyticsError):
    pass


@dataclass(frozen=True)
class ActorScope:
    actor_id: str
    allowed_product_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ScopeViolation("An authenticated or trusted demo actor is required.")
        if any(type(value) is not int or value <= 0 for value in self.allowed_product_ids):
            raise ScopeViolation("Product entitlements must be positive integer IDs.")
        object.__setattr__(self, "allowed_product_ids", tuple(sorted(set(self.allowed_product_ids))))


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[Metric] = Field(min_length=1, max_length=6)
    dimensions: list[Dimension] = Field(default_factory=list, max_length=5)
    start_date: date | None = None
    end_date: date | None = None
    product_ids: list[StrictInt] | None = Field(default=None, min_length=1, max_length=100)
    states: list[str] | None = Field(default=None, min_length=1, max_length=50)
    countries: list[str] | None = Field(default=None, min_length=1, max_length=50)
    categories: list[str] | None = Field(default=None, min_length=1, max_length=50)
    limit: StrictInt = Field(default=50, ge=1, le=50)

    @field_validator("metrics", "dimensions", "product_ids", "states", "countries", "categories")
    @classmethod
    def unique_values(cls, values: list[Any] | None) -> list[Any] | None:
        if values is not None and len(values) != len(set(values)):
            raise ValueError("Values must not be duplicated.")
        return values

    @field_validator("product_ids")
    @classmethod
    def positive_products(cls, values: list[int] | None) -> list[int] | None:
        if values is not None and any(value <= 0 for value in values):
            raise ValueError("Product IDs must be positive.")
        return values

    @field_validator("states", "countries", "categories")
    @classmethod
    def bounded_labels(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("Filters must contain nonempty labels of at most 100 characters.")
        return values

    @model_validator(mode="after")
    def valid_period(self) -> QuerySpec:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("Supply both date bounds or neither for explicit all-time analysis.")
        if self.start_date is not None and self.end_date is not None and self.start_date >= self.end_date:
            raise ValueError("start_date must precede exclusive end_date.")
        return self


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[QuerySpec] = Field(min_length=1, max_length=3)


@dataclass(frozen=True)
class SQLParameter:
    name: str
    data_type: str
    value: Any
    is_array: bool = False


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: tuple[SQLParameter, ...]


def permitted_products(spec: QuerySpec, scope: ActorScope) -> tuple[int, ...]:
    if not scope.allowed_product_ids:
        raise ScopeViolation("This actor has no permitted products.")
    if spec.product_ids is not None:
        if not set(spec.product_ids).issubset(scope.allowed_product_ids):
            raise ScopeViolation("The requested products exceed the actor's permissions.")
        return tuple(sorted(spec.product_ids))
    return scope.allowed_product_ids


class SQLCompiler:
    """Compile approved aggregate operations against four trusted join paths."""

    def __init__(self, dataset: str = "bigquery-public-data.thelook_ecommerce") -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_]+", dataset):
            raise SchemaViolation("Use a trusted project.dataset identifier.")
        self.dataset = dataset

    def compile(self, spec: QuerySpec, scope: ActorScope) -> CompiledQuery:
        spec = QuerySpec.model_validate(spec.model_dump())
        products = permitted_products(spec, scope)
        parameters = [SQLParameter("allowed_products", "INT64", products, True)]
        filters = [
            "oi.product_id IN UNNEST(@allowed_products)",
            "oi.status IN ('Complete', 'Shipped')",
            "o.status IN ('Complete', 'Shipped')",
            "oi.sale_price >= 0",
        ]
        if spec.start_date is not None:
            filters.extend(["oi.created_at >= TIMESTAMP(@start_date)", "oi.created_at < TIMESTAMP(@end_date)"])
            parameters.extend([SQLParameter("start_date", "DATE", spec.start_date), SQLParameter("end_date", "DATE", spec.end_date)])
        for name, expression in [("states", "COALESCE(u.state, 'Unknown')"), ("countries", "COALESCE(u.country, 'Unknown')"), ("categories", "COALESCE(p.category, 'Unknown')")]:
            values = getattr(spec, name)
            if values is not None:
                filters.append(f"{expression} IN UNNEST(@{name})")
                parameters.append(SQLParameter(name, "STRING", tuple(values), True))

        dimension_expressions = {
            "month": "FORMAT_TIMESTAMP('%Y-%m', oi.created_at, 'UTC')",
            "state": "COALESCE(u.state, 'Unknown')",
            "country": "COALESCE(u.country, 'Unknown')",
            "category": "COALESCE(p.category, 'Unknown')",
            "product": "CONCAT(CAST(p.id AS STRING), ': ', p.name)",
        }
        select = [f"{dimension_expressions[name]} AS {name}" for name in spec.dimensions]
        select.extend(["oi.order_id AS scoped_order", "oi.user_id AS scoped_customer", "oi.sale_price AS scoped_price"])
        metrics = {
            "revenue": "ROUND(SUM(scoped_price), 2)",
            "orders": "COUNT(DISTINCT scoped_order)",
            "purchasing_customers": "COUNT(DISTINCT scoped_customer)",
            "units": "COUNT(*)",
            "average_order_value": "ROUND(SAFE_DIVIDE(SUM(scoped_price), COUNT(DISTINCT scoped_order)), 2)",
            "spend_per_customer": "ROUND(SAFE_DIVIDE(SUM(scoped_price), COUNT(DISTINCT scoped_customer)), 2)",
        }
        output = list(spec.dimensions) + [f"{metrics[name]} AS {name}" for name in spec.metrics]
        output.append("COUNT(DISTINCT scoped_customer) AS group_customer_count")
        group = "\nGROUP BY " + ", ".join(spec.dimensions) if spec.dimensions else ""
        order = "\nORDER BY " + ", ".join(spec.dimensions) if spec.dimensions else ""
        sql = (
            "WITH scoped_items AS (\n  SELECT " + ", ".join(select)
            + f"\n  FROM `{self.dataset}.order_items` oi"
            + f"\n  JOIN `{self.dataset}.orders` o ON o.order_id = oi.order_id AND o.user_id = oi.user_id"
            + f"\n  JOIN `{self.dataset}.products` p ON p.id = oi.product_id"
            + f"\n  JOIN `{self.dataset}.users` u ON u.id = oi.user_id"
            + "\n  WHERE " + "\n    AND ".join(filters)
            + "\n)\nSELECT " + ", ".join(output)
            + "\nFROM scoped_items" + group + "\nHAVING COUNT(*) > 0" + order + "\nLIMIT @result_limit"
        )
        parameters.append(SQLParameter("result_limit", "INT64", spec.limit))
        return CompiledQuery(sql, tuple(parameters))
