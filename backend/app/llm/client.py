import httpx

from app.config import get_settings


class LLMError(RuntimeError):
    """Raised when communication with the LLM endpoint fails."""


def chat(messages: list[dict[str, str]], timeout: float = 60.0) -> str:
    """Send chat messages to an OpenAI-compatible completion endpoint.

    Args:
        messages: List of message objects with 'role' and 'content'.
        timeout: Request timeout in seconds.

    Returns:
        Generated text response from the model.

    Raises:
        LLMError: When model is missing, request fails, or payload is invalid.
    """
    settings = get_settings()
    if not settings.llm_model:
        raise LLMError("LLM_MODEL is not set in environment or .env")

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0,
    }
    headers: dict[str, str] = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc
