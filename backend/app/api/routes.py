from fastapi import APIRouter, HTTPException

from .schemas import (
    AskRequest,
    AskResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
)
from ..db.session import check_db
from ..llm.client import LLMError
from ..services import agent

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
        result = agent.process_chat_message(body.message, session_id=body.session_id)
        return ChatResponse(**result)
    except (LLMError, agent.AgentServiceError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Internal server error: {exc}") from exc
