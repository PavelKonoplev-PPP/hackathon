# Контракт API: фронтенд ↔ бэкенд (предложение)

Фронтенд написан под этот формат. Если бэкенд меняет формат, сначала правим этот файл.

## POST /api/chat

Запрос:

```json
{
  "session_id": "uuid",
  "message": "Продажи по каналам",
  "datasource_id": "raw",
  "table_name": "data_01_Продажи"
}
```

Параметры запроса:
- `session_id` (строка, обязательный): создаёт фронтенд; бэкенд хранит по нему память диалога.
- `message` (строка, обязательный): текст вопроса пользователя к аналитической системе.
- `datasource_id` (строка, необязательный): идентификатор источника данных/схемы (например, `"raw"` или имя таблицы). Если указан, анализ изолируется в рамках выбранного источника.
- `table_name` (строка, необязательный): имя или отображаемое название активной таблицы (например, `"data_01_Продажи"` или `"Продажи"`). При указании таблицы бэкенд строго изолирует каталог и исключает межтабличные JOIN.

Ответ (все поля, кроме `answer`, необязательные):

```json
{
  "answer": "Текст ответа простым языком",
  "chart": {
    "type": "bar | line | pie",
    "title": "Заголовок графика",
    "x_label": "Подпись оси X",
    "y_label": "Подпись оси Y",
    "categories": ["A", "B", "C"],
    "series": [{ "name": "Серия", "data": [1, 2, 3] }]
  },
  "table": { "columns": ["Показатель", "Значение"], "rows": [["Строк", "500 000"]] },
  "sql": "SELECT ...",
  "clarification": { "question": "Что вы имели в виду?", "options": ["Вариант 1", "Вариант 2"] }
}
```

- `chart` рисуется на фронтенде; для `pie` берётся первая серия.
- `table` показывается под текстом.
- `sql` скрыт под кнопкой «Как посчитано».
- `clarification` — кнопки для уточнения неясного вопроса; нажатие отправляет выбранный вариант как новое сообщение.

Ошибка: любой статус не 2xx — фронтенд покажет «Не удалось получить ответ».

## GET /api/schema (алиас: GET /schema)

