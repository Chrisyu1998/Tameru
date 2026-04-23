import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';

// App-shell PWA: the Service Worker caches the built shell (HTML + hashed JS/CSS
// + fonts) for offline load. No API responses are cached — authenticated
// financial data must never sit in the SW cache (DESIGN.md §10.1, privacy).
// The IndexedDB-backed offline transaction queue is a separate Week 3 concern.

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'prompt',
      includeAssets: [
        'apple-touch-icon.png',
        'favicon.svg',
      ],
      manifest: {
        name: 'Tameru',
        short_name: 'Tameru',
        description: 'Spending intelligence, powered by AI.',
        theme_color: '#F5EFE4',
        background_color: '#F5EFE4',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: '/icon-512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Precache shell assets only. Fonts from Google will be picked up by
        // the default runtimeCaching we intentionally leave empty below —
        // the CDN fonts are cacheable by the browser itself without SW help.
        globPatterns: ['**/*.{js,css,html,svg,woff2}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [
          // Never fall back to index.html for API paths; dev server proxies
          // API to localhost:8000 anyway, prod hits api.tameru.app cross-origin.
          /^\/healthz$/,
          /^\/me$/,
          /^\/transactions/,
          /^\/auth/,
          /^\/chat/,
        ],
        // Explicitly empty — shell-only caching. Do NOT add runtimeCaching
        // entries for authenticated API routes (privacy invariant).
        runtimeCaching: [],
      },
      devOptions: {
        // SW off in dev to keep HMR snappy and avoid stale shell.
        enabled: false,
      },
    }),
  ],
  server: {
    port: 5173,
    strictPort: true,
  },
});
