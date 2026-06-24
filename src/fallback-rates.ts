import rates from "../config/fallback-rates.json";

// Shared with scripts/fallback_rates.py through config/fallback-rates.json.
// Live/cache rates remain authoritative; these values are the last safety net.
export const FALLBACK_RATES: Readonly<Record<string, number>> = Object.freeze({ ...rates });
