from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    AskRequest,
    AskResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
)
from app.db.session import check_db
from app.llm.client import LLMError
from app.services import agent

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return API and database connectivity status."""
    return HealthResponse(status="ok", database=check_db())


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    """Simple query endpoint without session memory."""
    try:
        text = agent.answer(body.question)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AskResponse(answer=text)


@router.post("/api/chat", response_model=ChatResponse)
@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(body: ChatRequest) -> ChatResponse:
    """Frontend-compatible chat endpoint adhering to docs/api-contract.md."""
    try:
        text = agent.answer(body.message, session_id=body.session_id)
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(answer=text)
