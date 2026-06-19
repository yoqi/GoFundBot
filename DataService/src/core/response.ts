import type { Response } from 'express';
import type { ApiFailure, ApiSuccess, ResponseMeta, ServiceResult } from '../types/common.js';

export function makeMeta(
  cached = false,
  updatedAt: Date = new Date(),
  provider: string | null = null,
  fallback = false,
  stale = false
): ResponseMeta {
  return {
    source: 'DataService',
    provider,
    fallback,
    cached,
    stale,
    updatedAt: updatedAt.toISOString(),
  };
}

export function sendSuccess<T>(res: Response, result: ServiceResult<T> | T): void {
  const wrapped = isServiceResult(result)
    ? result
    : { data: result, provider: null, fallback: false, cached: false, stale: false, updatedAt: new Date() };

  const body: ApiSuccess<T> = {
    success: true,
    data: wrapped.data,
    meta: makeMeta(wrapped.cached, wrapped.updatedAt, wrapped.provider, wrapped.fallback, wrapped.stale),
  };

  res.json(body);
}

export function sendFailure(
  res: Response,
  statusCode: number,
  error: { code: string; message: string; detail?: unknown },
  cached = false
): void {
  const body: ApiFailure = {
    success: false,
    error,
    meta: makeMeta(cached),
  };

  res.status(statusCode).json(body);
}

function isServiceResult<T>(value: ServiceResult<T> | T): value is ServiceResult<T> {
  return Boolean(
    value &&
      typeof value === 'object' &&
      'data' in value &&
      'provider' in value &&
      'fallback' in value &&
      'cached' in value &&
      'stale' in value &&
      'updatedAt' in value
  );
}
