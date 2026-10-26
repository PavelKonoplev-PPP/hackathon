import { createContext } from 'react'

// Общие данные приложения: список дашбордов и всплывающие уведомления.
export const AppContext = createContext({
  dashboards: [],
  dashboardsError: null,
  refreshDashboards: () => {},
  notify: () => {},
})
