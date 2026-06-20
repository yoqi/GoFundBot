import { cacheThrough, ttl } from '../core/cache.js';
import { AppError, assertCode } from '../core/errors.js';
import { ProviderChain } from '../core/providerChain.js';
import type { ServiceResult } from '../types/common.js';
import type {
  FundAssetAllocationDto,
  FundDetailDto,
  FundDetailSection,
  FundDividendListDto,
  FundBasicDto,
  FundEstimateBatchDto,
  FundEstimateDto,
  FundHoldingsDto,
  FundManagersDto,
  FundNavHistoryDto,
  FundRankHistoryDto,
  FundScreeningSnapshotDto,
  FundSearchResultDto,
} from '../types/fund.js';
import { EastMoneyFundProvider } from '../providers/eastmoney/eastmoneyFundProvider.js';
import { StockSdkFundProvider } from '../providers/stock-sdk/stockSdkFundProvider.js';
import type { FundNavHistoryOptions, FundProvider, ProviderChainResult } from '../providers/types.js';

interface DateRange {
  startDate?: string;
  endDate?: string;
}

const stockSdkFundProvider = new StockSdkFundProvider();
const eastMoneyFundProvider = new EastMoneyFundProvider();

export async function getFundEstimate(code: string): Promise<ServiceResult<FundEstimateDto>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([stockSdkFundProvider, eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:estimate:${fundCode}`, ttl.fundEstimate, () =>
    chain.run('fund.estimate', (provider) => provider.estimate(fundCode))
  );

  return toServiceResult(result);
}

export async function getFundEstimates(codesValue: string | undefined): Promise<ServiceResult<FundEstimateBatchDto>> {
  const codes = parseFundCodes(codesValue);
  const results = await Promise.all(
    codes.map(async (code) => {
      try {
        const result = await getFundEstimate(code);
        return {
          code,
          success: true as const,
          data: result.data,
          provider: result.provider,
          fallback: result.fallback,
          cached: result.cached,
          stale: result.stale,
          updatedAt: result.updatedAt.toISOString(),
        };
      } catch (error) {
        const appError = error instanceof AppError ? error : new AppError('INTERNAL_ERROR', 'Fund estimate failed', 500);
        return {
          code,
          success: false as const,
          error: {
            code: appError.code,
            message: appError.message,
            detail: appError.detail,
          },
        };
      }
    })
  );

  const successItems = results.filter((item) => item.success);
  const failedItems = results.filter((item) => !item.success);
  return {
    data: {
      items: successItems,
      failed: failedItems,
      summary: {
        total: results.length,
        success: successItems.length,
        failed: failedItems.length,
      },
    },
    provider: 'mixed',
    fallback: successItems.some((item) => item.fallback),
    cached: successItems.length > 0 && successItems.every((item) => item.cached),
    stale: successItems.some((item) => item.stale),
    updatedAt: new Date(),
  };
}

export async function searchFunds(keyword: string | undefined): Promise<ServiceResult<FundSearchResultDto>> {
  const q = String(keyword ?? '').trim();
  if (!q) {
    throw new AppError('INVALID_ARGUMENT', 'q query is required', 400, { q: keyword });
  }

  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:search:${q}`, ttl.fundSearch, () =>
    chain.run('fund.search', (provider) => {
      if (!provider.search) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement fund search`, 501);
      }
      return provider.search(q);
    })
  );

  return toServiceResult(result);
}

export async function getFundBasic(code: string): Promise<ServiceResult<FundBasicDto>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:basic:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.basic', (provider) => {
      if (!provider.basic) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement fund basic`, 501);
      }
      return provider.basic(fundCode);
    })
  );

  return toServiceResult(result);
}

