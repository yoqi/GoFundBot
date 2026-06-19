<!-- 7x24 快讯组件 -->
<template>
  <div class="flash-news-container">
    <!-- 头部 -->
    <div class="news-header-bar">
      <div class="header-left">
        <h3 class="header-title">📰 7×24 快讯</h3>
        <span v-if="!loading && newsList.length" class="count-badge">{{ newsList.length }} 条</span>
      </div>
      <div class="header-right">
        <button class="refresh-btn" @click="fetchNews" :disabled="loading" title="刷新快讯">
          <span :class="{ spinning: loading }">🔄</span>
        </button>
      </div>
    </div>

    <!-- 骨架屏 -->
    <div v-if="loading && !newsList.length" class="skeleton-list">
      <div v-for="n in 6" :key="n" class="skeleton-item">
        <div class="skeleton-line skeleton-time"></div>
        <div class="skeleton-line skeleton-title"></div>
        <div class="skeleton-line skeleton-title short"></div>
      </div>
    </div>

    <!-- 错误 -->
    <div v-else-if="error" class="error-state">
      <span class="error-icon">⚠️</span>
      <span class="error-text">{{ error }}</span>
      <button class="retry-btn" @click="fetchNews">重试</button>
    </div>

    <!-- 空 -->
    <div v-else-if="!newsList.length && !loading" class="empty-state">
      <span class="empty-icon">📭</span>
      <span>暂无快讯数据</span>
    </div>

    <!-- 列表 -->
    <div v-else class="news-list">
      <TransitionGroup name="news-fade">
        <div
          v-for="(news, index) in newsList"
          :key="news._key || index"
          class="news-item"
          :class="{
            positive: news.evaluate === '利好',
            negative: news.evaluate === '利空',
            'is-new': news._isNew,
          }"
          @click="openDetail(news)"
        >
          <div class="item-meta">
            <span class="meta-time">{{ formatRelativeTime(news.publish_time) }}</span>
            <span v-if="news.source" class="meta-source">{{ news.source }}</span>
          </div>
          <p class="item-title">
            {{ truncate(news.title) }}
          </p>
        </div>
      </TransitionGroup>
    </div>

    <!-- 底部 -->
    <div v-if="updateTime && !loading" class="news-footer">
      <span class="footer-dot online"></span>
      <span>更新于 {{ updateTime.slice(-8) }}</span>
    </div>

    <!-- 详情弹窗 -->
    <Teleport to="body">
      <div v-if="modal.visible" class="news-modal-overlay" @click.self="closeDetail">
        <div class="news-modal">
          <div class="modal-header">
            <span class="modal-time">{{ modal.news?.publish_time }}</span>
            <button class="modal-close" @click="closeDetail">✕</button>
          </div>
          <div class="modal-body">
            <p class="modal-title">{{ modal.news?.title }}</p>
            <div class="modal-tags">
              <span v-if="modal.news?.source" class="meta-source">{{ modal.news.source }}</span>
              <span
                v-if="modal.news?.evaluate"
                class="meta-tag"
                :class="modal.news.evaluate === '利好' ? 'tag-bullish' : 'tag-bearish'"
              >{{ modal.news.evaluate }}</span>
            </div>
            <div v-if="modal.news?.related_stocks?.length" class="modal-stocks">
              <span class="modal-stocks-label">相关股票</span>
              <div class="modal-stocks-list">
                <span
                  v-for="stock in modal.news.related_stocks"
                  :key="stock.code"
                  class="stock-chip"
                  :class="{
                    'chip-up': stock.ratio && !stock.ratio.startsWith('-') && stock.ratio !== '0.00%',
                    'chip-down': stock.ratio && stock.ratio.startsWith('-'),
                  }"
                >
                  <span class="chip-name">{{ stock.name }}</span>
                  <span class="chip-ratio">{{ stock.ratio || '—' }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script>
import { ref, reactive, onMounted, onUnmounted, nextTick } from 'vue'
import { marketAPI } from '../services/api'

export default {
  name: 'FlashNews',
  props: {
    count: { type: Number, default: 30 },
    autoRefresh: { type: Boolean, default: true },
    refreshInterval: { type: Number, default: 30000 },
  },
  setup(props) {
    const MAX_LEN = 35
    const newsList = ref([])
    const loading = ref(false)
    const error = ref(null)
    const updateTime = ref('')
    const prevKeys = new Set()
    const modal = reactive({ visible: false, news: null })
    let refreshTimer = null

    const truncate = (text) => {
      if (!text) return ''
      return text.length > MAX_LEN ? text.slice(0, MAX_LEN) + '…' : text
    }

    const openDetail = (news) => {
      modal.news = news
      modal.visible = true
      document.body.style.overflow = 'hidden'
    }

    const closeDetail = () => {
      modal.visible = false
      modal.news = null
      document.body.style.overflow = ''
    }

    const fetchNews = async () => {
      loading.value = true
      error.value = null
      try {
        const response = await marketAPI.getFlashNews(props.count)
        if (response.data.success) {
          const incoming = response.data.data || []
          const currentKeys = new Set(incoming.map(n => n.publish_time + n.title?.slice(0, 20)))
          const enriched = incoming.map(n => ({
            ...n,
            _key: n.publish_time + n.title?.slice(0, 20),
            _isNew: prevKeys.size > 0 && !prevKeys.has(n.publish_time + n.title?.slice(0, 20)),
          }))
          prevKeys.clear()
          currentKeys.forEach(k => prevKeys.add(k))
          newsList.value = enriched
          updateTime.value = response.data.update_time || ''
          nextTick(() => {
            setTimeout(() => { newsList.value.forEach(n => (n._isNew = false)) }, 2000)
          })
        } else {
          error.value = response.data.error || '获取快讯失败'
        }
      } catch (e) {
        error.value = '网络异常，请稍后重试'
      } finally {
        loading.value = false
      }
    }

    const formatRelativeTime = (timeStr) => {
      if (!timeStr) return ''
      try {
        const ts = new Date(timeStr.replace(' ', 'T')).getTime()
        if (isNaN(ts)) return timeStr.slice(5, 16)
        const diff = Math.max(0, Date.now() - ts)
        const mins = Math.floor(diff / 60000)
        if (mins < 1) return '刚刚'
        if (mins < 60) return `${mins}分钟前`
        const hours = Math.floor(mins / 60)
        if (hours < 24) return `${hours}小时前`
        if (hours < 48) return '昨天'
        return timeStr.slice(5, 16)
      } catch { return timeStr.slice(5, 16) }
    }

    onMounted(() => {
      fetchNews()
      if (props.autoRefresh) refreshTimer = setInterval(fetchNews, props.refreshInterval)
    })
    onUnmounted(() => { if (refreshTimer) clearInterval(refreshTimer) })

    return {
      newsList, loading, error, updateTime, modal,
      truncate, openDetail, closeDetail, fetchNews, formatRelativeTime,
    }
  },
}
</script>

<style scoped>
/* ── 容器 ──────────────────────────────────────────── */
.flash-news-container {
  background: var(--card-bg, #fff);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  max-height: 80vh;
  min-height: 320px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  overflow: hidden;
}

.news-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-color, #f0f0f0);
  flex-shrink: 0;
}
.header-left { display: flex; align-items: center; gap: 8px; }
.header-title { margin: 0; font-size: 15px; font-weight: 700; color: var(--text-primary, #1a1a1a); }
.count-badge { font-size: 11px; font-weight: 600; color: #1677ff; background: #eef4ff; padding: 2px 8px; border-radius: 10px; }

.refresh-btn {
  width: 30px; height: 30px;
  display: flex; align-items: center; justify-content: center;
  border: none; border-radius: 8px; background: var(--item-bg, #f5f5f5);
  font-size: 14px; cursor: pointer; transition: all 0.2s;
}
.refresh-btn:hover { background: #e8e8e8; }
.refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.spinning { display: inline-block; animation: spin 0.8s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* ── 骨架屏 ────────────────────────────────────────── */
.skeleton-list { padding: 10px 14px; }
.skeleton-item { padding: 12px 0; border-bottom: 1px solid #f5f5f5; }
.skeleton-line { height: 10px; border-radius: 5px; margin-bottom: 6px;
  background: linear-gradient(90deg, #f0f0f0 25%, #e8e8e8 50%, #f0f0f0 75%); background-size: 200% 100%;
  animation: shimmer 1.5s infinite; }
.skeleton-time { width: 60px; height: 9px; }
.skeleton-title { width: 100%; }
.skeleton-title.short { width: 60%; }
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

/* ── 状态 ──────────────────────────────────────────── */
.error-state, .empty-state {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; padding: 40px 20px; color: #999;
}
.error-icon, .empty-icon { font-size: 28px; }
.retry-btn {
  margin-top: 8px; padding: 6px 18px; border: none; border-radius: 8px;
  background: #1677ff; color: #fff; font-size: 13px; font-weight: 600; cursor: pointer;
}
.retry-btn:hover { background: #0958d9; }

/* ── 列表 ──────────────────────────────────────────── */
.news-list { flex: 1; overflow-y: auto; padding: 2px 0; }
.news-list::-webkit-scrollbar { width: 4px; }
.news-list::-webkit-scrollbar-track { background: transparent; }
.news-list::-webkit-scrollbar-thumb { background: #d9d9d9; border-radius: 2px; }

.news-item {
  display: block;
  padding: 9px 14px;
  cursor: pointer;
  border-bottom: 1px solid #f8f8f8;
  border-left: 3px solid transparent;
  transition: background 0.15s;
}
.news-item:last-child { border-bottom: none; }
.news-item:hover { background: #f6f8fa; }
.news-item.is-new { animation: highlightIn 2s ease-out; }
.news-item.positive { border-left-color: #e74c3c; }
.news-item.negative { border-left-color: #27ae60; }

@keyframes highlightIn { 0% { background: #fef7e0; } 100% { background: transparent; } }

.item-meta { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; flex-wrap: wrap; }
.meta-time { font-size: 11px; color: #8c8c8c; font-weight: 500; white-space: nowrap; }
.meta-source { font-size: 10px; color: #1677ff; background: #eef4ff; padding: 1px 6px; border-radius: 4px; font-weight: 600; }
.meta-tag { font-size: 10px; padding: 1px 5px; border-radius: 4px; font-weight: 600; }
.tag-bullish { background: #fff1f0; color: #cf1322; }
.tag-bearish { background: #f0fdf4; color: #16a34a; }

.item-title { margin: 0; font-size: 13px; line-height: 1.55; color: #262626; }
.news-item:hover .item-title { color: #1677ff; }

/* ── 底部 ──────────────────────────────────────────── */
.news-footer {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 14px; border-top: 1px solid #f0f0f0;
  font-size: 11px; color: #bfbfbf; flex-shrink: 0;
}
.footer-dot { width: 6px; height: 6px; border-radius: 50%; background: #52c41a; flex-shrink: 0; }

/* ── 过渡动画 ──────────────────────────────────────── */
.news-fade-enter-active { transition: all 0.4s ease; }
.news-fade-leave-active { transition: all 0.25s ease; }
.news-fade-enter-from { opacity: 0; transform: translateY(-8px); }
.news-fade-leave-to { opacity: 0; transform: translateX(20px); }

/* ── 弹窗 ──────────────────────────────────────────── */
.news-modal-overlay {
  position: fixed; inset: 0; z-index: 9999;
  background: rgba(0,0,0,0.45);
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  animation: fadeIn 0.2s ease;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

.news-modal {
  background: #fff; border-radius: 16px;
  width: 100%; max-width: 560px; max-height: 80vh;
  display: flex; flex-direction: column;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
  animation: slideUp 0.25s ease;
}
@keyframes slideUp { from { opacity: 0; transform: translateY(24px); } to { opacity: 1; transform: translateY(0); } }

.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid #f0f0f0;
}
.modal-time { font-size: 13px; color: #8c8c8c; }
.modal-close {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  border: none; border-radius: 8px; background: #f5f5f5;
  font-size: 16px; color: #595959; cursor: pointer; transition: all 0.15s;
}
.modal-close:hover { background: #e8e8e8; color: #1a1a1a; }

.modal-body { padding: 20px; overflow-y: auto; }
.modal-title { margin: 0 0 14px; font-size: 16px; line-height: 1.7; color: #1a1a1a; font-weight: 500; }
.modal-tags { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.modal-tags .meta-source { font-size: 12px; padding: 3px 10px; }
.modal-tags .meta-tag { font-size: 13px; padding: 4px 12px; border-radius: 6px; font-weight: 700; }

.modal-stocks { border-top: 1px solid #f0f0f0; padding-top: 14px; }
.modal-stocks-label { display: block; font-size: 12px; color: #8c8c8c; margin-bottom: 8px; }
.modal-stocks-list { display: flex; flex-wrap: wrap; gap: 6px; }

.stock-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 8px; border-radius: 6px; background: #f5f5f5; font-size: 11px;
}
.stock-chip:hover { background: #ececec; }
.chip-name { color: #595959; font-weight: 500; }
.chip-ratio { font-weight: 600; color: #8c8c8c; }
.chip-up { background: #fff1f0; } .chip-up .chip-ratio { color: #cf1322; }
.chip-down { background: #f0fdf4; } .chip-down .chip-ratio { color: #16a34a; }

/* ── 响应式 ────────────────────────────────────────── */
@media (max-width: 768px) {
  .news-item { padding: 8px 12px; }
  .item-title { font-size: 12px; }
  .news-modal { max-width: 100%; max-height: 90vh; border-radius: 12px 12px 0 0; margin-top: auto; }
  .news-modal-overlay { align-items: flex-end; padding: 0; }
  @keyframes slideUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
}
</style>
