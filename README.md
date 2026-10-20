# Хакатон: AI-Native BI-система

Чат-платформа, где аналитик задаёт вопросы к данным обычными словами,
а LLM-агент должен находить ответ в базе и показывать его текстом,
графиком или дашбордом.

**СУБД проекта — PostgreSQL.** Архитектура:
[docs/architecture.md](docs/architecture.md).

## Структура проекта

| Папка | Назначение |
|---|---|
| `backend/` | API и LLM-агент (в разработке) |
| `frontend/` | Чат и визуализация, пока с демо-ответами |
| `db/` | Локальный PostgreSQL, загрузка CSV, проверка схемы |
| `docs/` | Архитектура, данные, сценарии и формат API |
| `scripts/` | Скачивание датасетов |

## Быстрый старт: база данных

```bash
git clone https://github.com/PavelKonoplev-PPP/hackathon.git
cd hackathon
cp .env.example .env
# Измените POSTGRES_PASSWORD в .env
docker compose up -d postgres
./scripts/fetch_data.sh

python3 -m venv .venv
source .venv/bin/activate
pip install -r db/requirements.txt
python db/load_csv.py
python db/inspect_db.py
```

Условия CSV, соглашения по именам таблиц и подключение backend описаны
в [db/README.md](db/README.md).

## Запуск интерфейса (демо-режим)

```bash
cd frontend
npm install
npm run dev
```

Откройте http://localhost:3000. Пока там заранее подготовленные примеры;
реальный backend ещё не подключён.

Правила работы команды: [CONTRIBUTING.md](CONTRIBUTING.md).
