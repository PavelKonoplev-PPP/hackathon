"""Дашборд с нуля по одному текстовому запросу (Issue #117).

План — список обычных вопросов к одной таблице: основа строится кодом по каталогу и словам
запроса, модель только дополняет её. Каждый вопрос проходит через обычного агента со всеми
проверками SQL, отдельной сессией. Дашборд сохраняется, только если верных виджетов не меньше
MIN_WIDGETS; неудачные виджеты не попадают на дашборд и перечисляются в ответе.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Callable

from ..api.schemas import DashboardCreateRequest, DashboardWidget
from . import agent, dashboards

logger = logging.getLogger(__name__)

MIN_WIDGETS = 3
MAX_PLANNED = 5
TIME_BUDGET_S = 300.0
TITLE_MAX = 80

_COMMAND = re.compile(
    r"\b(созда\w*|сдела\w*|собер\w*|собра\w*|построй\w*|сформир\w*|дашборд\w*|панел[ьи]\w*|"
    r"новы\w*|мне|пожалуйста)\b",
    re.IGNORECASE,
)
_NAMED_TABLE = re.compile(r"по таблице «([^»]+)»", re.IGNORECASE)
_JSON_LIST = re.compile(r"\[.*\]", re.DOTALL)
_NOT_A_QUESTION = re.compile(r"дашборд|панел[ьи]", re.IGNORECASE)
_NOT_ADDITIVE = re.compile(r"оценк|рейтинг|балл|норматив|процент|%|срок|время|опоздан|задержк|цена|, ч\b|, мин", re.IGNORECASE)
_COUNT_COLUMN = re.compile(r'count\(\s*"([^"]+)"\s*\)', re.IGNORECASE)


def _llm_ask():
    try:
        from backend.llm import ask
    except ImportError:
        from llm import ask  # type: ignore
    return ask


def request_subject(request: str) -> str:
    """О чём дашборд: запрос без команды («Создай дашборд по продажам» → «по продажам»)."""
    return re.sub(r"\s+", " ", _COMMAND.sub(" ", _NAMED_TABLE.sub(" ", request))).strip(" :,.!?-—\\/")


def dashboard_title(request: str, table: dict) -> str:
    subject = request_subject(request)
    if not subject:
        title = f"Дашборд: {agent.display_table_name(table.get('name', ''))}"
    elif re.match(r"(по|про|о|об|с|для)\s", subject, re.IGNORECASE):
        title = f"Дашборд {subject}"
    else:
        title = f"Дашборд: {subject}"
    if len(title) <= TITLE_MAX:
        return title
    return title[: TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,") + "…"


def _columns_of(table: dict, *types: str) -> list[str]:
    return [c["name"] for c in table.get("columns") or []
            if c.get("inferred_type") in types and c.get("name")]


def _time_grain(request: str) -> str:
    low = request.lower()
    if "кварт" in low:
        return "кварталам"
    if "недел" in low:
        return "неделям"
    if "год" in low and "месяц" not in low:
        return "годам"
    return "месяцам"


def _dimensions(table: dict, request: str) -> list[str]:
    """Разрезы: сначала названные в запросе, затем с удобным для графика числом значений."""
    distinct = {c.get("name"): c.get("sample_distinct") or 0 for c in table.get("columns") or []}
    categories = [c for c in _columns_of(table, "category") if distinct[c] != 1]
    mentioned = [c for c in _llm_ask().mentioned_columns(request, table) if c in categories]
    rest = sorted((c for c in categories if c not in mentioned),
                  key=lambda c: (not 3 <= distinct[c] <= 12, distinct[c]))
    return mentioned + rest


def _measures(table: dict) -> list[str]:
    return [n for n in _columns_of(table, "decimal", "integer")
            if not re.search(r"код|номер|id\b", n, re.IGNORECASE)]


def _metric(table: dict, request: str) -> str:
    """Показатель дашборда: названный в запросе, иначе выручка, деньги или число записей.
    Сумма нормативов или оценок не имеет смысла: такие показатели считаем средним."""
    measures = _measures(table)
    all_columns = len(table.get("columns") or [])
    named = [c for c in _llm_ask().mentioned_columns(request, table, limit=all_columns) if c in measures]
    names = [c.get("name") or "" for c in table.get("columns") or []]
    has_revenue = any(re.search(r"цена", n, re.IGNORECASE) for n in names) and any(
        re.search(r"количеств", n, re.IGNORECASE) for n in names)
    if has_revenue and (not named or re.search(r"выручк", request, re.IGNORECASE)):
        return "Выручка"
    if named:
        return f"{'Среднее' if _NOT_ADDITIVE.search(named[0]) else 'Сумма'} «{named[0]}»"
    money = [n for n in measures if re.search(r"руб|₽", n, re.IGNORECASE)]
    return f"Сумма «{money[0]}»" if money else "Количество записей"


def catalog_questions(table: dict, request: str = "") -> list[str]:
    """План по каталогу и словам запроса: работает с любой таблицей и без модели."""
    dates = _columns_of(table, "date", "timestamp")
    dimensions = _dimensions(table, request)
    metric = _metric(table, request)
    grain = _time_grain(request)

    questions = [f"{metric} по {grain}"] if dates else []
    questions.extend(f"{metric} по «{c}»" for c in dimensions[:3])
    if metric != "Количество записей":
        if dates:
            questions.append(f"Количество записей по {grain}")
        questions.extend(f"Количество записей по «{c}»" for c in dimensions[:2])
    return questions


def _plan_messages(request: str, table: dict) -> list[dict[str, str]]:
    columns = ", ".join(c.get("name") or "" for c in table.get("columns") or [])
    return [
        {"role": "system", "content": (
            "Ты составляешь план аналитического дашборда. Верни только JSON-массив строк без "
            "пояснений, например: [\"Выручка по кварталам\", \"Количество обращений по каналам\"]. "
            "Каждая строка — короткий вопрос к таблице на русском: один показатель в одном разрезе. "
            "Используй только колонки таблицы.")},
        {"role": "user", "content": (
            f"Таблица «{agent.display_table_name(table.get('name', ''))}». Колонки: {columns}.\n"
            f"Запрос: {request}")},
    ]


def parse_plan(text: str) -> list[str]:
    """Вопросы из ответа модели. Всё, что не похоже на вопрос к данным, отбрасываем."""
    match = _JSON_LIST.search(text or "")
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    plan: list[str] = []
    for item in items:
        if isinstance(item, dict):  # слабая модель иногда заворачивает строки в объекты
            item = item.get("Вопрос") or item.get("вопрос") or item.get("question")
        if not isinstance(item, str):
            continue
        question = item.strip()
        if 3 <= len(question) <= 200 and not _NOT_A_QUESTION.search(question) and question not in plan:
            plan.append(question)
    return plan[:MAX_PLANNED]


def plan_questions(request: str, table: dict, client: Any = None) -> tuple[list[str], list[str]]:
    """(вопросы плана, запасные). Основа — по каталогу и словам запроса: слабая модель выдаёт
    неполный план. Модель только дополняет его; недоступна — обходимся без неё."""
    base = catalog_questions(table, request)
    suggested: list[str] = []
    try:
        if client is None:
            try:
                from backend.llm.client import LLMClient
            except ImportError:
                from llm.client import LLMClient  # type: ignore

            client = LLMClient()
        suggested = parse_plan(client.chat(_plan_messages(request, table), max_tokens=400, temperature=0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("План дашборда без модели, она недоступна: %s", exc)
    head = base[:MIN_WIDGETS]
    planned = (head + [q for q in suggested if q not in head])[:MAX_PLANNED]
    reserve = [q for q in base + suggested if q not in planned]
    return planned, reserve


def widget_problem(res: dict, question: str = "", table_meta: dict | None = None) -> str | None:
    """Почему ответ агента нельзя поставить на дашборд; None — можно."""
    if res.get("status") not in (None, "completed"):
        return "агент не дал результата"
    if res.get("clarification"):
        return "нужно уточнение"
    if not res.get("sql"):
        return "нет проверенного расчёта"
    chart, table = res.get("chart"), res.get("table")
    if not chart and not (table and table.get("rows")):
        return "нет данных"
    if chart:
        values = [v for s in chart.get("series") or [] for v in s.get("data") or []]
        if not any(isinstance(v, (int, float)) and v != 0 for v in values):
            return "на графике нет значений"
    labels = [str(c) for c in (table or {}).get("columns") or []]
    labels += [str(s.get("name")) for s in (chart or {}).get("series") or []]
    if not re.search(r"выручк", question, re.IGNORECASE) and any(
            re.search(r"выручк", label, re.IGNORECASE) for label in labels):
        return "подпись показателя не совпадает с вопросом"
    # COUNT("Пассажиры") — это число строк, а не пассажиров: подпись обманет читателя
    measures = _measures(table_meta) if table_meta else []
    if any(c in measures for c in _COUNT_COLUMN.findall(res.get("sql") or "")):
        return "посчитаны строки, а не значения показателя"
    return None


def make_widget(question: str, res: dict, table_name: str) -> DashboardWidget:
    chart, table = res.get("chart"), res.get("table")
    single_row = not chart and table and len(table.get("rows") or []) == 1
    return DashboardWidget(
        title=question,
        type="chart" if chart else "metric" if single_row else "table",
        sql=res.get("sql"),
        chart=chart,
        table=table,
        answer=res.get("answer"),
        source_table=agent.display_table_name(res.get("table_name") or table_name),
        grid={"w": 6, "h": 1 if single_row else 2, "view": None},
    )


def _clarify(answer: str, subject: str, tables: list[dict]) -> dict:
    options = []
    for t in tables[:4]:
        name = agent.display_table_name(t.get("name", ""))
        options.append(f"Создай дашборд по таблице «{name}»" + (f": {subject}" if subject else ""))
    return {
        "answer": answer,
        "status": "clarification",
        "clarification": {"question": "По каким данным собрать дашборд?", "options": options},
    }


def _resolve_table(request: str, catalog: dict) -> tuple[dict | None, dict | None]:
    """(таблица, None) или (None, готовый ответ с уточнением)."""
    tables = [t for t in catalog.get("tables") or [] if isinstance(t, dict)]
    if not tables:
        return None, {"answer": "В источнике нет таблиц, дашборд собирать не из чего.", "status": "failed"}
    if len(tables) == 1:
        return tables[0], None
    named = _NAMED_TABLE.search(request)
    if named:
        for t in tables:
            if agent.match_table_name(t, named.group(1)):
                return t, None
    subject = request_subject(request)
    table, candidates = _llm_ask().choose_table(subject, tables, {}, catalog.get("schema")) if subject else (None, tables)
    if table:
        return table, None
    if candidates:
        return None, _clarify("Для этого дашборда подходит несколько таблиц, уточните источник.", subject, candidates)
    return None, _clarify("В данных не нашлось подходящей таблицы для этого дашборда.", "", tables)


def build_dashboard(
    request: str,
    datasource_id: str | None = None,
    table_name: str | None = None,
    catalog: dict | None = None,
    client: Any = None,
    ask: Callable[..., dict] | None = None,
    on_step: Callable[[str, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    budget_s: float = TIME_BUDGET_S,
) -> dict:
    """Собрать и сохранить дашборд. Результат — поля ChatResponse."""
    ask = ask or agent.process_chat_message
    say = on_step or (lambda _title, _pct: None)
    stopped = is_cancelled or (lambda: False)

    mutation = agent.check_destructive_mutation(request)
    if mutation:
        return {**mutation.to_dict(), "status": "refused"}

    relationships = None
    if catalog is None:
        catalog, relationships = agent.load_catalogs()
    if catalog is None:
        return {"answer": "Каталог данных ещё не построен, дашборд собирать не из чего.", "status": "failed"}
    if datasource_id or table_name:
        catalog, _, scope_error = agent.filter_catalog_for_scope(
            catalog, relationships, table_name=table_name, datasource_id=datasource_id)
        if scope_error:
            return {"answer": scope_error, "status": "failed"}

    subject = request_subject(request)
    if subject:
        out_of_scope = agent.detect_out_of_scope(subject, catalog=catalog)
        if out_of_scope:
            return {**out_of_scope.to_dict(), "status": "refused"}

    table, reply = _resolve_table(request, catalog)
    if reply:
        return reply
    source = table.get("name", "")
    source_title = agent.display_table_name(source)

    say(f"Составляю план: таблица «{source_title}»", 8)
    planned, reserve = plan_questions(request, table, client)
    if len(planned) + len(reserve) < MIN_WIDGETS:
        return {
            "answer": f"В таблице «{source_title}» не хватает показателей и разрезов для дашборда.",
            "status": "failed",
        }

    started = time.monotonic()
    queue = list(planned)
    widgets: list[DashboardWidget] = []
    failed: list[dict[str, str]] = []
    seen_sql: set[str] = set()
    done = 0
    while queue or (len(widgets) < MIN_WIDGETS and reserve):
        if stopped():
            return {"answer": "Сборка дашборда остановлена, дашборд не сохранён.", "status": "cancelled"}
        if time.monotonic() - started > budget_s:
            failed.extend({"question": q, "reason": "не хватило времени"} for q in queue)
            break
        question = queue.pop(0) if queue else reserve.pop(0)
        done += 1
        total = max(done + len(queue), len(planned))
        say(f"Виджет {done} из {total}: {question}", min(92, 10 + int(80 * (done - 1) / total)))
        session_id = f"dash-{uuid.uuid4().hex[:12]}"
        try:
            res = ask(question, session_id=session_id, datasource_id=datasource_id, table_name=source)
        except Exception as exc:  # noqa: BLE001 — причину не показываем: в ней могут быть детали подключения
            logger.warning("Виджет дашборда «%s» не построен: %s", question, exc)
            failed.append({"question": question, "reason": "ошибка при расчёте"})
            continue
        finally:
            agent.clear_session(session_id)
        problem = widget_problem(res, question, table)
        sql_key = re.sub(r"\s+", " ", res.get("sql") or "").strip().lower()
        if not problem and sql_key in seen_sql:
            continue  # тот же расчёт уже на дашборде: это не ошибка, сообщать не о чем
        if problem:
            failed.append({"question": question, "reason": problem})
            continue
        seen_sql.add(sql_key)
        widgets.append(make_widget(question, res, source))

    if stopped():
        return {"answer": "Сборка дашборда остановлена, дашборд не сохранён.", "status": "cancelled"}

    failed_text = "\n".join(f"- {f['question']}: {f['reason']}" for f in failed)
    if len(widgets) < MIN_WIDGETS:
        return {
            "answer": (
                f"Дашборд не создан: получилось виджетов {len(widgets)}, нужно минимум {MIN_WIDGETS}."
                + (f"\nНе удалось построить:\n{failed_text}" if failed_text else "")
                + "\nПопробуйте назвать показатели и разрезы точнее."
            ),
            "status": "failed",
        }

    say("Сохраняю дашборд", 95)
    saved = dashboards.create_dashboard(DashboardCreateRequest(
        title=dashboard_title(request, table),
        description=f"Создан по запросу: {request}",
        datasource_id=datasource_id or "raw",
        widgets=widgets,
    ))
    answer = f"Собрал дашборд «{saved.title}» по таблице «{source_title}». Виджетов: {len(widgets)}."
    if failed_text:
        answer += f"\nНе вошли на дашборд:\n{failed_text}"
    return {
        "answer": answer,
        "status": "completed",
        "table_name": source,
        "dashboard": {
            "id": saved.id,
            "title": saved.title,
            "widget_count": len(widgets),
            "failed": failed,
        },
    }
