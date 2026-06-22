<template>
  <div class="fund-screening">
    <!-- 顶部状态栏 -->
    <div class="screening-header">
      <div class="header-actions">
        <div class="header-left">
          <span class="stat-chip">
            📦 {{ dbStatus.basic_count || 0 }} 只基金
          </span>
          <span class="stat-chip complete" v-if="dbStatus.risk_metrics_count">
            ✅ {{ dbStatus.risk_metrics_count }} 只数据完整
          </span>
          <span class="update-time-chip" v-if="dbStatus.latest_update">
            🕐 {{ formatDate(dbStatus.latest_update) }}
          </span>
        </div>
        <div class="header-right">
          <button
            class="btn-update"
            @click="openUpdateDialog"
            :disabled="updateStatus.running"
            title="选择要执行的更新任务"
          >
            {{ updateStatus.running ? '⏳ 更新中...' : '📥 更新数据' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="showUpdateDialog" class="modal-mask" @click.self="closeUpdateDialog">
      <div class="update-dialog">
        <div class="dialog-head">
          <div>
            <h3>选择更新内容</h3>
            <p>只执行你这次需要的任务，减少不必要的等待。</p>
          </div>
          <button class="dialog-close" @click="closeUpdateDialog">×</button>
        </div>

        <div class="task-list">
          <label class="task-item">
            <input type="checkbox" v-model="updateTasks.basic" />
            <span>
              <strong>更新基础数据</strong>
              <em>拉取基金排行、收益、类型等筛选基础字段</em>
            </span>
          </label>
          <label class="task-item">
            <input type="checkbox" v-model="updateTasks.rankings" />
            <span>
              <strong>计算同类排名</strong>
              <em>刷新 4433 所需的同类排名百分位</em>
            </span>
          </label>
          <label class="task-item">
            <input type="checkbox" v-model="updateTasks.risk" />
            <span>
              <strong>补充风险指标</strong>
              <em>拉历史净值计算回撤、波动率、夏普、卡玛</em>
            </span>
          </label>
          <label class="task-item">
            <input type="checkbox" v-model="updateTasks.industry" />
            <span>
              <strong>刷新基金所属板块</strong>
              <em>先构建股票行业字典，再根据重仓股分类基金；缺口股票会补查接口</em>
            </span>
          </label>
          <label class="task-item">
            <input type="checkbox" v-model="updateTasks.industry_performance" />
            <span>
              <strong>汇总板块走势</strong>
              <em>按基金所属板块聚合近3月、半年、1年、3年表现</em>
            </span>
          </label>
        </div>

        <div class="dialog-options">
          <label>
            <span>基础数据上限</span>
            <input v-model.number="updateLimit" type="number" min="1" placeholder="留空为全部" />
          </label>
          <label>
            <span>风险指标上限</span>
            <input v-model.number="preciseLimit" type="number" min="1" />
          </label>
          <label>
            <span>行业刷新上限</span>
            <input v-model.number="industryLimit" type="number" min="1" placeholder="留空为全部" />
          </label>
        </div>

        <div class="dialog-actions">
          <button class="btn-secondary" @click="closeUpdateDialog">取消</button>
          <button class="btn-update" :disabled="!hasSelectedUpdateTask" @click="startUpdate">
            开始更新
          </button>
        </div>
      </div>
    </div>

    <!-- 进行中的更新 — 内联进度条 -->
    <div v-if="updateStatus.running" class="inline-update-bar">
      <div class="inline-progress-info">
        <span class="inline-current">{{ updateStatus.current_fund || updateStatus.message }}</span>
        <span class="inline-count">
          <template v-if="updateStatus.total">{{ updateStatus.progress }}/{{ updateStatus.total }}</template>
          <template v-else>已处理 {{ updateStatus.success_count || 0 }}</template>
        </span>
      </div>
      <div class="progress-bar" :class="{ indeterminate: isProgressIndeterminate }">
        <div
          class="progress-fill"
          :class="{ indeterminate: isProgressIndeterminate }"
          :style="{ width: progressPercent + '%' }"
        ></div>
      </div>
      <button class="btn-stop-inline" @click="stopUpdate">⏹ 停止</button>
    </div>

    <!-- 筛选面板 -->
    <div class="screening-panel">
      <div class="filter-card">
        <!-- 基础筛选栏 -->
        <div class="filter-bar">
          <div class="filter-bar-row">
            <div class="search-wrap" ref="searchWrapRef">
              <span class="input-icon">🔎</span>
              <input
                v-model="filters.keyword"
                type="text"
                placeholder="基金代码/名称"
                class="filter-keyword"
                @keyup.enter="search(true)"
                @input="onKeywordInput"
                @focus="onKeywordFocus"
              />
              <div class="search-dropdown" v-if="searchSuggestions.length && showSearchDropdown">
                <div
                  v-for="item in searchSuggestions"
                  :key="item.CODE"
                  class="search-dropdown-item"
                  @click="selectSearchSuggestion(item)"
                >
                  <span class="sug-code">{{ item.CODE }}</span>
                  <span class="sug-name">{{ item.NAME }}</span>
                  <span class="sug-type">{{ item.TYPE }}</span>
                </div>
              </div>
            </div>
            <!-- 基金类型下拉 -->
            <div class="type-select-wrap" ref="typeDropdownRef">
              <span class="input-icon">📁</span>
              <div class="type-trigger" @click="showTypeDropdown = !showTypeDropdown">
                <span class="type-trigger-text">
                  {{ filters.fund_types.length ? filters.fund_types.length + ' 种类型' : '基金类型' }}
                </span>
                <span class="type-trigger-arrow">▼</span>
              </div>
              <div class="type-dropdown" v-show="showTypeDropdown" @click.stop>
                <div
                  v-for="cat in fundTypeCategories"
                  :key="cat.name"
                  class="type-cat-group"
                >
                  <div
                    class="type-cat-header"
                    :class="{ all: isCatAllSelected(cat), partial: isCatPartialSelected(cat) }"
                    @click="toggleCategoryTypes(cat)"
                  >
                    <span class="cat-check">
                      {{ isCatAllSelected(cat) ? '✓' : isCatPartialSelected(cat) ? '−' : '' }}
                    </span>
                    <span>{{ cat.icon }} {{ cat.name }}</span>
                  </div>
                  <div class="type-cat-items">
                    <label
                      v-for="t in cat.types"
                      :key="t.value"
                      class="type-item-label"
                      :class="{ active: filters.fund_types.includes(t.value) }"
                    >
                      <input
                        type="checkbox"
                        :value="t.value"
                        :checked="filters.fund_types.includes(t.value)"
                        @change="toggleSingleType(t.value)"
                        class="type-checkbox"
                      />
                      {{ t.label }}
                    </label>
                  </div>
                </div>
              </div>
            </div>
            <button class="btn-query" @click="search(true)">🔍 查询</button>
            <button class="btn-reset" @click="resetFilters">清空</button>
          </div>

          <!-- 已选类型标签 -->
          <div class="selected-types-tags" v-if="filters.fund_types.length">
            <span
              v-for="t in filters.fund_types"
              :key="t"
              class="type-tag-selected"
              @click="removeFundType(t)"
            >
              {{ getShortTypeName(t) }} ✕
            </span>
          </div>

          <div class="industry-filter-block" v-if="industryTags.length">
            <span class="industry-filter-label">板块筛选</span>
            <button
              class="industry-chip"
              :class="{ active: filters.industry_tags.length === 0 }"
              @click="clearIndustryTags"
            >
              全部
            </button>
            <button
              v-for="tag in industryTags"
              :key="tag.name"
              class="industry-chip"
              :class="{ active: filters.industry_tags.includes(tag.name) }"
              @click="toggleIndustryTag(tag.name)"
            >
              {{ tag.name }} <span>{{ tag.count }}</span>
            </button>
          </div>
        </div>

        <!-- 高级筛选 (可折叠) -->
        <div class="advanced-toggle" @click="showAdvanced = !showAdvanced">
          <span>⚙️ 高级筛选条件</span>
          <span class="toggle-arrow">{{ showAdvanced ? '▲' : '▼' }}</span>
        </div>
        <div class="advanced-section" v-show="showAdvanced">
          <div class="adv-grid-simple">
            <!-- 收益率 -->
            <div class="adv-item">
              <label>📈 近1年收益率</label>
              <div class="adv-range">
                <input v-model.number="advFilters.return_1y_min" type="number" placeholder="最小值" step="any" class="adv-inp" />
                <span class="adv-sep">~</span>
                <input v-model.number="advFilters.return_1y_max" type="number" placeholder="最大值" step="any" class="adv-inp" />
                <span class="adv-unit">%</span>
              </div>
            </div>
            <!-- 最大回撤 -->
            <div class="adv-item">
              <label>📉 最大回撤 ≤</label>
              <div class="adv-range">
                <input v-model.number="advFilters.max_drawdown_max" type="number" placeholder="如 20" step="any" class="adv-inp" />
                <span class="adv-unit">%</span>
              </div>
            </div>
            <!-- 夏普比率 -->
            <div class="adv-item">
              <label>⚖️ 夏普比率 ≥</label>
              <div class="adv-range">
                <input v-model.number="advFilters.sharpe_min" type="number" placeholder="如 1.5" step="0.1" class="adv-inp" />
              </div>
            </div>
            <!-- 波动率 -->
            <div class="adv-item">
              <label>🌊 年化波动率 ≤</label>
              <div class="adv-range">
                <input v-model.number="advFilters.volatility_max" type="number" placeholder="如 25" step="any" class="adv-inp" />
                <span class="adv-unit">%</span>
              </div>
            </div>
            <!-- 卡玛比率 -->
            <div class="adv-item">
              <label>🎯 卡玛比率 ≥</label>
              <div class="adv-range">
                <input v-model.number="advFilters.calmar_min" type="number" placeholder="如 1" step="0.1" class="adv-inp" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 筛选结果 -->
    <div class="results-section" v-if="results.length > 0 || loading">
      <div class="results-header">
        <div class="results-title-row">
          <h3>筛选结果 <span class="result-count">(共 {{ totalCount }} 只)</span></h3>
        </div>
        
        <!-- 类型快速筛选 - 多级菜单 -->
        <div class="quick-type-filter">
          <span class="filter-label-inline">类型筛选:</span>
          <div class="quick-type-menu">
            <!-- 全部按钮 -->
            <span 
              class="quick-tag"
              :class="{ active: quickTypeFilter === '' }"
              @click="setQuickTypeFilter('')"
            >
              全部
            </span>
            
            <!-- 多级分类下拉 -->
            <div 
              v-for="category in quickTypeCategories" 
              :key="category.name"
              class="quick-type-dropdown"
              @click.stop
            >
              <span 
                class="quick-tag dropdown-trigger"
                :class="{ 
                  active: isCategoryTypeActive(category),
                  'has-active': hasCategoryActiveType(category)
                }"
                @click="toggleQuickDropdown(category.name)"
              >
                {{ category.icon }} {{ category.name }}
                <span class="dropdown-arrow">▼</span>
              </span>
              
              <!-- 下拉菜单 -->
              <div 
                class="dropdown-menu"
                v-show="activeQuickDropdown === category.name"
              >
                <div
                  v-for="item in getFilteredCategoryTypes(category)"
                  :key="item.value"
                  class="dropdown-item"
                  :class="{ active: quickTypeFilter === item.value, unavailable: !item.available }"
                  @click="item.available && setQuickTypeFilter(item.value)"
                >
                  {{ item.label }}
                  <span v-if="!item.available" class="unavailable-hint">暂无</span>
                </div>
                <div v-if="getFilteredCategoryTypes(category).length === 0" class="dropdown-empty">
                  暂无此类型基金
                </div>
              </div>
            </div>
            
            <!-- 未分类的类型（如果有） -->
            <span 
              v-for="type in uncategorizedTypes" 
              :key="type"
              class="quick-tag"
              :class="{ active: quickTypeFilter === type }"
              @click="setQuickTypeFilter(type)"
            >
              {{ getShortTypeName(type) }}
            </span>
          </div>
        </div>
        
        <div class="sort-options">
          <label>排序:</label>
          <select v-model="sortBy" @change="search">
            <option value="sharpe_ratio_1y">夏普比率(1年)</option>
            <option value="return_1y">收益率(1年)</option>
            <option value="calmar_ratio_1y">卡玛比率(1年)</option>
            <option value="max_drawdown_1y">最大回撤(1年)</option>
            <option value="volatility_1y">波动率(1年)</option>
            <option value="fund_scale">基金规模</option>
          </select>
          <select v-model="sortOrder" @change="search">
            <option value="desc">降序</option>
            <option value="asc">升序</option>
          </select>
        </div>
      </div>

      <!-- 加载状态 -->
      <div v-if="loading" class="loading-state">
        <div class="loading-spinner large"></div>
        <p>正在筛选...</p>
      </div>

      <!-- 结果表格 -->
      <div v-else class="results-table-wrapper vxe-results-wrapper">
        <vxe-grid
          ref="screeningGridRef"
          v-bind="gridOptions"
          :data="results"
          :loading="loading"
          @sort-change="handleGridSort"
          @cell-click="handleGridCellClick"
        >
          <template #fundName="{ row }">
            <span class="fund-name-cell" :title="row.fund_name">{{ row.fund_name || '--' }}</span>
          </template>
          <template #industryTag="{ row }">
            <span class="industry-cell">{{ row.industry_tag_name || '混合型' }}</span>
          </template>
          <template #percent="{ row, column }">
            <span :class="getReturnClass(row[column.field])">{{ formatPercent(row[column.field]) }}</span>
          </template>
          <template #drawdown="{ row }">
            <span class="negative">{{ formatPercent(row.max_drawdown_1y, true) }}</span>
          </template>
          <template #number="{ row, column }">
            <span>{{ formatNumber(row[column.field]) }}</span>
          </template>
          <template #sharpe="{ row }">
            <span :class="getSharpeClass(row.sharpe_ratio_1y)">{{ formatNumber(row.sharpe_ratio_1y) }}</span>
          </template>
          <template #calmar="{ row }">
            <span :class="getCalmarClass(row.calmar_ratio_1y)">{{ formatNumber(row.calmar_ratio_1y) }}</span>
          </template>
          <template #pass4433="{ row }">
            <span v-if="row.pass_4433" class="pass-badge">✓</span>
            <span v-else class="fail-badge">-</span>
          </template>
          <template #actions="{ row }">
            <button class="btn-action" @click.stop="addToWatchlist(row)" title="加入自选">★</button>
            <button class="btn-action" @click.stop="addToCompare(row)" title="加入对比">▦</button>
          </template>
        </vxe-grid>
      </div>

      <!-- 分页 -->
      <div class="pagination" v-if="totalPages > 1">
        <button 
          class="page-btn" 
          :disabled="currentPage === 1"
          @click="changePage(currentPage - 1)"
        >
          上一页
        </button>
        <span class="page-info">
          第 {{ currentPage }} / {{ totalPages }} 页
        </span>
        <button 
          class="page-btn" 
          :disabled="currentPage === totalPages"
          @click="changePage(currentPage + 1)"
        >
          下一页
        </button>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else-if="searched && !loading" class="empty-state">
      <div class="empty-icon">📭</div>
      <p>未找到符合条件的基金</p>
      <p class="hint">请尝试调整筛选条件</p>
    </div>

    <!-- 初始状态 -->
    <div v-else class="initial-state">
      <div class="initial-icon">🎯</div>
      <p>选择筛选策略或设置筛选条件</p>
      <p class="hint">支持4433法则（同类排名）、夏普比率、卡玛比率等多维度筛选</p>
      <p class="hint sub-hint">注：4433法则的排名是在同类型基金中计算的</p>
    </div>
  </div>
</template>

<script>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { screeningAPI, watchlistAPI, fundAPI } from '../services/api'

export default {
  name: 'FundScreening',
  emits: ['view-fund', 'add-to-compare'],
  
  setup(props, { emit }) {
    // 数据库状态
    const dbStatus = ref({
      basic_count: 0,
      latest_update: null,
      type_counts: {}
    })
    
    // 更新状态
    const updateStatus = ref({
      running: false,
      progress: 0,
      total: 0,
      current_fund: '',
      success_count: 0,
      fail_count: 0,
      message: ''
    })
    
    const selectedFundTypes = ref([])
    const updateLimit = ref(null)
    const updateMode = ref('sync_nav')
    const preciseLimit = ref(500)
    const industryLimit = ref(null)
    const showUpdateDialog = ref(false)
    const updateTasks = reactive({
      basic: true,
      rankings: true,
      risk: false,
      industry: true,
      industry_performance: true
    })
    const hasSelectedUpdateTask = computed(() => Object.values(updateTasks).some(Boolean))
    
    // 高级筛选 — 直观的数值范围
    const showAdvanced = ref(false)
    const showTypeDropdown = ref(false)
    const typeDropdownRef = ref(null)
    const searchWrapRef = ref(null)

    // 搜索自动补全
    const searchSuggestions = ref([])
    const showSearchDropdown = ref(false)
    let searchDebounce = null

    const onKeywordInput = () => {
      clearTimeout(searchDebounce)
      const kw = filters.keyword.trim()
      if (!kw || kw.length < 2) {
        searchSuggestions.value = []
        showSearchDropdown.value = false
        return
      }
      searchDebounce = setTimeout(async () => {
        try {
          const res = await fundAPI.searchFunds(kw)
          searchSuggestions.value = (res.data.data || []).slice(0, 10)
          showSearchDropdown.value = searchSuggestions.value.length > 0
        } catch (e) {
          // ignore
        }
      }, 150)
    }

    const onKeywordFocus = () => {
      if (searchSuggestions.value.length > 0) {
        showSearchDropdown.value = true
      }
    }

    const selectSearchSuggestion = (item) => {
      filters.keyword = item.CODE
      showSearchDropdown.value = false
      search(true)
    }

    // 点击外部关闭搜索下拉
    const handleSearchClickOutside = (e) => {
      if (searchWrapRef.value && !searchWrapRef.value.contains(e.target)) {
        showSearchDropdown.value = false
      }
    }
    const advFilters = reactive({
      return_1y_min: null,
      return_1y_max: null,
      max_drawdown_max: null,
      sharpe_min: null,
      volatility_max: null,
      calmar_min: null,
    })

    const onTypeSelectChange = () => {}  // deprecated, kept for compat

    const removeFundType = (type) => {
      const idx = filters.fund_types.indexOf(type)
      if (idx > -1) filters.fund_types.splice(idx, 1)
    }

    // 类型下拉：切换单个类型
    const toggleSingleType = (type) => {
      const idx = filters.fund_types.indexOf(type)
      if (idx > -1) {
        filters.fund_types.splice(idx, 1)
      } else {
        filters.fund_types.push(type)
      }
    }

    // 类型下拉：点击分类头全选/取消该分类所有子类型
    const toggleCategoryTypes = (cat) => {
      const typeVals = cat.types.map(t => t.value)
      const allSelected = typeVals.every(v => filters.fund_types.includes(v))
      if (allSelected) {
        // 全部取消
        for (const v of typeVals) {
          const idx = filters.fund_types.indexOf(v)
          if (idx > -1) filters.fund_types.splice(idx, 1)
        }
      } else {
        // 全部选中
        for (const v of typeVals) {
          if (!filters.fund_types.includes(v)) {
            filters.fund_types.push(v)
          }
        }
      }
    }

    const isCatAllSelected = (cat) => cat.types.every(t => filters.fund_types.includes(t.value))
    const isCatPartialSelected = (cat) => {
      const s = cat.types.filter(t => filters.fund_types.includes(t.value)).length
      return s > 0 && s < cat.types.length
    }

    // 点击外部关闭类型下拉
    const handleTypeDropdownClick = (e) => {
      if (typeDropdownRef.value && !typeDropdownRef.value.contains(e.target)) {
        showTypeDropdown.value = false
      }
    }

    // 筛选条件
    const filters = reactive({
      keyword: '',
      fund_types: [],
      industry_tags: [],
      return_1y_min: null,
      return_1y_max: null,
      max_drawdown_max: null,
      sharpe_min: null,
      volatility_max: null,
      calmar_min: null
    })
    
    // 多级基金类型选项
    const fundTypeCategories = [
      {
        name: '偏股型',
        icon: '📈',
        expanded: true,
        types: [
          { value: '混合型-偏股', label: '偏股混合' },
          { value: '混合型-灵活', label: '灵活配置' },
          { value: '混合型-平衡', label: '平衡混合' },
          { value: '股票型', label: '股票型' },
          { value: '股票指数', label: '股票指数' },
          { value: '联接基金', label: '联接基金' }
        ]
      },
      {
        name: '偏债型',
        icon: '📊',
        expanded: false,
        types: [
          { value: '混合型-偏债', label: '偏债混合' },
          { value: '债券型-长债', label: '长期纯债' },
          { value: '债券型-中短债', label: '中短债' },
          { value: '债券型', label: '债券型(全部)' },
          { value: '债券指数', label: '债券指数' }
        ]
      },
      {
        name: '货币/其他',
        icon: '💰',
        expanded: false,
        types: [
          { value: '货币型', label: '货币型' },
          { value: 'FOF', label: 'FOF' },
          { value: 'QDII', label: 'QDII' },
          { value: 'QDII-指数', label: 'QDII指数' },
          { value: 'REITs', label: 'REITs' }
        ]
      }
    ]
    
    // 控制分类展开状态
    // 兼容 fundTypeOptions (用于更新弹窗复选框)
    const fundTypeOptions = computed(() => {
      const allTypes = []
      fundTypeCategories.forEach(cat => {
        cat.types.forEach(t => allTypes.push(t))
      })
      return allTypes
    })
    
    // 排序
    const sortBy = ref('return_1y')
    const sortOrder = ref('desc')
    
    // 快速类型筛选（后端筛选）
    const quickTypeFilter = ref('')
    const availableTypes = ref([])  // 从后端获取可选类型
    const activeQuickDropdown = ref(null)  // 当前打开的下拉菜单
    const industryTags = ref([])
    const screeningGridRef = ref(null)
    
    // 快速筛选的多级分类配置
    const quickTypeCategories = [
      {
        name: '偏股型',
        icon: '📈',
        patterns: ['混合型-偏股', '混合型-灵活', '混合型-平衡', '股票型', '股票指数', '指数型-股票', '联接基金', '增强指数', '被动指数', '指数-股票']
      },
      {
        name: '偏债型',
        icon: '📊',
        patterns: ['混合型-偏债', '债券型', '债券指数', '指数型-固收', '短债', '中短债', '长债', '纯债', '可转债', '指数-债券']
      },
      {
        name: 'FOF',
        icon: '🎯',
        patterns: ['FOF']
      },
      {
        name: 'QDII',
        icon: '🌍',
        patterns: ['QDII', '海外指数', '指数型-海外']
      },
      {
        name: '货币/其他',
        icon: '💰',
        patterns: ['货币', 'REITs', '商品', '指数-其他', '指数型-其他', '其他']
      }
    ]
    
    // 辅助函数：确定类型的归属分类（按顺序优先匹配，避免重复）
    const getTypeCategoryName = (type) => {
      for (const cat of quickTypeCategories) {
        if (cat.patterns.some(p => type.includes(p))) {
          return cat.name
        }
      }
      return null
    }

    // 切换下拉菜单
    const toggleQuickDropdown = (categoryName) => {
      if (activeQuickDropdown.value === categoryName) {
        activeQuickDropdown.value = null
      } else {
        activeQuickDropdown.value = categoryName
      }
    }
    
    // 打开下拉菜单 (不再使用)
    const openQuickDropdown = (categoryName) => {
      // no-op
    }
    
    // 关闭下拉菜单
    const closeQuickDropdown = () => {
      activeQuickDropdown.value = null
    }
    
    // 获取分类下的所有子类型（从模式生成，不依赖当前页结果）
    const getFilteredCategoryTypes = (category) => {
      return (category.patterns || []).map(p => ({
        value: p,
        label: p,
        available: availableTypes.value.some(t => t.includes(p))
      }))
    }
    
    // 检查分类下是否有当前选中的类型
    const isCategoryTypeActive = (category) => {
      if (!quickTypeFilter.value) return false
      // 如果当前选中的类型属于该分类
      return getTypeCategoryName(quickTypeFilter.value) === category.name
    }
    
    // 检查分类下是否有可用类型
    const hasCategoryActiveType = (category) => {
      return getFilteredCategoryTypes(category).length > 0
    }
    
    // 获取未分类的类型
    const uncategorizedTypes = computed(() => {
      return availableTypes.value.filter(type => getTypeCategoryName(type) === null)
    })
    
    // 分页
    const currentPage = ref(1)
    const pageSize = ref(20)
    const totalCount = ref(0)  // 后端返回的总数
    const totalPages = computed(() => Math.ceil(totalCount.value / pageSize.value))
    
    // 结果
    const results = ref([])
    const loading = ref(false)
    const searched = ref(false)

    const gridOptions = reactive({
      border: true,
      stripe: true,
      showOverflow: true,
      height: 560,
      rowConfig: {
        isHover: true
      },
      sortConfig: {
        remote: true,
        trigger: 'cell'
      },
      toolbarConfig: {
        export: true,
        print: true,
        zoom: true,
        custom: true
      },
      exportConfig: {
        filename: '基金筛选结果',
        type: 'csv'
      },
      printConfig: {},
      columns: [
        { type: 'seq', width: 54, title: '序号', fixed: 'left' },
        { field: 'fund_code', title: '基金代码', width: 110, sortable: true, fixed: 'left' },
        { field: 'fund_name', title: '基金名称', minWidth: 190, fixed: 'left', slots: { default: 'fundName' } },
        { field: 'industry_tag_name', title: '板块', width: 110, sortable: true, slots: { default: 'industryTag' } },
        { field: 'fund_type', title: '类型', width: 130, sortable: true },
        { field: 'return_1m', title: '近1月', width: 100, sortable: true, slots: { default: 'percent' } },
        { field: 'return_3m', title: '近3月', width: 100, sortable: true, slots: { default: 'percent' } },
        { field: 'return_1y', title: '近1年', width: 100, sortable: true, slots: { default: 'percent' } },
        { field: 'max_drawdown_1y', title: '最大回撤', width: 110, sortable: true, slots: { default: 'drawdown' } },
        { field: 'volatility_1y', title: '波动率', width: 100, sortable: true, slots: { default: 'percent' } },
        { field: 'sharpe_ratio_1y', title: '夏普', width: 90, sortable: true, slots: { default: 'sharpe' } },
        { field: 'calmar_ratio_1y', title: '卡玛', width: 90, sortable: true, slots: { default: 'calmar' } },
        { field: 'pass_4433', title: '4433', width: 80, slots: { default: 'pass4433' } },
        { field: 'updated_time', title: '更新时间', minWidth: 160, sortable: true },
        { title: '操作', width: 110, fixed: 'right', slots: { default: 'actions' } }
      ]
    })
    
    // 进度百分比
    const progressPercent = computed(() => {
      const status = updateStatus.value
      if (!status.total || status.total === 0) {
        // total 未知时显示平滑增长（最多到 90%）
        const sc = status.success_count || 0
        if (sc > 0) return Math.min(10 + (sc / Math.max(sc + 100, 1)) * 80, 90)
        return 2  // 微小进度表示已开始
      }
      return Math.max(1, Math.round((status.progress / status.total) * 100))
    })

    const isProgressIndeterminate = computed(() => {
      return !updateStatus.value.total || updateStatus.value.total === 0
    })
    
    // 状态轮询定时器
    let statusPollTimer = null
    
    // 获取数据库状态
    const fetchDbStatus = async () => {
      try {
        const res = await screeningAPI.getStatus()
        dbStatus.value = res.data
        if (res.data.update_status) {
          updateStatus.value = res.data.update_status
        }
      } catch (err) {
        updateStatus.value.running = false
        console.error('获取状态失败:', err)
      }
    }
    
    const openUpdateDialog = () => {
      showUpdateDialog.value = true
    }

    const closeUpdateDialog = () => {
      if (!updateStatus.value.running) {
        showUpdateDialog.value = false
      }
    }

    // 开始更新
    const startUpdate = async () => {
      if (!hasSelectedUpdateTask.value) {
        alert('请至少选择一个更新任务')
        return
      }
      try {
        showUpdateDialog.value = false
        updateStatus.value = {
          running: true,
          progress: 0,
          total: 0,
          current_fund: '',
          success_count: 0,
          fail_count: 0,
          message: '正在启动更新任务...'
        }
        await screeningAPI.startUpdate({
          fund_types: selectedFundTypes.value,
          limit: updateLimit.value || null,
          mode: updateTasks.risk ? updateMode.value : 'sync_only',
          precise_limit: updateTasks.risk ? (preciseLimit.value || 500) : null,
          industry_limit: industryLimit.value || null,
          tasks: {
            ...updateTasks
          }
        })
        // 开始轮询状态
        startStatusPoll()
      } catch (err) {
        updateStatus.value.running = false
        if (err.response?.status === 409) {
          alert('更新任务已在进行中')
        } else {
          console.error('启动更新失败:', err)
          alert('启动更新失败')
        }
      }
    }

    // 停止更新
    const stopUpdate = async () => {
      try {
        await screeningAPI.stopUpdate()
      } catch (err) {
        console.error('停止更新失败:', err)
      }
    }
    
    // 开始轮询状态
    // 快速获取更新进度（仅内存端点，极快，不会被DB写入阻塞）
    const fetchProgress = async () => {
      try {
        const res = await screeningAPI.getProgress()
        if (res.data) {
          updateStatus.value = res.data
        }
        const d = res.data
        // 只有确认任务真正结束才停止轮询（total>0且progress>=total 或 running=false且progress=0）
        const looksComplete = d.total > 0 && d.progress >= d.total
        if (!d.running && (looksComplete || d.total === 0)) {
          stopStatusPoll()
          fetchDbStatus()
        }
      } catch (err) {
        console.error('获取进度失败:', err)
      }
    }

    const startStatusPoll = () => {
      if (statusPollTimer) return
      statusPollTimer = setInterval(() => {
        fetchProgress()
      }, 1500)
    }
    
    // 停止轮询
    const stopStatusPoll = () => {
      if (statusPollTimer) {
        clearInterval(statusPollTimer)
        statusPollTimer = null
      }
    }
    
    // 将 advFilters 转为后端需要的扁平 filter 字段
    const buildFilterParams = () => {
      const params = {}
      if (filters.keyword) params.keyword = filters.keyword
      if (filters.fund_types.length) params.fund_types = [...filters.fund_types]
      if (filters.industry_tags.length) params.industry_tags = [...filters.industry_tags]
      if (quickTypeFilter.value) params.quick_fund_type = quickTypeFilter.value
      // 直接映射 advFilters → 后端字段
      const map = {
        return_1y_min: 'return_1y_min', return_1y_max: 'return_1y_max',
        max_drawdown_max: 'max_drawdown_max', sharpe_min: 'sharpe_min',
        volatility_max: 'volatility_max', calmar_min: 'calmar_min',
      }
      for (const [k, v] of Object.entries(map)) {
        if (advFilters[k] !== null && advFilters[k] !== '' && Number.isFinite(Number(advFilters[k]))) {
          params[v] = Number(advFilters[k])
        }
      }
      return params
    }

    // 重置筛选条件
    const resetFilters = () => {
      filters.keyword = ''
      filters.fund_types = []
      filters.industry_tags = []
      Object.keys(advFilters).forEach(k => advFilters[k] = null)
      quickTypeFilter.value = ''
    }

    const fetchIndustryTags = async () => {
      try {
        const res = await screeningAPI.getIndustryTags()
        industryTags.value = res.data.data || []
      } catch (err) {
        console.error('加载板块标签失败:', err)
        industryTags.value = []
      }
    }

    const toggleIndustryTag = (name) => {
      const idx = filters.industry_tags.indexOf(name)
      if (idx > -1) {
        filters.industry_tags.splice(idx, 1)
      } else {
        filters.industry_tags.push(name)
      }
      search(true)
    }

    const clearIndustryTags = () => {
      filters.industry_tags = []
      search(true)
    }

    // 搜索
    const search = async (resetPage = false) => {
      loading.value = true
      searched.value = true

      if (resetPage) currentPage.value = 1

      try {
        const cleanFilters = buildFilterParams()

        const res = await screeningAPI.query({
          filters: cleanFilters,
          sort_by: sortBy.value,
          sort_order: sortOrder.value,
          page: currentPage.value,
          page_size: pageSize.value
        })

        results.value = res.data.data || []
        totalCount.value = res.data.total || 0

        if (!quickTypeFilter.value) {
          const types = new Set()
          results.value.forEach(f => {
            if (f.fund_type) types.add(f.fund_type)
          })
          const existingTypes = new Set(availableTypes.value)
          types.forEach(t => existingTypes.add(t))
          availableTypes.value = Array.from(existingTypes).sort()
        }
      } catch (err) {
        console.error('筛选失败:', err)
        results.value = []
        totalCount.value = 0
      } finally {
        loading.value = false
      }
    }
    
    // 快速类型筛选（触发后端重新查询）
    const setQuickTypeFilter = (type) => {
      quickTypeFilter.value = type
      activeQuickDropdown.value = null // 关闭下拉菜单
      currentPage.value = 1  // 重置到第一页
      search()  // 重新查询后端
    }
    
    // 获取简短类型名称
    const getShortTypeName = (type) => {
      if (!type) return '未知'
      // 简化显示名称
      const shortNames = {
        '混合型-偏股': '偏股混合',
        '混合型-灵活': '灵活配置',
        '混合型-偏债': '偏债混合',
        '混合型-平衡': '平衡混合',
        '指数型-股票': '股票指数',
        '指数型-固收': '债券指数',
        '指数型-海外股票': '海外指数',
        '债券型-长债': '长期债券',
        '债券型-中短债': '中短债',
        '债券型-混合一级': '一级债基',
        '债券型-混合二级': '二级债基',
        '货币型-普通货币': '货币基金',
        'FOF-稳健型': 'FOF稳健',
        'FOF-均衡型': 'FOF均衡',
        'FOF-进取型': 'FOF进取',
      }
      return shortNames[type] || type.replace('型-', '-').replace('型', '')
    }
    
    // 换页
    const changePage = (page) => {
      currentPage.value = page
      search()
    }

    const handleGridSort = ({ field, order }) => {
      if (!field || !order) return
      sortBy.value = field
      sortOrder.value = order === 'asc' ? 'asc' : 'desc'
      search(true)
    }

    const handleGridCellClick = ({ row, column }) => {
      if (column?.title === '操作') return
      viewFundDetail(row)
    }
    
    // 查看基金详情
    const viewFundDetail = (fund) => {
      emit('view-fund', fund.fund_code)
    }
    
    // 加入自选
    const addToWatchlist = async (fund) => {
      try {
        await watchlistAPI.addToWatchlist(fund.fund_code, fund.fund_name, fund.fund_type)
        alert(`已将 ${fund.fund_name} 加入自选`)
      } catch (err) {
        if (err.response?.status === 409) {
          alert('该基金已在自选列表中')
        } else {
          alert('加入自选失败')
        }
      }
    }
    
    // 加入对比
    const addToCompare = (fund) => {
      emit('add-to-compare', {
        code: fund.fund_code,
        name: fund.fund_name
      })
    }
    
    // 格式化函数
    const formatPercent = (value, isNegative = false) => {
      if (value === null || value === undefined) return '--'
      const prefix = isNegative ? '-' : (value > 0 ? '+' : '')
      return `${prefix}${Math.abs(value).toFixed(2)}%`
    }
    
    const formatNumber = (value) => {
      if (value === null || value === undefined) return '--'
      return value.toFixed(2)
    }
    
    const formatDate = (dateStr) => {
      if (!dateStr) return '--'
      const date = new Date(dateStr)
      return date.toLocaleString('zh-CN')
    }
    
    const truncateName = (name) => {
      if (!name) return '--'
      return name.length > 12 ? name.slice(0, 12) + '...' : name
    }
    
    // 样式判断函数
    const getReturnClass = (value) => {
      if (value === null || value === undefined) return ''
      return value > 0 ? 'positive' : value < 0 ? 'negative' : ''
    }
    
    const getSharpeClass = (value) => {
      if (value === null || value === undefined) return ''
      if (value >= 1.5) return 'excellent'
      if (value >= 1) return 'good'
      if (value >= 0.5) return 'normal'
      return 'poor'
    }
    
    const getCalmarClass = (value) => {
      if (value === null || value === undefined) return ''
      if (value >= 2) return 'excellent'
      if (value >= 1) return 'good'
      if (value >= 0.5) return 'normal'
      return 'poor'
    }
    
    const getStyleClass = (style) => {
      if (!style) return ''
      if (style.includes('股票')) return 'style-stock'
      if (style.includes('债券')) return 'style-bond'
      if (style.includes('均衡')) return 'style-balanced'
      return 'style-mixed'
    }
    
    // 生命周期
    onMounted(async () => {
      // 先用快速端点检查是否有运行中的任务
      await fetchProgress()
      // 再加载完整DB状态（显示基金数量等）
      fetchDbStatus()
      fetchIndustryTags()

      // 如果正在更新，开始轮询
      if (updateStatus.value.running) {
        startStatusPoll()
      }

      // 点击外部关闭下拉菜单
      document.addEventListener('click', closeQuickDropdown)
      document.addEventListener('click', handleTypeDropdownClick)
      document.addEventListener('click', handleSearchClickOutside)
    })

    onUnmounted(() => {
      stopStatusPoll()
      document.removeEventListener('click', closeQuickDropdown)
      document.removeEventListener('click', handleTypeDropdownClick)
      document.removeEventListener('click', handleSearchClickOutside)
    })
    
    return {
      // 状态
      dbStatus,
      updateStatus,
      selectedFundTypes,
      updateLimit,
      updateMode,
      preciseLimit,
      industryLimit,
      showUpdateDialog,
      updateTasks,
      hasSelectedUpdateTask,
      filters,
      industryTags,
      screeningGridRef,
      gridOptions,
      fundTypeCategories,
      sortBy,
      sortOrder,
      currentPage,
      totalCount,
      totalPages,
      results,
      loading,
      searched,
      progressPercent,
      isProgressIndeterminate,
      quickTypeFilter,
      availableTypes,
      activeQuickDropdown,
      quickTypeCategories,
      uncategorizedTypes,
      // 高级筛选
      showAdvanced,
      advFilters,
      onTypeSelectChange,
      searchSuggestions,
      showSearchDropdown,
      searchWrapRef,
      onKeywordInput,
      onKeywordFocus,
      selectSearchSuggestion,
      removeFundType,
      showTypeDropdown,
      typeDropdownRef,
      toggleSingleType,
      toggleCategoryTypes,
      toggleIndustryTag,
      clearIndustryTags,
      isCatAllSelected,
      isCatPartialSelected,

      // 方法
      fetchDbStatus,
      openUpdateDialog,
      closeUpdateDialog,
      startUpdate,
      stopUpdate,
      resetFilters,
      search,
      changePage,
      handleGridSort,
      handleGridCellClick,
      viewFundDetail,
      addToWatchlist,
      addToCompare,
      formatPercent,
      formatNumber,
      formatDate,
      truncateName,
      getReturnClass,
      getSharpeClass,
      getCalmarClass,
      getStyleClass,
      setQuickTypeFilter,
      getShortTypeName,
      closeQuickDropdown,
      toggleQuickDropdown,
      getFilteredCategoryTypes,
      isCategoryTypeActive,
      hasCategoryActiveType
    }
  }
}

</script>

<style scoped>
.fund-screening {
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  min-height: calc(100vh - 80px);
  display: flex;
  flex-direction: column;
}

.screening-panel {
  padding: 16px 24px;
}

.screening-panel .filter-card {
  padding: 0;
  background: transparent;
  border: none;
}

/* 顶部状态栏 */
.screening-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 24px;
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
}

