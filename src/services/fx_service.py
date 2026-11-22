"""Live Foreign Exchange (FX) Rate Service for AI-Invoice Auditor.

Integrates Taiwan official open exchange data (Taifex / Central Bank of Taiwan)
and open financial currency feeds with caching and offline fallback.
"""

import json
import logging
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Fallback realistic rates (NTD base) if offline
DEFAULT_FALLBACK_RATES: Dict[str, float] = {
    "NT$": 1.0,
    "TWD": 1.0,
    "USD": 31.75,
    "EUR": 36.75,
    "RMB": 4.72,
    "CNY": 4.72,
    "JPY": 0.205,
    "THB": 0.965,
    "HKD": 4.05,
    "GBP": 42.85,
    "AUD": 22.65,
    "SGD": 24.80,
}

CURRENCY_DISPLAY_NAMES: Dict[str, str] = {
    "USD": "美金 (USD)",
    "EUR": "歐元 (EUR)",
    "RMB": "人民幣 (RMB)",
    "JPY": "日圓 (JPY)",
    "THB": "泰銖 (THB)",
    "HKD": "港幣 (HKD)",
    "GBP": "英鎊 (GBP)",
    "AUD": "澳幣 (AUD)",
    "SGD": "新加坡幣 (SGD)",
    "NT$": "新台幣 (NTD)",
}

CACHE_DIR = Path("data/cache")


