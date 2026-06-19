import type {
  FundAssetAllocationDto,
  FundDividendListDto,
  FundEstimateDto,
  FundBasicDto,
  FundHolderStructureDto,
  FundHoldingsDto,
  FundHoldingItemDto,
  FundManagerDto,
  FundManagersDto,
  FundNavHistoryDto,
  FundNavPointDto,
  FundPerformanceDto,
  FundPerformanceEvaluationDto,
  FundPositionTrendItemDto,
  FundRankHistoryDto,
  FundScaleFluctuationDto,
  FundSearchResultDto,
  FundSubscriptionRedemptionDto,
  FundTotalReturnTrendDto,
} from '../../types/fund.js';
import { AppError } from '../../core/errors.js';
import type { FundNavHistoryOptions, FundProvider } from '../types.js';
import { fetchText, parseJsonpObject, parseJsJson, parseJsString, extractJsAssignment } from './eastmoneyRequest.js';

interface EastMoneyNavPoint {
  x?: number;
  y?: number;
  equityReturn?: number;
  unitMoney?: string;
}

type EastMoneyAccNavPoint = [number, number];

export class EastMoneyFundProvider implements FundProvider {
  readonly name = 'eastmoney';

  async search(keyword: string): Promise<FundSearchResultDto> {
    const normalized = keyword.trim();
    if (!normalized) {
      return { items: [] };
    }

    const funds = await fetchFundCodeSearchList();
    const lower = normalized.toLowerCase();
    const padded = /^\d{1,6}$/.test(normalized) ? normalized.padStart(6, '0') : normalized;

    const items = funds
      .map((item) => {
        let score = 0;
        if (item.code.startsWith(normalized) || item.code.startsWith(padded)) {
          score = 100;
        } else if (item.name.includes(normalized)) {
          score = 80;
        } else if (item.shortName.toLowerCase().includes(lower)) {
          score = 60;
        } else if (item.pinyin.toLowerCase().includes(lower)) {
          score = 40;
        }
        return { ...item, score };
      })
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score || a.code.localeCompare(b.code))
      .slice(0, 20)
      .map((item) => ({
        code: item.code,
        name: item.name,
        type: item.type,
        pinyin: item.pinyin,
        source: this.name,
      }));

