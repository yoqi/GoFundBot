<template>
  <div class="positions-container">
    <div class="positions-header">
      <h2>💼 我的持仓</h2>
      <p>输入基金代码自动补全名称；按中国基金交易规则自动处理购买日净值；持仓按实时估值刷新盈亏。</p>
    </div>

    <form class="position-form" @submit.prevent="addPosition">
      <div class="field">
        <label>基金代码</label>
        <input
          v-model.trim="form.code"
          placeholder="如 110022"
          maxlength="6"
          @blur="handleCodeBlur"
          required
        />
      </div>

      <div class="field">
        <label>基金名称</label>
        <input v-model.trim="form.name" placeholder="自动填充，可手动修改" required />
      </div>

      <div class="field">
        <label>购买日期</label>
        <input v-model="form.purchaseDate" type="date" :max="today" @change="handleDateChange" required />
      </div>

      <div class="field">
        <label>购买时间（用于当日15:00规则）</label>
        <input v-model="form.purchaseTime" type="time" @change="handleDateChange" required />
      </div>

      <div class="field">
        <label>持有份额</label>
        <input v-model.number="form.shares" type="number" min="0" step="0.01" placeholder="持有份额" required />
      </div>

      <div class="field">
        <label>成本净值</label>
        <input v-model.number="form.cost" type="number" min="0" step="0.0001" placeholder="自动填充，可手动修改" required />
      </div>

      <button type="submit" :disabled="isAutoFilling">{{ isAutoFilling ? '处理中...' : '添加' }}</button>
    </form>

    <p class="tips" v-if="helperText">{{ helperText }}</p>

    <div class="operation-panel" v-if="positions.length">
      <h3>🔁 持仓变更（加仓 / 减仓 / 转换）</h3>
      <p class="operation-tip">建议在晚上 21:00 后操作：基金准确净值通常在晚间更新，按金额换算份额更准确。</p>
      <form class="operation-form" @submit.prevent="applyOperation">
        <div class="field">
          <label>操作类型</label>
          <select v-model="operationForm.type" @change="handleOperationTypeChange">
            <option value="add">加仓</option>
            <option value="reduce">减仓</option>
            <option value="convert">转换</option>
          </select>
        </div>

        <div class="field">
          <label>原基金</label>
          <select v-model="operationForm.sourceId" required>
            <option value="" disabled>请选择基金</option>
            <option v-for="item in positions" :key="item.id" :value="item.id">
              {{ item.name }} ({{ item.code }})
            </option>
          </select>
        </div>

        <div class="field" v-if="operationForm.type === 'convert'">
          <label>目标基金代码</label>
          <input
            v-model.trim="operationForm.targetCode"
            maxlength="6"
            placeholder="如 001632"
            @blur="loadTargetFundName"
            required
          />
        </div>

        <div class="field" v-if="operationForm.type === 'convert'">
          <label>目标基金名称</label>
          <input v-model.trim="operationForm.targetName" placeholder="自动填充，可手动修改" required />
        </div>

        <div class="field">
          <label>金额（元）</label>
          <input v-model.number="operationForm.amount" type="number" min="0.01" step="0.01" placeholder="输入金额" required />
        </div>

        <div class="field">
          <label>操作日期</label>
          <input v-model="operationForm.date" type="date" :max="today" required />
        </div>

        <button type="submit" :disabled="operationLoading">{{ operationLoading ? '处理中...' : '确认变更' }}</button>
      </form>
      <p class="tips" v-if="operationText">{{ operationText }}</p>
    </div>

    <div class="summary" v-if="positions.length">
      <div>总成本：¥{{ formatNumber(totalCost, 2) }}</div>
      <div>总市值：¥{{ formatNumber(totalMarket, 2) }}</div>
      <div :class="totalProfit >= 0 ? 'up' : 'down'">总盈亏：{{ formatSigned(totalProfit) }}</div>
      <div :class="totalRate >= 0 ? 'up' : 'down'">总收益率：{{ formatSigned(totalRate) }}%</div>
      <div>上次刷新：{{ lastRefreshTime || '--' }}</div>
    </div>

    <div class="calendar-pnl" v-if="positions.length">
      <div class="pnl-card" :class="calendarPnl.day >= 0 ? 'up-bg' : 'down-bg'">
        <div class="label">今日盈亏</div>
        <div class="value">{{ formatSigned(calendarPnl.day) }}</div>
      </div>
      <div class="pnl-card" :class="calendarPnl.month >= 0 ? 'up-bg' : 'down-bg'">
        <div class="label">本月盈亏</div>
        <div class="value">{{ formatSigned(calendarPnl.month) }}</div>
      </div>
      <div class="pnl-card" :class="calendarPnl.year >= 0 ? 'up-bg' : 'down-bg'">
        <div class="label">本年盈亏</div>
        <div class="value">{{ formatSigned(calendarPnl.year) }}</div>
      </div>
    </div>

    <div class="charts" v-if="positions.length">
      <div class="chart-card">
        <h3>📊 持仓盈亏柱状图（明细）</h3>
        <div ref="pnlBarChartEl" class="chart-el"></div>
      </div>
      <div class="chart-card">
        <h3>📈 持有收益率走势（从成本起算）</h3>
        <div ref="returnTrendChartEl" class="chart-el"></div>
      </div>
    </div>

    <div v-if="positions.length" class="positions-table-wrap">
      <table class="positions-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>购买日期</th>
            <th>购买时间</th>
            <th>份额</th>
            <th>成本净值</th>
            <th>实时估值</th>
            <th>估值时间</th>
            <th>持仓成本</th>
            <th>持仓市值</th>
            <th>盈亏</th>
            <th>盈亏率</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in positions" :key="item.id">
            <td>{{ item.code }}</td>
            <td>{{ item.name }}</td>
            <td>{{ item.purchaseDate || '-' }}</td>
            <td>{{ item.purchaseTime || '-' }}</td>
            <td>{{ formatNumber(item.shares, 2) }}</td>
            <td>{{ formatNumber(item.cost, 4) }}</td>
            <td>{{ formatNumber(currentNav(item), 4) }}</td>
            <td>{{ quoteTime(item) }}</td>
            <td>{{ formatNumber(costAmount(item), 2) }}</td>
            <td>{{ formatNumber(marketAmount(item), 2) }}</td>
            <td :class="profit(item) >= 0 ? 'up' : 'down'">{{ formatSigned(profit(item)) }}</td>
            <td :class="profitRate(item) >= 0 ? 'up' : 'down'">{{ formatSigned(profitRate(item)) }}%</td>
            <td>
              <button class="danger" @click="removePosition(item.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="empty">暂无持仓，先添加一条记录吧。</div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { fundAPI } from '../services/api'

