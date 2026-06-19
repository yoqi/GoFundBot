import cors from 'cors';
import express, { type NextFunction, type Request, type Response } from 'express';
import { errorHandler, notFoundHandler } from './core/errors.js';
import { logger } from './core/logger.js';
import { fundRouter } from './routes/fund.routes.js';
import { healthRouter } from './routes/health.routes.js';
import { marketRouter } from './routes/market.routes.js';
import { newsRouter } from './routes/news.routes.js';
import { stockRouter } from './routes/stock.routes.js';

export function createApp() {
  const app = express();

  app.disable('x-powered-by');
  app.use(cors({ origin: parseCorsOrigin(process.env.CORS_ORIGIN) }));
  app.use(express.json({ limit: '256kb' }));
  app.use(requestLogger);

  app.use('/api/health', healthRouter);
  app.use('/api/funds', fundRouter);
  app.use('/api/market', marketRouter);
  app.use('/api/stocks', stockRouter);
  app.use('/api/news', newsRouter);

  app.use(notFoundHandler);
  app.use(errorHandler);

  return app;
}

function requestLogger(req: Request, res: Response, next: NextFunction): void {
  const startedAt = Date.now();
  logger.info('request start', {
    method: req.method,
    path: req.path,
    query: Object.keys(req.query),
  });

  res.on('finish', () => {
    logger.info('request end', {
      method: req.method,
      path: req.path,
      statusCode: res.statusCode,
      durationMs: Date.now() - startedAt,
    });
  });

  next();
}

function parseCorsOrigin(raw: string | undefined): boolean | string[] {
  if (!raw || raw.trim() === '*') {
    return true;
  }
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}
