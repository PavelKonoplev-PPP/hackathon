import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

try:
    from backend.app.api import routes
    from backend.app.db import session as db_session
    from backend.app.llm.client import LLMError
    from backend.app.main import app
    from backend.app.services import agent
except ImportError:
    from app.api import routes
    from app.db import session as db_session
    from app.llm.client import LLMError
    from app.main import app
    from app.services import agent


class ApiTests(unittest.TestCase):
    """Test suite for FastAPI endpoints and database connectivity."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch.object(routes, "check_db", return_value=False)
    def test_health_reports_db_status(self, mock_check: MagicMock) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": False})

    @patch.object(routes, "check_db", return_value=True)
    def test_api_health_alias(self, mock_check: MagicMock) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": True})

    @patch.object(agent, "chat", return_value="ответ")
    def test_ask_returns_answer(self, mock_chat: MagicMock) -> None:
        response = self.client.post("/ask", json={"question": "Что в данных?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"answer": "ответ"})

    @patch.object(agent, "chat", side_effect=LLMError("недоступна"))
    def test_ask_returns_503_when_llm_down(self, mock_chat: MagicMock) -> None:
        response = self.client.post("/ask", json={"question": "Что в данных?"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("недоступна", response.json()["detail"])

    def test_ask_rejects_empty_question(self) -> None:
        response = self.client.post("/ask", json={"question": ""})
        self.assertEqual(response.status_code, 422)

    @patch.object(
        agent,
        "process_chat_message",
        return_value={
            "answer": "Выручка составила 1 000 000 руб.",
            "chart": {
                "type": "bar",
                "title": "Выручка",
                "x_label": "Канал",
                "y_label": "Рубли",
                "categories": ["Розница", "Онлайн"],
                "series": [{"name": "Выручка", "data": [600000, 400000]}],
            },
            "table": {
                "columns": ["Канал", "Выручка"],
                "rows": [["Розница", "600 000"], ["Онлайн", "400 000"]],
            },
            "sql": "SELECT canal, SUM(price) FROM t GROUP BY 1",
            "clarification": None,
        },
    )
    def test_chat_returns_contract_response(self, mock_proc: MagicMock) -> None:
        response = self.client.post(
            "/api/chat",
            json={"session_id": "test-session-1", "message": "Сколько выручки?"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["answer"], "Выручка составила 1 000 000 руб.")
        self.assertEqual(data["chart"]["type"], "bar")
        self.assertEqual(data["table"]["columns"], ["Канал", "Выручка"])
        self.assertIn("SELECT", data["sql"])
        self.assertIsNone(data["clarification"])

    @patch.object(agent, "process_chat_message", side_effect=agent.AgentServiceError("LLM offline"))
    def test_chat_returns_503_when_llm_down(self, mock_proc: MagicMock) -> None:
        response = self.client.post(
            "/api/chat",
            json={"session_id": "test-session-2", "message": "Любой вопрос"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("LLM offline", response.json()["detail"])

    def test_chat_rejects_empty_message(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={"session_id": "test-session-3", "message": ""},
        )
        self.assertEqual(response.status_code, 422)

    @patch.object(db_session.psycopg, "connect")
    def test_reader_connection_uses_bi_reader(self, mock_connect: MagicMock) -> None:
        db_session.get_reader_connection()
        mock_connect.assert_called_once()
        kwargs = mock_connect.call_args[1]
        self.assertEqual(kwargs["user"], "bi_reader")
        self.assertIn("default_transaction_read_only=on", kwargs["options"])
        self.assertIn("statement_timeout=", kwargs["options"])

    @patch.object(agent, "load_catalogs")
    def test_schema_endpoint_mocked(self, mock_load: MagicMock) -> None:
        mock_load.return_value = (
            {
                "schema": "raw",
                "confidence_is_heuristic": True,
                "relationships_verified": False,
                "tables": [
                    {
                        "name": "data_01_orders",
                        "estimated_total_rows": 100,
                        "columns": [
                            {
                                "name": "order_id",
                                "postgres_type": "text",
                                "inferred_type": "identifier",
                                "nullable": False,
                                "conversion": {"policy": "not_applicable"},
                            },
                            {
                                "name": "amount",
                                "postgres_type": "text",
                                "inferred_type": "decimal",
                                "nullable": True,
                                "conversion": {
                                    "policy": "safe_with_nulls",
                                    "quality": {"total_rows": 100, "missing_rows": 5},
                                },
                            },
                        ],
                    }
                ],
            },
            {
                "rules": {"join_requires_explicit_business_approval": True},
                "join_candidates": [
                    {
                        "source": {"table": "data_01_orders", "column": "order_id"},
                        "target": {"table": "data_01_orders", "column": "parent_order_id"},
                        "kind": "self_reference",
                        "can_auto_join": False,
                        "policy": "manual_approval_required",
                    }
                ],
            },
        )
        response = self.client.get("/api/schema")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["schema_name"], "raw")
        self.assertIn("raw", data["sources"])
        self.assertIn("data_01_orders", data["sources"])
        self.assertEqual(len(data["tables"]), 1)
        table = data["tables"][0]
        self.assertEqual(table["name"], "data_01_orders")
        self.assertEqual(table["display_name"], "orders")
        self.assertEqual(table["row_count"], 100)
        self.assertEqual(len(table["columns"]), 2)
        self.assertEqual(table["columns"][0]["name"], "order_id")
        self.assertEqual(table["columns"][0]["type"], "identifier")
        self.assertEqual(table["columns"][1]["warning"], "Пропусков: 5")
        self.assertEqual(len(data["relationships"]), 1)
        rel = data["relationships"][0]
        self.assertEqual(rel["source_table"], "data_01_orders")
        self.assertEqual(rel["target_table"], "data_01_orders")
        self.assertFalse(rel["can_auto_join"])
        self.assertGreater(len(data["warnings"]), 0)

        # Test alias /schema
        alias_resp = self.client.get("/schema")
        self.assertEqual(alias_resp.status_code, 200)
        self.assertEqual(alias_resp.json(), data)

    @patch.object(agent, "load_catalogs", return_value=(None, None))
    def test_schema_endpoint_empty_catalog(self, mock_load: MagicMock) -> None:
        response = self.client.get("/api/schema")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tables"], [])
        self.assertEqual(data["relationships"], [])
        self.assertTrue(any("не найден" in w for w in data["warnings"]))

    def test_schema_endpoint_real_catalog(self) -> None:
        response = self.client.get("/api/schema")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["schema_name"], "raw")
        self.assertEqual(len(data["tables"]), 9)
        self.assertGreater(len(data["relationships"]), 0)
        total_rows = sum(t["row_count"] or 0 for t in data["tables"])
        self.assertGreater(total_rows, 0)

    @patch.object(agent, "load_catalogs")
    def test_schema_endpoint_edge_cases(self, mock_load: MagicMock) -> None:
        mock_load.return_value = (
            {
                "schema": "test_schema",
                "tables": [
                    {
                        "name": "empty_table",
                        "estimated_total_rows": 0,
                        "columns": [
                            {
                                "name": "bad_col",
                                "warning": "Малая выборка",
                                "conversion": {
                                    "policy": "blocked_invalid",
                                    "quality": {"invalid_rows": 42},
                                },
                            }
                        ],
                    }
                ],
            },
            None,
        )
        response = self.client.get("/api/schema")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["schema_name"], "test_schema")
        self.assertEqual(data["tables"][0]["warnings"], ["Таблица пуста"])
        # Both warning and quality warning are preserved
        self.assertIn("Малая выборка", data["tables"][0]["columns"][0]["warning"])
        self.assertIn("Некорректных значений: 42", data["tables"][0]["columns"][0]["warning"])
        self.assertEqual(data["relationships"], [])

    @patch.object(agent, "load_catalogs")
    def test_schema_endpoint_null_collections(self, mock_load: MagicMock) -> None:
        # None tables, None columns, None rules, None join_candidates must not crash
        mock_load.return_value = (
            {
                "schema": "raw",
                "tables": [
                    {"name": "t_none_cols", "columns": None},
                    None,
                ],
            },
            {
                "rules": None,
                "join_candidates": None,
            },
        )
        response = self.client.get("/api/schema")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["tables"]), 1)
        self.assertEqual(data["tables"][0]["columns"], [])
        self.assertEqual(data["relationships"], [])

    @patch.object(agent, "load_catalogs")
    def test_schema_endpoint_relationships_json_format(self, mock_load: MagicMock) -> None:
        # Verify support for relationships.json format (key "relationships", root "join_approval_required")
        mock_load.return_value = (
            {
                "schema": "raw",
                "tables": [{"name": "t1", "columns": [{"name": "c1"}]}],
            },
            {
                "join_approval_required": True,
                "relationships": [
                    {
                        "kind": "self_reference_candidate",
                        "source": {"table": "t1", "column": "c1"},
                        "target": {"table": "t1", "column": "c2"},
                        "status": "technical_reference_verified",
                        "can_auto_join": False,
                    }
                ],
            },
        )
        response = self.client.get("/api/schema")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["relationships"]), 1)
        self.assertEqual(data["relationships"][0]["policy"], "technical_reference_verified")
        self.assertTrue(
            any("Автоматические JOIN" in w for w in data["warnings"]),
            "Security rule join_approval_required must generate top-level warning",
        )



    def test_describe_intent_triggers(self) -> None:
        phrases = [
            "Что есть в этих данных?",
            "что в данных",
            "Что передо мной?",
            "Опиши данные",
            "Какие таблицы доступны?",
        ]
        for p in phrases:
            self.assertTrue(agent.is_describe_intent(p), f"Failed to match describe intent for: {p}")
        self.assertFalse(agent.is_describe_intent("Какая выручка за май?"))


if __name__ == "__main__":
    unittest.main()
