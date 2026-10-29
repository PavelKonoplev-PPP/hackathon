from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..api.schemas import (
    ChatResponse,
    ChartSeries,
    ChartSpec,
    ExecutionStep,
    SavedQuery,
    SavedQueryCreateRequest,
    SavedQueryExecuteResponse,
    SavedQueryUpdateRequest,
    TableSpec,
)
try:
    from backend.llm.sql_guard import SQLRejected, validate_sql
    from backend.llm.sql_runner import PostgresRunner
    from backend.llm.catalog_summary import match_table_name, display_table_name
except ImportError:
    from llm.sql_guard import SQLRejected, validate_sql
    from llm.sql_runner import PostgresRunner  # type: ignore
    from llm.catalog_summary import match_table_name, display_table_name  # type: ignore

from ..config import get_settings

logger = logging.getLogger(__name__)

# Pattern: SQL written for the agent's CTE alias «t» (FROM t / JOIN t / -- t = …)
_AGENT_CTE_RE = re.compile(
    r'(?:^|\n)\s*--\s*t\s*='       # comment header line
    r'|\bfrom\s+t\b'               # FROM t
    r'|\bjoin\s+t\b',              # JOIN t
    re.IGNORECASE | re.MULTILINE,
)
# Extract table name from comment: -- t = данные таблицы «Name» с …
_TABLE_NAME_RE = re.compile(
    r'\u00ab(.+?)\u00bb'           # «…»
    r'|"(.+?)"'                    # "…"
    r"|'(.+?)'"                    # '…'
)


