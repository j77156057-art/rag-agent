// Vue Flow 区域画布最小验证 spike —— 入口
// 独立于工作台 workbench 入口；验证结论后可整块删除（spike-canvas.html + src/spike/）
import { createApp } from 'vue'
import RegionCanvasSpike from './RegionCanvasSpike.vue'
// Vue Flow 基础样式（布局必需）+ 默认主题（控件/Minimap 外观，spike 用 spike.css 覆盖深色）
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'
import './spike.css'

createApp(RegionCanvasSpike).mount('#app')