    return { items };
  }

  async estimate(code: string): Promise<FundEstimateDto> {
    const url = `https://fundgz.1234567.com.cn/js/${code}.js`;
    const json = parseJsonpObject(await fetchText(url));

    return {
      code: toStringValue(json.fundcode || code),
      name: toNullableString(json.name),
      navDate: toNullableString(json.jzrq),
      nav: toNullableNumber(json.dwjz),
      estimatedNav: toNullableNumber(json.gsz),
      estimatedChangePercent: toNullableNumber(json.gszzl),
      estimateTime: toNullableString(json.gztime),
    };
  }

  async navHistory(code: string, options: FundNavHistoryOptions = {}): Promise<FundNavHistoryDto> {
    const url = `https://fund.eastmoney.com/pingzhongdata/${code}.js`;
    const script = await fetchText(url);
    const name = parseJsString(script, 'fS_name');
    const parsedCode = parseJsString(script, 'fS_code') || code;
    const navTrend = parseJsJson<EastMoneyNavPoint[]>(script, 'Data_netWorthTrend');
    const accTrend = parseJsJson<EastMoneyAccNavPoint[]>(script, 'Data_ACWorthTrend');
    const accByTimestamp = new Map<number, number | null>(
      accTrend.map((item) => [Number(item[0]), toNullableNumber(item[1])])
    );

    const items = navTrend.map((item): FundNavPointDto => {
      const timestamp = toNullableNumber(item.x);
      return {
        date: timestamp ? new Date(timestamp).toISOString().slice(0, 10) : '',
        timestamp,
        nav: toNumberValue(item.y),
        accNav: timestamp == null ? null : accByTimestamp.get(timestamp) ?? null,
        dailyReturn: toNullableNumber(item.equityReturn),
        unitMoney: toStringValue(item.unitMoney),
      };
    });

    return filterNavHistory({
      code: parsedCode,
      name,
      items,
    }, options);
  }

  async basic(code: string): Promise<FundBasicDto> {
    const script = await this.fetchFundDetailScript(code);
    const fundListItem = (await fetchFundCodeSearchList()).find((item) => item.code === code);
    const managers = safeParseJsJson<Record<string, unknown>[]>(script, 'Data_currentFundManager', []);
    const managerNames = managers
      .map((manager) => toNullableString(manager.name ?? manager.NAME ?? manager.fname))
      .filter((name): name is string => Boolean(name));

    // Parse rates: var fund_sourceRate="1.00"; var fund_Rate="0.10";
    const originalRate = parseOptionalString(script, 'fund_sourceRate');
    const currentRate = parseOptionalString(script, 'fund_Rate');
    const minSubAmount = parseOptionalString(script, 'fund_minsg');
    const isHBStr = parseOptionalString(script, 'ishb');
    const isHB = isHBStr === 'true' ? true : isHBStr === 'false' ? false : null;

    return {
      code: parseJsString(script, 'fS_code') || code,
      name: parseJsString(script, 'fS_name') || fundListItem?.name || null,
      type: fundListItem?.type ?? null,
      company: firstString(
        safeParseJsString(script, 'jjglr'),
        safeParseJsString(script, 'fundCompany'),
        managerCompany(managers)
      ),
      manager: managerNames.length > 0 ? managerNames.join(',') : null,
      establishDate: firstString(safeParseJsString(script, 'foundDate'), safeParseJsString(script, 'clrq')),
      fundSize: firstString(
        safeParseJsString(script, 'fundScale'),
        latestScaleValue(safeParseJsJson<Record<string, unknown>>(script, 'Data_fluctuationScale', {}))
      ),
      riskLevel: firstString(safeParseJsString(script, 'riskLevel'), safeParseJsString(script, 'risklevel')),
      originalRate,
      currentRate,
      minSubscriptionAmount: minSubAmount,
      isHB: isHB,
    };
  }

  rankHistory(_code: string): Promise<FundRankHistoryDto> {
    return Promise.reject(new AppError('PROVIDER_UNAVAILABLE', 'EastMoney rankHistory is not implemented yet', 501));
  }

  dividends(_code: string): Promise<FundDividendListDto> {
    return Promise.reject(new AppError('PROVIDER_UNAVAILABLE', 'EastMoney dividends is not implemented yet', 501));
  }

  // -----------------------------------------------------------------------
  // New / improved parsers – match Backend fund_api.py FundDataCleaner
  // -----------------------------------------------------------------------

  async holdings(code: string): Promise<FundHoldingsDto> {
    const script = await this.fetchFundDetailScript(code);
    const reportDate = safeParseJsString(script, 'fundSharesPositionDate');

    // Parse stock codes from top-level variables:
    // var stockCodes = ["6005191","0008580",...];  — trailing digit is internal suffix
    // var stockCodesNew = ["1.600519","0.000858",...];  — 1.=SH, 0.=SZ
    const stockCodesRaw = parseJsArrayValue(script, 'stockCodes');
    const stockCodesNewRaw = parseJsArrayValue(script, 'stockCodesNew');

    // Build market map from stockCodesNew: "1.600519" → market=SH, cleanCode=600519
    const marketMap: Record<string, { market: string; cleanCode: string }> = {};
    for (const raw of stockCodesNewRaw) {
      const s = String(raw);
      const m = s.match(/^([01])\.(\d{5,6})/);
      if (m) {
        const mk = m[1] === '1' ? '上海' : '深圳';
        const cc = m[2];
        marketMap[cc] = { market: mk, cleanCode: cc };
      }
    }

    // Also build by original code: stockCodes[i] maps to stockCodesNew[i]
    const marketByOriginal: Record<string, { market: string; cleanCode: string }> = {};
    for (let i = 0; i < stockCodesRaw.length && i < stockCodesNewRaw.length; i++) {
      const orig = String(stockCodesRaw[i]);
      const s = String(stockCodesNewRaw[i]);
      const m = s.match(/^([01])\.(\d{5,6})/);
      if (m) {
        marketByOriginal[orig] = { market: m[1] === '1' ? '上海' : '深圳', cleanCode: m[2] };
      }
    }

    // Parse detail info: names, ratios, shares, market values
    const detail = safeParseJsJson<Record<string, unknown> | null>(script, 'Data_InverstPositionDetail', null);
    const detailMap: Record<string, Record<string, unknown>> = {};
    if (detail && typeof detail === 'object') {
      for (const [key, val] of Object.entries(detail)) {
        if (val && typeof val === 'object') {
          detailMap[key] = val as Record<string, unknown>;
        }
      }
    }

    const items: FundHoldingItemDto[] = stockCodesRaw.map((rawCode, idx) => {
      const originalCode = String(rawCode);
      const marketInfo = marketByOriginal[originalCode] || { market: null, cleanCode: normalizeStockCode(originalCode) };
      const detailInfo = detailMap[originalCode] || {};

      // Look up by clean code in marketMap
      let market = marketInfo.market;
      let cleanCode = marketInfo.cleanCode;
      if (!market && cleanCode) {
        const mm = marketMap[cleanCode];
        if (mm) { market = mm.market; cleanCode = mm.cleanCode; }
      }

      const name = toNullableString(detailInfo.name || detailInfo.stockName || detailInfo.stockGPName) || cleanCode;
      const ratio = toNullableNumber(detailInfo.zhanbijjzc || detailInfo.ratio || detailInfo.zjl);
      const shares = toNullableNumber(detailInfo.share || detailInfo.chicang);
      const marketValue = toNullableNumber(detailInfo.marketValue || detailInfo.marketvalue || detailInfo.shizhi);

      return {
        stockCode: cleanCode,
        stockName: name,
        market,
        symbol: market ? (market === '上海' ? `sh${cleanCode}` : `sz${cleanCode}`) : null,
        ratio: ratio !== null ? Number((ratio / 100).toFixed(4)) : (idx === 0 ? 0 : null),
        shares,
        marketValue,
      };
    });

    // Parse bond codes
    const bondCodesRaw = parseOptionalString(script, 'zqCodes') || '';
    const bondCodesNewRaw = parseOptionalString(script, 'zqCodesNew') || '';
    const bondCodes = bondCodesRaw ? bondCodesRaw.split(',').map((s) => s.trim()).filter(Boolean) : [];
    const bondCodesNew = bondCodesNewRaw ? bondCodesNewRaw.split(',').map((s) => s.trim()).filter(Boolean) : [];

    return { items, bondCodes, bondCodesNew, reportDate };
  }

  async managers(code: string): Promise<FundManagersDto> {
    const script = await this.fetchFundDetailScript(code);
    const rawManagers = safeParseJsJson<Record<string, unknown>[]>(script, 'Data_currentFundManager', []);

    const items: FundManagerDto[] = rawManagers.map((mgr) => ({
      id: toNullableString(mgr.id || mgr.ID || mgr.managerId),
      name: toNullableString(mgr.name || mgr.NAME || mgr.fname || mgr.managerName),
      photoUrl: toNullableString(mgr.pic || mgr.PIC || mgr.photo),
      starRating: toNullableNumber(mgr.star || mgr.STAR || mgr.starRating),
      workExperience: toNullableString(mgr.workTime || mgr.WORKTIME || mgr.workExperience),
      managedFundSize: toNullableString(mgr.fundSize || mgr.FUNDSIZE),
      startDate: toNullableString(mgr.startDate || mgr.accessionDate || mgr.RZRQ || mgr.rzrq),
      endDate: toNullableString(mgr.endDate || mgr.LEAVEDATE || mgr.lzrq),
      tenure: toNullableString(mgr.tenure || mgr.tenureDays || mgr.term),
      description: toNullableString(mgr.description || mgr.DESC || mgr.desc || mgr.profile),
      abilityAssessment: mgr.power ? (mgr.power as Record<string, unknown>) : null,
      performance: mgr.profit ? (mgr.profit as Record<string, unknown>) : null,
    }));

    return { items };
  }

  async assetAllocation(code: string): Promise<FundAssetAllocationDto> {
    const script = await this.fetchFundDetailScript(code);
    const alloc = safeParseJsJson<Record<string, unknown>>(script, 'Data_assetAllocation', {});

    const categories = Array.isArray(alloc.categories)
      ? (alloc.categories as string[])
      : [];
    const rawSeries = Array.isArray(alloc.series)
      ? (alloc.series as Record<string, unknown>[])
      : [];
    const series = rawSeries.map((s) => ({ ...s }));

    return { categories, series, date: null };
  }

  async performance(code: string): Promise<FundPerformanceDto> {
    const script = await this.fetchFundDetailScript(code);
    // Match Backend: syl_1n, syl_6y, syl_3y, syl_1y are top-level JS variables
    return {
      return1m: parseOptionalNumber(script, 'syl_1y'),
      return3m: parseOptionalNumber(script, 'syl_3y'),
      return6m: parseOptionalNumber(script, 'syl_6y'),
      return1y: parseOptionalNumber(script, 'syl_1n'),
      date: null,
    };
  }

  async performanceEvaluation(code: string): Promise<FundPerformanceEvaluationDto> {
    const script = await this.fetchFundDetailScript(code);
    const pe = safeParseJsJson<Record<string, unknown>>(script, 'Data_performanceEvaluation', {});
    return {
      avr: toNullableString(pe.avr ?? pe.AVR ?? null),
      categories: Array.isArray(pe.categories) ? (pe.categories as string[]) : [],
      data: Array.isArray(pe.data) ? (pe.data as number[]) : [],
      dsc: Array.isArray(pe.dsc) ? (pe.dsc as string[]) : [],
    };
  }

  async subscriptionRedemption(code: string): Promise<FundSubscriptionRedemptionDto> {
    const script = await this.fetchFundDetailScript(code);
    const bd = safeParseJsJson<Record<string, unknown>>(script, 'Data_buySedemption', {});

    const categories = Array.isArray(bd.categories)
      ? (bd.categories as string[])
      : [];
    const rawSeries = Array.isArray(bd.series)
      ? (bd.series as Record<string, unknown>[])
      : [];
    const series = rawSeries.map((s) => ({ ...s }));

    return { categories, series };
  }

  async holderStructure(code: string): Promise<FundHolderStructureDto> {
    const script = await this.fetchFundDetailScript(code);
    const hs = safeParseJsJson<Record<string, unknown>>(script, 'Data_holderStructure', {});

    const categories = Array.isArray(hs.categories)
      ? (hs.categories as string[])
      : [];
    const rawSeries = Array.isArray(hs.series)
      ? (hs.series as Record<string, unknown>[])
      : [];
    const series = rawSeries.map((s) => ({ ...s }));

    return { categories, series };
  }

  async sameTypeFunds(code: string): Promise<unknown[][]> {
    const script = await this.fetchFundDetailScript(code);
    // Use regex-based extraction instead of extractJsAssignment
    const raw = parseJsValueByRegex<unknown[][]>(script, 'swithSameType', []);
    if (!Array.isArray(raw)) return [];

    return raw.map((category: unknown) => {
      if (!Array.isArray(category)) return [];
      return category.map((fundStr: unknown) => {
        if (typeof fundStr !== 'string') return { code: '', name: '', returnRate: null };
        const parts = fundStr.split('_');
        return {
          code: parts[0] || '',
          name: parts[1] || '',
          returnRate: parts.length >= 3 ? toNullableNumber(parts[2]) : null,
        };
      });
    });
  }

  async scaleFluctuation(code: string): Promise<FundScaleFluctuationDto> {
    const script = await this.fetchFundDetailScript(code);
    const sf = safeParseJsJson<Record<string, unknown>>(script, 'Data_fluctuationScale', {});

    const categories = Array.isArray(sf.categories)
      ? (sf.categories as string[])
      : [];
    const rawSeries = Array.isArray(sf.series)
      ? (sf.series as Record<string, unknown>[])
      : [];
    const series = rawSeries.map((s) => ({ ...s }));

    return { categories, series };
  }

  async positionTrend(code: string): Promise<FundPositionTrendItemDto[]> {
    const script = await this.fetchFundDetailScript(code);
    // Data_fundSharesPositions = [[timestamp, percentage], [timestamp, percentage], ...]
    const raw = safeParseJsJson<unknown[]>(script, 'Data_fundSharesPositions', []);
    const items: FundPositionTrendItemDto[] = [];
    for (const entry of raw) {
      if (!Array.isArray(entry) || entry.length < 2) continue;
      const ts = Number(entry[0]);
      const pct = Number(entry[1]);
      items.push({
        date: ts ? new Date(ts).toISOString().slice(0, 10) : '',
        positionPercentage: Number.isFinite(pct) ? pct : null,
      });
    }
    return items;
  }

  async totalReturnTrend(code: string): Promise<FundTotalReturnTrendDto> {
    const script = await this.fetchFundDetailScript(code);
    // Data_grandTotal is a top-level array of {name, data: [[ts, val], ...]}
    const raw = safeParseJsJson<Record<string, unknown>[]>(script, 'Data_grandTotal', []);
    const series = (Array.isArray(raw) ? raw : []).map((s) => ({ ...s }));
    return { series };
  }

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------

  private async fetchFundDetailScript(code: string): Promise<string> {
    return fetchText(`https://fund.eastmoney.com/pingzhongdata/${code}.js`);
  }
}

