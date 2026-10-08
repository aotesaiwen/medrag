import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const proxy = Object.fromEntries(['/ask', '/retrieve', '/health', '/auth', '/slides', '/speech']
  .map(path => [path, { target: 'http://127.0.0.1:8000' }]))

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
})
