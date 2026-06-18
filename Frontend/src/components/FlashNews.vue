<!-- 7x24 快讯组件 -->
<template>
  <div class="flash-news-container">
    <div class="section-header">
      <h3>📰 7×24 快讯</h3>
      <button class="refresh-btn" @click="fetchNews" :disabled="loading">
        <span :class="{ 'spinning': loading }">🔄</span>
      </button>
    </div>
    
    <div v-if="loading && !newsList.length" class="loading-state">
      <span class="loading-spinner"></span>
      <span>加载中...</span>
    </div>
    
    <div v-else-if="error" class="error-state">
      <span>{{ error }}</span>
      <button @click="fetchNews">重试</button>
    </div>
    
    <div v-else class="news-list">
      <div 
        v-for="(news, index) in newsList" 
        :key="index" 
        class="news-item"
        :class="{ 
          'positive': news.evaluate === '利好',
          'negative': news.evaluate === '利空'
        }"
      >
        <div class="news-header">
          <span class="news-time">{{ formatTime(news.publish_time) }}</span>
          <span v-if="news.source" class="news-source">{{ news.source }}</span>
          <span 
            v-if="news.evaluate" 
            class="news-tag"
            :class="news.evaluate === '利好' ? 'tag-positive' : 'tag-negative'"
          >
            {{ news.evaluate }}
          </span>
        </div>
        <div class="news-content">{{ news.title }}</div>
        <div v-if="news.related_stocks && news.related_stocks.length" class="related-stocks">
          <span 
            v-for="stock in news.related_stocks.slice(0, 5)" 
            :key="stock.code"
            class="stock-tag"
            :class="{ 
              'stock-up': stock.ratio && !stock.ratio.startsWith('-'),
              'stock-down': stock.ratio && stock.ratio.startsWith('-')
            }"
          >
            {{ stock.name }} {{ stock.ratio }}
          </span>
        </div>
      </div>
    </div>
    
    <div v-if="updateTime" class="update-time">
      更新于 {{ updateTime }}
    </div>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted } from 'vue'
import { marketAPI } from '../services/api'

export default {
  name: 'FlashNews',
  props: {
    count: {
      type: Number,
      default: 30
    },
    autoRefresh: {
      type: Boolean,
      default: true
    },
    refreshInterval: {
      type: Number,
      default: 30000 // 默认30秒刷新
    }
  },
  setup(props) {
    const newsList = ref([])
    const loading = ref(false)
    const error = ref(null)
    const updateTime = ref('')
    let refreshTimer = null
    
    const fetchNews = async () => {
      loading.value = true
      error.value = null
      
      try {
        const response = await marketAPI.getFlashNews(props.count)
        if (response.data.success) {
          newsList.value = response.data.data
          updateTime.value = response.data.update_time
        } else {
          error.value = response.data.error || '获取快讯失败'
        }
      } catch (e) {
        error.value = '网络错误，请稍后重试'
        console.error('获取快讯失败:', e)
      } finally {
        loading.value = false
      }
    }
    
    const formatTime = (timeStr) => {
      if (!timeStr) return ''
      // 只显示时分秒
      const parts = timeStr.split(' ')
      return parts.length > 1 ? parts[1] : timeStr
    }
    
    onMounted(() => {
      fetchNews()
      if (props.autoRefresh) {
        refreshTimer = setInterval(fetchNews, props.refreshInterval)
      }
    })
    
    onUnmounted(() => {
      if (refreshTimer) {
        clearInterval(refreshTimer)
      }
    })
    
    return {
      newsList,
      loading,
      error,
      updateTime,
      fetchNews,
      formatTime
    }
  }
}
</script>

<style scoped>
.flash-news-container {
  background: var(--card-bg, #fff);
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color, #eee);
}

.section-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary, #333);
}

.refresh-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 16px;
  padding: 4px 8px;
  border-radius: 4px;
  transition: background 0.2s;
}

.refresh-btn:hover {
  background: var(--hover-bg, #f5f5f5);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.spinning {
  display: inline-block;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.news-list {
  max-height: 400px;
  overflow-y: auto;
}

.news-item {
  padding: 12px;
  border-radius: 8px;
  margin-bottom: 8px;
  background: var(--item-bg, #f9f9f9);
  transition: all 0.2s;
  border-left: 3px solid transparent;
}

.news-item:hover {
  background: var(--item-hover-bg, #f0f0f0);
}

.news-item.positive {
  border-left-color: #e74c3c;
  background: rgba(231, 76, 60, 0.05);
}

.news-item.negative {
  border-left-color: #27ae60;
  background: rgba(39, 174, 96, 0.05);
}

.news-header {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}

.news-time {
  font-size: 12px;
  color: var(--text-secondary, #999);
}

.news-source {
  margin-left: auto;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #eef4ff;
  color: #1677ff;
  font-weight: 600;
}

.news-tag {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 500;
}

.tag-positive {
  background: rgba(231, 76, 60, 0.15);
  color: #e74c3c;
}

.tag-negative {
  background: rgba(39, 174, 96, 0.15);
  color: #27ae60;
}

.news-content {
  font-size: 14px;
  color: var(--text-primary, #333);
  line-height: 1.5;
}

.related-stocks {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.stock-tag {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--tag-bg, #e8e8e8);
  color: var(--text-secondary, #666);
}

.stock-tag.stock-up {
  background: rgba(231, 76, 60, 0.1);
  color: #e74c3c;
}

.stock-tag.stock-down {
  background: rgba(39, 174, 96, 0.1);
  color: #27ae60;
}

.loading-state,
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: var(--text-secondary, #999);
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--border-color, #eee);
  border-top-color: var(--primary-color, #81D8CF);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-bottom: 8px;
}

.error-state button {
  margin-top: 12px;
  padding: 6px 16px;
  background: var(--primary-color, #81D8CF);
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.update-time {
  margin-top: 12px;
  text-align: right;
  font-size: 11px;
  color: var(--text-tertiary, #bbb);
}
</style>
