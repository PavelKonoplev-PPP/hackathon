"""Тесты What-if (#56) и прогноза (сценарий 10) — без модели и БД."""
import datetime as dt
import unittest
from decimal import Decimal

from backend.llm.ask import _SESSIONS
from backend.llm.sql_runner import QueryResult
from backend.llm.test_ask import FakeRunner, ScriptedClient, ask
from backend.llm.whatif import (Change, base_question, combine, driver_column, forecast_steps,
                                is_forecast, is_whatif, parse_change, scenario_sql, split_question)

SALES = ["Канал продажи", "Количество, шт.", "Цена до скидки за единицу, руб.", "Скидка, %"]
NUMERIC = SALES[1:]


class ParseTests(unittest.TestCase):
    def test_change(self):
        cases = {
            "если скидка вырастет на 5 п.п.": ("pp", 5.0),
            "если скидка снизится на 2 процентных пункта": ("pp", -2.0),
            "что если поднять цены на 10%": ("pct", 10.0),
            "если количество продаж упадёт на 20%": ("pct", -20.0),
            "при снижении цен на 7 процентов": ("pct", -7.0),
            "что если удвоить скидку": ("times", 2.0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                c = parse_change(text)
                self.assertEqual((c.kind, c.value), expected)
        self.assertIsNone(parse_change("Какая выручка за прошлый год?"))

    def test_detection(self):
        self.assertTrue(is_whatif("Что будет с выручкой, если скидка вырастет на 5 п.п.?"))
        self.assertFalse(is_whatif("Что будет с выручкой?"))
        self.assertTrue(is_forecast("Какой прогноз выручки на следующий квартал?"))
        self.assertFalse(is_forecast("Что будет с выручкой, если цены вырастут на 10%?"))
        self.assertEqual(forecast_steps("прогноз на следующий год"), 12)
        self.assertEqual(forecast_steps("на следующий квартал"), 3)

    def test_split_and_base(self):
        q = "Что будет с выручкой по каналам, если скидка вырастет на 5 п.п.?"
        self.assertEqual(split_question(q), ("выручкой по каналам", "скидка вырастет на 5 п.п."))
        self.assertEqual(base_question(q, SALES), "Посчитай: выручкой по каналам с учётом скидки")
        self.assertEqual(base_question("Что если поднять цены на 10%?", SALES),
                         "Посчитай: выручка с учётом скидки")

    def test_driver(self):
        self.assertEqual(driver_column("скидка вырастет на 5 п.п.", NUMERIC), "Скидка, %")
        self.assertEqual(driver_column("поднять цены на 10%", NUMERIC), "Цена до скидки за единицу, руб.")
        self.assertEqual(driver_column("количество продаж упадёт", NUMERIC), "Количество, шт.")
        self.assertIsNone(driver_column("погода улучшится", NUMERIC))


class ScenarioSqlTests(unittest.TestCase):
    SQL = ('SELECT "Канал продажи", SUM("Количество, шт." * "Цена до скидки за единицу, руб." '
           '* (1 - "Скидка, %" / 100.0)) AS "Выручка" FROM t WHERE "Скидка, %" > 0 GROUP BY 1')

    def test_pp_only_in_select(self):
        new = scenario_sql(self.SQL, "Скидка, %", Change("pp", 5, ""))
        self.assertIn('(1 - LEAST(100, GREATEST(0, "Скидка, %" + (5)))', new)
        self.assertIn('WHERE "Скидка, %" > 0', new)  # условие не тронуто

    def test_pct_and_missing_driver(self):
        new = scenario_sql(self.SQL, "Цена до скидки за единицу, руб.", Change("pct", -10, ""))
        self.assertIn('("Цена до скидки за единицу, руб." * 0.9)', new)
        self.assertIsNone(scenario_sql(self.SQL, "Расходы, руб.", Change("pct", 10, "")))

    def test_combine(self):
        cols, rows, metric = combine(["Канал", "Выручка"], [("A", 100), ("B", 50)],
                                     [("A", 90), ("B", 45)], Change("pp", 5, ""))
        self.assertEqual(cols, ["Канал", "Сейчас", "При сценарии", "Изменение", "Изменение, %"])
        self.assertEqual(rows[0], ("A", 100.0, 90.0, -10.0, -10.0))
        _, rows, _ = combine(["Выручка"], [(200,)], None, Change("pct", 15, ""))
        self.assertAlmostEqual(rows[0][1], 230.0)


class ScenarioRunner(FakeRunner):
    """База — одни числа, запрос со сценарием (LEAST/×) — другие."""

    def run(self, table, sql):
        self.sqls.append(sql)
        if "LEAST(" in sql or "* 0.9)" in sql:
            return QueryResult(["Канал продажи", "Выручка"],
                               [("Интернет-магазин", Decimal(90)), ("Мобильное приложение", Decimal(180))])
        return QueryResult(["Канал продажи", "Выручка"],
                           [("Интернет-магазин", Decimal(100)), ("Мобильное приложение", Decimal(200))])


WHATIF_CATALOG_EXTRA = [
    {"name": "Количество, шт.", "inferred_type": "integer",
     "conversion": {"target_type": "integer", "policy": "safe_complete"}},
    {"name": "Цена до скидки за единицу, руб.", "inferred_type": "decimal",
     "conversion": {"target_type": "decimal", "policy": "safe_complete"}},
    {"name": "Скидка, %", "inferred_type": "integer",
     "conversion": {"target_type": "integer", "policy": "safe_complete"}},
]
REVENUE_SQL = ('```sql\nSELECT "Канал продажи", SUM("Количество, шт." * "Цена до скидки за единицу, руб." '
               '* (1 - "Скидка, %" / 100.0)) AS "Выручка" FROM t GROUP BY 1\n```')


class FlowTests(unittest.TestCase):
    def setUp(self):
        _SESSIONS.clear()
        from backend.llm import test_ask
        self.catalog = test_ask.CATALOG
        self.sales = self.catalog["tables"][0]
        self.saved = list(self.sales["columns"])
        self.sales["columns"] = self.saved + WHATIF_CATALOG_EXTRA

    def tearDown(self):
        self.sales["columns"] = self.saved

    def test_whatif_recomputed_in_database(self):
        runner = ScenarioRunner()
        result, client, trace = ask("Что будет с выручкой по каналам продаж, если скидка вырастет на 5 п.п.?",
                                    [REVENUE_SQL], runner)
        self.assertEqual(len(client.calls), 1)  # база без объяснения моделью
        self.assertEqual(trace["driver"], "Скидка, %")
        self.assertFalse(trace["proportional"])
        self.assertIn('"Скидка, %" + (5)', runner.sqls[-1])
        self.assertEqual(result["table"]["rows"][0], ["Интернет-магазин", "100", "90", "-10", "-10,0%"])
        self.assertIn("изменится с 300 до 270 (-30, -10,0%)", result["answer"])
        self.assertEqual([s["name"] for s in result["chart"]["series"]], ["Сейчас", "При сценарии"])

    def test_whatif_proportional_when_driver_not_in_formula(self):
        runner = ScenarioRunner()
        result, _, trace = ask("Что будет с выручкой по каналам продаж, если средний чек вырастет на 10%?",
                               [REVENUE_SQL, "x"], runner)
        self.assertTrue(trace["proportional"])
        self.assertIn("изменится с 300 до 330", result["answer"])
        self.assertIn("Допущение", result["answer"])

    def test_forecast_after_table_clarification(self):
        """«Прогноз выручки» → уточнение таблицы → ответ кнопкой → прогноз без повторного вопроса."""
        from backend.llm import test_ask
        tables = test_ask.CATALOG["tables"]
        ads = {"name": "data_06_Рекламные_кампании", "columns": [
            {"name": "Выручка по рекламе, руб.", "inferred_type": "decimal",
             "conversion": {"target_type": "decimal", "policy": "safe_complete"}}]}
        tables.append(ads)
        try:
            result, _, trace = ask("Какой прогноз выручки на следующий квартал?", [], session="f1")
            self.assertEqual(trace["clarification"], "table")
            months = [(dt.date(2025, m, 1), Decimal(100 + 10 * m)) for m in range(1, 10)]
            runner = FakeRunner(rows=months, columns=["Месяц", "Выручка"])
            sql = ('```sql\nSELECT date_trunc(\'month\', "Дата") AS "Месяц", SUM("Количество, шт." * '
                   '"Цена до скидки за единицу, руб." * (1 - "Скидка, %" / 100.0)) AS "Выручка" '
                   'FROM t GROUP BY 1 ORDER BY 1\n```')
            result, _, trace = ask("Продажи", [sql, "x"], runner, session="f1")
            self.assertEqual(trace["table"], "data_01_Продажи")
            self.assertIn("forecast", trace)
        finally:
            tables.remove(ads)

    def test_forecast(self):
        months = [(dt.date(2025, m, 1), Decimal(100 + 10 * m)) for m in range(1, 10)]
        runner = FakeRunner(rows=months, columns=["Месяц", "Выручка"])
        sql = ('```sql\nSELECT date_trunc(\'month\', "Дата") AS "Месяц", SUM("Количество, шт." * '
               '"Цена до скидки за единицу, руб." * (1 - "Скидка, %" / 100.0)) AS "Выручка" '
               'FROM t GROUP BY 1 ORDER BY 1\n```')
        result, _, trace = ask("Какой прогноз выручки по продажам на следующий квартал?", [sql, "x"], runner)
        self.assertEqual([round(v) for v in trace["forecast"]], [200, 210, 220])
        self.assertEqual(result["chart"]["type"], "line")
        self.assertEqual(result["chart"]["categories"][-1], "01.12.2025")
        self.assertEqual(result["table"]["rows"][0], ["01.10.2025", "200"])
        self.assertIn("рост на ", result["answer"])


if __name__ == "__main__":
    unittest.main()
