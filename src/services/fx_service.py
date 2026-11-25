"""Live and Historical Foreign Exchange (FX) Rate Service for AI-Invoice Auditor.

Integrates Taiwan official exchange feeds:
1. Bank of Taiwan Historical FX via FinMind OpenAPI (spot sell / cash sell fallback)
2. Taiwan Futures Exchange (Taifex) OpenData CSV
3. Open Exchange Rates API (supplementary currencies) + offline fallback
"""

import csv
import io
import json
import logging
import urllib.request
from datetime import datetime, timedelta
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
    "MYR": 7.65,
    "VND": 0.00135,
    "IDR": 0.00205,
    "PHP": 0.56,
    "KRW": 0.0235,
    "CAD": 22.90,
    "CHF": 36.10,
    "NZD": 19.20,
}

CURRENCY_DISPLAY_NAMES: Dict[str, str] = {
    "USD": "美金 (USD)",
    "EUR": "歐元 (EUR)",
    "RMB": "人民幣 (RMB/CNY)",
    "CNY": "人民幣 (CNY)",
    "JPY": "日圓 (JPY)",
    "THB": "泰銖 (THB)",
    "HKD": "港幣 (HKD)",
    "GBP": "英鎊 (GBP)",
    "AUD": "澳幣 (AUD)",
    "SGD": "新加坡幣 (SGD)",
    "MYR": "馬來西亞令吉 (MYR)",
    "VND": "越南盾 (VND)",
    "IDR": "印尼盾 (IDR)",
    "PHP": "菲律賓披索 (PHP)",
    "KRW": "韓元 (KRW)",
    "CAD": "加幣 (CAD)",
    "CHF": "瑞士法郎 (CHF)",
    "NZD": "紐幣 (NZD)",
    "NT$": "新台幣 (NTD)",
}

_CURRENCY_ALIASES: Dict[str, str] = {
    "NT$": "TWD",
    "TWD": "TWD",
    "NTD": "TWD",
    "NT": "TWD",
    "台幣": "TWD",
    "新台幣": "TWD",
    "RMB": "CNY",
    "CNY": "CNY",
    "人民幣": "CNY",
    "元": "CNY",
    "USD": "USD",
    "US$": "USD",
    "美金": "USD",
    "美元": "USD",
    "EUR": "EUR",
    "EURO": "EUR",
    "歐元": "EUR",
    "JPY": "JPY",
    "日圓": "JPY",
    "日幣": "JPY",
    "THB": "THB",
    "泰銖": "THB",
    "BAHT": "THB",
    "HKD": "HKD",
    "HK$": "HKD",
    "港幣": "HKD",
    "GBP": "GBP",
    "英鎊": "GBP",
    "AUD": "AUD",
    "澳幣": "AUD",
    "SGD": "SGD",
    "新幣": "SGD",
    "新加坡幣": "SGD",
    "MYR": "MYR",
    "馬幣": "MYR",
    "令吉": "MYR",
    "VND": "VND",
    "越南盾": "VND",
    "IDR": "IDR",
    "印尼盾": "IDR",
    "PHP": "PHP",
    "菲律賓披索": "PHP",
    "披索": "PHP",
    "KRW": "KRW",
    "韓元": "KRW",
    "韓圜": "KRW",
    "CAD": "CAD",
    "加幣": "CAD",
    "CHF": "CHF",
    "瑞郎": "CHF",
    "NZD": "NZD",
    "紐幣": "NZD",
}

CACHE_DIR = Path("data/cache")


def normalize_fx_code(currency: Optional[str]) -> str:
    """Normalize display/alias currency code into canonical ISO 4217 code."""
    if not currency:
        return "TWD"
    raw = str(currency).strip().upper()
    return _CURRENCY_ALIASES.get(raw, raw)


def _normalize_date_str(target_date: Optional[str]) -> str:
    """Normalize invoice/transaction date string into YYYY-MM-DD (defaults to today)."""
    if target_date:
        cleaned = str(target_date).strip().replace("/", "-")[:10]
        try:
            datetime.strptime(cleaned, "%Y-%m-%d")
            return cleaned
        except ValueError:
            pass
    return datetime.now().strftime("%Y-%m-%d")


