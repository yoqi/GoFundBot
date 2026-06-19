import type { IndexDto, IndexListDto, KlineDto, KlineOptions, MarketProvider, MarketQuoteDto } from '../types.js';
import { getStockSdk } from './stockSdkClient.js';
import { AppError } from '../../core/errors.js';

const DEFAULT_INDICES = [
  { symbol: 'sh000001', name: '上证指数', market: 'A股' },
  { symbol: 'sz399001', name: '深证成指', market: 'A股' },
  { symbol: 'sz399006', name: '创业板指', market: 'A股' },
  { symbol: 'sh000300', name: '沪深300', market: 'A股' },
  { symbol: 'sh000688', name: '科创50', market: 'A股' },
];

export class StockSdkMarketProvider implements MarketProvider {
  readonly name = 'stock-sdk';

  async quotes(symbols: string[]): Promise<MarketQuoteDto[]> {
    assertStockSdkNotForcedToFail();
    const raw = await getStockSdk().quotes.cnSimple(symbols);
    return raw.map(mapMarketQuote);
  }

  async kline(symbol: string, options: KlineOptions): Promise<KlineDto[]> {
    assertStockSdkNotForcedToFail();
    const raw = await getStockSdk().kline.cn(symbol, options);
    return raw.map(mapKline);
  }

  async indices(): Promise<IndexListDto> {
    assertStockSdkNotForcedToFail();
    try {
      const symbols = DEFAULT_INDICES.map((idx) => idx.symbol);
      const quotes = await getStockSdk().quotes.cnSimple(symbols);

      const quoteMap = new Map<string, MarketQuoteDto>();
      for (const q of quotes) {
        const mapped = mapMarketQuote(q);
        quoteMap.set(mapped.symbol, mapped);
      }

      const items: IndexDto[] = DEFAULT_INDICES.map((idx) => {
        const quote = quoteMap.get(idx.symbol);
        return {
          code: idx.symbol,
          name: idx.name,
          price: quote?.price ?? null,
          changePercent: quote?.changePercent ?? null,
          changeAmount: quote?.change ?? null,
          market: idx.market,
        };
      });

      return { items };
    } catch (error) {
      throw new AppError(
        'STOCK_SDK_ERROR',
        `stock-sdk indices failed: ${error instanceof Error ? error.message : String(error)}`,
        502
      );
    }
  }
}

function assertStockSdkNotForcedToFail(): void {
  if (process.env.DATA_SERVICE_FORCE_STOCK_SDK_FAILURE === '1') {
    throw new Error('Forced stock-sdk provider failure');
  }
}

function mapMarketQuote(raw: unknown): MarketQuoteDto {
  const item = asRecord(raw);
  const code = toStringValue(item.code);
  const marketId = toStringValue(item.marketId).toLowerCase();

  return {
    symbol: marketId && code ? `${marketId}${code}` : code,
    code,
    name: toStringValue(item.name),
    price: toNumberValue(item.price),
    change: toNumberValue(item.change),
    changePercent: toNumberValue(item.changePercent),
    volume: toNumberValue(item.volume),
    amount: toNumberValue(item.amount),
    market: toStringValue(item.market),
    assetType: toStringValue(item.assetType),
    source: toOptionalString(item.source),
  };
}

function mapKline(raw: unknown): KlineDto {
  const item = asRecord(raw);
  return {
    code: toStringValue(item.code),
    date: toOptionalString(item.date),
    time: toOptionalString(item.time),
    timestamp: toNullableNumber(item.timestamp),
    open: toNullableNumber(item.open),
    close: toNullableNumber(item.close),
    high: toNullableNumber(item.high),
    low: toNullableNumber(item.low),
    volume: toNullableNumber(item.volume),
    amount: toNullableNumber(item.amount),
    change: toNullableNumber(item.change),
    changePercent: toNullableNumber(item.changePercent),
    turnoverRate: toNullableNumber(item.turnoverRate),
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function toStringValue(value: unknown): string {
  return value == null ? '' : String(value);
}

function toOptionalString(value: unknown): string | undefined {
  return value == null || value === '' ? undefined : String(value);
}

function toNumberValue(value: unknown): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
}

function toNullableNumber(value: unknown): number | null {
  if (value == null || value === '') {
    return null;
  }
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}
