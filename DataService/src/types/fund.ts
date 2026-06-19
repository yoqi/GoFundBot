export interface FundEstimateDto {
  code: string;
  name: string | null;
  navDate: string | null;
  nav: number | null;
  estimatedNav: number | null;
  estimatedChangePercent: number | null;
  estimateTime: string | null;
}

export interface FundEstimateBatchSuccessDto {
  code: string;
  success: true;
  data: FundEstimateDto;
  provider: string;
  fallback: boolean;
  cached: boolean;
  stale: boolean;
  updatedAt: string;
}

export interface FundEstimateBatchFailureDto {
  code: string;
  success: false;
  error: {
    code: string;
    message: string;
    detail?: unknown;
  };
}

export interface FundEstimateBatchDto {
  items: FundEstimateBatchSuccessDto[];
  failed: FundEstimateBatchFailureDto[];
  summary: {
    total: number;
    success: number;
    failed: number;
  };
}

export interface FundNavPointDto {
  date: string;
  timestamp: number | null;
  nav: number;
  accNav: number | null;
  dailyReturn: number | null;
  unitMoney: string;
}

export interface FundNavHistoryDto {
  code: string;
  name: string | null;
  items: FundNavPointDto[];
}

export interface FundRankPointDto {
  date: string;
  timestamp: number | null;
  rank: number | null;
  total: number | null;
  percentile: number | null;
}

export interface FundRankHistoryDto {
  code: string;
  name: string | null;
  items: FundRankPointDto[];
}

export interface FundDividendDto {
  code: string;
  name: string;
  equityRecordDate: string | null;
  exDividendDate: string | null;
  dividendPerShare: number | null;
  payDate: string | null;
  dividendType: string | null;
}

export interface FundDividendListDto {
  items: FundDividendDto[];
  totalPages: number;
  pageSize: number;
  currentPage: number;
}

export interface FundSearchItemDto {
  code: string;
  name: string;
  type: string;
  pinyin: string;
  source: string;
}

export interface FundSearchResultDto {
  items: FundSearchItemDto[];
}

export interface FundBasicDto {
  code: string;
  name: string | null;
  type: string | null;
  company: string | null;
  manager: string | null;
  establishDate: string | null;
  fundSize: string | null;
  riskLevel: string | null;
  originalRate: string | null;
  currentRate: string | null;
  minSubscriptionAmount: string | null;
  isHB: boolean | null;
}

// ---------- Fund Detail (aggregated) ----------

export interface FundHoldingItemDto {
  stockCode: string;
  stockName: string;
  market: string | null;
  symbol: string | null;
  ratio: number | null;
  shares: number | null;
  marketValue: number | null;
}

export interface FundHoldingsDto {
  items: FundHoldingItemDto[];
  bondCodes: string[];
  bondCodesNew: string[];
  reportDate: string | null;
}

export interface FundManagerDto {
  id: string | null;
  name: string | null;
  photoUrl: string | null;
  starRating: number | null;
  workExperience: string | null;
  managedFundSize: string | null;
  startDate: string | null;
  endDate: string | null;
  tenure: string | null;
  description: string | null;
  abilityAssessment: Record<string, unknown> | null;
  performance: Record<string, unknown> | null;
}

export interface FundManagersDto {
  items: FundManagerDto[];
}

export interface FundAssetAllocationDto {
  categories: string[];
  series: Array<Record<string, unknown>>;
  date: string | null;
}

export interface FundPerformanceDto {
  return1m: number | null;
  return3m: number | null;
  return6m: number | null;
  return1y: number | null;
  date: string | null;
}

export interface FundPerformanceEvaluationDto {
  avr: string | null;
  categories: string[];
  data: number[];
  dsc: string[];
}

export interface FundSubscriptionRedemptionDto {
  categories: string[];
  series: Array<Record<string, unknown>>;
}

export interface FundHolderStructureDto {
  categories: string[];
  series: Array<Record<string, unknown>>;
}

export interface FundSameTypeItemDto {
  code: string;
  name: string;
  returnRate: number | null;
}

export interface FundScaleFluctuationDto {
  categories: string[];
  series: Array<Record<string, unknown>>;
}

export interface FundPositionTrendItemDto {
  date: string;
  positionPercentage: number | null;
}

export interface FundTotalReturnTrendDto {
  series: Array<Record<string, unknown>>;
}

export interface FundDetailSection<T> {
  data: T | null;
  provider: string | null;
  fallback: boolean;
  cached: boolean;
  updatedAt: string | null;
  error?: { code: string; message: string };
}

export interface FundDetailDto {
  code: string;
  sections: {
    basic: FundDetailSection<FundBasicDto>;
    estimate: FundDetailSection<FundEstimateDto>;
    navHistory: FundDetailSection<FundNavHistoryDto>;
    rankHistory: FundDetailSection<FundRankHistoryDto>;
    dividends: FundDetailSection<FundDividendListDto>;
    holdings: FundDetailSection<FundHoldingsDto>;
    assetAllocation: FundDetailSection<FundAssetAllocationDto>;
    managers: FundDetailSection<FundManagersDto>;
    performance: FundDetailSection<FundPerformanceDto>;
    performanceEvaluation: FundDetailSection<FundPerformanceEvaluationDto>;
    subscriptionRedemption: FundDetailSection<FundSubscriptionRedemptionDto>;
    holderStructure: FundDetailSection<FundHolderStructureDto>;
    sameTypeFunds: FundDetailSection<FundSameTypeItemDto[][]>;
    scaleFluctuation: FundDetailSection<FundScaleFluctuationDto>;
    positionTrend: FundDetailSection<FundPositionTrendItemDto[]>;
    totalReturnTrend: FundDetailSection<FundTotalReturnTrendDto>;
  };
  failedSections: string[];
  provider: string;
  updatedAt: string;
}