const STORAGE_KEY = 'gofundbot_positions'
const today = new Date().toISOString().split('T')[0]

const positions = ref([])
const helperText = ref('')
const isAutoFilling = ref(false)
const trendCache = new Map()
const quoteMap = ref({})
const historyMap = ref({})
const lastRefreshTime = ref('')
let refreshTimer = null

const normalizeFundCode = code => {
  const text = String(code ?? '').trim()
  return /^\d{1,6}$/.test(text) ? text.padStart(6, '0') : text
}

const fundSearchList = response => {
  const data = response?.data?.data
  return Array.isArray(data) ? data : (data?.funds || [])
}

const trendNav = item => {
  if (!item) return null
  const raw = item.net_worth ?? item.value ?? item.y
  if (raw === null || raw === undefined || raw === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

const pnlBarChartEl = ref(null)
const returnTrendChartEl = ref(null)
let pnlBarChart = null
let returnTrendChart = null

const form = reactive({
  code: '',
  name: '',
  purchaseDate: '',
  purchaseTime: '14:59',
  shares: null,
  cost: null
})

const operationForm = reactive({
  type: 'add',
  sourceId: '',
  targetCode: '',
  targetName: '',
  amount: null,
  date: today
})
const operationText = ref('')
const operationLoading = ref(false)


const currentNav = item => quoteMap.value[normalizeFundCode(item.code)]?.nav ?? item.cost
const quoteTime = item => quoteMap.value[normalizeFundCode(item.code)]?.time || '--'

const costAmount = item => Number(item.shares || 0) * Number(item.cost || 0)
const marketAmount = item => Number(item.shares || 0) * Number(currentNav(item) || 0)
const profit = item => marketAmount(item) - costAmount(item)
const profitRate = item => (costAmount(item) === 0 ? 0 : (profit(item) / costAmount(item)) * 100)

const totalCost = computed(() => positions.value.reduce((sum, item) => sum + costAmount(item), 0))
const totalMarket = computed(() => positions.value.reduce((sum, item) => sum + marketAmount(item), 0))
const totalProfit = computed(() => totalMarket.value - totalCost.value)
const totalRate = computed(() => (totalCost.value === 0 ? 0 : (totalProfit.value / totalCost.value) * 100))

const normalizeDate = raw => {
  if (!raw || typeof raw !== 'string') return ''
  return raw.split(' ')[0]
}

const findClosestNetWorth = (trend, targetDate) => {
  if (!Array.isArray(trend) || trend.length === 0 || !targetDate) return null
  const target = new Date(targetDate).setHours(0, 0, 0, 0)
  let best = null

  for (const item of trend) {
    const dateStr = normalizeDate(item.date)
    const nav = trendNav(item)
    if (!dateStr || nav === null) continue

    const ts = new Date(dateStr).setHours(0, 0, 0, 0)
    if (Number.isNaN(ts) || ts > target) continue

    if (!best || ts > best.ts) {
      best = { ts, date: dateStr, value: nav }
    }
  }

  return best
}

const findNavAtOrBeforeDate = (trend, dateStr) => {
  const matched = findClosestNetWorth(trend, dateStr)
  return matched ? matched.value : null
}


const getFundNavByRule = async (code, date, time = '15:00') => {
  const fundCode = normalizeFundCode(code)
  const isToday = date === today
  const isBeforeCutoff = time < '15:00'

  try {
    if (isToday && isBeforeCutoff) {
      const quote = await fetchRealtimeQuote(fundCode)
      if (quote?.nav) return quote.nav
    }

    const trend = await loadTrendByCode(fundCode)
    const match = findClosestNetWorth(trend, date)
    if (match) return Number(match.value)
  } catch (error) {
    console.error(`获取基金 ${code} 净值失败，尝试使用本地兜底净值:`, error)
  }

  // 兜底：优先使用当前已缓存估值，其次使用持仓成本净值
  const cachedQuote = quoteMap.value[fundCode]?.nav
  if (cachedQuote) return Number(cachedQuote)

  const existing = positions.value.find(item => normalizeFundCode(item.code) === fundCode)
  if (existing?.cost) return Number(existing.cost)

  return null
}

const findPositionById = id => positions.value.find(p => p.id === id)

const handleOperationTypeChange = () => {
  operationText.value = ''
  if (operationForm.type !== 'convert') {
    operationForm.targetCode = ''
    operationForm.targetName = ''
  }
}

const loadTargetFundName = async () => {
  const code = normalizeFundCode(operationForm.targetCode)
  if (!/^\d{6}$/.test(code)) return
  operationForm.targetCode = code
  try {
    const response = await fundAPI.searchFunds(code)
    const list = fundSearchList(response)
    const exact = list.find(item => item.CODE === code) || list[0]
    if (exact) operationForm.targetName = exact.NAME || operationForm.targetName
  } catch (error) {
    console.error('查询目标基金失败:', error)
  }
}

const applyOperation = async () => {
  const source = findPositionById(operationForm.sourceId)
  if (!source) {
    operationText.value = '请选择需要操作的原基金。'
    return
  }

  const amount = Number(operationForm.amount || 0)
  if (amount <= 0) {
    operationText.value = '请输入有效金额。'
    return
  }

  try {
    operationLoading.value = true
    const sourceNav = await getFundNavByRule(source.code, operationForm.date, '21:00')
    if (!sourceNav) {
      operationText.value = '无法获取原基金净值，请稍后重试。'
      return
    }

    if (operationForm.type === 'add') {
      const addShares = amount / sourceNav
      const oldCost = source.cost * source.shares
      const newCost = oldCost + amount
      const newShares = source.shares + addShares
      source.shares = Number(newShares.toFixed(6))
      source.cost = Number((newCost / newShares).toFixed(6))
      source.purchaseDate = operationForm.date
      source.purchaseTime = '21:00'
      operationText.value = `加仓完成：增加 ${addShares.toFixed(2)} 份。`
    }

    if (operationForm.type === 'reduce') {
      const reduceShares = amount / sourceNav
      if (reduceShares >= source.shares) {
        operationText.value = '减仓金额过大，超过当前持有份额。'
        return
      }
      source.shares = Number((source.shares - reduceShares).toFixed(6))
      operationText.value = `减仓完成：减少 ${reduceShares.toFixed(2)} 份。`
    }

    if (operationForm.type === 'convert') {
      const targetCode = normalizeFundCode(operationForm.targetCode)
      operationForm.targetCode = targetCode
      if (!/^\d{6}$/.test(targetCode) || !operationForm.targetName) {
        operationText.value = '请填写有效的目标基金代码与名称。'
        return
      }

      const reduceShares = amount / sourceNav
      if (reduceShares > source.shares) {
        operationText.value = '转换金额过大，超过当前持有份额。'
        return
      }

      const targetNav = await getFundNavByRule(targetCode, operationForm.date, '21:00')
      if (!targetNav) {
        operationText.value = '无法获取目标基金净值。若要转换到新基金，请在晚间净值更新后再试；若目标基金已在持仓中，可先添加目标持仓后再转换。'
        return
      }

      source.shares = Number((source.shares - reduceShares).toFixed(6))
      if (source.shares <= 0.000001) {
        positions.value = positions.value.filter(item => item.id !== source.id)
      }

      const addShares = amount / targetNav
      const existingTarget = positions.value.find(item => normalizeFundCode(item.code) === targetCode)
      if (existingTarget) {
        const oldCost = existingTarget.cost * existingTarget.shares
        const newShares = existingTarget.shares + addShares
        const newCost = oldCost + amount
        existingTarget.shares = Number(newShares.toFixed(6))
        existingTarget.cost = Number((newCost / newShares).toFixed(6))
      } else {
        positions.value.unshift({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          code: targetCode,
          name: operationForm.targetName,
          purchaseDate: operationForm.date,
          purchaseTime: '21:00',
          shares: Number(addShares.toFixed(6)),
          cost: Number(targetNav.toFixed(6))
        })
      }

      operationText.value = `转换完成：卖出 ${source.code} 金额 ¥${amount.toFixed(2)}，买入 ${targetCode}。`
    }

    await Promise.all([refreshRealtimeQuotes(), loadHistoryForPositions()])
    renderCharts()

    if (operationForm.type !== 'convert') {
      operationForm.amount = null
    }
  } catch (error) {
    console.error('执行持仓变更失败:', error)
    operationText.value = '操作失败，请稍后重试。'
  } finally {
    operationLoading.value = false
  }
}

const loadFundNameByCode = async code => {
  const normalizedCode = normalizeFundCode(code)
  if (!/^\d{6}$/.test(normalizedCode)) {
    helperText.value = '请输入 6 位基金代码。'
    return
  }

  try {
    isAutoFilling.value = true
    const response = await fundAPI.searchFunds(normalizedCode)
    const list = fundSearchList(response)
    const exact = list.find(item => item.CODE === normalizedCode) || list[0]

    if (exact) {
      form.name = exact.NAME || form.name
      helperText.value = `已自动填充基金名称：${form.name}`
    } else {
      helperText.value = '未找到该基金代码对应名称，请检查后手动填写。'
    }
  } catch (error) {
    console.error('自动查询基金名称失败:', error)
    helperText.value = '自动查询基金名称失败，请稍后重试。'
  } finally {
    isAutoFilling.value = false
  }
}

const loadTrendByCode = async code => {
  const fundCode = normalizeFundCode(code)
  if (trendCache.has(fundCode)) return trendCache.get(fundCode)
  const response = await fundAPI.getFundTrend(fundCode)
  const trend = response?.data?.net_worth_trend || []
  trendCache.set(fundCode, trend)
  return trend
}

const fetchRealtimeQuote = async code => {
  const fundCode = normalizeFundCode(code)
  const response = await fundAPI.getFundDetail(fundCode)
  const realtime = response?.data?.realtime_estimate || {}
  const estimate = Number(realtime.estimate_value)
  const official = Number(realtime.net_worth)
  const estimateDate = normalizeDate(realtime.estimate_time)
  const officialDate = normalizeDate(realtime.net_worth_date)
  const useOfficial = Number.isFinite(official) && officialDate && (!estimateDate || officialDate >= estimateDate)
  const nav = useOfficial ? official : estimate
  if (!Number.isFinite(nav)) return null
  return {
    nav,
    date: useOfficial ? officialDate : estimateDate,
    source: useOfficial ? 'official' : 'estimate',
    time: useOfficial ? (realtime.net_worth_date || '--') : (realtime.estimate_time || '--')
  }
}

const fillCostByDateRule = async () => {
  const code = normalizeFundCode(form.code)
  if (!/^\d{6}$/.test(code) || !form.purchaseDate || !form.purchaseTime) return
  form.code = code

  const isToday = form.purchaseDate === today
  const isBeforeCutoff = form.purchaseTime < '15:00'

  try {
    isAutoFilling.value = true

    if (isToday && !isBeforeCutoff) {
      form.cost = null
      helperText.value = '按交易规则：当日15:00后申购按下一交易日净值确认，当前无法自动确认成本净值，请次日补录或手动填写。'
      return
    }

    if (isToday && isBeforeCutoff) {
      const quote = await fetchRealtimeQuote(code)
      if (quote) {
        form.cost = quote.nav
        helperText.value = `按交易规则：当日15:00前申购按当日净值确认。当前以实时估值 ${quote.nav.toFixed(4)} 预填，待官方净值公布后可微调。`
        return
      }
    }

    const trend = await loadTrendByCode(code)
    const match = findClosestNetWorth(trend, form.purchaseDate)

    if (match) {
      form.cost = Number(match.value)
      helperText.value = `已根据 ${code} 在 ${match.date} 的净值自动填充成本净值：${Number(match.value).toFixed(4)}`
    } else {
      helperText.value = '未找到购买日及之前的净值数据，请手动填写成本净值。'
    }
  } catch (error) {
    console.error('自动填充购买净值失败:', error)
    helperText.value = '自动填充购买净值失败，请稍后重试。'
  } finally {
    isAutoFilling.value = false
  }
}

const refreshRealtimeQuotes = async () => {
  if (positions.value.length === 0) return
  const codes = [...new Set(positions.value.map(item => normalizeFundCode(item.code)).filter(Boolean))]

  try {
    const results = await Promise.allSettled(codes.map(code => fetchRealtimeQuote(code)))
    const nextMap = { ...quoteMap.value }

    results.forEach((result, index) => {
      const code = codes[index]
      if (result.status === 'fulfilled' && result.value) {
        nextMap[code] = result.value
      }
    })

    quoteMap.value = nextMap
    lastRefreshTime.value = new Date().toLocaleString('zh-CN')
  } catch (error) {
    console.error('刷新实时估值失败:', error)
  }
}

const loadHistoryForPositions = async () => {
  if (positions.value.length === 0) {
    historyMap.value = {}
    return
  }

  const codes = [...new Set(positions.value.map(item => normalizeFundCode(item.code)).filter(Boolean))]
  const results = await Promise.allSettled(codes.map(code => loadTrendByCode(code)))
  const next = {}
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      next[codes[index]] = result.value
    }
  })
  historyMap.value = next
}

