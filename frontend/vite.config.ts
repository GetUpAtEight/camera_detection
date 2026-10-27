import { defineConfig } from 'vite'
import type { UserConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 后端地址。默认 8080；若本机 8080 被占用（例如 Windows 端口保留导致
// 绑定报 WinError 10013），可用环境变量 VITE_API_TARGET 换成别的端口：
//   set VITE_API_TARGET=http://127.0.0.1:8090  &&  npm run dev
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8080'

/**
 * 单元测试配置由 vitest 自己读取（vitest 会加载本文件的 `test` 字段）。
 * 这里用 `as UserConfig` 断言，避免 vite 的类型不认识 `test` 字段，
 * 同时不给 devDependencies 之外的构建链增加依赖。
 */
const testConfig = {
  environment: 'jsdom',
  include: ['test/**/*.spec.ts'],
  globals: false,
  restoreMocks: true
}

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      // 开发环境把 /api 代理到 SpringBoot，避免 CORS 麻烦
      '/api': {
        target: apiTarget,
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          element: ['element-plus']
        }
      }
    }
  },
  test: testConfig
} as UserConfig & { test: typeof testConfig })
