/**
 * AkShare Market Provider — PLACEHOLDER / TODO
 *
 * AkShare is a Python library.  Node.js cannot directly import it.
 * This provider is a STUB that returns structured "not implemented" errors
 * so that Backend does NOT need to keep akShare fallback logic long-term.
 *
 * Current status:
 *   - All methods return Promise.reject with PROVIDER_UNAVAILABLE.
 *   - Backend still falls back to legacy MarketDataService → akShare for now.
 *   - This file documents that the migration is INCOMPLETE.
 *
 * Future options:
 *   a) Run akShare in a sidecar Python process and call it via HTTP.
 *   b) Replace akShare with a pure-TypeScript equivalent (e.g. EastMoney
 *      already covers most of the sector / index data).
 *   c) Remove the akShare fallback entirely once EastMoney is stable.
 */

import { AppError } from '../../core/errors.js';
import type {
  ConstituentListDto,
  IndexListDto,
  KlineDto,
  KlineOptions,
  MarketProvider,
  MarketQuoteDto,
  SectorListDto,
} from '../types.js';

export class AkShareMarketProvider implements MarketProvider {
  readonly name = 'akshare';

  quotes(_symbols: string[]): Promise<MarketQuoteDto[]> {
    return Promise.reject(
      new AppError(
        'PROVIDER_UNAVAILABLE',
        'akshare provider is a TODO placeholder – Node.js cannot call Python akShare directly',
        501
      )
    );
  }

  kline(_symbol: string, _options: KlineOptions): Promise<KlineDto[]> {
    return Promise.reject(
      new AppError(
        'PROVIDER_UNAVAILABLE',
        'akshare provider is a TODO placeholder – Node.js cannot call Python akShare directly',
        501
      )
    );
  }

  async sectors(): Promise<SectorListDto> {
    throw new AppError(
      'PROVIDER_UNAVAILABLE',
      'akshare provider is a TODO placeholder – Backend still falls back to legacy akshare path for sectors',
      501
    );
  }

  async sectorConstituents(_code: string): Promise<ConstituentListDto> {
    throw new AppError(
      'PROVIDER_UNAVAILABLE',
      'akshare provider is a TODO placeholder',
      501
    );
  }

  async indices(): Promise<IndexListDto> {
    throw new AppError(
      'PROVIDER_UNAVAILABLE',
      'akshare provider is a TODO placeholder',
      501
    );
  }
}
