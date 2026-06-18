<template>
  <div class="market-overview-container">
    <!-- 1. 市场指数实时走势 (置顶 & 折线图) -->
    <div class="market-section" v-if="showSSE30Min">
      <div class="section-header">
        <h3>📉 市场指数实时走势</h3>
        <div class="tab-group">
          <span 
            v-for="tab in tabs" 
            :key="tab.key" 
            :class="{ active: activeTab === tab.key }"
            @click="activeTab = tab.key"
          >
            {{ tab.name }}
          </span>
        </div>
        <span class="update-tag" v-if="updateTime">{{ updateTime.split(' ')[1] }} 更新</span>
      </div>
      <div class="chart-container sse-chart-container">
        <v-chart class="chart" :option="currentChartOption" autoresize v-if="hasCurrentData" />
        <div v-else class="empty-state">暂无数据 ({{ activeTabName }})</div>
      </div>
    </div>

    <!-- 2. 全球市场指数 (分组展示) -->
    <div class="market-section">
      <div class="section-header">
        <h3>🌍 全球行情</h3>
        <button class="refresh-btn" @click="fetchAll" :disabled="loading">
          <span :class="{ 'spinning': loading }">🔄</span>
        </button>
      </div>

      <!-- 中国市场：A股 + 港股 -->
      <div class="market-sub-section">
        <h4 class="sub-title"><span class="flag">🇨🇳</span> 中国市场 <span class="sub-desc">A股 / 港股</span></h4>
        <div class="index-grid china-grid" v-if="indices.china.length">
          <div v-for="item in indices.china" :key="item.name" class="index-card" :class="getUpDnClass(item.change_pct)">
            <div class="index-name">{{ item.name }}</div>
            <div class="index-price">{{ item.price }}</div>
            <div class="index-change">{{ item.change_pct }}</div>
          </div>
        </div>
      </div>

      <!-- 全球指数 -->
      <div class="market-sub-section">
        <h4 class="sub-title"><span class="flag">🌐</span> 全球指数</h4>
        <div class="index-grid global-grid" v-if="indices.global.length">
          <div v-for="item in indices.global" :key="item.name" class="index-card" :class="getUpDnClass(item.change_pct)">
            <div class="index-name">{{ item.name }}</div>
            <div class="index-price">{{ item.price }}</div>
            <div class="index-change">{{ item.change_pct }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 3. 近7日A股成交量 (柱状图) -->
    <div class="market-section">
      <div class="section-header">
        <h3>📊 近7日A股成交量</h3>
      </div>
      <div class="chart-container volume-chart-container">
        <v-chart class="chart" :option="volumeOption" autoresize v-if="aVolume.length" />
        <div v-else class="empty-state">暂无成交量数据</div>
      </div>
    </div>
    
    <!-- 4. 实时贵金属 -->
    <div class="market-section">
      <div class="section-header">
        <h3>🥇 实时贵金属</h3>
      </div>
      <div class="gold-grid" v-if="goldRealtime.length">
        <div 
          v-for="item in goldRealtime" 
          :key="item.name" 
          class="gold-card"
          :class="{ 
            'up': item.change >= 0,
            'down': item.change < 0
          }"
        >
          <div class="gold-name">{{ item.name }}</div>
          <div class="gold-price">{{ item.price }} <span class="unit">{{ item.unit }}</span></div>
          <div class="gold-change">
            <span>{{ item.change >= 0 ? '+' : '' }}{{ item.change }}</span>
            <span class="pct">{{ item.change_pct }}</span>
          </div>
        </div>
      </div>
    </div>
    
    <!-- 5. 黄金历史价格 (保持折叠功能) -->
    <div class="market-section" v-if="showGoldHistory">
      <div class="section-header">
        <h3>📜 黄金历史价格</h3>
        <button class="toggle-btn" @click="goldHistoryExpanded = !goldHistoryExpanded">
          {{ goldHistoryExpanded ? '收起' : '展开' }}
        </button>
      </div>
      <div v-if="goldHistoryExpanded && goldHistory.length" class="history-table">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>中国黄金</th>
              <th>涨跌</th>
              <th>周大福</th>
              <th>涨跌</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in goldHistory" :key="item.date">
              <td>{{ formatDate(item.date) }}</td>
              <td>{{ item.china_gold_price }}</td>
              <td :class="getChangeClass(item.china_gold_change)">{{ item.china_gold_change }}</td>
              <td>{{ item.zhoudafu_price }}</td>
              <td :class="getChangeClass(item.zhoudafu_change)">{{ item.zhoudafu_change }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { marketAPI } from '../services/api'
import { use } from "echarts/core"
import { CanvasRenderer } from "echarts/renderers"
import { LineChart, BarChart } from "echarts/charts"
import { GridComponent, TooltipComponent, TitleComponent, LegendComponent, DataZoomComponent } from "echarts/components"
import VChart from "vue-echarts"

use([CanvasRenderer, LineChart, BarChart, GridComponent, TooltipComponent, TitleComponent, LegendComponent, DataZoomComponent])

export default {
  name: 'MarketOverview',
  components: { VChart },
  props: {
    showGoldHistory: { type: Boolean, default: true },
    showSSE30Min: { type: Boolean, default: true },
    autoRefresh: { type: Boolean, default: true },
    refreshInterval: { type: Number, default: 60000 }
  },
  setup(props) {
    const loading = ref(false)
    const marketIndex = ref([])
    const goldRealtime = ref([])
    const goldHistory = ref([])
    const aVolume = ref([])
    const updateTime = ref('')
    const goldHistoryExpanded = ref(true)
    let refreshTimer = null
    
    // 指数分时数据
    const indicesIntraday = ref({ sh: [], sz: [], hs300: [] })
    const activeTab = ref('sh')
    const tabs = [
      { key: 'sh', name: '上证指数' },
      { key: 'sz', name: '深证成指' },
      { key: 'hs300', name: '沪深300' }
    ]

    const activeTabName = computed(() => tabs.find(t => t.key === activeTab.value)?.name || '')
    const hasCurrentData = computed(() => indicesIntraday.value[activeTab.value]?.length > 0)
    
    // 指数分组
    const indices = computed(() => {
      const all = marketIndex.value
      const chinaNames = ['上证指数','深证成指','创业板指','科创50','沪深300','上证50','中证500','中小100','恒生指数','国企指数','恒生科技']
      const globalNames = ['纳斯达克','纳斯达克100','道琼斯','标普500','日经225','韩国综合','英国富时100','德国DAX','法国CAC40','印度SENSEX']
      return {
        china: all.filter(i => i.market === 'A股' || i.market === '港股' || chinaNames.some(n => i.name.includes(n))),
        global: all.filter(i => i.market === '全球' || i.market === '美股' || globalNames.some(n => i.name.includes(n)))
      }
    })

    // 当前选中的指数图表配置
    const currentChartOption = computed(() => {
      const data = indicesIntraday.value[activeTab.value]
      if (!data || !data.length) return {}
      
      const times = data.map(i => i.time)
      const prices = data.map(i => parseFloat(i.price))
      // 计算涨跌色：基于第一笔数据
      const basePrice = prices[0]
      const isUp = prices[prices.length - 1] >= basePrice
      const color = isUp ? '#f5222d' : '#52c41a' // 红涨绿跌

      return {
        grid: { top: 10, right: 10, bottom: 20, left: 50, containLabel: false },
        tooltip: { 
          trigger: 'axis',
          formatter: (params) => {
            const p = params[0]
            if (!p) return ''
            const item = data[p.dataIndex]
            return `
              <div>${item.time}</div>
              <div style="font-weight:bold;color:${color}">${item.price}</div>
              <div>${item.change} (${item.change_pct})</div>
              <div>量: ${item.volume}</div>
            `
          }
        },
        xAxis: { 
          type: 'category', 
          data: times,
          axisLine: { lineStyle: { color: '#ddd' } },
          axisLabel: { color: '#999', fontSize: 10 },
          axisTick: { show: false }
        },
        yAxis: { 
          type: 'value', 
          scale: true, // 不从0开始
          splitLine: { lineStyle: { type: 'dashed', color: '#eee' } },
          axisLabel: { color: '#999', fontSize: 10 }
        },
        series: [{
          data: prices,
          type: 'line',
          smooth: true,
          symbol: 'none',
          lineStyle: { width: 2, color: color },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: isUp ? 'rgba(245,34,45,0.2)' : 'rgba(82,196,26,0.2)' },
                { offset: 1, color: isUp ? 'rgba(245,34,45,0)' : 'rgba(82,196,26,0)' }
              ]
            }
          }
        }]
      }
    })

    // 成交量图表配置
    const volumeOption = computed(() => {
      if (!aVolume.value.length) return {}
      
      const dates = aVolume.value.map(i => formatDate(i.date))
      const values = aVolume.value.map(i => parseFloat(i.total.replace('亿', '')))
      
      return {
        grid: { top: 30, right: 10, bottom: 20, left: 10, containLabel: true },
        tooltip: { 
          trigger: 'axis',
          formatter: (params) => {
            const idx = params[0].dataIndex
            const item = aVolume.value[idx]
            return `
              <b>${item.date}</b><br/>
              总成交: ${item.total}<br/>
              沪: ${item.shanghai}<br/>
              深: ${item.shenzhen}<br/>
              北: ${item.beijing}
            `
          }
        },
        xAxis: { 
          type: 'category', 
          data: dates,
          axisLine: { lineStyle: { color: '#ddd' } },
          axisTick: { show: false }
        },
        yAxis: { 
          type: 'value',
          splitLine: { lineStyle: { type: 'dashed', color: '#eee' } }
        },
        series: [{
          data: values,
          type: 'bar',
          barWidth: '40%',
          itemStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: '#1890ff' }, 
                { offset: 1, color: '#69c0ff' }
              ]
            },
            borderRadius: [4, 4, 0, 0]
          },
          label: {
            show: true,
            position: 'top',
            formatter: '{c}亿',
            color: '#666',
            fontSize: 10
          }
        }]
      }
    })

    const fetchAll = async () => {
      loading.value = true
      try {
        const response = await marketAPI.getOverview()
        if (response.data.success) {
          const data = response.data
          if (data.market_index?.success) marketIndex.value = data.market_index.data
          if (data.gold_realtime?.success) goldRealtime.value = data.gold_realtime.data
          if (data.a_volume_7days?.success) aVolume.value = data.a_volume_7days.data.reverse() // 按时间正序
          updateTime.value = data.update_time
        }
        
        // 获取多指数分时数据
        const intradayRes = await marketAPI.getIndicesIntraday()
        if (intradayRes.data.success) {
            indicesIntraday.value = intradayRes.data.data
        }
        
        if (props.showGoldHistory) {
          const historyRes = await marketAPI.getGoldHistory(10)
          if (historyRes.data.success) goldHistory.value = historyRes.data.data
        }
      } catch (e) {
        console.error(e)
      } finally {
        loading.value = false
      }
    }
    
    const getChangeClass = (change) => {
      if (!change) return ''
      return String(change).startsWith('-') ? 'down' : 'up'
    }

    const getUpDnClass = (pct) => {
      if (!pct) return ''
      const val = parseFloat(pct)
      if (isNaN(val) || val === 0) return ''
      return pct.startsWith('-') ? 'down' : 'up'
    }
    
    const formatDate = (dateStr) => {
      if (!dateStr) return ''
      const parts = dateStr.split('-')
      return parts.length >= 3 ? `${parts[1]}-${parts[2]}` : dateStr
    }

    onMounted(() => {
      fetchAll()
      if (props.autoRefresh) {
        refreshTimer = setInterval(fetchAll, props.refreshInterval)
      }
    })

    onUnmounted(() => {
      if (refreshTimer) clearInterval(refreshTimer)
    })

    return {
      loading, fetchAll,
      marketIndex, indices,
      goldRealtime, goldHistory, goldHistoryExpanded,
      aVolume, updateTime, 
      formatDate, getChangeClass, getUpDnClass,
      volumeOption,
      // New returns
      tabs, activeTab, activeTabName, hasCurrentData, currentChartOption
    }
  }
}
</script>