def _extract_table_from_comment(sql: str) -> str | None:
    """Parse the agent's SQL comment header to extract the source table name."""
    first_line_m = re.match(r'^--[^\n]*', sql)
    if not first_line_m:
        return None
    m = _TABLE_NAME_RE.search(first_line_m.group(0))
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def _load_catalog_for_datasource(datasource_id: str) -> dict | None:
    """Load schema catalog for a datasource (raw or external)."""
    if not datasource_id or datasource_id.lower() == "raw":
        try:
            from .agent import load_catalogs
            cat, _ = load_catalogs()
            return cat
        except Exception:
            return None
    # External datasource
    ext_path = Path("data/external-catalogs") / datasource_id / "schema-catalog.json"
    if ext_path.is_file():
        try:
            return json.loads(ext_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        from db.external_catalog import read_external_catalog
        res = read_external_catalog(Path("data"), datasource_id)
        if res:
            return res
        from .datasources import get_datasources_path
        data_dir = get_datasources_path().parent
        if Path(data_dir).resolve() != Path("data").resolve():
            return read_external_catalog(data_dir, datasource_id)
    except Exception:
        return None


def _build_chart(
    cols: list[str],
    formatted_rows: list[list[Any]],
    chart_config: dict | None,
    title: str,
) -> ChartSpec | None:
    if not chart_config:
        return None
    chart_type = chart_config.get("type", "bar")
    x_idx, y_idx = 0, (1 if len(cols) > 1 else 0)
    categories = [str(r[x_idx]) for r in formatted_rows[:20]]
    series_vals = [r[y_idx] if len(r) > y_idx else 0 for r in formatted_rows[:20]]
    return ChartSpec(
        type=chart_type if chart_type in ("bar", "line", "pie") else "bar",
        title=title,
        x_label=cols[x_idx] if cols else None,
        y_label=cols[y_idx] if len(cols) > 1 else None,
        categories=categories,
        series=[ChartSeries(name=cols[y_idx] if len(cols) > 1 else "Значение", data=series_vals)],
    )


def get_saved_queries_path() -> Path:
    """Resolve the path to data/saved_queries.json, ensuring directory exists."""
    settings = get_settings()
    configured = getattr(settings, "saved_queries_path", "data/saved_queries.json")
    p = Path(configured)
    if p.is_file() or p.parent.is_dir():
        return p
    p_up = Path("..") / configured
    if p_up.is_file() or p_up.parent.is_dir():
        return p_up
    root = Path(__file__).resolve().parents[3] / configured
    root.parent.mkdir(parents=True, exist_ok=True)
    return root


def load_saved_queries_file() -> list[dict[str, Any]]:
    """Load stored queries from JSON file. Returns empty list if missing or invalid."""
    path = get_saved_queries_path()
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "queries" in data and isinstance(data["queries"], list):
            return data["queries"]
        return []
    except Exception as exc:
        logger.warning("Failed to load saved queries from %s: %s", path, exc)
        return []


def save_saved_queries_file(queries: list[dict[str, Any]]) -> None:
    """Persist saved queries list atomically."""
    path = get_saved_queries_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except Exception as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        logger.error("Failed to save queries to %s: %s", path, exc)
        raise IOError(f"Could not write saved queries file: {exc}") from exc


def list_saved_queries(
    datasource_id: str | None = None,
    tag: str | None = None,
    search: str | None = None,
) -> list[SavedQuery]:
    """Return all saved queries with optional filtering."""
    raw_list = load_saved_queries_file()
    result: list[SavedQuery] = []
    search_lower = search.strip().lower() if search else None
    ds_lower = datasource_id.strip().lower() if datasource_id else None
    tag_clean = tag.strip().lower() if tag else None

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        try:
            query = SavedQuery(**item)
        except Exception as exc:
            logger.warning("Skipping invalid saved query record: %s", exc)
            continue

        if ds_lower and (query.datasource_id or "").strip().lower() != ds_lower:
            continue

        if tag_clean and not any(t.lower() == tag_clean for t in query.tags):
            continue

        if search_lower:
            text_corpus = f"{query.title} {query.description or ''} {query.question or ''} {query.sql or ''}".lower()
            if search_lower not in text_corpus:
                continue

        result.append(query)

    result.sort(key=lambda q: q.updated_at, reverse=True)
    return result


def get_saved_query_by_id(query_id: str) -> SavedQuery | None:
    """Retrieve a saved query by its unique ID."""
    raw_list = load_saved_queries_file()
    for item in raw_list:
        if isinstance(item, dict) and item.get("id") == query_id:
            try:
                return SavedQuery(**item)
            except Exception:
                return None
    return None


def create_saved_query(req: SavedQueryCreateRequest) -> SavedQuery:
    """Create and persist a new saved analytical query."""
    now_iso = datetime.now(timezone.utc).isoformat()
    query_id = uuid.uuid4().hex[:12]

    new_query = SavedQuery(
        id=query_id,
        title=req.title.strip(),
        description=req.description.strip() if req.description else None,
        question=req.question.strip() if req.question else None,
        sql=req.sql.strip() if req.sql else None,
        datasource_id=req.datasource_id or "raw",
        table_name=req.table_name.strip() if req.table_name else None,
        chart_config=req.chart_config,
        tags=[t.strip() for t in req.tags if t.strip()],
        created_at=now_iso,
        updated_at=now_iso,
    )

    raw_list = load_saved_queries_file()
    raw_list.append(new_query.model_dump())
    save_saved_queries_file(raw_list)
    return new_query


def update_saved_query(query_id: str, req: SavedQueryUpdateRequest) -> SavedQuery | None:
    """Update an existing saved analytical query."""
    raw_list = load_saved_queries_file()
    target_idx = None
    for idx, item in enumerate(raw_list):
        if isinstance(item, dict) and item.get("id") == query_id:
            target_idx = idx
            break

    if target_idx is None:
        return None

    current = raw_list[target_idx]
    now_iso = datetime.now(timezone.utc).isoformat()

    if req.title is not None:
        current["title"] = req.title.strip()
    if req.description is not None:
        current["description"] = req.description.strip() if req.description else None
    if req.question is not None:
        current["question"] = req.question.strip() if req.question else None
    if req.sql is not None:
        current["sql"] = req.sql.strip() if req.sql else None
    if req.datasource_id is not None:
        current["datasource_id"] = req.datasource_id or "raw"
    if req.table_name is not None:
        current["table_name"] = req.table_name.strip() if req.table_name else None
    if req.chart_config is not None:
        current["chart_config"] = req.chart_config
    if req.tags is not None:
        current["tags"] = [t.strip() for t in req.tags if t.strip()]

    current["updated_at"] = now_iso

    updated_obj = SavedQuery(**current)
    raw_list[target_idx] = updated_obj.model_dump()
    save_saved_queries_file(raw_list)
    return updated_obj


def delete_saved_query(query_id: str) -> bool:
    """Delete a saved query by ID. Returns True if found and deleted."""
    raw_list = load_saved_queries_file()
    new_list = [item for item in raw_list if isinstance(item, dict) and item.get("id") != query_id]
    if len(new_list) == len(raw_list):
        return False
    save_saved_queries_file(new_list)
    return True


def execute_saved_query(query_id: str) -> SavedQueryExecuteResponse:
    """Execute a saved analytical query by ID (BI-35 / Issue #85 / Issue #80).

    SQL execution strategy:
    - If sql contains the agent's CTE alias ``t`` (written by PostgresRunner), we
      re-execute through PostgresRunner so type-casting and read-only mode are applied
      correctly — this fixes the ``relation "t" does not exist`` crash.
    - Plain SQL (referencing real schema tables) runs directly in a read-only
      transaction with a row limit and statement timeout (BI-35 security hardening).
    - If no sql is stored (frontend workaround for agent SQL), we replay the natural-
      language question through the agent.
    """
    query = get_saved_query_by_id(query_id)
    if not query:
        raise ValueError(f"Saved query '{query_id}' not found")

    start_time = time.perf_counter()

    # ── 1. SQL execution path ────────────────────────────────────────────────
    if query.sql:
        # Security guard: reject DDL/DML and multi-statement queries (BI-35 / #80)
        try:
            validate_sql(query.sql)
        except SQLRejected as exc:
            raise ValueError(f"Безопасность: запрос отклонён: {exc}")

        from ..db.session import get_reader_connection

        conn = None
        ext_schema = "raw"
        if query.datasource_id and query.datasource_id.lower() != "raw":
            from .datasources import get_datasource_connection
            conn, ext_schema, _ = get_datasource_connection(query.datasource_id)
        else:
            conn = get_reader_connection()

        try:
            uses_cte_t = bool(_AGENT_CTE_RE.search(query.sql))
            resolved_table_name: str | None = query.table_name

            if uses_cte_t:
                # --- Agent SQL path: re-run via PostgresRunner ---
                # 1. Determine source table name
                if not resolved_table_name:
                    resolved_table_name = _extract_table_from_comment(query.sql)

                # 2. Load catalog to get column metadata for type-safe CTE
                cat = _load_catalog_for_datasource(query.datasource_id)
                target_table: dict | None = None
                if cat and isinstance(cat.get("tables"), list):
                    tables: list[dict] = cat["tables"]
                    if resolved_table_name:
                        for t in tables:
                            if isinstance(t, dict) and match_table_name(t, resolved_table_name):
                                target_table = t
                                resolved_table_name = t.get("name")
                                break
                    # Single-table datasource: use it even without explicit table_name
                    if target_table is None and len(tables) == 1:
                        target_table = tables[0]
                        resolved_table_name = target_table.get("name")

                if target_table is None:
                    raise ValueError(
                        f"Запрос обращается к представлению 't', но таблица "
                        f"'{resolved_table_name or 'не указана'}' не найдена "
                        f"в каталоге источника '{query.datasource_id}'. "
                        f"Сохраните запрос заново — фронтенд передаст table_name."
                    )

                # 3. Strip the comment header, then run via PostgresRunner
                clean_sql = re.sub(r'(?m)^\s*--[^\n]*\n?', '', query.sql, count=1).strip()
                runner = PostgresRunner(conn, schema=ext_schema, max_rows=500)
                run_res = runner.run(target_table, clean_sql)
                cols: list[str] = run_res.columns
                raw_rows: list = list(run_res.rows)

            else:
                # --- Plain SQL path: read-only transaction with limits (#80) ---
                with conn.transaction():
                    cur = conn.cursor()
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("SET LOCAL statement_timeout = 20000")
                    cur.execute(query.sql)
                    cols = [desc.name for desc in cur.description] if cur.description else []
                    raw_rows = list(cur.fetchmany(500)) if cur.description else []
                    resolved_table_name = resolved_table_name or query.table_name

            # Format rows (all values to JSON-safe types)
            formatted_rows: list[list[Any]] = [
                [str(v) if not isinstance(v, (int, float, bool, type(None))) else v for v in row]
                for row in raw_rows
            ]

            table_spec = TableSpec(columns=cols, rows=formatted_rows)
            chart_spec = _build_chart(cols, formatted_rows, query.chart_config, query.title)

            duration = round((time.perf_counter() - start_time) * 1000, 2)
            steps = [
                ExecutionStep(step="load_query", title="Загрузка сохранённого запроса",
                              status="completed", duration_ms=round(duration * 0.1, 2)),
                ExecutionStep(step="execute_sql", title="Выполнение SQL",
                              status="completed", duration_ms=round(duration * 0.9, 2)),
            ]

            return SavedQueryExecuteResponse(
                query_id=query.id,
                title=query.title,
                datasource_id=query.datasource_id,
                status="completed",
                execution_time_ms=duration,
                result=ChatResponse(
                    answer=f"Запрос «{query.title}» выполнен. Получено строк: {len(formatted_rows)}.",
                    table=table_spec,
                    chart=chart_spec,
                    sql=query.sql,
                    table_name=resolved_table_name,
                    status="completed",
                    steps=steps,
                    execution_time_ms=duration,
                ),
            )
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    # ── 2. Natural-language replay path ─────────────────────────────────────
    elif query.question:
        from .agent import process_chat_message

        result_dict = process_chat_message(
            query.question,
            datasource_id=query.datasource_id,
            table_name=query.table_name,
        )
        duration = round((time.perf_counter() - start_time) * 1000, 2)
        chat_resp = ChatResponse(**result_dict)
        return SavedQueryExecuteResponse(
            query_id=query.id,
            title=query.title,
            datasource_id=query.datasource_id,
            status="completed",
            execution_time_ms=duration,
            result=chat_resp,
        )

    # ── 3. Empty query ───────────────────────────────────────────────────────
    else:
        duration = round((time.perf_counter() - start_time) * 1000, 2)
        return SavedQueryExecuteResponse(
            query_id=query.id,
            title=query.title,
            datasource_id=query.datasource_id,
            status="completed",
            execution_time_ms=duration,
            result=ChatResponse(
                answer=f"Сохранённый запрос «{query.title}» не содержит SQL или формулировки вопроса.",
                status="completed",
                execution_time_ms=duration,
            ),
        )
