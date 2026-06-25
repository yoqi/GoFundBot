<template>
  <div class="realtime-container">
    <div class="top-row">
      <button class="btn btn-primary add-fund-trigger" @click="openAddFundModal">
        + 添加基金
      </button>
      <button class="btn btn-blue" @click="refreshAll" :disabled="refreshing || funds.length === 0">
        {{ refreshing ? '刷新中...' : '刷新估值' }}
      </button>
      <div class="sort-box">
        <select v-model="sortBy" class="select-sort">
          <option value="changeDesc">收益率从高到低</option>
          <option value="todayProfitDesc">今日盈亏从高到低</option>
          <option value="todayProfitAsc">今日盈亏从低到高</option>
          <option value="totalProfitDesc">持有收益从高到低</option>
        </select>
      </div>
      <button class="btn btn-blue" @click="exportData">导出数据</button>
      <label class="btn btn-green" for="import-file">导入数据</label>
      <input id="import-file" class="hidden-file" type="file" accept="application/json" @change="importData" />
    </div>

    <!-- Overview Box -->
    <div class="overview-box">
      <div class="overview-head">
        <div class="title-with-icon">📊 投资总览<span v-if="activeTab.startsWith('group_')" class="scope-tag">{{ portfolioGroups.find(g => 'group_' + g.id === activeTab)?.name || '' }}</span><span v-else-if="activeTab==='rebalance'" class="scope-tag">再平衡</span><span v-else-if="activeTab==='dividend'" class="scope-tag">红利低波</span></div>
        <div class="meta-info">实时数据来自互联网，仅供参考。数据更新时间: {{ nowTime }}</div>
      </div>
      <div class="overview-grid" v-if="hasHoldings">
        <div class="overview-cell purple">
          <div class="cell-label">总市值</div>
          <div class="cell-val">¥{{ totalAsset.toFixed(2) }}</div>
        </div>
        <div class="overview-cell">
          <div class="cell-label">总成本</div>
          <div class="cell-val">¥{{ totalCost.toFixed(2) }}</div>
        </div>
        <div class="overview-cell">
          <div class="cell-label">总收益</div>
          <div class="cell-val" :class="profitTotalClass">{{ totalProfitTotal >= 0 ? '+' : '' }}¥{{ totalProfitTotal.toFixed(2) }}</div>
        </div>
        <div class="overview-cell">
          <div class="cell-label">收益率</div>
          <div class="cell-val" :class="profitTotalClass">{{ totalReturnRate >= 0 ? '+' : '' }}{{ totalReturnRate.toFixed(2) }}%</div>
        </div>
        <div class="overview-cell">
          <div class="cell-label">今日盈亏</div>
          <div class="cell-val" :class="profitTodayClass">{{ totalProfitToday >= 0 ? '+' : '' }}¥{{ totalProfitToday.toFixed(2) }}</div>
        </div>
        <div class="overview-cell">
          <div class="cell-label">今日收益</div>
          <div class="cell-val" :class="profitTodayClass">{{ todayReturnRate >= 0 ? '+' : '' }}{{ todayReturnRate.toFixed(2) }}%</div>
        </div>
      </div>
      <div v-else class="overview-grid empty-hint">暂未设置持仓</div>
    </div>

    <!-- 挂起交易提示 -->
    <div class="pending-txns-bar" v-if="pendingTxns.length">
      <div class="pending-header" @click="showPending = !showPending">
        <span>⏳ {{ pendingTxns.length }} 笔交易待结算（等待当日净值公布）</span>
        <span class="pending-toggle">{{ showPending ? '收起 ▲' : '展开 ▼' }}</span>
      </div>
      <div class="pending-list" v-if="showPending">
        <div class="pending-item" v-for="txn in pendingTxns" :key="txn.id">
          <span class="p-type" :class="txn.type">{{ txn.type === 'buy' ? '买入' : '卖出' }}</span>
          <span class="p-name">{{ txn.fundName }}</span>
          <span class="p-val">{{ txn.type === 'buy' ? '¥' + txn.inputValue.toFixed(2) : txn.inputValue.toFixed(2) + ' 份' }}</span>
          <span class="p-date">交易日 {{ txn.tradeDate }}</span>
          <button class="btn-cancel-txn" @click="cancelPendingTxn(txn.id)">取消</button>
        </div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="content-tabs">
      <div class="ctab" :class="{active: activeTab==='all'}" @click="activeTab='all'">
        👜 基金持仓
      </div>
      <div
        v-for="g in portfolioGroups"
        :key="g.id"
        class="ctab"
        :class="{active: activeTab === 'group_' + g.id}"
        @click="activeTab = 'group_' + g.id"
        @contextmenu.prevent="openGroupContextMenu($event, g.id)"
      >
        📁 {{ g.name }}
      </div>
      <button class="ctab btn-add-group" @click="openAddGroupModal" title="新建分组">+ 📁</button>
      <div v-if="hasRebalanceFunds" class="ctab" :class="{active: activeTab==='rebalance'}" @click="activeTab='rebalance'">⚖️ 再平衡管理</div>
      <label v-if="activeTab === 'rebalance'" class="threshold-label" @click.stop>
        ≥<input v-model.number="rebalanceThreshold" type="number" min="1" step="1" class="threshold-input" />%
      </label>
      <div v-if="hasDividendFunds" class="ctab" :class="{active: activeTab==='dividend'}" @click="activeTab='dividend'">📉 红利低波</div>
    </div>

    <!-- Context menu for group tabs -->
    <div
      v-if="contextMenu.show"
      class="context-menu"
      :style="{left: contextMenu.x + 'px', top: contextMenu.y + 'px'}"
      @click.stop
    >
      <div class="context-menu-item" @click="renameGroupFromMenu">✏️ 重命名</div>
      <div class="context-menu-item danger" @click="deleteGroupFromMenu">🗑️ 删除分组</div>
    </div>

    <!-- Card Grid -->
    <div class="fund-grid" v-if="displayFunds.length">
      <div
        v-for="(fund, index) in displayFunds"
        :key="fund.code"
        class="fund-item-card"
        :class="{ dragging: dragIndex === index }"
        :draggable="activeTab === 'all'"
        @dragstart="activeTab === 'all' && onDragStart($event, index)"
        @dragend="onDragEnd"
        @dragover="onDragOver($event, index)"
      >
        <div class="card-head">
          <button class="c-title detail-link" @click.stop="openFundDetail(fund)" title="查看基金详情">{{ fund.name }}</button>
          <button class="btn-del" @click.stop="removeFund(fund.code)">删除</button>
        </div>
        <div class="c-tags">
          <span class="tag code-tag">{{ fund.code }}</span>
          <span class="tag red" v-if="holdings[fund.code]">持仓</span>
          <span class="tag pink">场外</span>
          <div class="group-assign-wrapper" @click.stop>
            <select
              class="group-assign-select"
              :value="fundGroupMap[fund.code] || ''"
              @change="assignFundToGroup(fund.code, $event.target.value || null)"
            >
              <option value="">📂 未分组</option>
              <option v-for="g in portfolioGroups" :key="g.id" :value="g.id">{{ g.name }}</option>
            </select>
          </div>
        </div>
        <div v-if="false" class="c-mid-tabs">
          <div
            class="c-tab"
            :class="{ active: true }"
          >
            📈 实时数据
          </div>
          <div
            class="c-tab"
            @click.stop="openFundDetail(fund)"
            title="打开基金详情页"
          >
            📊 基金详情
          </div>
        </div>

        <div class="c-hero">
          <div class="hero-metric">
            <div class="hero-chip" :class="getChangeClass(fund.gszzl)">{{ formatChange(fund.gszzl) }}</div>
            <div class="hero-label">{{ getPriceStatusLabel(fund) }}涨跌</div>
            <div class="hero-sub">单位净值 {{ fund.dwjz || '-' }}</div>
          </div>
          <div class="hero-metric" v-if="holdings[fund.code]">
            <div class="hero-chip" :class="getHoldingProfitTodayClass(fund)">
              {{ getHoldingProfitToday(fund) >= 0 ? '+' : '' }}¥{{ getHoldingProfitToday(fund).toFixed(2) }}
            </div>
            <div class="hero-label">{{ getPriceStatusLabel(fund) }}盈亏</div>
            <div class="hero-sub">{{ hasFreshEstimate(fund) ? '估算值' : '最新净值' }} {{ formatGsz(fund) }}</div>
          </div>
        </div>

        <div class="c-holdings-area">
          <div class="c-h-head">
            <span class="c-h-title">
              👜 持仓信息
              <button class="c-h-pen" @click.stop="openTradeHistory(fund)">📄 {{ getFundTradeRecords(fund).length }}笔</button>
            </span>
            <div class="c-h-actions">
              <button class="btn-sm b-trade" @click.stop="openTradeModal(fund, 'buy')">买卖</button>
              <button v-if="holdings[fund.code]" class="btn-sm b-edit" @click.stop="openEditModal(fund)">修改</button>
            </div>
          </div>
          <div class="c-h-grid" v-if="holdings[fund.code]">
            <div class="grid-box">
              <div class="g-label">持有份额</div>
              <div class="g-val">{{ holdings[fund.code].share.toFixed(2) }}</div>
            </div>
            <div class="grid-box">
              <div class="g-label">平均成本</div>
              <div class="g-val">{{ holdings[fund.code].cost.toFixed(4) }}</div>
            </div>
            <div class="grid-box">
              <div class="g-label">当前市值</div>
              <div class="g-val">¥{{ getHoldingEstimatedAmount(fund).toFixed(2) }}</div>
            </div>
            <div class="grid-box">
              <div class="g-label">持仓金额</div>
              <div class="g-val">¥{{ getHoldingAmount(fund).toFixed(2) }}</div>
            </div>
            <div class="grid-box">
              <div class="g-label">收益金额</div>
              <div class="g-val" :class="getHoldingProfitTotalClass(fund)">
                {{ getHoldingProfitTotal(fund) >= 0 ? '+' : '' }}¥{{ getHoldingProfitTotal(fund).toFixed(2) }}
              </div>
            </div>
            <div class="grid-box">
              <div class="g-label">收益率</div>
              <div class="g-val" :class="getHoldingProfitTotalClass(fund)">
                {{ getHoldingReturnRate(fund) >= 0 ? '+' : '' }}{{ getHoldingReturnRate(fund).toFixed(2) }}%
              </div>
            </div>
          </div>
          <div class="c-h-grid empty" v-else>
            <button class="btn-sm b-trade" @click.stop="openEditModal(fund)">录入持仓</button>
          </div>
        </div>

        <div class="c-chart">
          <svg v-if="getFundMiniChart3m(fund).points.length > 1" viewBox="0 0 110 64" preserveAspectRatio="none" class="c-svg">
            <line class="c-axis" x1="20" y1="28" x2="106" y2="28"></line>
            <line class="c-axis" x1="20" y1="4" x2="20" y2="28"></line>
            <line
              v-for="tick in getFundMiniChart3m(fund).yTicks"
              :key="`grid-${fund.code}-${tick.y}`"
              class="c-grid"
              x1="20"
              :y1="tick.y"
              x2="106"
              :y2="tick.y"
            ></line>
            <path
              class="c-fill"
              :class="getFundMiniChart3m(fund).trendUp ? 'up' : 'down'"
              :d="getSparklineFill(getFundMiniChart3m(fund).points, 48)"
            ></path>
            <path
              class="c-line"
              :class="getFundMiniChart3m(fund).trendUp ? 'up' : 'down'"
              :d="getSparklinePath(getFundMiniChart3m(fund).points)"
            ></path>
            <text
              v-for="tick in getFundMiniChart3m(fund).yTicks"
              :key="`y-${fund.code}-${tick.y}`"
              class="c-y-label"
              x="18"
              :y="tick.y + 1"
              text-anchor="end"
            >{{ tick.label }}</text>
            <text
              v-for="tick in getFundMiniChart3m(fund).xTicks"
              :key="`x-${fund.code}-${tick.x}`"
              class="c-x-label"
              :x="tick.x"
              y="55"
              text-anchor="middle"
            >{{ tick.label }}</text>
          </svg>
          <div v-else class="spark-empty">近3个月暂无走势数据</div>
        </div>

        <div class="c-time">
          更新时间: {{ fund.gztime || '-' }} | 净值日期: {{ fund.jzrq || '-' }}
        </div>
      </div>
    </div>
    <div v-else class="portfolio-empty">
      <div class="portfolio-empty-icon">📌</div>
      <div class="portfolio-empty-title">{{ emptyTitle }}</div>
      <div class="portfolio-empty-hint">{{ emptyHint }}</div>
      <button class="btn btn-primary" @click="openAddFundModal">+ 添加基金</button>
    </div>
    