const getHistoryDateSet = () => {
  const set = new Set()
  Object.values(historyMap.value).forEach(trend => {
    if (!Array.isArray(trend)) return
    trend.forEach(item => {
      const d = normalizeDate(item.date)
      if (d) set.add(d)
    })
  })
  return [...set].sort((a, b) => new Date(a) - new Date(b))
}

const buildPortfolioReturnSeries = () => {
  const dates = getHistoryDateSet()
  if (dates.length === 0 || positions.value.length === 0) return []

  const baseCost = totalCost.value
  if (baseCost <= 0) return []

  const rows = []
  for (const date of dates) {
    let market = 0
    let hasData = false

    for (const item of positions.value) {
      if (!item.purchaseDate || new Date(date) < new Date(item.purchaseDate)) continue
      const trend = historyMap.value[normalizeFundCode(item.code)] || []
      const nav = findNavAtOrBeforeDate(trend, date)
      if (nav !== null) {
        market += Number(item.shares || 0) * nav
        hasData = true
      }
    }

    if (!hasData) continue
    const rate = ((market - baseCost) / baseCost) * 100
    rows.push({ date, rate: Number(rate.toFixed(4)) })
  }

  return rows
}

const periodProfitSince = dateStr => {
  if (!dateStr) return totalProfit.value

  return positions.value.reduce((sum, item) => {
    const shares = Number(item.shares || 0)
    const current = Number(currentNav(item) || 0)
    if (shares <= 0 || current <= 0) return sum

    const purchasedAfterBase = item.purchaseDate && new Date(item.purchaseDate) > new Date(dateStr)
    if (purchasedAfterBase) {
      return sum + shares * (current - Number(item.cost || 0))
    }

    const trend = historyMap.value[normalizeFundCode(item.code)] || []
    const baseNav = findNavAtOrBeforeDate(trend, dateStr)
    if (baseNav === null) return sum

    return sum + shares * (current - baseNav)
  }, 0)
}