class FXService:
    """Manages real-time exchange rate retrieval, caching, and conversions."""

    def __init__(self, cache_ttl_seconds: int = 14400):  # 4 hours TTL
        self.cache_ttl = cache_ttl_seconds
        self.cache_file = CACHE_DIR / "fx_rates_cache.json"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._memory_cache: Optional[Dict] = None

    def get_live_rates(self, force_refresh: bool = False) -> Dict:
        """Fetch live exchange rates from Taifex/Open financial feeds or cache.

        Returns structured dict with:
          - rates: Dict[str, float] (Currency -> NTD sell rate)
          - details: List[Dict] (currency, name, rate)
          - source: str
          - updated_at: str
          - is_live: bool
        """
        if not force_refresh:
            cached = self._read_cache()
            if cached:
                return cached

        # Try Live Sources
        rates_dict = DEFAULT_FALLBACK_RATES.copy()
        is_live = False
        source_label = "台灣期交所與央行官方牌告 (Taifex / CBC Open Data)"

        # 1. Fetch Official Taifex Rates (USD, RMB, EUR, JPY, HKD, GBP, AUD)
        try:
            taifex_rates = self._fetch_taifex()
            if taifex_rates:
                rates_dict.update(taifex_rates)
                is_live = True
        except Exception as e:
            logger.warning("Taifex live fetch failed: %s", e)

        # 2. Fetch supplementary rates (THB, SGD, etc.)
        try:
            supp_rates = self._fetch_open_exchange()
            if supp_rates:
                for curr, rate in supp_rates.items():
                    # Only override or supply if not already high accuracy
                    if curr in ("THB", "SGD") or not is_live:
                        rates_dict[curr] = rate
                is_live = True
        except Exception as e:
            logger.warning("Supplementary FX fetch failed: %s", e)

        # Build details list
        details_list = []
        ordered_keys = ["USD", "EUR", "RMB", "JPY", "THB", "HKD", "GBP", "AUD", "SGD"]
        for curr in ordered_keys:
            rate_val = rates_dict.get(curr, DEFAULT_FALLBACK_RATES.get(curr, 1.0))
            details_list.append({
                "currency": curr,
                "name": CURRENCY_DISPLAY_NAMES.get(curr, curr),
                "rate": round(rate_val, 4),
            })

        result = {
            "rates": {k: round(v, 4) for k, v in rates_dict.items()},
            "details": details_list,
            "source": source_label if is_live else "離線基準牌告匯率 (Offline Fallback)",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_live": is_live,
        }

        self._save_cache(result)
        return result

    def convert_to_ntd(self, amount: float, currency: str) -> Tuple[float, float]:
        """Convert foreign currency amount to NTD based on live rates.

        Returns (converted_ntd_amount, applied_rate).
        """
        curr = currency.strip().upper() if currency else "NT$"
        if curr in ("NT$", "TWD", "NTD", "NT", "台幣", "新台幣"):
            return round(amount, 2), 1.0

        if curr in ("CNY", "人民幣", "元"):
            curr = "RMB"
        elif curr in ("EURO", "歐元"):
            curr = "EUR"
        elif curr in ("泰銖", "BAHT"):
            curr = "THB"
        elif curr in ("美金", "美元"):
            curr = "USD"
        elif curr in ("日圓", "日幣"):
            curr = "JPY"
        elif curr in ("港幣",):
            curr = "HKD"

        rates_data = self.get_live_rates()
        rate = rates_data["rates"].get(curr, DEFAULT_FALLBACK_RATES.get(curr, 1.0))
        converted = round(amount * rate, 2)
        return converted, rate

    def _fetch_taifex(self) -> Dict[str, float]:
        """Fetch official daily exchange rates from Taiwan Futures Exchange OpenAPI."""
        url = "https://openapi.taifex.com.tw/v1/DailyForeignExchangeRates"
        req = urllib.request.Request(
            url, headers={"User-Agent": "AI-Invoice-Auditor/1.2"}
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))

        if not data or not isinstance(data, list):
            raise ValueError("Empty data from Taifex")

        latest = data[-1]
        rates = {}

        usd_ntd = float(latest.get("USD/NTD", 0))
        if usd_ntd > 0:
            rates["USD"] = usd_ntd

            # RMB/NTD
            if "RMB/NTD" in latest and float(latest["RMB/NTD"]) > 0:
                rates["RMB"] = float(latest["RMB/NTD"])
                rates["CNY"] = rates["RMB"]

            # EUR/NTD = (USD/NTD) * (EUR/USD)
            if "EUR/USD" in latest and float(latest["EUR/USD"]) > 0:
                rates["EUR"] = usd_ntd * float(latest["EUR/USD"])

            # JPY/NTD = (USD/NTD) / (USD/JPY)
            if "USD/JPY" in latest and float(latest["USD/JPY"]) > 0:
                rates["JPY"] = usd_ntd / float(latest["USD/JPY"])

            # HKD/NTD = (USD/NTD) / (USD/HKD)
            if "USD/HKD" in latest and float(latest["USD/HKD"]) > 0:
                rates["HKD"] = usd_ntd / float(latest["USD/HKD"])

            # GBP/NTD = (USD/NTD) * (GBP/USD)
            if "GBP/USD" in latest and float(latest["GBP/USD"]) > 0:
                rates["GBP"] = usd_ntd * float(latest["GBP/USD"])

            # AUD/NTD = (USD/NTD) * (AUD/USD)
            if "AUD/USD" in latest and float(latest["AUD/USD"]) > 0:
                rates["AUD"] = usd_ntd * float(latest["AUD/USD"])

        return rates

    def _fetch_open_exchange(self) -> Dict[str, float]:
        """Fetch supplementary rates (e.g. THB, SGD) via Open Exchange API."""
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(
            url, headers={"User-Agent": "AI-Invoice-Auditor/1.2"}
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))

        rates_table = data.get("rates", {})
        twd_rate = rates_table.get("TWD", 0)
        if not twd_rate or twd_rate <= 0:
            return {}

        results = {}
        for code in ("THB", "SGD", "EUR", "JPY", "CNY", "HKD", "GBP", "AUD"):
            if code in rates_table and rates_table[code] > 0:
                # 1 foreign unit in USD = 1 / rates_table[code]
                # in NTD = twd_rate / rates_table[code]
                rate_in_ntd = twd_rate / rates_table[code]
                results[code] = rate_in_ntd
                if code == "CNY":
                    results["RMB"] = rate_in_ntd

        return results

    def _read_cache(self) -> Optional[Dict]:
        if self._memory_cache:
            cache_time = datetime.fromisoformat(self._memory_cache.get("cached_at", "2000-01-01"))
            if (datetime.now() - cache_time).total_seconds() < self.cache_ttl:
                return self._memory_cache

        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cache_time = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
                if (datetime.now() - cache_time).total_seconds() < self.cache_ttl:
                    self._memory_cache = data
                    return data
            except Exception:
                pass
        return None

    def _save_cache(self, data: Dict):
        data["cached_at"] = datetime.now().isoformat()
        self._memory_cache = data
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Could not write fx cache: %s", e)


# Global singleton instance
fx_service = FXService()