export async function getFundScreeningSnapshot(options: {
  types?: string[];
  sort?: string;
  pageSize?: number;
}): Promise<ServiceResult<FundScreeningSnapshotDto>> {
  const types = options.types?.filter(Boolean);
  const sort = options.sort || '1nzf';
  const pageSize = options.pageSize || 500;
  const key = `fund:screening-snapshot:${(types || []).join(',')}:${sort}:${pageSize}`;
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(key, ttl.fundScreeningSnapshot, () =>
    chain.run('fund.screeningSnapshot', (provider) => {
      if (!provider.screeningSnapshot) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement fund screening snapshot`, 501);
      }
      return provider.screeningSnapshot({ types, sort, pageSize });
    })
  );

  return toServiceResult(result);
}

export async function getFundNavHistory(
  code: string,
  range: DateRange
): Promise<ServiceResult<FundNavHistoryDto>> {
  const fundCode = assertFundCode(code);
  assertDateRange(range);
  const options = normalizeDateRange(range);
  const key = `fund:nav-history:${fundCode}:${options.startDate ?? ''}:${options.endDate ?? ''}`;
  const chain = new ProviderChain<FundProvider>([stockSdkFundProvider, eastMoneyFundProvider]);
  const result = await cacheThrough(key, ttl.fundNavHistory, () =>
    chain.run('fund.navHistory', (provider) => provider.navHistory(fundCode, options))
  );

  return toServiceResult(result);
}

export async function getFundRankHistory(code: string): Promise<ServiceResult<FundRankHistoryDto>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([stockSdkFundProvider]);
  const result = await cacheThrough(`fund:rank-history:${fundCode}`, ttl.fundRankHistory, () =>
    chain.run('fund.rankHistory', (provider) => provider.rankHistory(fundCode))
  );

  return toServiceResult(result);
}

const DIVIDENDS_TIMEOUT_MS = 15000; // 15s – stock-sdk dividends can be slow

export async function getFundDividends(code: string): Promise<ServiceResult<FundDividendListDto>> {
  const fundCode = assertFundCode(code);
  try {
    const chain = new ProviderChain<FundProvider>([stockSdkFundProvider]);
    const result = await cacheThrough(`fund:dividends:${fundCode}`, ttl.fundDividends, () =>
      withTimeout(
        chain.run('fund.dividends', (provider) => provider.dividends(fundCode)),
        DIVIDENDS_TIMEOUT_MS,
        `fund.dividends(${fundCode})`
      )
    );

    return toServiceResult(result);
  } catch (error) {
    // Return graceful empty result instead of crashing
    const appError = error instanceof AppError ? error : new AppError('PROVIDER_TIMEOUT', 'Dividends request timed out', 504);
    return {
      data: { items: [], totalPages: 0, pageSize: 0, currentPage: 0 },
      provider: 'none',
      fallback: true,
      cached: false,
      stale: false,
      updatedAt: new Date(),
    };
  }
}

function assertFundCode(value: string | undefined): string {
  const code = assertCode(value, 'code');
  if (!/^\d{6}$/.test(code)) {
    throw new AppError('INVALID_ARGUMENT', 'Fund code must be a 6-digit code', 400, { code: value });
  }
  return code;
}

function parseFundCodes(value: string | undefined): string[] {
  const rawCodes = String(value ?? '')
    .split(',')
    .map((code) => code.trim())
    .filter(Boolean);

  if (rawCodes.length === 0) {
    throw new AppError('INVALID_ARGUMENT', 'codes query is required', 400, { codes: value });
  }

  const seen = new Set<string>();
  const codes: string[] = [];
  for (const rawCode of rawCodes) {
    const code = assertFundCode(rawCode);
    if (!seen.has(code)) {
      seen.add(code);
      codes.push(code);
    }
  }
  return codes;
}

function assertDateRange({ startDate, endDate }: DateRange): void {
  if (startDate && !isDateLike(startDate)) {
    throw new AppError('INVALID_ARGUMENT', 'startDate must be YYYY-MM-DD or YYYYMMDD', 400, { startDate });
  }
  if (endDate && !isDateLike(endDate)) {
    throw new AppError('INVALID_ARGUMENT', 'endDate must be YYYY-MM-DD or YYYYMMDD', 400, { endDate });
  }
}

function normalizeDateRange(range: DateRange): FundNavHistoryOptions {
  return {
    startDate: range.startDate ? normalizeDate(range.startDate) : undefined,
    endDate: range.endDate ? normalizeDate(range.endDate) : undefined,
  };
}

function isDateLike(value: string): boolean {
  return /^\d{4}-?\d{2}-?\d{2}$/.test(value);
}

function normalizeDate(value: string): string {
  return value.replaceAll('-', '');
}

const DETAIL_SECTION_TIMEOUT_MS = 12000; // 12s per section – enough for slow providers

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new AppError('PROVIDER_TIMEOUT', `${label} timed out after ${ms}ms`, 504)), ms)
    ),
  ]);
}

export async function getFundDetail(code: string): Promise<ServiceResult<FundDetailDto>> {
  const fundCode = assertFundCode(code);

  const sectionNames = [
    'basic', 'estimate', 'navHistory', 'rankHistory', 'dividends',
    'holdings', 'assetAllocation', 'managers', 'performance',
    'performanceEvaluation', 'subscriptionRedemption', 'holderStructure',
    'sameTypeFunds', 'scaleFluctuation', 'positionTrend', 'totalReturnTrend',
  ] as const;

  const results = await Promise.allSettled(
    sectionNames.map(async (section) => {
      const label = `fund.${section}(${fundCode})`;
      switch (section) {
        case 'basic':
          return { section, result: await withTimeout(getFundBasic(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'estimate':
          return { section, result: await withTimeout(getFundEstimate(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'navHistory':
          return { section, result: await withTimeout(getFundNavHistory(fundCode, {}), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'rankHistory':
          return { section, result: await withTimeout(getFundRankHistory(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'dividends':
          return { section, result: await withTimeout(getFundDividends(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'holdings':
          return { section, result: await withTimeout(getFundHoldings(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'assetAllocation':
          return { section, result: await withTimeout(getFundAssetAllocation(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'managers':
          return { section, result: await withTimeout(getFundManagers(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'performance':
          return { section, result: await withTimeout(getFundPerformance(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'performanceEvaluation':
          return { section, result: await withTimeout(getFundPerformanceEvaluation(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'subscriptionRedemption':
          return { section, result: await withTimeout(getFundSubscriptionRedemption(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'holderStructure':
          return { section, result: await withTimeout(getFundHolderStructure(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'sameTypeFunds':
          return { section, result: await withTimeout(getFundSameTypeFunds(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'scaleFluctuation':
          return { section, result: await withTimeout(getFundScaleFluctuation(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'positionTrend':
          return { section, result: await withTimeout(getFundPositionTrend(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        case 'totalReturnTrend':
          return { section, result: await withTimeout(getFundTotalReturnTrend(fundCode), DETAIL_SECTION_TIMEOUT_MS, label) };
        default:
          throw new AppError('INTERNAL_ERROR', `Unknown section: ${section}`, 500);
      }
    })
  );

  const sections: FundDetailDto['sections'] = {} as FundDetailDto['sections'];
  const failedSections: string[] = [];
  const providers: string[] = [];

  for (const r of results) {
    if (r.status === 'fulfilled') {
      const { section, result } = r.value;
      (sections as Record<string, unknown>)[section] = {
        data: result.data,
        provider: result.provider,
        fallback: result.fallback,
        cached: result.cached,
        updatedAt: result.updatedAt.toISOString(),
      };
      providers.push(result.provider);
    } else {
      const section = sectionNames[results.indexOf(r)] || 'unknown';
      failedSections.push(section);
      (sections as Record<string, unknown>)[section] = {
        data: null,
        provider: null,
        fallback: false,
        cached: false,
        updatedAt: null,
        error: {
          code: 'SECTION_FAILED',
          message: r.reason instanceof Error ? r.reason.message : String(r.reason),
        },
      };
    }
  }

  return {
    data: {
      code: fundCode,
      sections,
      failedSections,
      provider: failedSections.length === 0
        ? (new Set(providers).size === 1 ? providers[0] : 'mixed')
        : 'mixed',
      updatedAt: new Date().toISOString(),
    },
    provider: failedSections.length === 0
      ? (new Set(providers).size === 1 ? providers[0] : 'mixed')
      : 'mixed',
    fallback: providers.some((p) => p === 'eastmoney'),
    cached: false,
    stale: false,
    updatedAt: new Date(),
  };
}

export async function getFundHoldings(code: string): Promise<ServiceResult<FundHoldingsDto>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:holdings:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.holdings', (provider) => {
      if (!provider.holdings) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement fund holdings`, 501);
      }
      return provider.holdings(fundCode);
    })
  );

  return toServiceResult(result);
}