<style scoped>
.market-overview-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.market-section {
  background: white;
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
  padding-bottom: 8px;
}

.section-header h3 {
  margin: 0;
  font-size: 1.1em;
  color: #333;
}

.tab-group {
  display: flex;
  gap: 8px;
  margin-left: 16px;
  flex: 1;
}

.tab-group span {
  font-size: 0.85em;
  color: #666;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 4px;
  transition: all 0.2s;
  user-select: none;
}

.tab-group span:hover {
  color: #1677ff;
  background: #e6f4ff;
}

.tab-group span.active {
  color: #1677ff;
  font-weight: bold;
  background: #e6f4ff;
}

/* 上证指数 */
.sse-chart-container {
  height: 200px;
}

/* 全球市场 */
.market-sub-section {
  margin-bottom: 16px;
}

.market-sub-section:last-child {
  margin-bottom: 0;
}

.sub-title {
  font-size: 0.95em;
  color: #666;
  margin: 0 0 10px 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.sub-desc {
  color: #999;
  font-size: 0.85em;
  font-weight: normal;
}

.index-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(108px, 1fr));
  gap: 12px;
}

.china-grid {
  grid-template-columns: repeat(auto-fit, minmax(108px, 1fr));
}

.global-grid {
  grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
}

.index-card {
  padding: 10px;
  border-radius: 8px;
  text-align: center;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  transition: transform 0.2s;
}