const getLastTradingDateBefore = dateStr => {
  const dates = getHistoryDateSet().filter(d => d < dateStr)
  return dates.length ? dates[dates.length - 1] : null
}

const calendarPnl = computed(() => {
  if (positions.value.length === 0) return { day: 0, month: 0, year: 0 }

  const now = new Date()
  const todayStr = now.toISOString().split('T')[0]
  const monthStart = `${todayStr.slice(0, 8)}01`
  const yearStart = `${todayStr.slice(0, 4)}-01-01`

  const prevDay = getLastTradingDateBefore(todayStr)
  const prevMonth = getLastTradingDateBefore(monthStart)
  const prevYear = getLastTradingDateBefore(yearStart)

  return {
    day: prevDay ? periodProfitSince(prevDay) : totalProfit.value,
    month: prevMonth ? periodProfitSince(prevMonth) : totalProfit.value,
    year: prevYear ? periodProfitSince(prevYear) : totalProfit.value
  }
})

const renderPnlBarChart = () => {
  if (!pnlBarChartEl.value || positions.value.length === 0) return
  if (!pnlBarChart) pnlBarChart = echarts.init(pnlBarChartEl.value)

  const labels = positions.value.map(item => `${item.name || item.code}(${item.code})`)
  const values = positions.value.map(item => Number(profit(item).toFixed(2)))

  pnlBarChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '4%', right: '4%', top: '12%', bottom: '12%', containLabel: true },
    xAxis: { type: 'category', data: labels, axisLabel: { rotate: 20 } },
    yAxis: { type: 'value', name: '盈亏(元)' },
    series: [
      {
        type: 'bar',
        data: values,
        itemStyle: {
          color: params => (params.value >= 0 ? '#16a34a' : '#dc2626')
        }
      }
    ]
  })
}

