import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端开发服务器配置：/api 代理到本地后端（可选，开发期也可直连 8000）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
