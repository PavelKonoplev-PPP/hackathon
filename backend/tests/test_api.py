import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.llm.client import LLMError
from app.main import app
from app.services import agent

client = TestClient(app)


def test_health_reports_db_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "check_db", lambda: False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": False}


def test_api_health_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "check_db", lambda: True)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True}


def test_ask_returns_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "chat", lambda messages, **kwargs: "ответ")

    response = client.post("/ask", json={"question": "Что в данных?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "ответ"}


def test_ask_returns_503_when_llm_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(messages, **kwargs):
        raise LLMError("недоступна")

    monkeypatch.setattr(agent, "chat", fail)

    response = client.post("/ask", json={"question": "Что в данных?"})

    assert response.status_code == 503
    assert "недоступна" in response.json()["detail"]


def test_ask_rejects_empty_question() -> None:
    response = client.post("/ask", json={"question": ""})

    assert response.status_code == 422


def test_chat_returns_contract_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "chat", lambda messages, **kwargs: "Продажи составили 100 рублей")

    response = client.post(
        "/api/chat",
        json={"session_id": "test-session-1", "message": "Сколько продаж?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Продажи составили 100 рублей"
    assert data["chart"] is None
    assert data["table"] is None
    assert data["sql"] is None
    assert data["clarification"] is None


def test_chat_returns_503_when_llm_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(messages, **kwargs):
        raise LLMError("LLM offline")

    monkeypatch.setattr(agent, "chat", fail)

    response = client.post(
        "/api/chat",
        json={"session_id": "test-session-2", "message": "Любой вопрос"},
    )

    assert response.status_code == 503
    assert "LLM offline" in response.json()["detail"]


def test_chat_rejects_empty_message() -> None:
    response = client.post(
        "/api/chat",
        json={"session_id": "test-session-3", "message": ""},
    )

    assert response.status_code == 422
