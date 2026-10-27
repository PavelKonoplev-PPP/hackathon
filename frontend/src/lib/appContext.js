import { createContext } from 'react'

// Общие данные приложения: список дашбордов и всплывающие уведомления.
export const AppContext = createContext({
  dashboards: [],
  datasources: [],
  queries: [],
  refreshQueries: () => {},
  dashboardsError: null,
  refreshDashboards: () => {},
  notify: () => {},
})
