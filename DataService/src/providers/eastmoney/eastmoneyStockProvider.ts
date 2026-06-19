import { URLSearchParams } from 'node:url';
import type { StockReferenceDto } from '../../models/stock.js';
import { AppError } from '../../core/errors.js';
import type { StockProvider } from '../types.js';
import { fetchJson } from './eastmoneyRequest.js';

export class EastMoneyStockProvider implements StockProvider {
  readonly name = 'eastmoney';

  async reference(code: string): Promise<StockReferenceDto> {
    const normalized = normalizeStockCode(code);
    const secid = toEastMoneySecid(normalized);
    const params = new URLSearchParams({
      fields: 'f57,f58,f107,f12,f14,f13',
      secid,
    });
    const payload = await fetchJson(`https://push2.eastmoney.com/api/qt/stock/get?${params.toString()}`);
    const data = payload.data;
    if (!data || typeof data !== 'object') {
      throw new AppError('PROVIDER_UNAVAILABLE', 'EastMoney stock reference returned empty data', 502, { code });
    }

    const record = data as Record<string, unknown>;
    const stockCode = toStringValue(record.f57 || record.f12 || normalized);
    const market = inferMarket(toStringValue(record.f107 || record.f13), stockCode);
    return {
      code: stockCode,
      name: toStringValue(record.f58 || record.f14 || stockCode),
      market,
      symbol: market === 'SH' ? `sh${stockCode}` : market === 'SZ' ? `sz${stockCode}` : stockCode,
    };
  }
}

function normalizeStockCode(code: string): string {
  return code.trim().replace(/^(sh|sz)/i, '');
}

function toEastMoneySecid(code: string): string {
  if (/^6|^9/.test(code)) {
    return `1.${code}`;
  }
  return `0.${code}`;
}

function inferMarket(rawMarket: string, code: string): string {
  if (rawMarket === '1' || /^6|^9/.test(code)) {
    return 'SH';
  }
  if (rawMarket === '0' || /^0|^3/.test(code)) {
    return 'SZ';
  }
  return rawMarket || 'CN';
}

function toStringValue(value: unknown): string {
  return value == null ? '' : String(value);
}
