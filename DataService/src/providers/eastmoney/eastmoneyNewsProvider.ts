import type { NewsItemDto, NewsListDto, NewsProvider } from '../types.js';
import { fetchJson, fetchText } from './eastmoneyRequest.js';

export class EastMoneyNewsProvider implements NewsProvider {
  readonly name = 'eastmoney';

  async flashNews(count = 30): Promise<NewsListDto> {
    try {
      const url = `https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_${count}_1_.html`;
      const text = await fetchText(url);
      const match = text.match(/ajaxResult\s*=\s*(\{.*\})\s*;?\s*$/s);
      const payload = match ? (JSON.parse(match[1]) as Record<string, unknown>) : (JSON.parse(text) as Record<string, unknown>);

      const newsList = (payload.LivesList ?? payload.data ?? payload.list ?? []) as Record<string, unknown>[];

      const items: NewsItemDto[] = (Array.isArray(newsList) ? newsList : [])
        .map((item) => ({
          title: toString(item.title ?? item.digest ?? item.simtitle),
          summary: toNullableString(item.digest ?? item.summary),
          url: toNullableString(item.url),
          source: '东方财富',
          publishedAt: formatNewsTime(item.showtime ?? item.time ?? item.ctime),
        }))
        .filter((item) => item.title);

      return { items };
    } catch {
      return { items: [] };
    }
  }
}

export class BaiduNewsProvider implements NewsProvider {
  readonly name = 'baidu';

  async flashNews(count = 30): Promise<NewsListDto> {
    try {
      const params = new URLSearchParams({
        rn: String(count),
        pn: '0',
        tag: 'A股',
        finClientType: 'pc',
      });
      const payload = await fetchJson(`https://finance.pae.baidu.com/selfselect/expressnews?${params.toString()}`);

      if (payload.ResultCode !== '0') {
        return { items: [] };
      }

      const newsList = getNested(payload, ['Result', 'content', 'list'], []) as Record<string, unknown>[];

      const items: NewsItemDto[] = (Array.isArray(newsList) ? newsList : [])
        .map((item) => {
          let title = toString(item.title ?? '');
          if (!title && (item.content as Record<string, unknown>)?.items) {
            const contentItems = (item.content as Record<string, unknown>).items as Record<string, unknown>[];
            if (contentItems && contentItems.length > 0) {
              title = toString(contentItems[0].data ?? '');
            }
          }
          return {
            title,
            summary: toNullableString(item.evaluate),
            url: toNullableString(item.url),
            source: '百度股市通',
            publishedAt: formatNewsTime(item.publish_time),
          };
        })
        .filter((item) => item.title);

      return { items };
    } catch {
      return { items: [] };
    }
  }
}

export class ClsNewsProvider implements NewsProvider {
  readonly name = 'cls';

  async flashNews(count = 30): Promise<NewsListDto> {
    try {
      const url = 'https://www.cls.cn/nodeapi/telegraphList';
      const params = new URLSearchParams({
        app: 'CailianpressWeb',
        category: '',
        lastTime: '',
        last_time: '',
        os: 'web',
        refresh_type: '1',
        rn: String(count),
        sv: '8.4.6',
      });

      const payload = await fetchJson(`${url}?${params.toString()}`);
      const data = (payload.data ?? {}) as Record<string, unknown>;
      const newsList = (data.roll_data ?? data.telegram ?? data.list ?? []) as Record<string, unknown>[];

      const items: NewsItemDto[] = (Array.isArray(newsList) ? newsList : [])
        .map((item) => ({
          title: toString(item.content ?? item.title ?? item.brief),
          summary: toNullableString(item.brief ?? item.summary),
          url: toNullableString(item.url),
          source: '财联社',
          publishedAt: formatNewsTime(item.ctime ?? item.time ?? item.created_at),
        }))
        .filter((item) => item.title);

      return { items };
    } catch {
      return { items: [] };
    }
  }
}

function toString(value: unknown): string {
  return value == null ? '' : String(value);
}

function toNullableString(value: unknown): string | null {
  if (value == null || value === '') return null;
  return String(value);
}

function formatNewsTime(value: unknown): string | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number' || /^\d+$/.test(String(value))) {
    let ts = Number(value);
    if (!Number.isFinite(ts)) return null;
    if (ts > 10_000_000_000) ts = Math.floor(ts / 1000);
    return new Date(ts * 1000).toISOString().slice(0, 19).replace('T', ' ');
  }

  const text = String(value).trim();
  if (/^\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}/.test(text)) {
    return text.split(':').length >= 3 ? text : `${text}:00`;
  }
  if (/^\d{1,2}:\d{1,2}/.test(text)) {
    return `${new Date().toISOString().slice(0, 10)} ${text}:00`;
  }
  return text;
}

function getNested(obj: Record<string, unknown>, keys: string[], fallback: unknown): unknown {
  let current: unknown = obj;
  for (const key of keys) {
    if (current && typeof current === 'object' && key in (current as Record<string, unknown>)) {
      current = (current as Record<string, unknown>)[key];
    } else {
      return fallback;
    }
  }
  return current;
}
