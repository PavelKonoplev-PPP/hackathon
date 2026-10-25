import json
import re
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..db.session import get_reader_connection
from ..llm.client import chat

SYSTEM_PROMPT = (
    "Ты аналитический ассистент. Отвечай на русском языке кратко и по делу. "
    "Доступа к данным пока нет."
)

_SESSION_HISTORY: dict[str, list[dict[str, str]]] = {}

# Try importing teammate's LLM core modules from PR #21 / #22
try:
    from backend.llm.ask import answer_question  # type: ignore
    from backend.llm.describe import describe_data  # type: ignore
    from backend.llm.sql_runner import PostgresRunner  # type: ignore
    from backend.llm.semantics import (  # type: ignore
        check_destructive_mutation,
        detect_ambiguous_query,
        detect_out_of_scope,
    )
    from backend.llm.catalog_summary import display_table_name, match_table_name  # type: ignore
    HAS_LLM_MODULE = True
except ImportError:
    try:
        from llm.ask import answer_question  # type: ignore
        from llm.describe import describe_data  # type: ignore
        from llm.sql_runner import PostgresRunner  # type: ignore
        from llm.semantics import (  # type: ignore
            check_destructive_mutation,
            detect_ambiguous_query,
            detect_out_of_scope,
        )
        from llm.catalog_summary import display_table_name, match_table_name  # type: ignore
        HAS_LLM_MODULE = True
    except ImportError:
        HAS_LLM_MODULE = False

        def check_destructive_mutation(q: str):  # type: ignore
            return None

        def detect_out_of_scope(q: str):  # type: ignore
            return None

        def detect_ambiguous_query(q: str):  # type: ignore
            return None

        def display_table_name(name: str) -> str:  # type: ignore
            if name.startswith("data_"):
                parts = name.split("_", 2)
                if len(parts) == 3 and parts[1].isdigit():
                    return parts[2].replace("_", " ")
            return name

        def match_table_name(table: dict, target: str) -> bool:  # type: ignore
            raw = (table.get("name") or "").strip()
            disp = display_table_name(raw).strip()
            q = target.strip()
            if not q or not raw:
                return False
            if raw == q or disp == q:
                return True
            q_lower = q.lower()
            raw_lower = raw.lower()
            disp_lower = disp.lower()
            if raw_lower == q_lower or disp_lower == q_lower:
                return True
            if raw_lower.replace("_", " ") == q_lower.replace("_", " "):
                return True
            if disp_lower.replace("_", " ") == q_lower.replace("_", " "):
                return True
            if q_lower.startswith("raw."):
                q_no_schema = q_lower[4:]
                if raw_lower == q_no_schema or disp_lower == q_no_schema:
                    return True
            if raw_lower.startswith("data_") and raw_lower[5:] == q_lower:
                return True
            parts = raw_lower.split("_", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                code = f"data_{parts[1]}"
                num = parts[1]
                num_int = str(int(parts[1]))
                if q_lower in (code, num, num_int, f"data_{num_int}"):
                    return True
                if q_lower.startswith(code + "_"):
                    return True
            return False



class AgentServiceError(RuntimeError):
    """Raised when agent processing fails."""


def _resolve_path(path_str: str) -> Path | None:
    """Find a relative path from current directory or parent repository root."""
    candidate = Path(path_str)
    if candidate.is_file():
        return candidate
    parent_candidate = Path("..") / path_str
    if parent_candidate.is_file():
        return parent_candidate
    root_candidate = Path(__file__).resolve().parents[3] / path_str
    if root_candidate.is_file():
        return root_candidate
    return None


def load_catalogs() -> tuple[dict | None, dict | None]:
    """Load schema and relationship catalogs from JSON files if available."""
    settings = get_settings()
    schema_file = _resolve_path(settings.schema_catalog_path)
    rel_file = _resolve_path(settings.relationship_catalog_path)

    catalog = None
    relationships = None

    if schema_file:
        try:
            catalog = json.loads(schema_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            catalog = None

    if rel_file:
        try:
            relationships = json.loads(rel_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            relationships = None

    return catalog, relationships


DESCRIBE_PATTERNS = [
    re.compile(r"\b(опиши|описание|структур\w*|обзор\w*)\b.*?\b(данн\w*|таблиц\w*|е[ёе])\b", re.IGNORECASE),
    re.compile(r"\b(данн\w*|таблиц\w*)\b.*?\b(опиши|описание|структур\w*|обзор\w*)\b", re.IGNORECASE),
    re.compile(r"что\s+(есть\s+|это\s+за\s+|за\s+)?(в\s+|за\s+)?(данн\w*|таблиц\w*|этих\s+данн\w*|этой\s+таблиц\w*|ней)\b", re.IGNORECASE),
    re.compile(r"расскажи\s+(о|про|обо)\s+(данн\w*|таблиц\w*|эту\s+таблиц\w*|ней)\b", re.IGNORECASE),
    re.compile(r"(какие|список|покажи)\s+таблиц\w*", re.IGNORECASE),
    re.compile(r"что\s+(передо\s+мной|перед\s+нами)", re.IGNORECASE),
    re.compile(r"^\s*(опиши|обзор|структура|что\s+в\s+ней|что\s+здесь|что\s+тут)\s*[?!.]*$", re.IGNORECASE),
]


def is_describe_intent(question: str) -> bool:
    """Detect if the query asks for a broad overview/description of dataset or table."""
    q = question.lower().strip()
    triggers = [
        "что в данных",
        "что есть в данных",
        "что есть в этих данных",
        "что в этих данных",
        "что это за данные",
        "что за данные",
        "что передо мной",
        "что перед нами",
        "какие данные",
        "опиши данные",
        "описание данных",
        "опиши таблицы",
        "какие таблицы",
        "список таблиц",
        "структура данных",
        "покажи таблицы",
        "расскажи о данных",
        "расскажи про данные",
        "обзор данных",
        "опиши таблицу",
        "описание таблицы",
        "что в таблице",
        "что в этой таблице",
        "что за таблица",
        "структура таблицы",
        "обзор таблицы",
        "расскажи о таблице",
        "расскажи про таблицу",
    ]
    if any(t in q for t in triggers):
        return True
    return any(p.search(question.strip()) for p in DESCRIBE_PATTERNS)


def filter_catalog_for_scope(
    catalog: dict | None,
    relationships: dict | None = None,
    table_name: str | None = None,
    datasource_id: str | None = None,
) -> tuple[dict | None, dict | None, str | None]:
    """Filter catalog and relationships to a specific table or datasource scope.

    Returns:
        (filtered_catalog, filtered_relationships, error_message)
    """
    if catalog is None:
        return None, None, None

    tables = catalog.get("tables") or []
    if not isinstance(tables, list):
        tables = []

    available_names = [display_table_name(t.get("name", "")) for t in tables if isinstance(t, dict)]
    available_str = ", ".join(f"«{n}»" for n in available_names if n)

    # 1. Neither table_name nor datasource_id is specified -> full catalog
    if not table_name and not datasource_id:
        return catalog, relationships, None

    # 2. Check datasource_id if provided
    schema_name = catalog.get("schema") or "raw"
    matched_by_source = None
    if datasource_id:
        ds_clean = datasource_id.strip()
        if ds_clean.lower() == schema_name.lower() or ds_clean.lower() == "default":
            # Entire schema datasource selected
            pass
        else:
            # Check if datasource_id directly names a table
            for t in tables:
                if isinstance(t, dict) and match_table_name(t, ds_clean):
                    matched_by_source = t
                    break
            if not matched_by_source:
                return (
                    None,
                    None,
                    f"Источник данных «{datasource_id}» не найден в каталоге данных. "
                    f"Доступные таблицы: {available_str}."
                )

    # 3. Resolve target table
    target_table = None
    if table_name:
        t_clean = table_name.strip()
        for t in tables:
            if isinstance(t, dict) and match_table_name(t, t_clean):
                target_table = t
                break
        if target_table is None:
            return (
                None,
                None,
                f"Таблица «{table_name}» не найдена в каталоге данных. "
                f"Доступные таблицы: {available_str}."
            )
        if matched_by_source and matched_by_source.get("name") != target_table.get("name"):
            return (
                None,
                None,
                f"Таблица «{table_name}» не принадлежит источнику данных «{datasource_id}». "
                f"Доступные таблицы: {available_str}."
            )
    elif matched_by_source:
        target_table = matched_by_source

    # If no specific table was targeted (e.g. datasource_id="raw" with all tables)
    if target_table is None:
        return catalog, relationships, None

    # Filter catalog to strictly this active table
    filtered_cat = dict(catalog)
    filtered_cat["tables"] = [target_table]
    if isinstance(catalog.get("summary"), dict):
        filtered_sum = dict(catalog["summary"])
        filtered_sum["tables"] = 1
        filtered_sum["columns"] = len(target_table.get("columns", []))
        filtered_cat["summary"] = filtered_sum

    # Filter relationships: strictly keep intra-table self-references for target table
    filtered_rel = None
    if isinstance(relationships, dict):
        filtered_rel = dict(relationships)
        target_name = target_table.get("name", "")
        raw_joins = (
            relationships.get("join_candidates")
            or relationships.get("relationships")
            or []
        )
        if isinstance(raw_joins, list):
            kept_joins = []
            for jc in raw_joins:
                if not isinstance(jc, dict):
                    continue
                src = jc.get("source") if isinstance(jc.get("source"), dict) else {}
                tgt = jc.get("target") if isinstance(jc.get("target"), dict) else {}
                src_tbl = src.get("table") or jc.get("source_table")
                tgt_tbl = tgt.get("table") or jc.get("target_table")
                if src_tbl == target_name and tgt_tbl == target_name:
                    kept_joins.append(jc)
            if "join_candidates" in relationships:
                filtered_rel["join_candidates"] = kept_joins
            if "relationships" in relationships:
                filtered_rel["relationships"] = kept_joins

    return filtered_cat, filtered_rel, None


def process_chat_message(
    message: str,
    session_id: str | None = None,
    catalog: dict | None = None,
    relationships: dict | None = None,
    runner: Any = None,
    datasource_id: str | None = None,
    table_name: str | None = None,
) -> dict:
    """Process a chat message conforming to docs/api-contract.md.

    Uses teammate's describe_data / answer_question when available,
    otherwise falls back to basic chat completion.
    """
    # 1. Защита от деструктивных операций (DDL/DML)
    mutation = check_destructive_mutation(message)
    if mutation:
        return mutation.to_dict()

    # 2. Неподдерживаемые метрики и сущности, отсутствующие в датасете
    out_of_scope = detect_out_of_scope(message)
    if out_of_scope:
        return out_of_scope.to_dict()

    if HAS_LLM_MODULE:
        if catalog is None:
            catalog, loaded_rel = load_catalogs()
            if relationships is None:
                relationships = loaded_rel

        if catalog is None:
            return {
                "answer": (
                    "Каталог данных ещё не сгенерирован (data/schema-catalog.json не найден). "
                    "Выполните сборку каталога перед аналитическими запросами."
                ),
                "chart": None,
                "table": None,
                "sql": None,
                "clarification": None,
            }

        # Catalog filtering helper for active scope
        if table_name or datasource_id:
            catalog, relationships, scope_error = filter_catalog_for_scope(
                catalog,
                relationships,
                table_name=table_name,
                datasource_id=datasource_id,
            )
            if scope_error:
                return {
                    "answer": scope_error,
                    "chart": None,
                    "table": None,
                    "sql": None,
                    "clarification": None,
                }

        # 3. Слишком обобщённый / неоднозначный запрос (если не запрос описания данных и таблица не зафиксирована)
        if not is_describe_intent(message):
            is_single_table_scoped = catalog is not None and len(catalog.get("tables", [])) == 1
            if not is_single_table_scoped:
                ambiguous = detect_ambiguous_query(message)
                if ambiguous:
                    return ambiguous.to_dict()

        if is_describe_intent(message):
            try:
                res = describe_data(
                    catalog,
                    relationships=relationships,
                    table_name=table_name,
                )
                return {
                    "answer": res.get("answer", ""),
                    "chart": res.get("chart"),
                    "table": res.get("table"),
                    "sql": res.get("sql"),
                    "clarification": res.get("clarification"),
                }
            except Exception as exc:
                raise AgentServiceError(f"Ошибка при описании данных: {exc}") from exc

        # Analytical SQL query
        conn = None
        if runner is None:
            try:
                conn = get_reader_connection()
                runner = PostgresRunner(conn)
            except Exception as exc:
                raise AgentServiceError(f"Не удалось подключиться к базе данных под bi_reader: {exc}") from exc

        try:
            res = answer_question(
                message,
                catalog,
                runner,
                session_id=session_id,
                table_name=table_name,
            )
            return {
                "answer": res.get("answer", ""),
                "chart": res.get("chart"),
                "table": res.get("table"),
                "sql": res.get("sql"),
                "clarification": res.get("clarification"),
            }
        except Exception as exc:
            raise AgentServiceError(f"Ошибка при ответе на вопрос: {exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    else:
        loaded_cat, loaded_rel = load_catalogs()
        if (table_name or datasource_id) and loaded_cat:
            _, _, scope_error = filter_catalog_for_scope(
                loaded_cat,
                loaded_rel,
                table_name=table_name,
                datasource_id=datasource_id,
            )
            if scope_error:
                return {
                    "answer": scope_error,
                    "chart": None,
                    "table": None,
                    "sql": None,
                    "clarification": None,
                }

    ans = answer(message, session_id=session_id)
    return {
        "answer": ans,
        "chart": None,
        "table": None,
        "sql": None,
        "clarification": None,
    }


def answer(question: str, session_id: str | None = None) -> str:
    """Generate an analytical response for a user question using the LLM."""
    mutation = check_destructive_mutation(question)
    if mutation:
        return mutation.to_dict()["answer"]
    out_of_scope = detect_out_of_scope(question)
    if out_of_scope:
        return out_of_scope.to_dict()["answer"]
    ambiguous = detect_ambiguous_query(question)
    if ambiguous:
        return ambiguous.to_dict()["answer"]

    if session_id:

        history = _SESSION_HISTORY.setdefault(
            session_id, [{"role": "system", "content": SYSTEM_PROMPT}]
        )
        history.append({"role": "user", "content": question})
        reply = chat(history)
        history.append({"role": "assistant", "content": reply})
        return reply

    return chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
    )


def clear_session(session_id: str) -> None:
    """Clear memory for a given session."""
    _SESSION_HISTORY.pop(session_id, None)
    if HAS_LLM_MODULE:
        try:
            from backend.llm.ask import reset_session
        except ImportError:
            from llm.ask import reset_session  # type: ignore
        reset_session(session_id)
