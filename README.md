# AI-Native BI: аналитика данных в диалоге

Платформа для продуктовых аналитиков и руководителей: вопрос к данным задаётся обычным языком,
а система отвечает текстом, таблицей, графиком и показывает SQL, по которому всё посчитано.
Результаты собираются в дашборды, выгружаются в Excel, PNG и PDF. Платформа работает локально,
без облачных сервисов, с открытой LLM.

Кейс: «AI-Native BI Система» (ИАЦ). Данные кейса — 9 CSV, около 5,4 млн строк.

## Возможности

**Диалог с данными**
- описание неизвестных данных простым языком: таблицы, поля, период, качество;
- ответы на вопросы с SQL-запросом и этапами выполнения; уточняющие вопросы при неоднозначности;
- контекст диалога («а теперь только за сентябрь»), отказ на изменение данных и вопросы вне данных;
- сравнение периодов и сегментов, аномалии, сезонность, корреляции, прогноз и сценарии «что если»;
- остановка ответа, история диалогов, поиск, сохранённые запросы с повторным запуском.

**Визуализация и дашборды**
- автоматический выбор графика; смена вида в один клик или командой в чате;
- несколько метрик — на одном графике или на отдельных, каждая со своей шкалой;
- объединение графиков из разных ответов и разбивка по признаку (каналу, городу, категории);
- KPI-карточки; дашборды из виджетов с перемещением, изменением размера и общим фильтром периода;
- экспорт: таблицы в Excel (суммы остаются числами), графики в PNG, дашборд в PDF.

**Данные и безопасность**
- загрузка CSV с предпросмотром, определением разделителя и кодировки, защитой от перезаписи;
- подключение внешней PostgreSQL с проверкой соединения и автоматическим каталогом;
- агент работает под ролью PostgreSQL только для чтения; SQL проверяется до выполнения
  (один `SELECT`, без опасных функций), запросы с таймаутом и лимитом строк;
- пароли внешних баз хранятся зашифрованными и не возвращаются в интерфейс; защита от SSRF.

## Архитектура

```
React (Vite, ECharts)  →  FastAPI  →  LLM-агент  →  PostgreSQL (схема raw, роль bi_reader)
                                         ↑
                           OpenAI-совместимый API (Ollama / vLLM)
```

Модель не считает цифры: она выбирает таблицу и пишет SQL по описанию колонок, код проверяет
запрос, база выполняет его только на чтение, модель пересказывает готовый результат.
Подробнее: [docs/architecture.md](docs/architecture.md), формат API — [docs/api-contract.md](docs/api-contract.md).

| Слой | Технологии |
|---|---|
| Интерфейс | React 18, Vite, Apache ECharts, jsPDF |
| API | Python 3.12, FastAPI, Pydantic |
| Агент | OpenAI-совместимый клиент, локальная модель `qwen2.5:3b` (`backend/llm/Modelfile`) |
| Данные | PostgreSQL 16, psycopg |

## Запуск локально

Нужны: Docker (или PostgreSQL 16), Python 3.12, Node.js 18+, [Ollama](https://ollama.com).

**1. Настройки**

```bash
git clone https://github.com/PavelKonoplev-PPP/hackathon.git
cd hackathon
cp .env.example .env   # задайте пароли POSTGRES_PASSWORD и POSTGRES_READER_PASSWORD
```

**2. База и данные**

```bash
docker compose up -d postgres
./scripts/fetch_data.sh                       # 9 CSV кейса в папку Исходники/
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r db/requirements.txt -r backend/requirements.txt
python db/load_csv.py                         # CSV загружаются как есть, в TEXT
```

Роль только для чтения для агента — `db/setup_readonly.sql`, порядок и проверка описаны
в [db/README.md](db/README.md).

**3. Каталог данных** (описания таблиц для агента)

```bash
python db/profile_db.py --schema raw --sample-size 300 --no-examples --output data/all-profile.json
python db/audit_profile.py --output data/type-audit.json
python db/catalog_db.py --profile data/all-profile.json --audit data/type-audit.json --output data/schema-catalog.json
python db/discover_relationships.py --catalog data/schema-catalog.json --output data/relationships.json
python db/audit_identifiers.py --relationships data/relationships.json --output data/unprefixed-id-audit.json
python db/relationship_catalog.py --relationships data/relationships.json \
  --id-audit data/unprefixed-id-audit.json \
  --group "data_08_Фоновые_задания:Код цепочки запусков" --output data/relationship-catalog.json
```

CSV, загруженные через интерфейс, попадают в каталог автоматически.

**4. Модель**

```bash
ollama pull qwen2.5:3b
ollama create qwen-bi -f backend/llm/Modelfile   # в .env: LLM_MODEL=qwen-bi
```

Модель заменяется одной настройкой (`LLM_BASE_URL`, `LLM_MODEL`) — подойдёт любая
OpenAI-совместимая, в том числе на vLLM.

**5. Backend и интерфейс**

```bash
uvicorn backend.app.main:app --port 8000      # из корня репозитория
```

```bash
cd frontend
npm install
echo "VITE_MOCK=0" > .env                     # работа с настоящим backend
npm run dev
```

Откройте http://localhost:3000. Без backend интерфейс можно посмотреть в демо-режиме
(`VITE_MOCK=1`, значение по умолчанию): ответы-заглушки, дашборды хранятся в браузере.

## Тесты

```bash
python -m pytest -q backend                               # API и агент
python -m unittest discover -s db -p 'test_*.py'          # загрузка, профилирование, каталог
python db/run_golden_checks.py                            # контрольные расчёты на данных
python -m backend.llm.eval                                # точность ответов агента через API
```

CI на каждом pull request: тесты загрузчика, unit-тесты агента, сборка интерфейса,
запрет на CSV и большие файлы в репозитории.

## Структура

| Папка | Что внутри |
|---|---|
| `frontend/` | интерфейс: чат, графики, дашборды, данные ([frontend/README.md](frontend/README.md)) |
| `backend/app/` | FastAPI: чат и задачи, дашборды, запросы, источники, CSV, KPI, PDF |
| `backend/llm/` | агент: выбор таблицы, SQL, проверки, сравнение, прогноз ([backend/llm/README.md](backend/llm/README.md)) |
| `db/` | загрузка CSV, профилирование, каталог, роль только для чтения ([db/README.md](db/README.md)) |
| `docs/` | архитектура, API, словарь данных, сценарии, проверка интерфейса, сценарий показа |
| `scripts/` | скачивание данных кейса |

## Сценарии кейса

Статус всех 10 сценариев и где они реализованы — [docs/scenarios.md](docs/scenarios.md).
Сценарий показа — [docs/demo-script.md](docs/demo-script.md).

## Команда

| Участник | Зона ответственности |
|---|---|
| [PavelKonoplev-PPP](https://github.com/PavelKonoplev-PPP) | капитан, интерфейс, интеграция и проверка |
| [LTB77-7](https://github.com/LTB77-7) | LLM-агент: SQL, контекст, аналитика, оценка точности |
| [Pluray](https://github.com/Pluray) | backend: API, источники, дашборды, запросы, экспорт |
| [mertensmordecai-svg](https://github.com/mertensmordecai-svg) | данные: загрузка, профилирование, каталог, контрольные расчёты |

Правила работы: [CONTRIBUTING.md](CONTRIBUTING.md).
