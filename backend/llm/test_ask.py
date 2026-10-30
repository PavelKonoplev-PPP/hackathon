"""Тесты сценария 2 без модели и без БД:
    python -m unittest discover -s backend -p 'test_*.py' -t .
"""
import datetime as dt
import json
import unittest
from pathlib import Path
from decimal import Decimal

from backend.llm.ask import (_SESSIONS, answer_looks_good, answer_question, format_value,
                             missing_note, suggest_chart, template_answer)
from backend.llm.sql_guard import SQLRejected, extract_sql, validate_sql
from backend.llm.sql_runner import QueryResult, build_query, cast_sql

COLS = ["Канал продажи", "Цена, руб.", "Дата"]


class GuardTests(unittest.TestCase):
    def ok(self, sql):
        return validate_sql(sql, COLS)

    def bad(self, sql, text=""):
        with self.assertRaises(SQLRejected) as ctx:
            validate_sql(sql, COLS)
        self.assertIn(text, str(ctx.exception))

    def test_simple_select(self):
        self.assertEqual(self.ok('SELECT "Канал продажи", SUM("Цена, руб.") AS "Сумма" '
                                 'FROM t GROUP BY 1 ORDER BY "Сумма" DESC;'),
                         'SELECT "Канал продажи", SUM("Цена, руб.") AS "Сумма" '
                         'FROM t GROUP BY 1 ORDER BY "Сумма" DESC')

    def test_own_cte_allowed(self):
        self.ok('WITH m AS (SELECT date_trunc(\'month\', "Дата") AS "Месяц" FROM t) '
                'SELECT "Месяц", COUNT(*) FROM m GROUP BY 1')

    def test_writes_rejected(self):
        self.bad('DELETE FROM t', "SELECT")
        self.bad('SELECT 1; DROP TABLE t', "один запрос")
        self.bad('WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x', "DELETE")
        self.bad('SELECT * INTO new_table FROM t', "INTO")

    def test_keywords_inside_strings_are_fine(self):
        self.ok("SELECT COUNT(*) FROM t WHERE \"Канал продажи\" = 'delete; drop'")

    def test_other_tables_rejected(self):
        self.bad('SELECT * FROM raw."data_01_Продажи"', "только в таблице t")
        self.bad('SELECT * FROM pg_catalog.pg_roles', "только в таблице t")
        self.bad('SELECT * FROM t JOIN users ON true', "users")

    def test_dangerous_functions_rejected(self):
        self.bad("SELECT pg_sleep(100) FROM t", "Служебные")
        self.bad("SELECT pg_read_file('/etc/passwd')", "Служебные")
        self.bad("SELECT set_config('x', 'y', false) FROM t", "Служебные")

    def test_extract_function_with_from_allowed(self):
        # Defect 3: EXTRACT/SUBSTRING with FROM must not be rejected as foreign table
        self.ok('SELECT * FROM t WHERE EXTRACT(YEAR FROM "Дата") = 2025')
        self.ok('SELECT EXTRACT(EPOCH FROM ("Дата" - "Дата")) / 3600 FROM t')
        self.ok('SELECT SUBSTRING("Канал продажи" FROM 1 FOR 3) FROM t')

    def test_unknown_column_hint(self):
        self.bad('SELECT "Выручка" FROM t', 'Колонки "Выручка" нет')
        self.bad('SELECT "Выручка" FROM t', '"Цена, руб."')

    def test_column_case_fixed(self):
        self.assertEqual(self.ok('SELECT "канал продажи" FROM t'), 'SELECT "Канал продажи" FROM t')

    def test_extract_sql(self):
        self.assertEqual(extract_sql("Вот запрос:\n```sql\nSELECT 1 FROM t\n```"), "SELECT 1 FROM t")
        self.assertEqual(extract_sql("Конечно! SELECT 1 FROM t"), "SELECT 1 FROM t")
        with self.assertRaises(SQLRejected):
            extract_sql("Не знаю")


class RunnerTests(unittest.TestCase):
    def test_restore_column_aliases_issue_defect_4(self):
        from backend.llm.sql_runner import restore_column_aliases
        sql = 'SELECT COUNT(*) AS "Количество продаж без карты лояльности", "Канал" AS "Канал продажи" FROM t'
        cols = ["Количество продаж без карты лояль", "Канал продажи"]
        restored = restore_column_aliases(cols, sql)
        self.assertEqual(restored, ["Количество продаж без карты лояльности", "Канал продажи"])

    def test_caches_are_per_datasource(self):
        """Две внешние базы с одинаковой public.sales не делят значения категорий."""
        from backend.llm import sql_runner as sr

        class Info:
            def __init__(self, host, db):
                self.host, self.port, self.dbname = host, 5432, db

        class Conn:
            def __init__(self, host, db):
                self.info = Info(host, db)

        class Runner(sr.PostgresRunner):
            def _fetch(self, query, limit):
                return ["v"], [(self.conn.info.dbname,)]

        sr.clear_values_cache()
        table = {"name": "sales", "columns": [
            {"name": "Канал", "inferred_type": "category", "sample_distinct": 3}]}
        shop = Runner(Conn("10.0.0.5", "shop"), schema="public").category_values(table)
        crm = Runner(Conn("10.0.0.9", "crm"), schema="public").category_values(table)
        self.assertEqual(shop, {"Канал": ["shop"]})
        self.assertEqual(crm, {"Канал": ["crm"]})
        sr.clear_values_cache()

    def test_build_query_casts_only_safe_columns(self):
        table = {"name": "data_01_Продажи", "columns": [
            {"name": "Цена, руб.", "conversion": {"target_type": "decimal", "policy": "safe_complete"}},
            {"name": "Код", "conversion": {"target_type": None, "policy": "not_applicable"}},
            {"name": "Сумма", "conversion": {"target_type": "decimal", "policy": "blocked_invalid"}},
        ]}
        query = build_query("raw", table, "SELECT 1 FROM t", 201)
        self.assertIn(f'{cast_sql("Цена, руб.", "decimal")} AS "Цена, руб."', query)
        self.assertIn('"Код" AS "Код"', query)
        self.assertIn('"Сумма" AS "Сумма"', query)  # с ошибками формата — не приводим
        self.assertIn('FROM "raw"."data_01_Продажи")', query)
        self.assertTrue(query.endswith(") AS q LIMIT 201"))


