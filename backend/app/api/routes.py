import io
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from .schemas import (
    AskRequest,
    AskResponse,
    ChatRequest,
    ChatResponse,
    ColumnSchema,
    CsvPreviewResponse,
    CsvUploadResponse,
    DashboardCreateRequest,
    DashboardDetail,
    DashboardSummary,
    DashboardUpdateRequest,
    DataSourceCreateRequest,
    DataSourceResponse,
    DataSourceTestRequest,
    DataSourceTestResponse,
    HealthResponse,
    JobCreateResponse,
    JobStatusResponse,
    RelationshipSchema,
    SavedQuery,
    SavedQueryCreateRequest,
    SavedQueryExecuteResponse,
    SavedQueryUpdateRequest,
    SchemaResponse,
    TableSchema,
    BusinessKpiApproveRequest,
    BusinessKpiItem,
    BusinessKpisResponse,
    PdfExportRequest,
)
from ..db.session import check_db, get_reader_connection, get_writer_connection
from ..llm.client import LLMError
from ..services import agent, dashboards, datasources, kpis, pdf_export, query_tracker, saved_queries

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB limit

def _get_csv_modules():
    try:
        from db.csv_service import preview_csv, import_uploaded_csv
        from db.load_csv import table_name_for
        from db.refresh_catalog import refresh_catalog
        return preview_csv, import_uploaded_csv, table_name_for, refresh_catalog
    except (ImportError, AttributeError):
        import sys
        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        import db.csv_service
        import db.load_csv
        import db.refresh_catalog
        return (
            db.csv_service.preview_csv,
            db.csv_service.import_uploaded_csv,
            db.load_csv.table_name_for,
            db.refresh_catalog.refresh_catalog,
        )


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
        result = agent.process_chat_message(
            body.message,
            session_id=body.session_id,
            datasource_id=body.datasource_id,
            table_name=body.table_name,
        )
        return ChatResponse(**result)
    except (LLMError, agent.AgentServiceError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}") from exc


@router.get("/api/schema", response_model=SchemaResponse)
@router.get("/schema", response_model=SchemaResponse)
def get_schema(datasource_id: str | None = None) -> SchemaResponse:
    """Return catalog schema including tables, columns, row counts, warnings, and relationships."""
    # 1. External datasource schema handling
    if datasource_id and datasource_id.strip().lower() != "raw":
        ds_clean = datasource_id.strip()
        ext_cat = None
        try:
            try:
                from db.external_catalog import read_external_catalog
                ext_cat = read_external_catalog(Path("data"), ds_clean)
            except ImportError:
                from ...db.external_catalog import read_external_catalog  # type: ignore
                ext_cat = read_external_catalog(Path("data"), ds_clean)
        except Exception:
            ext_cat = None

        if not ext_cat:
            stored_ds = datasources.get_all_datasources()
            matched_ds = next((ds for ds in stored_ds if ds.id.lower() == ds_clean.lower()), None)
            if not matched_ds:
                raise HTTPException(status_code=404, detail=f"Источник данных «{datasource_id}» не найден.")

            ext_tables_out = [
                TableSchema(
                    name=t,
                    display_name=display_table_name(t),
                    row_count=None,
                    columns=[],
                    warnings=[],
                )
                for t in matched_ds.tables
            ]
            return SchemaResponse(
                schema_name=matched_ds.schema_name or matched_ds.dbname,
                sources=[matched_ds.id] + matched_ds.tables,
                tables=ext_tables_out,
                relationships=[],
                warnings=["Внешний источник PostgreSQL (список таблиц получен)"],
            )

        schema_name = ext_cat.get("schema") or ds_clean
        top_warnings = ["Внешний каталог PostgreSQL (режим read-only)"]
        tables_out = []
        for t in ext_cat.get("tables", []):
            if not isinstance(t, dict):
                continue
            t_name = t.get("name") or ""
            cols_out = []
            for c in t.get("columns", []):
                if not isinstance(c, dict):
                    continue
                cols_out.append(
                    ColumnSchema(
                        name=c.get("name") or "",
                        type=c.get("inferred_type") or c.get("postgres_type") or "text",
                        postgres_type=c.get("postgres_type"),
                        inferred_type=c.get("inferred_type"),
                        nullable=bool(c.get("nullable", True)),
                        warning=c.get("warning"),
                    )
                )
            tables_out.append(
                TableSchema(
                    name=t_name,
                    display_name=display_table_name(t_name),
                    row_count=table_row_count(t),
                    columns=cols_out,
                    warnings=t.get("warnings", []),
                )
            )
        sources = [schema_name] + [t.name for t in tables_out if t.name]
        return SchemaResponse(
            schema_name=schema_name,
            sources=sources,
            tables=tables_out,
            relationships=[],
            warnings=top_warnings,
        )

    # 2. Local raw schema handling
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


