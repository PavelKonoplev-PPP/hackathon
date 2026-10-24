"""Понимание вопроса и смысловая проверка SQL (issues: P0 корректность агента, #4).

Синтаксически верный SQL может считать не то, что спросили. Здесь код
(без модели) разбирает вопрос и сверяет с ним SQL:
- «сколько ...» — нужен COUNT/SUM, а не список строк с LIMIT;
- «разных / уникальных» — нужен COUNT(DISTINCT ...);
- LIMIT только для «топ / самые»; для «топ-5» — ровно 5;
- «по месяцам / по годам» — группировка по date_trunc;
- фильтры WHERE — только по значениям, которые есть в данных и упомянуты в вопросе.
Ошибки формулируются так, чтобы их можно было вернуть модели на исправление.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

STOP_WORDS = {"какой", "какая", "какие", "каких", "какое", "каком", "сколько", "больше",
              "меньше", "всего", "всех", "было", "были", "самый", "самая", "самые", "самых",
              "каждый", "каждого", "каждой", "каждом", "который", "среди", "между", "через",
              "покажи", "показать", "посчитай", "данные", "данных", "таблица", "таблицы",
              "приносит", "лучше", "хуже", "чаще", "среднее", "средняя", "средний", "только",
              "теперь", "также", "этот", "этом", "этой", "такой", "есть", "нужно", "можно",
              # единицы счёта — не тема вопроса
              "запись", "записи", "записей", "строк", "строки", "строка", "строках"}

UNIT_WORDS = re.compile(r"строк|запис|операци|позици|штук|\bшт\b|заказ|покупател|клиент|"
                        r"пользоват|уникальн|разн|различн|единиц", re.IGNORECASE)


def words(text: str) -> set[str]:
    """Значимые слова (от 4 букв, без служебных) в нижнем регистре."""
    found = re.findall(r"[а-яёa-z0-9]+", text.lower().replace("_", " ").replace("ё", "е"))
    return {w for w in found if len(w) >= 4 and w not in STOP_WORDS}


def same_word(a: str, b: str) -> bool:
    """«теме» ~ «тема», «каналам» ~ «канал», «розницу» ~ «розничный»: общее начало слова."""
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return common >= 3 and common >= min(len(a), len(b), 6) - 1


def strong_word(a: str, b: str) -> bool:
    """Почти та же словоформа: «операций» ~ «операции», но не «оператора»."""
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return common >= 4 and common >= min(len(a), len(b)) - 2


def overlap(asked: set[str], target: set[str]) -> int:
    return sum(any(same_word(q, w) for w in target) for q in asked)


def quoted(question: str) -> list[str]:
    """Значения, явно взятые в кавычки: «Продажа», "Возврат"."""
    return [q.lower() for q in re.findall(r"[«\"']([^»\"']+)[»\"']", question)]


def mentions(question: str, value: str, ignore: set[str] | None = None) -> bool:
    """Упомянуто ли значение категории в вопросе («через розницу» ~ «Розничный магазин»).
    ignore — слова, которые не считаются упоминанием (название таблицы: «строк продаж»
    не означает фильтр «Тип операции = 'Продажа'»), если значение не взято в кавычки."""
    if value.lower() in quoted(question) or value.lower() in question.lower():
        return True  # значение названо дословно («VK Реклама», «Мобильное приложение»)
    value_words = words(value)
    if not value_words:  # короткие значения: «Да», «VK»
        return value.lower() in question.lower()
    asked = words(question)
    if ignore:
        asked = {q for q in asked if not any(same_word(q, w) for w in ignore)}
    return overlap(asked, value_words) > 0


# ---------- разбор вопроса ----------

_TOP = re.compile(r"\bтоп\b|\bтоп-?\d+|самы[йхеаяо]|больше всего|меньше всего|наибол|наимен|"
                  r"лидер|лучш|худш|первы[хе]\s+\d+|\d+\s+(самых|лучших|худших|крупнейших)",
                  re.IGNORECASE)
_TOP_N = re.compile(r"топ[- ]?(\d+)|первы[хе]\s+(\d+)|(\d+)\s+(?:самых|лучших|худших|крупнейших)",
                    re.IGNORECASE)
# «по категориям», «по каналам», «в каждом статусе», «для каждого города», «в разрезе»
_GROUPS = re.compile(r"\bпо\s+(кажд\w*|\w+(ам|ям|ах|ях)\b)|\b(в|для)\s+кажд\w*|в\s+разрезе|"
                     r"\bразбивк|\bраспредел", re.IGNORECASE)
# Разбивка по времени — только явные фразы: «по месяцам», «в каждом месяце», «помесячно».
# «за сентябрь 2025 года» — это фильтр, а не группировка.
_GRAINS = [("month", r"по\s+месяц|кажд\w*\s+месяц|помесячн|динамик"),
           ("year", r"по\s+год|кажд\w*\s+год|ежегодн"),
           ("quarter", r"по\s+квартал|кажд\w*\s+квартал|поквартальн"),
           ("week", r"по\s+недел|кажд\w*\s+недел|еженедельн"),
           ("day", r"по\s+дн(ям|ю)|кажд\w*\s+д(ень|ня)|ежедневн")]


@dataclass
class Intent:
    is_count: bool
    wants_distinct: bool
    wants_top: bool
    top_n: int | None
    time_grain: str | None
    has_unit: bool
    wants_groups: bool

    @classmethod
    def parse(cls, question: str) -> "Intent":
        q = question.lower()
        top_n = None
        m = _TOP_N.search(q)
        if m:
            top_n = int(next(g for g in m.groups() if g))
        grain = next((name for name, pattern in _GRAINS if re.search(pattern, q)), None)
        return cls(
            is_count=bool(re.search(r"\bсколько\b|\bколичеств|\bчисло\b", q)),
            wants_distinct=bool(re.search(r"уникальн|разных|различн", q)),
            wants_top=bool(_TOP.search(q)) or top_n is not None,
            top_n=top_n,
            time_grain=grain,
            has_unit=bool(UNIT_WORDS.search(q)),
            wants_groups=bool(_GROUPS.search(q)) or grain is not None,
        )


def counted_noun(question: str) -> str | None:
    """Что именно считают: первое значимое слово после «сколько»."""
    m = re.search(r"\bсколько\b(.*)", question.lower())
    if not m:
        return None
    for w in re.findall(r"[а-яё]+", m.group(1)):
        if len(w) >= 4 and w not in STOP_WORDS:
            return w
    return None


# ---------- проверка и исправление SQL ----------

_AGG = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)
_TRAILING_LIMIT = re.compile(r"\s+limit\s+(\d+)\s*$", re.IGNORECASE)
_FILTER = re.compile(
    r'"([^"]+)"\s*(=|<>|!=|\bnot\s+like\b|\bilike\b|\blike\b|\bnot\s+in\b|\bin\b)\s*'
    r"(\(\s*'(?:[^']|'')*'(?:\s*,\s*'(?:[^']|'')*')*\s*\)|'(?:[^']|'')*')",
    re.IGNORECASE)


def adjust_sql(sql: str, intent: Intent) -> str:
    """Автоисправления, на которые не стоит тратить попытку модели."""
    has_group = re.search(r"\bgroup\s+by\b", sql, re.IGNORECASE)
    m = _TRAILING_LIMIT.search(sql)
    if intent.wants_top:
        if intent.top_n:
            sql = _TRAILING_LIMIT.sub("", sql) + f"\nLIMIT {intent.top_n}"
        elif m and has_group and int(m.group(1)) == 1:
            sql = _TRAILING_LIMIT.sub("\nLIMIT 10", sql)  # «самый» — покажем и остальных
    elif m and has_group:
        sql = _TRAILING_LIMIT.sub("", sql)  # не топ: нужны все группы
    return sql


def _literals(group: str) -> list[str]:
    return [v.replace("''", "'") for v in re.findall(r"'((?:[^']|'')*)'", group)]


def _where_bounds(sql: str) -> tuple[int, int] | None:
    """Границы условия WHERE верхнего уровня (без подзапросов)."""
    if re.search(r"\(\s*select\b", sql, re.IGNORECASE):
        return None
    m = re.search(r"\bwhere\b", sql, re.IGNORECASE)
    if not m:
        return None
    end = re.search(r"\b(group\s+by|order\s+by|having|limit)\b", sql[m.end():], re.IGNORECASE)
    return m.start(), m.end() + (end.start() if end else len(sql) - m.end())


def strip_unmentioned_filters(sql: str, question: str, values: dict[str, list[str]],
                              ignore: set[str] | None = None) -> tuple[str, list[str]]:
    """Убирает из WHERE условия по категориям, которых нет в вопросе
    (маленькие модели любят «на всякий случай» добавить фильтр). Только для
    простых условий через AND; возвращает (новый SQL, список убранных условий)."""
    bounds = _where_bounds(sql)
    if not bounds:
        return sql, []
    start, end = bounds
    clause = sql[start + len("where"):end]
    if re.search(r"\bor\b", clause, re.IGNORECASE):
        return sql, []
    conditions = re.split(r"\band\b", clause, flags=re.IGNORECASE)
    kept, removed = [], []
    for cond in conditions:
        m = _FILTER.fullmatch(cond.strip())
        if m and m.group(1) in values and "not" not in m.group(2).lower():
            literals = _literals(m.group(3))
            known = {v.lower() for v in values[m.group(1)]}
            if literals and all(v.lower() in known for v in literals) and \
                    not any(mentions(question, v, ignore) for v in literals):
                removed.append(cond.strip())
                continue
        kept.append(cond.strip())
    if not removed:
        return sql, []
    where = (" WHERE " + " AND ".join(kept) + " ") if kept else " "
    return (sql[:start].rstrip() + where + sql[end:].lstrip()).strip(), removed


def _asked_words(question: str, ignore: set[str] | None) -> set[str]:
    asked = words(question)
    if ignore:
        asked = {q for q in asked if not any(same_word(q, w) for w in ignore)}
    return asked


def _short_words(text: str) -> set[str]:
    """Слова названия колонки, включая короткие: «Тип_Лекарства» -> {тип, лекарства}."""
    found = re.findall(r"[а-яёa-z0-9]+", text.lower().replace("_", " ").replace("ё", "е"))
    return {w for w in found if len(w) >= 3}


def _check_group_by(sql: str, question: str, columns: list[str],
                    ignore: set[str] | None, values: dict[str, list[str]] | None = None) -> list[str]:
    """GROUP BY только по тому, о чём спросили («в каждом статусе» — не по команде)."""
    m = re.search(r"\bgroup\s+by\b(.*?)(\border\s+by\b|\bhaving\b|\blimit\b|$)", sql,
                  re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    clause = re.sub(r"(date_trunc|extract|date_part)\s*\([^)]*\)", " ", m.group(1), flags=re.IGNORECASE)
    asked = _asked_words(question, ignore)
    values = values or {}
    extra = [c for c in re.findall(r'"([^"]+)"', clause)
             if c in columns and not overlap(asked, _short_words(c))
             # «Сравни Яндекс Директ и VK Рекламу» — названы значения колонки «Площадка»
             and not any(mentions(question, v, ignore) for v in values.get(c, []))]
    if not extra:
        return []
    return [f"Лишняя группировка по \"{extra[0]}\": этого нет в вопросе — убери её из "
            "GROUP BY и SELECT."]


def _check_missing_filter(sql: str, question: str, values: dict[str, list[str]],
                          columns: list[str], ignore: set[str] | None, strict: bool) -> list[str]:
    """В вопросе есть условие («без карты», «через розницу»), а фильтра в SQL нет."""
    if not strict:
        return []
    asked = _asked_words(question, ignore)
    column_words = set().union(set(), *(words(c) for c in columns))
    # слова, которые называют колонку («по категориям товаров»), — это разрез, а не условие
    condition_words = {q for q in asked if not any(same_word(q, w) for w in column_words)}
    # слова, уже покрытые фильтрами запроса («без карты» -> 'Без карты лояльности')
    used = set().union(set(), *(words(v) for v in re.findall(r"'((?:[^']|'')*)'", sql)))
    condition_words = {q for q in condition_words if not any(same_word(q, w) for w in used)}
    explicit = {q.lower() for q in quoted(question)}
    candidates = []
    for column, vs in values.items():
        if re.search(r'"' + re.escape(column) + r'"\s*(=|<>|!=|\bin\b|\blike\b|\bilike\b|\bnot\b)',
                     sql, re.IGNORECASE):
            continue  # по этой колонке уже фильтруют
        if re.search(r'group\s+by[^;]*"' + re.escape(column) + '"', sql, re.IGNORECASE | re.DOTALL):
            continue  # по ней группируют — значения и так видны
        for v in vs:
            if v.lower() in explicit or overlap(condition_words, words(v)):
                candidates.append(f"\"{column}\" = '{v}'")
    if not candidates:
        return []
    # если фраза из вопроса дословно входит в значение («без карты» -> 'Без карты лояльности'),
    # остальные совпадения по одному слову («Банковская карта») — шум
    tokens = re.findall(r"[а-яёa-z0-9]+", question.lower())
    phrases = {f"{a} {b}" for a, b in zip(tokens, tokens[1:])}
    exact = [c for c in candidates if any(p in c.lower() for p in phrases)]
    candidates = exact or candidates
    return ["В вопросе есть условие, а в запросе нет фильтра. Подходящие значения: "
            + ", ".join(candidates[:4]) + ". Добавь нужное условие в WHERE."]


def check_sql(sql: str, question: str, intent: Intent, values: dict[str, list[str]],
              strict: bool = True, ignore: set[str] | None = None,
              numeric_columns: list[str] | None = None,
              columns: list[str] | None = None) -> list[str]:
    """Смысловые проблемы SQL относительно вопроса. Пустой список — всё в порядке.

    strict=False (последняя попытка) — не придираемся к упоминанию фильтров в вопросе.
    ignore — слова названия таблицы (не считаются упоминанием значения).
    numeric_columns — числовые колонки: «сколько кликов» при колонке «Клики» — это SUM.
    columns — все колонки таблицы: для проверки группировок и пропущенных фильтров.
    """
    problems = []
    low = sql.lower()
    if intent.is_count and not _AGG.search(sql):
        problems.append("Вопрос «сколько»: нужен COUNT(...) или SUM(...), а не список строк.")
    noun = counted_noun(question)
    if intent.is_count and noun and numeric_columns:
        # «Номер события в сессии» — порядковый номер, а не количество: его не суммируют
        amounts = [c for c in numeric_columns if not re.match(r"(номер|код|id)\b", c, re.IGNORECASE)]
        matching = [c for c in amounts if any(same_word(noun, w) for w in words(c))]
        summed = [c for c in matching
                  if re.search(r'sum\s*\(\s*(t\.)?"' + re.escape(c) + '"', sql, re.IGNORECASE)]
        if matching and not summed:
            options = " или ".join(f'SUM("{c}")' for c in matching)
            problems.append(f"«Сколько {noun}» — это сумма значений колонки: {options}, а не COUNT(*).")
    if intent.wants_distinct and "distinct" not in low:
        problems.append("В вопросе «разных/уникальных»: считай COUNT(DISTINCT \"колонка\").")
    if intent.time_grain and not re.search(r"date_trunc|extract|date_part", low):
        problems.append(f"Нужна группировка по времени: date_trunc('{intent.time_grain}', "
                        "\"колонка с датой\").")
    elif intent.wants_groups and not re.search(r"\bgroup\s+by\b", low):
        problems.append("В вопросе просят разбивку («по ...», «в каждом ...»): "
                        "добавь GROUP BY по нужной колонке и выведи её в SELECT.")
    if not re.search(r"\bfrom\s+t\b", low):
        problems.append("Запрос должен брать данные из таблицы t.")

    if columns:
        problems += _check_group_by(sql, question, columns, ignore, values)
        problems += check_money(sql, question, columns)
        problems += _check_missing_filter(sql, question, values, columns, ignore, strict)

    value_owner = {v.lower(): col for col, vs in values.items() for v in vs}
    for column, op, raw in _FILTER.findall(sql):
        known = values.get(column)
        if not known:
            # значение известной категории приклеено к другой колонке
            for literal in _literals(raw):
                owner = value_owner.get(literal.strip("%").lower())
                if owner and owner != column:
                    problems.append(f"'{literal}' — значение колонки \"{owner}\", "
                                    f"а не \"{column}\".")
            continue  # у колонки много значений — не проверяем
        known_low = {v.lower(): v for v in known}
        for literal in _literals(raw):
            text = literal.strip("%").lower()
            if not text:
                continue
            if "like" in op.lower():
                matched = [v for lv, v in known_low.items() if text in lv]
            else:
                matched = [known_low[text]] if text in known_low else []
            if not matched:
                problems.append(f"В колонке \"{column}\" нет значения '{literal}'. "
                                f"Есть: " + ", ".join(f"'{v}'" for v in known) + ".")
            elif strict and not any(mentions(question, v, ignore) for v in matched):
                problems.append(f"Фильтр \"{column}\" = '{literal}' не упоминается в вопросе — "
                                "убери его.")
    return problems


# ---------- вид графика по просьбе пользователя (issue #16) ----------

VIEW_PATTERNS = [
    ("pie", r"кругов\w*|кругом|пирог\w*"),
    ("line", r"линейн\w*|линией|линиями|линию"),
    ("bar", r"столбч\w*|столбц\w*|столбик\w*|гистограмм\w*"),
    ("table", r"таблицей|в\s+виде\s+таблицы|как\s+таблицу|таблично"),
]
_VIEW_FILLER = {"покажи", "показать", "отобрази", "отобразить", "сделай", "нарисуй", "построй",
                "давай", "можно", "пожалуйста", "это", "этот", "эти", "тоже", "самое", "же", "то",
                "в", "виде", "как", "а", "и", "теперь", "график", "графиком", "графике",
                "диаграмма", "диаграммой", "диаграмму", "диаграмме", "вид", "лучше", "мне", "его",
                "их", "результат", "ответ", "данные", "на", "по", "форме", "формате", "вместо"}


def requested_view(text: str) -> str | None:
    """«покажи кругом» -> 'pie'; «по месяцам линией» -> 'line'; иначе None."""
    low = text.lower()
    for view, pattern in VIEW_PATTERNS:
        if re.search(pattern, low):
            return view
    return None


def is_pure_view_request(text: str) -> bool:
    """Только смена вида («покажи то же самое кругом»), без новых условий («по месяцам линией»)."""
    view = requested_view(text)
    if not view:
        return False
    low = text.lower()
    for _, pattern in VIEW_PATTERNS:
        low = re.sub(pattern, " ", low)
    rest = [w for w in re.findall(r"[а-яёa-z0-9]+", low) if w not in _VIEW_FILLER]
    return not rest


# ---------- выручка: количество × цена (× скидка) ----------

MONEY = re.compile(r"выручк|оборот|доход|сумм\w*\s+продаж|продаж\w*\s+в\s+рубл", re.IGNORECASE)


def money_parts(columns: list[str]) -> tuple[str, str, str | None] | None:
    """Колонки количества, цены за единицу и скидки в процентах, если они есть в таблице."""
    qty = next((c for c in columns if re.search(r"количеств", c, re.IGNORECASE)), None)
    price = next((c for c in columns if re.search(r"цен", c, re.IGNORECASE)
                  and re.search(r"единиц", c, re.IGNORECASE)), None)
    disc = next((c for c in columns if re.search(r"скидк", c, re.IGNORECASE) and "%" in c), None)
    return (qty, price, disc) if qty and price else None


def money_formula(columns: list[str]) -> str | None:
    parts = money_parts(columns)
    if not parts:
        return None
    qty, price, disc = parts
    inner = f'"{qty}" * "{price}"' + (f' * (1 - "{disc}" / 100.0)' if disc else "")
    return f"SUM({inner})"


def check_money(sql: str, question: str, columns: list[str]) -> list[str]:
    """«Выручка» в таблице с ценой за единицу — обязательно количество × цена (× скидка)."""
    parts = money_parts(columns)
    if not parts or not MONEY.search(question):
        return []
    qty, price, disc = parts
    formula = money_formula(columns)
    if f'"{qty}"' not in sql or f'"{price}"' not in sql:
        return [f"Выручка = {formula} — используй именно эту формулу."]
    if disc and re.search(r"скидк", question, re.IGNORECASE) and not re.search(
            r'1\s*-\s*"' + re.escape(disc) + r'"\s*/\s*100', sql):
        return [f"С учётом скидки: {formula} — скидка делится на 100 внутри скобок."]
    return []