const renderReturnTrendChart = () => {
  if (!returnTrendChartEl.value || positions.value.length === 0) return
  if (!returnTrendChart) returnTrendChart = echarts.init(returnTrendChartEl.value)

  const seriesData = buildPortfolioReturnSeries()
  returnTrendChart.setOption({
    tooltip: { trigger: 'axis', valueFormatter: value => `${Number(value).toFixed(2)}%` },
    grid: { left: '4%', right: '4%', top: '12%', bottom: '12%', containLabel: true },
    xAxis: { type: 'category', data: seriesData.map(i => i.date) },
    yAxis: { type: 'value', name: '持有收益率(%)' },
    series: [
      {
        name: '持有收益率',
        type: 'line',
        smooth: true,
        data: seriesData.map(i => i.rate),
        lineStyle: { width: 2, color: '#2563eb' },
        areaStyle: { color: 'rgba(37,99,235,0.15)' }
      }
    ]
  })
}

const renderCharts = async () => {
  await nextTick()
  if (positions.value.length === 0) {
    if (pnlBarChart) { pnlBarChart.dispose(); pnlBarChart = null }
    if (returnTrendChart) { returnTrendChart.dispose(); returnTrendChart = null }
    return
  }
  renderPnlBarChart()
  renderReturnTrendChart()
}

