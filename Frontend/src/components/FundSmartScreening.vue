<template>
  <div class="smart-screening">
    <section class="hero-card">
      <div>
        <p class="eyebrow">排雷 → 精选 → 打分</p>
        <h2>智能基金筛选</h2>
        <p class="hero-desc">
          基于本地筛选库执行三层量化筛选，一键返回风险可控、趋势可持续且未处高位的优质基金列表。
        </p>
      </div>
      <button class="primary-btn" :disabled="loading" @click="runSmartSelect">
        {{ loading ? '筛选中...' : '一键智能筛选' }}
      </button>
    </section>

    <section class="rule-strip">
      <div class="rule-card danger">
        <strong>1. 排雷过滤</strong>
        <span>短期脉冲、高波动、动量透支、经理任期、4433</span>
      </div>
      <div class="rule-card select">
        <strong>2. 精选计算</strong>
        <span>RCS、DCR、卡玛标准化、风格稳定性 λ</span>
      </div>
      <div class="rule-card score">
        <strong>3. 综合打分</strong>
        <span>按用户定义权重排序，输出 Top N 优质基金</span>
      </div>
    </section>

    <section class="filter-panel">
      <div class="field">
        <label>Top N</label>
        <select v-model.number="form.top_n">
          <option :value="10">10</option>
          <option :value="20">20</option>
          <option :value="30">30</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
        </select>
      </div>
      <div class="field keyword-field">
        <label>基金代码/名称</label>
        <input v-model.trim="form.keyword" placeholder="可选，输入基金代码或名称" @keyup.enter="runSmartSelect" />
      </div>
      <div class="field">
        <label>基金类型</label>
        <select v-model="form.fund_type">
          <option value="">全部</option>
          <option value="股票">股票型</option>
          <option value="混合">混合型</option>
          <option value="债券">债券型</option>
          <option value="指数">指数型</option>
          <option value="QDII">QDII</option>
          <option value="货币">货币型</option>
        </select>
      </div>
      <label class="check-field">
        <input type="checkbox" v-model="form.include_review" />
        包含复核队列
      </label>
      <button class="secondary-btn" @click="resetForm">重置</button>
    </section>

    <section class="notice-card">
      <strong>数据说明：</strong>
      第一版优先使用本地缓存数据。行业 PE/PB 估值分位、DCR 基准序列、近 4 季度行业集中度如缺失，会采用中性值或暂不惩罚，并在“数据说明”中逐项标注。
    </section>

    <section class="summary-grid" v-if="summary">
      <div class="summary-item">
        <span>候选基金</span>
        <strong>{{ summary.candidate_count || 0 }}</strong>
      </div>
      <div class="summary-item">
        <span>排雷剔除</span>
        <strong>{{ summary.excluded_count || 0 }}</strong>
      </div>
      <div class="summary-item">
        <span>复核队列</span>
        <strong>{{ summary.review_count || 0 }}</strong>
      </div>
      <div class="summary-item highlight">
        <span>精选入围</span>
        <strong>{{ summary.selected_count || 0 }}</strong>
      </div>
    </section>

    <div class="error-card" v-if="errorMessage">{{ errorMessage }}</div>

    <section class="result-card">
      <div class="result-head">
        <div>
          <h3>优质基金列表</h3>
          <p>综合分越高代表风险收益、趋势一致性和估值位置综合更优。</p>
        </div>
        <span class="total-chip">共 {{ total }} 只</span>
      </div>

      <vxe-grid
        v-bind="gridOptions"
        :data="results"
        :loading="loading"
        @cell-click="handleCellClick"
      >
        <template #fundName="{ row }">
          <button class="link-btn" @click.stop="viewFundDetail(row)">{{ row.fund_name || '--' }}</button>
        </template>
        <template #score="{ row, column }">
          <span class="score-pill">{{ formatNumber(row[column.field], 2) }}</span>
        </template>
        <template #number="{ row, column }">
          <span>{{ formatNumber(row[column.field], 2) }}</span>
        </template>
        <template #percent="{ row, column }">
          <span :class="percentClass(row[column.field])">{{ formatPercent(row[column.field]) }}</span>
        </template>
        <template #tag="{ row }">
          <span class="label-tag" :class="labelClass(row.label)">{{ row.label || '--' }}</span>
        </template>
        <template #trigger="{ row }">
          <span class="muted-text">{{ row.risk_trigger_text || row.review_trigger_text || '无' }}</span>
        </template>
        <template #flags="{ row }">
          <span class="muted-text">{{ row.data_flag_text || '完整' }}</span>
        </template>
        <template #pass4433="{ row }">
          <span :class="row.pass_4433 ? 'pass' : 'fail'">{{ row.pass_4433 ? '达标' : '未达' }}</span>
        </template>
        <template #actions="{ row }">
          <div class="action-buttons">
            <button @click.stop="viewFundDetail(row)">查看</button>
            <button @click.stop="addToWatchlist(row)">自选</button>
            <button @click.stop="addToCompare(row)">对比</button>
          </div>
        </template>
      </vxe-grid>

      <div class="pager" v-if="totalPages > 1">
        <button :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</button>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <button :disabled="page >= totalPages || loading" @click="changePage(page + 1)">下一页</button>
      </div>
    </section>

    <section class="detail-card" v-if="selectedRow">
      <div class="detail-head">
        <h3>{{ selectedRow.fund_name }} 评分拆解</h3>
        <button @click="selectedRow = null">关闭</button>
      </div>
      <div class="breakdown-grid">
        <div><span>综合分</span><strong>{{ formatNumber(selectedRow.composite_score, 2) }}</strong></div>
        <div><span>卡玛_norm</span><strong>{{ formatNumber(selectedRow.calmar_norm, 2) }}</strong></div>
        <div><span>DCR</span><strong>{{ formatNumber(selectedRow.dcr, 2) }}</strong></div>
        <div><span>RCS</span><strong>{{ formatNumber(selectedRow.rcs, 2) }}</strong></div>
        <div><span>估值分位</span><strong>{{ formatNumber(selectedRow.valuation_percentile, 2) }}</strong></div>
        <div><span>风格 λ</span><strong>{{ formatNumber(selectedRow.style_lambda, 4) }}</strong></div>
      </div>
      <p><strong>排雷触发：</strong>{{ selectedRow.risk_trigger_text || '无' }}</p>
      <p><strong>复核触发：</strong>{{ selectedRow.review_trigger_text || '无' }}</p>
      <p><strong>数据说明：</strong>{{ selectedRow.data_flag_text || '无' }}</p>
    </section>
  </div>
