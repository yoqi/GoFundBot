import type { StockReferenceDto } from '../../models/stock.js';
import { AppError } from '../../core/errors.js';
import type { StockProvider } from '../types.js';

const TENCENT_QT_URL = 'https://qt.gtimg.cn/';
const USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36';

export class TencentStockProvider implements StockProvider {
  readonly name = 'tencent';

  async reference(code: string): Promise<StockReferenceDto> {
    const qtCode = toQtCode(code);
    if (!qtCode) {
      throw new AppError('PROVIDER_UNAVAILABLE', `Tencent does not support stock code ${code}`, 502, { code });
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);

    try {
      const response = await fetch(`${TENCENT_QT_URL}?q=${qtCode}`, {
        headers: { 'User-Agent': USER_AGENT, Referer: 'https://gu.qq.com/' },
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new AppError('PROVIDER_UNAVAILABLE', `Tencent HTTP ${response.status}`, 502, {
          code,
          status: response.status,
        });
      }

      const buffer = await response.arrayBuffer();
      const text = new TextDecoder('gbk').decode(buffer);
      const parsed = parseQtText(text, qtCode);

      if (!parsed || !parsed.name) {
        throw new AppError('PROVIDER_UNAVAILABLE', 'Tencent stock reference returned empty data', 502, { code });
      }

      return {
        code: parsed.code || code,
        name: parsed.name,
        market: parsed.market,
        symbol: qtCode,
        industry: null,
        region: null,
        concepts: [],
      };
    } finally {
      clearTimeout(timer);
    }
  }
}

/**
 * Convert a 6-digit A-share code to Tencent's sh/sz/bj-prefixed format.
 * Returns null for HK (5-digit) or unrecognized codes.
 */
function toQtCode(code: string): string | null {
  const normalized = code.trim().replace(/^(sh|sz|bj|hk)/i, '').replace(/\.(SH|SZ|BJ|HK)$/i, '');
  if (/^\d{5}$/.test(normalized)) {
    // HK stocks — Tencent qt.gtimg.cn does not support them
    return null;
  }
  if (/^6|^9/.test(normalized)) {
    return `sh${normalized}`;
  }
  if (/^0|^2|^3/.test(normalized)) {
    return `sz${normalized}`;
  }
  if (/^4|^8/.test(normalized)) {
    return `bj${normalized}`;
  }
  return null;
}

function parseQtText(
  text: string,
  qtCode: string,
): { name: string; code: string; market: string } | null {
  // Tencent qt format: v_sh600519="1~贵州茅台~600519~..."
  const pattern = /v_(\w+)="([^"]*)"/g;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    const rawQtCode = match[1];
    const raw = match[2];
    const parts = raw.split('~');

    if (parts.length < 3) continue;

    const name = (parts[1] || '').trim();
    if (!name) continue;

    return {
      name,
      code: parts[2] || rawQtCode.replace(/^(sh|sz|bj)/i, ''),
      market: detectMarket(rawQtCode),
    };
  }

  return null;
}

function detectMarket(qtCode: string): string {
  const lower = qtCode.toLowerCase();
  if (lower.startsWith('sh')) return 'SH';
  if (lower.startsWith('sz')) return 'SZ';
  if (lower.startsWith('bj')) return 'BJ';
  return 'CN';
}