// ---------------------------------------------------------------------------
// Module-level helpers (reused from original)
// ---------------------------------------------------------------------------

interface FundCodeSearchItem {
  code: string;
  shortName: string;
  name: string;
  type: string;
  pinyin: string;
}

let fundCodeSearchCache: { items: FundCodeSearchItem[]; expiresAt: number } | null = null;

async function fetchFundCodeSearchList(): Promise<FundCodeSearchItem[]> {
  if (fundCodeSearchCache && Date.now() < fundCodeSearchCache.expiresAt) {
    return fundCodeSearchCache.items;
  }

  const text = await fetchText('https://fund.eastmoney.com/js/fundcode_search.js');
  const match = text.match(/var\s+r\s*=\s*(\[[\s\S]*?\]);/);
  if (!match) {
    throw new Error('Unable to parse EastMoney fund search list');
  }

  const raw = JSON.parse(match[1]) as unknown[];
  const items = raw
    .filter(Array.isArray)
    .map((item) => item as unknown[])
    .map((item) => ({
      code: toStringValue(item.at(0)),
      shortName: toStringValue(item.at(1)),
      name: toStringValue(item.at(2)),
      type: toStringValue(item.at(3)),
      pinyin: toStringValue(item.at(4)),
    }))
    .filter((item) => item.code && item.name);

  fundCodeSearchCache = {
    items,
    expiresAt: Date.now() + 24 * 60 * 60 * 1000,
  };
  return items;
}

function safeParseJsJson<T>(script: string, variableName: string, fallback: T): T {
  try {
    return parseJsJson<T>(script, variableName);
  } catch {
    return fallback;
  }
}

function safeParseJsString(script: string, variableName: string): string | null {
  try {
    return parseJsString(script, variableName);
  } catch {
    return null;
  }
}

function parseOptionalNumber(script: string, variableName: string): number | null {
  // Use regex to directly extract the value – more robust than extractJsAssignment
  const regex = new RegExp(`var\\s+${escapeRegExp(variableName)}\\s*=\\s*([^;]*);`);
  const match = script.match(regex);
  if (!match) return null;

  let raw = match[1].trim();
  // Unquote if quoted
  if ((raw.startsWith('"') && raw.endsWith('"')) || (raw.startsWith("'") && raw.endsWith("'"))) {
    raw = raw.slice(1, -1);
  }
  const num = Number(raw);
  return Number.isFinite(num) ? num : null;
}

function parseOptionalString(script: string, variableName: string): string | null {
  // Use regex to extract string values like var name = "value";
  const regex = new RegExp(`var\\s+${escapeRegExp(variableName)}\\s*=\\s*([^;]*);`);
  const match = script.match(regex);
  if (!match) return null;

  let raw = match[1].trim();
  // Unquote if quoted
  if ((raw.startsWith('"') && raw.endsWith('"')) || (raw.startsWith("'") && raw.endsWith("'"))) {
    raw = raw.slice(1, -1);
  }
  return raw || null;
}

