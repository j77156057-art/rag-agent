import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

window.DocMindSession.ready.then(() => createApp(App).mount('#app')).catch(error => {
  document.getElementById('app')!.textContent = '会话初始化失败：' + error.message
})