class AnswerQualityTests(unittest.TestCase):
    ROWS = [("Мобильное приложение", Decimal("1150771281.54")), ("Магазин", Decimal("900000"))]
    COLS = ["Канал продажи", "Выручка, руб."]

    def test_bad_answers_rejected(self):
        self.assertFalse(answer_looks_good("1 Мобильное приложение 1150771281.54", self.COLS, self.ROWS))
        self.assertFalse(answer_looks_good("Больше всего выручки приносит мобильное приложение.",
                                           self.COLS, self.ROWS))  # нет ни одной цифры
        self.assertTrue(answer_looks_good("Больше всего выручки приносит мобильное приложение: "
                                          "1 150 771 281,54 руб.", self.COLS, self.ROWS))

    def test_template(self):
        self.assertEqual(template_answer(self.COLS, self.ROWS, False),
                         "Наибольшее значение «Выручка, руб.» — у «Мобильное приложение»: "
                         "1 150 771 281,54. Далее: «Магазин» — 900 000. Всего: 1 151 671 281,54.")
        avg = template_answer(["Канал", "Средняя оценка"], [("Телефон", Decimal("3.94")),
                                                            ("Чат", Decimal("3.87"))], False)
        self.assertNotIn("Всего", avg)

    def test_template_preserves_ascending_order(self):
        # Defect 5: When rows are sorted ascending, template follows table order
        rows = [("Казань", Decimal("100000")), ("Москва", Decimal("250000"))]
        res = template_answer(["Город", "Выручка, руб."], rows, False)
        self.assertIn("Наименьшее значение «Выручка, руб.» — у «Казань»: 100 000", res)
        self.assertIn("Далее: «Москва» — 250 000", res)

    def test_missing_note(self):
        table = {"columns": [{"name": "Оценка клиента", "conversion": {"quality": {
            "total_rows": 100, "missing_rows": 65}}}, {"name": "Канал", "conversion": {}}]}
        self.assertEqual(missing_note('SELECT "Канал", AVG("Оценка клиента") FROM t', table),
                         "Учтены только заполненные значения: «Оценка клиента» заполнено "
                         "только в 35% строк.")
        self.assertEqual(missing_note('SELECT "Канал" FROM t', table), "")
        self.assertEqual(template_answer(["Количество"], [(42,)], False), "Количество: 42.")


class FormatTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(format_value(Decimal("1234567.891")), "1 234 567,89")
        self.assertEqual(format_value(Decimal("2500.00")), "2 500")
        self.assertEqual(format_value(42), "42")
        self.assertEqual(format_value(dt.datetime(2025, 3, 1)), "01.03.2025")
        self.assertEqual(format_value(None), "—")

    def test_chart(self):
        bar, _ = suggest_chart(["Канал", "Выручка"], [("Онлайн", Decimal(10)), ("Магазин", Decimal(5))])
        self.assertEqual(bar["type"], "bar")
        self.assertEqual(bar["series"][0]["data"], [10.0, 5.0])
        line, _ = suggest_chart(["Месяц", "N"], [(dt.datetime(2025, 2, 1), 2), (dt.datetime(2025, 1, 1), 1)])
        self.assertEqual(line["type"], "line")
        self.assertEqual(line["categories"], ["01.01.2025", "01.02.2025"])
        self.assertIsNone(suggest_chart(["Канал"], [("a",), ("b",)])[0])

    def test_pivot_chart_and_template(self):
        rows = [("Онлайн", dt.date(2025, 1, 1), Decimal(10)), ("Онлайн", dt.date(2025, 2, 1), Decimal(12)),
                ("Магазин", dt.date(2025, 1, 1), Decimal(5)), ("Магазин", dt.date(2025, 2, 1), Decimal(30))]
        cols = ["Канал", "Месяц", "Выручка"]
        chart, note = suggest_chart(cols, rows)
        self.assertEqual(chart["type"], "line")
        self.assertEqual(chart["categories"], ["01.01.2025", "01.02.2025"])
        self.assertEqual([s["name"] for s in chart["series"]], ["Онлайн", "Магазин"])
        self.assertEqual(chart["series"][1]["data"], [5.0, 30.0])
        text = template_answer(cols, rows, False)
        self.assertIn("Максимум — «Магазин», 01.02.2025: 30", text)
        self.assertIn("Больше всего в сумме — у «Магазин»: 35", text)
        self.assertEqual(suggest_chart(cols, rows, "pie")[0]["type"], "line")

    def test_chart_views(self):
        rows = [("Онлайн", 10), ("Магазин", 5)]
        self.assertEqual(suggest_chart(["Канал", "N"], rows, "pie")[0]["type"], "pie")
        self.assertEqual(suggest_chart(["Канал", "N"], rows, "line")[0]["type"], "line")
        self.assertEqual(suggest_chart(["Канал", "N"], rows, question="Доля каналов в продажах")[0]["type"], "pie")
        self.assertIsNone(suggest_chart(["Канал", "N"], rows, "table")[0])
        many = [(f"Город {i}", i) for i in range(20)]
        chart, note = suggest_chart(["Город", "N"], many, "pie")
        self.assertEqual(chart["type"], "bar")
        self.assertIn("Круговая диаграмма подходит", note)


