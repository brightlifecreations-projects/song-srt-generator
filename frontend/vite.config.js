import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The version this bundle was built for; App.jsx compares it with the server's (V4.5.1).
const version = readFileSync(new URL('../VERSION', import.meta.url), 'utf8').trim()

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: { __BUILD_VERSION__: JSON.stringify(version) },
})
