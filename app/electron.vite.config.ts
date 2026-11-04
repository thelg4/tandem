import { resolve } from 'node:path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import type { Plugin } from 'vite'

// The dev server's hot reload needs a websocket; a built app talks to nothing.
const DEV_CONNECT = " connect-src 'self' ws://localhost:*"

function productionCsp(): Plugin {
  return {
    name: 'tandem-production-csp',
    apply: 'build',
    transformIndexHtml(html) {
      if (!html.includes(DEV_CONNECT)) throw new Error('index.html CSP no longer has the dev connect-src to strip')
      return html.replace(DEV_CONNECT, " connect-src 'none'")
    },
  }
}

// No React plugin: esbuild compiles TSX directly, so the dev server injects no inline
// preamble script and the renderer CSP can stay script-src 'self'.
export default defineConfig({
  main: { plugins: [externalizeDepsPlugin()] },
  preload: { plugins: [externalizeDepsPlugin()] },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    plugins: [productionCsp()],
    esbuild: { jsx: 'automatic' },
    build: { rollupOptions: { input: resolve(__dirname, 'src/renderer/index.html') } },
  },
})
