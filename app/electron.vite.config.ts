import { resolve } from 'node:path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'

// No React plugin: esbuild compiles TSX directly, so the dev server injects no inline
// preamble script and the renderer CSP can stay script-src 'self'.
export default defineConfig({
  main: { plugins: [externalizeDepsPlugin()] },
  preload: { plugins: [externalizeDepsPlugin()] },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    esbuild: { jsx: 'automatic' },
    build: { rollupOptions: { input: resolve(__dirname, 'src/renderer/index.html') } },
  },
})
