from __future__ import annotations

import uuid

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    datasource_id: str | None = None
    table_name: str | None = None


class ExecutionStep(BaseModel):
    """Execution step detail for analytical query progress tracking (BI-07 / Issue #35)."""

    step: str
    title: str
    status: Literal["pending", "in_progress", "completed", "failed", "skipped"] = "completed"
    duration_ms: float | None = None
    details: str | None = None
    timestamp: str | None = None


class ChatResponse(BaseModel):
    """Full chat response schema matching docs/api-contract.md with BI-07 status tracking."""

    answer: str
    chart: ChartSpec | None = None
    table: TableSpec | None = None
    sql: str | None = None
    clarification: ClarificationSpec | None = None
    table_name: str | None = None
    status: str = "completed"
    steps: list[ExecutionStep] = Field(default_factory=list)
    execution_time_ms: float | None = None


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


# DataSource schemas (Sprint 3 / BI-09 / Issue #37)
class DataSourceTestRequest(BaseModel):
    """Payload to test external PostgreSQL database connection."""

    host: str = Field(min_length=1, description="PostgreSQL host or IP address")
    port: int = Field(default=5432, ge=1, le=65535, description="Port number")
    dbname: str = Field(min_length=1, description="Database name")
    user: str = Field(min_length=1, description="Database user")
    password: str | None = Field(default="", description="Database password")
    schema_name: str = Field(default="public", description="Target schema name")
    sslmode: str | None = Field(default=None, description="SSL connection mode")

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "database" in data and not data.get("dbname"):
                data["dbname"] = data["database"]
            if "username" in data and not data.get("user"):
                data["user"] = data["username"]
            if "host" in data and isinstance(data["host"], str):
                data["host"] = data["host"].strip()
            if "dbname" in data and isinstance(data["dbname"], str):
                data["dbname"] = data["dbname"].strip()
            if "user" in data and isinstance(data["user"], str):
                data["user"] = data["user"].strip()
            if data.get("password") is None:
                data["password"] = ""
            if "schema_name" in data and data.get("schema_name") is None:
                data["schema_name"] = "public"
        return data


class DataSourceTestResponse(BaseModel):
    """Response returned by connection test probe."""

    status: str = Field(description="'ok' on success, 'error' on failure")
    success: bool = Field(default=True, description="Boolean success flag")
    latency_ms: float = Field(description="Connection latency in milliseconds")
    tables_count: int = Field(default=0, description="Number of discovered tables")
    tables: list[str] = Field(default_factory=list, description="List of discovered table names")
    message: str | None = Field(default=None, description="Human-readable result or error message")


class DataSourceCreateRequest(BaseModel):
    """Payload to register a new external PostgreSQL data source."""

    name: str = Field(min_length=1, max_length=255, description="Display name for datasource")
    host: str = Field(min_length=1, description="PostgreSQL host or IP address")
    port: int = Field(default=5432, ge=1, le=65535, description="Port number")
    dbname: str = Field(min_length=1, description="Database name")
    user: str = Field(min_length=1, description="Database user")
    password: str | None = Field(default="", description="Database password")
    schema_name: str = Field(default="public", description="Target schema name")
    sslmode: str | None = Field(default=None, description="SSL connection mode")
    description: str | None = Field(default=None, description="Optional description")
    id: str | None = Field(default=None, description="Optional custom unique identifier")

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "database" in data and not data.get("dbname"):
                data["dbname"] = data["database"]
            if "username" in data and not data.get("user"):
                data["user"] = data["username"]
            if "name" in data and isinstance(data["name"], str):
                data["name"] = data["name"].strip()
            if "host" in data and isinstance(data["host"], str):
                data["host"] = data["host"].strip()
            if "dbname" in data and isinstance(data["dbname"], str):
                data["dbname"] = data["dbname"].strip()
            if "user" in data and isinstance(data["user"], str):
                data["user"] = data["user"].strip()
            if data.get("password") is None:
                data["password"] = ""
            if "schema_name" in data and data.get("schema_name") is None:
                data["schema_name"] = "public"
        return data


class DataSourceResponse(BaseModel):
    """Public representation of a configured data source with masked credentials."""

    id: str
    name: str
    type: str = "postgresql"
    host: str
    port: int = 5432
    dbname: str
    user: str
    schema_name: str = "public"
    tables_count: int = 0
    tables: list[str] = Field(default_factory=list)
    is_default: bool = False
    password: str | None = "******"
    created_at: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def enforce_masked_credentials(self) -> DataSourceResponse:
        self.password = "******"
        return self


