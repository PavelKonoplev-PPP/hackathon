"""Тесты сравнения периодов и сегментов (#29) — без модели и БД."""
import datetime as dt
import unittest
from decimal import Decimal

from backend.llm.ask import _SESSIONS, two_segment_difference
from backend.llm.ask import format_value
from backend.llm.compare import (check_compare_sql, compare_summary, enrich_compare, is_compare,
                                 parse_periods)
from backend.llm.sql_runner import QueryResult
from backend.llm.test_ask import FakeRunner, ask

LATEST = dt.date(2026, 9, 28)


def labels(question, latest=LATEST):
    return [p.label for p in parse_periods(question, latest)]


class PeriodTests(unittest.TestCase):
    def test_months_and_shared_year(self):
        self.assertEqual(labels("Сравни выручку за сентябрь и август 2025"), ["август 2025", "сентябрь 2025"])
        self.assertEqual(labels("Сравни май 2025 и июнь 2025"), ["май 2025", "июнь 2025"])
        # без года — последний такой месяц в данных
        self.assertEqual(labels("Сравни март и апрель"), ["март 2026", "апрель 2026"])
        self.assertEqual(labels("Сравни октябрь и ноябрь"), ["октябрь 2025", "ноябрь 2025"])

    def test_relative(self):
        self.assertEqual(labels("Сравни сентябрь 2025 с предыдущим месяцем"), ["август 2025", "сентябрь 2025"])
        self.assertEqual(labels("Сравни январь 2025 с предыдущим месяцем"), ["декабрь 2024", "январь 2025"])
        self.assertEqual(labels("2025 год по сравнению с прошлым годом"), ["2024 год", "2025 год"])

    def test_years_and_quarters(self):
        self.assertEqual(labels("Как изменилась выручка в 2025 году по сравнению с 2024"), ["2024 год", "2025 год"])
        periods = parse_periods("Сравни 4 квартал 2024 и 1 квартал 2025", LATEST)
        self.assertEqual([p.label for p in periods], ["4 квартал 2024", "1 квартал 2025"])
        self.assertEqual((periods[0].start, periods[0].end), (dt.date(2024, 10, 1), dt.date(2025, 1, 1)))

    def test_is_compare(self):
        self.assertTrue(is_compare("Сравни Москву и Казань"))
        self.assertTrue(is_compare("Как изменилась выручка"))
        self.assertFalse(is_compare("Выручка по каналам"))


class MoneyTests(unittest.TestCase):
    COLS = ["Канал продажи", "Количество, шт.", "Цена до скидки за единицу, руб.", "Скидка, %"]

    def test_formula_and_check(self):
        from backend.llm.semantics import check_money, money_formula
        f = money_formula(self.COLS)
        self.assertEqual(f, 'SUM("Количество, шт." * "Цена до скидки за единицу, руб." * (1 - "Скидка, %" / 100.0))')
        q = "Выручка с учётом скидки по каналам"
        self.assertIn("Выручка =", check_money('SELECT SUM("Цена до скидки за единицу, руб.") FROM t', q, self.COLS)[0])
        bad = ('SELECT SUM("Количество, шт." * "Цена до скидки за единицу, руб." * (1 - "Скидка, %") / 100.0) FROM t')
        self.assertIn("скидка делится", check_money(bad, q, self.COLS)[0])
        self.assertEqual(check_money(f"SELECT {f} FROM t", q, self.COLS), [])
        self.assertEqual(check_money("SELECT COUNT(*) FROM t", "Сколько строк?", self.COLS), [])

    def test_compare_metric(self):
        from backend.llm.ask import compare_metric
        table = {"columns": [{"name": c, "conversion": {"target_type": "integer", "policy": "safe_complete"}}
                             for c in self.COLS[1:]] + [{"name": "Канал продажи", "conversion": {}}]}
        self.assertTrue(compare_metric("Сравни выручку", table).startswith('SUM("Количество, шт." *'))
        self.assertEqual(compare_metric("Как изменилось количество штук", table), 'SUM("Количество, шт.")')
        self.assertEqual(compare_metric("Сравни число операций", table), "COUNT(*)")


class CheckTests(unittest.TestCase):
    def test_both_periods_required_and_no_date_in_where(self):
        periods = parse_periods("сравни август и сентябрь 2025")
        sql_one = ("SELECT SUM(x) FILTER (WHERE \"Дата\" >= '2025-09-01' AND \"Дата\" < '2025-10-01') "
                   "AS \"сентябрь 2025\" FROM t")
        self.assertIn("август 2025", check_compare_sql(sql_one, periods, "Дата")[0])
        sql_where = ("SELECT SUM(x) FILTER (WHERE \"Дата\" >= '2025-08-01' AND \"Дата\" < '2025-09-01'), "
                     "SUM(x) FILTER (WHERE \"Дата\" >= '2025-09-01' AND \"Дата\" < '2025-10-01') "
                     "FROM t WHERE \"Дата\" >= '2025-09-01'")
        self.assertIn("Убери условие", check_compare_sql(sql_where, periods, "Дата")[0])
        self.assertEqual(check_compare_sql(sql_where.split(" WHERE \"Дата\" >= '2025-09-01'")[0]
                                           .replace("FROM t WHERE", "FROM t"), periods, "Дата"), [])


