# GoFundBot DataService

DataService 是 GoFundBot 的**统一数据源网关**。Flask 后端不再直接访问任何第三方财经数据源；所有外部财经数据访问必须先经过 DataServiceClient，再由 DataService 内部的 **ProviderChain** 访问 stock-sdk / EastMoney / 后续 provider。

## 架构

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
stock-sdk / EastMoney / AKShare / Tushare / Local Cache / Future Providers
```

## ProviderChain 工作方式

`ProviderChain<P>` 按优先级顺序调用已注册的 provider。主源（如 stock-sdk）失败时，自动 fallback 到下一个（如 eastmoney）。仅当**所有** provider 都失败时才抛出错误。

每次响应包含统一 meta：

```json
{
  "source": "DataService",
  "provider": "stock-sdk",
  "fallback": false,
  "cached": false,
  "stale": false,
  "updatedAt": "2026-06-19T10:00:00.000Z"
}
```

多 provider 混合时（如 batch estimates、news flash）：

```json
{
  "source": "DataService",
  "provider": "mixed",
  "fallback": true,
  "cached": false,
  "stale": false,
  "updatedAt": "..."
}
```

统一错误：

```json
{
  "success": false,
  "error": {
    "code": "PROVIDER_UNAVAILABLE",
    "message": "...",
    "detail": { "providerErrors": [...] }
  },
  "meta": {}
}
```

## 当前 Provider 列表

| Provider | 名称 | 已实现能力 |
| --- | --- | --- |
| `stock-sdk` | `stock-sdk` | 基金估值、净值历史、排名历史、分红、市场行情、K线、指数 |
| `eastmoney` | `eastmoney` | 基金估值、净值历史、基金搜索、基本信息、持仓、基金经理、资产配置、业绩、认购赎回、板块、成分股、指数、快讯 |
| `baidu` | `baidu` | 快讯（百度股市通） |
| `cls` | `cls` | 快讯（财联社） |

## 安装与启动

Node.js 版本要求：`>= 18`。

```bash
cd DataService
npm install
```

开发模式：

```bash
npm run dev
```

构建：

```bash
npm run typecheck
npm run build
```

生产启动：

```bash
npm start
```

默认端口：`3100`。

## 已支持 API 列表

### 健康检查
- `GET /api/health`

### 基金
- `GET /api/funds/search?q=<keyword>`
- `GET /api/funds/estimates?codes=161725,110022`（单次最多 50 个）
- `GET /api/funds/:code/estimate`
- `GET /api/funds/:code/basic`
- `GET /api/funds/:code/detail`（聚合：basic + estimate + nav + rank + dividends + holdings + managers + assetAllocation + performance + subscriptionRedemption）
- `GET /api/funds/:code/nav-history?startDate=&endDate=`
- `GET /api/funds/:code/rank-history`
- `GET /api/funds/:code/dividends`
- `GET /api/funds/:code/holdings`
- `GET /api/funds/:code/managers`
- `GET /api/funds/:code/asset-allocation`

### 市场
- `GET /api/market/quotes?symbols=sh600519,sz000001`
- `GET /api/market/kline/:symbol?period=daily&adjust=none&startDate=&endDate=`
- `GET /api/market/indices`
- `GET /api/market/sectors`
- `GET /api/market/sectors/:code/constituents`

### 股票
- `GET /api/stocks/:code/reference`
- `GET /api/stocks/references?codes=600519,000001`（单次最多 100 个）

### 新闻
- `GET /api/news/flash?count=30`

### 缓存策略

| 数据类型 | TTL |
| --- | --- |
| 基金估值 | 30 秒 |
| 市场行情 | 15 秒 |
| 市场 K 线 | 1 小时 |
| 基金信息（basic/holdings/managers 等） | 24 小时 |
| 基金历史净值 | 24 小时 |
| 基金排名历史 | 24 小时 |
| 基金分红 | 7 天 |
| 股票参考 | 7 天 |

## curl 测试样例

```bash
curl "http://localhost:3100/api/health"
curl "http://localhost:3100/api/funds/search?q=招商"
curl "http://localhost:3100/api/funds/161725/basic"
curl "http://localhost:3100/api/funds/161725/detail"
curl "http://localhost:3100/api/funds/161725/estimate"
curl "http://localhost:3100/api/funds/estimates?codes=161725,110022"
curl "http://localhost:3100/api/funds/161725/holdings"
curl "http://localhost:3100/api/funds/161725/managers"
curl "http://localhost:3100/api/stocks/600519/reference"
curl "http://localhost:3100/api/stocks/references?codes=600519,000001"
curl "http://localhost:3100/api/market/indices"
curl "http://localhost:3100/api/market/sectors"
curl "http://localhost:3100/api/news/flash"
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `3100` | 服务端口 |
| `NODE_ENV` | `development` | 环境 |
| `CORS_ORIGIN` | `http://localhost:5173,http://localhost:8080` | CORS 域名 |
| `STOCK_SDK_TIMEOUT_MS` | `10000` | stock-sdk 超时 |
| `DATA_SERVICE_FORCE_STOCK_SDK_FAILURE` | （未设置） | 设为 `1` 模拟 stock-sdk 失败（验证 eastmoney fallback） |

## Flask 后端如何调用

Flask 后端通过 `Backend/services/data_service_client.py` 统一调用：

```python
from services.data_service_client import get_data_service_client

client = get_data_service_client()
estimate = client.get_fund_estimate("161725")
```

所有异常统一转为 `DataServiceError`。DataService 返回 `success=false` 时也会转为 `DataServiceError`，detail 中保留原始 payload。
