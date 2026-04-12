import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        // Use IPv4 loopback — on Windows, "localhost" can hit ::1 while the proxy fails (ECONNREFUSED).
        target: 'http://127.0.0.1:8000',
        rewrite: (p) => p.replace(/^\/api/, ''),
        changeOrigin: true,
      },
    },
  },
})