CATALOG = {"tables": [
    {"name": "data_01_Продажи", "columns": [
        {"name": "Код позиции", "inferred_type": "identifier", "sample_rows": 300,
         "sample_distinct": 300, "conversion": {}},
        {"name": "Код заказа", "inferred_type": "identifier", "sample_rows": 300,
         "sample_distinct": 280, "conversion": {}},
        {"name": "Канал продажи", "inferred_type": "category", "sample_distinct": 3,
         "conversion": {"policy": "not_applicable"}},
        {"name": "Тип операции", "inferred_type": "category", "sample_distinct": 2,
         "conversion": {"policy": "not_applicable"}},
        {"name": "Цена, руб.", "inferred_type": "decimal",
         "conversion": {"target_type": "decimal", "policy": "safe_complete"}},
        {"name": "Дата", "inferred_type": "date",
         "conversion": {"target_type": "date", "policy": "safe_complete"}},
        {"name": "Город", "inferred_type": "category", "sample_distinct": 30, "conversion": {}},
    ]},
    {"name": "data_02_Доставка", "columns": [
        {"name": "Перевозчик", "inferred_type": "category", "sample_distinct": 2, "conversion": {}},
        {"name": "Город", "inferred_type": "category", "sample_distinct": 30, "conversion": {}},
        {"name": "Причина отмены или возврата", "inferred_type": "category",
         "sample_distinct": 2, "conversion": {}}]},
]}

VALUES = {
    "data_01_Продажи": {"Канал продажи": ["Интернет-магазин", "Мобильное приложение"],
                        "Тип операции": ["Возврат", "Продажа"]},
    "data_02_Доставка": {"Перевозчик": ["Почта", "Курьер"],
                         "Причина отмены или возврата": ["Отказ получателя", "Повреждение"]},
}


