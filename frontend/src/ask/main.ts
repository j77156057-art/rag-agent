import { createApp } from 'vue'
import AskApp from './AskApp.vue'
import './ask.css'

window.DocMindSession.ready.then(() => createApp(AskApp).mount('#app')).catch((error: Error) => {
  document.getElementById('app')!.textContent = '会话初始化失败：' + error.message
})
