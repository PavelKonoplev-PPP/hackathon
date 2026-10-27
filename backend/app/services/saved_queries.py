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
except ImportError:
    from llm.sql_guard import SQLRejected, validate_sql

from ..config import get_settings

logger = logging.getLogger(__name__)


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
    """Execute a saved analytical query by ID.

    If SQL query is preserved, runs SQL directly on the designated datasource.
    Otherwise delegates to the analytical chat agent.
    """
    query = get_saved_query_by_id(query_id)
    if not query:
        raise ValueError(f"Saved query '{query_id}' not found")

    start_time = time.perf_counter()

    # 1. Direct SQL execution path
    if query.sql:
        # BI-35: Security hardening
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
            with conn.cursor() as cur:
                cur.execute(query.sql)
                cols = [desc[0] for desc in cur.description] if cur.description else []
                raw_rows = cur.fetchall() if cur.description else []

            # Format rows
            formatted_rows: list[list[Any]] = []
            for row in raw_rows:
                formatted_rows.append([
                    str(v) if not isinstance(v, (int, float, bool, type(None))) else v
                    for v in row
                ])

            table_spec = TableSpec(columns=cols, rows=formatted_rows)
            chart_spec = None

            # Build default chart if applicable
            if query.chart_config:
                chart_type = query.chart_config.get("type", "bar")
                x_idx = 0
                y_idx = 1 if len(cols) > 1 else 0
                categories = [str(r[x_idx]) for r in formatted_rows[:20]]
                series_vals = [r[y_idx] if len(r) > y_idx else 0 for r in formatted_rows[:20]]
                chart_spec = ChartSpec(
                    type=chart_type if chart_type in ("bar", "line", "pie") else "bar",
                    title=query.title,
                    x_label=cols[x_idx] if cols else None,
                    y_label=cols[y_idx] if len(cols) > 1 else None,
                    categories=categories,
                    series=[ChartSeries(name=cols[y_idx] if len(cols) > 1 else "Значение", data=series_vals)],
                )

            duration = round((time.perf_counter() - start_time) * 1000, 2)
            steps = [
                ExecutionStep(
                    step="load_query",
                    title="Загрузка сохранённого запроса",
                    status="completed",
                    duration_ms=round(duration * 0.1, 2),
                ),
                ExecutionStep(
                    step="execute_sql",
                    title="Выполнение SQL запроса",
                    status="completed",
                    duration_ms=round(duration * 0.9, 2),
                ),
            ]

            return SavedQueryExecuteResponse(
                query_id=query.id,
                title=query.title,
                datasource_id=query.datasource_id,
                status="completed",
                execution_time_ms=duration,
                result=ChatResponse(
                    answer=f"Запрос «{query.title}» успешно выполнен. Получено строк: {len(formatted_rows)}.",
                    table=table_spec,
                    chart=chart_spec,
                    sql=query.sql,
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

    # 2. Natural language question re-execution path
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