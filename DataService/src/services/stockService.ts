import { cacheThrough, ttl } from '../core/cache.js';
import { AppError, assertCode } from '../core/errors.js';
import { ProviderChain } from '../core/providerChain.js';
import type { StockReferenceDto } from '../models/stock.js';
import { EastMoneyStockProvider } from '../providers/eastmoney/eastmoneyStockProvider.js';
import type { ProviderChainResult, StockProvider } from '../providers/types.js';
import type { ServiceResult } from '../types/common.js';

const eastMoneyStockProvider = new EastMoneyStockProvider();

export async function getStockReference(code: string): Promise<ServiceResult<StockReferenceDto>> {
  const stockCode = assertStockCode(code);
  const chain = new ProviderChain<StockProvider>([eastMoneyStockProvider]);
  const result = await cacheThrough(`stock:reference:${stockCode}`, ttl.stockReference, () =>
    chain.run('stock.reference', (provider) => provider.reference(stockCode))
  );

  return toServiceResult(result);
}

function assertStockCode(value: string | undefined): string {
  const code = assertCode(value, 'code').replace(/^(sh|sz)/i, '');
  if (!/^\d{6}$/.test(code)) {
    throw new AppError('INVALID_ARGUMENT', 'Stock code must be a 6-digit code', 400, { code: value });
  }
  return code;
}

export async function getStockReferences(codesValue: string | undefined): Promise<ServiceResult<{ items: Array<{ code: string; success: boolean; data?: StockReferenceDto; error?: { code: string; message: string } }>; summary: { total: number; success: number; failed: number } }>> {
  const codes = parseStockCodes(codesValue);
  const results = await Promise.all(
    codes.map(async (code) => {
      try {
        const result = await getStockReference(code);
        return {
          code,
          success: true as const,
          data: result.data,
          provider: result.provider,
          fallback: result.fallback,
          cached: result.cached,
        };
      } catch (error) {
        const appError = error instanceof AppError ? error : new AppError('INTERNAL_ERROR', 'Stock reference failed', 500);
        return {
          code,
          success: false as const,
          error: {
            code: appError.code,
            message: appError.message,
          },
        };
      }
    })
  );

  const successCount = results.filter((r) => r.success).length;
  return {
    data: {
      items: results,
      summary: {
        total: results.length,
        success: successCount,
        failed: results.length - successCount,
      },
    },
    provider: 'mixed',
    fallback: results.some((r) => r.success && r.fallback),
    cached: false,
    stale: false,
    updatedAt: new Date(),
  };
}

function parseStockCodes(value: string | undefined): string[] {
  const rawCodes = String(value ?? '')
    .split(',')
    .map((code) => code.trim())
    .filter(Boolean);

  if (rawCodes.length === 0) {
    throw new AppError('INVALID_ARGUMENT', 'codes query is required', 400, { codes: value });
  }

  if (rawCodes.length > 100) {
    throw new AppError('INVALID_ARGUMENT', 'Maximum 100 stock codes per request', 400, { count: rawCodes.length });
  }

  const seen = new Set<string>();
  const codes: string[] = [];
  for (const rawCode of rawCodes) {
    const code = assertStockCode(rawCode);
    if (!seen.has(code)) {
      seen.add(code);
      codes.push(code);
    }
  }
  return codes;
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
