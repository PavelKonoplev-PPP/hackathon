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
            "Что это за данные?",
            "Что за данные?",
            "Расскажи про данные",
            "Опиши таблицы",
            "Обзор данных",
        ]
        for p in phrases:
            self.assertTrue(agent.is_describe_intent(p), f"Failed to match describe intent for: {p}")
        self.assertFalse(agent.is_describe_intent("Какая выручка за май?"))

    def test_chat_refuses_out_of_scope_metric_with_suggestions(self) -> None:
        """Issue #33 / BI-05: Запрос отсутствующих метрик возвращает 200 с пояснением и подсказками."""
        queries = [
            ("Какой средний NPS у клиентов?", "NPS"),
            ("Посчитай ROI рекламных кампаний", "ROI"),
            ("Покажи LTV клиентов", "LTV"),
            ("Какая эффективность рекламы на ТВ?", "ТВ"),
            ("Сколько потратили на ТВ?", "ТВ"),
            ("Сколько товаров на складе?", "склад"),
            ("Какая зарплата у курьеров?", "заработной плате"),
        ]
        for query, expected_mention in queries:
            with self.subTest(query=query):
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "test-session-out-of-scope", "message": query},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertIsNone(data["sql"])
                self.assertIsNone(data["table"])
                self.assertIsNone(data["chart"])
                self.assertIn(expected_mention.lower(), data["answer"].lower())
                self.assertIn("Вы можете запросить", data["answer"])
                self.assertIn("- «", data["answer"])

    def test_chat_blocks_destructive_sql_mutation(self) -> None:
        """Issue #33 / BI-05: Попытки изменения данных блокируются с кодом 200 и объяснением."""
        mutations = [
            "DROP TABLE data_01_Продажи",
            "DELETE FROM data_01_Продажи WHERE 1=1",
            "UPDATE data_01_Продажи SET status = 'deleted'",
            "ALTER TABLE data_01_Продажи DROP COLUMN price",
            "INSERT INTO data_01_Продажи VALUES ('1', '2')",
            "TRUNCATE TABLE data_01_Продажи",
            "TRUNCATE data_01_Продажи",
            "Удали все данные о продажах",
            "Очисти таблицу доставок",
            "Сотри все данные из таблицы продаж",
            "Дропни таблицу",
            "Снеси все записи",
        ]
        for cmd in mutations:
            with self.subTest(cmd=cmd):
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "test-session-mutation", "message": cmd},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertIsNone(data["sql"])
                self.assertIsNone(data["table"])
                self.assertIsNone(data["chart"])
                self.assertIn("только на чтение", data["answer"])
                self.assertIn("запрещены", data["answer"])
                self.assertIn("Вы можете запросить", data["answer"])

    def test_chat_handles_ambiguous_query_with_clarification(self) -> None:
        """Issue #33 / BI-05: Неоднозначные запросы возвращают 200 с clarification и подсказками."""
        ambiguous = [
            "Покажи метрики",
            "Какая статистика?",
            "Сделай сводку",
            "Покажи все метрики",
            "Сводка по данным",
            "Что с эффективностью бизнеса?",
        ]
        for query in ambiguous:
            with self.subTest(query=query):
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "test-session-ambiguous", "message": query},
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertIsNone(data["sql"])
                self.assertIsNone(data["table"])
                self.assertIsNone(data["chart"])
                self.assertIsNotNone(data["clarification"])
                self.assertGreaterEqual(len(data["clarification"]["options"]), 2)
                self.assertIn("слишком обобщённо", data["answer"])
                self.assertIn("Вы можете запросить", data["answer"])

    @patch.object(agent, "get_reader_connection", side_effect=Exception("could not connect to server"))
    def test_chat_returns_503_when_db_down(self, mock_conn: MagicMock) -> None:
        """Issue #33 / BI-05: Отказ инфраструктуры БД возвращает HTTP 503."""
        response = self.client.post(
            "/api/chat",
            json={"session_id": "test-db-down", "message": "Какая выручка по каналам продаж?"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Не удалось подключиться к базе данных под bi_reader", response.json()["detail"])

    @patch.object(agent, "get_reader_connection", return_value=MagicMock())
    @patch.object(agent, "answer_question", side_effect=Exception("Ollama connection refused"))
    def test_chat_returns_503_when_llm_fails_in_answer_question(
        self, mock_answer: MagicMock, mock_conn: MagicMock
    ) -> None:
        """Issue #33 / BI-05: Сбой LLM во время answer_question возвращает HTTP 503."""
        response = self.client.post(
            "/api/chat",
            json={"session_id": "test-llm-fail", "message": "Какая выручка по каналам продаж?"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Ошибка при ответе на вопрос", response.json()["detail"])

    def test_chat_request_schema_supports_optional_scoping(self) -> None:
        """Issue #40 / BI-12: ChatRequest schema supports optional datasource_id and table_name."""
        try:
            from backend.app.api.schemas import ChatRequest
        except ImportError:
            from app.api.schemas import ChatRequest

        req = ChatRequest(
            session_id="sess-1",
            message="Какая выручка?",
            datasource_id="raw",
            table_name="data_01_Продажи",
        )
        self.assertEqual(req.datasource_id, "raw")
        self.assertEqual(req.table_name, "data_01_Продажи")

        # Backward compatibility: omitted fields default to None
        req_default = ChatRequest(session_id="sess-2", message="Что в данных?")
        self.assertIsNone(req_default.datasource_id)
        self.assertIsNone(req_default.table_name)

    @patch.object(agent, "process_chat_message")
    def test_chat_passes_scoping_parameters_to_agent(self, mock_proc: MagicMock) -> None:
        """Issue #40 / BI-12: chat endpoint passes datasource_id and table_name to agent."""
        mock_proc.return_value = {
            "answer": "Результат",
            "chart": None,
            "table": None,
            "sql": None,
            "clarification": None,
        }
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-scope-pass",
                "message": "Какая выручка?",
                "datasource_id": "raw",
                "table_name": "data_01_Продажи",
            },
        )
        self.assertEqual(response.status_code, 200)
        mock_proc.assert_called_once_with(
            "Какая выручка?",
            session_id="test-scope-pass",
            datasource_id="raw",
            table_name="data_01_Продажи",
        )

    @patch.object(agent, "get_reader_connection", return_value=MagicMock())
    @patch.object(agent, "answer_question")
    def test_chat_with_active_table_name_restricts_catalog_for_sql(
        self, mock_answer: MagicMock, mock_conn: MagicMock
    ) -> None:
        """Issue #40 / BI-12: ChatRequest with active table_name restricts catalog and guides SQL generation."""
        mock_answer.return_value = {
            "answer": "Выручка составила 1 000 000 руб.",
            "chart": None,
            "table": {"columns": ["Выручка"], "rows": [["1 000 000"]]},
            "sql": "SELECT SUM(price) FROM t",
            "clarification": None,
        }
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-table-scoped-sql",
                "message": "Какая общая выручка?",
                "table_name": "data_01_Продажи",
            },
        )
        self.assertEqual(response.status_code, 200)
        mock_answer.assert_called_once()
        args, kwargs = mock_answer.call_args
        call_catalog = args[1] if len(args) > 1 else kwargs.get("catalog")
        self.assertIsNotNone(call_catalog)
        self.assertEqual(len(call_catalog["tables"]), 1)
        self.assertEqual(call_catalog["tables"][0]["name"], "data_01_Продажи")
        self.assertEqual(kwargs.get("table_name"), "data_01_Продажи")

    @patch.object(agent, "describe_data")
    def test_chat_describe_intent_with_active_table_returns_single_table_overview(
        self, mock_describe: MagicMock
    ) -> None:
        """Issue #40 / BI-12: Describe intent with active table returns single-table overview."""
        mock_describe.return_value = {
            "answer": "Таблица «Продажи» хранит данные о продажах.",
            "table": {
                "columns": ["Таблица", "Строк", "Колонок", "Числовых", "С датами"],
                "rows": [["Продажи", "500 000", "7", "1", "1"]],
            },
            "chart": None,
            "sql": None,
            "clarification": None,
        }
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-describe-single-table",
                "message": "Что в данных?",
                "table_name": "data_01_Продажи",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("«Продажи»", data["answer"])
        self.assertIsNotNone(data["table"])
        self.assertEqual(len(data["table"]["rows"]), 1)
        self.assertEqual(data["table"]["rows"][0][0], "Продажи")

        mock_describe.assert_called_once()
        passed_cat = mock_describe.call_args[0][0]
        self.assertEqual(len(passed_cat["tables"]), 1)
        self.assertEqual(passed_cat["tables"][0]["name"], "data_01_Продажи")
        self.assertEqual(mock_describe.call_args[1].get("table_name"), "data_01_Продажи")

    @patch.object(agent, "describe_data")
    def test_chat_natural_describe_phrasing_with_active_table(
        self, mock_describe: MagicMock
    ) -> None:
        """Issue #40: Natural phrasing 'Опиши эту таблицу' triggers describe_data."""
        mock_describe.return_value = {
            "answer": "Таблица о продажах.",
            "table": {"columns": ["Таблица", "Строк"], "rows": [["Продажи", "500 000"]]},
            "chart": None,
            "sql": None,
            "clarification": None,
        }
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-natural-describe",
                "message": "Опиши эту таблицу",
                "table_name": "data_01_Продажи",
            },
        )
        self.assertEqual(response.status_code, 200)
        mock_describe.assert_called_once()

    def test_chat_invalid_table_name_returns_graceful_notification(self) -> None:
        """Issue #40 / BI-12: Invalid table name returns structured graceful notification."""
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-invalid-table",
                "message": "Какая выручка?",
                "table_name": "несуществующая_таблица_xyz",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["sql"])
        self.assertIsNone(data["table"])
        self.assertIsNone(data["chart"])
        self.assertIn("не найдена в каталоге данных", data["answer"])
        self.assertIn("«Продажи»", data["answer"])

    def test_chat_invalid_datasource_id_returns_graceful_notification(self) -> None:
        """Issue #40 / BI-12: Invalid datasource_id returns structured graceful notification."""
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-invalid-ds",
                "message": "Какая выручка?",
                "datasource_id": "unknown_datasource_xyz",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["sql"])
        self.assertIn("не найден в каталоге данных", data["answer"])
        self.assertIn("«Продажи»", data["answer"])

    def test_chat_conflicting_datasource_and_table_returns_notification(self) -> None:
        """Issue #40: Conflicting datasource_id and table_name returns graceful explanation."""
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-conflict-ds-table",
                "message": "Какая выручка?",
                "datasource_id": "data_02_Техническая_поддержка",
                "table_name": "data_01_Продажи",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["sql"])
        self.assertIn("не принадлежит источнику данных", data["answer"])

    def test_chat_common_prefix_data_does_not_falsely_match(self) -> None:
        """Issue #40: table_name='data' must not falsely match data_01_Продажи."""
        response = self.client.post(
            "/api/chat",
            json={
                "session_id": "test-prefix-data",
                "message": "Какая выручка?",
                "table_name": "data",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("не найдена в каталоге данных", data["answer"])

    @patch.object(agent, "describe_data")
    def test_chat_backward_compatibility_when_scope_omitted(
        self, mock_describe: MagicMock
    ) -> None:
        """Issue #40 / BI-12: Full backward compatibility when datasource_id and table_name are omitted."""
        mock_describe.return_value = {
            "answer": "Обзор всей базы данных.",
            "table": {
                "columns": ["Таблица", "Строк"],
                "rows": [["Продажи", "500 000"], ["Доставка", "100 000"]],
            },
            "chart": None,
            "sql": None,
            "clarification": None,
        }
        response = self.client.post(
            "/api/chat",
            json={"session_id": "test-no-scope", "message": "Опиши таблицы"},
        )
        self.assertEqual(response.status_code, 200)
        mock_describe.assert_called_once()
        passed_cat = mock_describe.call_args[0][0]
        # Full catalog has 9 tables
        self.assertEqual(len(passed_cat["tables"]), 9)



if __name__ == "__main__":
    unittest.main()

