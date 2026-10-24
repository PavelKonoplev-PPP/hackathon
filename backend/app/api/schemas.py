from typing import Any, Literal
from pydantic import BaseModel, Field


# Health schemas
class HealthResponse(BaseModel):
    """Health check response schema."""

    status: str
    database: bool


# Draft endpoint schemas (/ask)
class AskRequest(BaseModel):
    """Simple ask request schema."""

    question: str = Field(min_length=1, max_length=2000)


class AskResponse(BaseModel):
    """Simple ask response schema."""

    answer: str


# API contract schemas (/api/chat conforming to docs/api-contract.md)
class ChartSeries(BaseModel):
    """Series specification for frontend charts."""

    name: str
    data: list[Any]


class ChartSpec(BaseModel):
    """Chart specification conforming to frontend ECharts schema."""

    type: Literal["bar", "line", "pie"]
    title: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)


class TableSpec(BaseModel):
    """Tabular data specification."""

    columns: list[str]
    rows: list[list[Any]]


class ClarificationSpec(BaseModel):
    """Clarification prompt with selectable options for ambiguous queries."""

    question: str
    options: list[str]


class ChatRequest(BaseModel):
    """Chat request payload from frontend."""

    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    """Full chat response schema matching docs/api-contract.md."""

    answer: str
    chart: ChartSpec | None = None
    table: TableSpec | None = None
    sql: str | None = None
    clarification: ClarificationSpec | None = None


# Schema / Catalog endpoint schemas (/api/schema conforming to Issue #39)
class ColumnSchema(BaseModel):
    """Metadata for a table column."""

    name: str
    type: str | None = None
    postgres_type: str | None = None
    inferred_type: str | None = None
    nullable: bool = True
    warning: str | None = None


class TableSchema(BaseModel):
    """Metadata for an available database table."""

    name: str
    display_name: str | None = None
    row_count: int | None = None
    columns: list[ColumnSchema] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RelationshipSchema(BaseModel):
    """Foreign key or join candidate relationship."""

    source_table: str
    source_column: str
    target_table: str
    target_column: str
    kind: str | None = None
    can_auto_join: bool = False
    policy: str | None = None
    source: dict[str, Any] | None = None
    target: dict[str, Any] | None = None


class SchemaResponse(BaseModel):
    """Response model for /api/schema and /schema."""

    schema_name: str = "raw"
    sources: list[str] = Field(default_factory=list)
    tables: list[TableSchema] = Field(default_factory=list)
    relationships: list[RelationshipSchema] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
