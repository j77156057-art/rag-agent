import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

// DocMind 工作台前端工程配置
// - 多页应用（MPA）：当前只有 workbench 一个入口，后续页面在 build.rollupOptions.input 追加
// - 产物直接写入后端静态目录 ../web（PyInstaller 的 ("web","web") 会原样带走）
// - emptyOutDir 必须为 false：../web 里有问答页 index.html，构建时绝不能清空
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: resolve(__dirname, '../web'),
    emptyOutDir: false,
    assetsDir: 'assets',
    rollupOptions: {
      input: {
        workbench: resolve(__dirname, 'workbench.html'),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