</template>

<script>
import { computed, onMounted, reactive, ref } from 'vue'
import { screeningAPI, watchlistAPI } from '../services/api'

export default {
  name: 'FundSmartScreening',
  emits: ['view-fund', 'add-to-compare'],
  setup(_, { emit }) {
    const loading = ref(false)
    const errorMessage = ref('')
    const results = ref([])
    const total = ref(0)
    const totalPages = ref(0)
    const page = ref(1)
    const pageSize = ref(20)
    const summary = ref(null)
    const selectedRow = ref(null)

    const form = reactive({
      top_n: 50,
      keyword: '',
      fund_type: '',
      include_review: false
    })

    const gridOptions = computed(() => ({
      border: true,
      stripe: true,
      showOverflow: true,
      height: 560,
      rowConfig: { isHover: true },
      columns: [
        { field: 'rank_no', title: '排名', width: 70, fixed: 'left' },
        { field: 'fund_code', title: '基金代码', width: 110, fixed: 'left' },
        { field: 'fund_name', title: '基金名称', minWidth: 190, fixed: 'left', slots: { default: 'fundName' } },
        { field: 'fund_type', title: '类型', width: 130 },
        { field: 'composite_score', title: '综合分', width: 100, sortable: true, slots: { default: 'score' } },
        { field: 'calmar_norm', title: '卡玛_norm', width: 110, slots: { default: 'number' } },
        { field: 'dcr', title: 'DCR', width: 90, slots: { default: 'number' } },
        { field: 'rcs', title: 'RCS', width: 90, slots: { default: 'number' } },
        { field: 'valuation_percentile', title: '估值分位', width: 110, slots: { default: 'number' } },
        { field: 'label', title: '标签', width: 120, slots: { default: 'tag' } },
        { field: 'return_1m', title: '近1月', width: 90, slots: { default: 'percent' } },
        { field: 'return_3m', title: '近3月', width: 90, slots: { default: 'percent' } },
        { field: 'return_1y', title: '近1年', width: 90, slots: { default: 'percent' } },
        { field: 'volatility_1y', title: '波动率', width: 90, slots: { default: 'percent' } },
        { field: 'sharpe_ratio_1y', title: '夏普', width: 80, slots: { default: 'number' } },
        { field: 'calmar_ratio_1y', title: '卡玛', width: 80, slots: { default: 'number' } },
        { field: 'pass_4433', title: '4433', width: 80, slots: { default: 'pass4433' } },
        { field: 'risk_trigger_text', title: '排雷层触发项', minWidth: 180, slots: { default: 'trigger' } },
        { field: 'data_flag_text', title: '数据说明', minWidth: 220, slots: { default: 'flags' } },
        { title: '操作', width: 145, fixed: 'right', slots: { default: 'actions' } }
      ]
    }))

    const buildParams = () => ({
      top_n: form.top_n,
      page: page.value,
      page_size: pageSize.value,
      filters: {
        keyword: form.keyword,
        fund_types: form.fund_type ? [form.fund_type] : [],
        include_review: form.include_review
      }
    })

    const runSmartSelect = async () => {
      loading.value = true
      errorMessage.value = ''
      try {
        const res = await screeningAPI.smartSelect(buildParams())
        results.value = res.data.data || []
        total.value = res.data.total || 0
        totalPages.value = res.data.total_pages || 0
        summary.value = res.data.summary || null
      } catch (error) {
        console.error('智能筛选失败:', error)
        errorMessage.value = '智能筛选失败，请确认后端服务和筛选数据库可用。'
        results.value = []
        total.value = 0
        totalPages.value = 0
        summary.value = null
      } finally {
        loading.value = false
      }
    }

    const resetForm = () => {
      form.top_n = 50
      form.keyword = ''
      form.fund_type = ''
      form.include_review = false
      page.value = 1
      runSmartSelect()
    }

    const changePage = (nextPage) => {
      page.value = nextPage
      runSmartSelect()
    }

    const viewFundDetail = (row) => {
      emit('view-fund', row.fund_code)
    }

    const addToWatchlist = async (row) => {
      try {
        await watchlistAPI.addToWatchlist(row.fund_code, row.fund_name, row.fund_type)
        window.dispatchEvent(new Event('watchlist-updated'))
      } catch (error) {
        console.error('加入自选失败:', error)
      }
    }

    const addToCompare = (row) => {
      emit('add-to-compare', {
        code: row.fund_code,
        name: row.fund_name
      })
    }

    const handleCellClick = ({ row }) => {
      selectedRow.value = row
    }

    const formatNumber = (value, digits = 2) => {
      if (value === null || value === undefined || value === '') return '--'
      const number = Number(value)
      return Number.isFinite(number) ? number.toFixed(digits) : '--'
    }

    const formatPercent = (value) => {
      const formatted = formatNumber(value, 2)
      return formatted === '--' ? '--' : `${formatted}%`
    }

    const percentClass = (value) => {
      const number = Number(value)
      if (!Number.isFinite(number)) return ''
      return number > 0 ? 'positive' : number < 0 ? 'negative' : ''
    }

    const labelClass = (label) => {
      if (label === '稳健增长型') return 'stable'
      if (label === '复苏型') return 'recovery'
      if (label === '高位风险型') return 'risk'
      return 'candidate'
    }

    onMounted(runSmartSelect)

    return {
      loading,
      errorMessage,
      results,
      total,
      totalPages,
      page,
      summary,
      selectedRow,
      form,
      gridOptions,
      runSmartSelect,
      resetForm,
      changePage,
      viewFundDetail,
      addToWatchlist,
      addToCompare,
      handleCellClick,
      formatNumber,
      formatPercent,
      percentClass,
      labelClass
    }
  }
}
</script>

