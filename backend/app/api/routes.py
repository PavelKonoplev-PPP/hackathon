from fastapi import APIRouter, HTTPException

from .schemas import (
    AskRequest,
    AskResponse,
    ChatRequest,
    ChatResponse,
    ColumnSchema,
    HealthResponse,
    RelationshipSchema,
    SchemaResponse,
    TableSchema,
)
from ..db.session import check_db
from ..llm.client import LLMError
from ..services import agent

try:
    from backend.llm.catalog_summary import display_table_name, table_row_count
except ImportError:
    try:
        from llm.catalog_summary import display_table_name, table_row_count
    except ImportError:

        def display_table_name(name: str) -> str:
            if name.startswith("data_"):
                parts = name.split("_", 2)
                if len(parts) == 3 and parts[1].isdigit():
                    return parts[2].replace("_", " ")
            return name

        def table_row_count(table: dict) -> int | None:
            exact = []
            for col in table.get("columns", []):
                conv = col.get("conversion") or {}
                quality = conv.get("quality")
                if isinstance(quality, dict) and quality.get("total_rows") is not None:
                    exact.append(quality["total_rows"])
            if exact:
                return max(exact)
            estimate = table.get("estimated_total_rows")
            return estimate if isinstance(estimate, int) and estimate >= 0 else None

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return API and database connectivity status."""
    return HealthResponse(status="ok", database=check_db())


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    """Simple query endpoint without session memory."""
    try:
        text = agent.answer(body.question)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AskResponse(answer=text)


@router.post("/api/chat", response_model=ChatResponse)
@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(body: ChatRequest) -> ChatResponse:
    """Frontend-compatible chat endpoint adhering to docs/api-contract.md."""
    try:
        result = agent.process_chat_message(body.message, session_id=body.session_id)
        return ChatResponse(**result)
    except (LLMError, agent.AgentServiceError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}") from exc


@router.get("/api/schema", response_model=SchemaResponse)
@router.get("/schema", response_model=SchemaResponse)
def get_schema() -> SchemaResponse:
    """Return catalog schema including tables, columns, row counts, warnings, and relationships."""
    catalog, relationships = agent.load_catalogs()
    if not catalog or not isinstance(catalog, dict):
        return SchemaResponse(
            schema_name="raw",
            sources=[],
            tables=[],
            relationships=[],
            warnings=["Каталог данных ещё не сгенерирован (data/schema-catalog.json не найден)."],
        )

    schema_name = catalog.get("schema") or "raw"
    top_warnings: list[str] = []
    if catalog.get("confidence_is_heuristic"):
        top_warnings.append("Достоверность типов эвристическая")
    if not catalog.get("relationships_verified", True):
        top_warnings.append(
            "Связи между таблицами не верифицированы бизнесом (требуется ручное подтверждение)"
        )

    join_approval_needed = False
    if isinstance(relationships, dict):
        rules = relationships.get("rules")
        if isinstance(rules, dict) and rules.get("join_requires_explicit_business_approval"):
            join_approval_needed = True
        elif relationships.get("join_approval_required"):
            join_approval_needed = True
    if join_approval_needed:
        top_warnings.append(
            "Автоматические JOIN между таблицами заблокированы правилами безопасности"
        )

    tables_out: list[TableSchema] = []
    raw_tables = catalog.get("tables") or []
    if not isinstance(raw_tables, list):
        raw_tables = []

    for t in raw_tables:
        if not isinstance(t, dict):
            continue
        t_name = t.get("name") or ""
        disp_name = display_table_name(t_name)
        rows = table_row_count(t)
        t_warnings: list[str] = []
        if isinstance(t.get("warnings"), list):
            for w in t["warnings"]:
                if w and str(w) not in t_warnings:
                    t_warnings.append(str(w))
        if t.get("warning") and str(t["warning"]) not in t_warnings:
            t_warnings.append(str(t["warning"]))
        if rows == 0 and "Таблица пуста" not in t_warnings:
            t_warnings.append("Таблица пуста")

        cols_out: list[ColumnSchema] = []
        raw_cols = t.get("columns") or []
        if isinstance(raw_cols, list):
            for c in raw_cols:
                if not isinstance(c, dict):
                    continue
                c_name = c.get("name") or ""
                inf_type = c.get("inferred_type")
                pg_type = c.get("postgres_type")
                effective_type = inf_type or pg_type or "text"
                nullable = bool(c.get("nullable", True))

                col_warnings: list[str] = []
                if c.get("warning"):
                    col_warnings.append(str(c["warning"]))

                conv = c.get("conversion") if isinstance(c.get("conversion"), dict) else {}
                policy = conv.get("policy")
                quality = conv.get("quality") if isinstance(conv.get("quality"), dict) else {}

                if policy == "blocked_invalid":
                    inv = quality.get("invalid_rows", 0)
                    col_warnings.append(f"Некорректных значений: {inv}")
                elif policy == "safe_with_nulls":
                    miss = quality.get("missing_rows", 0)
                    if miss:
                        col_warnings.append(f"Пропусков: {miss}")

                c_warning = "; ".join(col_warnings) if col_warnings else None

                cols_out.append(
                    ColumnSchema(
                        name=c_name,
                        type=effective_type,
                        postgres_type=pg_type,
                        inferred_type=inf_type,
                        nullable=nullable,
                        warning=c_warning,
                    )
                )

        tables_out.append(
            TableSchema(
                name=t_name,
                display_name=disp_name,
                row_count=rows,
                columns=cols_out,
                warnings=t_warnings,
            )
        )

    rels_out: list[RelationshipSchema] = []
    if isinstance(relationships, dict):
        raw_rels = (
            relationships.get("join_candidates")
            or relationships.get("relationships")
            or []
        )
        if isinstance(raw_rels, list):
            for jc in raw_rels:
                if not isinstance(jc, dict):
                    continue
                src = jc.get("source") if isinstance(jc.get("source"), dict) else {}
                tgt = jc.get("target") if isinstance(jc.get("target"), dict) else {}
                rels_out.append(
                    RelationshipSchema(
                        source_table=src.get("table", ""),
                        source_column=src.get("column", ""),
                        target_table=tgt.get("table", ""),
                        target_column=tgt.get("column", ""),
                        kind=jc.get("kind"),
                        can_auto_join=bool(jc.get("can_auto_join", False)),
                        policy=jc.get("policy") or jc.get("status"),
                        source=src,
                        target=tgt,
                    )
                )

    sources = [schema_name]
    for t in tables_out:
        if t.name and t.name not in sources:
            sources.append(t.name)

    return SchemaResponse(
        schema_name=schema_name,
        sources=sources,
        tables=tables_out,
        relationships=rels_out,
        warnings=top_warnings,
    )
