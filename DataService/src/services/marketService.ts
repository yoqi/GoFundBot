import { cacheThrough, ttl } from '../core/cache.js';
import { AppError, assertCode } from '../core/errors.js';
import { ProviderChain } from '../core/providerChain.js';
import type { ServiceResult } from '../types/common.js';
import { StockSdkMarketProvider } from '../providers/stock-sdk/stockSdkMarketProvider.js';
import { EastMoneyMarketProvider } from '../providers/eastmoney/eastmoneyMarketProvider.js';
import type {
  ConstituentListDto,
  IndexListDto,
  KlineDto,
  KlineOptions,
  MarketProvider,
  MarketQuoteDto,
  ProviderChainResult,
  SectorListDto,
} from '../providers/types.js';

export type { KlineDto, MarketQuoteDto } from '../providers/types.js';
export type { ConstituentListDto, IndexListDto, SectorListDto } from '../providers/types.js';

export interface KlineQuery {
  period?: string;
  adjust?: string;
  startDate?: string;
  endDate?: string;
}

const stockSdkMarketProvider = new StockSdkMarketProvider();
const eastMoneyMarketProvider = new EastMoneyMarketProvider();

export async function getMarketQuotes(symbolsParam: string | undefined): Promise<ServiceResult<MarketQuoteDto[]>> {
  const symbols = parseSymbols(symbolsParam);
  const key = `market:quotes:${symbols.join(',')}`;
  const chain = new ProviderChain<MarketProvider>([stockSdkMarketProvider]);
  const result = await cacheThrough(key, ttl.marketQuotes, () =>
    chain.run('market.quotes', (provider) => provider.quotes(symbols))
  );

  return toServiceResult(result);
}

export async function getMarketKline(symbol: string, query: KlineQuery): Promise<ServiceResult<KlineDto[]>> {
  const stockSymbol = assertStockSymbol(symbol);
  const options = parseKlineOptions(query);
  const key = `market:kline:${stockSymbol}:${JSON.stringify(options)}`;
  const chain = new ProviderChain<MarketProvider>([stockSdkMarketProvider]);
  const result = await cacheThrough(key, ttl.marketKline, () =>
    chain.run('market.kline', (provider) => provider.kline(stockSymbol, options))
  );

  return toServiceResult(result);
}

function parseSymbols(value: string | undefined): string[] {
  if (!value) {
    throw new AppError('INVALID_ARGUMENT', 'symbols query is required', 400, { symbols: value });
  }

  const symbols = value
    .split(',')
    .map((symbol) => symbol.trim())
    .filter(Boolean);

  if (symbols.length === 0) {
    throw new AppError('INVALID_ARGUMENT', 'symbols query must contain at least one symbol', 400, { symbols: value });
  }

  return symbols.map((symbol) => assertStockSymbol(symbol));
}

function assertStockSymbol(value: string | undefined): string {
  const symbol = assertCode(value, 'symbol');
  if (!/^(sh|sz)?\d{6}$/i.test(symbol)) {
    throw new AppError('INVALID_ARGUMENT', 'A-share symbol must look like sh600519, sz000001, or 600519', 400, {
      symbol: value,
    });
  }
  return symbol.toLowerCase();
}

function parseKlineOptions(query: KlineQuery): KlineOptions {
  const period = query.period ?? 'daily';
  const adjust = query.adjust ?? 'none';

  if (!['daily', 'weekly', 'monthly'].includes(period)) {
    throw new AppError('INVALID_ARGUMENT', 'period must be daily, weekly, or monthly', 400, { period });
  }

  if (!['none', 'qfq', 'hfq'].includes(adjust)) {
    throw new AppError('INVALID_ARGUMENT', 'adjust must be none, qfq, or hfq', 400, { adjust });
  }

  if (query.startDate && !isDateLike(query.startDate)) {
    throw new AppError('INVALID_ARGUMENT', 'startDate must be YYYY-MM-DD or YYYYMMDD', 400, {
      startDate: query.startDate,
    });
  }

  if (query.endDate && !isDateLike(query.endDate)) {
    throw new AppError('INVALID_ARGUMENT', 'endDate must be YYYY-MM-DD or YYYYMMDD', 400, {
      endDate: query.endDate,
    });
  }

  const normalizedAdjust: '' | 'qfq' | 'hfq' = adjust === 'none' ? '' : (adjust as 'qfq' | 'hfq');

  return {
    period: period as 'daily' | 'weekly' | 'monthly',
    adjust: normalizedAdjust,
    startDate: query.startDate ? normalizeDate(query.startDate) : undefined,
    endDate: query.endDate ? normalizeDate(query.endDate) : undefined,
  };
}

function isDateLike(value: string): boolean {
  return /^\d{4}-?\d{2}-?\d{2}$/.test(value);
}

function normalizeDate(value: string): string {
  return value.replaceAll('-', '');
}

export async function getMarketSectors(): Promise<ServiceResult<SectorListDto>> {
  const chain = new ProviderChain<MarketProvider>([eastMoneyMarketProvider]);
  const result = await cacheThrough('market:sectors', ttl.marketQuotes, () =>
    chain.run('market.sectors', (provider) => {
      if (!provider.sectors) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement sectors`, 501);
      }
      return provider.sectors();
    })
  );

  return toServiceResult(result);
}

export async function getMarketSectorConstituents(code: string): Promise<ServiceResult<ConstituentListDto>> {
  const sectorCode = assertCode(code, 'code');
  const chain = new ProviderChain<MarketProvider>([eastMoneyMarketProvider]);
  const result = await cacheThrough(`market:sector:${sectorCode}:constituents`, ttl.marketQuotes, () =>
    chain.run('market.sectorConstituents', (provider) => {
      if (!provider.sectorConstituents) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement sector constituents`, 501);
      }
      return provider.sectorConstituents(sectorCode);
    })
  );

  return toServiceResult(result);
}

export async function getMarketIndices(): Promise<ServiceResult<IndexListDto>> {
  const chain = new ProviderChain<MarketProvider>([stockSdkMarketProvider, eastMoneyMarketProvider]);
  const result = await cacheThrough('market:indices', ttl.marketQuotes, () =>
    chain.run('market.indices', (provider) => {
      if (!provider.indices) {
        throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement indices`, 501);
      }
      return provider.indices();
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