class ScriptedClient:
    """Отвечает по очереди заготовленными ответами и запоминает запросы."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0)


class FakeRunner:
    def __init__(self, fail_first=False, rows=None, columns=None):
        self.fail_first = fail_first
        self.sqls = []
        self.rows = rows or [("Мобильное приложение", Decimal("1500000.5")),
                             ("Интернет-магазин", Decimal("900000"))]
        self.columns = columns or ["Канал продажи", "Выручка"]

    def category_values(self, table):
        return VALUES.get(table["name"], {})

    def run(self, table, sql):
        self.sqls.append(sql)
        if self.fail_first and len(self.sqls) == 1:
            raise RuntimeError("function sum(text) does not exist\nHINT: ...")
        return QueryResult(self.columns, self.rows)


GOOD_SQL = ('```sql\nSELECT "Канал продажи", SUM("Цена, руб.") AS "Выручка" FROM t '
            'GROUP BY 1 ORDER BY 2 DESC\n```')
GOOD_TEXT = "Больше всего выручки у мобильного приложения: 1 500 000,50 руб."


def ask(question, replies, runner=None, session=None):
    client = ScriptedClient(replies)
    trace = {}
    result = answer_question(question, CATALOG, runner or FakeRunner(), client=client,
                             trace=trace, session_id=session)
    return result, client, trace


class AnswerTests(unittest.TestCase):
    def setUp(self):
        _SESSIONS.clear()

    def test_full_flow(self):
        result, client, trace = ask("Какая выручка по каналам продаж?", [GOOD_SQL, GOOD_TEXT])
        self.assertEqual(trace["table"], "data_01_Продажи")
        self.assertEqual(result["answer"], GOOD_TEXT)
        self.assertEqual(result["table"]["rows"][0], ["Мобильное приложение", "1 500 000,50"])
        self.assertEqual(result["chart"]["type"], "bar")
        sql_prompt = client.calls[0][0]["content"]
        self.assertIn("'Интернет-магазин', 'Мобильное приложение'", sql_prompt)
        self.assertIn('"Цена, руб." — число', sql_prompt)
        self.assertIn('речь о колонках "Канал продажи"', sql_prompt)

    def test_table_chosen_by_category_value(self):
        # «возврат» есть и в колонке доставки, но «операций» + значение 'Возврат' — продажи
        result, client, trace = ask(
            "Сколько было операций возврата?",
            ['```sql\nSELECT COUNT(*) FROM t WHERE "Тип операции" = \'Возврат\'\n```', "x"],
            FakeRunner(rows=[(5819,)], columns=["Количество"]))
        self.assertEqual(trace["table"], "data_01_Продажи")
        self.assertEqual(result["answer"], "Количество: 5 819.")

    def test_repairs_after_bad_column_and_db_error(self):
        runner = FakeRunner(fail_first=True)
        result, client, trace = ask("Какая выручка по каналам продаж?", [
            '```sql\nSELECT "Выручка" FROM t\n```',   # несуществующая колонка
            GOOD_SQL,                                 # база вернёт ошибку
            GOOD_SQL,
            "Ответ.",
        ], runner)
        self.assertEqual(len(runner.sqls), 2)
        self.assertIn('Колонки "Выручка" нет', client.calls[0][-1]["content"]
                      if len(client.calls[0]) > 2 else client.calls[1][-1]["content"])
        self.assertIn("Ошибка PostgreSQL: function sum(text) does not exist", client.calls[2][-1]["content"])
        self.assertNotIn("HINT", client.calls[2][-1]["content"])
        self.assertTrue(result["answer"].startswith("Наибольшее значение"))  # «Ответ.» -> шаблон
        self.assertEqual(trace["attempts"], 3)

    def test_count_question_rejects_row_listing(self):
        _, client, trace = ask("Сколько строк продаж через мобильное приложение?", [
            '```sql\nSELECT * FROM t WHERE "Канал продажи" = \'Мобильное приложение\' LIMIT 10\n```',
            '```sql\nSELECT COUNT(*) FROM t WHERE "Канал продажи" = \'Мобильное приложение\'\n```',
            "x"], FakeRunner(rows=[(214505,)], columns=["Количество"]))
        self.assertIn("нужен COUNT", trace["errors"][0])
        self.assertEqual(trace["attempts"], 2)

    def test_invented_filter_removed(self):
        runner = FakeRunner()
        _, client, trace = ask("Какая выручка по каналам продаж?", [
            '```sql\nSELECT "Канал продажи", SUM("Цена, руб.") FROM t '
            'WHERE "Канал продажи" IN (\'Интернет-магазин\') AND "Тип операции" = \'Продажа\' '
            'GROUP BY 1\n```', GOOD_TEXT], runner)
        self.assertEqual(trace["errors"], [])
        self.assertEqual(len(trace["removed_filters"]), 2)
        self.assertNotIn("WHERE", runner.sqls[0])

    def test_quoted_value_is_kept(self):
        runner = FakeRunner(rows=[(494181,)], columns=["N"])
        _, _, trace = ask("Сколько операций «Продажа»?", [
            '```sql\nSELECT COUNT(*) FROM t WHERE "Тип операции" = \'Продажа\'\n```', "x"], runner)
        self.assertIn("WHERE", runner.sqls[0])

    def test_unknown_value_rejected(self):
        _, client, trace = ask("Сколько строк продаж в статусе фоновых?", [
            '```sql\nSELECT COUNT(*) FROM t WHERE "Тип операции" LIKE \'%фонов%\'\n```',
            '```sql\nSELECT COUNT(*) FROM t\n```', "x"],
            FakeRunner(rows=[(5,)], columns=["N"]))
        self.assertIn("нет значения '%фонов%'", trace["errors"][0])

    def test_limit_removed_when_not_top(self):
        runner = FakeRunner()
        ask("Какая выручка по каналам продаж?", [
            GOOD_SQL.replace("DESC\n```", "DESC LIMIT 10\n```"), GOOD_TEXT], runner)
        self.assertNotIn("LIMIT", runner.sqls[0])

    def test_gives_up_without_numbers(self):
        result, _, _ = ask("Какая выручка по каналам продаж?",
                           ["DELETE FROM t", "DELETE FROM t", "DELETE FROM t"])
        self.assertIn("цифр не показываю", result["answer"])
        self.assertNotIn("table", result)

    def test_off_topic_and_write_requests_refused(self):
        result, client, _ = ask("Какая завтра погода в Москве?", [])
        self.assertIn("не похож на вопрос к загруженным данным", result["answer"])
        result, client, _ = ask("Удали все продажи", [])
        self.assertIn("только на чтение", result["answer"])
        from backend.llm.ask import WRITE_INTENT
        self.assertIsNone(WRITE_INTENT.search("Как изменилась выручка по сравнению с 2024?"))
        self.assertTrue(WRITE_INTENT.search("Измени цену товара"))
        self.assertEqual(client.calls, [])

    def test_tie_asks_which_table_then_uses_answer(self):
        result, _, trace = ask("Сколько записей по городам?", [], session="s1")
        self.assertEqual(trace["clarification"], "table")
        self.assertEqual(result["clarification"]["options"], ["Продажи", "Доставка"])
        result, client, trace = ask("Доставка", [
            '```sql\nSELECT "Город", COUNT(*) FROM t GROUP BY 1\n```', "x"],
            FakeRunner(rows=[("Москва", 10), ("Пермь", 5)], columns=["Город", "N"]), session="s1")
        self.assertEqual(trace["table"], "data_02_Доставка")
        self.assertIn("Вопрос: Сколько записей по городам?", client.calls[0][1]["content"])

    def test_returns_ambiguous_between_tables(self):
        result, _, trace = ask("Сколько возвратов?", [])
        self.assertEqual(trace["clarification"], "table")
        self.assertEqual(result["clarification"]["options"], ["Продажи", "Доставка"])

    def test_returns_operations_go_to_sales(self):
        _, _, trace = ask("Сколько было операций возврата?", [
            '```sql\nSELECT COUNT(*) FROM t WHERE "Тип операции" = \'Возврат\'\n```', "x"],
            FakeRunner(rows=[(5819,)], columns=["N"]))
        self.assertEqual(trace["table"], "data_01_Продажи")

    def test_no_unit_question_when_row_is_the_entity(self):
        # «Сколько доставок»: нет уникального кода, отличного от сущности — уточнять нечего
        from backend.llm.ask import unit_options
        from backend.llm.semantics import Intent
        table = {"name": "data_05_События", "columns": [
            {"name": "Код события", "inferred_type": "identifier", "sample_rows": 300, "sample_distinct": 300},
            {"name": "Код сессии", "inferred_type": "identifier", "sample_rows": 300, "sample_distinct": 245}]}
        q = "Сколько событий на каждой платформе?"
        self.assertEqual(unit_options(q, Intent.parse(q), table), [])

    def test_unit_clarification(self):
        result, _, trace = ask("Сколько продаж через интернет-магазин?", [], session="s2")
        self.assertEqual(trace["clarification"], "unit")
        self.assertEqual(result["clarification"]["options"],
                         ["Количество строк (записей)", "Количество уникальных: Код заказа"])
        _, client, trace = ask("Количество уникальных: Код заказа", [
            '```sql\nSELECT COUNT(DISTINCT "Код заказа") FROM t WHERE "Канал продажи" = '
            '\'Интернет-магазин\'\n```', "x"],
            FakeRunner(rows=[(60138,)], columns=["N"]), session="s2")
        self.assertIn("уникальных «Код заказа»", client.calls[0][1]["content"])
        self.assertEqual(trace["errors"], [])

    def test_followup_keeps_previous_query(self):
        ask("Какая выручка по каналам продаж?", [GOOD_SQL, GOOD_TEXT], session="s3")
        _, client, trace = ask("А теперь только мобильное приложение", [
            '```sql\nSELECT "Канал продажи", SUM("Цена, руб.") FROM t WHERE "Канал продажи" = '
            '\'Мобильное приложение\' GROUP BY 1\n```', GOOD_TEXT], session="s3")
        messages = client.calls[0]
        self.assertEqual(messages[1]["content"], "Вопрос: Какая выручка по каналам продаж?")
        self.assertIn('SUM("Цена, руб.")', messages[2]["content"])
        self.assertIn("Уточнение к предыдущему вопросу: А теперь только мобильное", messages[3]["content"])
        self.assertEqual(trace["errors"], [])

    def test_pure_view_switch_reuses_last_sql_without_model(self):
        runner = FakeRunner()
        ask("Какая выручка по каналам продаж?", [GOOD_SQL, GOOD_TEXT], runner, session="v1")
        result, client, trace = ask("Покажи то же самое кругом", [], runner, session="v1")
        self.assertEqual(client.calls, [])
        self.assertEqual(result["chart"]["type"], "pie")
        self.assertEqual(result["answer"], "Вот тот же результат круговой диаграммой.")
        self.assertEqual(runner.sqls[0], runner.sqls[1])

    def test_view_without_history(self):
        result, client, _ = ask("Покажи кругом", [], session="v2")
        self.assertIn("Сначала задайте вопрос", result["answer"])

    def test_followup_with_new_condition_and_view(self):
        runner = FakeRunner()
        ask("Какая выручка по каналам продаж?", [GOOD_SQL, GOOD_TEXT], runner, session="v3")
        month_sql = ('```sql\nSELECT date_trunc(\'month\', "Дата") AS "Месяц", SUM("Цена, руб.") '
                     'FROM t GROUP BY 1 ORDER BY 1\n```')
        runner.rows = [(dt.datetime(2025, 1, 1), Decimal(5)), (dt.datetime(2025, 2, 1), Decimal(7))]
        runner.columns = ["Месяц", "Выручка"]
        result, client, trace = ask("По месяцам столбцами", [month_sql, "x"], runner, session="v3")
        self.assertIn("Уточнение к предыдущему вопросу: По месяцам столбцами", client.calls[0][3]["content"])
        self.assertEqual(result["chart"]["type"], "bar")  # просили столбцы, хотя это время

    def test_short_refinement_vs_new_question(self):
        runner = FakeRunner()
        ask("Какая выручка по каналам продаж?", [GOOD_SQL, GOOD_TEXT], runner, session="r1")
        filtered = ('```sql\nSELECT "Канал продажи", SUM("Цена, руб.") FROM t WHERE "Канал продажи" = '
                    '\'Мобильное приложение\' GROUP BY 1\n```')
        _, client, trace = ask("Только мобильное приложение", [filtered, GOOD_TEXT], runner, session="r1")
        self.assertEqual(trace["errors"], [])
        self.assertIn("Уточнение к предыдущему вопросу", client.calls[0][3]["content"])
        # после вопроса о доставке короткий вопрос о продажах — новый вопрос, а не продолжение
        ask("Сколько записей по перевозчикам?", ['```sql\nSELECT "Перевозчик", COUNT(*) FROM t GROUP BY 1\n```',
                                                "x"], runner, session="r2")
        _, client, trace = ask("Выручка по каналам продаж", [GOOD_SQL, GOOD_TEXT], runner, session="r2")
        self.assertEqual(trace["table"], "data_01_Продажи")
        self.assertEqual(client.calls[0][1]["content"], "Вопрос: Выручка по каналам продаж")

    def test_off_topic_after_analytic_question_is_refused(self):
        runner = FakeRunner()
        ask("Какая выручка по каналам продаж?", [GOOD_SQL, GOOD_TEXT], runner, session="w1")
        result, client, trace = ask("Какая погода в Москве?", [], runner, session="w1")
        self.assertEqual(client.calls, [])
        self.assertNotIn("table", result)
        self.assertIn("не похож на вопрос к загруженным данным", result["answer"])

    def test_no_session_no_memory(self):
        ask("Какая выручка по каналам продаж?", [GOOD_SQL, GOOD_TEXT])
        result, _, _ = ask("А теперь только мобильное приложение", [GOOD_SQL, GOOD_TEXT])
        self.assertNotIn("clarification", result)

    def test_answer_question_with_active_table_name_scopes_query_and_sql(self):
        runner = FakeRunner(columns=["Перевозчик", "Количество"], rows=[("Курьер", 50)])
        delivery_sql = '```sql\nSELECT "Перевозчик", COUNT(*) AS "Количество" FROM t GROUP BY 1\n```'
        client = ScriptedClient([delivery_sql, "Больше всего доставок курьером: 50."])
        trace = {}
        result = answer_question(
            "Сколько записей?",
            CATALOG,
            runner,
            client=client,
            trace=trace,
            table_name="data_02_Доставка",
        )
        self.assertEqual(trace["table"], "data_02_Доставка")
        # LLM SQL prompt must only see data_02_Доставка columns
        sql_prompt = client.calls[0][0]["content"]
        self.assertIn('"Перевозчик"', sql_prompt)
        self.assertNotIn('"Канал продажи"', sql_prompt)
        self.assertEqual(result["table"]["rows"][0], ["Курьер", "50"])

    def test_answer_question_with_invalid_table_name_returns_polite_message(self):
        runner = FakeRunner()
        client = ScriptedClient([])
        result = answer_question(
            "Сколько записей?",
            CATALOG,
            runner,
            client=client,
            table_name="unknown_table",
        )
        self.assertIn("не найдена в каталоге данных", result["answer"])
        self.assertIn("«Продажи»", result["answer"])
        self.assertIn("«Доставка»", result["answer"])
        self.assertIsNone(result["sql"])
        self.assertEqual(len(client.calls), 0)

    def test_answer_question_with_value_query_scoped_table(self):
        """Issue #40: value-based query with scoped table must not be rejected as unanalytic."""
        runner = FakeRunner(columns=["Канал продажи", "Цена, руб."], rows=[("Мобильное приложение", 1500)])
        sql = '```sql\nSELECT "Канал продажи", SUM("Цена, руб.") FROM t WHERE "Канал продажи" = \'Мобильное приложение\' GROUP BY 1\n```'
        client = ScriptedClient([sql, "Выручка в мобильном приложении: 1 500 руб."])
        trace = {}
        result = answer_question(
            "Какая ситуация в мобильном приложении?",
            CATALOG,
            runner,
            client=client,
            trace=trace,
            table_name="data_01_Продажи",
        )
        self.assertEqual(trace["table"], "data_01_Продажи")
        self.assertIsNotNone(result["sql"])
        self.assertIn("1 500", result["answer"])

    def test_answer_question_multi_turn_with_table_scope(self):
        """Issue #40: multi-turn follow-ups stay scoped to table, but switching table resets previous."""
        runner = FakeRunner()
        # Turn 1 on data_01_Продажи
        sql1 = '```sql\nSELECT "Канал продажи", SUM("Цена, руб.") FROM t GROUP BY 1\n```'
        client1 = ScriptedClient([sql1, "Выручка по каналам."])
        res1 = answer_question(
            "Какая выручка по каналам продаж?",
            CATALOG,
            runner,
            client=client1,
            session_id="multi-turn-scope",
            table_name="data_01_Продажи",
        )
        self.assertIsNotNone(res1["sql"])

        # Turn 2: Followup on same table
        sql2 = '```sql\nSELECT "Канал продажи", SUM("Цена, руб.") FROM t WHERE "Канал продажи" = \'Мобильное приложение\' GROUP BY 1\n```'
        client2 = ScriptedClient([sql2, "Только мобильное приложение."])
        trace2 = {}
        res2 = answer_question(
            "А теперь только мобильное приложение",
            CATALOG,
            runner,
            client=client2,
            trace=trace2,
            session_id="multi-turn-scope",
            table_name="data_01_Продажи",
        )
        self.assertIsNotNone(res2["sql"])
        # Should have referenced previous query in prompt
        self.assertTrue(any("Уточнение к предыдущему вопросу" in m["content"] for m in client2.calls[0]))

        # Turn 3: Switch table to data_02_Доставка -> previous table query must not be reused
        sql3 = '```sql\nSELECT "Перевозчик", COUNT(*) FROM t GROUP BY 1\n```'
        client3 = ScriptedClient([sql3, "Доставки по перевозчикам."])
        res3 = answer_question(
            "Сколько записей по перевозчикам?",
            CATALOG,
            runner,
            client=client3,
            session_id="multi-turn-scope",
            table_name="data_02_Доставка",
        )
        self.assertIsNotNone(res3["sql"])
        # Must not contain previous "Продажи" query as previous
        self.assertFalse(any("Какая выручка по каналам продаж?" in m["content"] for m in client3.calls[0]))

    def test_filter_catalog_for_scope_helper(self):
        try:
            from backend.app.services.agent import filter_catalog_for_scope
        except ImportError:
            from app.services.agent import filter_catalog_for_scope

        cat = {
            "schema": "raw",
            "summary": {"tables": 2, "columns": 10},
            "tables": [
                {"name": "data_01_Продажи", "columns": [{"name": "Код позиции"}]},
                {"name": "data_02_Доставка", "columns": [{"name": "Код доставки"}]},
            ],
        }
        rel = {
            "join_candidates": [
                {
                    "source": {"table": "data_01_Продажи", "column": "Код исходной позиции"},
                    "target": {"table": "data_01_Продажи", "column": "Код позиции"},
                },
                {
                    "source": {"table": "data_01_Продажи", "column": "Код доставки"},
                    "target": {"table": "data_02_Доставка", "column": "Код доставки"},
                },
            ]
        }

        # 1. Scope by display name
        filt_cat, filt_rel, err = filter_catalog_for_scope(cat, rel, table_name="Продажи")
        self.assertIsNone(err)
        self.assertEqual(len(filt_cat["tables"]), 1)
        self.assertEqual(filt_cat["tables"][0]["name"], "data_01_Продажи")
        self.assertEqual(filt_cat["summary"]["tables"], 1)
        # Cross-table join must be stripped, only intra-table self-ref kept
        self.assertEqual(len(filt_rel["join_candidates"]), 1)
        self.assertEqual(filt_rel["join_candidates"][0]["target"]["table"], "data_01_Продажи")

        # 2. Scope by raw name
        filt_cat2, _, err2 = filter_catalog_for_scope(cat, rel, table_name="data_02_Доставка")
        self.assertIsNone(err2)
        self.assertEqual(filt_cat2["tables"][0]["name"], "data_02_Доставка")

        # 3. Invalid table name
        _, _, err3 = filter_catalog_for_scope(cat, rel, table_name="несуществующая")
        self.assertIsNotNone(err3)
        self.assertIn("не найдена в каталоге данных", err3)

        # 4. Scope omitted (backward compatibility)
        full_cat, full_rel, err4 = filter_catalog_for_scope(cat, rel)
        self.assertIsNone(err4)
        self.assertEqual(len(full_cat["tables"]), 2)
        self.assertEqual(len(full_rel["join_candidates"]), 2)

        # 5. Invalid datasource_id even when table_name is given
        _, _, err5 = filter_catalog_for_scope(
            cat, rel, table_name="data_01_Продажи", datasource_id="invalid_ds"
        )
        self.assertIsNotNone(err5)
        self.assertIn("не найден в каталоге данных", err5)

        # 6. Conflicting datasource_id and table_name
        _, _, err6 = filter_catalog_for_scope(
            cat, rel, table_name="data_01_Продажи", datasource_id="data_02_Доставка"
        )
        self.assertIsNotNone(err6)
        self.assertIn("не принадлежит источнику данных", err6)

        # 7. Common prefix 'data' must NOT match data_01_Продажи
        _, _, err7 = filter_catalog_for_scope(cat, rel, table_name="data")
        self.assertIsNotNone(err7)
        self.assertIn("не найдена в каталоге данных", err7)


CATALOG_FILE = Path(__file__).resolve().parents[2] / "data" / "schema-catalog.json"
VALUES_FILE = CATALOG_FILE.with_name("category-values.json")


class E2EFindingsTests(unittest.TestCase):
    """Регрессии по E2E 29.10.2026: #96, #98, #102, #104."""

    def setUp(self):
        _SESSIONS.clear()

    def test_share_after_grouping_drops_previous_sql(self):
        """#96: «А какая доля … на мобильное приложение?» после разбивки — одно число."""
        runner = FakeRunner(rows=[("Мобильное приложение", 10), ("Интернет-магазин", 5)],
                            columns=["Канал продажи", "Количество"])
        grouped = '```sql\nSELECT "Канал продажи", COUNT(*) FROM t GROUP BY 1\n```'
        ask("Сколько записей о продажах по каждому каналу продажи?", [grouped, "x"], runner, session="s")
        share = ('```sql\nSELECT 100.0 * COUNT(*) FILTER (WHERE "Канал продажи" = \'Мобильное приложение\') '
                 '/ NULLIF(COUNT(*), 0) AS "Доля, %" FROM t\n```')
        runner.rows, runner.columns = [(66.67,)], ["Доля, %"]
        result, client, trace = ask("А какая доля продаж приходится на мобильное приложение?",
                                    [share, "x"], runner, session="s")
        self.assertEqual(len(client.calls[0]), 2)  # без прошлого SQL с GROUP BY
        self.assertEqual(trace["errors"], [])
        self.assertEqual(trace["table"], "data_01_Продажи")

    def test_explicit_other_table_breaks_followup(self):
        """#102: «А сколько всего обращений в таблице demo_support?» — новая таблица."""
        from backend.llm.ask import explicit_table, points_elsewhere
        tables = [{"name": "demo_orders", "columns": [{"name": "order_id", "inferred_type": "identifier"}]},
                  {"name": "demo_support", "columns": [{"name": "ticket_id", "inferred_type": "identifier"}]}]
        q = "А сколько всего обращений в таблице demo_support?"
        self.assertEqual(explicit_table(q, tables)["name"], "demo_support")
        self.assertTrue(points_elsewhere(q, tables, {}, "demo_orders", "public"))
        self.assertFalse(points_elsewhere("А по месяцам?", tables, {}, "demo_orders", "public"))
        self.assertFalse(points_elsewhere("А сколько в таблице demo_orders за 2025?", tables, {},
                                          "demo_orders", "public"))
        catalog = {"schema": "public", "tables": tables}
        runner = FakeRunner(rows=[(180,)], columns=["Количество"])
        ask_ = lambda q, sql, s: answer_question(q, catalog, runner, client=ScriptedClient([sql, "x"]),
                                                 trace=(t := {}), session_id=s) and t
        t1 = ask_("Сколько всего заказов в таблице demo_orders?",
                  '```sql\nSELECT COUNT(*) AS "Количество" FROM t\n```', "d")
        self.assertEqual(t1["table"], "demo_orders")
        runner.rows = [(80,)]
        t2 = ask_(q, '```sql\nSELECT COUNT(*) AS "Количество" FROM t\n```', "d")
        self.assertEqual(t2["table"], "demo_support")
        self.assertEqual(t2["errors"], [])

    @unittest.skipUnless(CATALOG_FILE.is_file() and VALUES_FILE.is_file(), "нет каталога")
    def test_domain_switch_not_forced(self):
        """#98: после вопросов о техподдержке «Сравни выручку с учётом скидок…» — продажи."""
        from backend.llm.ask import choose_table, points_elsewhere
        tables = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))["tables"]
        values = json.loads(VALUES_FILE.read_text(encoding="utf-8"))
        q = ("Сравни выручку с учётом скидок за август и сентябрь 2025 года. "
             "Покажи абсолютное и процентное изменение.")
        support = "data_02_Техническая_поддержка"
        self.assertTrue(points_elsewhere(q, tables, values, support, "raw"))
        self.assertEqual(choose_table(q, tables, values, "raw")[0]["name"], "data_01_Продажи")
        for keep in ["Сравни с предыдущим месяцем", "А по месяцам?", "Сравни Москву и Казань",
                     "А только за 2025 год?"]:
            self.assertFalse(points_elsewhere(keep, tables, values, "data_01_Продажи", "raw"), keep)

    def test_revenue_not_counted_on_table_without_money(self):
        """#98: «выручка» по таблице без денег — уточнение, а не SUM часов."""
        catalog = {"tables": [
            {"name": "data_02_Поддержка", "columns": [
                {"name": "Норматив решения, ч", "inferred_type": "integer",
                 "conversion": {"target_type": "integer", "policy": "safe_complete"}}]},
            {"name": "data_01_Продажи", "columns": [
                {"name": "Цена, руб.", "inferred_type": "decimal",
                 "conversion": {"target_type": "decimal", "policy": "safe_complete"}}]}]}
        scoped = answer_question("Какая выручка?", catalog, FakeRunner(), client=ScriptedClient([]),
                                 trace={}, table_name="data_02_Поддержка")
        self.assertIn("нет денежных показателей", scoped["answer"])
        self.assertIsNone(scoped.get("table"))
        # пользователь сам выбрал таблицу без денег в ответ на уточнение — предлагаем подходящие
        _SESSIONS["m"] = {"pending": {"kind": "table", "question": "Какая выручка?",
                                      "options": ["Поддержка", "Продажи"],
                                      "tables": ["data_02_Поддержка", "data_01_Продажи"]}}
        trace = {}
        result = answer_question("Поддержка", catalog, FakeRunner(), client=ScriptedClient([]),
                                 trace=trace, session_id="m")
        self.assertEqual(result["clarification"]["options"], ["Продажи"])
        self.assertIn("нет денежных показателей", result["answer"])

    def test_multi_metric_text(self):
        """#104: текст про каждую метрику, валовая прибыль названа валовой."""
        import datetime as dt
        from backend.llm.ask import multi_metric_answer, profit_note
        rows = [(dt.date(2025, 11, 1), 150.0, 40.0), (dt.date(2025, 12, 1), 200.0, 30.0)]
        text = multi_metric_answer(["Месяц", "Выручка, руб.", "Прибыль, руб."], rows, False)
        self.assertIn("«Выручка, руб.»: максимум 200 (01.12.2025)", text)
        self.assertIn("«Прибыль, руб.»: максимум 40 (01.11.2025)", text)
        self.assertIsNone(multi_metric_answer(["Месяц", "Выручка"], [r[:2] for r in rows], False))
        note = profit_note(["Месяц", "Прибыль, руб."],
                           'SELECT SUM(x - "Количество, шт." * "Себестоимость единицы, руб.") FROM t')
        self.assertIn("валовая", note)
        self.assertEqual(profit_note(["Месяц", "Выручка"], "SELECT 1"), "")


if __name__ == "__main__":
    unittest.main()
