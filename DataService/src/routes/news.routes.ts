import { Router } from 'express';
import { asyncHandler } from '../core/errors.js';
import { sendSuccess } from '../core/response.js';
import { getFlashNews } from '../services/newsService.js';

export const newsRouter = Router();

newsRouter.get(
  '/flash',
  asyncHandler(async (req, res) => {
    const count = parseInt(String(req.query.count ?? '30'), 10) || 30;
    sendSuccess(res, await getFlashNews(Math.min(Math.max(count, 1), 100)));
  })
);
