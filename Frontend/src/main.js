// Frontend/src/main.js
import { createApp } from 'vue'
import VXETable from 'vxe-table'
import 'vxe-table/lib/style.css'
import App from './App.vue'

// 全局样式
import './style.css'

// 创建Vue应用
const app = createApp(App)
app.use(VXETable)

// 挂载到DOM
app.mount('#app')

// 移除加载状态
const loadingElement = document.getElementById('loading')
if (loadingElement) {
  loadingElement.style.display = 'none'
}
