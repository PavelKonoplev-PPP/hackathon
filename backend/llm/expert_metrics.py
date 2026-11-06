"""Grounded SQL methodology and fail-closed checks for difficult BI metrics.

Nothing in this module contains precomputed benchmark results. Hints are enabled
only when both the user's intent and the matching source columns are present.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re


_RU_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
    "ноябр": 11, "декабр": 12,
}


def _has(columns: list[str], *required: str) -> bool:
    return set(required).issubset(columns)


def _month_window(q: str) -> tuple[str, str] | None:
    match = re.search(
        r"\b(январ\w*|феврал\w*|март\w*|апрел\w*|мае|май|"
        r"июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
        r"\s+(\d{4})\b", q, re.IGNORECASE,
    )
    if not match:
        return None
    year = int(match.group(2))
    month = next((number for key, number in _RU_MONTHS.items()
                  if match.group(1).lower().startswith(key)), None)
    if not month or not 1 <= year <= 9998:
        return None
    begin = dt.date(year, month, 1)
    end = dt.date(year + (month == 12), month % 12 + 1, 1)
    return begin.isoformat(), end.isoformat()


def _end_of_day(q: str) -> str | None:
    match = re.search(
        r"\b(\d{1,2})\s+(январ\w*|феврал\w*|март\w*|апрел\w*|мая|"
        r"июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
        r"\s+(\d{4})", q, re.IGNORECASE,
    )
    if not match:
        return None
    month = next((number for key, number in _RU_MONTHS.items()
                  if match.group(2).lower().startswith(key)), None)
    if not month:
        return None
    try:
        return (dt.date(int(match.group(3)), month, int(match.group(1))) +
                dt.timedelta(days=1)).isoformat()
    except ValueError:
        return None


def _is_asof_purchase(q: str, columns: list[str]) -> bool:
    return (
        _has(columns, "Согласована (МСК)", "Оплачена (МСК)",
             "Отменена (МСК)", "Отклонена (МСК)", "Подразделение")
        and "согласован" in q and "оплачен" in q
        and "подразделен" in q  # подсказка и формула группируют по подразделениям: иной разрез считает модель
        and re.search(r"\bне\s+оплачен|\bнеоплачен", q) is not None
        and re.search(r"на\s+конец|по\s+состоян|на\s+дату", q) is not None
    )


def _is_retry_chain(q: str, columns: list[str]) -> bool:
    return (
        _has(columns, "Задание", "Код цепочки запусков", "Номер попытки", "Статус")
        and re.search(r"цепочк", q) is not None
        and re.search(r"повторн", q) is not None
        and re.search(r"успешн", q) is not None
    )


def _percentile(q: str) -> int | None:
    match = re.search(r"\b(?:p(?:90|95)|(?:90|95)(?:-[а-яё]+)?\s*"
                      r"(?:перцентил|процентил))", q, re.IGNORECASE)
    if not match:
        return None
    number = re.search(r"(90|95)", match.group(0))
    return int(number.group(1)) if number else None


def expert_sql_hint(question: str, columns: list[str]) -> str:
    """Methodological SQL example for a recognised complex metric."""
    q = question.lower()
    percentile = _percentile(q)
    if percentile in (90, 95):
        cutoff = percentile / 100
        window = _month_window(q)
        if (window and _has(columns, "Группа поддержки", "Создано (МСК)",
                            "Закрыто (МСК)") and "решен" in q or
                window and _has(columns, "Группа поддержки", "Создано (МСК)",
                                "Закрыто (МСК)") and "времен" in q):
            start, end = window
            return (
                "\n[Методика P90/P95 техподдержки]: "
                f"Выбери только закрытые в [{start}, {end}) по «Закрыто (МСК)», "
                "считай время решения в часах как "
                "EXTRACT(EPOCH FROM (\"Закрыто (МСК)\" - \"Создано (МСК)\"))/3600, "
                "затем группируй по \"Группа поддержки\" и рассчитывай "
                f"PERCENTILE_CONT({cutoff:g}) WITHIN GROUP (ORDER BY часы_решения). "
                "Выведи в итоговом SELECT и COUNT(*) AS cases (количество закрытий), "
                "и PERCENTILE_CONT(0.9) с округлением до двух знаков. "
                "Если требуется минимум 100 закрытий, поставь HAVING COUNT(*) >= 100. "
                "Используй CTE WITH s AS (SELECT ... FROM t); никаких AVG/MAX вместо перцентиля.\n"
            )
        if (window and _has(columns, "Перевозчик", "Обещанный срок (МСК)",
                            "Доставлено (МСК)", "Статус")
                and "задерж" in q):
            start, end = window
            return (
                "\n[Методика P90/P95 задержки доставки]: "
                f"WHERE \"Статус\" = 'Доставлено' и \"Доставлено (МСК)\" >= '{start}' "
                f"AND \"Доставлено (МСК)\" < '{end}'. "
                "Считай задержку в часах как GREATEST(0, "
                "EXTRACT(EPOCH FROM (\"Доставлено (МСК)\" - \"Обещанный срок (МСК)\"))/3600). "
                "Группируй по \"Перевозчик\"; используй "
                f"PERCENTILE_CONT({cutoff:g}) WITHIN GROUP (ORDER BY задержка_часы). "
                "Доставку вовремя учитывай как ноль. Выведи в итоговом SELECT "
                "COUNT(*) AS deliveries и перцентиль с округлением до двух знаков. "
                "Можно использовать CTE из t.\n"
            )
    if _is_asof_purchase(q, columns):
        exclusive_end = _end_of_day(q)
        if exclusive_end:
            condition = "TIMESTAMP '" + exclusive_end + "'"
            return (
                "\n[Исторический срез статуса]: Состояние определяется датами событий, "
                "не текущим текстовым статусом. В конце указанного дня используй "
                f"WHERE \"Согласована (МСК)\" < {condition} "
                f"AND (\"Оплачена (МСК)\" IS NULL OR \"Оплачена (МСК)\" >= {condition}) "
                f"AND (\"Отменена (МСК)\" IS NULL OR \"Отменена (МСК)\" >= {condition}) "
                f"AND (\"Отклонена (МСК)\" IS NULL OR \"Отклонена (МСК)\" >= {condition}). "
                "Затем GROUP BY \"Подразделение\", COUNT(*), "
                "SUM(\"Согласованная сумма, руб.\"). "
                "Не добавляй фильтр \"Статус\"='Оплачена': он противоречит вопросу.\n"
            )
    if _is_retry_chain(q, columns):
        return (
            "\n[Семантика цепочек повторных попыток]: Сначала объедини строки "
            "в цепочки по \"Задание\", \"Код цепочки запусков\". "
            "В CTE рассчитай MAX(\"Номер попытки\") AS attempts, "
            "(ARRAY_AGG(\"Статус\" ORDER BY \"Номер попытки\" DESC))[1] AS final_status. "
            "Затем по каждому заданию считай число цепочек, число цепочек "
            "WHERE attempts > 1 AND final_status='Успешно', их долю от ВСЕХ "
            "цепочек и AVG(attempts) только среди таких успешных цепочек. "
            "Запрос делай через WITH chains AS (SELECT ... FROM t GROUP BY ...), "
            "SELECT ... FROM chains GROUP BY ... . Не используй AVG(\"Номер попытки\") "
            "по исходным строкам и не фильтруй по \"Описание ошибки\".\n"
        )
    if (_has(columns, "ID_Аптеки", "ID_Лекарства", "Сколько_Купили", "Дата")
            and "среднедневн" in q and "аптек" in q and "без продаж" in q):
        return (
            "\n[Среднедневные продажи на аптеку]: знаменатель — общее число "
            "уникальных аптек во ВСЕЙ таблице, умноженное на число КАЛЕНДАРНЫХ "
            "дней месяца (28, 29, 30 или 31), включая дни без продаж. "
            "Сделай CTE pharmacies AS (SELECT COUNT(DISTINCT \"ID_Аптеки\") AS n FROM t); "
            "агрегируй SUM(\"Сколько_Купили\") по \"ID_Лекарства\" для каждого "
            "сравниваемого месяца отдельно, дели на n*days, затем "
            "считай 100 * (current_daily/NULLIF(previous_daily,0)-1). "
            "Не дели на количество строк с продажами и не считай отсутствующие дни "
            "нулевыми строками: они уже учтены календарным знаменателем.\n"
        )
    if (_has(columns, "Город", "Количество, шт.", "Цена до скидки за единицу, руб.",
             "Скидка, %", "Дата операции (МСК)")
            and "чист" in q and "выручк" in q and "возврат" in q):
        return (
            "\n[Чистая выручка с возвратами]: метрика = "
            "SUM(\"Количество, шт.\" * \"Цена до скидки за единицу, руб.\" * "
            "(1 - \"Скидка, %\"/100.0)). Отрицательное количество у возвратов "
            "уже уменьшает итог: не исключай их фильтром статуса. "
            "Считай эту формулу отдельно за каждый календарный месяц, "
            "группируй по \"Город\", процентное изменение = "
            "100 * (выручка_новая / NULLIF(выручка_старая,0) - 1), "
            "затем сортируй по процентному изменению DESC.\n"
        )
    return ""


def check_expert_sql(sql: str, question: str, columns: list[str]) -> list[str]:
    """Fail closed on known wrong measures, even on the last model retry."""
    q = question.lower()
    problems: list[str] = []
    percentile = _percentile(q)
    if percentile and (
        ("времен" in q and _has(columns, "Группа поддержки", "Закрыто (МСК)"))
        or ("задерж" in q and _has(columns, "Перевозчик", "Доставлено (МСК)"))
    ):
        expected = f"{percentile / 100:g}"
        if not re.search(r"\bpercentile_cont\s*\(\s*" + re.escape(expected) + r"\s*\)",
                         sql, re.IGNORECASE):
            problems.append(
                f"Вопрос о P{percentile}: нужен PERCENTILE_CONT({expected}) WITHIN GROUP, "
                "а не AVG, MAX или простой COUNT."
            )
        if "задерж" in q and "вовремя" in q and not re.search(
            r"\bgreatest\s*\(\s*0(?:\.0+)?\s*,", sql, re.IGNORECASE
        ):
            problems.append("Доставку вовремя считай нулевой задержкой: GREATEST(0, ...).")
        # The reference acceptance includes the number of observations in
        # addition to P90/P95; COUNT in HAVING is not an output column.
        selects = list(re.finditer(r"\bselect\b", sql, re.IGNORECASE))
        if selects:
            final_select = sql[selects[-1].end():]
            final_select = re.split(r"\bfrom\b", final_select, maxsplit=1,
                                    flags=re.IGNORECASE)[0]
            if not re.search(r"\bcount\s*\(\s*\*\s*\)", final_select, re.IGNORECASE):
                problems.append(
                    "Выведи COUNT(*) в итоговом SELECT рядом с перцентилем: "
                    "для проверки нужен размер выборки по каждой группе."
                )
        if percentile == 90 and "времен" in q and re.search(r"менее\s+чем\s+100", q):
            if not re.search(
                r"\bhaving\s+count\s*\(\s*\*\s*\)\s*>=\s*100\b", sql,
                re.IGNORECASE,
            ):
                problems.append("Исключи группы с менее чем 100: HAVING COUNT(*) >= 100.")
        if "задерж" in q and _has(columns, "Статус"):
            if not re.search(r'"Статус"\s*=\s*\'Доставлено\'', sql, re.IGNORECASE):
                problems.append("Учитывай только доставленные: WHERE \"Статус\" = 'Доставлено'.")
    if _is_asof_purchase(q, columns):
        if re.search(r'"Статус"\s*=\s*\'Оплачена\'', sql, re.IGNORECASE):
            problems.append("Не отбирай статус «Оплачена», если требуются неоплаченные на дату.")
        for name in ("Оплачена (МСК)", "Отменена (МСК)", "Отклонена (МСК)"):
            quoted = re.escape(f'"{name}"')
            # Historical state needs to retain records whose event has NOT yet
            # happened at the cutoff (NULL OR future timestamp).
            has_temporal_null = bool(re.search(
                r"\(\s*" + quoted + r"\s+IS\s+NULL\s+OR\s+" + quoted +
                r"\s*>=\s*", sql, re.IGNORECASE,
            ))
            if not has_temporal_null:
                problems.append(
                    f"Для \"{name}\" на дату нужен фильтр "
                    f"(\"{name}\" IS NULL OR \"{name}\" >= граница_следующего_дня)."
                )
        if not re.search(r'"Согласована \(МСК\)"\s*(?:<|<=)\s*', sql, re.IGNORECASE):
            problems.append("Для состояния на дату отфильтруй время согласования до конца дня.")
        exclusive_end = _end_of_day(q)
        if exclusive_end:
            # A well-shaped WHERE can still use midnight at the START of
            # 31 March instead of midnight on 1 April. This silently loses
            # an entire day, so validate the date for every event filter.
            for name, operator in (
                ("Согласована (МСК)", "<"),
                ("Оплачена (МСК)", ">="),
                ("Отменена (МСК)", ">="),
                ("Отклонена (МСК)", ">="),
            ):
                pattern = (
                    re.escape(f'"{name}"') + r"\s*" + re.escape(operator) +
                    r"\s*(?:TIMESTAMP\s+|DATE\s+)?'([^']+)'"
                )
                dates = re.findall(pattern, sql, re.IGNORECASE)
                if exclusive_end not in dates:
                    problems.append(
                        f'Исторический срез на конец дня: для "{name}" '
                        f"используй границу {exclusive_end}, "
                        "а не начало последнего дня."
                    )
    if _is_retry_chain(q, columns):
        if not re.search(
            r"\bgroup\s+by\b[^;]*\"Код цепочки запусков\"", sql,
            re.IGNORECASE | re.DOTALL,
        ):
            problems.append(
                "Сначала сгруппируй исходные попытки по \"Код цепочки запусков\" "
                "в CTE, затем агрегируй цепочки по \"Задание\"."
            )
        if not re.search(r'\bmax\s*\(\s*"Номер попытки"\s*\)', sql, re.IGNORECASE):
            problems.append("Число попыток в цепочке: MAX(\"Номер попытки\") внутри CTE.")
        if not re.search(
            r'\barray_agg\s*\(\s*"Статус"\s+order\s+by\s+"Номер попытки"\s+desc',
            sql, re.IGNORECASE,
        ) and not re.search(r'\brow_number\s*\(\s*\)\s*over\b', sql, re.IGNORECASE):
            problems.append(
                "Успех цепочки определяй по статусу её ПОСЛЕДНЕЙ попытки "
                "(ARRAY_AGG(«Статус» ORDER BY «Номер попытки» DESC)[1])."
            )
        if re.search(r'\bavg\s*\(\s*"Номер попытки"\s*\)', sql, re.IGNORECASE):
            problems.append("AVG(\"Номер попытки\") по строкам неверен: усредняй число попыток по цепочкам.")
        if "ошиб" not in q and re.search(
            r'\bwhere\b[^;]*"Описание ошибки"\s*=', sql, re.IGNORECASE | re.DOTALL
        ):
            problems.append("Не добавляй фильтр по \"Описание ошибки\": его нет в вопросе.")
    return problems
