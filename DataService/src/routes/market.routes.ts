import { Router } from 'express';
import { asyncHandler } from '../core/errors.js';
import { sendSuccess } from '../core/response.js';
import {
  getMarketIndices,
  getMarketKline,
  getMarketQuotes,
  getMarketSectorConstituents,
  getMarketSectors,
} from '../services/marketService.js';

export const marketRouter = Router();

marketRouter.get(
  '/quotes',
  asyncHandler(async (req, res) => {
    sendSuccess(res, await getMarketQuotes(firstQueryValue(req.query.symbols)));
  })
);

marketRouter.get(
  '/kline/:symbol',
  asyncHandler(async (req, res) => {
    sendSuccess(
      res,
      await getMarketKline(routeParam(req.params.symbol), {
        period: firstQueryValue(req.query.period),
        adjust: firstQueryValue(req.query.adjust),
        startDate: firstQueryValue(req.query.startDate),
        endDate: firstQueryValue(req.query.endDate),
      })
    );
  })
);

marketRouter.get(
  '/indices',
  asyncHandler(async (req, res) => {
    sendSuccess(res, await getMarketIndices());
  })
);

marketRouter.get(
  '/sectors',
  asyncHandler(async (req, res) => {
    sendSuccess(res, await getMarketSectors());
  })
);

marketRouter.get(
  '/sectors/:code/constituents',
  asyncHandler(async (req, res) => {
    sendSuccess(res, await getMarketSectorConstituents(routeParam(req.params.code)));
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
