# Handover Checkpoint: ALL SPRINTS + PRODUCTION HARDENING COMPLETE!

> **Статус**: Все спринты бэкенда хакатона и интеграционные требования безопасности полностью реализованы и протестированы.
> **Ветка**: `main`
> **База данных**: PostgreSQL 16 (схема `raw`, 9 таблиц, 5 376 000 строк) + внешние PostgreSQL (read-only)
> **LLM**: Ollama `qwen-bi:latest` (локально на GPU AMD Radeon RX 6600, порт 11434, ~65 токенов/сек)

---

## 1. Сводка выполненных задач бэкенда (Roadmap)

| Спринт / Модуль | Задача / Сценарий | Что реализовано |
|---|---|---|
| **Спринт 0** | [BI-11] Issue #39 | Эндпоинт каталога `GET /api/schema`: таблицы, строки, типы, warning'и качества, связи, фикс `fcntl` на Windows. |
| **Спринт 1** | [BI-05] Issue #33 | Обработка невозможных запросов (NPS, ROI), мутаций DDL/DML, подсказки и 200 ответы по Критерию №8. |
| **Спринт 2** | [BI-12] Issue #40 | Скоупинг источников и таблиц в `ChatRequest`, изоляция каталога от кросс-джойнов, таргетированное описание. |
| **Спринт 3** | [BI-09] Issue #37 | Прямое подключение к внешней PostgreSQL (`POST /api/datasources/test`, `GET /api/datasources`), маскирование паролей. |
| **Спринт 4** | [BI-15] Issue #43 | Персистентный CRUD аналитических дашбордов (`/api/dashboards`) с атомарным хранилищем в `data/dashboards.json`. |
| **Спринт 5** | Сценарии 7–10 | Продвинутая аналитика: сезонность (Сценарий 7), сегментация (Сценарий 8), корреляции Пирсона и лаги (Сценарий 9), линейная экстраполяция (Сценарий 10). |
| **Hardening** | BI-08 / BI-10 | Эндпоинты `POST /api/csv/preview` и `POST /api/csv/upload`: лимит 100 МБ, авто-определение разделителя и кодировки, защита от перезаписи (409 Conflict), вызов `refresh_catalog`. |
| **Security & BI-09** | PR #73 / Интеграция | Влит PR #73 (`db/external_catalog.py`). Секреты в `data/datasources.json` теперь шифруются алгоритмом Fernet (AES-128-CBC + HMAC-SHA256). Защита от SSRF (блокировка 169.254.169.254, link-local, multicast). Реальное переключение подключения и каталога по `datasource_id`. Изоляция контекста диалога между источниками. |

---

## 2. Результаты верификации тестов

* **Backend**: **173 passed, 2 skipped, 0 failed** (`backend\.venv\Scripts\pytest backend`).
* **Database**: **100 passed, 0 failed** (`backend\.venv\Scripts\python -m unittest discover -s db -p "test_*.py"`).
* **Golden Checks**: **25/25 OK, 0 ошибок** (`backend\.venv\Scripts\python db/run_golden_checks.py`).
* **Frontend**: не изменялся (директория `frontend/` изолирована).

---

## 3. Чек-лист: Что нужно сделать вам (User Actions)

1. **[GitHub] Закрыть / подтвердить PR и Issue**:
   - **PR #73** (Изолированные каталоги внешних PostgreSQL — влит в main).
   - **PR #77** (Фронтенд выбор источника — влит).
   - Закрыть в веб-интерфейсе Issue #39, #33, #40, #37, #43.
2. **[Запуск локального стенда для демонстрации]**:
   - База PostgreSQL: убедиться, что служба активна (`127.0.0.1:5432`, логин `bi_reader`).
   - LLM Ollama: `ollama serve` (модель `qwen-bi:latest`).
   - Сервер FastAPI: `backend\.venv\Scripts\uvicorn backend.app.main:app --reload --port 8000`.
   - Фронтенд: `cd frontend && npm run dev`.