# CSV Upload & Preview endpoints (FastAPI integration with db.csv_service & db.refresh_catalog)
@router.post("/api/csv/preview", response_model=CsvPreviewResponse)
@router.post("/csv/preview", response_model=CsvPreviewResponse)
async def preview_csv_endpoint(
    file: UploadFile = File(...),
    delimiter: str | None = Form(None),
    encoding: str = Form("utf-8-sig"),
    preview_rows: int = Form(5),
) -> CsvPreviewResponse:
    """Sniff CSV format, delimiters and preview top rows safely without full load."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение .csv")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="Размер файла превышает допустимый лимит (100 МБ).",
                    )
                tmp.write(chunk)

        preview_csv, _, _, _ = _get_csv_modules()

        preview = preview_csv(
            tmp_path,
            original_filename=file.filename,
            delimiter=delimiter,
            encoding=encoding,
            preview_rows=preview_rows,
        )
        return CsvPreviewResponse(
            filename=preview["filename"],
            schema_name=preview.get("schema", "raw"),
            table_name=preview["table_name"],
            encoding=preview["encoding"],
            delimiter=preview["delimiter"],
            original_headers=preview["original_headers"],
            columns=preview["columns"],
            rows=preview["rows"],
            warnings=preview.get("warnings", []),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка обработки CSV: {exc}") from exc
    finally:
        if tmp_path and tmp_path.is_file():
            tmp_path.unlink(missing_ok=True)


@router.post("/api/csv/upload", response_model=CsvUploadResponse)
@router.post("/api/data/upload", response_model=CsvUploadResponse)
@router.post("/csv/upload", response_model=CsvUploadResponse)
async def upload_csv_endpoint(
    file: UploadFile = File(...),
    delimiter: str | None = Form(None),
    encoding: str = Form("utf-8-sig"),
    replace: bool = Form(False),
) -> CsvUploadResponse:
    """Stream CSV into PostgreSQL raw schema with overwrite protection and catalog refresh."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение .csv")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="Размер файла превышает допустимый лимит (100 МБ).",
                    )
                tmp.write(chunk)

        _, import_uploaded_csv, table_name_for, refresh_catalog = _get_csv_modules()

        target_table_name = table_name_for(Path(file.filename))

        # Check existing table to prevent accidental overwrite
        if not replace:
            try:
                with get_reader_connection() as r_conn:
                    with r_conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT 1 FROM information_schema.tables
                            WHERE lower(table_schema) = 'raw' AND lower(table_name) = lower(%s);
                            """,
                            (target_table_name,),
                        )
                        if cur.fetchone() is not None:
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    f"Таблица «{target_table_name}» уже существует в схеме raw. "
                                    "Перезапись запрещена (передайте replace=True для явной перезаписи)."
                                ),
                            )
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("Table existence check skipped: %s", exc)

        # Phase 1: CSV import inside writer transaction
        with get_writer_connection() as w_conn:
            res = import_uploaded_csv(
                w_conn,
                tmp_path,
                original_filename=file.filename,
                schema="raw",
                delimiter=delimiter,
                encoding=encoding,
                replace=replace,
            )
            w_conn.commit()

        # w_conn is closed and committed. Table is physically persisted in PostgreSQL.

        # Phase 2: Metadata catalog profiling (strictly separate read-only connection)
        catalog_report = None
        catalog_refreshed = False
        catalog_status = "failed"
        upload_warnings = list(res.get("warnings") or [])

        try:
            with get_reader_connection() as r_conn:
                raw_report = refresh_catalog(r_conn, table=res["table_name"], schema="raw")
                catalog_report = raw_report if isinstance(raw_report, dict) else None
                failed_audits = catalog_report.get("failed_audits", 0) if catalog_report else 0
                unverified = catalog_report.get("unverified_columns", 0) if catalog_report else 0

                if failed_audits > 0:
                    catalog_status = "partial"
                    catalog_refreshed = False
                    upload_warnings.append(
                        f"Каталог обновлён частично: ошибок аудита типов: {failed_audits}, "
                        f"неверифицированных колонок: {unverified}."
                    )
                else:
                    catalog_status = "ready"
                    catalog_refreshed = True
        except Exception as cat_exc:
            logger.exception("Failed to refresh catalog after CSV import for %s: %s", res["table_name"], cat_exc)
            catalog_status = "failed"
            catalog_refreshed = False
            upload_warnings.append(
                f"Таблица создана, но каталогизация завершилась с ошибкой: {cat_exc}. "
                "Агенту потребуется ручной пересбор каталога."
            )

        try:
            from backend.llm.sql_runner import clear_values_cache
            clear_values_cache()
        except Exception:
            pass

        if catalog_status == "ready":
            msg = "Таблица успешно импортирована, каталог обновлён."
        elif catalog_status == "partial":
            msg = "Таблица импортирована, но каталог содержит частично неверифицированные колонки."
        else:
            msg = "Таблица успешно импортирована в базу, но каталог не был обновлён."

        return CsvUploadResponse(
            status="ok",
            schema_name=res["schema"],
            table_name=res["table_name"],
            row_count=res["row_count"],
            columns=res["columns"],
            warnings=upload_warnings,
            catalog_refreshed=catalog_refreshed,
            catalog_status=catalog_status,
            catalog_report=catalog_report,
            message=msg,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка импорта CSV: {exc}") from exc
    finally:
        if tmp_path and tmp_path.is_file():
            tmp_path.unlink(missing_ok=True)


# DataSource endpoints (Sprint 3 / BI-09 / Issue #37)
@router.post("/api/datasources/test", response_model=DataSourceTestResponse)
@router.post("/datasources/test", response_model=DataSourceTestResponse)
def test_datasource(body: DataSourceTestRequest) -> DataSourceTestResponse:
    """Test connection to an external PostgreSQL database with read-only safeguards."""
    return datasources.test_datasource(body)


@router.get("/api/datasources", response_model=list[DataSourceResponse])
@router.get("/datasources", response_model=list[DataSourceResponse])
def get_datasources() -> list[DataSourceResponse]:
    """List available datasources including default 'raw' dataset without leaking passwords."""
    return datasources.get_all_datasources()


@router.post("/api/datasources", response_model=DataSourceResponse)
@router.post("/datasources", response_model=DataSourceResponse)
def create_datasource(body: DataSourceCreateRequest) -> DataSourceResponse:
    """Validate connection and register a new external PostgreSQL datasource."""
    try:
        return datasources.create_datasource(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to register datasource: {exc}"
        ) from exc


# Dashboard endpoints (Sprint 4 / BI-15 / Issue #43)
@router.get("/api/dashboards", response_model=list[DashboardSummary])
@router.get("/dashboards", response_model=list[DashboardSummary])
def get_dashboards() -> list[DashboardSummary]:
    """List all saved analytical dashboards."""
    return dashboards.list_dashboards()


@router.post("/api/dashboards", response_model=DashboardDetail, status_code=201)
@router.post("/dashboards", response_model=DashboardDetail, status_code=201)
def create_dashboard_endpoint(body: DashboardCreateRequest) -> DashboardDetail:
    """Create and persist a new analytical dashboard."""
    try:
        return dashboards.create_dashboard(body)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to create dashboard: {exc}"
        ) from exc


@router.get("/api/dashboards/{dashboard_id}", response_model=DashboardDetail)
@router.get("/dashboards/{dashboard_id}", response_model=DashboardDetail)
def get_dashboard_endpoint(dashboard_id: str) -> DashboardDetail:
    """Retrieve full details of a saved dashboard by ID."""
    dash = dashboards.get_dashboard(dashboard_id)
    if not dash:
        raise HTTPException(status_code=404, detail=f"Dashboard '{dashboard_id}' not found.")
    return dash


@router.put("/api/dashboards/{dashboard_id}", response_model=DashboardDetail)
@router.put("/dashboards/{dashboard_id}", response_model=DashboardDetail)
def update_dashboard_endpoint(
    dashboard_id: str, body: DashboardUpdateRequest
) -> DashboardDetail:
    """Update title, description, or widgets of an existing dashboard."""
    dash = dashboards.update_dashboard(dashboard_id, body)
    if not dash:
        raise HTTPException(status_code=404, detail=f"Dashboard '{dashboard_id}' not found.")
    return dash


@router.delete("/api/dashboards/{dashboard_id}", status_code=204)
@router.delete("/dashboards/{dashboard_id}", status_code=204)
def delete_dashboard_endpoint(dashboard_id: str) -> None:
    """Delete a saved dashboard by ID."""
    deleted = dashboards.delete_dashboard(dashboard_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Dashboard '{dashboard_id}' not found.")


# -------------------------------------------------------------
# Saved Analytical Queries (BI-35 / Issue #60)
# -------------------------------------------------------------
@router.get("/api/queries", response_model=list[SavedQuery])
@router.get("/queries", response_model=list[SavedQuery])
@router.get("/api/saved-queries", response_model=list[SavedQuery])
@router.get("/saved-queries", response_model=list[SavedQuery])
def list_saved_queries_endpoint(
    datasource_id: str | None = None,
    tag: str | None = None,
    search: str | None = None,
) -> list[SavedQuery]:
    """List all saved analytical queries with optional filters."""
    return saved_queries.list_saved_queries(
        datasource_id=datasource_id, tag=tag, search=search
    )


@router.post("/api/queries", response_model=SavedQuery, status_code=201)
@router.post("/queries", response_model=SavedQuery, status_code=201)
@router.post("/api/saved-queries", response_model=SavedQuery, status_code=201)
@router.post("/saved-queries", response_model=SavedQuery, status_code=201)
def create_saved_query_endpoint(body: SavedQueryCreateRequest) -> SavedQuery:
    """Create and persist a new saved analytical query."""
    try:
        return saved_queries.create_saved_query(body)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create query: {exc}") from exc


@router.get("/api/queries/{query_id}", response_model=SavedQuery)
@router.get("/queries/{query_id}", response_model=SavedQuery)
@router.get("/api/saved-queries/{query_id}", response_model=SavedQuery)
@router.get("/saved-queries/{query_id}", response_model=SavedQuery)
def get_saved_query_endpoint(query_id: str) -> SavedQuery:
    """Retrieve saved query details by ID."""
    q = saved_queries.get_saved_query_by_id(query_id)
    if not q:
        raise HTTPException(status_code=404, detail=f"Query '{query_id}' not found.")
    return q


@router.put("/api/queries/{query_id}", response_model=SavedQuery)
@router.put("/queries/{query_id}", response_model=SavedQuery)
@router.put("/api/saved-queries/{query_id}", response_model=SavedQuery)
@router.put("/saved-queries/{query_id}", response_model=SavedQuery)
def update_saved_query_endpoint(query_id: str, body: SavedQueryUpdateRequest) -> SavedQuery:
    """Update title, description, question, sql, or tags of a saved query."""
    q = saved_queries.update_saved_query(query_id, body)
    if not q:
        raise HTTPException(status_code=404, detail=f"Query '{query_id}' not found.")
    return q


@router.delete("/api/queries/{query_id}", status_code=204)
@router.delete("/queries/{query_id}", status_code=204)
@router.delete("/api/saved-queries/{query_id}", status_code=204)
@router.delete("/saved-queries/{query_id}", status_code=204)
def delete_saved_query_endpoint(query_id: str) -> None:
    """Delete a saved query by ID."""
    deleted = saved_queries.delete_saved_query(query_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Query '{query_id}' not found.")


@router.post("/api/queries/{query_id}/execute", response_model=SavedQueryExecuteResponse)
@router.post("/queries/{query_id}/execute", response_model=SavedQueryExecuteResponse)
@router.post("/api/saved-queries/{query_id}/execute", response_model=SavedQueryExecuteResponse)
@router.post("/saved-queries/{query_id}/execute", response_model=SavedQueryExecuteResponse)
def execute_saved_query_endpoint(query_id: str) -> SavedQueryExecuteResponse:
    """Execute a saved analytical query and return fresh results."""
    try:
        return saved_queries.execute_saved_query(query_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Query execution failed: {exc}") from exc


# -------------------------------------------------------------
# Query Execution Statuses & Background Jobs (BI-07 / Issue #35)
# -------------------------------------------------------------
@router.post("/api/chat/jobs", response_model=JobCreateResponse, status_code=202)
@router.post("/api/queries/jobs", response_model=JobCreateResponse, status_code=202)
def start_query_job_endpoint(
    body: ChatRequest,
    background_tasks: BackgroundTasks,
) -> JobCreateResponse:
    """Submit an analytical query for asynchronous execution with progress tracking."""
    job = query_tracker.create_job(
        message=body.message,
        session_id=body.session_id,
        datasource_id=body.datasource_id,
        table_name=body.table_name,
    )
    background_tasks.add_task(query_tracker.execute_job_worker, job.job_id)
    return JobCreateResponse(
        job_id=job.job_id,
        status="pending",
        message="Query execution job submitted successfully",
    )


@router.get("/api/chat/jobs/{job_id}", response_model=JobStatusResponse)
@router.get("/api/queries/jobs/{job_id}", response_model=JobStatusResponse)
@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_query_job_endpoint(job_id: str) -> JobStatusResponse:
    """Retrieve execution status, current step, and results of an analytical query job."""
    job = query_tracker.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@router.get("/api/chat/jobs", response_model=list[JobStatusResponse])
@router.get("/api/queries/jobs", response_model=list[JobStatusResponse])
def list_query_jobs_endpoint(limit: int = 50) -> list[JobStatusResponse]:
    """List recent analytical query execution jobs."""
    return query_tracker.list_jobs(limit=limit)


@router.get("/api/chat/stream")
def stream_chat_endpoint(
    message: str,
    session_id: str | None = None,
    datasource_id: str | None = None,
    table_name: str | None = None,
) -> StreamingResponse:
    """Server-Sent Events (SSE) endpoint streaming execution progress steps in real time."""
    import json

    def event_generator():
        yield f"data: {json.dumps({'type': 'status', 'step': 'init', 'title': 'Инициализация запроса'})}\n\n"
        recorder = query_tracker.StepRecorder()
        try:
            recorder.step("parse_intent", "Анализ вопроса и ограничений")
            yield f"data: {json.dumps({'type': 'step', 'step': 'parse_intent', 'title': 'Анализ вопроса и ограничений', 'status': 'in_progress'})}\n\n"

            result = agent.process_chat_message(
                message=message,
                session_id=session_id,
                datasource_id=datasource_id,
                table_name=table_name,
                recorder=recorder,
            )
            for s in recorder.steps:
                yield f"data: {json.dumps({'type': 'step', 'step': s.step, 'title': s.title, 'status': s.status, 'duration_ms': s.duration_ms})}\n\n"

            chat_resp = ChatResponse(**result)
            yield f"data: {json.dumps({'type': 'result', 'data': chat_resp.model_dump()})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# -------------------------------------------------------------
# Business KPIs (BI-32 / Issue #78)
# -------------------------------------------------------------
@router.get("/api/kpis", response_model=BusinessKpisResponse)
def get_business_kpis_endpoint(
    datasource_id: str = "raw",
    calculate: bool = True,
) -> BusinessKpisResponse:
    """Return verified business KPIs with calculated values from technical catalog."""
    data = kpis.get_kpis(datasource_id=datasource_id, calculate=calculate)
    return BusinessKpisResponse(**data)


@router.post("/api/kpis/{kpi_id}/approve", response_model=BusinessKpiItem)
def approve_business_kpi_endpoint(
    kpi_id: str,
    body: BusinessKpiApproveRequest,
    datasource_id: str = "raw",
) -> BusinessKpiItem:
    """Sign off / approve a proposed business KPI definition with persistent audit trail."""
    updated = kpis.approve_kpi(
        kpi_id=kpi_id,
        approved_by=body.approved_by,
        comment=body.comment,
        datasource_id=datasource_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"KPI '{kpi_id}' not found.")
    return BusinessKpiItem(**updated)


# -------------------------------------------------------------
# PDF Export (BI-40 / Issue #82)
# -------------------------------------------------------------
@router.get("/api/dashboards/{dashboard_id}/pdf")
def export_dashboard_pdf_endpoint(dashboard_id: str) -> StreamingResponse:
    """Generate and stream a professional PDF report for a saved dashboard."""
    try:
        pdf_bytes = pdf_export.export_dashboard_to_pdf(dashboard_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Dashboard PDF export failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"PDF export failed: {exc}") from exc

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="dashboard_{dashboard_id}.pdf"'
        },
    )


@router.post("/api/export/pdf")
def export_custom_pdf_endpoint(body: PdfExportRequest) -> StreamingResponse:
    """Generate and stream a professional PDF report from query results, tables, or charts."""
    try:
        table_dict = body.table.model_dump() if body.table else None
        chart_dict = body.chart.model_dump() if body.chart else None
        widgets_list = [w.model_dump() for w in body.widgets] if body.widgets else None

        pdf_bytes = pdf_export.build_pdf_document(
            title=body.title,
            subtitle=body.subtitle,
            datasource_id=body.datasource_id,
            kpis=body.kpis,
            table_data=table_dict,
            chart_data=chart_dict,
            sql_text=body.sql,
            widgets=widgets_list,
        )
    except Exception as exc:
        logger.error("Custom PDF export failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"PDF export failed: {exc}") from exc

    filename = "analytical_report.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )