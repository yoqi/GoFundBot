import { AppError } from '../../core/errors.js';

const DEFAULT_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
  Referer: 'https://fund.eastmoney.com/',
  Accept: '*/*',
};

export async function fetchText(url: string, timeoutMs = 10000): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      headers: DEFAULT_HEADERS,
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new AppError('PROVIDER_UNAVAILABLE', `EastMoney HTTP ${response.status}`, 502, {
        url,
        status: response.status,
      });
    }

    return await response.text();
  } finally {
    clearTimeout(timer);
  }
}

export async function fetchJson(url: string, timeoutMs = 10000): Promise<Record<string, unknown>> {
  const text = await fetchText(url, timeoutMs);
  return JSON.parse(text) as Record<string, unknown>;
}

export function parseJsonpObject(text: string): Record<string, unknown> {
  const match = text.match(/^[^(]*\((.*)\)\s*;?\s*$/s);
  if (!match) {
    throw new Error('Invalid JSONP payload');
  }
  return JSON.parse(match[1]) as Record<string, unknown>;
}

export function extractJsAssignment(script: string, variableName: string): string {
  const marker = `var ${variableName} =`;
  const start = script.indexOf(marker);
  if (start < 0) {
    throw new Error(`Missing EastMoney variable ${variableName}`);
  }

  const valueStart = start + marker.length;
  const end = script.indexOf(';', valueStart);
  if (end < 0) {
    throw new Error(`Missing semicolon for EastMoney variable ${variableName}`);
  }

  return script.slice(valueStart, end).trim();
}

export function parseJsJson<T>(script: string, variableName: string): T {
  return JSON.parse(extractJsAssignment(script, variableName)) as T;
}

export function parseJsString(script: string, variableName: string): string | null {
  const raw = extractJsAssignment(script, variableName);
  try {
    return JSON.parse(raw) as string;
  } catch {
    return raw.replace(/^['"]|['"]$/g, '') || null;
  }
}
