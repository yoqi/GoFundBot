import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // 所有 /api 请求（包括 /api/eastmoney/...）都转发到 Flask 后端
      // 东方财富代理由 Flask 的 /api/eastmoney/<path:subpath> 端点处理
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true
      }
    }
  }
})
