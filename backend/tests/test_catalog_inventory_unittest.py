"""Regression: source-wide table/row inventory must not ask to choose one table."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import agent
from backend.llm.catalog_summary import catalog_inventory_response, table_row_count


QUESTION = (
    "Сколько таблиц и строк всего в текущем источнике данных? "
    "Перечисли названия таблиц и количество строк в каждой."
)


def fixture():
    return {
        "schema": "raw",
        "tables": [
            {
                "name": "data_01_Продажи",
                "estimated_total_rows": 987,
                "columns": [{"name": "Сумма", "inferred_type": "decimal",
                             "conversion": {"quality": {"total_rows": 120}}}],
            },
            {
                "name": "data_02_Доставка",
                "estimated_total_rows": 80,
                "columns": [{"name": "Дата", "inferred_type": "date"}],
            },
            {
                "name": "data_03_empty",
                "row_count": 0,
                "columns": [],
            },
        ],
    }


class InventoryTests(unittest.TestCase):
    def test_full_question_is_inventory_intent(self):
        self.assertTrue(agent.is_catalog_inventory_intent(QUESTION))
        self.assertTrue(agent.is_catalog_inventory_intent("Сколько всего таблиц в базе?"))
        self.assertTrue(agent.is_catalog_inventory_intent(
            "Перечисли названия таблиц и количество строк в каждой"))
        self.assertFalse(agent.is_catalog_inventory_intent("Сколько строк в таблице «Продажи»?"))
        self.assertFalse(agent.is_catalog_inventory_intent("Сколько всего продаж в текущем источнике?"))
        self.assertFalse(agent.is_catalog_inventory_intent("Какая выручка по всем таблицам?"))
        self.assertFalse(agent.is_catalog_inventory_intent("Опиши данные"))

    def test_inventory_prefers_audited_rows_and_handles_zero(self):
        self.assertEqual(table_row_count(fixture()["tables"][0]), 120)
        self.assertEqual(table_row_count(fixture()["tables"][2]), 0)
        result = catalog_inventory_response(fixture())
        self.assertIn("3 таблиц", result["answer"])
        self.assertIn("200", result["answer"])
        self.assertIn("Для 1 таблиц использована оценка", result["answer"])
        self.assertEqual(result["table"]["columns"], ["Таблица", "Строк"])
        self.assertEqual(result["table"]["rows"], [
            ["Продажи", "120"], ["Доставка", "80"], ["empty", "0"],
        ])
        self.assertIsNone(result["sql"])
        self.assertIsNone(result["chart"])
        self.assertIsNone(result["clarification"])

    def test_missing_count_not_fabricated(self):
        source = {"tables": [{"name": "orders", "columns": []},
                             {"name": "support", "row_count": 12, "columns": []}]}
        result = catalog_inventory_response(source)
        self.assertIn("общий итог по источнику пока неизвестен", result["answer"])
        self.assertEqual(result["table"]["rows"], [
            ["orders", "неизвестно"], ["support", "12"],
        ])

    @patch.object(agent, "get_reader_connection", side_effect=AssertionError("DB should not be queried"))
    @patch.object(agent, "answer_question", side_effect=AssertionError("Per-table LLM cannot handle inventory"))
    @patch.object(agent, "describe_data", side_effect=AssertionError("No lengthy LLM describe"))
    def test_agent_handles_full_question_without_llm_or_db(self, *_):
        response = agent.process_chat_message(
            QUESTION, catalog=fixture(), datasource_id="raw", session_id="qa-catalog-raw")
        self.assertEqual(len(response["table"]["rows"]), 3)
        self.assertIn("200", response["answer"])
        self.assertIsNone(response["clarification"])

    @patch.object(agent, "get_reader_connection", side_effect=AssertionError("DB should not be queried"))
    @patch.object(agent, "answer_question", side_effect=AssertionError("LLM should not be used"))
    @patch.object(agent, "filter_catalog_for_scope")
    def test_external_scope_never_lists_raw_tables(self, filter_catalog, *_):
        filter_catalog.return_value = (
            {"schema": "public", "datasource_id": "bi_test", "tables": [
                {"name": "demo_orders", "estimated_total_rows": 180, "columns": []},
                {"name": "demo_support", "row_count": 80, "columns": []},
            ]}, None, None
        )
        response = agent.process_chat_message(
            QUESTION, catalog=fixture(), datasource_id="bi_test",
            session_id="qa-catalog-external")
        self.assertEqual([r[0] for r in response["table"]["rows"]],
                         ["demo_orders", "demo_support"])
        self.assertNotIn("Продажи", response["answer"])
        self.assertNotIn("Продажи", str(response["table"]))

    @patch.object(agent, "load_catalogs")
    @patch.object(agent, "get_reader_connection", side_effect=AssertionError("No DB for inventory"))
    @patch.object(agent, "answer_question", side_effect=AssertionError("No per-table disambiguation"))
    def test_api_contract_source_count(self, _ans, _conn, catalogs):
        catalogs.return_value = (fixture(), None)
        response = TestClient(app).post(
            "/api/chat",
            json={"session_id": "qa-source-inventory-api",
                  "datasource_id": "raw", "message": QUESTION},
        )
        self.assertEqual(response.status_code, 200)
        obj = response.json()
        self.assertIsNone(obj.get("clarification"))
        self.assertIsNone(obj.get("sql"))
        self.assertEqual(len(obj["table"]["rows"]), 3)
        self.assertIn("Всего строк по каталогу: 200", obj["answer"])


if __name__ == "__main__":
    unittest.main()
