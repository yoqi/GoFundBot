import { AppError } from '../core/errors.js';
import type { ServiceResult } from '../types/common.js';
import type { NewsItemDto, NewsListDto, NewsProvider } from '../providers/types.js';
import { BaiduNewsProvider, ClsNewsProvider, EastMoneyNewsProvider } from '../providers/eastmoney/eastmoneyNewsProvider.js';

const eastmoneyNewsProvider = new EastMoneyNewsProvider();
const baiduNewsProvider = new BaiduNewsProvider();
const clsNewsProvider = new ClsNewsProvider();

const allProviders: NewsProvider[] = [eastmoneyNewsProvider, baiduNewsProvider, clsNewsProvider];

export async function getFlashNews(count = 30): Promise<ServiceResult<NewsListDto>> {
  const providerResults = await Promise.allSettled(
    allProviders.map(async (provider) => {
      try {
        if (!provider.flashNews) {
          throw new AppError('PROVIDER_UNAVAILABLE', `${provider.name} does not implement flashNews`, 501);
        }
        const result = await provider.flashNews(count);
        return { provider: provider.name, items: result.items };
      } catch {
        return { provider: provider.name, items: [] as NewsItemDto[] };
      }
    })
  );

  const allItems: NewsItemDto[] = [];
  const successfulProviders: string[] = [];
  const failedProviders: string[] = [];

  for (const r of providerResults) {
    if (r.status === 'fulfilled') {
      allItems.push(...r.value.items);
      if (r.value.items.length > 0) {
        successfulProviders.push(r.value.provider);
      } else {
        failedProviders.push(r.value.provider);
      }
    } else {
      // Couldn't determine provider name for rejected promise
    }
  }

  // De-duplicate by title
  const seen = new Set<string>();
  const deduped = allItems.filter((item) => {
    const key = item.title.trim();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Sort by publishedAt descending
  deduped.sort((a, b) => {
    const ta = a.publishedAt ?? '';
    const tb = b.publishedAt ?? '';
    return tb.localeCompare(ta);
  });

  // Limit to count
  const items = deduped.slice(0, count);

  // Build meta
  const provider = successfulProviders.length === 0
    ? 'none'
    : successfulProviders.length === 1
      ? successfulProviders[0]
      : 'mixed';

  return {
    data: { items },
    provider,
    fallback: failedProviders.length > 0,
    cached: false,
    stale: false,
    updatedAt: new Date(),
  };
}
