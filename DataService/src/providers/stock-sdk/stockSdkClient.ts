import StockSDK from 'stock-sdk';

let sdk: StockSDK | null = null;

export function getStockSdk(): StockSDK {
  if (!sdk) {
    const timeout = Number(process.env.STOCK_SDK_TIMEOUT_MS ?? 10000);
    sdk = new StockSDK({
      timeout: Number.isFinite(timeout) ? timeout : 10000,
    });
  }

  return sdk;
}
