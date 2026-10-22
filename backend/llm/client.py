"""Подключение к LLM через OpenAI-совместимый API (Ollama, vLLM и т. п.).

Настройки берутся из .env в корне проекта:
    LLM_BASE_URL=http://localhost:11434/v1
    LLM_API_KEY=            (для Ollama можно оставить пустым)
    LLM_MODEL=qwen2.5:3b

Проверка, что модель отвечает (из корня проекта):
    python -m backend.llm.client
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# <think>...</think> — «рассуждения», которые печатают некоторые модели (Qwen3, DeepSeek-R1).
# Пользователю их не показываем.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # без python-dotenv просто читаем переменные окружения
        return
    load_dotenv()


@dataclass
class LLMSettings:
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0

    @classmethod
    def from_env(cls) -> "LLMSettings":
        _load_env()
        model = os.getenv("LLM_MODEL", "").strip()
        if not model:
            raise RuntimeError("Не задана модель: укажите LLM_MODEL в файле .env")
        return cls(
            base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").strip(),
            # Библиотека openai требует непустой ключ; Ollama его не проверяет.
            api_key=os.getenv("LLM_API_KEY", "").strip() or "not-needed",
            model=model,
            timeout=float(os.getenv("LLM_TIMEOUT", "120")),
        )


class LLMClient:
    """Минимальная обёртка: отправить сообщения — получить текст ответа."""

    def __init__(self, settings: LLMSettings | None = None):
        from openai import OpenAI

        self.settings = settings or LLMSettings.from_env()
        self._client = OpenAI(
            base_url=self.settings.base_url,
            api_key=self.settings.api_key,
            timeout=self.settings.timeout,
        )

    def chat(self, messages: list[dict], *, temperature: float = 0.2,
             max_tokens: int = 1500) -> str:
        response = self._client.chat.completions.create(
            model=self.settings.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content or ""
        return _THINK_RE.sub("", text).strip()


def main() -> int:
    try:
        client = LLMClient()
        print(f"Модель: {client.settings.model} @ {client.settings.base_url}")
        answer = client.chat([
            {"role": "user", "content": "Ответь одним предложением по-русски: ты работаешь?"}
        ])
    except Exception as exc:  # для ручной проверки показываем любую ошибку понятно
        print(f"Не удалось связаться с моделью: {exc}")
        return 1
    print(f"Ответ модели: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
