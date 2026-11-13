import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端开发服务器配置：/api 代理到本地后端（可选，开发期也可直连 8000）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 后端路由自带 /api 前缀，不作 rewrite
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // DEV 冒烟：testdata 静态服务（python -m http.server 8099 --directory testdata）
      '/smoke-pdf': {
        target: 'http://localhost:8099',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/smoke-pdf/, ''),
      },
    },
  },
})
