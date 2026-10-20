import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Запросы /api уходят на бэкенд (FastAPI, порт 8000 по умолчанию).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': process.env.VITE_BACKEND_URL || 'http://localhost:8000',
    },
  },
})
