# Handover Checkpoint: ALL SPRINTS + PRODUCTION HARDENING COMPLETE!

> **Статус**: Все бэкенд-задачи хакатона, P0/P1 Issues (#78, #79, #80, #81, #82, #85), бизнес-KPI и PDF-экспорт полностью реализованы и протестированы.
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
| **Auto-Catalog** | [BI-09/10] Issue #79 | Автоматический вызов `refresh_catalog` при регистрации внешнего PostgreSQL в `POST /api/datasources`. |
| **Запросы** | [BI-35] Issue #60 | Сохранённые аналитические запросы: полный CRUD `/api/queries` и `/api/saved-queries` с персистентным атомарным хранилищем в `data/saved_queries.json`, фильтрация по тегам/источникам/поиску. |
| **CTE-t Фикс** | [BI-35] Issue #85 | Устранена ошибка `relation "t" does not exist` при перевыполнении сохранённых запросов. SQL агента с CTE `t` парсит имя таблицы из заголовка/схемы и исполняется через `PostgresRunner`. |
| **SQL Безопасность** | [BI-35] Issue #80 | Усиленная валидация `validate_sql` перед выполнением сохранённого SQL: блокировка DDL/DML, множественных операторов, запрещённых функций. Plain-SQL выполняется в `READ ONLY` транзакции с таймаутом 20 сек и лимитом 500 строк. |
| **Статусы** | [BI-07] Issue #35 | Статусы выполнения аналитического запроса: этапы выполнения (`steps`) с замером длительности (`duration_ms`), статусы (`status`: completed, clarification, refused, failed) и latency в `ChatResponse`; асинхронный запуск задач с трекингом прогресса (`POST /api/chat/jobs`, `GET /api/chat/jobs/{id}`) и SSE Server-Sent Events стриминг (`GET /api/chat/stream`). |
| **Бизнес-KPI** | [BI-32] Issue #78 | Сервис `backend/app/services/kpis.py` и эндпоинты `GET /api/kpis` (расчёт live-значений через `PostgresRunner`) и `POST /api/kpis/{id}/approve` (персистентный аудит согласования в `data/kpi-definitions.json`). Контрольные значения подтверждены (Выручка: 2 684 326 520,31 руб., CTR: 2.23%). |
| **PDF Экспорт** | [BI-40] Issue #82 | Сервис `backend/app/services/pdf_export.py` и эндпоинты `GET /api/dashboards/{id}/pdf` и `POST /api/export/pdf`. Корпоративное оформление, кириллические шрифты (Arial/DejaVu), таблицы, графики, карточки KPI и SQL. |
| **Регрессия** | Issue #81 | Сквозная интеграционная регрессия подтверждена. Все 183 бэкенд-теста и 110 db-тестов зелёные. |

---

## 2. Результаты верификации тестов

* **Backend**: **183 passed, 2 skipped, 0 failed** (`backend\.venv\Scripts\pytest backend`).
* **Database**: **110 passed, 0 failed** (`backend\.venv\Scripts\python -m unittest discover -s db -p "test_*.py"`).
* **Golden Checks**: **25/25 OK, 0 ошибок** (`backend\.venv\Scripts\python db/run_golden_checks.py`).
* **Frontend**: не изменялся (директория `frontend/` строго изолирована).

---

## 3. Чек-лист: Что нужно сделать вам (User Actions)

1. **[GitHub] Закрыть / подтвердить Issues**:
   - **Issue #79** [P0][BI-09/10] — реализовано (commit `bbb6493`).
   - **Issue #80** [P0][BI-35] — реализовано (валидация `validate_sql` + read-only transaction).
   - **Issue #85** [P0] — реализовано (фикс CTE-t через `PostgresRunner`).
   - **Issue #78** [P1][BI-32] — реализовано (`GET /api/kpis`, `POST /api/kpis/{id}/approve`).
   - **Issue #82** [P1][BI-40] — реализовано (`GET /api/dashboards/{id}/pdf`, `POST /api/export/pdf`).
   - **Issue #81** [P1] — сквозная интеграционная регрессия пройдена (183 backend + 110 db + 25 golden checks).
2. **[Запуск локального стенда для демонстрации]**:
   - База PostgreSQL: убедиться, что служба активна (`127.0.0.1:5432`, логин `bi_reader`).
   - LLM Ollama: `ollama serve` (модель `qwen-bi:latest`).
   - Сервер FastAPI: `backend\.venv\Scripts\uvicorn backend.app.main:app --reload --port 8000`.
   - Фронтенд: `cd frontend && npm run dev`.
