import type {
  FundAssetAllocationDto,
  FundDividendListDto,
  FundEstimateDto,
  FundBasicDto,
  FundHolderStructureDto,
  FundHoldingsDto,
  FundManagersDto,
  FundNavHistoryDto,
  FundPerformanceDto,
  FundPerformanceEvaluationDto,
  FundPositionTrendItemDto,
  FundRankHistoryDto,
  FundScaleFluctuationDto,
  FundScreeningSnapshotDto,
  FundSearchResultDto,
  FundSubscriptionRedemptionDto,
  FundTotalReturnTrendDto,
} from '../types/fund.js';
import type { StockReferenceDto } from '../models/stock.js';

export interface FundNavHistoryOptions {
  startDate?: string;
  endDate?: string;
}

export interface FundScreeningSnapshotOptions {
  types?: string[];
  sort?: string;
  pageSize?: number;
}

export interface KlineOptions {
  period: 'daily' | 'weekly' | 'monthly';
  adjust: '' | 'qfq' | 'hfq';
  startDate?: string;
  endDate?: string;
}

export interface MarketQuoteDto {
  symbol: string;
  code: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  amount: number;
  market: string;
  assetType: string;
  source?: string;
}

export interface KlineDto {
  code: string;
  date?: string;
  time?: string;
  timestamp: number | null;
  open: number | null;
  close: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  amount: number | null;
  change: number | null;
  changePercent: number | null;
  turnoverRate: number | null;
}

export interface SectorDto {
  code: string;
  name: string;
  price: number | null;
  changePercent: number | null;
  mainNetInflow: number | null;
  turnoverRate: number | null;
}

export interface SectorListDto {
  items: SectorDto[];
}

export interface ConstituentDto {
  code: string;
  name: string;
  price: number | null;
  changePercent: number | null;
  marketValue: number | null;
  pe: number | null;
  turnoverRate: number | null;
}

export interface ConstituentListDto {
  items: ConstituentDto[];
  sectorCode: string;
  sectorName: string | null;
}

export interface IndexDto {
  code: string;
  name: string;
  price: number | null;
  changePercent: number | null;
  changeAmount: number | null;
  market: string;
}

export interface IndexListDto {
  items: IndexDto[];
}

export interface NewsItemDto {
  title: string;
  summary: string | null;
  url: string | null;
  source: string;
  publishedAt: string | null;
}

export interface NewsListDto {
  items: NewsItemDto[];
  total?: number;
  hasMore?: boolean;
}

export interface FundProvider {
  name: string;
  estimate(code: string): Promise<FundEstimateDto>;
  navHistory(code: string, options?: FundNavHistoryOptions): Promise<FundNavHistoryDto>;
  rankHistory(code: string): Promise<FundRankHistoryDto>;
  dividends(code: string): Promise<FundDividendListDto>;
  search?(keyword: string): Promise<FundSearchResultDto>;
  screeningSnapshot?(options?: FundScreeningSnapshotOptions): Promise<FundScreeningSnapshotDto>;
  basic?(code: string): Promise<FundBasicDto>;
  holdings?(code: string): Promise<FundHoldingsDto>;
  managers?(code: string): Promise<FundManagersDto>;
  assetAllocation?(code: string): Promise<FundAssetAllocationDto>;
  performance?(code: string): Promise<FundPerformanceDto>;
  performanceEvaluation?(code: string): Promise<FundPerformanceEvaluationDto>;
  subscriptionRedemption?(code: string): Promise<FundSubscriptionRedemptionDto>;
  holderStructure?(code: string): Promise<FundHolderStructureDto>;
  sameTypeFunds?(code: string): Promise<unknown[][]>;
  scaleFluctuation?(code: string): Promise<FundScaleFluctuationDto>;
  positionTrend?(code: string): Promise<FundPositionTrendItemDto[]>;
  totalReturnTrend?(code: string): Promise<FundTotalReturnTrendDto>;
}

export interface MarketProvider {
  name: string;
  quotes(symbols: string[]): Promise<MarketQuoteDto[]>;
  kline(symbol: string, options: KlineOptions): Promise<KlineDto[]>;
  indices?(): Promise<IndexListDto>;
  sectors?(): Promise<SectorListDto>;
  sectorConstituents?(code: string): Promise<ConstituentListDto>;
}

export interface StockProvider {
  name: string;
  reference(code: string): Promise<StockReferenceDto>;
}

export interface NewsProvider {
  name: string;
  flashNews?(count?: number): Promise<NewsListDto>;
}

export interface ProviderErrorSummary {
  provider: string;
  message: string;
  code?: unknown;
}

export interface ProviderChainResult<T> {
  data: T;
  provider: string;
  fallback: boolean;
  stale: boolean;
  providerErrors: ProviderErrorSummary[];
}
