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

## Дашборды (на втором этапе)

Эндпоинты будут добавлены вместе с задачей про дашборды (#6).

