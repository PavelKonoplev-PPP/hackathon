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


if __name__ == "__main__":
    unittest.main()
