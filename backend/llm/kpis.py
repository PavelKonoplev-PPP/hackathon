"""Утверждённые бизнес-KPI в агенте (BI-32 / #57).

Каталог KPI: формулы предлагает db/business_kpis.py, бэкенд хранит подтверждения
(POST /api/kpis/{id}/approve → data/kpi-definitions.json, см. backend/app/services/kpis.py).
По правилу из db/BUSINESS_KPIS.md агент подставляет ТОЛЬКО утверждённые (approved)
и доступные KPI; «proposed» и «unavailable» не используются.

Если вопрос про такой KPI («Какой CTR по площадкам?»):
- модель получает утверждённую формулу как показатель;
- смысловая проверка требует именно эту формулу (а не «похожую»);
- в ответе указано, что расчёт по утверждённому определению.

Файл перечитывается при изменении — перезапуск бэкенда не нужен.
Путь можно задать переменной KPI_DEFINITIONS_PATH.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "kpi-definitions.json"

# Как KPI называют в вопросах. Порядок важен: сначала более узкие (CTR раньше кликов).
TRIGGERS: list[tuple[str, re.Pattern]] = [
    ("ads_ctr", re.compile(r"\bctr\b|\bцтр\b|кликабельн", re.IGNORECASE)),
    ("support_avg_resolution_h", re.compile(
        r"(врем\w*|скорост\w*|час\w*|долго)[^?]*(закрыт|закрыва|решени|решаю|решен)", re.IGNORECASE)),
    ("sales_revenue", re.compile(r"выручк|оборот", re.IGNORECASE)),
    ("ads_spend", re.compile(r"(расход\w*|затрат\w*|потрат\w*|бюджет\w*)[^?]*реклам|"
                             r"реклам\w*[^?]*(расход|затрат|потрат|бюджет)", re.IGNORECASE)),
    ("ads_clicks", re.compile(r"\bклик(ов|и|а)?\b", re.IGNORECASE)),
    ("sales_operations", re.compile(r"операци\w*\s+(типа\s+)?[«\"']продажа", re.IGNORECASE)),
]

_CACHE: dict[str, tuple[float, list]] = {}


@dataclass(frozen=True)
class Kpi:
    id: str
    schema: str
    table: str
    title: str
    expression: str
    unit: str | None
    description: str
    approved_by: str | None

    def mentioned_in(self, question: str) -> bool:
        pattern = dict(TRIGGERS).get(self.id)
        if pattern is not None:
            return bool(pattern.search(question))
        return self.title.lower() in question.lower()  # KPI, о которых агент заранее не знает


def kpi_path() -> Path:
    return Path(os.getenv("KPI_DEFINITIONS_PATH") or DEFAULT_PATH)


def load(path: Path | None = None) -> list[Kpi]:
    """Утверждённые KPI с формулой. Нет файла или он испорчен — пустой список."""
    path = Path(path or kpi_path())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    key = str(path)
    if key in _CACHE and _CACHE[key][0] == mtime:
        return _CACHE[key][1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("kpis") or []
        default_schema = data.get("schema")
    except (OSError, ValueError, AttributeError):
        return []
    result = []
    for k in items:
        if not isinstance(k, dict) or k.get("status") != "approved":
            continue
        if not all(isinstance(k.get(f), str) and k.get(f) for f in ("id", "table", "expression")):
            continue
        result.append(Kpi(id=k["id"], schema=k.get("schema") or default_schema or "",
                          table=k["table"], title=str(k.get("title") or k["id"]),
                          expression=k["expression"].strip(), unit=k.get("unit"),
                          description=str(k.get("description") or ""),
                          approved_by=k.get("approved_by")))
    _CACHE[key] = (mtime, result)
    return result


def for_question(question: str, table: dict, schema: str | None,
                 approved: list[Kpi] | None = None) -> Kpi | None:
    """Утверждённый KPI этой таблицы В ЭТОМ источнике (schema), о котором спрашивают, или None.
    Таблица с тем же именем в другом источнике чужие KPI не получает."""
    approved = load() if approved is None else approved
    own = [k for k in approved if k.table == table.get("name") and k.schema == (schema or "")]
    for kpi_id, _ in TRIGGERS:  # узкие раньше широких
        for k in own:
            if k.id == kpi_id and k.mentioned_in(question):
                return k
    for k in own:  # KPI без известных триггеров — по названию
        if k.id not in dict(TRIGGERS) and k.mentioned_in(question):
            return k
    return None


def table_bonus(question: str, table: dict, schema: str | None,
                approved: list[Kpi] | None = None) -> int:
    """Очки при выборе таблицы: вопрос про утверждённый KPI этой таблицы."""
    return 2 if for_question(question, table, schema, approved) else 0


def hint(kpi: Kpi) -> str:
    return (f"\nВ вопросе — утверждённый бизнес-KPI «{kpi.title}». Считай его строго по "
            f"утверждённой формуле: {kpi.expression}\n"
            "Если в вопросе есть разбивка (по площадкам, по месяцам) — добавь колонку разбивки "
            "в SELECT и GROUP BY, формулу не меняй.\n")


def _norm(sql: str) -> str:
    return re.sub(r"\s+", "", sql).lower()


_AGGREGATE = re.compile(r"\b(sum|avg|count|min|max)\s*\(", re.IGNORECASE)
_ALIAS = re.compile(r'\s+as\s+("[^"]*"|[a-zа-яё_][\w]*)\s*$', re.IGNORECASE)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _split_top(text: str) -> list[str]:
    """Делит по запятым верхнего уровня (не внутри скобок и строк)."""
    parts, depth, quote, start = [], 0, None, 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def select_items(sql: str) -> list[str]:
    """Выражения внешнего SELECT без псевдонимов (комментарии убраны)."""
    sql = _strip_comments(sql)
    m = re.search(r"\bselect\b", sql, re.IGNORECASE)
    if not m:
        return []
    depth, quote, i = 0, None, m.end()
    while i < len(sql):  # конец списка — FROM на верхнем уровне
        ch = sql[i]
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and re.match(r"from\b", sql[i:i + 5], re.IGNORECASE) and \
                not re.match(r"[\w]", sql[i - 1]):
            break
        i += 1
    body = re.sub(r"^\s*distinct\b", "", sql[m.end():i], flags=re.IGNORECASE)
    return [_ALIAS.sub("", item).strip() for item in _split_top(body)]


def check(sql: str, kpi: Kpi) -> list[str]:
    """Проблема, если показатель считается не по утверждённой формуле.

    Проверяется сама колонка-показатель, а не подстрока где-то в SQL: среди выражений
    SELECT должна быть ровно утверждённая формула; выражение, которое содержит формулу
    (`1 + формула`), или считает KPI по тем же колонкам иначе — ошибка. Формула в
    комментарии не засчитывается."""
    expected = _norm(kpi.expression)
    operands = set(re.findall(r'"([^"]+)"', kpi.expression))
    items = select_items(sql)
    exact = [it for it in items if _norm(it) == expected]
    wrong = [it for it in items if _norm(it) != expected and _AGGREGATE.search(it) and
             (expected in _norm(it) or
              (operands and operands <= set(re.findall(r'"([^"]+)"', it))))]
    if exact and not wrong:
        return []
    detail = f" (сейчас: {wrong[0]})" if wrong else ""
    return [f"«{kpi.title}» — утверждённый KPI: колонка-показатель должна быть ровно "
            f"{kpi.expression}{detail}"]


def note(kpi: Kpi) -> str:
    who = f", утвердил: {kpi.approved_by}" if kpi.approved_by else ""
    text = f"Расчёт по утверждённому определению KPI «{kpi.title}»{who}."
    if kpi.description:
        text += f" {kpi.description}"
    return text