<!-- Modals retained -->
    <div v-if="addFundModalOpen" class="modal-overlay" @click.self="closeAddFundModal">
      <div class="modal-box add-fund-modal">
        <div class="modal-title-row">
          <h3>添加基金</h3>
          <button class="modal-close" @click="closeAddFundModal" aria-label="关闭">×</button>
        </div>

        <div class="add-search-box">
          <input
            v-model="searchTerm"
            @input="handleSearchInput"
            @keyup.enter="confirmAddFund"
            placeholder="输入基金名称或代码"
            class="modal-input add-search-input"
            autofocus
          />
        </div>

        <div v-if="searchLoading" class="add-loading">搜索中...</div>
        <div v-else-if="searchResults.length > 0" class="add-result-list">
          <button
            v-for="fund in searchResults"
            :key="fund.CODE"
            class="add-result-item"
            :class="{ selected: isSelected(fund.CODE) }"
            @click="selectFundForAdd(fund)"
          >
            <span class="fund-code">{{ fund.CODE }}</span>
            <span class="fund-name">{{ fund.NAME }}</span>
          </button>
        </div>
        <div v-else-if="searchTerm" class="add-empty">未找到匹配基金</div>
        <div v-else class="add-empty">输入基金名称、简称或 6 位代码后选择基金</div>

        <div class="modal-actions">
          <button class="btn" @click="closeAddFundModal">取消</button>
          <button class="btn btn-primary" @click="confirmAddFund" :disabled="refreshing || (!selectedFunds.length && !searchTerm)">
            确定添加
          </button>
        </div>
      </div>
    </div>

    <div v-if="holdingModal.open" class="modal-overlay" @click.self="closeHoldingModal">
      <div class="modal-box holding-modal">
        <div class="modal-title-row holding-title-row">
          <div>
            <div class="modal-kicker">买卖交易</div>
            <h3>{{ holdingModal.fund?.name }} <span class="fund-code-sm">#{{ holdingModal.fund?.code }}</span></h3>
          </div>
          <button class="modal-close" @click="closeHoldingModal" aria-label="关闭">×</button>
        </div>

        <div class="fund-modal-info holding-summary-card">
          <div class="fund-nav-info">
            <div>
              <span class="nav-label">上一交易日净值</span>
              <span class="nav-value">{{ holdingModal.fund?.dwjz || '-' }}</span>
            </div>
            <span class="nav-date" v-if="holdingModal.fund?.jzrq">{{ holdingModal.fund.jzrq }}</span>
          </div>
        </div>

        <div class="elegant-trade-box">
          <div class="trade-toggle">
            <div
              class="trade-toggle-btn buy"
              :class="{ active: tradeForm.type === 'buy' }"
              @click="tradeForm.type = 'buy'"
            >
              加仓买入
            </div>
            <div
              class="trade-toggle-btn sell"
              :class="{ active: tradeForm.type === 'sell' }"
              @click="tradeForm.type = 'sell'"
            >
              减仓卖出
            </div>
          </div>

          <div class="form-group elegant-input-group">
            <label>交易日期</label>
            <div class="input-wrapper date-wrapper">
              <input v-model="tradeForm.tradeDate" type="date" class="modal-input no-border" :max="todayDate" />
            </div>
          </div>

          <div class="trade-nav-derived" v-if="getTradeNav() > 0">
            <span>参考净值：¥{{ getTradeNav().toFixed(4) }}</span>
            <span class="nav-date-hint" v-if="tradeForm.tradeDate === todayDate && !hasExactNavForDate(holdingModal.fund, todayDate)">⚠️ 当日净值未公布，交易将挂起</span>
            <span class="nav-date-hint confirmed" v-else-if="tradeForm.tradeDate === todayDate && hasExactNavForDate(holdingModal.fund, todayDate)">✓ 当日净值已公布</span>
          </div>

          <div class="form-group elegant-input-group">
            <label>{{ tradeForm.type === 'buy' ? '加仓金额' : '减仓份额' }}</label>
            <div class="input-wrapper">
              <span class="prefix">{{ tradeForm.type === 'buy' ? '¥' : '📦' }}</span>
              <input
                v-model.number="tradeForm.inputValue"
                type="number"
                step="any"
                :placeholder="tradeForm.type === 'buy' ? '请输入加仓金额' : '请输入减仓份额'"
                class="modal-input no-border highlight"
              />
            </div>
            <div class="trade-inline-hint" v-if="tradeForm.inputValue && getTradeNav() > 0">
              <template v-if="tradeForm.type === 'buy'">
                <span>折算份额 {{ (tradeForm.inputValue / getTradeNav()).toFixed(2) }} 份</span>
                <span>交易后总份额 {{ getTradeResultShares().toFixed(2) }} 份</span>
              </template>
              <template v-else>
                <span>预计到账 ¥{{ (tradeForm.inputValue * getTradeNav()).toFixed(2) }}</span>
                <span>交易后剩余份额 {{ getTradeResultShares().toFixed(2) }} 份</span>
                <span class="warning" v-if="getTradeResultShares() < 0">份额不足</span>
              </template>
            </div>
          </div>

          <div class="modal-actions elegant-actions">
            <button class="elegant-btn-cancel" @click="closeHoldingModal">取消</button>
            <button
              class="elegant-btn-confirm"
              :class="tradeForm.type"
              @click="saveTrade"
              :disabled="!canSubmitTrade()"
            >
              {{ submitButtonText() }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="tradeHistoryModal.open" class="modal-overlay" @click.self="closeTradeHistory">
      <div class="modal-box holding-modal trade-history-modal">
        <div class="modal-title-row holding-title-row">
          <div>
            <div class="modal-kicker">买卖记录</div>
            <h3>{{ tradeHistoryModal.fund?.name }} <span class="fund-code-sm">#{{ tradeHistoryModal.fund?.code }}</span></h3>
          </div>
          <button class="modal-close" @click="closeTradeHistory" aria-label="关闭">×</button>
        </div>

        <div class="trade-history-table" v-if="getFundTradeRecords(tradeHistoryModal.fund).length">
          <div class="trade-history-row trade-history-head">
            <span>类型</span>
            <span>时间</span>
            <span>金额</span>
            <span>份额</span>
            <span>状态</span>
          </div>
          <div
            class="trade-history-row"
            v-for="record in getFundTradeRecords(tradeHistoryModal.fund)"
            :key="record.id"
          >
            <span class="trade-type" :class="record.type">{{ record.type === 'buy' ? '买' : '卖' }}</span>
            <span>{{ record.tradeDate || '-' }}</span>
            <span>¥{{ formatMoney(record.amount) }}</span>
            <span>{{ formatShare(record.share) }}</span>
            <span class="trade-status" :class="record.status">{{ getTradeStatusText(record.status) }}</span>
          </div>
        </div>
        <div class="trade-history-empty" v-else>暂无买卖记录</div>
      </div>
    </div>

    <!-- Group management modal -->
    <div v-if="showGroupModal" class="modal-overlay" @click.self="closeGroupModal">
      <div class="modal-box group-modal">
        <div class="modal-title-row">
          <h3>{{ editingGroup ? '重命名分组' : '新建分组' }}</h3>
          <button class="modal-close" @click="closeGroupModal" aria-label="关闭">×</button>
        </div>
        <input
          v-model="groupName"
          type="text"
          placeholder="请输入分组名称"
          class="modal-input group-name-input"
          @keyup.enter="saveGroup"
          autofocus
        />
        <div class="modal-actions">
          <button class="btn" @click="closeGroupModal">取消</button>
          <button class="btn btn-primary" @click="saveGroup" :disabled="!groupName.trim()">
            {{ editingGroup ? '保存' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Standalone set/edit holding modal -->
    <div v-if="showEditModal" class="modal-overlay" @click.self="closeEditModal">
      <div class="modal-box holding-modal">
        <div class="modal-title-row holding-title-row">
          <div>
            <div class="modal-kicker">{{ holdings[editForm.fund?.code] ? '修改持仓' : '设置持仓' }}</div>
            <h3>{{ editForm.fund?.name }} <span class="fund-code-sm">#{{ editForm.fund?.code }}</span></h3>
          </div>
          <button class="modal-close" @click="closeEditModal" aria-label="关闭">×</button>
        </div>

        <div class="set-holding-form">
          <div class="form-group elegant-input-group">
            <label>持有金额 (元)</label>
            <div class="input-wrapper">
              <span class="prefix">¥</span>
              <input v-model.number="editForm.amount" type="number" step="any" placeholder="请输入当前持有金额" class="modal-input no-border highlight" />
            </div>
          </div>
          <div class="form-group elegant-input-group">
            <label>持有收益 (元)</label>
            <div class="input-wrapper">
              <span class="prefix">¥</span>
              <input v-model.number="editForm.profit" type="number" step="any" placeholder="0" class="modal-input no-border" />
            </div>
          </div>
          <div class="form-group elegant-input-group" v-if="editForm.fund">
            <label class="hint-label">份额按最新公布净值 ¥{{ parseFloat(editForm.fund.dwjz || '').toFixed(4) || '—' }} 自动计算</label>
          </div>
          <div class="modal-actions elegant-actions">
            <button class="elegant-btn-cancel" @click="closeEditModal">取消</button>
            <button class="elegant-btn-confirm buy" @click="saveEdit" :disabled="!editForm.amount">保存修改</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { fundAPI } from '../services/api'

export default {
  name: 'FundRealtime',
  emits: ['view-detail'],
  setup(props, { emit }) {
    // ==================== 状态 ====================
    const funds = ref([])
    const holdings = ref({})  // { code: { share, cost } }
    const collapsedCodes = ref(new Set())
    const refreshing = ref(false)
    const refreshMs = ref(180000)
    const searchTerm = ref('')
    const searchResults = ref([])
    const selectedFunds = ref([])
    const showDropdown = ref(false)
    const addFundModalOpen = ref(false)
    const username = ref('guest')
    const nowTime = ref('--:--')
    const sortBy = ref('changeDesc')
    const activeTab = ref('all')
    const dropdownRef = ref(null)
    const searchPanelRef = ref(null)
    const searchTimeoutRef = ref(null)
    const refreshTimer = ref(null)
    const timeTimer = ref(null)
    const searchLoading = ref(false)
    const todayDate = ref(new Date().toISOString().slice(0, 10))

    // 持仓弹窗
    const holdingModal = ref({ open: false, fund: null })
    const tradeForm = ref({ type: 'buy', inputValue: '', tradeDate: todayDate.value })
    const pendingTxns = ref([])   // 挂起交易列表
    const tradeRecords = ref([])
    const tradeHistoryModal = ref({ open: false, fund: null })
    const showPending = ref(false)

    // 拖拽排序
    const dragIndex = ref(null)
    const dragOverIndex = ref(null)
    const fundOrder = ref([])

    // 持仓分组
    const portfolioGroups = ref([])
    const fundGroupMap = ref({})
    const showGroupModal = ref(false)
    const editingGroup = ref(null)
    const groupName = ref('')
    const contextMenu = ref({ show: false, x: 0, y: 0, groupId: null })

    // 再平衡阈值（百分比）
    const rebalanceThreshold = ref(8)

    // 独立修改弹窗
    const showEditModal = ref(false)
    const editForm = ref({ fund: null, amount: '', profit: 0 })

    // ==================== 计算属性 ====================
    const isTradingTime = computed(() => {
      const now = new Date()
      const day = now.getDay()
      if (day === 0 || day === 6) return false
      const h = now.getHours()
      const m = now.getMinutes()
      const minutes = h * 60 + m
      // 9:30 - 11:30, 13:00 - 15:00
      return (minutes >= 570 && minutes <= 690) || (minutes >= 780 && minutes <= 900)
    })

    const metricBySort = (fund, key) => {
      if (key === 'todayProfitDesc' || key === 'todayProfitAsc') return getHoldingProfitToday(fund)
      if (key === 'totalProfitDesc') return getHoldingProfitTotal(fund)
      return typeof fund.gszzl === 'number' ? fund.gszzl : parseFloat(fund.gszzl) || 0
    }

    const sortedFunds = computed(() => {
      const orderMap = {}
      fundOrder.value.forEach((code, i) => { orderMap[code] = i })
      funds.value.forEach(f => { if (!(f.code in orderMap)) orderMap[f.code] = Infinity })

      const list = [...funds.value]
      list.sort((a, b) => {
        const orderDiff = orderMap[a.code] - orderMap[b.code]
        if (orderDiff !== 0) return orderDiff
        const aVal = metricBySort(a, sortBy.value)
        const bVal = metricBySort(b, sortBy.value)
        if (sortBy.value === 'todayProfitAsc') return aVal - bVal
        return bVal - aVal
      })
      return list
    })

    const displayFunds = computed(() => {
      if (activeTab.value === 'all') return sortedFunds.value
      if (activeTab.value.startsWith('group_')) {
        const groupId = activeTab.value.replace('group_', '')
        return sortedFunds.value.filter(f => fundGroupMap.value[f.code] === groupId)
      }
      if (activeTab.value === 'rebalance') {
        return sortedFunds.value.filter(f => {
          const h = holdings.value[f.code]
          if (!h || !h.share) return false
          const amount = getHoldingEstimatedAmount(f)
          if (!amount) return false
          const diffRatio = Math.abs(getHoldingProfitTotal(f) / amount)
          return diffRatio >= rebalanceThreshold.value / 100
        })
      }
      if (activeTab.value === 'dividend') {
        return sortedFunds.value.filter(f => /红利|低波|价值|股息|高股息/.test(f.name || ''))
      }
      return sortedFunds.value
    })

    const emptyTitle = computed(() => {
      if (activeTab.value.startsWith('group_')) {
        const g = portfolioGroups.value.find(g => 'group_' + g.id === activeTab.value)
        return g ? `”${g.name}” 分组暂无基金` : '暂无基金'
      }
      if (activeTab.value === 'rebalance') return '暂无需要再平衡的基金'
      if (activeTab.value === 'dividend') return '暂无匹配”红利低波”主题的基金'
      return '暂无基金'
    })

    const emptyHint = computed(() => {
      if (activeTab.value.startsWith('group_')) return '将基金分配到该分组即可在此查看'
      if (activeTab.value === 'rebalance') return `当前持仓波动处于 ±${rebalanceThreshold.value}% 以内`
      if (activeTab.value === 'dividend') return '请添加名称包含”红利 / 低波 / 股息”等关键词基金'
      return '点击添加基金后，搜索基金名称或代码即可加入持仓列表'
    })

    const hasHoldings = computed(() => {
      return displayFunds.value.some(f => holdings.value[f.code] && holdings.value[f.code].share)
    })

    const hasRebalanceFunds = computed(() => {
      return funds.value.some(f => {
        const h = holdings.value[f.code]
        if (!h || !h.share) return false
        const amount = getHoldingEstimatedAmount(f)
        if (!amount) return false
        return Math.abs(getHoldingProfitTotal(f) / amount) >= rebalanceThreshold.value / 100
      })
    })

    const hasDividendFunds = computed(() => {
      return funds.value.some(f => /红利|低波|价值|股息|高股息/.test(f.name || ''))
    })

    const totalAsset = computed(() => {
      const scope = displayFunds.value
      let total = 0
      scope.forEach(fund => {
        const h = holdings.value[fund.code]
        if (h && h.share) {
          total += getHoldingEstimatedAmount(fund)
        }
      })
      return total
    })

    const totalProfitToday = computed(() => {
      const scope = displayFunds.value
      let total = 0
      scope.forEach(fund => {
        const h = holdings.value[fund.code]
        if (h && h.share) {
          total += getHoldingProfitToday(fund)
        }
      })
      return total
    })

    const totalPreviousAsset = computed(() => {
      const scope = displayFunds.value
      let total = 0
      scope.forEach(fund => {
        const h = holdings.value[fund.code]
        if (h && h.share) {
          total += h.share * getPreviousPrice(fund)
        }
      })
      return total
    })

    const totalProfitTotal = computed(() => {
      const scope = displayFunds.value
      let total = 0
      scope.forEach(fund => {
        const h = holdings.value[fund.code]
        if (h && h.share && h.cost) {
          total += getHoldingProfitTotal(fund)
        }
      })
      return total
    })

    const totalCost = computed(() => {
      const scope = displayFunds.value
      let total = 0
      scope.forEach(fund => {
        const h = holdings.value[fund.code]
        if (h && h.share && h.cost) {
          total += h.share * h.cost
        }
      })
      return total
    })

    const totalReturnRate = computed(() => {
      const principal = totalAsset.value - totalProfitTotal.value
      if (!principal) return 0
      return (totalProfitTotal.value / principal) * 100
    })

    const todayReturnRate = computed(() => {
      if (!totalPreviousAsset.value) return 0
      return (totalProfitToday.value / totalPreviousAsset.value) * 100
    })

    const getValueClass = (val) => {
      const num = typeof val === 'number' ? val : parseFloat(val)
      if (!Number.isFinite(num)) return ''
      return num > 0 ? 'up' : num < 0 ? 'down' : 'flat'
    }

    const profitTodayClass = computed(() => getValueClass(totalProfitToday.value))
    const profitTotalClass = computed(() => getValueClass(totalProfitTotal.value))

    // ==================== 方法 ====================
    const getChangeClass = (val) => {
      const num = typeof val === 'number' ? val : parseFloat(val)
      if (!Number.isFinite(num)) return ''
      return getValueClass(num)
    }

    const formatGsz = (fund) => {
      const price = getCurrentPrice(fund)
      return price ? price.toFixed(4) : '-'
    }

    const formatChange = (val) => {
      const num = typeof val === 'number' ? val : parseFloat(val)
      if (isNaN(num)) return '-'
      return (num >= 0 ? '+' : '') + num.toFixed(2) + '%'
    }

    const getDateText = (value) => {
      if (!value) return ''
      const text = String(value)
      const matched = text.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/)
      if (matched) {
        return `${matched[1]}-${String(matched[2]).padStart(2, '0')}-${String(matched[3]).padStart(2, '0')}`
      }
      return ''
    }

    const hasFreshEstimate = (fund) => {
      const estimate = Number(fund?.gsz)
      if (!Number.isFinite(estimate) || estimate <= 0) return false
      const estimateDate = getDateText(fund?.gztime)
      const navDate = getDateText(fund?.jzrq)
      return !!estimateDate && !!navDate && estimateDate > navDate
    }

    const getCurrentPrice = (fund) => {
      const estimate = parseFloat(fund?.gsz)
      if (hasFreshEstimate(fund) && Number.isFinite(estimate) && estimate > 0) return estimate
      const nav = parseFloat(fund?.dwjz)
      return Number.isFinite(nav) && nav > 0 ? nav : 0
    }

    const getLatestPublishedPrice = (fund) => {
      const nav = parseFloat(fund?.dwjz)
      return Number.isFinite(nav) && nav > 0 ? nav : 0
    }

    const getPriceStatusLabel = (fund) => hasFreshEstimate(fund) ? '当日估值' : '已更新净值'

    const getPreviousPrice = (fund) => {
      if (hasFreshEstimate(fund)) {
        const nav = parseFloat(fund?.dwjz)
        return Number.isFinite(nav) && nav > 0 ? nav : 0
      }
      const prev = parseFloat(fund?.prevDwjz)
      if (Number.isFinite(prev) && prev > 0) return prev
      const nav = parseFloat(fund?.dwjz)
      return Number.isFinite(nav) && nav > 0 ? nav : 0
    }

    // 持仓金额 = 最新公布净值 × 份额；盘中净值未更新时保持在上一公布净值口径。
    const getHoldingAmount = (fund) => {
      const h = holdings.value[fund.code]
      if (!h || !h.share) return 0
      return h.share * getLatestPublishedPrice(fund)
    }

    const getHoldingCostAmount = (fund) => {
      const h = holdings.value[fund.code]
      if (!h || !h.share || !h.cost) return 0
      return h.share * h.cost
    }

    // 估算金额 = 估算净值 × 份额
    const getHoldingEstimatedAmount = (fund) => {
      const h = holdings.value[fund.code]
      if (!h || !h.share) return 0
      const nav = getCurrentPrice(fund)
      return h.share * nav
    }

    // 今日收益 = 份额 × (估算净值 - 上一交易日净值)
    const getHoldingProfitToday = (fund) => {
      const h = holdings.value[fund.code]
      if (!h || !h.share) return 0
      const gsz = getCurrentPrice(fund)
      const dwjz = getPreviousPrice(fund)
      return h.share * (gsz - dwjz)
    }

    const getHoldingProfitTotal = (fund) => {
      const h = holdings.value[fund.code]
      if (!h || !h.share || !h.cost) return 0
      const stored = h.profit ?? 0
      return hasFreshEstimate(fund) ? stored + getHoldingProfitToday(fund) : stored
    }

    const getHoldingPrincipalAmount = (fund) => {
      return getHoldingAmount(fund) - getHoldingProfitTotal(fund)
    }

    const getHoldingReturnRate = (fund) => {
      const principal = getHoldingPrincipalAmount(fund)
      if (!principal) return 0
      return (getHoldingProfitTotal(fund) / principal) * 100
    }

    const ensureProfitNavDates = (fundList = funds.value) => {
      let changed = false
      const newHoldings = { ...holdings.value }
      fundList.forEach(fund => {
        const h = newHoldings[fund.code]
        const navDate = getDateText(fund?.jzrq)
        if (h && h.share && navDate && !h.profit_nav_date) {
          newHoldings[fund.code] = { ...h, profit_nav_date: navDate }
          changed = true
        }
      })
      if (changed) {
        holdings.value = newHoldings
        localStorage.setItem('realtime_holdings', JSON.stringify(newHoldings))
      }
    }

    const settleOfficialNavProfits = (fundList = funds.value) => {
      let changed = false
      const newHoldings = { ...holdings.value }
      fundList.forEach(fund => {
        const h = newHoldings[fund.code]
        const navDate = getDateText(fund?.jzrq)
        if (!h || !h.share || !navDate || hasFreshEstimate(fund)) return
        if (h.profit_nav_date && h.profit_nav_date >= navDate) return

        newHoldings[fund.code] = {
          ...h,
          profit: parseFloat(((h.profit ?? 0) + getHoldingProfitToday(fund)).toFixed(2)),
          profit_nav_date: navDate
        }
        changed = true
      })
      if (changed) {
        holdings.value = newHoldings
        localStorage.setItem('realtime_holdings', JSON.stringify(newHoldings))
      }
    }

    const getHoldingProfitTodayClass = (fund) => getValueClass(getHoldingProfitToday(fund))
    const getHoldingProfitTotalClass = (fund) => getValueClass(getHoldingProfitTotal(fund))

    // 根据金额和净值计算份额
    const calculateShare = (amount, nav) => {
      const a = parseFloat(amount)
      const n = parseFloat(nav)
      if (isNaN(a) || isNaN(n) || n <= 0) return 0
      return a / n
    }

    const formatMoney = (value) => {
      const num = Number(value)
      return Number.isFinite(num) ? num.toFixed(2) : '0.00'
    }

    const formatShare = (value) => {
      const num = Number(value)
      return Number.isFinite(num) ? num.toFixed(2) : '0.00'
    }

    const saveTradeRecords = () => {
      localStorage.setItem('realtime_trade_records', JSON.stringify(tradeRecords.value))
    }

    const buildTradeRecord = (fund, type, inputValue, nav, tradeDate, status = 'settled', txnId = '') => {
      const amount = type === 'buy' ? inputValue : inputValue * nav
      const share = type === 'buy' ? inputValue / nav : inputValue
      return {
        id: txnId || genTxnId(),
        txnId,
        fundCode: fund.code,
        fundName: fund.name || fund.code,
        type,
        tradeDate,
        amount: Number.isFinite(amount) ? amount : 0,
        share: Number.isFinite(share) ? share : 0,
        nav,
        status,
        createdAt: new Date().toISOString(),
        settledAt: status === 'settled' ? new Date().toISOString() : ''
      }
    }

    const upsertTradeRecord = (record) => {
      const idx = tradeRecords.value.findIndex(r => r.id === record.id || (record.txnId && r.txnId === record.txnId))
      if (idx >= 0) {
        const next = [...tradeRecords.value]
        next[idx] = { ...next[idx], ...record }
        tradeRecords.value = next
      } else {
        tradeRecords.value = [record, ...tradeRecords.value]
      }
      saveTradeRecords()
    }

    const removeTradeRecordByTxnId = (txnId) => {
      tradeRecords.value = tradeRecords.value.filter(r => r.txnId !== txnId)
      saveTradeRecords()
    }

    const getTradeStatusText = (status) => status === 'pending' ? '挂起' : '已更新'

    const getLegacyPendingRecord = (txn, fund) => {
      const nav = Number(txn.nav) || getFundNavByDate(fund, txn.tradeDate) || getCurrentPrice(fund)
      return buildTradeRecord(fund, txn.type, Number(txn.inputValue) || 0, nav || 0, txn.tradeDate, 'pending', txn.id)
    }

    const getFundTradeRecords = (fund) => {
      if (!fund?.code) return []
      const records = tradeRecords.value.filter(r => r.fundCode === fund.code)
      const recordedTxnIds = new Set(records.map(r => r.txnId).filter(Boolean))
      const legacyPending = pendingTxns.value
        .filter(txn => txn.fundCode === fund.code && !recordedTxnIds.has(txn.id))
        .map(txn => getLegacyPendingRecord(txn, fund))
      return [...records, ...legacyPending].sort((a, b) => {
        const aTime = a.createdAt || a.tradeDate || ''
        const bTime = b.createdAt || b.tradeDate || ''
        return String(bTime).localeCompare(String(aTime))
      })
    }

    const toggleCollapse = (code) => {
      const next = new Set(collapsedCodes.value)
      if (next.has(code)) {
        next.delete(code)
      } else {
        next.add(code)
      }
      collapsedCodes.value = next
      localStorage.setItem('realtime_collapsed', JSON.stringify([...next]))
    }

    const isSelected = (code) => selectedFunds.value.some(f => f.CODE === code)

    const openAddFundModal = () => {
      addFundModalOpen.value = true
      searchTerm.value = ''
      searchResults.value = []
      selectedFunds.value = []
      showDropdown.value = false
    }

    const closeAddFundModal = () => {
      addFundModalOpen.value = false
      searchTerm.value = ''
      searchResults.value = []
      selectedFunds.value = []
      showDropdown.value = false
    }

    const selectFundForAdd = (fund) => {
      selectedFunds.value = [fund]
    }

    const toggleSelectFund = (fund) => {
      selectFundForAdd(fund)
    }

    const mapPortfolioHoldings = (portfolio = {}) => {
      const rawList = portfolio.stock_codes_new || portfolio.stock_codes || []
      if (!Array.isArray(rawList)) return []
      return rawList.slice(0, 10).map((item, idx) => {
        if (typeof item === 'string') {
          const code = item.includes('.') ? item.split('.').pop() : item
          return {
            code,
            name: `持仓股票${idx + 1}`,
            weight: '-',
            change: null
          }
        }
        return {
          code: item.code || item.original_code || `STK${idx + 1}`,
          name: item.name || `持仓股票${idx + 1}`,
          weight: item.ratio != null ? `${item.ratio}%` : '-',
          change: null
        }
      })
    }

    const mapFundDetailToRealtime = (detail, fallbackCode) => {
      const realtime = detail?.realtime_estimate || {}
      const basic = detail?.basic_info || {}
      const changeNum = Number(realtime.estimate_change)
      const trend = Array.isArray(detail?.net_worth_trend) ? detail.net_worth_trend : []
      const trendNavPoints = trend
        .map(item => {
          const nav = Number(item?.net_worth ?? item?.y ?? item?.value)
          let date = item?.date ? String(item.date).slice(0, 10) : ''
          if (!date && item?.x) {
            const ts = Number(item.x)
            if (Number.isFinite(ts)) date = new Date(ts).toISOString().slice(0, 10)
          }
          return Number.isFinite(nav) && nav > 0 && date ? { date, nav } : null
        })
        .filter(Boolean)
        .sort((a, b) => a.date.localeCompare(b.date))
      const latestTrendNav = trendNavPoints[trendNavPoints.length - 1]
      const previousTrendNav = trendNavPoints[trendNavPoints.length - 2]
      const realtimeNavDate = getDateText(realtime.net_worth_date)
      const latestOfficialNav = latestTrendNav && (!realtimeNavDate || latestTrendNav.date >= realtimeNavDate)
        ? latestTrendNav
        : null
      const effectiveNavDate = latestOfficialNav?.date || realtimeNavDate
      const realtimeEstimateDate = getDateText(realtime.estimate_time)
      const shouldUseEstimateChange = !!realtimeEstimateDate && !!effectiveNavDate && realtimeEstimateDate > effectiveNavDate
      const estimatedNav = Number(realtime.estimate_value)
      const baseOfficialNav = latestOfficialNav?.nav ?? Number(realtime.net_worth)
      const estimateChangeFromNav = shouldUseEstimateChange
        && Number.isFinite(estimatedNav)
        && Number.isFinite(baseOfficialNav)
        && baseOfficialNav > 0
          ? ((estimatedNav - baseOfficialNav) / baseOfficialNav) * 100
          : null
      const officialChange = latestOfficialNav && previousTrendNav?.nav
        ? ((latestOfficialNav.nav - previousTrendNav.nav) / previousTrendNav.nav) * 100
        : null
      return {
        code: realtime.fund_code || basic.fund_code || fallbackCode,
        name: realtime.name || basic.fund_name || fallbackCode,
        dwjz: latestOfficialNav ? String(latestOfficialNav.nav) : realtime.net_worth,
        prevDwjz: previousTrendNav?.nav ? String(previousTrendNav.nav) : realtime.net_worth,
        gsz: realtime.estimate_value,
        gztime: realtime.estimate_time,
        jzrq: latestOfficialNav ? latestOfficialNav.date : realtime.net_worth_date,
        gszzl: shouldUseEstimateChange
          ? (Number.isFinite(estimateChangeFromNav) ? estimateChangeFromNav : (Number.isFinite(changeNum) ? changeNum : 0))
          : (latestOfficialNav && Number.isFinite(officialChange)
            ? officialChange
            : (Number.isFinite(changeNum) ? changeNum : 0)),
        holdings: mapPortfolioHoldings(detail?.portfolio),
        netWorthTrend: trend,
        totalReturnTrend: Array.isArray(detail?.total_return_trend) ? detail.total_return_trend : []
      }
    }

    const parseTrendPoint = (item) => {
      if (!item || typeof item !== 'object') return null
      const navRaw = item.net_worth ?? item.y ?? item.value
      const nav = Number(navRaw)
      if (!Number.isFinite(nav) || nav <= 0) return null

      let dateText = ''
      if (typeof item.date === 'string' && item.date) {
        dateText = item.date.slice(0, 10)
      } else if (item.x) {
        const ts = Number(item.x)
        if (Number.isFinite(ts)) {
          const d = new Date(ts)
          dateText = d.toISOString().slice(0, 10)
        }
      }
      if (!dateText) return null
      return { date: dateText, nav }
    }

    const getFundTrendSeries = (fund) => {
      // 使用单位净值走势数据（totalReturnTrend 是业绩对比基准，含多系列，不适合画走势图）
      if (!Array.isArray(fund?.netWorthTrend)) return []
      return fund.netWorthTrend
        .map(parseTrendPoint)
        .filter(Boolean)
        .sort((a, b) => a.date.localeCompare(b.date))
    }

    const getFundNavByDate = (fund, dateStr) => {
      const trend = getFundTrendSeries(fund)
      if (!trend.length) return parseFloat(fund?.dwjz) || 0
      const target = String(dateStr || '').slice(0, 10)
      if (!target) return parseFloat(fund?.dwjz) || trend[trend.length - 1].nav || 0

      let matched = null
      for (const point of trend) {
        if (point.date <= target) {
          matched = point
        } else {
          break
        }
      }
      return matched?.nav || parseFloat(fund?.dwjz) || trend[trend.length - 1].nav || 0
    }

    // 判断净值走势中是否有精确匹配 dateStr 的净值数据点
    const hasExactNavForDate = (fund, dateStr) => {
      const trend = getFundTrendSeries(fund)
      if (!trend.length) return false
      const target = String(dateStr || '').slice(0, 10)
      if (!target) return false
      return trend.some(p => p.date === target)
    }

    // 判断交易是否应挂起（当日净值尚未公布）
    const isTradeDatePending = (fund, tradeDate) => {
      if (!fund || !tradeDate) return false
      const today = new Date().toISOString().slice(0, 10)
      if (tradeDate !== today) return false
      return !hasExactNavForDate(fund, tradeDate)
    }

    const getFundSparklinePoints = (fund) => {
      const trend = getFundTrendSeries(fund)
      if (!trend.length) return []
      const recent = trend.slice(-24)
      const min = Math.min(...recent.map(p => p.nav))
      const max = Math.max(...recent.map(p => p.nav))
      const span = max - min || 1
      return recent.map((p, i) => ({
        x: (i / (recent.length - 1 || 1)) * 100,
        y: 26 - ((p.nav - min) / span) * 22
      }))
    }

    const getSparklinePath = (points) => {
      if (!points || points.length < 2) return ''
      return points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')
    }

    const getSparklineFill = (points, baseY = 30) => {
      if (!points || points.length < 2) return ''
      const line = getSparklinePath(points)
      const firstX = points[0].x.toFixed(2)
      const lastX = points[points.length - 1].x.toFixed(2)
      return `${line} L${lastX},${baseY} L${firstX},${baseY} Z`
    }

    const openFundDetail = (fund) => {
      emit('view-detail', {
        code: fund.code,
        name: fund.name
      })
    }

    const getFundSparklinePoints3m = (fund) => {
      const trend = getFundTrendSeries(fund)
      if (!trend.length) return []

      const cutoff = new Date()
      cutoff.setMonth(cutoff.getMonth() - 3)
      const cutoffText = cutoff.toISOString().slice(0, 10)

      let series = trend.filter(p => p.date >= cutoffText)
      if (series.length < 2) {
        series = trend.slice(-24)
      }
      if (series.length < 2) return []

      const min = Math.min(...series.map(p => p.nav))
      const max = Math.max(...series.map(p => p.nav))
      const span = max - min || 1
      return series.map((p, i) => ({
        x: (i / (series.length - 1 || 1)) * 100,
        y: 26 - ((p.nav - min) / span) * 22
      }))
    }

    const getFundMiniChart3m = (fund) => {
      const trend = getFundTrendSeries(fund)
      if (!trend.length) {
        return { points: [], yTicks: [], xTicks: [], trendUp: false }
      }

      const cutoff = new Date()
      cutoff.setMonth(cutoff.getMonth() - 3)
      const cutoffText = cutoff.toISOString().slice(0, 10)
      let series = trend.filter(p => p.date >= cutoffText)
      if (series.length < 2) series = trend.slice(-24)
      if (series.length < 2) {
        return { points: [], yTicks: [], xTicks: [], trendUp: false }
      }

      const startNav = series[0].nav || 1
      const pctSeries = series.map(p => ({
        date: p.date,
        pct: ((p.nav - startNav) / startNav) * 100
      }))

      const rawMin = Math.min(...pctSeries.map(p => p.pct))
      const rawMax = Math.max(...pctSeries.map(p => p.pct))
      const pad = Math.max((rawMax - rawMin) * 0.12, 0.25)
      const min = rawMin - pad
      const max = rawMax + pad
      const span = max - min || 1

      const plotLeft = 20
      const plotRight = 106
      const plotTop = 5
      const plotBottom = 48
      const plotW = plotRight - plotLeft
      const plotH = plotBottom - plotTop

      const points = pctSeries.map((p, i) => ({
        x: plotLeft + (i / (pctSeries.length - 1 || 1)) * plotW,
        y: plotBottom - ((p.pct - min) / span) * plotH
      }))

      const yTickCount = 5
      const yTicks = Array.from({ length: yTickCount }, (_, i) => {
        const ratio = i / (yTickCount - 1)
        const y = plotBottom - ratio * plotH
        const val = min + ratio * span
        return {
          y,
          label: `${val.toFixed(2)}%`
        }
      })

      const xTickCount = 6
      const xTicks = Array.from({ length: xTickCount }, (_, i) => {
        const idx = Math.min(
          pctSeries.length - 1,
          Math.round((i / (xTickCount - 1 || 1)) * (pctSeries.length - 1))
        )
        return {
          x: points[idx].x,
          label: pctSeries[idx].date.slice(5)
        }
      })

      const trendUp = pctSeries[pctSeries.length - 1].pct >= pctSeries[0].pct
      return { points, yTicks, xTicks, trendUp }
    }

    const getTrendColorClass3m = (fund) => {
      const trend = getFundTrendSeries(fund)
      if (!trend.length) return 'down'

      const cutoff = new Date()
      cutoff.setMonth(cutoff.getMonth() - 3)
      const cutoffText = cutoff.toISOString().slice(0, 10)
      let series = trend.filter(p => p.date >= cutoffText)
      if (series.length < 2) series = trend.slice(-24)
      if (series.length < 2) return 'down'

      return series[series.length - 1].nav >= series[0].nav ? 'up' : 'down'
    }

    // 搜索基金
    const performSearch = async () => {
      const keyword = String(searchTerm.value || '').trim()
      if (!keyword) {
        searchResults.value = []
        return
      }
      try {
        searchLoading.value = true
        const res = await fundAPI.searchFunds(keyword)
        const payload = res?.data?.data
        const list = Array.isArray(payload) ? payload : (payload?.funds || [])
        searchResults.value = list.map(item => ({
          CODE: item.fund_code || item.CODE || item.code,
          NAME: item.fund_name || item.NAME || item.name
        })).filter(item => item.CODE && item.NAME)
        showDropdown.value = true

        // 与顶部搜索一致：输入6位代码时，优先命中精确项并自动选择
        if (/^\d{6}$/.test(keyword)) {
          const exact = searchResults.value.find(item => item.CODE === keyword)
          if (exact) {
            selectedFunds.value = [exact]
          }
        }
      } catch (e) {
        console.error('搜索失败', e)
        searchResults.value = []
      } finally {
        searchLoading.value = false
      }
    }

    const handleSearchInput = () => {
      if (searchTimeoutRef.value) clearTimeout(searchTimeoutRef.value)
      if (!String(searchTerm.value || '').trim()) {
        searchResults.value = []
        return
      }
      searchTimeoutRef.value = setTimeout(() => performSearch(), 150)
    }

    // 通过后端接口获取基金数据
    const fetchFundData = async (code) => {
      try {
        const freshRes = await fundAPI.getFundCompareData(code, true)
        return mapFundDetailToRealtime(freshRes?.data || {}, code)
      } catch (e) {
        try {
          const res = await fundAPI.getFundDetail(code)
          return mapFundDetailToRealtime(res?.data || {}, code)
        } catch {
          const cachedRes = await fundAPI.getFundCompareData(code)
          return mapFundDetailToRealtime(cachedRes?.data || {}, code)
        }
      }
    }

    // 从自选添加
    const addFundToRealtime = async (fundInfo) => {
      const code = fundInfo.fund_code || fundInfo.code || fundInfo.CODE
      if (!code) return
      
      // 已存在检查
      if (funds.value.some(f => f.code === String(code))) {
         return 
      }
      
      refreshing.value = true
      try {
        const data = await fetchFundData(String(code))
        funds.value = [...funds.value, data]
        localStorage.setItem('realtime_funds', JSON.stringify(funds.value))
        fundOrder.value = [...fundOrder.value.filter(c => c !== String(code)), String(code)]
        localStorage.setItem('realtime_fund_order', JSON.stringify(fundOrder.value))
      } catch(e) {
          console.error(e)
      } finally {
          refreshing.value = false
      }
    }

    const pickSearchCandidate = () => {
      const keyword = String(searchTerm.value || '').trim()
      if (selectedFunds.value[0]) return selectedFunds.value[0]
      if (/^\d{6}$/.test(keyword)) {
        const exact = searchResults.value.find(item => item.CODE === keyword)
        if (exact) return exact
      }
      return searchResults.value.length === 1 ? searchResults.value[0] : null
    }

    const confirmAddFund = async () => {
      if (!selectedFunds.value.length && String(searchTerm.value || '').trim()) {
        await performSearch()
      }

      const fund = pickSearchCandidate()
      if (!fund?.CODE) return

      activeTab.value = 'all'
      if (funds.value.some(existing => existing.code === fund.CODE)) {
        closeAddFundModal()
        return
      }

      refreshing.value = true
      try {
        const data = await fetchFundData(fund.CODE)
        const updated = [...funds.value, data]
        funds.value = updated
        localStorage.setItem('realtime_funds', JSON.stringify(updated))
        fundOrder.value = [...fundOrder.value.filter(c => c !== fund.CODE), fund.CODE]
        localStorage.setItem('realtime_fund_order', JSON.stringify(fundOrder.value))
        closeAddFundModal()
      } catch (e) {
        console.error(`添加基金 ${fund.CODE} 失败`, e)
      } finally {
        refreshing.value = false
      }
    }

    // 批量添加基金
    const batchAddFunds = async () => {
      // 允许直接输入6位代码后点“添加基金”
      if (selectedFunds.value.length === 0 && /^\d{6}$/.test(String(searchTerm.value || '').trim())) {
        await performSearch()
      }

      if (selectedFunds.value.length === 0) return
      refreshing.value = true
      
      try {
        const newFunds = []
        for (const f of selectedFunds.value) {
          if (funds.value.some(existing => existing.code === f.CODE)) continue
          try {
            const data = await fetchFundData(f.CODE)
            newFunds.push(data)
          } catch (e) {
            console.error(`添加基金 ${f.CODE} 失败`, e)
          }
        }
        
        if (newFunds.length > 0) {
          const updated = [...funds.value, ...newFunds]
          funds.value = updated
          localStorage.setItem('realtime_funds', JSON.stringify(updated))
          const newCodes = newFunds.map(f => f.code)
          fundOrder.value = [...fundOrder.value, ...newCodes]
          localStorage.setItem('realtime_fund_order', JSON.stringify(fundOrder.value))
        }
        
        selectedFunds.value = []
        searchTerm.value = ''
        searchResults.value = []
        showDropdown.value = false
        activeTab.value = 'all'
      } catch (e) {
        console.error('批量添加失败', e)
      } finally {
        refreshing.value = false
      }
    }

    // 刷新所有基金
    const refreshAll = async () => {
      if (refreshing.value || funds.value.length === 0) return
      refreshing.value = true
      
      try {
        ensureProfitNavDates(funds.value)
        const updated = []
        for (const fund of funds.value) {
          try {
            const data = await fetchFundData(fund.code)
            updated.push(data)
          } catch (e) {
            console.error(`刷新基金 ${fund.code} 失败`, e)
            updated.push(fund) // 保留旧数据
          }
        }
        
        funds.value = updated
        localStorage.setItem('realtime_funds', JSON.stringify(updated))
        settleOfficialNavProfits(updated)

        // 检查挂起交易是否可以结算
        settlePendingTxnsIfReady()
      } catch (e) {
        console.error('刷新失败', e)
      } finally {
        refreshing.value = false
        updateNowTime()
      }
    }

    // 删除基金
    const removeFund = (code) => {
      funds.value = funds.value.filter(f => f.code !== code)
      localStorage.setItem('realtime_funds', JSON.stringify(funds.value))
      fundOrder.value = fundOrder.value.filter(c => c !== code)
      localStorage.setItem('realtime_fund_order', JSON.stringify(fundOrder.value))
      if (fundGroupMap.value[code]) {
        const newMap = { ...fundGroupMap.value }
        delete newMap[code]
        fundGroupMap.value = newMap
        localStorage.setItem('realtime_fund_group_map', JSON.stringify(newMap))
      }
      if (activeTab.value !== 'all' && displayFunds.value.length === 0) {
        activeTab.value = 'all'
      }
    }

    // 拖拽排序
    const onDragStart = (event, index) => {
      dragIndex.value = index
      event.dataTransfer.effectAllowed = 'move'
    }

    const onDragOver = (event, index) => {
      event.preventDefault()
      dragOverIndex.value = index
    }

    const onDragEnd = () => {
      if (dragIndex.value !== null && dragOverIndex.value !== null && dragIndex.value !== dragOverIndex.value) {
        const displayCodes = displayFunds.value.map(f => f.code)
        const [moved] = displayCodes.splice(dragIndex.value, 1)
        displayCodes.splice(dragOverIndex.value, 0, moved)
        fundOrder.value = displayCodes
        localStorage.setItem('realtime_fund_order', JSON.stringify(displayCodes))
      }
      dragIndex.value = null
      dragOverIndex.value = null
    }

    // 分组管理
    const openAddGroupModal = () => {
      editingGroup.value = null
      groupName.value = ''
      showGroupModal.value = true
      nextTick(() => {
        const input = document.querySelector('.group-name-input')
        if (input) input.focus()
      })
    }

    const openEditGroupModal = (group) => {
      editingGroup.value = group
      groupName.value = group.name
      showGroupModal.value = true
      nextTick(() => {
        const input = document.querySelector('.group-name-input')
        if (input) input.focus()
      })
    }

    const closeGroupModal = () => {
      showGroupModal.value = false
      editingGroup.value = null
      groupName.value = ''
    }

    const saveGroup = () => {
      const name = groupName.value.trim()
      if (!name) return
      if (editingGroup.value) {
        const idx = portfolioGroups.value.findIndex(g => g.id === editingGroup.value.id)
        if (idx !== -1) portfolioGroups.value[idx].name = name
      } else {
        portfolioGroups.value.push({
          id: 'g_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
          name
        })
      }
      localStorage.setItem('realtime_portfolio_groups', JSON.stringify(portfolioGroups.value))
      closeGroupModal()
    }

    const deleteGroup = (groupId) => {
      const group = portfolioGroups.value.find(g => g.id === groupId)
      if (!group) return
      if (!confirm(`确定删除分组"${group.name}"吗？分组内的基金将回到默认状态。`)) return
      const newMap = { ...fundGroupMap.value }
      Object.keys(newMap).forEach(code => {
        if (newMap[code] === groupId) delete newMap[code]
      })
      fundGroupMap.value = newMap
      localStorage.setItem('realtime_fund_group_map', JSON.stringify(newMap))
      portfolioGroups.value = portfolioGroups.value.filter(g => g.id !== groupId)
      localStorage.setItem('realtime_portfolio_groups', JSON.stringify(portfolioGroups.value))
      if (activeTab.value === 'group_' + groupId) activeTab.value = 'all'
    }

    const assignFundToGroup = (fundCode, groupId) => {
      const newMap = { ...fundGroupMap.value }
      if (!groupId) {
        delete newMap[fundCode]
      } else {
        newMap[fundCode] = groupId
      }
      fundGroupMap.value = newMap
      localStorage.setItem('realtime_fund_group_map', JSON.stringify(newMap))
    }

    // 分组右键菜单
    const openGroupContextMenu = (event, groupId) => {
      contextMenu.value = { show: true, x: event.clientX, y: event.clientY, groupId }
    }

    const closeContextMenu = () => {
      contextMenu.value = { ...contextMenu.value, show: false }
    }

    const renameGroupFromMenu = () => {
      const group = portfolioGroups.value.find(g => g.id === contextMenu.value.groupId)
      if (group) openEditGroupModal(group)
      closeContextMenu()
    }

    const deleteGroupFromMenu = () => {
      deleteGroup(contextMenu.value.groupId)
      closeContextMenu()
    }

    // 持仓弹窗
    const openHoldingModal = (fund) => {
      holdingModal.value = { open: true, fund }
      tradeForm.value = { type: 'buy', inputValue: '', tradeDate: todayDate.value }
    }

    const openTradeHistory = (fund) => {
      tradeHistoryModal.value = { open: true, fund }
    }

    const closeTradeHistory = () => {
      tradeHistoryModal.value = { open: false, fund: null }
    }

    const openTradeModal = (fund, type) => {
      openHoldingModal(fund)
      tradeForm.value.type = type
    }

    const openEditModal = (fund) => {
      const h = holdings.value[fund.code]
      const currentNav = getCurrentPrice(fund) || parseFloat(fund.dwjz) || 1
      // 已有持仓无 profit 字段时，取当前市场盈亏作为默认值，避免覆盖为 0
      let defaultProfit = 0
      if (h) {
        defaultProfit = getHoldingProfitTotal(fund)
      }
      editForm.value = {
        fund,
        // 持有金额按最新公布净值计算，净值更新后会随份额同步调整。
        amount: h ? getHoldingAmount(fund).toFixed(2) : '',
        profit: parseFloat(defaultProfit.toFixed(2))
      }
      showEditModal.value = true
    }

    const closeEditModal = () => {
      showEditModal.value = false
      editForm.value = { fund: null, amount: '', profit: 0 }
    }

    const saveEdit = () => {
      const fund = editForm.value.fund
      if (!fund) return

      const amount = parseFloat(editForm.value.amount)
      const profit = parseFloat(editForm.value.profit) || 0
      // Auto-determine nav: use latest published nav (dwjz)
      const nav = parseFloat(fund.dwjz) || getCurrentPrice(fund) || 1

      if (!amount || !nav || nav <= 0) {
        closeEditModal()
        return
      }

      const share = amount / nav
      const newHoldings = { ...holdings.value }
      newHoldings[fund.code] = {
        share,
        cost: nav,
        buy_date: fund.jzrq || todayDate.value,
        profit: parseFloat(profit.toFixed(2)),
        profit_nav_date: getDateText(fund.jzrq) || todayDate.value
      }

      holdings.value = newHoldings
      localStorage.setItem('realtime_holdings', JSON.stringify(newHoldings))
      closeEditModal()
    }

    const closeHoldingModal = () => {
      holdingModal.value = { open: false, fund: null }
    }

    const clearHolding = () => {
      const fund = holdingModal.value.fund
      if (!fund) return
      
      const newHoldings = { ...holdings.value }
      delete newHoldings[fund.code]
      holdings.value = newHoldings
      localStorage.setItem('realtime_holdings', JSON.stringify(newHoldings))
      closeHoldingModal()
    }

    // 获取交易净值（根据所选交易日期从净值走势中查找）
    const getTradeNav = () => {
      const fund = holdingModal.value.fund
      if (!fund) return 0
      const date = tradeForm.value.tradeDate
      if (!date) return 0
      return getFundNavByDate(fund, date)
    }

    // 计算交易后总份额
    const getTradeResultShares = () => {
      const nav = getTradeNav()
      const inputValue = parseFloat(tradeForm.value.inputValue) || 0
      if (nav <= 0 || inputValue <= 0) return holdings.value[holdingModal.value.fund?.code]?.share || 0
      const currentShares = holdings.value[holdingModal.value.fund?.code]?.share || 0

      if (tradeForm.value.type === 'buy') {
        // 买入：金额 / 净值 = 所得份额
        const tradeShares = inputValue / nav
        return currentShares + tradeShares
      } else {
        // 卖出：直接扣除份额
        return currentShares - inputValue
      }
    }

    // 判断是否可以提交交易
    const canSubmitTrade = () => {
      const inputValue = parseFloat(tradeForm.value.inputValue)
      if (!inputValue || inputValue <= 0) return false
      if (getTradeNav() <= 0) return false
      if (getTradeResultShares() < 0) return false
      return true
    }

    // 提交按钮文字
    const submitButtonText = () => {
      const fund = holdingModal.value.fund
      const pending = isTradeDatePending(fund, tradeForm.value.tradeDate)
      const base = tradeForm.value.type === 'buy' ? '加仓' : '减仓'
      return pending ? `确认${base}并挂起` : `确认${base}`
    }

    // 结算一笔交易（直接更新持仓）
    const settleTrade = (fund, type, inputValue, nav, tradeDate, options = {}) => {
      const newHoldings = { ...holdings.value }
      const h = newHoldings[fund.code] || { share: 0, cost: 0, buy_date: '' }

      if (type === 'buy') {
        const tradeShares = inputValue / nav
        const newTotalShares = h.share + tradeShares
        const newAvgCost = h.share > 0
          ? (h.share * h.cost + inputValue) / newTotalShares
          : nav
        newHoldings[fund.code] = {
          share: newTotalShares,
          cost: newAvgCost,
          buy_date: h.buy_date || tradeDate || tradeForm.value.tradeDate,
          profit: h.profit ?? 0,
          profit_nav_date: h.profit_nav_date || getDateText(fund.jzrq) || tradeDate || tradeForm.value.tradeDate
        }
      } else {
        // 卖出：inputValue 是份额
        const newTotalShares = h.share - inputValue
        if (newTotalShares <= 0.01) {
          delete newHoldings[fund.code]
        } else {
          newHoldings[fund.code] = {
            share: newTotalShares,
            cost: h.cost,
            buy_date: h.buy_date,
            profit: h.profit ?? 0,
            profit_nav_date: h.profit_nav_date || getDateText(fund.jzrq) || tradeDate || tradeForm.value.tradeDate
          }
        }
      }

      holdings.value = newHoldings
      localStorage.setItem('realtime_holdings', JSON.stringify(newHoldings))

      if (options.record !== false) {
        const record = buildTradeRecord(fund, type, inputValue, nav, tradeDate, 'settled', options.txnId || '')
        if (options.txnId) {
          record.id = options.txnId
        }
        upsertTradeRecord(record)
      }
    }

    // 生成唯一 ID
    const genTxnId = () => {
      return 'txn_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8)
    }

    // 保存加减仓交易
    const saveTrade = () => {
      const fund = holdingModal.value.fund
      if (!fund || !canSubmitTrade()) return

      const inputValue = parseFloat(tradeForm.value.inputValue)
      const nav = getTradeNav()
      const tradeDate = tradeForm.value.tradeDate
      const type = tradeForm.value.type

      // 判断是否挂起
      if (isTradeDatePending(fund, tradeDate)) {
        // 挂起交易
        const txnId = genTxnId()
        const txn = {
          id: txnId,
          fundCode: fund.code,
          fundName: fund.name || fund.code,
          type,
          tradeDate,
          inputValue,
          nav,
          createdAt: new Date().toISOString()
        }
        upsertTradeRecord(buildTradeRecord(fund, type, inputValue, nav, tradeDate, 'pending', txnId))
        pendingTxns.value.push(txn)
        localStorage.setItem('realtime_pending_txns', JSON.stringify(pendingTxns.value))
        closeHoldingModal()
        return
      }

      // 不挂起，直接结算
      settleTrade(fund, type, inputValue, nav, tradeDate)
      closeHoldingModal()
    }

    // 取消挂起交易
    const cancelPendingTxn = (txnId) => {
      pendingTxns.value = pendingTxns.value.filter(t => t.id !== txnId)
      localStorage.setItem('realtime_pending_txns', JSON.stringify(pendingTxns.value))
      removeTradeRecordByTxnId(txnId)
    }

    // 检查并结算已就绪的挂起交易
    const settlePendingTxnsIfReady = () => {
      if (!pendingTxns.value.length) return

      const remaining = []
      let changed = false

      for (const txn of pendingTxns.value) {
        // 在 funds 列表中找到对应基金
        const fund = funds.value.find(f => f.code === txn.fundCode)
        if (!fund) {
          // 基金已被删除，保留挂起以便用户手动取消
          remaining.push(txn)
          continue
        }

        // 检查该交易日净值是否已公布
        if (hasExactNavForDate(fund, txn.tradeDate)) {
          const nav = getFundNavByDate(fund, txn.tradeDate)
          if (nav > 0) {
            settleTrade(fund, txn.type, txn.inputValue, nav, txn.tradeDate, { txnId: txn.id })
            changed = true
            continue  // 已结算，不加入 remaining
          }
        }

        remaining.push(txn)
      }

      if (changed) {
        pendingTxns.value = remaining
        localStorage.setItem('realtime_pending_txns', JSON.stringify(remaining))
      }
    }

    const saveRefreshMs = () => {
      localStorage.setItem('realtime_refresh_ms', refreshMs.value.toString())
      // 重启定时器
      startRefreshTimer()
    }

    const updateNowTime = () => {
      nowTime.value = new Date().toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      })
    }

    const exportData = () => {
      const payload = {
        funds: funds.value,
        holdings: holdings.value,
        pendingTxns: pendingTxns.value,
        tradeRecords: tradeRecords.value,
        fundOrder: fundOrder.value,
        portfolioGroups: portfolioGroups.value,
        fundGroupMap: fundGroupMap.value,
        exportedAt: new Date().toISOString()
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `gofundbot-realtime-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    }

    const importData = async (event) => {
      const file = event?.target?.files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        const parsed = JSON.parse(text)
        if (Array.isArray(parsed.funds)) {
          funds.value = parsed.funds
          localStorage.setItem('realtime_funds', JSON.stringify(parsed.funds))
        }
        if (parsed.holdings && typeof parsed.holdings === 'object') {
          holdings.value = parsed.holdings
          localStorage.setItem('realtime_holdings', JSON.stringify(parsed.holdings))
        }
        if (Array.isArray(parsed.pendingTxns)) {
          pendingTxns.value = parsed.pendingTxns
          localStorage.setItem('realtime_pending_txns', JSON.stringify(parsed.pendingTxns))
        }
        if (Array.isArray(parsed.tradeRecords)) {
          tradeRecords.value = parsed.tradeRecords
          localStorage.setItem('realtime_trade_records', JSON.stringify(parsed.tradeRecords))
        }
        if (Array.isArray(parsed.fundOrder)) {
          fundOrder.value = parsed.fundOrder
          localStorage.setItem('realtime_fund_order', JSON.stringify(parsed.fundOrder))
        }
        if (Array.isArray(parsed.portfolioGroups)) {
          portfolioGroups.value = parsed.portfolioGroups
          localStorage.setItem('realtime_portfolio_groups', JSON.stringify(parsed.portfolioGroups))
        }
        if (parsed.fundGroupMap && typeof parsed.fundGroupMap === 'object') {
          fundGroupMap.value = parsed.fundGroupMap
          localStorage.setItem('realtime_fund_group_map', JSON.stringify(parsed.fundGroupMap))
        }
        refreshAll()
      } catch (error) {
        console.error('导入失败', error)
      } finally {
        event.target.value = ''
      }
    }

    const startRefreshTimer = () => {
      if (refreshTimer.value) clearInterval(refreshTimer.value)
      refreshTimer.value = setInterval(() => {
        refreshAll()
      }, refreshMs.value)
    }

    // 点击外部关闭下拉框
    const handleClickOutside = (event) => {
      if (searchPanelRef.value && !searchPanelRef.value.contains(event.target) && dropdownRef.value && !dropdownRef.value.contains(event.target)) {
        showDropdown.value = false
      }
    }

    // ==================== 生命周期 ====================
    onMounted(() => {
      // 加载本地数据
      try {
        const savedFunds = JSON.parse(localStorage.getItem('realtime_funds') || '[]')
        if (Array.isArray(savedFunds) && savedFunds.length) {
          funds.value = savedFunds
          refreshAll()
        }
        
        const savedHoldings = JSON.parse(localStorage.getItem('realtime_holdings') || '{}')
        if (savedHoldings && typeof savedHoldings === 'object') {
          holdings.value = savedHoldings
        }
        
        const savedMs = parseInt(localStorage.getItem('realtime_refresh_ms') || '180000', 10)
        if (Number.isFinite(savedMs) && savedMs >= 5000) {
          refreshMs.value = savedMs
        }
        
        const savedCollapsed = JSON.parse(localStorage.getItem('realtime_collapsed') || '[]')
        if (Array.isArray(savedCollapsed)) {
          collapsedCodes.value = new Set(savedCollapsed)
        }

        const savedPending = JSON.parse(localStorage.getItem('realtime_pending_txns') || '[]')
        if (Array.isArray(savedPending)) {
          pendingTxns.value = savedPending
        }
        
        const savedTradeRecords = JSON.parse(localStorage.getItem('realtime_trade_records') || '[]')
        if (Array.isArray(savedTradeRecords)) {
          tradeRecords.value = savedTradeRecords
        }
        ensureProfitNavDates(funds.value)
      } catch (e) {
        console.error('加载本地数据失败', e)
      }

      // 加载手动排序
      try {
        const savedOrder = JSON.parse(localStorage.getItem('realtime_fund_order') || 'null')
        if (Array.isArray(savedOrder) && savedOrder.length) {
          fundOrder.value = savedOrder
        } else if (Array.isArray(savedFunds) && savedFunds.length) {
          fundOrder.value = savedFunds.map(f => f.code)
        }
      } catch (e) { /* ignore */ }

      // 加载分组
      try {
        const savedGroups = JSON.parse(localStorage.getItem('realtime_portfolio_groups') || '[]')
        if (Array.isArray(savedGroups)) portfolioGroups.value = savedGroups
      } catch (e) { /* ignore */ }

      // 加载分组映射
      try {
        const savedGroupMap = JSON.parse(localStorage.getItem('realtime_fund_group_map') || '{}')
        if (savedGroupMap && typeof savedGroupMap === 'object') fundGroupMap.value = savedGroupMap
      } catch (e) { /* ignore */ }

      startRefreshTimer()
      updateNowTime()
      timeTimer.value = setInterval(updateNowTime, 60000)
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('click', closeContextMenu)
      const savedSortBy = localStorage.getItem('realtime_sort_by')
      if (savedSortBy) sortBy.value = savedSortBy
      const savedUser = localStorage.getItem('gofundbot_user')
      if (savedUser) username.value = savedUser

      const savedThreshold = parseInt(localStorage.getItem('realtime_rebalance_threshold') || '8', 10)
      if (savedThreshold >= 1) rebalanceThreshold.value = savedThreshold
    })

    onUnmounted(() => {
      if (refreshTimer.value) clearInterval(refreshTimer.value)
      if (timeTimer.value) clearInterval(timeTimer.value)
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('click', closeContextMenu)
    })

    watch(sortBy, (value) => {
      localStorage.setItem('realtime_sort_by', value)
    })

    watch([hasRebalanceFunds, hasDividendFunds], ([hasReb, hasDiv]) => {
      if (activeTab.value === 'rebalance' && !hasReb) activeTab.value = 'all'
      if (activeTab.value === 'dividend' && !hasDiv) activeTab.value = 'all'
    })

    watch(rebalanceThreshold, (val) => {
      localStorage.setItem('realtime_rebalance_threshold', String(val))
    })

    return {
      funds,
      holdings,
      collapsedCodes,
      refreshing,
      refreshMs,
      searchTerm,
      searchResults,
      selectedFunds,
      addFundModalOpen,
      username,
      nowTime,
      sortBy,
      activeTab,
      displayFunds,
      emptyTitle,
      emptyHint,
      addFundToRealtime,
      showDropdown,
      dropdownRef,
      searchPanelRef,
      searchLoading,
      todayDate,
      holdingModal,
      tradeForm,
      isTradingTime,
      sortedFunds,
      hasHoldings,
      totalAsset,
      totalCost,
      totalPreviousAsset,
      totalProfitToday,
      totalProfitTotal,
      totalReturnRate,
      todayReturnRate,
      profitTodayClass,
      profitTotalClass,
      getChangeClass,
      formatGsz,
      formatChange,
      getHoldingAmount,
      getHoldingCostAmount,
      getHoldingEstimatedAmount,
      getHoldingProfitToday,
      getHoldingProfitTotal,
      getHoldingReturnRate,
      getHoldingProfitTodayClass,
      getHoldingProfitTotalClass,
      getFundTradeRecords,
      getTradeStatusText,
      formatMoney,
      formatShare,
      getPriceStatusLabel,
      toggleCollapse,
      isSelected,
      toggleSelectFund,
      openAddFundModal,
      closeAddFundModal,
      selectFundForAdd,
      getFundNavByDate,
      getFundSparklinePoints,
      getFundSparklinePoints3m,
      getFundMiniChart3m,
      getTrendColorClass3m,
      getSparklinePath,
      getSparklineFill,
      openFundDetail,
      hasFreshEstimate,
      handleSearchInput,
      confirmAddFund,
      batchAddFunds,
      refreshAll,
      removeFund,
      openHoldingModal,
      openTradeModal,
      openTradeHistory,
      closeTradeHistory,
      tradeHistoryModal,
      openEditModal,
      closeEditModal,
      saveEdit,
      showEditModal,
      editForm,
      closeHoldingModal,
      clearHolding,
      saveRefreshMs,
      exportData,
      importData,
      calculateShare,
      getTradeNav,
      getTradeResultShares,
      saveTrade,
      pendingTxns,
      showPending,
      hasExactNavForDate,
      canSubmitTrade,
      submitButtonText,
      cancelPendingTxn,
      settlePendingTxnsIfReady,
      hasRebalanceFunds,
      hasDividendFunds,
      rebalanceThreshold,
      dragIndex,
      dragOverIndex,
      fundOrder,
      onDragStart,
      onDragOver,
      onDragEnd,
      portfolioGroups,
      fundGroupMap,
      showGroupModal,
      editingGroup,
      groupName,
      contextMenu,
      openAddGroupModal,
      openEditGroupModal,
      closeGroupModal,
      saveGroup,
      deleteGroup,
      assignFundToGroup,
      openGroupContextMenu,
      closeContextMenu,
      renameGroupFromMenu,
      deleteGroupFromMenu
    }
  }
}
</script>
<style scoped>
.realtime-container {
  background: #f5f6f8;
  padding: 16px;
  font-family: sans-serif;
  color: #333;
}
.top-row {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
  background: #fff;
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 12px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}
.add-fund-trigger {
  font-weight: 700;
}
.sort-box {
  margin-left: auto;
}
.search-box {
  flex: 1;
  position: relative;
}
.search-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  outline: none;
}
.search-input:focus { border-color: #6075ff; border-radius: 6px;}
.search-dropdown-overlay {
  position: absolute; top: 100%; left: 0; right: 0; background: #fff; z-index: 99;
  border: 1px solid #ddd; border-radius: 6px; max-height: 200px; overflow-y: auto; margin-top: 4px;
}
.dropdown-item { padding: 8px 12px; cursor: pointer; }
.dropdown-item:hover { background: #f0f0f0; }

.btn {
  padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer;
}
.btn-primary { background: #6075ff; color: #fff; }
.select-sort { padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; }
.btn-blue { background: #1677ff; color: #fff; }
.btn-green { background: #52c41a; color: #fff; }
.hidden-file { display: none; }

.overview-box {
  background: #fff; padding: 16px; border-radius: 8px; margin-bottom: 16px;
}
.overview-head {
  display: flex; justify-content: space-between; margin-bottom: 16px; border-bottom: 1px solid #eee; padding-bottom: 12px;
}
.title-with-icon { font-weight: bold; font-size: 16px; }
.meta-info { font-size: 12px; color: #999; }
.overview-grid {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; text-align: center;
}
.overview-cell.purple { background: #6b46c1; color: #fff; border-radius: 6px; padding: 12px 0;}
.overview-cell { padding: 12px 0; border: 1px solid #eee; border-radius: 6px; }
.cell-label { font-size: 13px; color: #666; margin-bottom: 4px; }
.overview-cell.purple .cell-label { color: #eee; }
.cell-val { font-size: 18px; font-weight: bold; }
.overview-cell.purple .cell-val { color: #fff; }
.cell-val.flat { color: #1677ff; }

.content-tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.ctab { padding: 6px 12px; background: #eee; border-radius: 16px; font-size: 14px; cursor: pointer; }
.ctab.active { background: #fff; color: #1677ff; font-weight: bold; }

.fund-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.portfolio-empty {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: #fff;
  border: 1px dashed #d0d7e2;
  border-radius: 10px;
  color: #667085;
  text-align: center;
}
.portfolio-empty-icon {
  font-size: 34px;
}
.portfolio-empty-title {
  color: #1f2937;
  font-size: 16px;
  font-weight: 800;
}
.portfolio-empty-hint {
  max-width: 360px;
  margin-bottom: 6px;
  font-size: 13px;
}
.fund-item-card { background: #fff; border-radius: 10px; padding: 16px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); }
.card-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.c-title { font-weight: bold; font-size: 15px; }
.detail-link {
  flex: 1;
  border: none;
  background: transparent;
  padding: 0;
  color: #1f2937;
  cursor: pointer;
  text-align: left;
  line-height: 1.35;
  text-decoration: underline;
  text-decoration-color: rgba(22, 119, 255, 0.35);
  text-underline-offset: 5px;
}
.detail-link:hover {
  color: #1677ff;
  text-decoration-color: #1677ff;
}
.btn-del { background: #ff4d4f; color: #fff; border: none; padding: 2px 8px; border-radius: 4px; font-size: 12px; cursor: pointer;}
.c-tags { display: flex; gap: 8px; font-size: 12px; color: #666; margin-bottom: 16px; }
.tag { padding: 2px 6px; border-radius: 4px; color: white;}
.tag.code-tag {
  background: #f3f4f6;
  color: #4b5563;
  border: 1px solid #e5e7eb;
  font-weight: 700;
}
.tag.red { background: #ff4d4f; }
.tag.pink { background: #eb2f96; }

.c-mid-tabs { display: flex; border-bottom: 1px solid #eee; margin-bottom: 12px; }
.c-tab { flex: 1; text-align: center; padding: 8px 0; cursor: pointer; font-size: 13px; color: #666;}
.c-tab.active { color: #1677ff; border-bottom: 2px solid #1677ff; }
.c-tab:hover { color: #1677ff; background: #f7fbff; }

.c-hero { display: flex; justify-content: center; gap: 18px; margin-bottom: 14px; }
.hero-metric {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
}
.hero-chip { padding: 4px 12px; border-radius: 4px; font-weight: bold; font-size: 16px; }
.hero-chip.up { color: #f5222d; background: #fff1f0; }
.hero-chip.down { color: #52c41a; background: #f6ffed; }
.hero-chip.flat { color: #1677ff; background: #eef6ff; }
.hero-label {
  color: #98a2b3;
  font-size: 12px;
  font-weight: 700;
}
.hero-sub {
  color: #667085;
  font-size: 12px;
  font-weight: 700;
}

.c-holdings-area { background: #f8f9fc; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
.c-h-head { display: flex; justify-content: space-between; margin-bottom: 12px; font-size: 13px; font-weight: bold; align-items: center;}
.c-h-pen {
  border: none;
  background: transparent;
  color: #1677ff;
  font: inherit;
  font-weight: 700;
  margin-left: 8px;
  padding: 0;
  cursor: pointer;
}
.c-h-pen:hover { text-decoration: underline; text-underline-offset: 3px; }
.btn-sm {
  border: none;
  padding: 6px 14px;
  border-radius: 999px;
  cursor: pointer;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.2px;
  transition: all 0.18s ease;
  box-shadow: 0 2px 8px rgba(17, 24, 39, 0.12);
}
.btn-sm:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 14px rgba(17, 24, 39, 0.16);
}
.btn-sm:active {
  transform: translateY(0);
}
.b-trade { background: linear-gradient(135deg, #2d8cff 0%, #1f6bff 100%); }
.b-edit { background: linear-gradient(135deg, #8b9dc3 0%, #64748b 100%); margin-left: 8px; }

.c-h-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.grid-box { display: flex; justify-content: space-between; font-size: 12px; }
.g-label { color: #888; } .g-val { font-weight: bold; }
.g-val.up { color: #f5222d; } .g-val.down { color: #52c41a; } .g-val.flat { color: #1677ff; }

.c-bottom-nav { display: flex; justify-content: space-around; margin-bottom: 8px; }
.bn-col { text-align: center; font-size: 13px;}
.bn-label { color: #888; margin-bottom: 4px;}
.bn-val { font-weight: bold; }
.bn-val.up { color: #f5222d; } .bn-val.down { color: #52c41a; } .bn-val.flat { color: #1677ff; }

.c-chart {
  height: 132px;
  margin-top: 10px;
  margin-bottom: 8px;
  border: 1px solid #f3d7d7;
  border-radius: 8px;
  background: linear-gradient(180deg, #fffafa 0%, #fff4f4 100%);
  overflow: hidden;
  padding: 4px 6px;
}
.c-svg { width: 100%; height: 100%; }
.c-axis { stroke: #cfd7e5; stroke-width: 0.5; shape-rendering: crispEdges; }
.c-grid { stroke: #e9edf5; stroke-width: 0.5; shape-rendering: crispEdges; }
.c-fill { fill: rgba(245, 34, 45, 0.14); }
.c-fill.up { fill: rgba(245, 34, 45, 0.14); }
.c-fill.down { fill: rgba(82, 196, 26, 0.14); }
.c-line { fill: none; stroke: #ea4a4a; stroke-width: 1; shape-rendering: geometricPrecision; }
.c-line.up { stroke: #ea4a4a; }
.c-line.down { stroke: #33a853; }
.c-y-label { fill: #7f8897; font-size: 3.1px; }
.c-x-label { fill: #8a93a3; font-size: 2.9px; }
.spark-empty { height: 100%; display: flex; align-items: center; justify-content: center; color: #9aa0aa; font-size: 12px; }

.c-time { text-align: center; color: #ccc; font-size: 11px; }

.modal-overlay { position: fixed; top:0; left:0; right:0; bottom:0; background: rgba(17,24,39,0.55); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px;}
.modal-box { background: #fff; padding: 20px; border-radius: 10px; width: 420px; box-shadow: 0 24px 70px rgba(15, 23, 42, 0.22); }
.holding-modal {
  width: 560px;
  max-width: calc(100vw - 32px);
  padding: 24px;
  border-radius: 14px;
}
.trade-history-modal {
  width: 680px;
}
.trade-history-table {
  overflow-x: auto;
  border: 1px solid #edf0f5;
  border-radius: 8px;
}
.trade-history-row {
  min-width: 600px;
  display: grid;
  grid-template-columns: 72px 1.1fr 1fr 1fr 80px;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid #edf0f5;
  font-size: 13px;
}
.trade-history-row:last-child { border-bottom: none; }
.trade-history-head {
  background: #f7f9fc;
  color: #667085;
  font-weight: 800;
}
.trade-type,
.trade-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: fit-content;
  min-width: 44px;
  height: 24px;
  padding: 0 8px;
  border-radius: 999px;
  font-weight: 800;
}
.trade-type.buy { color: #f5222d; background: #fff1f0; }
.trade-type.sell { color: #16a34a; background: #f0fdf4; }
.trade-status.settled { color: #1677ff; background: #eef6ff; }
.trade-status.pending { color: #b45309; background: #fff7ed; }
.trade-history-empty {
  padding: 28px 0;
  text-align: center;
  color: #98a2b3;
  font-size: 14px;
}
.add-fund-modal {
  width: 520px;
  max-width: calc(100vw - 32px);
}
.modal-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.modal-title-row h3 {
  margin: 0;
  font-size: 18px;
}
.holding-title-row {
  margin-bottom: 16px;
}
.modal-kicker {
  margin-bottom: 4px;
  color: #1677ff;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.04em;
}
.modal-close {
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 6px;
  background: #f2f4f7;
  color: #667085;
  cursor: pointer;
  font-size: 20px;
  line-height: 1;
}
.modal-close:hover {
  background: #e8edf5;
  color: #344054;
}
.add-search-box {
  margin-bottom: 12px;
}
.add-search-input {
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  outline: none;
}
.add-search-input:focus {
  border-color: #6075ff;
  box-shadow: 0 0 0 3px rgba(96, 117, 255, 0.12);
}
.add-result-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 280px;
  overflow-y: auto;
  padding: 2px;
}
.add-result-item {
  display: grid;
  grid-template-columns: 86px 1fr;
  gap: 12px;
  align-items: center;
  width: 100%;
  border: 1px solid #e4e7ec;
  border-radius: 8px;
  background: #fff;
  padding: 10px 12px;
  cursor: pointer;
  text-align: left;
  color: #344054;
}
.add-result-item:hover {
  border-color: #6075ff;
  background: #f7f9ff;
}
.add-result-item.selected {
  border-color: #6075ff;
  background: #eef2ff;
}
.add-result-item .fund-code {
  font-weight: 700;
  color: #1677ff;
}
.add-result-item .fund-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.add-empty,
.add-loading {
  min-height: 96px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #98a2b3;
  background: #f9fafb;
  border: 1px dashed #d0d5dd;
  border-radius: 8px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
.modal-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 14px;
}
.modal-tab {
  border: none;
  background: transparent;
  color: #6b7484;
  font-size: 14px;
  font-weight: 700;
  padding: 10px 0;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}
.form-group { margin-bottom: 12px; } .modal-input { width: 100%; padding: 8px; box-sizing: border-box;}
.up { color: #f5222d !important; } .down { color: #52c41a !important; }

/* Elegant Modal Styles */
.elegant-tabs {
  background: #f4f6fb;
  border-radius: 8px;
  padding: 4px;
}
.holding-tabs {
  margin-bottom: 18px;
  background: #f3f6fc;
  border: 1px solid #e8eef8;
}
.elegant-tabs .modal-tab {
  background: transparent;
  color: #666;
  font-weight: 600;
  transition: all 0.2s;
  padding: 9px 0;
  border: none;
}
.elegant-tabs .modal-tab.active {
  background: #fff;
  color: #1677ff;
  box-shadow: 0 2px 6px rgba(22, 119, 255, 0.18);
  border-radius: 6px;
  font-weight: 700;
}
.holding-summary-card {
  padding: 16px;
  margin-bottom: 18px;
  border: 1px solid #e6edf7;
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
}
.fund-summary-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.holding-summary-card .fund-name {
  color: #111827;
  font-size: 17px;
  font-weight: 800;
  line-height: 1.35;
}
.holding-summary-card .fund-code {
  flex-shrink: 0;
  padding: 4px 8px;
  border-radius: 999px;
  background: #eaf2ff;
  color: #1677ff;
  font-size: 12px;
  font-weight: 800;
}
.fund-nav-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid #edf2f7;
}
.nav-label {
  display: block;
  margin-bottom: 4px;
  color: #667085;
  font-size: 12px;
  font-weight: 700;
}
.nav-value {
  color: #101828;
  font-size: 20px;
  font-weight: 800;
}
.nav-date {
  color: #98a2b3;
  font-size: 13px;
  white-space: nowrap;
}
.set-holding-form {
  padding-top: 2px;
}
.elegant-trade-box {
  padding-top: 16px;
}
.trade-toggle {
  display: flex;
  background: #f4f7fb;
  border: 1px solid #e4e9f2;
  border-radius: 10px;
  margin-bottom: 20px;
  overflow: hidden;
  padding: 3px;
}
.trade-toggle-btn {
  flex: 1;
  text-align: center;
  padding: 9px 0;
  font-size: 13px;
  font-weight: 700;
  color: #7f8794;
  cursor: pointer;
  transition: all 0.2s;
  border-radius: 8px;
}
.trade-toggle-btn.buy.active {
  background: #e9f2ff;
  color: #1677ff;
  box-shadow: 0 2px 6px rgba(22, 119, 255, 0.22);
}
.trade-toggle-btn.sell.active {
  background: #fff3e8;
  color: #f57c00;
  box-shadow: 0 2px 6px rgba(245, 124, 0, 0.2);
}
.elegant-input-group label {
  font-size: 13px; color: #344054; margin-bottom: 8px; font-weight: 700; display: block;
}
.elegant-input-group .input-wrapper {
  display: flex; align-items: center; background: #fff; border: 1px solid #d0d5dd; border-radius: 10px; padding: 0 14px; transition: all 0.25s; min-height: 48px;
}
.elegant-input-group .input-wrapper:focus-within {
  border-color: #1677ff; box-shadow: 0 0 0 3px rgba(22, 119, 255, 0.12); background: #fff;
}
.elegant-input-group .prefix {
  color: #98a2b3; font-size: 16px; margin-right: 8px; font-weight: 800;
}
.elegant-input-group .modal-input.no-border {
  border: none; outline: none; box-shadow: none; font-size: 15px; padding: 12px 0; flex: 1; background: transparent; width: 100%; box-sizing: border-box;
}
.elegant-input-group .modal-input.highlight {
  font-weight: bold; color: #222; font-size: 16px;
}
.date-wrapper {
  padding-right: 10px;
}
.elegant-actions {
  margin-top: 24px; display: flex; gap: 12px; justify-content: flex-end; padding-top: 16px; border-top: 1px solid #f0f0f0;
}
.elegant-btn-cancel {
  background: #f2f4f7; color: #475467; font-weight: bold; padding: 10px 24px; border-radius: 8px; border: none; cursor: pointer; transition: background 0.2s;
}
.elegant-btn-cancel:hover { background: #e5e8ea; }
.elegant-btn-confirm {
  font-weight: bold; color: #fff; border: none; padding: 10px 32px; border-radius: 8px; cursor: pointer; transition: opacity 0.2s, transform 0.2s;
}
.elegant-btn-confirm.buy { background: #1677ff; }
.elegant-btn-confirm.buy:hover { opacity: 0.9; transform: translateY(-1px); }
.elegant-btn-confirm.sell { background: #f5222d; }
.elegant-btn-confirm.sell:hover { opacity: 0.9; transform: translateY(-1px); }
.elegant-btn-confirm:disabled {
  cursor: not-allowed;
  opacity: 0.45;
  transform: none;
}

.trade-inline-hint {
  margin-top: 8px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #667085;
}

.trade-inline-hint .warning {
  color: #f5222d;
  font-weight: 600;
}

/* 交易日期衍生的净值展示 */
.trade-nav-derived {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: #f0f5ff;
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #1677ff;
  font-weight: 600;
}

.nav-date-hint {
  color: #fa8c16;
  font-weight: 500;
  font-size: 12px;
}

.nav-date-hint.confirmed {
  color: #52c41a;
}

/* 挂起交易提示栏 */
.pending-txns-bar {
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}

.pending-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: #ad6800;
  user-select: none;
}

.pending-header:hover {
  background: #fff7cc;
}

.pending-toggle {
  font-size: 12px;
  color: #ad6800;
  font-weight: 500;
}

.pending-list {
  border-top: 1px solid #ffe58f;
}

.pending-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  font-size: 13px;
  border-bottom: 1px solid #fff3cc;
}

.pending-item:last-child {
  border-bottom: none;
}

.p-type {
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.p-type.buy {
  background: #f6ffed;
  color: #52c41a;
}

.p-type.sell {
  background: #fff1f0;
  color: #f5222d;
}

.p-name {
  flex: 1;
  color: #333;
}

.p-val {
  color: #1677ff;
  font-weight: 600;
}

.p-date {
  color: #999;
  font-size: 12px;
}

.btn-cancel-txn {
  padding: 4px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  background: #fff;
  color: #999;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s;
}

.btn-cancel-txn:hover {
  border-color: #f5222d;
  color: #f5222d;
}

/* --- Drag and Drop --- */
.fund-item-card.dragging {
  opacity: 0.5;
  transform: scale(0.98);
  transition: opacity 0.15s, transform 0.15s;
}

/* --- Rebalance Threshold --- */
.threshold-label {
  font-size: 12px;
  color: #6b7280;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 8px;
  background: #f3f4f6;
  border-radius: 12px;
  cursor: default;
}
.threshold-input {
  width: 48px;
  padding: 4px 6px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 13px;
  text-align: center;
  background: #fff;
}
.threshold-input:focus {
  outline: none;
  border-color: #1677ff;
}

/* --- Add Group Button --- */
.btn-add-group {
  border: 1px dashed #c0c7d0;
  background: transparent;
  color: #98a2b3;
  cursor: pointer;
  font-size: 14px;
  padding: 6px 10px;
  transition: all 0.2s;
}
.btn-add-group:hover {
  border-color: #1677ff;
  color: #1677ff;
  background: #f0f5ff;
}

/* --- Group Assignment Select --- */
.group-assign-wrapper {
  margin-left: auto;
}
.group-assign-select {
  padding: 2px 6px;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  font-size: 11px;
  color: #6b7280;
  background: #f9fafb;
  cursor: pointer;
  max-width: 100px;
}
.group-assign-select:focus {
  outline: none;
  border-color: #1677ff;
}

/* --- Context Menu --- */
.context-menu {
  position: fixed;
  z-index: 2000;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  min-width: 140px;
  overflow: hidden;
  padding: 4px 0;
}
.context-menu-item {
  padding: 8px 14px;
  font-size: 13px;
  cursor: pointer;
  color: #374151;
  transition: background 0.15s;
}
.context-menu-item:hover {
  background: #f3f4f6;
}
.context-menu-item.danger {
  color: #ef4444;
}
.context-menu-item.danger:hover {
  background: #fef2f2;
}

/* --- Hint label in forms --- */
.hint-label {
  font-size: 11px;
  color: #98a2b3;
  font-weight: 400;
}

/* --- Fund code in modal titles --- */
.fund-code-sm {
  font-size: 12px;
  font-weight: 400;
  color: #98a2b3;
  margin-left: 4px;
}

/* --- Scope Tag in Overview --- */
.scope-tag {
  font-size: 12px;
  font-weight: 500;
  color: #1677ff;
  background: #e6f4ff;
  border-radius: 10px;
  padding: 2px 8px;
  margin-left: 8px;
  vertical-align: middle;
}

/* --- Group Modal --- */
.group-modal {
  width: 360px;
  max-width: calc(100vw - 32px);
}
.group-name-input {
  margin-bottom: 12px;
}
</style>
