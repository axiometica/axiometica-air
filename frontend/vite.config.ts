import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'child_process'

function gitCommit() {
  try {
    return execSync('git rev-parse --short HEAD').toString().trim()
  } catch {
    return 'dev'
  }
}

export default defineConfig({
  define: {
    __GIT_COMMIT__: JSON.stringify(gitCommit()),
    __BUILD_DATE__: JSON.stringify(new Date().toISOString().slice(0, 10)),
  },
  plugins: [react()],
  server: {
    port: 3001,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
  // Pre-bundle @tabler/icons-react during dev so Vite doesn't have to re-parse
  // the 5 000-icon barrel on every HMR cycle. Has no effect on production builds
  // (pre-bundling is a dev-server-only feature) but makes `vite dev` noticeably
  // faster on first load and after cold cache clears.
  optimizeDeps: {
    include: ['@tabler/icons-react', 'react-markdown', 'remark-gfm'],
  },
  build: {
    // esbuild is Vite's built-in minifier — 3-5x faster than terser.
    minify: 'esbuild',
    target: 'es2020',
    rollupOptions: {
      output: {
        manualChunks: {
          // React core — stable, cached by browsers between deploys
          'vendor': ['react', 'react-dom'],
          // Force-graph is large and rarely changes — lazy-loaded by CMDBPage
          'graph': ['react-force-graph-2d'],
          // Tabler: icons.tsx now uses direct per-file imports (bypasses the
          // 5 000-symbol barrel), so no manualChunk needed — each of the ~65
          // used icons is a tiny module Rollup inlines into the app chunk.
        },
      },
    },
  },
})
