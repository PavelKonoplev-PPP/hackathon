import json
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
    HAS_LLM_MODULE = True
except ImportError:
    try:
        from llm.ask import answer_question  # type: ignore
        from llm.describe import describe_data  # type: ignore
        from llm.sql_runner import PostgresRunner  # type: ignore
        HAS_LLM_MODULE = True
    except ImportError:
        HAS_LLM_MODULE = False


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


def is_describe_intent(question: str) -> bool:
    """Detect if the query asks for a broad overview/description of the dataset."""
    q = question.lower().strip()
    triggers = [
        "что в данных",
        "какие данные",
        "опиши данные",
        "описание данных",
        "какие таблицы",
        "список таблиц",
        "структура данных",
        "покажи таблицы",
        "расскажи о данных",
    ]
    return any(t in q for t in triggers)


def process_chat_message(
    message: str,
    session_id: str | None = None,
    catalog: dict | None = None,
    relationships: dict | None = None,
    runner: Any = None,
) -> dict:
    """Process a chat message conforming to docs/api-contract.md.

    Uses teammate's describe_data / answer_question when available,
    otherwise falls back to basic chat completion.
    """
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

        if is_describe_intent(message):
            try:
                res = describe_data(catalog, relationships=relationships)
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
            return answer_question(message, catalog, runner, session_id=session_id)
        except Exception as exc:
            raise AgentServiceError(f"Ошибка при ответе на вопрос: {exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

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