class FXService:
    """Manages real-time and historical exchange rate retrieval, caching, and conversions."""

    def __init__(self, cache_ttl_seconds: int = 14400):  # 4 hours TTL
        self.cache_ttl = cache_ttl_seconds
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.cache_file = CACHE_DIR / "fx_rates_cache.json"
        self.historical_cache_file = CACHE_DIR / "fx_historical_cache.json"
        self._memory_cache: Optional[Dict] = None
        self._historical_cache: Dict[str, Dict] = self._load_historical_cache()

    def get_live_rates(self, force_refresh: bool = False) -> Dict:
        """Fetch live exchange rates from Taifex/Open financial feeds or cache."""
        if not force_refresh:
            cached = self._read_cache()
            if cached:
                return cached

        rates_dict = DEFAULT_FALLBACK_RATES.copy()
        is_live = False
        source_label = "台灣期交所與央行官方牌告 (Taifex / CBC Open Data)"

        # 1. Fetch Official Taifex Rates
        try:
            taifex_rates = self._fetch_taifex()
            if taifex_rates:
                rates_dict.update(taifex_rates)
                is_live = True
        except Exception as e:
            logger.warning("Taifex live fetch failed: %s", e)

        # 2. Fetch supplementary rates (THB, SGD, SEA currencies, etc.)
        try:
            supp_rates = self._fetch_open_exchange()
            if supp_rates:
                for curr, rate in supp_rates.items():
                    if curr in ("THB", "SGD", "MYR", "VND", "IDR", "PHP", "KRW", "CAD", "CHF", "NZD") or not is_live:
                        rates_dict[curr] = rate
                is_live = True
        except Exception as e:
            logger.warning("Supplementary FX fetch failed: %s", e)

        details_list = []
        ordered_keys = ["USD", "EUR", "RMB", "JPY", "THB", "HKD", "GBP", "AUD", "SGD", "MYR", "VND", "IDR", "PHP", "KRW"]
        for curr in ordered_keys:
            rate_val = rates_dict.get(curr, DEFAULT_FALLBACK_RATES.get(curr, 1.0))
            details_list.append({
                "currency": curr,
                "name": CURRENCY_DISPLAY_NAMES.get(curr, curr),
                "rate": round(rate_val, 5),
            })

        result = {
            "rates": {k: round(v, 5) for k, v in rates_dict.items()},
            "details": details_list,
            "source": source_label if is_live else "離線基準牌告匯率 (Offline Fallback)",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_live": is_live,
        }

        self._save_cache(result)
        return result

    def get_rate_for_date(
        self,
        currency: Optional[str],
        target_date: Optional[str] = None,
        rate_type: str = "spot_sell",
    ) -> Dict:
        """Look up Bank of Taiwan historical exchange rate on or before target_date.

        Automatically handles weekends/holidays by falling back to the nearest prior
        business day and falls back to cash_sell for SEA currencies without spot quotes.
        """
        iso_curr = normalize_fx_code(currency)
        req_date = _normalize_date_str(target_date)
        normalized_type = "cash_sell" if rate_type == "cash_sell" else "spot_sell"

        if iso_curr == "TWD":
            return {
                "currency": "NT$",
                "iso_currency": "TWD",
                "requested_date": req_date,
                "rate_date": req_date,
                "rate": 1.0,
                "rate_type": "base",
                "source": "新台幣本位幣",
                "is_live": True,
            }

        cache_key = f"{iso_curr}:{req_date}:{normalized_type}"
        cached = self._historical_cache.get(cache_key)
        if cached:
            # Historical dates older than 2 days stay immutable; recent dates expire after cache_ttl
            try:
                is_old_date = (datetime.now().date() - datetime.strptime(req_date, "%Y-%m-%d").date()).days > 2
                cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
                if is_old_date or (datetime.now() - cached_at).total_seconds() < self.cache_ttl:
                    return cached
            except Exception:
                pass

        # 1. Primary: Bank of Taiwan historical dataset via FinMind OpenAPI
        try:
            bot_hit = self._fetch_bot_finmind_for_date(iso_curr, req_date, rate_type=normalized_type)
            if bot_hit and bot_hit.get("rate", 0) > 0:
                bot_hit.update({
                    "currency": currency or iso_curr,
                    "iso_currency": iso_curr,
                    "requested_date": req_date,
                })
                self._save_historical_entry(cache_key, bot_hit)
                return bot_hit
        except Exception as e:
            logger.warning("BOT FinMind lookup failed (%s %s): %s", iso_curr, req_date, e)

        # 2. Secondary: Taifex historical CSV row <= req_date
        try:
            taifex_hit = self._lookup_taifex_for_date(iso_curr, req_date)
            if taifex_hit and taifex_hit.get("rate", 0) > 0:
                taifex_hit.update({
                    "currency": currency or iso_curr,
                    "iso_currency": iso_curr,
                    "requested_date": req_date,
                })
                self._save_historical_entry(cache_key, taifex_hit)
                return taifex_hit
        except Exception as e:
            logger.warning("Taifex historical lookup failed (%s %s): %s", iso_curr, req_date, e)

        # 3. Fallback: Live / Offline baseline table
        live = self.get_live_rates()
        lookup_key = "RMB" if iso_curr == "CNY" and "RMB" in live["rates"] else iso_curr
        fallback_rate = float(
            live["rates"].get(lookup_key)
            or live["rates"].get(iso_curr)
            or DEFAULT_FALLBACK_RATES.get(iso_curr, 1.0)
        )
        return {
            "currency": currency or iso_curr,
            "iso_currency": iso_curr,
            "requested_date": req_date,
            "rate_date": req_date,
            "rate": round(fallback_rate, 5),
            "rate_type": normalized_type,
            "source": f"{live.get('source', '基準匯率')} (最近期參考)",
            "is_live": bool(live.get("is_live", False)),
        }

    def convert_to_ntd(
        self,
        amount: float,
        currency: str,
        custom_rate: Optional[float] = None,
        target_date: Optional[str] = None,
    ) -> Tuple[float, float]:
        """Convert foreign currency amount to NTD based on explicit, dated, or live rates."""
        if custom_rate is not None and float(custom_rate) > 0:
            rate_val = float(custom_rate)
            return round(amount * rate_val, 2), rate_val

        iso_curr = normalize_fx_code(currency)
        if iso_curr == "TWD":
            return round(amount, 2), 1.0

        if target_date:
            dated = self.get_rate_for_date(iso_curr, target_date=target_date)
            rate = float(dated.get("rate") or DEFAULT_FALLBACK_RATES.get(iso_curr, 1.0))
            return round(amount * rate, 2), rate

        rates_data = self.get_live_rates()
        lookup_key = "RMB" if iso_curr == "CNY" and "RMB" in rates_data["rates"] else iso_curr
        rate = float(
            rates_data["rates"].get(lookup_key)
            or rates_data["rates"].get(iso_curr)
            or DEFAULT_FALLBACK_RATES.get(iso_curr, 1.0)
        )
        converted = round(amount * rate, 2)
        return converted, rate

    def _fetch_bot_finmind_for_date(
        self,
        iso_currency: str,
        target_date: str,
        rate_type: str = "spot_sell",
    ) -> Optional[Dict]:
        """Query FinMind Bank of Taiwan historical exchange rate dataset."""
        dt = datetime.strptime(target_date, "%Y-%m-%d").date()
        start_dt = (dt - timedelta(days=10)).strftime("%Y-%m-%d")
        url = (
            f"https://api.finmindtrade.com/api/v4/data"
            f"?dataset=TaiwanExchangeRate&data_id={iso_currency}"
            f"&start_date={start_dt}&end_date={target_date}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Invoice-Auditor/1.3"})
        with urllib.request.urlopen(req, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        rows = payload.get("data") or []
        if not rows or not isinstance(rows, list):
            return None

        # Choose latest quote row <= target_date so weekends/holidays step back cleanly
        eligible = [r for r in rows if isinstance(r, dict) and str(r.get("date", "")) <= target_date]
        if not eligible:
            return None
        eligible.sort(key=lambda r: str(r.get("date", "")))
        chosen = eligible[-1]

        spot_sell = float(chosen.get("spot_sell") or 0.0)
        cash_sell = float(chosen.get("cash_sell") or 0.0)
        spot_buy = float(chosen.get("spot_buy") or 0.0)
        cash_buy = float(chosen.get("cash_buy") or 0.0)

        if rate_type == "cash_sell":
            rate_val = cash_sell if cash_sell > 0 else spot_sell
            used_type = "cash_sell" if cash_sell > 0 else "spot_sell"
        else:
            # Many SEA currencies (MYR, VND, IDR, PHP, KRW) only publish cash rates at BOT
            rate_val = spot_sell if spot_sell > 0 else cash_sell
            used_type = "spot_sell" if spot_sell > 0 else "cash_sell"

        if rate_val <= 0:
            return None

        actual_date = str(chosen.get("date") or target_date)
        type_label = "即期賣出" if used_type == "spot_sell" else "現金賣出"
        return {
            "rate": round(rate_val, 5),
            "rate_date": actual_date,
            "rate_type": used_type,
            "source": f"臺灣銀行牌告匯率 ({type_label} {actual_date})",
            "cash_buy": cash_buy,
            "cash_sell": cash_sell,
            "spot_buy": spot_buy,
            "spot_sell": spot_sell,
            "is_live": True,
        }

    def _fetch_taifex_rows(self) -> List[Dict[str, str]]:
        """Fetch raw rows from Taifex OpenAPI (supports real CSV and JSON fallback)."""
        url = "https://openapi.taifex.com.tw/v1/DailyForeignExchangeRates"
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Invoice-Auditor/1.3"})
        with urllib.request.urlopen(req, timeout=4) as response:
            raw_text = response.read().decode("utf-8-sig", errors="replace").strip()

        if not raw_text:
            return []
        if raw_text.startswith("[") or raw_text.startswith("{"):
            parsed = json.loads(raw_text)
            return parsed if isinstance(parsed, list) else []

        reader = csv.DictReader(io.StringIO(raw_text))
        return [dict(row) for row in reader if row]

    @staticmethod
    def _parse_taifex_row(row: Dict) -> Dict[str, float]:
        """Parse one Taifex row supporting both Chinese CSV column headers and English JSON keys."""
        def _num(*keys: str) -> float:
            for k in keys:
                val = row.get(k)
                if val not in (None, "", "N/A", "-"):
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        pass
            return 0.0

        rates: Dict[str, float] = {}
        usd_ntd = _num("美元_新台幣(匯率)", "USD/NTD")
        if usd_ntd <= 0:
            return rates
        rates["USD"] = usd_ntd

        rmb_ntd = _num("人民幣_新台幣(匯率)", "RMB/NTD")
        if rmb_ntd > 0:
            rates["RMB"] = rmb_ntd
            rates["CNY"] = rmb_ntd

        eur_usd = _num("歐元_美元(匯率)", "EUR/USD")
        if eur_usd > 0:
            rates["EUR"] = usd_ntd * eur_usd

        usd_jpy = _num("美元_日幣(匯率)", "USD/JPY")
        if usd_jpy > 0:
            rates["JPY"] = usd_ntd / usd_jpy

        usd_hkd = _num("美元_港幣(匯率)", "USD/HKD")
        if usd_hkd > 0:
            rates["HKD"] = usd_ntd / usd_hkd

        gbp_usd = _num("英鎊_美元(匯率)", "GBP/USD")
        if gbp_usd > 0:
            rates["GBP"] = usd_ntd * gbp_usd

        aud_usd = _num("澳幣_美元(匯率)", "AUD/USD")
        if aud_usd > 0:
            rates["AUD"] = usd_ntd * aud_usd

        return rates

    def _fetch_taifex(self) -> Dict[str, float]:
        """Fetch official daily exchange rates from Taiwan Futures Exchange OpenAPI."""
        rows = self._fetch_taifex_rows()
        if not rows:
            raise ValueError("Empty data from Taifex")
        return self._parse_taifex_row(rows[-1])

    def _lookup_taifex_for_date(self, iso_currency: str, target_date: str) -> Optional[Dict]:
        """Find nearest prior rate row from Taifex CSV for target_date (YYYY-MM-DD)."""
        rows = self._fetch_taifex_rows()
        if not rows:
            return None
        target_compact = target_date.replace("-", "")
        best_row = None
        best_date_compact = ""
        for row in rows:
            raw_d = str(row.get("日期") or row.get("Date") or "").strip().replace("-", "").replace("/", "")
            if len(raw_d) == 8 and raw_d.isdigit() and raw_d <= target_compact and raw_d >= best_date_compact:
                best_row = row
                best_date_compact = raw_d
        if not best_row:
            return None
        parsed = self._parse_taifex_row(best_row)
        rate_val = parsed.get(iso_currency)
        if not rate_val or rate_val <= 0:
            return None
        formatted_d = f"{best_date_compact[:4]}-{best_date_compact[4:6]}-{best_date_compact[6:8]}"
        return {
            "rate": round(rate_val, 5),
            "rate_date": formatted_d,
            "rate_type": "taifex_close",
            "source": f"台灣期交所參考匯率 ({formatted_d})",
            "is_live": True,
        }

    def _fetch_open_exchange(self) -> Dict[str, float]:
        """Fetch supplementary rates (SEA, Europe, Asia-Pacific) via Open Exchange API."""
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Invoice-Auditor/1.3"})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode("utf-8"))

        rates_table = data.get("rates", {})
        twd_rate = rates_table.get("TWD", 0)
        if not twd_rate or twd_rate <= 0:
            return {}

        results = {}
        for code in ("THB", "SGD", "EUR", "JPY", "CNY", "HKD", "GBP", "AUD", "MYR", "VND", "IDR", "PHP", "KRW", "CAD", "CHF", "NZD"):
            if code in rates_table and rates_table[code] > 0:
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

    def _load_historical_cache(self) -> Dict[str, Dict]:
        if self.historical_cache_file.exists():
            try:
                return json.loads(self.historical_cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_historical_entry(self, key: str, data: Dict) -> None:
        entry = dict(data)
        entry["cached_at"] = datetime.now().isoformat()
        self._historical_cache[key] = entry
        try:
            self.historical_cache_file.write_text(
                json.dumps(self._historical_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Could not write historical fx cache: %s", e)


# Global singleton instance
fx_service = FXService()
