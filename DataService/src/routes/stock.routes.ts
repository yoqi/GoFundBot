import { Router } from 'express';
import { asyncHandler } from '../core/errors.js';
import { sendSuccess } from '../core/response.js';
import { getStockReference, getStockReferences } from '../services/stockService.js';

export const stockRouter = Router();

stockRouter.get(
  '/references',
  asyncHandler(async (req, res) => {
    sendSuccess(res, await getStockReferences(firstQueryValue(req.query.codes)));
  })
);

stockRouter.get(
  '/:code/reference',
  asyncHandler(async (req, res) => {
    sendSuccess(res, await getStockReference(routeParam(req.params.code)));
  })
);

function routeParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? '' : value ?? '';
}

function firstQueryValue(value: unknown): string | undefined {
  if (Array.isArray(value)) {
    return typeof value[0] === 'string' ? value[0] : undefined;
  }
  return typeof value === 'string' ? value : undefined;
}