# Dashboard schemas (/api/dashboards conforming to Issue #43 / Scenario 5)
class DashboardWidget(BaseModel):
    """Widget inside an analytical dashboard."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str
    type: str = "chart"  # chart | table | metric | text
    sql: str | None = None
    chart: ChartSpec | None = None
    table: TableSpec | None = None
    answer: str | None = None
    grid: dict[str, Any] | None = None


class DashboardCreateRequest(BaseModel):
    """Request payload to create a new dashboard."""

    title: str = Field(..., min_length=1)
    description: str | None = None
    datasource_id: str = "raw"
    widgets: list[DashboardWidget] = Field(default_factory=list)


class DashboardUpdateRequest(BaseModel):
    """Request payload to update an existing dashboard."""

    title: str | None = None
    description: str | None = None
    datasource_id: str | None = None
    widgets: list[DashboardWidget] | None = None


class DashboardSummary(BaseModel):
    """Summary of a saved dashboard for list views."""

    id: str
    title: str
    description: str | None = None
    datasource_id: str = "raw"
    widget_count: int = 0
    created_at: str
    updated_at: str


class DashboardDetail(BaseModel):
    """Full detail of a saved dashboard with all widgets."""

    id: str
    title: str
    description: str | None = None
    datasource_id: str = "raw"
    widgets: list[DashboardWidget] = Field(default_factory=list)
    created_at: str
    updated_at: str


# CSV Upload & Preview schemas
class CsvPreviewResponse(BaseModel):
    """Result of CSV format sniffing and preview."""

    filename: str
    schema_name: str = "raw"
    table_name: str
    encoding: str
    delimiter: str
    original_headers: list[str]
    columns: list[str]
    rows: list[list[Any]]
    warnings: list[str] = Field(default_factory=list)


class CsvUploadResponse(BaseModel):
    """Result of importing CSV table and refreshing catalog."""

    status: str = "ok"
    schema_name: str = "raw"
    table_name: str
    row_count: int
    columns: list[str]
    warnings: list[str] = Field(default_factory=list)
    catalog_refreshed: bool = True
    message: str | None = None


# Query Execution Statuses & Jobs (BI-07 / Issue #35)
class JobStatusResponse(BaseModel):
    """Status and progress details of an analytical query execution job."""

    job_id: str
    session_id: str | None = None
    message: str
    datasource_id: str | None = "raw"
    table_name: str | None = None
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    current_step: str | None = None
    progress_pct: int = 0
    steps: list[ExecutionStep] = Field(default_factory=list)
    result: ChatResponse | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    execution_time_ms: float | None = None


class JobCreateResponse(BaseModel):
    """Initial response on query execution job submission."""

    job_id: str
    status: str = "pending"
    message: str = "Query execution initiated"


# Saved Analytical Queries (BI-35 / Issue #60)
class SavedQuery(BaseModel):
    """Saved analytical query model."""

    id: str
    title: str
    description: str | None = None
    question: str | None = None
    sql: str | None = None
    datasource_id: str = "raw"
    table_name: str | None = None
    chart_config: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class SavedQueryCreateRequest(BaseModel):
    """Payload to create a saved analytical query."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    question: str | None = None
    sql: str | None = None
    datasource_id: str = "raw"
    table_name: str | None = None
    chart_config: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)


class SavedQueryUpdateRequest(BaseModel):
    """Payload to update an existing saved analytical query."""

    title: str | None = None
    description: str | None = None
    question: str | None = None
    sql: str | None = None
    datasource_id: str | None = None
    table_name: str | None = None
    chart_config: dict[str, Any] | None = None
    tags: list[str] | None = None


class SavedQueryExecuteResponse(BaseModel):
    """Result of executing a saved analytical query."""

    query_id: str
    title: str
    datasource_id: str
    status: str = "completed"
    execution_time_ms: float = 0.0
    result: ChatResponse

# Business KPIs (BI-32 / Issue #78)
class BusinessKpiItem(BaseModel):
    """Audited business KPI metric specification."""

    id: str
    schema_name: str = Field(alias="schema", default="raw")
    table: str
    title: str
    unit: str
    description: str
    expression: str | None = None
    required_columns: list[str] = Field(default_factory=list)
    status: Literal["proposed", "approved", "unavailable"] = "proposed"
    requires_business_approval: bool = True
    warnings: list[str] = Field(default_factory=list)
    value: float | int | str | None = None
    formatted_value: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class BusinessKpisResponse(BaseModel):
    """Response model for /api/kpis endpoint."""

    datasource_id: str
    schema_name: str
    kpis: list[BusinessKpiItem]
    summary: dict[str, int]


class BusinessKpiApproveRequest(BaseModel):
    """Request to approve or sign off on a proposed KPI definition."""

    approved_by: str = Field(min_length=1, max_length=100)
    comment: str | None = None


# PDF Export (BI-40 / Issue #82)
class PdfExportRequest(BaseModel):
    """Payload for analytical report PDF export."""

    title: str = Field(default="Аналитический отчёт", max_length=255)
    subtitle: str | None = None
    datasource_id: str = "raw"
    kpis: list[dict[str, Any]] = Field(default_factory=list)
    table: TableSpec | None = None
    chart: ChartSpec | None = None
    sql: str | None = None
    widgets: list[DashboardWidget] = Field(default_factory=list)

