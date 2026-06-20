<template>
  <div class="stock-popup">
    <!-- 加载状态 -->
    <div v-if="loading" class="stock-loading">
      <div class="loading-spinner"></div>
      <p>正在获取行情数据...</p>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="stock-error">
      <span class="error-icon">⚠️</span>
      <p>{{ error }}</p>
    </div>

    <!-- 行情数据 -->
    <div v-else-if="stockData" class="stock-content">
      <!-- 头部：名称 + 代码 -->
      <div class="stock-header">
        <div class="stock-title">
          <h2>{{ stockData.name }}</h2>
          <span class="stock-code">{{ stockData.code }}</span>
          <span v-if="stockData.exchange" class="stock-exchange">{{ exchangeLabel }}</span>
        </div>
      </div>

      <!-- 核心行情：价格 + 涨跌 -->
      <div class="stock-price-section">
        <div class="current-price" :class="changeClass">
          {{ formatPrice(stockData.price) }}
        </div>
        <div class="price-change">
          <span class="change-value" :class="changeClass">
            {{ formatChange(stockData.change) }}
          </span>
          <span class="change-percent" :class="changeClass">
            {{ formatPercent(stockData.changePercent) }}
          </span>
        </div>
      </div>

      <!-- 行情详情网格 -->
      <div class="stock-detail-grid">
        <div class="detail-item">
          <span class="detail-label">今开</span>
          <span class="detail-value">{{ formatPrice(stockData.open) }}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">昨收</span>
          <span class="detail-value">{{ formatPrice(stockData.prevClose) }}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">最高</span>
          <span class="detail-value high">{{ formatPrice(stockData.high) }}</span>
        </div>
        <div class="detail-item">
          <span class="detail-label">最低</span>
          <span class="detail-value low">{{ formatPrice(stockData.low) }}</span>
        </div>
      </div>

      <!-- 交易数据 -->
      <div class="stock-section">
        <h4 class="section-title">交易数据</h4>
        <div class="stock-detail-grid">
          <div class="detail-item">
            <span class="detail-label">成交量</span>
            <span class="detail-value">{{ formatVolume(stockData.volume) }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">成交额</span>
            <span class="detail-value">{{ formatAmount(stockData.amount) }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">换手率</span>
            <span class="detail-value">{{ formatPercent(stockData.turnoverRate) }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">振幅</span>
            <span class="detail-value">{{ formatPercent(stockData.amplitude) }}</span>
          </div>
        </div>
      </div>

      <!-- 估值数据 -->
      <div class="stock-section">
        <h4 class="section-title">估值指标</h4>
        <div class="stock-detail-grid">
          <div class="detail-item">
            <span class="detail-label">市盈率(动)</span>
            <span class="detail-value">{{ formatPE(stockData.pe) }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">总市值</span>
            <span class="detail-value">{{ formatMarketCap(stockData.marketCap) }}</span>
          </div>
        </div>
      </div>

      <!-- 历史走势图 -->
      <div class="stock-section chart-section">
        <div class="chart-header">
          <h4 class="section-title">走势图</h4>
          <div class="chart-period-tabs">
            <button
              v-for="range in klinePeriods"
              :key="range.value"
              :class="['period-btn', { active: klineSelectedRange === range.value }]"
              @click="setKlineRange(range.value)"
            >
              {{ range.label }}
            </button>
          </div>
        </div>
        <div class="chart-loading" v-if="klineLoading">
          <div class="loading-spinner"></div>
          <span>加载走势数据...</span>
        </div>
        <div class="chart-error" v-else-if="klineError">
          <span>{{ klineError }}</span>
        </div>
        <div class="chart-wrapper" v-else-if="filteredKlineData.length > 0">
          <div ref="klineChartEl" class="kline-chart"></div>
          <div class="chart-summary" v-if="klineSummary">
            <div class="summary-item">
              <span class="summary-label">区间涨幅</span>
              <span class="summary-value" :class="klineSummary.changePercent >= 0 ? 'up' : 'down'">
                {{ klineSummary.changePercent >= 0 ? '+' : '' }}{{ klineSummary.changePercent.toFixed(2) }}%
              </span>
            </div>
            <div class="summary-item">
              <span class="summary-label">起始价</span>
              <span class="summary-value">{{ klineSummary.startPrice.toFixed(2) }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">最新价</span>
              <span class="summary-value">{{ klineSummary.endPrice.toFixed(2) }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">最高</span>
              <span class="summary-value high">{{ klineSummary.high.toFixed(2) }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">最低</span>
              <span class="summary-value low">{{ klineSummary.low.toFixed(2) }}</span>
            </div>
          </div>
        </div>
        <div class="chart-empty" v-else-if="!klineLoading && !klineError">
          <span>暂无历史走势数据</span>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="stock-empty">
      <p>暂无行情数据</p>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { marketAPI } from '../services/api'

export default {
  name: 'StockPopup',
  props: {
    stockData: {
      type: Object,
      default: null
    },
    loading: {
      type: Boolean,
      default: false
    },
    error: {
      type: String,
      default: ''
    }
  },
  setup(props) {
    // ── K 线图表状态 ──────────────────────────────────────────
    const klineChartEl = ref(null)
    const klineData = ref([])
    const klineLoading = ref(false)
    const klineError = ref('')
    const klineSelectedRange = ref('1y')
    let klineChartInstance = null

    const klinePeriods = [
      { label: '近1月', value: '1m' },
      { label: '近3月', value: '3m' },
      { label: '近6月', value: '6m' },
      { label: '近1年', value: '1y' },
      { label: '全部', value: 'all' }
    ]

    // 按时间范围过滤 K 线数据
    const filteredKlineData = computed(() => {
      if (!klineData.value || klineData.value.length === 0) return []

      if (klineSelectedRange.value === 'all') {
        return [...klineData.value].sort((a, b) => a.timestamp - b.timestamp)
      }

      const now = new Date()
      let cutoff = new Date()
      const rangeMap = { '1m': -1, '3m': -3, '6m': -6, '1y': -12 }
      const months = rangeMap[klineSelectedRange.value] || -12
      cutoff.setMonth(now.getMonth() + months)

      const cutoffTs = cutoff.getTime()
      return klineData.value
        .filter(item => item.timestamp >= cutoffTs)
        .sort((a, b) => a.timestamp - b.timestamp)
    })

    // 区间汇总指标
    const klineSummary = computed(() => {
      const data = filteredKlineData.value
      if (data.length === 0) return null

      const closes = data.map(d => d.close).filter(v => v != null)
      if (closes.length === 0) return null

      const startPrice = closes[0]
      const endPrice = closes[closes.length - 1]
      const changePercent = startPrice !== 0 ? ((endPrice - startPrice) / startPrice) * 100 : 0
      const high = Math.max(...closes)
      const low = Math.min(...closes)

      return { startPrice, endPrice, changePercent, high, low }
    })

    // 获取 K 线数据
    const fetchKlineData = async (code) => {
      if (!code) return
      klineLoading.value = true
      klineError.value = ''
      klineData.value = []

      try {
        const response = await marketAPI.getStockKline(code, {
          period: 'daily',
          adjust: 'qfq',
          endDate: new Date().toISOString().slice(0, 10).replace(/-/g, '')
        })
        if (response.data?.success && Array.isArray(response.data?.data)) {
          klineData.value = response.data.data.map(item => ({
            ...item,
            timestamp: parseKlineDate(item.date),
            open: parseFloat(item.open) || null,
            close: parseFloat(item.close) || null,
            high: parseFloat(item.high) || null,
            low: parseFloat(item.low) || null,
            volume: parseFloat(item.volume) || null,
            amount: parseFloat(item.amount) || null,
            changePercent: parseFloat(item.changePercent) || null
          }))
        } else {
          klineError.value = response.data?.error || '获取走势数据失败'
        }
      } catch (err) {
        console.error('获取K线数据失败:', err)
        klineError.value = err.response?.data?.error || '网络请求失败，请稍后重试'
      } finally {
        klineLoading.value = false
      }
    }

    // 解析 K 线日期字符串为时间戳
    const parseKlineDate = (dateStr) => {
      if (!dateStr) return 0
      const s = String(dateStr)
      // 格式: YYYYMMDD 或 YYYY-MM-DD
      if (s.includes('-')) {
        return new Date(s).getTime()
      }
      if (s.length === 8) {
        const y = s.slice(0, 4)
        const m = s.slice(4, 6)
        const d = s.slice(6, 8)
        return new Date(`${y}-${m}-${d}`).getTime()
      }
      return new Date(s).getTime()
    }

    // 设置 K 线时间范围
    const setKlineRange = (range) => {
      klineSelectedRange.value = range
      nextTick(() => renderKlineChart())
    }

    // 判断整体涨跌趋势（用于图表颜色）
    const getTrendColor = (data) => {
      if (data.length < 2) return { line: '#1677ff', area: ['rgba(22,119,255,0.2)', 'rgba(22,119,255,0.0)'] }
      const firstClose = data[0].close
      const lastClose = data[data.length - 1].close
      const isUp = lastClose >= firstClose
      return {
        line: isUp ? '#cf1322' : '#389e0d',
        area: isUp
          ? ['rgba(207,19,34,0.2)', 'rgba(207,19,34,0.0)']
          : ['rgba(56,158,29,0.2)', 'rgba(56,158,29,0.0)']
      }
    }

    // 渲染 ECharts K 线图
    const renderKlineChart = () => {
      const data = filteredKlineData.value
      if (!klineChartEl.value || data.length === 0) {
        if (klineChartInstance) {
          klineChartInstance.dispose()
          klineChartInstance = null
        }
        return
      }

      if (!klineChartInstance) {
        klineChartInstance = echarts.init(klineChartEl.value)
      }

      const colors = getTrendColor(data)

      // 准备收盘价序列
      const closeSeries = data.map(item => [item.timestamp, item.close])

      // 准备 OHLC 数据用于 tooltip
      const ohlcMap = {}
      data.forEach(item => {
        ohlcMap[item.timestamp] = item
      })

      const option = {
        grid: {
          left: '3%',
          right: '4%',
          bottom: '8%',
          top: '8%',
          containLabel: true
        },
        tooltip: {
          trigger: 'axis',
          backgroundColor: 'rgba(255,255,255,0.96)',
          borderColor: '#e0e0e0',
          borderWidth: 1,
          textStyle: { color: '#333', fontSize: 12 },
          formatter: function (params) {
            if (!params || params.length === 0) return ''
            const ts = params[0].value[0]
            const item = ohlcMap[ts]
            if (!item) return ''

            const date = new Date(ts)
            const dateStr = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`

            const changeColor = (item.changePercent || 0) >= 0 ? '#cf1322' : '#389e0d'
            const changeSign = (item.changePercent || 0) >= 0 ? '+' : ''

            return `
              <div style="font-weight:600;margin-bottom:6px">${dateStr}</div>
              <div style="display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:12px">
                <span style="color:#888">收盘：</span><span style="font-weight:600">${item.close?.toFixed(2) || '--'}</span>
                <span style="color:#888">开盘：</span><span>${item.open?.toFixed(2) || '--'}</span>
                <span style="color:#888">最高：</span><span style="color:#cf1322">${item.high?.toFixed(2) || '--'}</span>
                <span style="color:#888">最低：</span><span style="color:#389e0d">${item.low?.toFixed(2) || '--'}</span>
                <span style="color:#888">涨跌幅：</span><span style="color:${changeColor}">${changeSign}${(item.changePercent || 0).toFixed(2)}%</span>
                <span style="color:#888">成交量：</span><span>${formatKlineVolume(item.volume)}</span>
              </div>
            `
          }
        },
        xAxis: {
          type: 'time',
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#e0e0e0' } },
          axisTick: { show: false },
          axisLabel: {
            color: '#999',
            fontSize: 10,
            formatter: function (value) {
              const d = new Date(value)
              const m = d.getMonth() + 1
              const day = d.getDate()
              return `${m}/${day}`
            }
          },
          splitLine: { show: false }
        },
        yAxis: {
          type: 'value',
          scale: true,
          splitLine: { lineStyle: { color: '#f0f0f0', type: 'dashed' } },
          axisLabel: {
            color: '#999',
            fontSize: 10,
            formatter: '{value}'
          }
        },
        series: [
          {
            name: '收盘价',
            type: 'line',
            data: closeSeries,
            smooth: true,
            symbol: 'none',
            lineStyle: { width: 2, color: colors.line },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: colors.area[0] },
                { offset: 1, color: colors.area[1] }
              ])
            },
            markLine: {
              silent: true,
              symbol: 'none',
              lineStyle: { type: 'dashed', color: '#bbb', width: 1 },
              data: data.length > 0 ? [{
                yAxis: data[0].close,
                label: { formatter: '{c}', fontSize: 10, color: '#999' }
              }] : []
            }
          }
        ]
      }

      klineChartInstance.setOption(option, true)
    }

    // 格式化 K 线成交量
    const formatKlineVolume = (vol) => {
      if (vol == null || isNaN(vol)) return '--'
      if (vol >= 10000) return (vol / 10000).toFixed(1) + ' 万手'
      return vol.toFixed(0) + ' 手'
    }

    // 监听 stockData 变化，自动获取 K 线
    watch(() => props.stockData, (newData) => {
      if (newData && newData.code) {
        fetchKlineData(newData.code)
      }
    }, { immediate: false })

    // 窗口大小变化时重绘
    const handleResize = () => {
      if (klineChartInstance) {
        klineChartInstance.resize()
      }
    }

    onMounted(() => {
      window.addEventListener('resize', handleResize)
    })

    onUnmounted(() => {
      window.removeEventListener('resize', handleResize)
      if (klineChartInstance) {
        klineChartInstance.dispose()
        klineChartInstance = null
      }
    })

    // 监听过滤后的数据变化重绘
    watch(filteredKlineData, () => {
      nextTick(() => renderKlineChart())
    }, { immediate: false })

    // ── 原有的行情展示逻辑 ───────────────────────────────────
    const changeClass = computed(() => {
      if (!props.stockData) return ''
      const change = parseFloat(props.stockData.changePercent) || 0
      if (change > 0) return 'up'
      if (change < 0) return 'down'
      return ''
    })

    const exchangeLabel = computed(() => {
      const ex = props.stockData?.exchange
      if (!ex) return ''
      const map = { sh: '沪市', sz: '深市', bj: '北交所' }
      return map[ex] || ex.toUpperCase()
    })

    const formatPrice = (val) => {
      const num = parseFloat(val)
      if (isNaN(num) || num === 0) return '--'
      return num.toFixed(2)
    }

    const formatChange = (val) => {
      const num = parseFloat(val)
      if (isNaN(num)) return '--'
      const prefix = num > 0 ? '+' : ''
      return prefix + num.toFixed(2)
    }

    const formatPercent = (val) => {
      const num = parseFloat(val)
      if (isNaN(num) || num === 0) return '--'
      const prefix = num > 0 ? '+' : ''
      return prefix + num.toFixed(2) + '%'
    }

    const formatVolume = (val) => {
      const num = parseFloat(val)
      if (isNaN(num) || num === 0) return '--'
      if (num >= 10000) return (num / 10000).toFixed(2) + ' 万手'
      return num.toFixed(0) + ' 手'
    }

    const formatAmount = (val) => {
      const num = parseFloat(val)
      if (isNaN(num) || num === 0) return '--'
      if (num >= 100000000) return (num / 100000000).toFixed(2) + ' 亿'
      if (num >= 10000) return (num / 10000).toFixed(2) + ' 万'
      return num.toFixed(2)
    }

    const formatPE = (val) => {
      const num = parseFloat(val)
      if (isNaN(num) || num === 0) return '--'
      return num.toFixed(2)
    }

    const formatMarketCap = (val) => {
      const num = parseFloat(val)
      if (isNaN(num) || num === 0) return '--'
      if (num >= 100000000) return (num / 100000000).toFixed(2) + ' 亿'
      if (num >= 10000) return (num / 10000).toFixed(2) + ' 万'
      return num.toFixed(2)
    }

    return {
      // 行情
      changeClass,
      exchangeLabel,
      formatPrice,
      formatChange,
      formatPercent,
      formatVolume,
      formatAmount,
      formatPE,
      formatMarketCap,
      // K线图表
      klineChartEl,
      klineData,
      klineLoading,
      klineError,
      klineSelectedRange,
      klinePeriods,
      filteredKlineData,
      klineSummary,
      setKlineRange
    }
  }
}
</script>

<style scoped>
.stock-popup {
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* 加载状态 */
.stock-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
}

.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #f0f0f0;
  border-top: 3px solid #1677ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-bottom: 12px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 错误 */
.stock-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #d32f2f;
}

.error-icon {
  font-size: 40px;
  margin-bottom: 12px;
}

/* 内容 */
.stock-content {
  padding: 20px 24px;
  overflow-y: auto;
  flex: 1;
}

/* 头部 */
.stock-header {
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid #f0f0f0;
}

.stock-title h2 {
  margin: 0 0 6px 0;
  font-size: 22px;
  font-weight: 700;
  color: #1a1a1a;
}

.stock-code {
  display: inline-block;
  font-family: monospace;
  font-size: 14px;
  color: #1677ff;
  background: #e6f7ff;
  padding: 2px 10px;
  border-radius: 4px;
  margin-right: 8px;
}

.stock-exchange {
  font-size: 12px;
  color: #888;
  background: #f5f5f5;
  padding: 2px 8px;
  border-radius: 4px;
}

/* 价格区域 */
.stock-price-section {
  text-align: center;
  padding: 16px 0;
  margin-bottom: 20px;
  background: #fafafa;
  border-radius: 12px;
}

.current-price {
  font-size: 42px;
  font-weight: 700;
  font-family: 'DIN Alternate', 'Helvetica Neue', monospace;
  line-height: 1.2;
  color: #333;
}

.current-price.up {
  color: #cf1322;
}

.current-price.down {
  color: #389e0d;
}

.price-change {
  display: flex;
  justify-content: center;
  gap: 16px;
  margin-top: 8px;
  font-size: 16px;
  font-family: 'DIN Alternate', 'Helvetica Neue', monospace;
}

.change-value.up,
.change-percent.up {
  color: #cf1322;
}

.change-value.down,
.change-percent.down {
  color: #389e0d;
}

.change-value:not(.up):not(.down),
.change-percent:not(.up):not(.down) {
  color: #999;
}

/* 详情网格 */
.stock-detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
}

.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid #f5f5f5;
}

.detail-item:nth-child(odd) {
  border-right: 1px solid #f5f5f5;
}

.detail-label {
  font-size: 13px;
  color: #888;
}

.detail-value {
  font-size: 14px;
  font-weight: 600;
  color: #333;
  font-family: 'DIN Alternate', 'Helvetica Neue', monospace;
}

.detail-value.high {
  color: #cf1322;
}

.detail-value.low {
  color: #389e0d;
}

/* 分区 */
.stock-section {
  margin-top: 20px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #666;
  margin: 0 0 8px 0;
  padding-bottom: 8px;
  border-bottom: 2px solid #1677ff;
}

/* 空状态 */
.stock-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
}

/* ── 走势图区块 ─────────────────────────────────── */
.chart-section {
  margin-top: 24px;
}

.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}

.chart-header .section-title {
  margin: 0;
  padding: 0;
  border: none;
}

.chart-period-tabs {
  display: flex;
  gap: 4px;
}

.period-btn {
  padding: 3px 10px;
  font-size: 11px;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
  background: #fff;
  color: #888;
  cursor: pointer;
  transition: all 0.15s;
}

.period-btn:hover {
  border-color: #1677ff;
  color: #1677ff;
}

.period-btn.active {
  background: #1677ff;
  color: #fff;
  border-color: #1677ff;
}

.chart-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: #999;
  font-size: 13px;
}

.chart-loading .loading-spinner {
  width: 20px;
  height: 20px;
  border-width: 2px;
  margin: 0;
}

.chart-error {
  text-align: center;
  padding: 20px;
  color: #d32f2f;
  font-size: 13px;
}

.chart-empty {
  text-align: center;
  padding: 20px;
  color: #999;
  font-size: 13px;
}

.chart-wrapper {
  display: flex;
  flex-direction: column;
}

.kline-chart {
  width: 100%;
  height: 240px;
}

.chart-summary {
  display: flex;
  justify-content: space-around;
  padding: 12px 8px 4px;
  border-top: 1px solid #f5f5f5;
  margin-top: 8px;
}

.summary-item {
  text-align: center;
}

.summary-label {
  display: block;
  font-size: 11px;
  color: #999;
  margin-bottom: 2px;
}

.summary-value {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  font-family: 'DIN Alternate', 'Helvetica Neue', monospace;
}

.summary-value.up {
  color: #cf1322;
}

.summary-value.down {
  color: #389e0d;
}

.summary-value.high {
  color: #cf1322;
}

.summary-value.low {
  color: #389e0d;
}
</style>
