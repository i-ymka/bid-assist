"""Currency conversion service with free API + hardcoded fallback."""

import logging
import math
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# API endpoint (free, no key required)
_API_URL = "https://open.er-api.com/v6/latest/USD"

# Cache
_rates: dict = {}
_last_fetch: float = 0
_CACHE_TTL = 86400  # 24 hours

# Fallback rates (USD-based, approximate)
_FALLBACK_RATES = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "INR": 83.5,
    "AUD": 1.55,
    "CAD": 1.36,
    "SGD": 1.34,
    "HKD": 7.82,
    "BRL": 4.97,
    "PKR": 278.0,
    "PHP": 56.0,
    "MYR": 4.47,
    "NZD": 1.67,
    "ZAR": 18.5,
    "AED": 3.67,
    "SAR": 3.75,
    "PLN": 4.05,
    "CZK": 23.5,
    "SEK": 10.8,
    "NOK": 10.9,
    "DKK": 6.9,
    "CHF": 0.88,
    "JPY": 150.0,
    "KRW": 1330.0,
    "TWD": 31.5,
    "THB": 35.0,
    "IDR": 15700.0,
    "VND": 25000.0,
    "TRY": 32.0,
    "MXN": 17.2,
    "CLP": 950.0,
    "COP": 3950.0,
    "ARS": 870.0,
    "NGN": 1550.0,
    "KES": 153.0,
    "UAH": 41.0,
    "RON": 4.6,
    "BGN": 1.8,
    "HUF": 365.0,
    "ILS": 3.65,
    "EGP": 49.0,
    "BDT": 110.0,
    "LKR": 310.0,
}


def _fetch_rates() -> dict:
    """Fetch current exchange rates from API. Returns rates dict or empty on failure."""
    global _rates, _last_fetch

    # Return cache if fresh
    if _rates and (time.time() - _last_fetch) < _CACHE_TTL:
        return _rates

    try:
        resp = requests.get(_API_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") == "success":
            _rates = data.get("rates", {})
            _last_fetch = time.time()
            logger.info(f"Exchange rates updated: {len(_rates)} currencies")
            return _rates
        else:
            logger.warning(f"Exchange rate API returned non-success: {data.get('result')}")
    except Exception as e:
        logger.warning(f"Failed to fetch exchange rates: {e}")

    return _rates  # Return stale cache if available


def _get_rate(currency_code: str) -> Optional[float]:
    """Get USD → currency rate. Tries API first, falls back to hardcoded."""
    code = currency_code.upper()
    if code == "USD":
        return 1.0

    # Try live rates
    rates = _fetch_rates()
    if code in rates:
        return rates[code]

    # Fallback to hardcoded
    if code in _FALLBACK_RATES:
        logger.debug(f"Using fallback rate for {code}")
        return _FALLBACK_RATES[code]

    logger.warning(f"No exchange rate for {code}, treating as USD")
    return None


def to_usd(amount: float, currency_code: str) -> float:
    """Convert amount from given currency to USD."""
    if not amount:
        return 0.0
    code = currency_code.upper()
    if code == "USD":
        return amount
    rate = _get_rate(code)
    if rate is None:
        return amount  # Unknown currency, return as-is
    return amount / rate


def from_usd(amount: float, currency_code: str) -> float:
    """Convert amount from USD to given currency."""
    if not amount:
        return 0.0
    code = currency_code.upper()
    if code == "USD":
        return amount
    rate = _get_rate(code)
    if rate is None:
        return amount  # Unknown currency, return as-is
    return amount * rate


def round_up_10(amount: float) -> int:
    """Round amount up to nearest multiple of 10."""
    return int(math.ceil(amount / 10) * 10)
