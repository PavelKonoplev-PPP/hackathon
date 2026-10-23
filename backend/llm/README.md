# LLM-модуль

Подключение к модели через OpenAI-совместимый API и сценарии агента.

| Файл | Что делает |
|---|---|
| `client.py` | Отправляет сообщения модели, получает текст. Настройки — из `.env` |
| `catalog_summary.py` | Сжимает каталог данных (`db/catalog_db.py`) в короткую сводку для модели |
| `describe.py` | Сценарий 1 (#3): описание неизвестных данных простым языком |
| `ask.py` | Сценарий 2 (#4): вопрос → выбор таблицы → SQL → проверка → выполнение → ответ |
| `sql_guard.py` | Проверка SQL модели: только один SELECT из `t`, без опасных функций и чужих таблиц |
| `sql_runner.py` | Таблица `t` с приведёнными типами (по каталогу) и выполнение в read-only транзакции |
| `test_ask.py` | Тесты сценария 2 без модели и без БД |
| `test_describe.py` | Тесты без модели и без БД |
| `Modelfile` | Локальная модель с большим контекстом: `ollama create qwen-bi -f backend/llm/Modelfile` |

## Запуск (из корня проекта)

```bash
pip install -r backend/requirements.txt
ollama create qwen-bi -f backend/llm/Modelfile   # один раз
# В .env: LLM_BASE_URL=http://localhost:11434/v1 и LLM_MODEL=qwen-bi

python -m backend.llm.client                       # модель отвечает?
python -m backend.llm.describe --dry-run           # что уйдёт в промпт
python -m backend.llm.describe \
  --catalog data/schema-catalog.json \
  --relationships data/relationship-catalog.json   # описание данных
python -m backend.llm.ask "Какой канал продаж приносит больше всего выручки?"
python -m unittest discover -s backend -p 'test_*.py' -t .
```

Принцип: факты (строки, типы, пропуски) считает код, модель только
пересказывает их. Так слабая модель (до 80B на проверке, 3B при разработке)
не выдумывает цифры.

## Сценарий 2: как устроено

Модель пишет SQL не к сырой таблице (там всё TEXT, десятичные запятые),
а к подготовленной таблице `t`:

```sql
WITH t AS (SELECT <безопасные приведения по каталогу> FROM raw."таблица")
SELECT * FROM (<запрос модели>) AS q LIMIT 201
```

Числа и даты в `t` уже нужного типа, коды и категории остаются текстом.
Для категорий модели показываются реальные значения (для верных `WHERE`).
Ошибку проверки или базы модель получает обратно и исправляет запрос (до 3 попыток).

Для backend:

```python
from backend.llm.ask import answer_question
from backend.llm.sql_runner import PostgresRunner

runner = PostgresRunner(conn)          # conn — psycopg-соединение под bi_reader
result = answer_question(message, catalog, runner)   # dict по docs/api-contract.md
```
