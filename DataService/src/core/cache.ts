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
  private readonly maxEntries: number;
  private cleanupTimer: ReturnType<typeof setInterval> | null = null;

  constructor(maxEntries: number = 2000) {
    this.maxEntries = maxEntries;
    this.startCleanup();
  }

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

    this.evictIfNeeded();

    return {
      value,
      cached: false,
      updatedAt,
    };
  }

  clear(): void {
    this.store.clear();
  }

  get size(): number {
    return this.store.size;
  }

  /**
   * Evict entries when the store exceeds maxEntries.
   * Priority: expired entries first, then oldest by updatedAt.
   */
  private evictIfNeeded(): void {
    if (this.store.size <= this.maxEntries) return;

    const now = Date.now();
    const entries = Array.from(this.store.entries());

    // Sort: expired first, then oldest by updatedAt
    entries.sort((a, b) => {
      const aExpired = now >= a[1].expiresAt ? 0 : 1;
      const bExpired = now >= b[1].expiresAt ? 0 : 1;
      if (aExpired !== bExpired) return aExpired - bExpired;
      return a[1].updatedAt.getTime() - b[1].updatedAt.getTime();
    });

    const excess = this.store.size - this.maxEntries;
    const toRemove = entries.slice(0, excess);
    for (const [key] of toRemove) {
      this.store.delete(key);
    }

    const expiredCount = toRemove.filter(([, e]) => now >= e.expiresAt).length;
    console.warn(
      `[MemoryCache] Evicted ${toRemove.length} entries ` +
      `(${expiredCount} expired, ${toRemove.length - expiredCount} oldest). ` +
      `Size: ${this.store.size}/${this.maxEntries}`
    );
  }

  /**
   * Periodically sweep expired entries so stale data doesn't
   * linger in memory until the next access.
   */
  private startCleanup(): void {
    this.cleanupTimer = setInterval(() => {
      const now = Date.now();
      let removed = 0;
      for (const [key, entry] of this.store) {
        if (now >= entry.expiresAt) {
          this.store.delete(key);
          removed++;
        }
      }
      if (removed > 0) {
        console.log(
          `[MemoryCache] Periodic cleanup: removed ${removed} expired entries ` +
          `(remaining: ${this.store.size})`
        );
      }
    }, 5 * 60 * 1000); // every 5 minutes
  }
}

const maxEntries = Number(process.env.CACHE_MAX_ENTRIES ?? 2000);
export const cache = new MemoryCache(maxEntries);

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
  fundScreeningSnapshot: 30 * 60 * 1000,
  marketQuotes: 15 * 1000,
  marketKline: 60 * 60 * 1000,
  stockReference: 7 * 24 * 60 * 60 * 1000,
};
