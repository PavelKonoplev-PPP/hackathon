"""Открытые аналитические вопросы: проверка утверждения и поиск условий.

«Можно ли утверждать, что команды стали работать быстрее?» и «При каких условиях крупные задачи
дольше ждут начала?» нельзя ответить одним запросом: нужно сравнить несколько расчётов и не попасть
в ловушку данных. Поэтому всё считает код, модель здесь не участвует. Модуль не знает названий таблиц:
показатель и разрезы определяются по каталогу. Если вопрос не удалось разобрать, возвращается None,
и его обрабатывает обычный агент.
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
import re
from dataclasses import dataclass

from .sql_runner import quote_ident

CLAIM = re.compile(r"можно ли (утверждать|сказать|считать|говорить)|правда ли|верно ли|действительно ли"
                   r"|подтвержда\w+ ли|стал[аио]? ли", re.IGNORECASE)
DRIVERS = re.compile(r"при каких условиях|в каких случаях|от чего зависит|что влияет|какие факторы"
                     r"|что определяет|из-за чего|почему", re.IGNORECASE)
FASTER = re.compile(r"быстрее|ускор|сократил|меньше времени", re.IGNORECASE)
SLOWER = re.compile(r"медленнее|дольше|замедл|больше времени", re.IGNORECASE)
# \\b обязателен: в слове «утверждать» есть «ждать»
TIMING = re.compile(r"быстр|медлен|дольше|ускор|замедл|сократ|\bждут|\bждать|\bожида|\bвремен?[яи]|длитель", re.IGNORECASE)
WAITING = re.compile(r"\bждут|\bждать|\bожида|\bначал|\bприступ", re.IGNORECASE)
LARGE = re.compile(r"крупн|больши[ехм]|тяж[её]л", re.IGNORECASE)

PLANNED = re.compile(r"срок|обещан|требуется|обновлен|возвращен|отмен|отклон", re.IGNORECASE)
START_STEP = re.compile(r"начал|перв\w+ ответ|передано|согласован", re.IGNORECASE)
END_STEP = re.compile(r"заверш|закрыт|доставлен|окончан|исполн|оплач", re.IGNORECASE)
SIZE = re.compile(r"план|вес|объ[её]м|сумм|стоимост|количеств", re.IGNORECASE)
NOT_MEASURE = re.compile(r"код|номер|\bid\b", re.IGNORECASE)
AFTER_THE_FACT = re.compile(r"статус|результат|причин|ошибк|оценк|повторн", re.IGNORECASE)

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма[йяе]": 5, "июн": 6, "июл": 7,
          "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
               "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_GENITIVE = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                   "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

MIN_GROUP = 30  # меньше — разница ничего не значит
MAX_GROUPS = 40
MAX_DIMENSIONS = 10
SIGNIFICANT_Z = 3.0  # разрезов много: на уровне двух стандартных ошибок «находки» будут случайными
NOTABLE_GAP = 0.05  # отличие от общего уровня меньше 5% не считаем условием
TREND_GAP = 0.05
SHARE_GAP_PP = 2.0
CENSORED_GAP_PP = 5.0
BASELINE_DAYS = 56


@dataclass
class Duration:
    start: str
    end: str

    @property
    def sql(self) -> str:
        return f"EXTRACT(EPOCH FROM ({quote_ident(self.end)} - {quote_ident(self.start)})) / 3600"

    @property
    def label(self) -> str:
        return f"от «{_short(self.start)}» до «{_short(self.end)}»"


@dataclass
class Group:
    value: str
    n: int
    mean: float
    median: float


def _short(column: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*$", "", column)


def _num(value: float, digits: int = 1) -> str:
    text = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text


def _count(value: float) -> str:
    return f"{int(value):,}".replace(",", " ")


def _timestamps(table: dict) -> list[str]:
    return [c["name"] for c in table.get("columns") or []
            if c.get("inferred_type") in ("timestamp", "date") and not PLANNED.search(c["name"])]


def _measures(table: dict) -> list[str]:
    return [c["name"] for c in table.get("columns") or []
            if c.get("inferred_type") in ("integer", "decimal") and not NOT_MEASURE.search(c["name"])]


def _named_in_question(column: str, question: str) -> bool:
    low = question.lower().replace("ё", "е")
    words = [w for w in re.findall(r"[а-яёa-z]+", _short(column).lower().replace("ё", "е")) if len(w) >= 4]
    return any(w[:5] in low for w in words)


def choose_duration(question: str, table: dict) -> Duration | None:
    """Какой отрезок времени имеется в виду. Начало — первое событие в таблице."""
    columns = _timestamps(table)
    if len(columns) < 2:
        return None
    start, later = columns[0], columns[1:]
    named = [c for c in later if _named_in_question(c, question)]
    if named:
        return Duration(start, named[0])
    pattern = START_STEP if WAITING.search(question) else END_STEP
    matched = [c for c in later if pattern.search(c)]
    return Duration(start, matched[0]) if matched else None


def size_column(table: dict, question: str) -> str | None:
    if not LARGE.search(question):
        return None
    sized = [c for c in _measures(table) if SIZE.search(c)]
    return sized[0] if sized else None


def _dimensions(table: dict, start: str) -> list[tuple[str, str]]:
    """Разрезы: (название, выражение SQL). Что стало известно позже («Статус»), причиной быть не может."""
    columns = table.get("columns") or []
    cats = [c for c in columns if c.get("inferred_type") == "category" and not AFTER_THE_FACT.search(c["name"])
            and 2 <= (c.get("sample_distinct") or 0) <= MAX_GROUPS]
    cats.sort(key=lambda c: c.get("sample_distinct") or 0)
    dims = [(c["name"], f'{quote_ident(c["name"])}::text') for c in cats[:MAX_DIMENSIONS]]
    begin = quote_ident(start)
    dims.append(("Месяц", f"EXTRACT(MONTH FROM {begin})::int::text"))
    dims.append(("День недели", f"EXTRACT(ISODOW FROM {begin})::int::text"))
    return dims


def _pretty_group(dimension: str, value: str) -> str:
    if dimension == "Месяц":
        return MONTH_NAMES[int(value) - 1]
    if dimension == "День недели":
        return WEEKDAYS[int(value) - 1]
    return value


def _rows(result) -> list[list]:
    return [list(r) for r in result.rows]


def _f(value) -> float:
    return float(value)


def analyse_dimension(groups: list[Group], overall_mean: float, overall_sd: float) -> dict | None:
    """Сила разреза и значимые группы. Без z-проверки в 32 городах всегда найдётся «худший»."""
    groups = [g for g in groups if g.n >= MIN_GROUP]
    if len(groups) < 2 or overall_sd <= 0 or overall_mean == 0:
        return None
    total = sum(g.n for g in groups)
    strength = sum(g.n * (g.mean - overall_mean) ** 2 for g in groups) / (total * overall_sd ** 2)
    notable = []
    for g in groups:
        gap = (g.mean - overall_mean) / abs(overall_mean)
        z = (g.mean - overall_mean) / (overall_sd / math.sqrt(g.n))
        if abs(z) >= SIGNIFICANT_Z and abs(gap) >= NOTABLE_GAP:
            notable.append((g, gap))
    return {"strength": strength, "notable": notable}


def _period(question: str, latest: dt.date) -> tuple[dt.date, dt.date, str] | None:
    """Период из вопроса: [начало, конец). «В конце сентября 2026» — последние 10 дней месяца."""
    low = question.lower()
    month = next((m for stem, m in MONTHS.items() if re.search(rf"\b{stem}\w*", low)), None)
    if month is None:
        return None
    found = re.search(r"\b(20\d\d)\b", low)
    year = int(found.group(1)) if found else (latest.year if month <= latest.month else latest.year - 1)
    last = calendar.monthrange(year, month)[1]
    name, genitive = MONTH_NAMES[month - 1], MONTHS_GENITIVE[month - 1]
    if re.search(r"\bкон[цe]\w*", low):
        return dt.date(year, month, last - 9), dt.date(year, month, last) + dt.timedelta(days=1), f"конец {genitive} {year}"
    if re.search(r"\bначал\w* " + genitive[:4], low):
        return dt.date(year, month, 1), dt.date(year, month, 11), f"начало {genitive} {year}"
    return dt.date(year, month, 1), dt.date(year, month, last) + dt.timedelta(days=1), f"{name} {year}"


def claim_verdict(wants_faster: bool, cohort: dict, finished: dict, fair: dict) -> dict:
    """Итог проверки. Главный расчёт — по дате завершения: на него не влияет обрыв данных."""
    censored = cohort["period_done"] < cohort["base_done"] - CENSORED_GAP_PP
    finished_change = _change(finished["base"], finished["period"])
    fair_change = None
    if fair["period"] is not None and fair["base"] is not None:
        fair_change = fair["period"] - fair["base"]
    cohort_change = _change(cohort["base"], cohort["period"])

    sign = -1 if wants_faster else 1
    moved = sign * finished_change >= TREND_GAP * 100
    # доля завершённых за фиксированный срок растёт, когда работают быстрее
    fair_agrees = fair_change is None or (-sign) * fair_change >= SHARE_GAP_PP
    return {
        "confirmed": moved and fair_agrees,
        "censored": censored,
        "cohort_change": cohort_change,
        "finished_change": finished_change,
        "fair_change": fair_change,
        "trap": censored and sign * cohort_change >= TREND_GAP * 100 and not moved,
    }


def _change(base: float | None, value: float | None) -> float:
    if not base or value is None:
        return 0.0
    return (value - base) / base * 100


def _sign(value: float, digits: int = 1) -> str:
    return ("+" if value > 0 else "") + _num(value, digits)


def _sql_note(table: dict) -> str:
    return f"-- t = данные таблицы «{_table_title(table)}» с приведёнными типами\n"


def _table_title(table: dict) -> str:
    from .catalog_summary import display_table_name

    return display_table_name(table["name"])


def check_claim(question: str, table: dict, runner, say=lambda _text: None) -> dict | None:
    """«Можно ли утверждать, что к концу сентября стали работать быстрее?»"""
    wants_faster = bool(FASTER.search(question))
    if not wants_faster and not SLOWER.search(question):
        return None
    duration = choose_duration(question, table)
    if duration is None:
        return None

    start, end, metric = quote_ident(duration.start), quote_ident(duration.end), duration.sql
    say("Определяю, где заканчиваются данные")
    edge = _rows(runner.run(table, f"SELECT MAX({start}), MAX({end}) FROM t"))[0]
    if edge[0] is None or edge[1] is None:
        return None
    data_end = max(_as_datetime(edge[0]), _as_datetime(edge[1]))
    parsed = _period(question, data_end.date())
    if parsed is None:
        last = data_end.date() + dt.timedelta(days=1)
        parsed = (last - dt.timedelta(days=10), last, "последние 10 дней данных")
    period_from, period_to, label = parsed
    base_from = period_from - dt.timedelta(days=BASELINE_DAYS)

    say(f"Считаю по дате начала: {label} и {BASELINE_DAYS // 7} недель до него")
    bucket = (f"CASE WHEN {{col}} >= TIMESTAMP '{period_from}' AND {{col}} < TIMESTAMP '{period_to}' THEN 'period' "
              f"WHEN {{col}} >= TIMESTAMP '{base_from}' AND {{col}} < TIMESTAMP '{period_from}' THEN 'base' END")
    cohort_sql = (
        f"SELECT b, COUNT(*) AS n, COUNT(m) AS done, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m) AS med "
        f"FROM (SELECT {bucket.format(col=start)} AS b, CASE WHEN {metric} >= 0 THEN {metric} END AS m FROM t) x "
        f"WHERE b IS NOT NULL GROUP BY b")
    cohort_rows = {r[0]: r for r in _rows(runner.run(table, cohort_sql))}
    say("Считаю по дате завершения: на него не влияет обрыв данных")
    finished_sql = (
        f"SELECT b, COUNT(*) AS n, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m) AS med "
        f"FROM (SELECT {bucket.format(col=end)} AS b, {metric} AS m FROM t WHERE {metric} >= 0) x "
        f"WHERE b IS NOT NULL GROUP BY b")
    finished_rows = {r[0]: r for r in _rows(runner.run(table, finished_sql))}
    if not all(k in cohort_rows for k in ("period", "base")) or not all(k in finished_rows for k in ("period", "base")):
        return None

    base_median = _f(finished_rows["base"][2])
    horizon = dt.timedelta(hours=base_median)
    observed_until = data_end - horizon
    say(f"Считаю долю завершённых за {_num(base_median, 0)} ч среди начатых с полным сроком наблюдения")
    fair_sql = (
        f"SELECT b, COUNT(*) AS n, 100.0 * COUNT(*) FILTER (WHERE m <= {base_median}) / COUNT(*) AS share "
        f"FROM (SELECT {bucket.format(col=start)} AS b, {metric} AS m FROM t "
        f"WHERE {start} <= TIMESTAMP '{observed_until:%Y-%m-%d %H:%M:%S}') x "
        f"WHERE b IS NOT NULL GROUP BY b")
    fair_rows = {r[0]: r for r in _rows(runner.run(table, fair_sql))}
    enough = lambda key: key in fair_rows and fair_rows[key][1] >= MIN_GROUP  # noqa: E731

    weekly_sql = (
        f"WITH s AS (SELECT DATE_TRUNC('week', {start}) AS w, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {metric}) AS med "
        f"FROM t WHERE {metric} >= 0 AND {start} >= TIMESTAMP '{base_from}' GROUP BY 1), "
        f"e AS (SELECT DATE_TRUNC('week', {end}) AS w, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {metric}) AS med "
        f"FROM t WHERE {metric} >= 0 AND {end} >= TIMESTAMP '{base_from}' GROUP BY 1) "
        f"SELECT s.w, s.med, e.med FROM s JOIN e ON s.w = e.w ORDER BY s.w")
    weekly = _rows(runner.run(table, weekly_sql))

    cohort = {
        "base": _f(cohort_rows["base"][3]) if cohort_rows["base"][3] is not None else None,
        "period": _f(cohort_rows["period"][3]) if cohort_rows["period"][3] is not None else None,
        "base_done": 100.0 * cohort_rows["base"][2] / cohort_rows["base"][1],
        "period_done": 100.0 * cohort_rows["period"][2] / cohort_rows["period"][1],
    }
    finished = {"base": _f(finished_rows["base"][2]), "period": _f(finished_rows["period"][2])}
    fair = {"base": _f(fair_rows["base"][2]) if enough("base") else None,
            "period": _f(fair_rows["period"][2]) if enough("period") else None}
    verdict = claim_verdict(wants_faster, cohort, finished, fair)

    word = "быстрее" if wants_faster else "медленнее"
    lead = (f"Да, данные это подтверждают: стало {word}." if verdict["confirmed"]
            else "Нет, утверждать это нельзя.")
    lines = [lead, f"Время считал {duration.label}, период — {label}, сравнение с {BASELINE_DAYS // 7} неделями до него."]
    if verdict["trap"]:
        lines.append(
            f"Если считать по дате начала, кажется, что стало {word}: медиана {_num(cohort['base'])} → "
            f"{_num(cohort['period'])} ч. Но данные обрываются {data_end:%d.%m.%Y}: завершено только "
            f"{_num(cohort['period_done'])}% начатых за период (обычно {_num(cohort['base_done'])}%). "
            "В расчёт попали самые быстрые, долгие ещё не закончились.")
    elif verdict["censored"]:
        lines.append(f"Осторожно: данные обрываются {data_end:%d.%m.%Y}, завершено только "
                     f"{_num(cohort['period_done'])}% начатых за период (обычно {_num(cohort['base_done'])}%).")
    lines.append(f"По дате завершения обрыв не мешает: медиана {_num(finished['base'])} → {_num(finished['period'])} ч "
                 f"({_sign(verdict['finished_change'])}%).")
    if verdict["fair_change"] is not None:
        lines.append(f"Доля завершённых за {_num(base_median, 0)} ч среди начатых с полным сроком наблюдения: "
                     f"{_num(fair['base'])}% → {_num(fair['period'])}% ({_sign(verdict['fair_change'])} п.п.).")

    table_rows = [
        ["По дате начала, медиана, ч", _num(cohort["base"]) if cohort["base"] is not None else "—",
         _num(cohort["period"]) if cohort["period"] is not None else "—", f"{_sign(verdict['cohort_change'])}%",
         "искажено обрывом данных" if verdict["censored"] else "надёжно"],
        ["По дате завершения, медиана, ч", _num(finished["base"]), _num(finished["period"]),
         f"{_sign(verdict['finished_change'])}%", "надёжно"],
    ]
    if verdict["fair_change"] is not None:
        table_rows.append([f"Завершено за {_num(base_median, 0)} ч, %", _num(fair["base"]), _num(fair["period"]),
                           f"{_sign(verdict['fair_change'])} п.п.", "надёжно"])
    table_rows.append(["Завершено начатых, %", _num(cohort["base_done"]), _num(cohort["period_done"]),
                       f"{_sign(cohort['period_done'] - cohort['base_done'])} п.п.", "показывает обрыв данных"])

    response = {
        "answer": "\n".join(lines),
        "table": {"columns": ["Показатель", f"{BASELINE_DAYS // 7} недель до периода", label, "Изменение", "Надёжность"],
                  "rows": table_rows},
        "sql": _sql_note(table) + finished_sql,
        "table_name": table["name"],
    }
    if len(weekly) >= 2:
        response["chart"] = {
            "type": "line",
            "title": "Медиана по неделям, ч",
            "x_label": "Неделя",
            "y_label": "ч",
            "categories": [f"{_as_datetime(r[0]):%Y-%m-%d}" for r in weekly],
            "series": [
                {"name": "По дате начала", "data": [round(_f(r[1]), 1) for r in weekly]},
                {"name": "По дате завершения", "data": [round(_f(r[2]), 1) for r in weekly]},
            ],
        }
    return response


def _as_datetime(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    return dt.datetime.fromisoformat(str(value))


def find_conditions(question: str, table: dict, runner, mentioned: list[str] | None = None,
                    say=lambda _text: None) -> dict | None:
    """«При каких условиях крупные задачи дольше ждут начала работы?»"""
    measures = _measures(table)
    named = [c for c in (mentioned or []) if c in measures]
    duration = choose_duration(question, table) if TIMING.search(question) else None
    if duration is not None:
        metric, unit, what = duration.sql, " ч", f"время {duration.label}"
        event = duration.start
    elif named and not LARGE.search(question):
        metric, unit, what = quote_ident(named[0]) + "::numeric", "", f"«{named[0]}»"
        dates = _timestamps(table)
        event = dates[0] if dates else None
    else:
        return None
    if event is None:
        return None

    where = [f"({metric}) >= 0"]
    scope, size_note = "все записи", ""
    size = size_column(table, question)
    if size:
        threshold = _rows(runner.run(table, f"SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {quote_ident(size)}) FROM t"))[0][0]
        if threshold is None:
            return None
        threshold = _f(threshold)
        compare = _rows(runner.run(table, (
            f"SELECT {quote_ident(size)} >= {threshold} AS big, COUNT(*), AVG({metric}), "
            f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {metric}) FROM t WHERE ({metric}) >= 0 GROUP BY 1")))
        by_size = {r[0]: r for r in compare if r[0] is not None}
        where.append(f"{quote_ident(size)} >= {threshold}")
        scope = f"«{_short(size)}» от {_num(threshold, 0)} (верхняя четверть)"
        if True in by_size and False in by_size:
            big, rest = by_size[True], by_size[False]
            size_note = (f"Сам размер почти ни при чём: среднее {_num(_f(big[2]))}{unit} у крупных и {_num(_f(rest[2]))}{unit} у остальных."
                         if abs(_change(_f(rest[2]), _f(big[2]))) < NOTABLE_GAP * 100 else
                         f"Размер важен: среднее {_num(_f(big[2]))}{unit} у крупных и {_num(_f(rest[2]))}{unit} у остальных.")
    condition = " AND ".join(where)

    say("Считаю общий уровень")
    overall = _rows(runner.run(table, f"SELECT COUNT(*), AVG({metric}), STDDEV_SAMP({metric}), "
                                      f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {metric}) FROM t WHERE {condition}"))[0]
    if not overall[0] or overall[0] < MIN_GROUP or overall[2] is None:
        return None
    total, mean, sd, median = int(overall[0]), _f(overall[1]), _f(overall[2]), _f(overall[3])
    scale = 1.0
    if duration is not None and abs(mean) < 1:  # фоновые задания идут минуты: «0,0 ч» ничего не говорит
        scale, unit = 60.0, " мин"
    mean, sd, median = mean * scale, sd * scale, median * scale

    found: list[dict] = []
    weak: list[str] = []
    first_sql = None
    for name, expr in _dimensions(table, event):
        say(f"Проверяю разрез: {name}")
        sql = (f"SELECT {expr} AS g, COUNT(*) AS n, AVG({metric}) AS a, "
               f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {metric}) AS m "
               f"FROM t WHERE {condition} AND {expr} IS NOT NULL GROUP BY 1")
        first_sql = first_sql or sql
        groups = [Group(_pretty_group(name, str(r[0])), int(r[1]), _f(r[2]) * scale, _f(r[3]) * scale)
                  for r in _rows(runner.run(table, sql))]
        result = analyse_dimension(groups, mean, sd)
        if result is None:
            continue
        if result["notable"]:
            found.append({"name": name, **result})
        else:
            weak.append(name)
    found.sort(key=lambda d: -d["strength"])
    found = _without_duplicates(found)

    lower = bool(re.search(r"быстрее|меньше|ниже|короче", question, re.IGNORECASE))
    want_high = not lower
    conditions = []
    for d in found:
        for g, gap in d["notable"]:
            conditions.append((d["name"], g, gap))
    wanted = [c for c in conditions if (c[2] > 0) == want_high]
    wanted.sort(key=lambda c: -abs(c[2]))
    opposite = [c for c in conditions if (c[2] > 0) != want_high]
    opposite.sort(key=lambda c: -abs(c[2]))

    head = f"Проверил {len(found) + len(weak)} разрезов: {what}, {scope}, записей {_count(total)}. "
    head += f"Общий уровень: среднее {_num(mean)}{unit}, медиана {_num(median)}{unit}."
    lines = [head]
    if size_note:
        lines.append(size_note)
    word = "меньше" if lower else "больше"
    if wanted:
        lines.append(f"Значение {word} общего уровня:")
        lines.extend(f"- {n}: {g.value} — {_num(g.mean)}{unit} ({_sign(gap * 100, 0)}%, записей {_count(g.n)})"
                     for n, g, gap in wanted[:5])
    else:
        lines.append(f"Условий, при которых значение заметно {word} общего уровня, не нашлось.")
    if opposite:
        n, g, gap = opposite[0]
        lines.append(f"Самое сильное отличие в другую сторону: {n}: {g.value} — {_num(g.mean)}{unit} ({_sign(gap * 100, 0)}%).")
    if weak:
        lines.append("Без заметной разницы: " + ", ".join(f"«{w}»" for w in weak[:6]) + ".")
    lines.append("Это сравнение групп, оно показывает связь, но не доказывает причину.")

    shown = (wanted + opposite)[:12]
    if not shown:
        return {"answer": "\n".join(lines), "sql": _sql_note(table) + (first_sql or ""), "table_name": table["name"]}
    avg_label = f"Среднее{',' + unit if unit else ''}"
    response = {
        "answer": "\n".join(lines),
        "table": {
            "columns": ["Условие", "Значение", "Записей", avg_label, "Медиана", "К общему уровню, %"],
            "rows": [[n, g.value, _count(g.n), _num(g.mean), _num(g.median), _sign(gap * 100, 0)] for n, g, gap in shown],
        },
        "sql": _sql_note(table) + (first_sql or ""),
        "table_name": table["name"],
    }
    top = shown[:8]
    if len(top) >= 2:
        response["chart"] = {
            "type": "bar",
            "title": f"Условия: {avg_label.lower()}, общий уровень {_num(mean)}",
            "x_label": "Условие",
            "y_label": avg_label,
            "categories": [g.value for _, g, _ in top],
            "series": [{"name": avg_label, "data": [round(g.mean, 1) for _, g, _ in top]}],
        }
    return response


def _without_duplicates(found: list[dict]) -> list[dict]:
    """«Город» и «Регион» часто один и тот же разрез: оставляем первый, второй называем рядом."""
    kept: list[dict] = []
    for dim in found:
        same = next((k for k in kept if sorted((g.n, round(g.mean, 6)) for g, _ in k["notable"])
                     == sorted((g.n, round(g.mean, 6)) for g, _ in dim["notable"])), None)
        if same:
            same["name"] = f"{same['name']} / {dim['name']}"
        else:
            kept.append(dim)
    return kept


def answer(question: str, table: dict, runner, mentioned: list[str] | None = None, say=lambda _text: None) -> dict | None:
    """Ответ на открытый вопрос или None, если это обычный вопрос к данным."""
    if CLAIM.search(question):
        return check_claim(question, table, runner, say)
    if DRIVERS.search(question):
        return find_conditions(question, table, runner, mentioned, say)
    return None
