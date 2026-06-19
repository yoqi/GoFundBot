import 'dotenv/config';
import { createApp } from './app.js';
import { logger } from './core/logger.js';

const port = Number(process.env.PORT ?? 3100);

const app = createApp();

app.listen(port, () => {
  logger.info('gofund data service started', {
    port,
    env: process.env.NODE_ENV ?? 'development',
  });
});