const startRefreshTimer = () => {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = setInterval(async () => {
    await refreshRealtimeQuotes()
    renderCharts()
  }, 60000)
}

const handleCodeBlur = async () => {
  await loadFundNameByCode(form.code)
  if (form.purchaseDate && form.purchaseTime) {
    await fillCostByDateRule()
  }
}

const handleDateChange = async () => {
  if (!form.name && form.code) {
    await loadFundNameByCode(form.code)
  }
  await fillCostByDateRule()
}

const save = () => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(positions.value))
}

const load = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      positions.value = JSON.parse(raw).map(item => ({
        ...item,
        code: normalizeFundCode(item.code)
      }))
    }
  } catch (error) {
    console.error('读取持仓失败:', error)
  }
}

const resetForm = () => {
  form.code = ''
  form.name = ''
  form.purchaseDate = ''
  form.purchaseTime = '14:59'
  form.shares = null
  form.cost = null
  helperText.value = ''
}

const addPosition = async () => {
  const code = normalizeFundCode(form.code)
  positions.value.unshift({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    code,
    name: form.name,
    purchaseDate: form.purchaseDate,
    purchaseTime: form.purchaseTime,
    shares: Number(form.shares),
    cost: Number(form.cost)
  })
  resetForm()
  await refreshRealtimeQuotes()
  await loadHistoryForPositions()
  renderCharts()
}