class EnrichTests(unittest.TestCase):
    def test_single_row(self):
        periods = parse_periods("сравни август и сентябрь 2025")
        cols, rows, pair = enrich_compare(["август 2025", "сентябрь 2025"],
                                          [(Decimal(100), Decimal(125))], periods)
        self.assertEqual(rows[0][-2:], (25.0, 25.0))
        self.assertEqual(compare_summary(cols, rows, pair, format_value),
                         "сентябрь 2025: 125, август 2025: 100 — рост: +25, +25,0%.")

    def test_by_segment(self):
        periods = parse_periods("сравни август и сентябрь 2025")
        cols, rows, pair = enrich_compare(
            ["Канал", "август 2025", "сентябрь 2025"],
            [("Онлайн", 100, 150), ("Магазин", 200, 180), ("Приложение", 50, 55)], periods)
        text = compare_summary(cols, rows, pair, format_value)
        self.assertIn("в целом — рост: +35, +10,0%", text)
        self.assertIn("Сильнее всего выросло «Онлайн» (рост: +50, +50,0%)", text)
        self.assertIn("слабее всего — «Магазин» (снижение: -20, -10,0%)", text)

    def test_two_segments(self):
        self.assertEqual(two_segment_difference(["Город", "Выручка"], [("Казань", 80), ("Москва", 100)]),
                         "«Москва» больше «Казань» на 20 (25,0%) по показателю «Выручка».")


class FlowTests(unittest.TestCase):
    def setUp(self):
        _SESSIONS.clear()

    SQL = ('```sql\nSELECT "Канал продажи",\n'
           'SUM("Цена, руб.") FILTER (WHERE "Дата" >= \'2025-08-01\' AND "Дата" < \'2025-09-01\') AS "август 2025",\n'
           'SUM("Цена, руб.") FILTER (WHERE "Дата" >= \'2025-09-01\' AND "Дата" < \'2025-10-01\') AS "сентябрь 2025"\n'
           'FROM t GROUP BY "Канал продажи"\n```')

    def test_compare_flow(self):
        runner = FakeRunner(rows=[("Интернет-магазин", Decimal(100), Decimal(120)),
                                  ("Мобильное приложение", Decimal(200), Decimal(190))],
                            columns=["Канал продажи", "август 2025", "сентябрь 2025"])
        result, client, trace = ask("Сравни выручку по каналам продаж за август и сентябрь 2025",
                                    [self.SQL], runner)
        self.assertEqual(trace["periods"], ["август 2025", "сентябрь 2025"])
        self.assertIn("FILTER (WHERE \"Дата\" >= '2025-08-01'", client.calls[0][0]["content"])
        self.assertEqual(trace["errors"], [])
        self.assertEqual(result["table"]["columns"][-2:], ["Изменение", "Изменение, %"])
        self.assertEqual(result["table"]["rows"][0][-2:], ["+20", "+20,0%"])
        self.assertIn("в целом — рост: +10", result["answer"])
        self.assertEqual([s["name"] for s in result["chart"]["series"]], ["август 2025", "сентябрь 2025"])
        self.assertEqual(len(client.calls), 1)  # объяснение без модели

    def test_followup_compare_with_previous_month(self):
        runner = FakeRunner()
        sept = ('```sql\nSELECT "Канал продажи", SUM("Цена, руб.") FROM t WHERE "Дата" >= \'2025-09-01\' '
                'AND "Дата" < \'2025-10-01\' GROUP BY 1\n```')
        ask("Какая выручка по каналам продаж за сентябрь 2025?", [sept, "x"], runner, session="c1")
        runner.rows = [("Интернет-магазин", Decimal(90), Decimal(120))]
        runner.columns = ["Канал продажи", "август 2025", "сентябрь 2025"]
        result, client, trace = ask("Сравни с предыдущим месяцем", [self.SQL], runner, session="c1")
        self.assertEqual(trace["periods"], ["август 2025", "сентябрь 2025"])
        # сравнение пишется заново по полному вопросу, без прошлого SQL
        self.assertEqual(client.calls[0][1]["content"],
                         "Вопрос: Какая выручка по каналам продаж за сентябрь 2025? "
                         "Сравни с предыдущим месяцем")
        self.assertEqual(len(client.calls[0]), 2)
        self.assertIn("+33,3%", result["table"]["rows"][0][-1])


if __name__ == "__main__":
    unittest.main()
