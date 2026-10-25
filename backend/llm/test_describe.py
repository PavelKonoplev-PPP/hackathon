"""Тесты LLM-модуля без модели и без БД:
    python -m unittest discover -s backend -p 'test_*.py' -t .
"""
import unittest

from backend.llm.catalog_summary import (display_table_name, overview_table,
                                         quality_section, summarize_catalog,
                                         table_row_count)
from backend.llm.describe import describe_data, shorten


def catalog():
    return {"schema": "raw", "tables": [{
        "name": "data_01_Продажи", "estimated_total_rows": 499000,
        "columns": [
            {"name": "Цена, руб.", "inferred_type": "decimal",
             "conversion": {"policy": "safe_with_nulls", "quality": {
                 "total_rows": 500000, "missing_rows": 25000,
                 "invalid_rows": 0, "convertible_rows": 475000}}},
            {"name": "Дата продажи", "inferred_type": "date",
             "conversion": {"policy": "blocked_invalid", "quality": {
                 "total_rows": 500000, "missing_rows": 0,
                 "invalid_rows": 12, "convertible_rows": 499988}}},
            {"name": "Канал", "inferred_type": "category",
             "conversion": {"policy": "not_applicable", "quality": None}},
        ]}]}


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if "Общая картина" in messages[0]["content"]:
            return "Общая картина: продажи.\n\nЧто можно спросить:\n1. Вопрос?"
        return "Таблица о продажах."


class SummaryTests(unittest.TestCase):
    def test_table_name(self):
        self.assertEqual(display_table_name("data_02_Техническая_поддержка"),
                         "Техническая поддержка")
        self.assertEqual(display_table_name("orders"), "orders")

    def test_exact_rows_preferred(self):
        self.assertEqual(table_row_count(catalog()["tables"][0]), 500000)

    def test_summary_facts(self):
        text = summarize_catalog(catalog())
        self.assertIn("Всего строк во всех таблицах: 500 000", text)
        self.assertIn("Цена, руб.: число, пропуски 5%", text)
        self.assertIn("некорректных значений 12", text)
        self.assertIn("Канал: категория", text)

    def test_relationships(self):
        rel = {"join_candidates": [{
            "source": {"table": "data_01_Продажи", "column": "Клиент"},
            "target": {"table": "data_03_Клиенты", "column": "ID"},
            "technical_reference_verified": True}]}
        self.assertIn("«Продажи».Клиент -> «Клиенты».ID", summarize_catalog(catalog(), rel))

    def test_self_reference_and_no_cross_links(self):
        rel = {"join_candidates": [{
            "source": {"table": "data_01_Продажи", "column": "Код исходной позиции"},
            "target": {"table": "data_01_Продажи", "column": "Код позиции"},
            "technical_reference_verified": True}]}
        text = summarize_catalog(catalog(), rel)
        self.assertIn("«Продажи»: Код исходной позиции -> Код позиции", text)
        self.assertIn("анализировать по отдельности", text)

    def test_overview_table(self):
        table = overview_table(catalog())
        self.assertEqual(table["rows"], [["Продажи", "500 000", "3", "1", "1"]])

    def test_quality_from_code(self):
        text = quality_section(catalog())
        self.assertIn("Значений в некорректном формате: 12", text)
        self.assertIn("«Продажи»: пустые значения в полях Цена, руб. — 5%", text)

    def test_describe_one_call_per_table_plus_overview(self):
        client = FakeClient()
        result = describe_data(catalog(), client=client)
        self.assertEqual(len(client.calls), 2)
        self.assertIn("Цена, руб.", client.calls[0][1]["content"])
        answer = result["answer"]
        self.assertTrue(answer.startswith("Общая картина: продажи."))
        self.assertIn("1. «Продажи» — Таблица о продажах.", answer)
        self.assertIn("Качество данных", answer)
        self.assertTrue(answer.rstrip().endswith("1. Вопрос?"))

    def test_describe_with_active_table_name_filters_to_single_table(self):
        multi_cat = {
            "schema": "raw",
            "tables": [
                {
                    "name": "data_01_Продажи",
                    "estimated_total_rows": 500000,
                    "columns": [{"name": "Цена", "inferred_type": "decimal"}],
                },
                {
                    "name": "data_02_Доставка",
                    "estimated_total_rows": 100000,
                    "columns": [{"name": "Город", "inferred_type": "category"}],
                },
            ],
        }
        client = FakeClient()
        result = describe_data(multi_cat, client=client, table_name="data_02_Доставка")
        # 1 call for single table + 1 call for overview
        self.assertEqual(len(client.calls), 2)
        self.assertIn("Город", client.calls[0][1]["content"])
        self.assertEqual(len(result["table"]["rows"]), 1)
        self.assertEqual(result["table"]["rows"][0][0], "Доставка")
        self.assertIn("1. «Доставка» — Таблица о продажах.", result["answer"])

    def test_describe_with_invalid_table_name_returns_polite_message(self):
        client = FakeClient()
        result = describe_data(catalog(), client=client, table_name="nonexistent_xyz")
        self.assertIn("не найдена в каталоге данных", result["answer"])
        self.assertIn("«Продажи»", result["answer"])
        self.assertIsNone(result["table"])

    def test_match_table_name_variations(self):
        from backend.llm.catalog_summary import match_table_name

        tbl = {"name": "data_01_Продажи"}
        self.assertTrue(match_table_name(tbl, "data_01_Продажи"))
        self.assertTrue(match_table_name(tbl, "Продажи"))
        self.assertTrue(match_table_name(tbl, "продажи"))
        self.assertTrue(match_table_name(tbl, "data_01"))
        self.assertTrue(match_table_name(tbl, "01"))
        self.assertTrue(match_table_name(tbl, "1"))
        self.assertTrue(match_table_name(tbl, "01_Продажи"))
        self.assertTrue(match_table_name(tbl, "raw.data_01_Продажи"))
        # Must not match common table prefix 'data'
        self.assertFalse(match_table_name(tbl, "data"))
        self.assertFalse(match_table_name(tbl, "несуществующая"))

        tbl2 = {"name": "data_02_Техническая_поддержка"}
        self.assertTrue(match_table_name(tbl2, "Техническая поддержка"))
        self.assertTrue(match_table_name(tbl2, "Техническая_поддержка"))
        self.assertTrue(match_table_name(tbl2, "data_02"))




class ShortenTests(unittest.TestCase):
    def test_two_sentences_and_no_name_repeat(self):
        text = ("Таблица «Продажи» хранит данные о продажах. Можно считать выручку. "
                "Ещё одно лишнее предложение.")
        self.assertEqual(shorten(text, "Продажи"),
                         "Хранит данные о продажах. Можно считать выручку.")


if __name__ == "__main__":
    unittest.main()