.header-title h2 {
  margin: 0 0 4px 0;
  font-size: 1.4rem;
}

.subtitle {
  margin: 0;
  opacity: 0.9;
  font-size: 0.9rem;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.stat-chip {
  background: #f0f5ff;
  color: #1677ff;
  padding: 3px 10px;
  border-radius: 20px;
  font-weight: 500;
  white-space: nowrap;
}

.stat-chip.complete {
  background: #f0fdf4;
  color: #16a34a;
}

.update-time-chip {
  color: #999;
  font-size: 0.8rem;
  margin-left: 4px;
}

/* 内联更新进度条 */
.inline-update-bar {
  background: #fff7e6;
  border-bottom: 2px solid #fa8c16;
  padding: 10px 24px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.inline-progress-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.inline-current {
  font-size: 13px;
  color: #333;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inline-count {
  font-size: 12px;
  color: #999;
  white-space: nowrap;
}

.btn-stop-inline {
  padding: 5px 14px;
  background: #fff;
  border: 1px solid #fa8c16;
  border-radius: 6px;
  color: #fa8c16;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  transition: all 0.2s;
}

.btn-stop-inline:hover {
  background: #fa8c16;
  color: #fff;
}

/* 关键词搜索 */
.search-row {
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
  margin-bottom: 4px;
}

.search-keyword-input {
  flex: 1;
  padding: 8px 14px;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}

.search-keyword-input:focus {
  border-color: #1677ff;
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.1);
}

.btn-search-inline {
  padding: 8px 18px;
  background: #1677ff;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  margin-left: 8px;
}

.btn-search-inline:hover {
  background: #0958d9;
}

.search-input-wrap {
  display: flex;
  align-items: center;
  flex: 1;
}

/* 搜索下拉 */
.search-wrap {
  position: relative;
}

.search-dropdown {
  position: absolute;
  top: 100%;
  left: -28px;
  z-index: 300;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  max-height: 340px;
  overflow-y: auto;
  margin-top: 4px;
  min-width: 400px;
}

.search-dropdown-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 16px;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.1s;
  border-bottom: 1px solid #f8f8f8;
}

