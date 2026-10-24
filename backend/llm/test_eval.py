"""Тесты сравнения результатов и статическая проверка эталонов (без БД и модели)."""
import datetime as dt
import json
import unittest
from decimal import Decimal
from pathlib import Path

from backend.llm.eval import compare
from backend.llm.eval_cases import CASES
from backend.llm.sql_guard import validate_sql

CATALOG = Path(__file__).resolve().parents[2] / "data" / "schema-catalog.json"
VALUES = CATALOG.with_name("category-values.json")  # выгрузка значений категорий (см. README)


class CompareTests(unittest.TestCase):
    def test_value(self):
        self.assertTrue(compare("value", [(500000,)], [("Всего", 500000)])[0])
        self.assertFalse(compare("value", [(500000,)], [(499000,)])[0])
        self.assertFalse(compare("value", [(500000,)], [(499999,)])[0])
        self.assertTrue(compare("value", [(Decimal("3.9412"),)], [(Decimal("3.94"),)])[0])
        self.assertFalse(compare("value", [(Decimal("3.9412"),)], [(Decimal("3.9"),)])[0])

    def test_all_ignores_order_names_and_rounding(self):
        gold = [("Онлайн", Decimal("10.004")), ("Магазин", Decimal("5"))]
        agent = [("магазин", 5.0), ("Онлайн", 10.0)]
        self.assertTrue(compare("all", gold, agent)[0])

    def test_all_reports_missing(self):
        ok, reason = compare("all", [("A", 1), ("B", 2)], [("A", 1)])
        self.assertFalse(ok)
        self.assertIn("(b, 2)", reason)

    def test_wrong_number(self):
        self.assertFalse(compare("all", [("A", 100)], [("A", 90)])[0])

    def test_top_order_matters(self):
        gold = [("A", 3), ("B", 2)]
        self.assertTrue(compare("top", gold, [("A", 3), ("B", 2), ("C", 1)])[0])
        self.assertFalse(compare("top", gold, [("B", 2), ("A", 3)])[0])

    def test_dates_and_percent(self):
        gold = [(dt.datetime(2025, 3, 1), 7)]
        self.assertTrue(compare("all", gold, [(dt.date(2025, 3, 1), 7)])[0])
        self.assertTrue(compare("all", [("A", 0.0123)], [("A", 1.23)], scale100=True)[0])
        self.assertFalse(compare("all", [("A", 0.0123)], [("A", 1.23)])[0])

    def test_empty(self):
        self.assertFalse(compare("value", [(None,)], [(1,)])[0])
        self.assertFalse(compare("all", [("A", 1)], [])[0])


@unittest.skipUnless(CATALOG.is_file(), "нет data/schema-catalog.json")
class GoldStaticTests(unittest.TestCase):
    """Каждый эталон ссылается на существующие таблицу и колонки и проходит проверку SQL."""

    def test_gold_sql(self):
        tables = {t["name"]: t for t in json.loads(CATALOG.read_text(encoding="utf-8"))["tables"]}
        ids = set()
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertNotIn(case["id"], ids)
                ids.add(case["id"])
                self.assertIn(case["mode"], ("value", "all", "top", "clarify", "refuse"))
                if case["mode"] == "refuse":
                    continue
                self.assertIn(case["table"], tables)
                if "gold" in case:
                    columns = [c["name"] for c in tables[case["table"]]["columns"]]
                    validate_sql(case["gold"], columns)
                elif case["mode"] != "clarify":
                    self.assertIsInstance(case["expected"], int)



@unittest.skipUnless(CATALOG.is_file() and VALUES.is_file(), "нет каталога или выгрузки значений")
class TableChoiceTests(unittest.TestCase):
    """Выбор таблицы / уточнение / отказ для всех вопросов набора — без модели."""

    def test_all_cases(self):
        from backend.llm.ask import WRITE_INTENT, choose_table, unit_options
        from backend.llm.semantics import Intent

        tables = json.loads(CATALOG.read_text(encoding="utf-8"))["tables"]
        values = json.loads(VALUES.read_text(encoding="utf-8"))
        for case in CASES:
            question = (case.get("steps") or [case["question"]])[0]
            with self.subTest(case=case["id"]):
                if WRITE_INTENT.search(question):
                    self.assertEqual(case["mode"], "refuse")
                    continue
                table, candidates = choose_table(question, tables, values)
                if case["mode"] == "refuse":
                    self.assertEqual((table, candidates), (None, []))
                elif case["mode"] == "clarify" and case["clarify"] == "table":
                    self.assertIsNone(table)
                    self.assertGreater(len(candidates), 1)
                else:
                    self.assertIsNotNone(table, candidates)
                    self.assertEqual(table["name"], case["table"])
                    units = unit_options(question, Intent.parse(question), table)
                    if case["mode"] == "clarify":
                        self.assertTrue(units)
                    elif not case.get("steps"):
                        self.assertFalse(units)

    def test_semantic_checks_accept_gold_sql(self):
        """Эталонный SQL верен по определению — смысловая проверка не должна к нему придираться."""
        from backend.llm.catalog_summary import display_table_name
        from backend.llm.semantics import Intent, adjust_sql, check_sql, words
        from backend.llm.sql_runner import column_kind

        tables = {t["name"]: t for t in json.loads(CATALOG.read_text(encoding="utf-8"))["tables"]}
        values = json.loads(VALUES.read_text(encoding="utf-8"))
        for case in CASES:
            if "gold" not in case:
                continue
            with self.subTest(case=case["id"]):
                table = tables[case["table"]]
                question = " ".join(case.get("steps") or [case["question"]])
                intent = Intent.parse(question)
                sql = adjust_sql(case["gold"], intent)
                problems = check_sql(
                    sql, question, intent, values[table["name"]],
                    ignore=words(display_table_name(table["name"])),
                    numeric_columns=[c["name"] for c in table["columns"]
                                     if column_kind(c) in ("integer", "decimal")],
                    columns=[c["name"] for c in table["columns"]])
                self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
