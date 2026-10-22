from app.llm.client import chat

SYSTEM_PROMPT = (
    "Ты аналитический ассистент. Отвечай на русском языке кратко и по делу. "
    "Доступа к данным пока нет."
)

_SESSION_HISTORY: dict[str, list[dict[str, str]]] = {}


def answer(question: str, session_id: str | None = None) -> str:
    """Generate an analytical response for a user question using the LLM.

    Args:
        question: User query string.
        session_id: Optional session identifier for maintaining multi-turn context.

    Returns:
        LLM generated answer string.
    """
    if session_id:
        history = _SESSION_HISTORY.setdefault(
            session_id, [{"role": "system", "content": SYSTEM_PROMPT}]
        )
        history.append({"role": "user", "content": question})
        reply = chat(history)
        history.append({"role": "assistant", "content": reply})
        return reply

    return chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
    )


def clear_session(session_id: str) -> None:
    """Clear memory for a given session."""
    _SESSION_HISTORY.pop(session_id, None)
