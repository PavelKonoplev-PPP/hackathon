# LLM-модуль

Подключение к модели через OpenAI-совместимый API и сценарии агента.

| Файл | Что делает |
|---|---|
| `client.py` | Отправляет сообщения модели, получает текст. Настройки — из `.env` |
| `catalog_summary.py` | Сжимает каталог данных (`db/catalog_db.py`) в короткую сводку для модели |
| `describe.py` | Сценарий 1 (#3): описание неизвестных данных простым языком |
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
python -m unittest discover -s backend -p 'test_*.py' -t .
```

Принцип: факты (строки, типы, пропуски) считает код, модель только
пересказывает их. Так слабая модель (до 80B на проверке, 3B при разработке)
не выдумывает цифры.