.index-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}

.index-card.up { background: #fff1f0; border-color: #ffa39e; }
.index-card.down { background: #f6ffed; border-color: #b7eb8f; }

.index-name { font-size: 0.85em; color: #666; margin-bottom: 4px; }
.index-price { font-weight: bold; font-size: 1.1em; color: #333; }
.index-card.up .index-price, .index-card.up .index-change { color: #f5222d; }
.index-card.down .index-price, .index-card.down .index-change { color: #52c41a; }
.index-change { font-size: 0.8em; margin-top: 2px; }

/* 成交量图表 */
.volume-chart-container {
  height: 220px;
}

/* 黄金 */
.gold-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.gold-card {
  padding: 12px;
  background: #fffcf0;
  border: 1px solid #ffe58f;
  border-radius: 8px;
  text-align: center;
}

.gold-name { font-size: 0.9em; color: #666; margin-bottom: 4px; }
.gold-price { font-weight: bold; font-size: 1.2em; color: #fa8c16; }
.gold-change { font-size: 0.85em; margin-top: 4px; display: flex; justify-content: center; gap: 6px; }
.gold-change .pct { padding: 0 4px; border-radius: 4px; }
.gold-card.up .pct { background: #fff1f0; color: #f5222d; }
.gold-card.down .pct { background: #e6fffb; color: #13c2c2; } /* Try teal for down or green */
.gold-card.down .pct { background: #f6ffed; color: #52c41a; }

/* 历史表格 */
.history-table {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9em;
}

th, td {
  padding: 8px;
  text-align: right;
  border-bottom: 1px solid #f0f0f0;
}
th:first-child, td:first-child { text-align: left; }
th { color: #999; font-weight: normal; }
td.up { color: #f5222d; }
td.down { color: #52c41a; }

.refresh-btn, .toggle-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: #1677ff;
}

.empty-state {
  text-align: center;
  color: #999;
  padding: 20px;
  font-size: 0.9em;
}

.chart {
  height: 100%;
  width: 100%;
}

.update-tag {
  font-size: 0.8em;
  color: #999;
  background: #f5f5f5;
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
