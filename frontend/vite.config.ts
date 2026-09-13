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
        // spike 独立入口（验证 Vue Flow 区域画布）；验证结束删除入口时一并移除本行
        spike: resolve(__dirname, 'spike-canvas.html'),
      },
      output: {
        // 第三方依赖单独成块：业务代码高频改动，vendor 哈希稳定可被浏览器长期缓存。
        // CodeMirror 6 体积最大（约 2/3 bundle），独立成块避免与 Vue 混在一起。
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return
          // spike 专用：@vue-flow 独立成块，不进入工作台加载的 vendor-vue
          if (id.includes('@vue-flow')) {
            return 'vendor-vueflow'
          }
          if (id.includes('@codemirror') || id.includes('@lezer') || id.includes('codemirror')) {
            return 'vendor-codemirror'
          }
          if (id.includes('@vue') || id.includes('vue') || id.includes('@vitejs')) {
            return 'vendor-vue'
          }
          return 'vendor-misc'
        },
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
