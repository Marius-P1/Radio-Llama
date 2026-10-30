import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // GitHub Pages serves a project site under /<repo>/,
  // and import.meta.env.BASE_URL then carries that prefix at runtime.
  base: mode === 'replay' ? '/Radio-Llama/' : '/',
  server: {
    // In dev the backend runs on port 8000 (`uv run radio-llama serve --reload`).
    // In production the backend serves the built files itself, so the app only uses relative URLs.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
}))
