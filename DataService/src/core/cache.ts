interface CacheEntry<T> {
  value: T;
  expiresAt: number;
  updatedAt: Date;
}

export interface CacheLookup<T> {
  value: T;
  cached: boolean;
  updatedAt: Date;
}

class MemoryCache {
  private readonly store = new Map<string, CacheEntry<unknown>>();

  get<T>(key: string): CacheLookup<T> | null {
    const entry = this.store.get(key) as CacheEntry<T> | undefined;
    if (!entry) {
      return null;
    }

    if (Date.now() >= entry.expiresAt) {
      this.store.delete(key);
      return null;
    }

    return {
      value: entry.value,
      cached: true,
      updatedAt: entry.updatedAt,
    };
  }

  set<T>(key: string, value: T, ttlMs: number): CacheLookup<T> {
    const updatedAt = new Date();
    this.store.set(key, {
      value,
      expiresAt: Date.now() + ttlMs,
      updatedAt,
    });

    return {
      value,
      cached: false,
      updatedAt,
    };
  }

  clear(): void {
    this.store.clear();
  }
}

export const cache = new MemoryCache();

export async function cacheThrough<T>(
  key: string,
  ttlMs: number,
  loader: () => Promise<T>
): Promise<CacheLookup<T>> {
  const hit = cache.get<T>(key);
  if (hit) {
    return hit;
  }

  const value = await loader();
  return cache.set(key, value, ttlMs);
}

export const ttl = {
  fundEstimate: 30 * 1000,
  fundNavHistory: 24 * 60 * 60 * 1000,
  fundRankHistory: 24 * 60 * 60 * 1000,
  fundDividends: 7 * 24 * 60 * 60 * 1000,
  fundSearch: 24 * 60 * 60 * 1000,
  fundBasic: 24 * 60 * 60 * 1000,
  marketQuotes: 15 * 1000,
  marketKline: 60 * 60 * 1000,
  stockReference: 7 * 24 * 60 * 60 * 1000,
};
