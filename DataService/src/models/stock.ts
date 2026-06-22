export interface StockReferenceDto {
  code: string;
  name: string;
  market: string;
  symbol: string;
  industry?: string | null;
  region?: string | null;
  concepts?: string[];
}