function escapeRegExp(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function parseJsValueByRegex<T>(script: string, variableName: string, fallback: T): T {
  // Match var NAME = VALUE; — VALUE may contain newlines (large arrays).
  const regex = new RegExp(`var\\s+${escapeRegExp(variableName)}\\s*=\\s*([\\s\\S]*?);`);
  const match = script.match(regex);
  if (!match) return fallback;
  try {
    return JSON.parse(match[1].trim()) as T;
  } catch {
    // EastMoney JS sometimes uses single quotes; JSON.parse needs double quotes.
    try {
      const withDoubleQuotes = match[1].trim().replace(/'/g, '"');
      return JSON.parse(withDoubleQuotes) as T;
    } catch {
      return fallback;
    }
  }
}

function parseJsArrayValue(script: string, variableName: string): string[] {
  const raw = parseJsValueByRegex<unknown[]>(script, variableName, []);
  if (!Array.isArray(raw)) return [];
  return raw.map((item: unknown) => String(item));
}

function normalizeStockCode(rawCode: string): string {
  if (rawCode.endsWith('116') && rawCode.length > 3) {
    return rawCode.slice(0, -3).padStart(5, '0');
  }
  if (rawCode.length > 1) {
    return rawCode.slice(0, -1).padStart(6, '0');
  }
  return rawCode.padStart(6, '0');
}


function firstString(...values: (string | null | undefined)[]): string | null {
  return values.find((value): value is string => Boolean(value)) ?? null;
}

function managerCompany(managers: Record<string, unknown>[]): string | null {
  for (const manager of managers) {
    const company = toNullableString(manager.company ?? manager.jjgs ?? manager.COMPANY);
    if (company) {
      return company;
    }
  }
  return null;
}

function latestScaleValue(scale: Record<string, unknown>): string | null {
  const series = scale.series;
  if (!Array.isArray(series)) {
    return null;
  }

  const first = series[0];
  if (!first || typeof first !== 'object') {
    return null;
  }

  const data = (first as Record<string, unknown>).data;
  if (!Array.isArray(data) || data.length === 0) {
    return null;
  }

  const last = data.at(-1);
  if (!last || typeof last !== 'object') {
    return null;
  }

  return toNullableString((last as Record<string, unknown>).y);
}

function filterNavHistory(data: FundNavHistoryDto, range: FundNavHistoryOptions): FundNavHistoryDto {
  const start = range.startDate ? normalizeDate(range.startDate) : null;
  const end = range.endDate ? normalizeDate(range.endDate) : null;

  if (!start && !end) {
    return data;
  }

  return {
    ...data,
    items: data.items.filter((item) => {
      const date = normalizeDate(item.date);
      return (!start || date >= start) && (!end || date <= end);
    }),
  };
}

function normalizeDate(value: string): string {
  return value.replaceAll('-', '');
}

function toStringValue(value: unknown): string {
  return value == null ? '' : String(value);
}

function toNullableString(value: unknown): string | null {
  return value == null || value === '' ? null : String(value);
}

function toNumberValue(value: unknown): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
}

function toNullableNumber(value: unknown): number | null {
  if (value == null || value === '') {
    return null;
  }
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}
