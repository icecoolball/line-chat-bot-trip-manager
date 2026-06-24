import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fallback_rates import FALLBACK_RATES  # noqa: E402


class FallbackRatesTest(unittest.TestCase):
    def test_python_loader_matches_shared_json(self):
        with (ROOT / "config" / "fallback-rates.json").open("r", encoding="utf-8") as handle:
            expected = json.load(handle)
        self.assertEqual(FALLBACK_RATES, expected)
        self.assertEqual(FALLBACK_RATES["THB"], 1)
        self.assertGreater(len(FALLBACK_RATES), 30)


if __name__ == "__main__":
    unittest.main()
