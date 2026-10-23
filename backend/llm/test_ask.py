"""Тесты сценария 2 без модели и без БД:
    python -m unittest discover -s backend -p 'test_*.py' -t .
"""
import datetime as dt
import unittest
from decimal import Decimal

from backend.llm.ask import (answer_looks_good, answer_question, format_value,
                             missing_note, suggest_chart, template_answer, widen_top1)
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

    def test_missing_note(self):
        table = {"columns": [{"name": "Оценка клиента", "conversion": {"quality": {
            "total_rows": 100, "missing_rows": 65}}}, {"name": "Канал", "conversion": {}}]}
        self.assertEqual(missing_note('SELECT "Канал", AVG("Оценка клиента") FROM t', table),
                         "Учтены только заполненные значения: «Оценка клиента» заполнено "
                         "только в 35% строк.")
        self.assertEqual(missing_note('SELECT "Канал" FROM t', table), "")
        self.assertEqual(template_answer(["Количество"], [(42,)], False), "Количество: 42.")

    def test_widen_top1(self):
        self.assertTrue(widen_top1("SELECT a, SUM(b) FROM t GROUP BY a ORDER BY 2 DESC LIMIT 1")
                        .endswith("LIMIT 10"))
        self.assertTrue(widen_top1("SELECT a FROM t ORDER BY a LIMIT 1").endswith("LIMIT 1"))


class FormatTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(format_value(Decimal("1234567.891")), "1 234 567,89")
        self.assertEqual(format_value(Decimal("2500.00")), "2 500")
        self.assertEqual(format_value(42), "42")
        self.assertEqual(format_value(dt.datetime(2025, 3, 1)), "01.03.2025")
        self.assertEqual(format_value(None), "—")

    def test_chart(self):
        bar = suggest_chart(["Канал", "Выручка"], [("Онлайн", Decimal(10)), ("Магазин", Decimal(5))])
        self.assertEqual(bar["type"], "bar")
        self.assertEqual(bar["series"][0]["data"], [10.0, 5.0])
        line = suggest_chart(["Месяц", "N"], [(dt.datetime(2025, 2, 1), 2), (dt.datetime(2025, 1, 1), 1)])
        self.assertEqual(line["type"], "line")
        self.assertEqual(line["categories"], ["01.01.2025", "01.02.2025"])
        self.assertIsNone(suggest_chart(["Канал"], [("a",), ("b",)]))


CATALOG = {"tables": [
    {"name": "data_01_Продажи", "columns": [
        {"name": "Канал продажи", "inferred_type": "category", "sample_distinct": 3,
         "conversion": {"policy": "not_applicable"}},
        {"name": "Цена, руб.", "inferred_type": "decimal",
         "conversion": {"target_type": "decimal", "policy": "safe_complete"}},
    ]},
    {"name": "data_02_Доставка", "columns": [
        {"name": "Перевозчик", "inferred_type": "category", "conversion": {}}]},
]}


class ScriptedClient:
    """Отвечает по очереди заготовленными ответами и запоминает запросы."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0)


class FakeRunner:
    def __init__(self, fail_first=False):
        self.fail_first = fail_first
        self.sqls = []

    def category_values(self, table):
        return {"Канал продажи": ["Магазин", "Онлайн"]}

    def run(self, table, sql):
        self.sqls.append(sql)
        if self.fail_first and len(self.sqls) == 1:
            raise RuntimeError("function sum(text) does not exist\nHINT: ...")
        return QueryResult(["Канал продажи", "Выручка"],
                           [("Онлайн", Decimal("1500000.5")), ("Магазин", Decimal("900000"))])


GOOD_SQL = ('```sql\nSELECT "Канал продажи", SUM("Цена, руб.") AS "Выручка" FROM t '
            'GROUP BY 1 ORDER BY 2 DESC\n```')


class AnswerTests(unittest.TestCase):
    def test_full_flow(self):
        good = "Больше всего выручки у онлайн-канала: 1 500 000,50 руб."
        client = ScriptedClient(["1", GOOD_SQL, good])
        result = answer_question("Какой канал лучше?", CATALOG, FakeRunner(), client=client)
        self.assertEqual(result["answer"], good)
        self.assertEqual(result["table"]["rows"][0], ["Онлайн", "1 500 000,50"])
        self.assertEqual(result["chart"]["type"], "bar")
        self.assertIn("FROM t", result["sql"])
        sql_prompt = client.calls[1][0]["content"]
        self.assertIn("'Магазин', 'Онлайн'", sql_prompt)
        self.assertIn('"Цена, руб." — число', sql_prompt)
        self.assertIn("1 500 000,50", client.calls[2][1]["content"])

    def test_hint_and_answer_prefix(self):
        client = ScriptedClient(["1", GOOD_SQL,
                                 "Прямой ответ: лучше всего продаёт онлайн-канал, выручка 1 500 000,50 руб."])
        result = answer_question("Какой канал продаж лучше?", CATALOG, FakeRunner(), client=client)
        self.assertIn('речь о колонках "Канал продажи"', client.calls[1][0]["content"])
        self.assertEqual(result["answer"],
                         "лучше всего продаёт онлайн-канал, выручка 1 500 000,50 руб.")

    def test_repairs_after_bad_column_and_db_error(self):
        client = ScriptedClient([
            "1",
            '```sql\nSELECT "Выручка" FROM t\n```',   # несуществующая колонка
            GOOD_SQL,                                 # база вернёт ошибку
            GOOD_SQL,
            "Ответ.",
        ])
        runner = FakeRunner(fail_first=True)
        result = answer_question("Вопрос", CATALOG, runner, client=client)
        self.assertTrue(result["answer"].startswith("Наибольшее значение"))  # «Ответ.» -> шаблон
        self.assertEqual(len(runner.sqls), 2)
        self.assertEqual(client.calls[2][1]["content"], "Вопрос: Вопрос")
        self.assertIn('Колонки "Выручка" нет', client.calls[2][-1]["content"])
        repair = client.calls[3][-1]["content"]
        self.assertIn("Ошибка PostgreSQL: function sum(text) does not exist", repair)
        self.assertNotIn("HINT", repair)

    def test_gives_up_after_three_attempts(self):
        client = ScriptedClient(["1", "DELETE FROM t", "DELETE FROM t", "DELETE FROM t"])
        result = answer_question("Удали всё", CATALOG, FakeRunner(), client=client)
        self.assertIn("Не получилось", result["answer"])

    def test_keywords_rescue_bad_pick(self):
        client = ScriptedClient(["0", GOOD_SQL, "Ответ."])
        runner = FakeRunner()
        answer_question("Какой канал продаж лучше?", CATALOG, runner, client=client)
        self.assertEqual(len(runner.sqls), 1)
        self.assertIn('"Канал продажи"', client.calls[1][0]["content"])

    def test_keywords_override_model_miss(self):
        client = ScriptedClient(["2", GOOD_SQL, "Ответ."])
        answer_question("Выручка по каналам продаж", CATALOG, FakeRunner(), client=client)
        self.assertIn('"Канал продажи"', client.calls[1][0]["content"])

    def test_no_table_asks_to_clarify(self):
        client = ScriptedClient(["0"])
        result = answer_question("Какая погода?", CATALOG, FakeRunner(), client=client)
        self.assertIn("clarification", result)
        self.assertEqual(result["clarification"]["options"], ["Продажи", "Доставка"])


if __name__ == "__main__":
    unittest.main()