export async function getFundManagers(code: string): Promise<ServiceResult<FundManagersDto>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:managers:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.managers', (provider) => {
      if (!provider.managers) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement fund managers`, 501);
      }
      return provider.managers(fundCode);
    })
  );

  return toServiceResult(result);
}

export async function getFundAssetAllocation(code: string): Promise<ServiceResult<FundAssetAllocationDto>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:asset-allocation:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.assetAllocation', (provider) => {
      if (!provider.assetAllocation) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement asset allocation`, 501);
      }
      return provider.assetAllocation(fundCode);
    })
  );

  return toServiceResult(result);
}

async function getFundPerformance(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:performance:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.performance', (provider) => {
      if (!provider.performance) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement performance`, 501);
      }
      return provider.performance(fundCode);
    })
  );

  return toServiceResult(result);
}

async function getFundSubscriptionRedemption(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:sub:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.subscriptionRedemption', (provider) => {
      if (!provider.subscriptionRedemption) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement subscriptionRedemption`, 501);
      }
      return provider.subscriptionRedemption(fundCode);
    })
  );

  return toServiceResult(result);
}

async function getFundPerformanceEvaluation(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:perfEval:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.performanceEvaluation', (provider) => {
      if (!provider.performanceEvaluation) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement performanceEvaluation`, 501);
      }
      return provider.performanceEvaluation(fundCode);
    })
  );
  return toServiceResult(result);
}

async function getFundHolderStructure(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:holder:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.holderStructure', (provider) => {
      if (!provider.holderStructure) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement holderStructure`, 501);
      }
      return provider.holderStructure(fundCode);
    })
  );
  return toServiceResult(result);
}

