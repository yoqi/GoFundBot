# GoFundBot 数据源迁移文档

## 架构目标

Backend 不再直接访问任何第三方财经数据源。所有外部财经数据访问必须先经过 DataServiceClient，再由 DataService 内部 ProviderChain 访问 stock-sdk / EastMoney / 后续 provider。

```
Frontend (Vue)
    ↓
Flask Backend
    ↓
Backend/services/data_service_client.py
    ↓
DataService (Node.js + TypeScript + Express)
    ↓
ProviderChain
    ↓
stock-sdk / EastMoney / AkShare(TODO) / Tushare(TODO) / Local Cache
```

---

## A. 已完成 ✅

### 基础设施
- ✅ DataService Provider Gateway（Express + TypeScript）
- ✅ ProviderChain 架构（顺序 fallback、统一 meta、统一错误）
- ✅ stock-sdk provider（基金估值、净值历史、排名历史、分红、行情、K线、指数）
- ✅ eastmoney provider（基金估值、净值历史、搜索、基本信息、持仓、基金经理、资产配置、业绩、认购赎回、板块、成分股、指数、快讯）
- ✅ Flask DataServiceClient（20 个方法，统一错误处理，per-request timeout）
- ✅ Flask 代理路由（`/api/data-service/...`）

### API 端点（全部 200）
- ✅ `GET /api/funds/:code/detail`（10 个 section 聚合）
- ✅ `GET /api/funds/:code/holdings`
- ✅ `GET /api/funds/:code/managers`
- ✅ `GET /api/funds/:code/asset-allocation`
- ✅ `GET /api/funds/:code/estimate`
- ✅ `GET /api/funds/:code/nav-history`
- ✅ `GET /api/funds/:code/rank-history`
- ✅ `GET /api/funds/:code/dividends`（timeout 隔离）
- ✅ `GET /api/funds/estimates?codes=...`
- ✅ `GET /api/funds/search?q=...`
- ✅ `GET /api/stocks/:code/reference`
- ✅ `GET /api/stocks/references?codes=...`
- ✅ `GET /api/market/indices`
- ✅ `GET /api/market/sectors`
- ✅ `GET /api/market/sectors/:code/constituents`
- ✅ `GET /api/news/flash`

### Backend 迁移（DataService-first + fallback）
- ✅ `/api/watchlist/refresh-estimates` → DataServiceClient.get_fund_estimates()
- ✅ `/api/fund/search` → DataServiceClient.search_funds()
- ✅ `/api/market/sectors` → DataServiceClient.get_market_sectors()
- ✅ `/api/market/sector/<code>/constituents` → DataServiceClient.get_market_sector_constituents()
- ✅ `/api/market/index` → DataServiceClient.get_market_indices()
- ✅ `/api/market/news` → DataServiceClient.get_flash_news()
- ✅ 股票引用补全 → `get_stock_info_ds_first()` adapter

### 工具
- ✅ `compare_data_service_contract.py`（字段完整度评分、blocking fields 检测）
- ✅ `data_source_inventory.py`（逻辑单元聚合、cls 误判修复）
- ✅ `data_service_legacy_mapper.py`（DataService → legacy 格式映射，仅用于测试对比）
- ✅ `akshareMarketProvider.ts`（占位 provider，标记迁移未完成）

---

## B. 未完成 ❌

### /api/fund/<code> 未整体替换
- ❌ contract compare `overall_completeness_score`: **30%**
- ❌ **19 blocking fields** 仍缺失或不可映射
- ❌ 不允许整体替换 `/api/fund/<code>`

### detail 字段完整度不足
| 旧字段 | 状态 |
| --- | --- |
| `holder_structure` | ❌ 未覆盖（blocking） |
| `same_type_funds` | ❌ 未覆盖（blocking） |
| `risk_metrics` | ❌ 未覆盖（blocking） |
| `scale_fluctuation` | ❌ 未覆盖（blocking） |
| `position_trend` | ❌ 未覆盖（blocking） |
| `total_return_trend` | ❌ 未覆盖（blocking） |
| `performance_evaluation` | ❌ 未覆盖 |
| `bond_codes`（portfolio 内） | ❌ 未覆盖 |
| `original_rate` / `current_rate`（basic_info 内） | ❌ 未覆盖 |

### 数据解析不足
- ❌ `performance` section：所有字段返回 null（pingzhongdata 解析待修复）
- ❌ `subscriptionRedemption` section：所有字段返回 null
- ❌ `assetAllocation` section：stock/bond/cash 字段返回 null
- ❌ `managers` section：缺少 photo_url/star_rating/work_experience 等扩展字段

### Backend fallback 仍可能直接访问第三方
- ❌ `/api/market/sectors`：DataService eastmoney 不可用时 fallback 到 Backend MarketDataService → **akshare**（第三方 Python 库）
- ❌ `stock_service.py`：biyingapi / mairuiapi 股票参考列表缓存
- ❌ `fund_list_cache.py`：fundcode_search.js 本地缓存
- ❌ `fund_master_service.py`：黄金/成交量/分时仍直接访问第三方 API
- ❌ 以上均已有 deprecated 标记，但 **尚未删除**

### 旧模块已 deprecated 但保留
- `Backend/fund_api.py` — 仍用于 `/api/fund/<code>` 完整详情
- `Backend/fund_list_cache.py` — 仍用于搜索 fallback
- `Backend/stock_service.py` — 仍用于股票引用 fallback
- `Backend/fund_master_service.py` — 仍用于 news/gold/volume/intraday fallback
- `Backend/services/market_data.py` — 仍用于 sector fallback（含 akshare）

### DataService akshare provider
- ⚠️ `akshareMarketProvider.ts` 为占位实现（akshare 是 Python 库，Node.js 无法直接调用）
- ⚠️ 所有方法返回 501 PROVIDER_UNAVAILABLE

---

## C. 下一步删除条件

旧模块（fund_api.py、fund_list_cache.py、stock_service.py、fund_master_service.py、market_data.py）可以删除的**充要条件**：

1. **contract compare `overall_completeness_score` ≥ 85%**，且所有 blocking_fields 已解决。
2. **`/api/fund/<code>` 已整体切换到 DataService**，前端确认所有字段正常。
3. **Backend 不再有任何直接访问第三方财经数据源的路径**：
   - akshare fallback 已迁移到 DataService 或移除
   - biyingapi / mairuiapi 已迁移到 DataService provider
   - fundcode_search.js 已由 DataService search 完全替代
   - 黄金/成交量/分时已迁移到 DataService 或移除
4. **所有已迁移接口经过充分的灰度验证**，fallback 路径未触发。

**在以上条件全部满足之前，不允许删除旧模块。**
