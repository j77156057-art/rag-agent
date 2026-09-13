import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { existsSync, rmSync } from 'node:fs'

/**
 * 构建前清掉 web/assets。
 *
 * emptyOutDir 必须是 false（web/index.html 是手写的问答页，清了就没了），副作用是
 * 带哈希的旧产物会一直堆积——实测攒了 90 个文件，而冻结打包会把整个 web/ 拷进
 * 安装目录，等于白带一堆死文件。这里只删 assets 子目录：它纯粹是 Vite 产物，
 * 入口 HTML 每次构建都会重写引用，删掉安全。
 */
function cleanAssets(): Plugin {
  return {
    name: 'docmind-clean-assets',
    buildStart() {
      const dir = resolve(__dirname, '../web/assets')
      if (existsSync(dir)) rmSync(dir, { recursive: true, force: true })
    },
  }
}

// DocMind 工作台前端工程配置
// - 多页应用（MPA）：当前只有 workbench 一个入口，后续页面在 build.rollupOptions.input 追加
// - 产物直接写入后端静态目录 ../web（PyInstaller 的 ("web","web") 会原样带走）
// - emptyOutDir 必须为 false：../web 里有问答页 index.html，构建时绝不能清空
export default defineConfig({
  plugins: [vue(), cleanAssets()],
  build: {
    outDir: resolve(__dirname, '../web'),
    emptyOutDir: false,
    assetsDir: 'assets',
    rollupOptions: {
      input: {
        workbench: resolve(__dirname, 'workbench.html'),
      },
      output: {
        // 第三方依赖单独成块：业务代码高频改动，vendor 哈希稳定可被浏览器长期缓存。
        // CodeMirror 6 体积最大（约 2/3 bundle），独立成块避免与 Vue 混在一起。
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return
          if (id.includes('@codemirror') || id.includes('@lezer') || id.includes('codemirror')) {
            return 'vendor-codemirror'
          }
          // @vue-flow 必须显式排在 @vue 之前，否则会被下面的 @vue 规则捞进首屏的
          // vendor-vue（@vue-flow 的路径里也含 "@vue"）。这里返回 undefined = 不做切分，
          // 让它跟画布一起留在 SceneCanvas 异步 chunk：JS 与 CSS 同批加载，且首屏不受影响。
          if (id.includes('@vue-flow')) return
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