.search-dropdown-item:last-child {
  border-bottom: none;
}

.search-dropdown-item:hover {
  background: #f0f5ff;
}

.sug-code {
  font-weight: 600;
  color: #1677ff;
  min-width: 60px;
}

.sug-name {
  flex: 1;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sug-type {
  font-size: 11px;
  color: #999;
  background: #f5f5f5;
  padding: 1px 8px;
  border-radius: 10px;
  flex-shrink: 0;
}

.btn-update,
.btn-secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 18px;
  font-size: 13px;
  font-weight: 600;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  line-height: 1;
}

.btn-update {
  background: #1677ff;
  color: #fff;
  border: 1px solid #1677ff;
}

.btn-update:hover:not(:disabled) { background: #0958d9; }

.btn-secondary {
  background: #fff;
  color: #4b5563;
  border: 1px solid #d1d5db;
}

.btn-secondary:hover:not(:disabled) { background: #f9fafb; }

.btn-update:disabled,
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(15, 23, 42, 0.42);
}

.update-dialog {
  width: min(620px, 100%);
  max-height: calc(100vh - 40px);
  overflow-y: auto;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.24);
}

.dialog-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 22px 14px;
  border-bottom: 1px solid #eef2f7;
}

.dialog-head h3 {
  margin: 0 0 6px;
  font-size: 18px;
  color: #111827;
}