const removePosition = async id => {
  positions.value = positions.value.filter(item => item.id !== id)
  await loadHistoryForPositions()
  renderCharts()
}

const formatNumber = (value, digit = 2) => Number(value || 0).toFixed(digit)
const formatSigned = value => `${value >= 0 ? '+' : ''}${formatNumber(value, 2)}`

watch(positions, save, { deep: true })
watch([quoteMap, historyMap], () => renderCharts(), { deep: true })

onMounted(async () => {
  load()
  await Promise.all([refreshRealtimeQuotes(), loadHistoryForPositions()])
  renderCharts()
  startRefreshTimer()
  window.addEventListener('resize', renderCharts)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  if (pnlBarChart) pnlBarChart.dispose()
  if (returnTrendChart) returnTrendChart.dispose()
  window.removeEventListener('resize', renderCharts)
})
</script>

<style scoped>
.positions-container { background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 6px 20px rgba(0,0,0,0.08); }
.positions-header { margin-bottom: 16px; }
.positions-header h2 { margin: 0 0 6px; }
.positions-header p { margin: 0; color: #666; }
.position-form { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); gap: 10px; margin-bottom: 10px; align-items: end; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 12px; color: #555; }
.position-form input, .position-form button { border: 1px solid #d9d9d9; border-radius: 8px; padding: 8px 10px; font-size: 14px; }
.position-form button { background: #2563eb; color: #fff; border: none; cursor: pointer; height: 38px; }
.position-form button:disabled { opacity: .6; cursor: not-allowed; }
.tips { margin: 6px 0 12px; color: #475569; font-size: 13px; }

.operation-panel { border: 1px dashed #cbd5e1; border-radius: 10px; padding: 12px; margin-bottom: 14px; background: #f8fafc; }
.operation-panel h3 { margin: 0 0 6px; font-size: 15px; }
.operation-tip { margin: 0 0 10px; color: #334155; font-size: 13px; }
.operation-form { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); gap: 10px; align-items: end; }
.operation-form select, .operation-form input, .operation-form button { border: 1px solid #d9d9d9; border-radius: 8px; padding: 8px 10px; font-size: 14px; }
.operation-form button { background: #0f766e; color: #fff; border: none; cursor: pointer; height: 38px; }
.operation-form button:disabled { opacity: .6; cursor: not-allowed; }
.summary { margin: 8px 0 12px; display: flex; flex-wrap: wrap; gap: 12px; font-weight: 600; }
.up { color: #16a34a; }
.down { color: #dc2626; }

.calendar-pnl { display: grid; grid-template-columns: repeat(3, minmax(140px, 1fr)); gap: 12px; margin-bottom: 14px; }
.pnl-card { border-radius: 10px; padding: 10px 12px; border: 1px solid #e5e7eb; }
.pnl-card .label { font-size: 12px; color: #475569; }
.pnl-card .value { font-size: 18px; font-weight: 700; margin-top: 4px; }
.up-bg { background: rgba(22,163,74,.08); }
.down-bg { background: rgba(220,38,38,.08); }

.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }
.chart-card { border: 1px solid #eee; border-radius: 10px; padding: 10px; background: #fff; }
.chart-card h3 { margin: 0 0 8px; font-size: 15px; }
.chart-el { height: 300px; width: 100%; }

.positions-table-wrap { overflow: auto; }
.positions-table { width: 100%; border-collapse: collapse; min-width: 1320px; }
.positions-table th, .positions-table td { border-bottom: 1px solid #eee; padding: 10px; text-align: left; font-size: 13px; }
.empty { padding: 18px; text-align: center; color: #888; background: #f8fafc; border-radius: 8px; }
.danger { background: #ef4444; color: #fff; border: none; border-radius: 6px; padding: 6px 10px; cursor: pointer; }

@media (max-width: 1200px) {
  .position-form { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
  .operation-form { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
  .charts { grid-template-columns: 1fr; }
  .calendar-pnl { grid-template-columns: 1fr; }
}
</style>