async function getFundSameTypeFunds(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:sameType:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.sameTypeFunds', (provider) => {
      if (!provider.sameTypeFunds) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement sameTypeFunds`, 501);
      }
      return provider.sameTypeFunds(fundCode);
    })
  );
  return toServiceResult(result);
}

async function getFundScaleFluctuation(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:scale:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.scaleFluctuation', (provider) => {
      if (!provider.scaleFluctuation) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement scaleFluctuation`, 501);
      }
      return provider.scaleFluctuation(fundCode);
    })
  );
  return toServiceResult(result);
}

async function getFundPositionTrend(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:posTrend:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.positionTrend', (provider) => {
      if (!provider.positionTrend) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement positionTrend`, 501);
      }
      return provider.positionTrend(fundCode);
    })
  );
  return toServiceResult(result);
}

async function getFundTotalReturnTrend(code: string): Promise<ServiceResult<unknown>> {
  const fundCode = assertFundCode(code);
  const chain = new ProviderChain<FundProvider>([eastMoneyFundProvider]);
  const result = await cacheThrough(`fund:totalReturn:${fundCode}`, ttl.fundBasic, () =>
    chain.run('fund.totalReturnTrend', (provider) => {
      if (!provider.totalReturnTrend) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement totalReturnTrend`, 501);
      }
      return provider.totalReturnTrend(fundCode);
    })
  );
  return toServiceResult(result);
}

function toServiceResult<T>(lookup: {
  value: ProviderChainResult<T>;
  cached: boolean;
  updatedAt: Date;
}): ServiceResult<T> {
  return {
    data: lookup.value.data,
    provider: lookup.value.provider,
    fallback: lookup.value.fallback,
    cached: lookup.cached,
    stale: lookup.value.stale,
    updatedAt: lookup.updatedAt,
  };
}
