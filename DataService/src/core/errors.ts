import type { ErrorRequestHandler, NextFunction, Request, Response } from 'express';
import { sendFailure } from './response.js';
import { logger } from './logger.js';

export type AppErrorCode =
  | 'INVALID_ARGUMENT'
  | 'STOCK_SDK_ERROR'
  | 'PROVIDER_TIMEOUT'
  | 'PROVIDER_UNAVAILABLE'
  | 'INTERNAL_ERROR';

export class AppError extends Error {
  readonly code: AppErrorCode;
  readonly statusCode: number;
  readonly detail?: unknown;

  constructor(code: AppErrorCode, message: string, statusCode = 500, detail?: unknown) {
    super(message);
    this.name = 'AppError';
    this.code = code;
    this.statusCode = statusCode;
    this.detail = detail;
  }
}

export function asyncHandler(
  handler: (req: Request, res: Response, next: NextFunction) => Promise<void>
) {
  return (req: Request, res: Response, next: NextFunction) => {
    handler(req, res, next).catch(next);
  };
}

export function assertCode(value: string | undefined, field = 'code'): string {
  const code = String(value ?? '').trim();
  if (!/^[A-Za-z0-9._-]+$/.test(code)) {
    throw new AppError('INVALID_ARGUMENT', `Invalid ${field}`, 400, { [field]: value });
  }
  return code;
}

export function toAppError(error: unknown): AppError {
  if (error instanceof AppError) {
    return error;
  }

  const detail = normalizeErrorDetail(error);
  const message = detail.message ?? 'provider request failed';
  const lower = message.toLowerCase();

  if (lower.includes('timeout') || lower.includes('abort')) {
    return new AppError('PROVIDER_TIMEOUT', 'Provider request timed out', 504, detail);
  }

  if (
    lower.includes('network') ||
    lower.includes('fetch failed') ||
    lower.includes('econn') ||
    lower.includes('unavailable')
  ) {
    return new AppError('PROVIDER_UNAVAILABLE', 'Provider is unavailable', 503, detail);
  }

  return new AppError('STOCK_SDK_ERROR', message, 502, detail);
}

export const notFoundHandler = (req: Request, res: Response): void => {
  sendFailure(res, 404, {
    code: 'INVALID_ARGUMENT',
    message: `Route not found: ${req.method} ${req.path}`,
    detail: { method: req.method, path: req.path },
  });
};

export const errorHandler: ErrorRequestHandler = (error, req, res, _next) => {
  const appError = error instanceof AppError ? error : toAppError(error);

  logger.error('request failed', {
    method: req.method,
    path: req.path,
    code: appError.code,
    statusCode: appError.statusCode,
    message: appError.message,
  });

  if (res.headersSent) {
    return;
  }

  sendFailure(res, appError.statusCode, {
    code: appError.code,
    message: appError.message,
    detail: appError.detail,
  });
};

function normalizeErrorDetail(error: unknown): { name?: string; message?: string; code?: unknown } {
  if (error instanceof Error) {
    const maybeCode = 'code' in error ? (error as { code?: unknown }).code : undefined;
    return {
      name: error.name,
      message: error.message,
      code: maybeCode,
    };
  }

  if (typeof error === 'object' && error !== null) {
    const record = error as Record<string, unknown>;
    return {
      name: typeof record.name === 'string' ? record.name : undefined,
      message: typeof record.message === 'string' ? record.message : undefined,
      code: record.code,
    };
  }

  return { message: String(error) };
}
