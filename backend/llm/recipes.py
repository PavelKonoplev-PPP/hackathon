"""Готовые аналитические расчёты (#112, #113, #114): SQL строит код, а не модель.

Многошаговые вопросы маленькая модель решает ненадёжно (см. TestCases.md): подменяет
перцентиль средним, проверяет текущий статус вместо состояния на дату, считает все
успешные запуски вместо цепочек. Здесь такие расчёты собираются по шаблону из колонок
таблицы — без названий конкретных таблиц или значений, только по смыслу колонок
(тип, слова в названии, значения категорий):

- percentile — «90-й перцентиль времени решения по группам, закрытые в марте 2025,
  группы от 100 закрытий», «95-й перцентиль задержки», «медиана стоимости»;
- as_of — «согласовано, но не оплачено на конец 31 марта 2025, исключая отменённые»;
- chains — «доля цепочек, успешных только после повторных попыток, и среднее число попыток»;
- anomaly (#121) — «найди аномальные значения суммы заказов по каналам и поясни критерий».

build() возвращает Recipe(sql, answer_fn, description) или None — тогда вопрос
решает модель обычным путём. Если параметры определить нельзя, рецепт не строится:
лучше честная попытка модели с проверками, чем угаданный расчёт.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from . import compare as cmp
from .semantics import same_word
from .sql_runner import column_kind

# ---------- общие помощники ----------


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[а-яеa-z0-9]+", _norm(text))


def _head(name: str) -> str:
    """Главное слово названия колонки: «Закрыто (МСК)» → «закрыто»."""
    found = _tokens(name)
    return found[0] if found else ""


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _columns(table: dict, *kinds: str) -> list[str]:
    return [c["name"] for c in table.get("columns") or [] if column_kind(c) in kinds]


def _categories(table: dict) -> list[str]:
    return [c["name"] for c in table.get("columns") or []
            if c.get("inferred_type") == "category" and column_kind(c) is None]


def _token_matches(token: str, name: str) -> bool:
    head = _head(name)
    return len(token) >= 4 and len(head) >= 4 and same_word(token, head)


_GROUP_CUE = re.compile(r"^(кажд\w*|по|как\w+|разрезе|среди|разбивк\w*)$")


def group_column(question: str, table: dict, exclude: set[str] = frozenset()) -> str | None:
    """Колонка разбивки: «у какой группы» → «Группа поддержки», «по подразделениям»,
    «для каждого задания». Только категории и только после слова-признака разбивки."""
    toks = _tokens(question)
    candidates = [c for c in _categories(table) if c not in exclude]
    for i, tok in enumerate(toks):
        if i == 0 or not _GROUP_CUE.match(toks[i - 1]):
            continue
        for col in candidates:
            if _token_matches(tok, col):
                return col
    return None


def _event_column(question: str, columns: list[str]) -> str | None:
    """Временная колонка, названная в вопросе словом-событием: «закрытых» → «Закрыто»."""
    for tok in _tokens(question):
        for col in columns:
            if _token_matches(tok, col):
                return col
    return None


def _first(columns: list[str], pattern: str) -> str | None:
    rx = re.compile(pattern, re.IGNORECASE)
    return next((c for c in columns if rx.search(_norm(c))), None)


def _period(question: str):
    periods = cmp.parse_periods(question)
    return periods[0] if len(periods) == 1 else None


_MIN_COUNT = re.compile(r"(?:менее|меньше)\s+(?:чем\s+)?(\d+)|"
                        r"(?:не\s+менее|не\s+меньше|минимум|хотя\s+бы|от)\s+(\d+)\s+"
                        r"(?:закрыт|наблюден|обращен|отправлен|заказ|запис|строк|случа|раз)",
                        re.IGNORECASE)


def _min_count(question: str) -> int | None:
    m = _MIN_COUNT.search(question)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return f"{int(value):,}".replace(",", " ")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number):,}".replace(",", " ")
    return f"{number:,.2f}".replace(",", " ").replace(".", ",")


@dataclass
class Recipe:
    kind: str
    sql: str
    description: str             # как посчитано — для ответа и для проверки человеком
    answer: callable = field(repr=False, default=None)  # (columns, rows) -> текст


# ---------- перцентили (#112) ----------

PERCENTILE = re.compile(r"(\d{1,2})\s*-?\s*(?:й|ый|ой|ом|го)?\s*(?:перцентил|процентил)|"
                        r"\bp\s?(\d{2})\b|(медиан)", re.IGNORECASE)
_DELAY = re.compile(r"задержк|опоздан|просроч", re.IGNORECASE)
_DURATION = re.compile(r"врем\w*|длительн|продолжительн|скорост|сколько\s+час", re.IGNORECASE)
_START = r"^(создан|начал|принят|подан|открыт|поступ|получен|выставлен|оформлен|зарегистр|start|creat|open)"
_PROMISED = r"(обещан|срок|план|дедлайн|норматив|требует)"


def _units(question: str) -> tuple[float, str]:
    q = _norm(question)
    if re.search(r"минут", q):
        return 60.0, "мин"
    if re.search(r"\bдн(ях|ей|и)\b|сутк", q):
        return 86400.0, "дн."
    return 3600.0, "ч"


def percentile_recipe(question: str, table: dict) -> Recipe | None:
    m = PERCENTILE.search(question)
    if not m:
        return None
    p = 50 if m.group(3) else int(m.group(1) or m.group(2))
    if not 1 <= p <= 99:
        return None
    times = _columns(table, "timestamp", "date")
    seconds, unit = _units(question)
    value = what = None
    event = None
    if _DELAY.search(question):
        promised = _first(times, _PROMISED)
        event = _event_column(question, [c for c in times if c != promised])
        if promised and event:
            value = (f"GREATEST(0, EXTRACT(EPOCH FROM ({_q(event)} - {_q(promised)})) / {seconds:g})")
            what = (f"задержка = max(0, «{event}» − «{promised}»), {unit}; "
                    "доставленное вовремя — нулевая задержка")
    elif _DURATION.search(question):
        start = _first(times, _START)
        event = _event_column(question, [c for c in times if c != start])
        if start and event:
            value = f"EXTRACT(EPOCH FROM ({_q(event)} - {_q(start)})) / {seconds:g}"
            what = f"время = «{event}» − «{start}», {unit}"
    if value is None:  # перцентиль числовой колонки: «медиана стоимости доставки»
        numeric = _columns(table, "integer", "decimal")
        col = _event_column(question, numeric)
        if not col:
            return None
        value, what, unit = _q(col), f"«{col}»", ""
        event = None
    group = group_column(question, table)
    period = _period(question)
    where = []
    if event:
        where.append(f"{_q(event)} IS NOT NULL")
    if period:
        date_col = event or cmp.choose_date_column(
            table, question, {c["name"]: column_kind(c) for c in table.get("columns") or []})
        if not date_col:
            return None
        where.append(period.condition(date_col))
    min_n = _min_count(question)
    label = f"{p}-й перцентиль" if p != 50 else "Медиана"
    metric_name = f"{label}, {unit}" if unit else label
    select = [f"COUNT(*) AS {_q('Наблюдений')}",
              f"ROUND((percentile_cont({p / 100:g}) WITHIN GROUP (ORDER BY {value}))::numeric, 2) "
              f"AS {_q(metric_name)}"]
    sql = "SELECT " + (f"{_q(group)}, " if group else "") + ", ".join(select) + " FROM t"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group:
        sql += " GROUP BY 1"
        if min_n:
            sql += f" HAVING COUNT(*) >= {min_n}"
        low = re.search(r"минимальн|наименьш|самый\s+низк|меньше\s+всего", question, re.IGNORECASE)
        sql += f" ORDER BY 3 {'ASC' if low else 'DESC'}"
    desc = f"{label} ({what})"
    if period:
        desc += f"; событие «{event}» в периоде {period.label}" if event else f"; период {period.label}"
    if group and min_n:
        desc += f"; группы «{group}» от {min_n} наблюдений"
    elif group:
        desc += f"; по «{group}»"

    def answer(columns, rows):
        if not rows:
            return "Под условия не попало ни одного наблюдения."
        if group:
            top = rows[0]
            text = (f"{'Самое низкое' if 'ASC' in sql else 'Самое высокое'} значение "
                    f"({label.lower()}) — у «{top[0]}»: {_fmt(top[2])} {unit} "
                    f"(наблюдений: {_fmt(top[1])}).").replace("  ", " ").replace(" )", ")")
            if len(rows) > 1:
                text += " Остальные — в таблице."
        else:
            text = (f"{label}: {_fmt(rows[0][1])} {unit} (наблюдений: {_fmt(rows[0][0])})."
                    .replace("  ", " ").replace(" (", " (", 1))
        return text + f" Как посчитано: {desc}."
    return Recipe("percentile", sql, desc, answer)


# ---------- состояние на дату (#113) ----------

_GEN_MONTHS = ["январ", "феврал", "март", "апрел", "ма[йя]", "июн", "июл", "август", "сентябр",
               "октябр", "ноябр", "декабр"]
_ASOF_WORDS = re.compile(r"\bна\s+(?:конец|начало|момент|дату|состояние)|по\s+состоянию\s+на|"
                         r"\bна\s+\d{1,2}[.\s]", re.IGNORECASE)
_DATE_WORDS = re.compile(r"(\d{1,2})\s+(" + "|".join(_GEN_MONTHS) + r")[а-я]*\s+(20\d{2})", re.IGNORECASE)
_DATE_DOTS = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b")
_NEGATE = re.compile(r"^(не|исключ\w*|кроме|без|ни)$")


def _asof_date(question: str):
    if not _ASOF_WORDS.search(question):
        return None
    m = _DATE_WORDS.search(question)
    if m:
        month = next(i for i, pat in enumerate(_GEN_MONTHS, 1)
                     if re.match(pat, m.group(2), re.IGNORECASE))
        return dt.date(int(m.group(3)), month, int(m.group(1)))
    m = _DATE_DOTS.search(question)
    if m:
        return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def _polarity(question: str, column: str) -> bool | None:
    """True — событие уже было к дате, False — ещё не было («не оплачено», «исключая
    отменённые»), None — колонка в вопросе не названа."""
    for clause in re.split(r"[,;:]", _norm(question)):
        toks = _tokens(clause)
        negated_clause = any(re.match(r"^(исключ\w*|кроме|без)$", t) for t in toks[:2])
        for i, tok in enumerate(toks):
            if _token_matches(tok, column):
                near = toks[max(0, i - 2):i]
                return not (negated_clause or any(_NEGATE.match(t) for t in near))
    return None


def asof_recipe(question: str, table: dict) -> Recipe | None:
    day = _asof_date(question)
    if not day:
        return None
    start_of = re.search(r"на\s+начало", question, re.IGNORECASE)
    bound = day if start_of else day + dt.timedelta(days=1)
    times = _columns(table, "timestamp", "date")
    states = {c: _polarity(question, c) for c in times}
    states = {c: s for c, s in states.items() if s is not None}
    if not any(states.values()):
        # «не оплачено на дату» — запись должна существовать к дате: событие создания
        # (по смыслу названия, иначе первая временная колонка таблицы)
        created = _first(times, _START) or (times[0] if times else None)
        if not created or not states:
            return None
        states[created] = True
    b = _lit(bound.isoformat())
    where = []
    for col, happened in states.items():
        where.append(f"{_q(col)} < {b}" if happened else f"({_q(col)} IS NULL OR {_q(col)} >= {b})")
    positive = [c for c, s in states.items() if s]
    money = [c for c in _columns(table, "decimal", "integer")
             if any(_token_matches(_head(p), c) or same_word(_head(p), _head(c)) for p in positive)]
    if not money and re.search(r"сумм", question, re.IGNORECASE):
        money = [c for c in _columns(table, "decimal") if re.search(r"руб|сумм", c, re.IGNORECASE)][:1]
    group = group_column(question, table)
    select = [f"COUNT(*) AS {_q('Количество')}"]
    if money:
        select.append(f"ROUND(SUM({_q(money[0])})::numeric, 2) AS {_q(money[0])}")
    sql = ("SELECT " + (f"{_q(group)}, " if group else "") + ", ".join(select) + " FROM t WHERE "
           + " AND ".join(where))
    if group:
        sql += f" GROUP BY 1 ORDER BY {3 if money else 2} DESC"
    shown = (day.strftime("%d.%m.%Y") + (" (на начало дня)" if start_of else " (на конец дня)"))
    parts = [f"«{c}» {'до' if s else 'не раньше'} {bound.strftime('%d.%m.%Y')}"
             + ("" if s else " или пусто") for c, s in states.items()]
    desc = f"состояние на {shown}: " + "; ".join(parts) + (" — текущий «Статус» не используется"
                                                          if any(c["name"] == "Статус" for c in
                                                                 table.get("columns") or []) else "")

    cur = " ₽" if money and re.search(r"руб|₽", money[0], re.IGNORECASE) else ""

    def answer(columns, rows):
        if not rows or not rows[0][-1]:
            return f"На {shown} под условия не попало ни одной записи. Как посчитано: {desc}."
        if group:
            top = rows[0]
            text = f"На {shown} больше всего — «{top[0]}»: {_fmt(top[-1])}{cur}"
            text += f" (записей: {_fmt(top[1])})." if money else "."
            text += " По остальным — в таблице."
        else:
            text = (f"На {shown}: {_fmt(rows[0][-1])}{cur}"
                    + (f" (записей: {_fmt(rows[0][0])})." if money else "."))
        return text + f" Как посчитано: {desc}."
    return Recipe("as_of", sql, desc, answer)


# ---------- цепочки попыток (#114) ----------

CHAINS = re.compile(r"цепоч|повторн\w*\s+попыт|после\s+повтор|ретра|\bretr", re.IGNORECASE)
_SUCCESS = re.compile(r"^(успеш|success|ok\b|выполнен|done|завершен)", re.IGNORECASE)


def chains_recipe(question: str, table: dict, values: dict[str, list[str]]) -> Recipe | None:
    if not CHAINS.search(question):
        return None
    names = [c["name"] for c in table.get("columns") or []]
    chain = _first(names, r"цепоч|chain")
    attempt = next((c for c in _columns(table, "integer") if re.search(r"попыт|attempt|retry", c, re.IGNORECASE)), None)
    status = _first([c for c in names if c in (values or {})], r"статус|status|состояни")
    ok_value = next((v for v in (values or {}).get(status or "", []) if _SUCCESS.match(_norm(v))), None)
    if not (chain and attempt and status and ok_value):
        return None
    group = group_column(question, table, exclude={status})
    g_sel = f"{_q(group)} AS g, " if group else ""
    g_by = "g, " if group else ""
    retry_ok = f"last_st = {_lit(ok_value)} AND n_att > 1"
    sql = (f"WITH c AS (SELECT {g_sel}{_q(chain)} AS ch, MAX({_q(attempt)}) AS n_att, "
           f"(array_agg({_q(status)} ORDER BY {_q(attempt)} DESC))[1] AS last_st "
           f"FROM t GROUP BY {g_by}ch) "
           "SELECT " + (f"g AS {_q(group)}, " if group else "")
           + f"COUNT(*) AS {_q('Цепочек')}, "
           f"COUNT(*) FILTER (WHERE {retry_ok}) AS {_q('Успешно после повторов')}, "
           f"ROUND(100.0 * COUNT(*) FILTER (WHERE {retry_ok}) / NULLIF(COUNT(*), 0), 2) AS {_q('Доля, %')}, "
           f"ROUND(AVG(n_att) FILTER (WHERE {retry_ok}), 2) AS {_q('Среднее число попыток')} "
           "FROM c" + (" GROUP BY 1 ORDER BY 4 DESC" if group else ""))
    desc = (f"цепочка — записи с одним «{chain}»; успешна только после повторов, если последняя "
            f"по «{attempt}» попытка имеет «{status}» = «{ok_value}» и попыток больше одной; "
            "доля — от всех цепочек")

    def answer(columns, rows):
        if not rows:
            return "Цепочек не нашлось."
        if group:
            shares = [r[3] for r in rows if r[3] is not None]
            avgs = [r[4] for r in rows if r[4] is not None]
            top = rows[0]
            text = (f"Доля цепочек, успешных только после повторов, — от {_fmt(min(shares))}% до "
                    f"{_fmt(max(shares))}% по {len(rows)} значениям «{group}»; среднее число попыток "
                    f"в них — {_fmt(min(avgs))}–{_fmt(max(avgs))}. Больше всего — «{top[0]}»: "
                    f"{_fmt(top[3])}% ({_fmt(top[2])} из {_fmt(top[1])}), {_fmt(top[4])} попытки.")
        else:
            r = rows[0]
            text = (f"Успешно только после повторов — {_fmt(r[1])} из {_fmt(r[0])} цепочек "
                    f"({_fmt(r[2])}%), в среднем {_fmt(r[3])} попытки.")
        return text + f" Как посчитано: {desc}."
    return Recipe("chains", sql, desc, answer)


# ---------- аномалии (#121) ----------

ANOMALY = re.compile(r"аномал|выброс|необычн\w*\s+(значен|сумм|заказ)|подозрительн", re.IGNORECASE)
MIN_SAMPLE = 20  # fewer observations in a group: suppress outlier counts, don't invent certainty
_LARGE_OUTLIER = re.compile(
    r"крупн|больш\w*|высок\w*|верхн\w*|завышен|дорог\w*|превыша\w*",
    re.IGNORECASE,
)
_SMALL_OUTLIER = re.compile(
    r"маленьк\w*|мелк\w*|низк\w*|нижн\w*|небольш\w*|дешев\w*|"
    r"меньш\w*|минимальн\w*",
    re.IGNORECASE,
)


def anomaly_recipe(question: str, table: dict) -> Recipe | None:
    """Tukey IQR outliers computed per segment, with exact user period and tail.

    Aggregate by a named entity first when requested (e.g. one row per order),
    otherwise evaluate individual rows. Never silently drop an explicit period.
    """
    if not ANOMALY.search(question):
        return None
    from .semantics import MONEY, money_formula

    names = [c["name"] for c in table.get("columns") or []]
    numeric = _columns(table, "integer", "decimal")
    formula = money_formula(names)
    value = None
    if formula and MONEY.search(question):
        value, what = formula[4:-1], "выручка"  # SUM(expr) -> expr
    else:
        col = _event_column(question, numeric) or next(
            (c for c in numeric for t in _tokens(question) if len(t) >= 4 and
             any(same_word(t, w) for w in _tokens(c))), None)
        if col:
            value, what = _q(col), f"«{col}»"
    if value is None and re.search(r"сумм", question, re.IGNORECASE):
        if formula:
            value, what = formula[4:-1], "сумма (количество × цена с учётом скидки)"
        else:
            money = [c for c in numeric if re.search(r"руб|сумм|₽", c, re.IGNORECASE)]
            if money:
                value, what = _q(money[0]), f"«{money[0]}»"
    if value is None:
        return None

    periods = cmp.parse_periods(question)
    # Multiple explicit periods require a comparison, not one unspecified pool.
    if len(periods) > 1:
        return None
    period = periods[0] if periods else None
    period_col = None
    if period:
        kinds = {c["name"]: column_kind(c) for c in table.get("columns") or []}
        period_col = cmp.choose_date_column(table, question, kinds)
        if not period_col:
            return None
    period_cond = period.condition(period_col) if period else None

    upper = bool(_LARGE_OUTLIER.search(question))
    lower = bool(_SMALL_OUTLIER.search(question))
    if upper and not lower:
        direction = "upper"
    elif lower and not upper:
        direction = "lower"
    else:
        direction = "both"  # unspecified or explicitly asks for both tails

    ids = [c["name"] for c in table.get("columns") or [] if c.get("inferred_type") == "identifier"]
    entity = next((c for c in ids for t in _tokens(question)
                   if len(t) >= 4 and any(same_word(t, w) for w in _tokens(c) if len(w) >= 4)), None)
    group = group_column(question, table)
    g = f"{_q(group)} AS g, " if group else ""
    if entity:
        base = f"SELECT {g}{_q(entity)} AS e, SUM({value}) AS v FROM t"
        if period_cond:
            base += f" WHERE {period_cond}"
        base += f" GROUP BY {'g, ' if group else ''}e"
        unit_desc = f"{what} по каждому «{entity}»"
    else:
        base = f"SELECT {g}{value} AS v FROM t WHERE {value} IS NOT NULL"
        if period_cond:
            base += f" AND ({period_cond})"
        unit_desc = what

    by = "g" if group else None
    stats = ("SELECT " + ("g, " if by else "")
             + "percentile_cont(0.25) WITHIN GROUP (ORDER BY v) AS q1, "
             "percentile_cont(0.75) WITHIN GROUP (ORDER BY v) AS q3 FROM d WHERE v IS NOT NULL"
             + (" GROUP BY g" if by else ""))
    join = " JOIN s USING (g)" if by else " CROSS JOIN s"
    lower_cond = "d.v < s.q1 - 1.5 * (s.q3 - s.q1)"
    upper_cond = "d.v > s.q3 + 1.5 * (s.q3 - s.q1)"
    out_cond = (upper_cond if direction == "upper" else lower_cond if direction == "lower"
                else f"({lower_cond} OR {upper_cond})")
    bound_expr = ("s.q1 - 1.5 * (s.q3 - s.q1)" if direction == "lower"
                  else "s.q3 + 1.5 * (s.q3 - s.q1)")
    bound_label = "Нижняя граница" if direction == "lower" else "Верхняя граница"
    extremum = "MIN" if direction == "lower" else "MAX"
    extremum_label = "Минимум" if direction == "lower" else "Максимум"
    # A tiny group can produce arbitrary IQR estimates. Show n, but no
    # authoritative outlier count for it (NULL instead of a misleading value).
    count_sql = (f"CASE WHEN COUNT(*) >= {MIN_SAMPLE} THEN "
                 f"COUNT(*) FILTER (WHERE {out_cond}) ELSE NULL END")
    sql = (f"WITH d AS ({base}), s AS ({stats}) SELECT "
           + (f"d.g AS {_q(group)}, " if by else "")
           + f"COUNT(*) AS {_q('Наблюдений')}, "
           f"ROUND(MIN(s.q1)::numeric, 2) AS {_q('Q1')}, ROUND(MIN(s.q3)::numeric, 2) AS {_q('Q3')}, "
           f"ROUND(MIN({bound_expr})::numeric, 2) AS {_q(bound_label)}, "
           f"{count_sql} AS {_q('Выбросов')}, "
           f"ROUND({extremum}(d.v)::numeric, 2) AS {_q(extremum_label)} "
           f"FROM d{join} WHERE d.v IS NOT NULL"
           + (" GROUP BY d.g ORDER BY 6 DESC NULLS LAST, 1" if by else ""))
    criterion = {
        "upper": "выше Q3 + 1,5·IQR",
        "lower": "ниже Q1 − 1,5·IQR",
        "both": "вне [Q1 − 1,5·IQR; Q3 + 1,5·IQR]",
    }[direction]
    desc = (f"значение — {unit_desc}; выброс — {criterion} "
            + (f"внутри каждого «{group}»" if group else "по всем данным")
            + (f"; фильтр по «{period_col}»: {period.label}" if period else "")
            + "; пустые значения не учитываются. Выброс — статистически необычное значение, "
            f"а не ошибка данных; при меньше {MIN_SAMPLE} наблюдений число выбросов не рассчитывается")

    def answer(columns, rows):
        if not rows:
            return "Данных для поиска выбросов не нашлось."
        idx = 1 if group else 0
        small = [r for r in rows if (r[idx] or 0) < MIN_SAMPLE]
        eligible = [r for r in rows if (r[idx] or 0) >= MIN_SAMPLE]
        if not eligible:
            return (f"Недостаточно наблюдений для надёжного поиска выбросов "
                    f"(требуется минимум {MIN_SAMPLE} в группе). Критерий: {desc}.")
        total = sum(r[idx + 4] or 0 for r in eligible)
        denominator = sum(r[idx] or 0 for r in eligible)
        if group:
            top = eligible[0]
            text = (f"Выбросов всего {_fmt(total)} из {_fmt(denominator)} проверенных наблюдений. "
                    f"Больше всего — «{top[0]}»: {_fmt(top[5])} "
                    f"({bound_label.lower()} {_fmt(top[4])}, "
                    f"{extremum_label.lower()} {_fmt(top[6])}).")
        else:
            row = eligible[0]
            text = (f"Выбросов {_fmt(row[4])} из {_fmt(row[0])} наблюдений; "
                    f"{bound_label.lower()} {_fmt(row[3])}, "
                    f"{extremum_label.lower()} {_fmt(row[5])}.")
        if small:
            text += (f" Исключены группы с менее {MIN_SAMPLE} наблюдений: "
                     + ", ".join(f"«{r[0]}»" if group else "вся выборка" for r in small) + ".")
        return text + f" Критерий: {desc}."
    return Recipe("anomaly", sql, desc, answer)


# ---------- выбор ----------

def build(question: str, table: dict, values: dict[str, list[str]] | None = None) -> Recipe | None:
    """Verified deterministic calculations, including true aligned daily lags."""
    from .lag_analysis import build_lag_plan, is_lag_request

    if is_lag_request(question):
        lag = build_lag_plan(question, table)
        return Recipe("lag", lag.sql, lag.description, lag.answer) if lag else None
    for make in (lambda: chains_recipe(question, table, values or {}),
                 lambda: percentile_recipe(question, table),
                 lambda: asof_recipe(question, table),
                 lambda: anomaly_recipe(question, table)):
        recipe = make()
        if recipe:
            return recipe
    return None


def is_recipe_question(question: str) -> bool:
    """Вопрос, похожий на рецепт (для выбора таблицы и отключения прочих эвристик)."""
    return bool(PERCENTILE.search(question) or CHAINS.search(question) or _asof_date(question)
                or ANOMALY.search(question))