<style scoped>
.smart-screening {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.hero-card,
.filter-panel,
.notice-card,
.result-card,
.detail-card {
  background: white;
  border-radius: 16px;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
  border: 1px solid #e5e7eb;
}

.hero-card {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: center;
  padding: 26px;
  background: linear-gradient(135deg, #0f62fe 0%, #334155 100%);
  color: white;
}

.eyebrow {
  font-size: 13px;
  opacity: 0.85;
  margin-bottom: 8px;
}

.hero-card h2 {
  font-size: 28px;
  margin-bottom: 8px;
}

.hero-desc {
  opacity: 0.9;
  max-width: 720px;
}

.primary-btn,
.secondary-btn,
.action-buttons button,
.pager button,
.detail-head button {
  border: none;
  border-radius: 9px;
  cursor: pointer;
  font-weight: 600;
}

.primary-btn {
  background: white;
  color: #0f62fe;
  padding: 12px 22px;
  white-space: nowrap;
}

.primary-btn:disabled,
.pager button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.rule-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.rule-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 18px;
  border-radius: 14px;
  border: 1px solid #e5e7eb;
  background: #fff;
}

.rule-card span {
  color: #6b7280;
  font-size: 13px;
}

.rule-card.danger { border-top: 4px solid #ef4444; }
.rule-card.select { border-top: 4px solid #f59e0b; }
.rule-card.score { border-top: 4px solid #22c55e; }

.filter-panel {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 14px;
  padding: 18px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field label {
  color: #4b5563;
  font-size: 13px;
  font-weight: 600;
}

.field input,
.field select {
  min-width: 150px;
  height: 38px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  padding: 0 12px;
}

.keyword-field input {
  min-width: 260px;
}

.check-field {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  color: #374151;
}

.secondary-btn {
  height: 38px;
  padding: 0 18px;
  background: #f3f4f6;
  color: #374151;
}

.notice-card {
  padding: 14px 18px;
  color: #92400e;
  background: #fffbeb;
  border-color: #fde68a;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.summary-item {
  background: white;
  padding: 18px;
  border-radius: 14px;
  border: 1px solid #e5e7eb;
  box-shadow: 0 2px 10px rgba(15, 23, 42, 0.06);
}

.summary-item span {
  display: block;
  color: #6b7280;
  margin-bottom: 8px;
}

.summary-item strong {
  font-size: 26px;
}

.summary-item.highlight strong {
  color: #16a34a;
}

.error-card {
  padding: 14px 18px;
  border-radius: 12px;
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

.result-card,
.detail-card {
  padding: 18px;
}

.result-head,
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.result-head h3,
.detail-head h3 {
  margin-bottom: 4px;
}

.result-head p {
  color: #6b7280;
  font-size: 13px;
}

.total-chip {
  background: #eff6ff;
  color: #1d4ed8;
  border-radius: 999px;
  padding: 6px 12px;
  font-weight: 600;
}

.link-btn {
  border: none;
  background: transparent;
  color: #1677ff;
  cursor: pointer;
  font-weight: 600;
}

.score-pill {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  background: #ecfdf5;
  color: #047857;
  font-weight: 700;
}

.label-tag {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.label-tag.stable { background: #dcfce7; color: #166534; }
.label-tag.recovery { background: #dbeafe; color: #1d4ed8; }
.label-tag.risk { background: #fee2e2; color: #b91c1c; }
.label-tag.candidate { background: #f3f4f6; color: #374151; }

.positive { color: #16a34a; font-weight: 600; }
.negative { color: #dc2626; font-weight: 600; }
.pass { color: #16a34a; font-weight: 600; }
.fail { color: #dc2626; font-weight: 600; }
.muted-text { color: #6b7280; font-size: 12px; }

.action-buttons {
  display: flex;
  gap: 6px;
}

.action-buttons button {
  padding: 5px 8px;
  background: #f3f4f6;
  color: #374151;
}

.pager {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 14px;
  margin-top: 16px;
}

.pager button,
.detail-head button {
  padding: 8px 14px;
  background: #f3f4f6;
  color: #374151;
}

.breakdown-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.breakdown-grid div {
  background: #f8fafc;
  border-radius: 10px;
  padding: 12px;
}

.breakdown-grid span {
  display: block;
  color: #6b7280;
  font-size: 12px;
  margin-bottom: 4px;
}

.detail-card p {
  margin-top: 8px;
  color: #374151;
}

@media (max-width: 1100px) {
  .rule-strip,
  .summary-grid,
  .breakdown-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hero-card {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