.dialog-head p {
  margin: 0;
  font-size: 13px;
  color: #6b7280;
}

.dialog-close {
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: #f3f4f6;
  color: #4b5563;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}

.task-list {
  display: grid;
  gap: 8px;
  padding: 16px 22px;
}

.task-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
}

.task-item input {
  margin-top: 3px;
}

.task-item strong {
  display: block;
  font-size: 14px;
  color: #111827;
}

.task-item em {
  display: block;
  margin-top: 4px;
  font-style: normal;
  font-size: 12px;
  color: #6b7280;
}

.dialog-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  padding: 0 22px 16px;
}

.dialog-options label {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: #6b7280;
}

.dialog-options input {
  width: 100%;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 13px;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 22px 20px;
  border-top: 1px solid #eef2f7;
}

/* 结果区域 */
.results-section {
  padding: 20px 24px;
}

.results-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
  position: relative;
  z-index: 50;
  overflow: visible;
}

.results-title-row {
  flex-shrink: 0;
}

.results-header h3 {
  margin: 0;
  font-size: 1rem;
}

.result-count {
  font-weight: normal;
  color: #666;
  font-size: 0.9rem;
}

/* 快速类型筛选 */
.quick-type-filter {
  flex: 1;
  min-width: 300px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 10px;
  overflow: visible;
  position: relative;
  z-index: 50;
}

