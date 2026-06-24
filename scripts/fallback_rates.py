import json
from pathlib import Path


_RATES_PATH = Path(__file__).resolve().parents[1] / "config" / "fallback-rates.json"


def load_fallback_rates():
    with _RATES_PATH.open("r", encoding="utf-8") as handle:
        rates = json.load(handle)
    if rates.get("THB") != 1:
        raise ValueError("THB fallback rate must equal 1")
    if any(not isinstance(code, str) or len(code) != 3 for code in rates):
        raise ValueError("Fallback currency codes must be three-letter ISO codes")
    if any(not isinstance(rate, (int, float)) or rate <= 0 for rate in rates.values()):
        raise ValueError("Fallback rates must be positive numbers")
    return rates


FALLBACK_RATES = load_fallback_rates()
