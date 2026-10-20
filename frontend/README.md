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

## Структура

| Файл | Что делает |
|---|---|
| `src/App.jsx` | Шапка и кнопка «Новый диалог» |
| `src/components/Chat.jsx` | Окно чата, ввод, история, подсказки |
| `src/components/Message.jsx` | Одно сообщение: текст, график, таблица, SQL, уточнения |
| `src/components/ChartView.jsx` | Рисует график по спецификации |
| `src/lib/api.js` | Запрос к бэкенду |
| `src/lib/mock.js` | Заглушки ответов |