.filter-label-inline {
  font-size: 13px;
  color: #6b7280;
  white-space: nowrap;
}

.quick-type-menu {
  display: flex;
  gap: 6px;
  flex-wrap: nowrap;
  align-items: center;
}

.quick-type-tags {
  display: flex;
  gap: 6px;
  flex-wrap: nowrap;
}

.quick-tag {
  padding: 4px 12px;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 15px;
  font-size: 12px;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.quick-tag:hover {
  border-color: #a5b4fc;
  color: #4f46e5;
}

.quick-tag.active {
  background: #667eea;
  border-color: #667eea;
  color: rgb(50, 53, 218);
}

.quick-tag.has-active {
  border-color: #a5b4fc;
  background: #eef2ff;
}

/* 下拉菜单容器 */
.quick-type-dropdown {
  position: relative;
  z-index: 100;
}

.dropdown-trigger {
  display: flex;
  align-items: center;
  gap: 4px;
}

.dropdown-arrow {
  font-size: 8px;
  margin-left: 2px;
  transition: transform 0.2s;
}

.quick-type-dropdown:hover .dropdown-arrow {
  transform: rotate(180deg);
}

.dropdown-menu {
  position: absolute;
  top: 100%;
  left: 0;
  min-width: 140px;
  max-height: 280px;
  overflow-y: auto;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  z-index: 1000;
  margin-top: 4px;
  padding: 4px 0;
}

.dropdown-item {
  padding: 8px 14px;
  font-size: 12px;
  color: #374151;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.dropdown-item:hover {
  background: #f3f4f6;
  color: #4f46e5;
}

.dropdown-item.active {
  background: #eef2ff;
  color: #667eea;
  font-weight: 500;
}

.dropdown-item.unavailable {
  color: #ccc;
  cursor: default;
}

.unavailable-hint {
  font-size: 10px;
  color: #ddd;
  margin-left: auto;
}

.dropdown-empty {
  padding: 12px 14px;
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
}

.sort-options {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.sort-options label {
  color: #666;
  font-size: 0.9rem;
}

.sort-options select {
  padding: 6px 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 13px;
  background: white;
}

/* 表格 */
.results-table-wrapper {
  overflow-x: auto;
}

.vxe-results-wrapper {
  overflow: visible;
}

.fund-name-cell {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
  white-space: nowrap;
  color: #111827;
  font-weight: 600;
}

.industry-cell {
  display: inline-flex;
  align-items: center;
  max-width: 96px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f0f5ff;
  color: #1677ff;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.results-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.results-table th,
.results-table td {
  padding: 12px 10px;
  text-align: left;
  border-bottom: 1px solid #eee;
  white-space: nowrap;
}

.results-table th {
  background: #f8f9fa;
  font-weight: 600;
  color: #666;
  position: sticky;
  top: 0;
}

.results-table .sticky-col {
  position: sticky;
  left: 0;
  background: white;
  z-index: 1;
}

.results-table .sticky-col-2 {
  position: sticky;
  left: 70px;
  background: white;
  z-index: 1;
}

.results-table th.sticky-col,
.results-table th.sticky-col-2 {
  background: #f8f9fa;
  z-index: 2;
}

.clickable-row {
  cursor: pointer;
  transition: background 0.2s;
}

.clickable-row:hover {
  background: #f8f9fa;
}

.fund-code {
  font-family: monospace;
  color: #1677ff;
  font-weight: 600;
}

.fund-name {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.positive {
  color: #ef4444;
}

.negative {
  color: #10b981;
}

.excellent {
  color: #10b981;
  font-weight: 600;
}

.good {
  color: #3b82f6;
}

.normal {
  color: #666;
}

.poor {
  color: #9ca3af;
}

.style-tag {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.pass-badge {
  color: #059669;
  font-weight: bold;
}

.fail-badge {
  color: #9ca3af;
}

.style-stock {
  background: #fef3c7;
  color: #92400e;
}

.style-bond {
  background: #dbeafe;
  color: #1e40af;
}

.style-balanced {
  background: #d1fae5;
  color: #065f46;
}

.style-mixed {
  background: #f3e8ff;
  color: #6b21a8;
}

.actions {
  display: flex;
  gap: 8px;
}

.btn-action {
  padding: 4px 8px;
  background: #f5f7fa;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-action:hover {
  background: #e9ecef;
  transform: scale(1.1);
}

/* 分页 */
.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #eee;
}

.page-btn {
  padding: 8px 16px;
  background: #f5f7fa;
  border: 1px solid #ddd;
  border-radius: 6px;
  cursor: pointer;
}

.page-btn:hover:not(:disabled) {
  background: #e9ecef;
}

.page-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.page-info {
  color: #666;
  font-size: 14px;
}

/* 状态显示 */
.loading-state,
.empty-state,
.initial-state {
  text-align: center;
  padding: 120px 20px;
  color: #666;
  min-height: 60vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.loading-spinner {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid #fff;
  border-radius: 50%;
  border-top-color: transparent;
  animation: spin 0.8s linear infinite;
  vertical-align: middle;
  flex-shrink: 0;
}

.loading-spinner.small {
  width: 14px;
  height: 14px;
  border-width: 2px;
  border-color: #fa8c16;
  border-top-color: transparent;
}

.loading-spinner.large {
  width: 40px;
  height: 40px;
  border-width: 3px;
  border-color: #1677ff;
  border-top-color: transparent;
  margin-bottom: 16px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.empty-icon,
.initial-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.hint {
  font-size: 0.9rem;
  color: #9ca3af;
  margin-top: 8px;
}

.sub-hint {
  font-size: 0.8rem;
  color: #6b7280;
}

/* ── New ifund-style filter layout ── */
.filter-bar {
  background: #fff;
  border-radius: 8px;
}

.filter-bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.search-wrap,
.type-select-wrap {
  display: flex;
  align-items: center;
  background: #f8f9fa;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 0 10px;
  transition: border-color 0.2s;
}

.search-wrap:focus-within,
.type-select-wrap:focus-within {
  border-color: #1677ff;
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.08);
}

.input-icon {
  font-size: 14px;
  margin-right: 6px;
  flex-shrink: 0;
}

.filter-keyword {
  border: none;
  background: transparent;
  padding: 8px 0;
  font-size: 14px;
  outline: none;
  min-width: 240px;
  flex: 1;
}

.filter-type-select {
  border: none;
  background: transparent;
  padding: 8px 0;
  font-size: 13px;
  outline: none;
  min-width: 120px;
  cursor: pointer;
}

.type-count {
  font-size: 11px;
  color: #1677ff;
  font-weight: 600;
  margin-left: 4px;
}

.btn-query {
  padding: 8px 20px;
  background: #1677ff;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}

.btn-query:hover { background: #0958d9; }

.btn-reset {
  padding: 8px 16px;
  background: #fff;
  color: #666;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.btn-reset:hover {
  border-color: #1677ff;
  color: #1677ff;
  background: #f0f5ff;
}

.selected-types-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.type-tag-selected {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  background: #f0f5ff;
  border: 1px solid #b3d4ff;
  border-radius: 16px;
  font-size: 12px;
  color: #1677ff;
  cursor: pointer;
}

.type-tag-selected:hover {
  background: #e6f0ff;
  border-color: #1677ff;
}

.industry-filter-block {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.industry-filter-label {
  color: #666;
  font-size: 12px;
  font-weight: 600;
}

.industry-chip {
  border: 1px solid #d9e3f0;
  border-radius: 999px;
  background: #fff;
  color: #4b5563;
  cursor: pointer;
  font-size: 12px;
  padding: 4px 10px;
  transition: all 0.2s;
}

.industry-chip span {
  margin-left: 4px;
  color: #9ca3af;
}

.industry-chip:hover,
.industry-chip.active {
  border-color: #1677ff;
  background: #f0f5ff;
  color: #1677ff;
}

/* Advanced toggle */
.advanced-toggle {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  margin-top: 12px;
  font-size: 13px;
  font-weight: 600;
  color: #555;
  cursor: pointer;
  user-select: none;
  background: #f8f9fa;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  transition: all 0.2s;
}

.advanced-toggle:hover {
  color: #1677ff;
  border-color: #1677ff;
  background: #f0f5ff;
}

.toggle-arrow { font-size: 10px; }

/* Advanced section */
.advanced-section {
  padding: 14px;
  margin-top: 8px;
  background: #fafbfc;
  border: 1px solid #eee;
  border-radius: 8px;
}

.adv-grid-simple {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

.adv-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: #fff;
  border: 1px solid #eee;
  border-radius: 8px;
}

.adv-item label {
  font-size: 13px;
  color: #555;
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
}

.adv-range {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
}

.adv-inp {
  width: 80px;
  padding: 6px 8px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  text-align: center;
  transition: border-color 0.2s;
}

.adv-inp:focus {
  border-color: #1677ff;
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.08);
}

.adv-inp::placeholder {
  color: #ccc;
  font-size: 12px;
}

.adv-sep {
  color: #bbb;
  font-size: 14px;
}

.adv-unit {
  font-size: 12px;
  color: #999;
  flex-shrink: 0;
}

/* ── 自定义类型下拉 ── */
.type-select-wrap {
  position: relative;
}

.type-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 4px 8px 0;
  cursor: pointer;
  min-width: 100px;
  font-size: 13px;
  user-select: none;
}

.type-trigger-text {
  color: #333;
}

.type-trigger-arrow {
  font-size: 10px;
  color: #999;
  transition: transform 0.2s;
}

.type-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 200;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  max-height: 420px;
  overflow-y: auto;
  min-width: 220px;
  padding: 4px 0;
}

.type-cat-group {
  border-bottom: 1px solid #f5f5f5;
}

.type-cat-group:last-child {
  border-bottom: none;
}

.type-cat-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  color: #555;
  transition: background 0.15s;
}

.type-cat-header:hover {
  background: #f0f5ff;
}

.type-cat-header.all {
  color: #1677ff;
  background: #f0f5ff;
}

.type-cat-header.partial {
  color: #1677ff;
}

.cat-check {
  width: 18px;
  height: 18px;
  border: 2px solid #d9d9d9;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  flex-shrink: 0;
}

.type-cat-header.all .cat-check,
.type-cat-header.partial .cat-check {
  background: #1677ff;
  border-color: #1677ff;
  color: #fff;
}

.type-cat-items {
  padding: 2px 14px 8px 36px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.type-item-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border: 1px solid #e8e8e8;
  border-radius: 14px;
  font-size: 12px;
  cursor: pointer;
  color: #666;
  transition: all 0.15s;
  user-select: none;
}

.type-item-label:hover {
  border-color: #1677ff;
  color: #1677ff;
}

.type-item-label.active {
  background: #f0f5ff;
  border-color: #1677ff;
  color: #1677ff;
  font-weight: 500;
}

.type-checkbox {
  display: none;
}
</style>