Каталог доступных источников, таблиц, колонок, типов, числа строк, предупреждений качества и связей (Issue #39 / BI-11).

Ответ:

```json
{
  "schema_name": "raw",
  "sources": ["raw", "data_01_Продажи"],
  "tables": [
    {
      "name": "data_01_Продажи",
      "display_name": "Продажи",
      "row_count": 500000,
      "columns": [
        {
          "name": "Код позиции",
          "type": "identifier",
          "postgres_type": "text",
          "inferred_type": "identifier",
          "nullable": true,
          "warning": null
        }
      ],
      "warnings": []
    }
  ],
  "relationships": [
    {
      "source_table": "data_01_Продажи",
      "source_column": "Код исходной позиции",
      "target_table": "data_01_Продажи",
      "target_column": "Код позиции",
      "kind": "self_reference_candidate",
      "can_auto_join": false,
      "policy": "manual_approval_required",
      "source": { "table": "data_01_Продажи", "column": "Код исходной позиции" },
      "target": { "table": "data_01_Продажи", "column": "Код позиции" }
    }
  ],
  "warnings": [
    "Достоверность типов эвристическая",
    "Связи между таблицами не верифицированы бизнесом (требуется ручное подтверждение)"
  ]
}
```

## Источники данных (Datasources) — Sprint 3 (BI-09 / Issue #37)

### 1. POST /api/datasources/test (алиас: POST /datasources/test)

Проверка доступности и прав доступа к внешней базе данных PostgreSQL. Подключение выполняется с таймаутом 3 сек и строгим флагом `default_transaction_read_only=on`.

Запрос:

```json
{
  "host": "postgres.internal",
  "port": 5432,
  "dbname": "analytics_db",
  "user": "bi_reader",
  "password": "secret_password",
  "schema_name": "public",
  "sslmode": "prefer"
}
```

Параметры запроса:
- `host` (строка, обязательный): IP или hostname сервера PostgreSQL.
- `port` (целое число, по умолчанию 5432): порт СУБД.
- `dbname` (строка, обязательный, алиас `database`): имя базы данных.
- `user` (строка, обязательный, алиас `username`): имя пользователя PostgreSQL.
- `password` (строка, необязательный): пароль. Никогда не возвращается в ответах.
- `schema_name` (строка, по умолчанию `"public"`): целевая схема для поиска таблиц.
- `sslmode` (строка, необязательный): режим SSL (`prefer`, `require`, `disable`).

Пример успешного ответа (HTTP 200):

```json
{
  "status": "ok",
  "success": true,
  "latency_ms": 14.5,
  "tables_count": 3,
  "tables": ["orders", "users", "products"],
  "message": "Успешное подключение. Доступно таблиц: 3"
}
```

Пример ответа при ошибке подключения (HTTP 200, без утечки паролей):

```json
{
  "status": "error",
  "success": false,
  "latency_ms": 3002.1,
  "tables_count": 0,
  "tables": [],
  "message": "Ошибка подключения к PostgreSQL: connection failed: timeout expired"
}
```

### 2. GET /api/datasources (алиас: GET /datasources)

Получение списка всех доступных источников данных (включая встроенный датасет `"raw"`). Пароли строго маскируются (`"******"`).

Пример ответа (HTTP 200):

```json
[
  {
    "id": "raw",
    "name": "Основная база данных (raw)",
    "type": "postgresql",
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "hackathon",
    "user": "bi_reader",
    "password": "******",
    "schema_name": "raw",
    "tables_count": 9,
    "tables": [
      "data_01_Продажи",
      "data_02_Техническая_поддержка",
      "data_03_Программа_лояльности",
      "data_04_Рекламные_кампании",
      "data_05_Опросы_клиентов",
      "data_06_Отзывы_клиентов",
      "data_07_Служба_доставки",
      "data_08_Возвраты_товаров",
      "data_09_Каталог_товаров"
    ],
    "is_default": true,
    "created_at": "2026-10-25T00:00:00Z",
    "description": "Встроенный локальный датасет хакатона (PostgreSQL, схема raw)"
  },
  {
    "id": "ext_analytics",
    "name": "Внешняя аналитическая БД",
    "type": "postgresql",
    "host": "postgres.internal",
    "port": 5432,
    "dbname": "analytics_db",
    "user": "bi_reader",
    "password": "******",
    "schema_name": "public",
    "tables_count": 3,
    "tables": ["orders", "users", "products"],
    "is_default": false,
    "created_at": "2026-10-25T15:30:00Z",
    "description": "Продакшн реплика"
  }
]
```

### 3. POST /api/datasources (алиас: POST /datasources)

Регистрация нового внешнего источника данных. Сначала валидирует подключение через `test_postgres_connection`. При успешном подключении сохраняет конфигурацию в `data/datasources.json` и возвращает зарегистрированный источник с маскированным паролем.

Пример запроса:

```json
{
  "name": "Внешняя аналитическая БД",
  "host": "postgres.internal",
  "port": 5432,
  "dbname": "analytics_db",
  "user": "bi_reader",
  "password": "secret_password",
  "schema_name": "public",
  "description": "Продакшн реплика"
}
```

Параметры запроса:
- `name` (строка, обязательный): читаемое название источника.
- `host` (строка, обязательный): адрес сервера PostgreSQL.
- `port` (целое число, по умолчанию 5432): порт сервера.
- `dbname` (строка, обязательный, алиас `database`): имя базы данных.
- `user` (строка, обязательный, алиас `username`): имя пользователя.
- `password` (строка, по умолчанию `""`): пароль доступа.
- `schema_name` (строка, по умолчанию `"public"`): схема PostgreSQL.
- `description` (строка, необязательный): описание источника.
- `id` (строка, необязательный): пользовательский идентификатор источника. Если не указан, генерируется автоматически из имени через транслитерацию. Идентификатор `"raw"` зарезервирован для встроенного датасета и не может быть использован для внешних источников.

Пример успешного ответа (HTTP 200):

```json
{
  "id": "vneshnyaya_analiticheskaya_bd",
  "name": "Внешняя аналитическая БД",
  "type": "postgresql",
  "host": "postgres.internal",
  "port": 5432,
  "dbname": "analytics_db",
  "user": "bi_reader",
  "password": "******",
  "schema_name": "public",
  "tables_count": 3,
  "tables": ["orders", "users", "products"],
  "is_default": false,
  "created_at": "2026-10-25T15:30:00Z",
  "description": "Продакшн реплика"
}
```

Пример ошибки валидации (HTTP 400):

```json
{
  "detail": "Ошибка подключения к PostgreSQL: connection failed: password authentication failed"
}
```

## Аналитические дашборды (BI-15 / Issue #43 / Сценарий №5)

Система предоставляет полноценный REST API для управления сохранёнными аналитическими дашбордами с автоматической генерацией и персистентным хранением конфигурации виджетов (`data/dashboards.json`).

### 1. GET /api/dashboards (алиас: GET /dashboards)

Получение списка всех сохранённых дашбордов (краткая сводка для меню/списка).

Ответ (HTTP 200):
```json
[
  {
    "id": "dash_prodazhi_i_vyruchka",
    "title": "Продажи и выручка",
    "description": "Сводный мониторинг каналов продаж и среднего чека",
    "datasource_id": "raw",
    "widget_count": 2,
    "created_at": "2026-10-25T16:00:00Z",
    "updated_at": "2026-10-25T16:00:00Z"
  }
]
```

### 2. POST /api/dashboards (алиас: POST /dashboards)

Создание и сохранение нового дашборда с набором аналитических виджетов (графики, таблицы, метрики).

Пример запроса:
```json
{
  "title": "Продажи и выручка",
  "description": "Сводный мониторинг каналов продаж",
  "datasource_id": "raw",
  "widgets": [
    {
      "id": "w_sales_bar",
      "title": "Выручка по каналам",
      "type": "chart",
      "sql": "SELECT \"Канал продажи\", SUM(...) ...",
      "chart": {
        "type": "bar",
        "title": "Выручка по каналам продаж",
        "x": ["Интернет-магазин", "Розничный магазин"],
        "series": [{"name": "Выручка", "data": [1200000, 850000]}]
      }
    }
  ]
}
```

Ответ (HTTP 201 Created): возвращает полный объект `DashboardDetail` с присвоенным `id`.

### 3. GET /api/dashboards/{dashboard_id} (алиас: GET /dashboards/{dashboard_id})

Загрузка полной конфигурации дашборда со всеми виджетами по идентификатору.

Ответ (HTTP 200): объект `DashboardDetail`.
Ошибка (HTTP 404): если дашборд с указанным `id` не найден.

### 4. PUT /api/dashboards/{dashboard_id} (алиас: PUT /dashboards/{dashboard_id})

Обновление заголовка, описания, источника или списка виджетов дашборда.

Ответ (HTTP 200): обновленный объект `DashboardDetail`.
Ошибка (HTTP 404): если дашборд не найден.

### 5. DELETE /api/dashboards/{dashboard_id} (алиас: DELETE /dashboards/{dashboard_id})

Удаление дашборда по идентификатору.

Ответ (HTTP 204 No Content).
Ошибка (HTTP 404): если дашборд не найден.


### Как дашборды использует фронтенд

В демо-режиме (`VITE_MOCK=1`) те же операции идут в localStorage браузера.

Виджет — результат ответа агента (`answer`, `chart`, `table`, `sql`). Тип: `chart` (есть график),
`metric` (однострочная таблица с числами → KPI-карточки), `table`, `text`. Поле `grid`:

```json
{ "w": 6, "h": 2, "view": "line" }
```

- `w` — ширина в колонках из 12 (3–12), `h` — высота в рядах по 160 px (1–4);
- `view` — выбранный пользователем вид (`bar | line | pie | table`), `null` — как прислал агент.

Порядок виджетов в массиве — порядок на экране; при перестановке, изменении размера, смене вида
и переименовании фронтенд отправляет `PUT` с полным списком виджетов.

Фильтр периода (BI-34) хранится в браузере и применяется к уже посчитанным результатам, у которых
категории графика или первая колонка таблицы — даты (`YYYY-MM`, `YYYY-MM-DD`, `YYYY-MM-DD HH:MM:SS`).
Повторного SQL-запроса нет.

Команда в чате «Собери дашборд» и кнопка «Собрать дашборд» создают дашборд (`POST`) из всех графиков
и таблиц текущего диалога на стороне фронтенда; агент для этого не вызывается.

### Источник данных в чате (фронтенд)

Фронтенд берёт список из `GET /api/datasources` и показывает выбор источника в шапке чата,
если источников больше одного. Источник хранится у диалога и уходит в `POST /api/chat` полем
`datasource_id`. Смена источника в диалоге с сообщениями начинает **новый** диалог с новым
`session_id`: контекст и кэши прошлой базы в новый диалог не попадают. Дашборд, собранный из
диалога, получает тот же `datasource_id`.

Круговая диаграмма на фронтенде доступна только для одной серии, не больше 12 частей и без
отрицательных значений; иначе кнопка «Круговая» неактивна, а команда «покажи кругом»
объясняет причину и показывает гистограмму.


## Загрузка и предпросмотр CSV (CSV Upload & Ingestion)

### 1. POST /api/csv/preview (алиас: POST /csv/preview)

Определение разделителя (`;`, `,`, `\t`, `|`), кодировки (`utf-8-sig`, `utf-8`, `cp1251`), нормализация названий колонок и вывод первых `preview_rows` строк без записи в БД.

Content-Type: `multipart/form-data`
Параметры формы:
- `file` (UploadFile, обязательный): CSV-файл (размер до 100 МБ).
- `delimiter` (строка, необязательный): принудительный разделитель.
- `encoding` (строка, по умолчанию `"utf-8-sig"`): кодировка файла.
- `preview_rows` (целое число, по умолчанию 5): количество строк предпросмотра (до 20).

Пример ответа (HTTP 200):
```json
{
  "filename": "orders.csv",
  "schema_name": "raw",
  "table_name": "data_orders",
  "encoding": "utf-8-sig",
  "delimiter": ";",
  "original_headers": ["Номер заказа", "Город", "Сумма"],
  "columns": ["Номер заказа", "Город", "Сумма"],
  "rows": [["001", "Москва", "100,50"], ["002", "СПб", "200,00"]],
  "warnings": []
}
```

### 2. POST /api/csv/upload (алиасы: POST /api/data/upload, POST /csv/upload)

Потоковая загрузка CSV в схему `raw`, защита от случайной перезаписи и автоматический запуск инкрементального обновления каталога (`refresh_catalog`).

Content-Type: `multipart/form-data`
Параметры формы:
- `file` (UploadFile, обязательный): CSV-файл (размер до 100 МБ).
- `delimiter` (строка, необязательный): разделитель.
- `encoding` (строка, по умолчанию `"utf-8-sig"`).
- `replace` (boolean, по умолчанию `false`): разрешение перезаписи существующей таблицы. Если `false` и таблица уже существует в схеме `raw`, возвращается HTTP 409 Conflict.

Пример ответа (HTTP 200):
```json
{
  "status": "ok",
  "schema_name": "raw",
  "table_name": "data_orders",
  "row_count": 1000,
  "columns": ["Номер заказа", "Город", "Сумма"],
  "warnings": [],
  "catalog_refreshed": true,
  "catalog_status": "ready",
  "catalog_report": {
    "mode": "incremental",
    "tables": 10,
    "columns": 55,
    "unverified_columns": 0,
    "failed_audits": 0
  },
  "message": "Таблица успешно импортирована, каталог обновлён."
}
```
Статусы каталогизации (`catalog_status`):
- `"ready"`: профилирование и аудит типов завершены успешно (`failed_audits == 0`, `catalog_refreshed = true`).
- `"partial"`: импорт завершён, но аудит типов выявил ошибки (`failed_audits > 0`, `catalog_refreshed = false`, предупреждения в `warnings`).
- `"failed"`: импорт таблицы успешно зафиксирован в БД, но профилирование завершилось сбоем (`catalog_refreshed = false`). Ошибка не откатывает импортированную таблицу и возвращает HTTP 200 с предупреждением в `warnings`.


---

## Сохранённые аналитические запросы (BI-35 / Issue #60)

### 1. GET /api/queries (алиасы: `/api/saved-queries`, `/queries`)
Возвращает список сохранённых запросов.
Query параметры:
- `datasource_id` (необязательный): фильтр по источнику.
- `tag` (необязательный): фильтр по тегу.
- `search` (необязательный): подстрочный поиск по заголовку, вопросу, описанию и SQL.

### 2. POST /api/queries (алиасы: `/api/saved-queries`, `/queries`)
Создание нового сохранённого запроса.
Тело (JSON):
```json
{
  "title": "Выручка по каналам",
  "description": "Агрегированная выручка за всё время",
  "question": "Какой канал продаж приносит больше всего выручки?",
  "sql": "SELECT channel, SUM(amount) AS revenue FROM t GROUP BY channel ORDER BY revenue DESC",
  "datasource_id": "raw",
  "table_name": "data_03_orders",
  "tags": ["revenue", "sales", "kpi"],
  "chart_config": {"type": "bar"}
}
```

### 3. GET /api/queries/{id}
Получение детальной информации о сохранённом запросе.

### 4. PUT /api/queries/{id}
Обновление полей сохранённого запроса.

### 5. DELETE /api/queries/{id}
Удаление сохранённого запроса (HTTP 204).

### 6. POST /api/queries/{id}/execute
Выполнение сохранённого запроса с возвратом актуальных данных таблицы и графика.

---

## Статусы выполнения аналитического запроса (BI-07 / Issue #35)

### 1. Статусы и шаги в ответах POST /api/chat
В ответ `ChatResponse` включены поля жизненного цикла:
- `status`: `"completed"` | `"clarification"` | `"refused"` | `"failed"`
- `steps`: массив шагов выполнения с замерами времени (`duration_ms`):
  - `parse_intent`: анализ намерения и проверка безопасности (DDL/DML, out-of-scope).
  - `route_table`: выбор таблицы и сопоставление схемы.
  - `generate_sql`: генерация и валидация SQL.
  - `execute_sql`: выполнение запроса в PostgreSQL.
  - `synthesize`: построение ответа, таблицы и графика.
- `execution_time_ms`: общее время обработки запроса в миллисекундах.

### 2. Асинхронное выполнение: POST /api/chat/jobs (алиас: POST /api/queries/jobs)
Приём запроса на фоновое выполнение с возвратом `job_id` (HTTP 202 Accepted).
```json
{
  "job_id": "c1f7a28e9b14",
  "status": "pending",
  "message": "Query execution job submitted successfully"
}
```

### 3. Опрос статуса задачи: GET /api/chat/jobs/{job_id}
Возвращает текущую фазу, процент выполнения и шаги:
```json
{
  "job_id": "c1f7a28e9b14",
  "status": "running",
  "current_step": "Генерация аналитического SQL",
  "progress_pct": 60,
  "steps": [],
  "result": null,
  "execution_time_ms": null
}
```

### 4. SSE-стриминг шагов: GET /api/chat/stream?message=...
Потоковая передача событий Server-Sent Events по мере прохождения фаз пайплайна и выдача финального результата.

---

## Как новые эндпоинты использует фронтенд

- **Вопрос в чате** уходит через `POST /api/chat/jobs`; фронтенд опрашивает `GET /api/chat/jobs/{job_id}`
  (первый раз через 250 мс, дальше каждые 700 мс) и показывает `current_step` и `progress_pct`. При `failed` —
  текст `error`, через 3 минуты — понятное сообщение о таймауте. Если `/api/chat/jobs` отвечает 404/405,
  используется обычный `POST /api/chat`. Под ответом — «Этапы: N · время» со списком `steps`.
- **Страница «Данные»** (`#/data`): `POST /api/csv/preview` сразу после выбора файла (и при смене
  разделителя/кодировки), затем `POST /api/csv/upload` с разделителем и кодировкой из предпросмотра.
  На 409 — предложение перезаписать (`replace=true`) с подтверждением. Лимит 100 МБ проверяется ещё в браузере.
  Список таблиц — `GET /api/schema`; «Спросить» открывает новый диалог с `table_name` этой таблицы.
- **Сохранённые запросы**: «Сохранить запрос» под ответом с SQL → `POST /api/queries`
  (`title`, `question`, `sql`, `datasource_id`, `table_name`, `chart_config.type` — выбранный вид).
  Раздел «Запросы» в боковой панели: запуск — `POST /api/queries/{id}/execute` (результат в текущий диалог),
  переименование — `PUT`, удаление — `DELETE`. В демо-режиме запросы хранятся в localStorage.


---

## Бизнес-KPI (BI-32 / Issue #78)

Эндпоинты для получения проверенных бизнес-метрик из технического каталога схемы с вычислением живых значений и аудитом согласования.

### 1. GET /api/kpis
Возвращает список бизнес-KPI для указанного источника данных.
- **Параметры**:
  - `datasource_id` (string, default `"raw"`): идентификатор источника данных.
  - `calculate` (boolean, default `true`): вычислять ли актуальные значения метрик в базе данных через read-only `PostgresRunner`.
- **Ответ (200 OK)**:
```json
{
  "datasource_id": "raw",
  "schema_name": "raw",
  "kpis": [
    {
      "id": "sales_revenue",
      "schema": "raw",
      "table": "data_01_Продажи",
      "title": "Сумма с учётом скидки",
      "unit": "RUB",
      "description": "Сумма строк по формуле количество × цена × (1−скидка/100).",
      "expression": "SUM(\"Количество, шт.\" * \"Цена до скидки за единицу, руб.\" * (1 - \"Скидка, %\" / 100.0))",
      "required_columns": ["Количество, шт.", "Цена до скидки за единицу, руб.", "Скидка, %"],
      "status": "proposed",
      "requires_business_approval": true,
      "warnings": [],
      "value": 2684326520.31,
      "formatted_value": "2 684 326 520,31 руб."
    }
  ],
  "summary": {
    "proposed": 8,
    "approved": 0,
    "unavailable": 0,
    "total": 8
  }
}
```

### 2. POST /api/kpis/{kpi_id}/approve
Согласование (sign-off) предложенного KPI ответственным лицом. Сохраняется персистентно в `data/kpi-definitions.json`.
- **Тело запроса**:
```json
{
  "approved_by": "Chief Financial Officer",
  "comment": "Формула согласована с финдепартаментом"
}
```
- **Ответ (200 OK)**: обновлённый объект `BusinessKpiItem` со статусом `"approved"` и `"requires_business_approval": false`.

---

## Экспорт отчётов в PDF (BI-40 / Issue #82)

Сквозной экспорт аналитических данных, дашбордов и результатов запросов в PDF с поддержкой кириллицы и корпоративного оформления.

### 1. GET /api/dashboards/{dashboard_id}/pdf
Генерация и потоковая выгрузка PDF-отчёта по сохранённому дашборду.
- **Ответ (200 OK)**: бинарный поток `application/pdf`, заголовок `Content-Disposition: attachment; filename="dashboard_{id}.pdf"`.

### 2. POST /api/export/pdf
Генерация произвольного аналитического отчёта (таблицы, графики, KPI, SQL, выводы).
- **Тело запроса**:
```json
{
  "title": "Сводный финансовый отчёт",
  "subtitle": "Автоматическая выгрузка за 2026 год",
  "datasource_id": "raw",
  "kpis": [
    {
      "title": "Выручка",
      "formatted_value": "2 684 326 520,31 руб.",
      "status": "approved",
      "description": "Сумма строк продаж"
    }
  ],
  "table": {
    "columns": ["Канал", "Выручка"],
    "rows": [["Online", 1500000], ["Retail", 1184326520]]
  },
  "sql": "SELECT channel, SUM(amount) FROM raw.sales GROUP BY channel"
}
```
- **Ответ (200 OK)**: бинарный поток `application/pdf`.
