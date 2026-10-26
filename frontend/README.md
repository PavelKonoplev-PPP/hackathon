# frontend/

Чат-интерфейс AI-аналитика: React + Vite, графики на ECharts.

## Запуск

Нужен Node.js 18+.

```bash
cd frontend
npm install
npm run dev
```

Сайт откроется на http://localhost:3000.

## Демо-режим

По умолчанию включены ответы-заглушки (`VITE_MOCK=1`), бэкенд не нужен.
Когда бэкенд готов, создайте `frontend/.env` со строкой `VITE_MOCK=0`:
запросы `/api` пойдут на `http://localhost:8000`.

Формат обмена с бэкендом — в [docs/api-contract.md](../docs/api-contract.md).

## Что умеет

- Чат с историей диалогов, поиском, переименованием и удалением.
- Ответ: текст, график (столбцы / линия / круг / таблица), KPI-карточки, «Как посчитано».
- Экспорт: график в PNG, таблица в Excel (XLSX), весь дашборд в Excel.
- Дашборды: «+ На дашборд» под ответом, «Собрать дашборд» / команда «Собери дашборд»,
  перетаскивание и изменение размера виджетов, общий фильтр периода, переименование.
- Светлая и тёмная тема (или как в системе).

Адреса: `#/chat`, `#/dashboards`, `#/dashboards/<id>` — ссылку на дашборд можно открыть повторно.

## Структура

| Файл | Что делает |
|---|---|
| `src/App.jsx` | Шапка, навигация, уведомления, общий контекст |
| `src/components/Chat.jsx`, `Message.jsx` | Окно чата и одно сообщение |
| `src/components/ResultView.jsx` | Результат: переключатель вида, график / таблица / KPI, экспорт |
| `src/components/ChartView.jsx`, `DataTable.jsx`, `KpiCards.jsx` | График, таблица, KPI-карточки |
| `src/components/Sidebar.jsx` | Поиск, диалоги, дашборды |
| `src/components/AddToDashboard.jsx` | Кнопка «+ На дашборд» |
| `src/components/DashboardsPage.jsx`, `DashboardPage.jsx`, `DashboardWidget.jsx` | Список дашбордов, дашборд, виджет |
| `src/components/PeriodFilter.jsx`, `ThemeSelect.jsx`, `InlineEdit.jsx` | Фильтр периода, тема, переименование |
| `src/lib/api.js`, `mock.js` | Запрос к бэкенду и заглушки |
| `src/lib/store.js` | Диалоги в localStorage, команды чата |
| `src/lib/dashboardsApi.js` | `/api/dashboards` или localStorage в демо-режиме |
| `src/lib/format.js`, `period.js`, `export.js`, `theme.js`, `router.js` | Числа и даты, период, XLSX/PNG, тема, адреса |
