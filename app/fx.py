"""Currency support: supported list, web FX fetch, and NL currency detection.

Rates are stored as ``to_sgd`` (1 unit of the currency = N SGD). The fetch uses a
keyless public endpoint; on any failure callers keep the last stored / fallback
rates (the app never blocks on FX).
"""
from __future__ import annotations

import re
from decimal import Decimal

import httpx

# Display order for selects + dashboard. (code, name, symbol)
CURRENCIES: list[dict] = [
    {"code": "SGD", "name": "Singapore Dollar", "symbol": "S$"},
    {"code": "MYR", "name": "Malaysian Ringgit", "symbol": "RM"},
    {"code": "USD", "name": "US Dollar", "symbol": "US$"},
    {"code": "EUR", "name": "Euro", "symbol": "€"},
    {"code": "GBP", "name": "British Pound", "symbol": "£"},
    {"code": "JPY", "name": "Japanese Yen", "symbol": "¥"},
    {"code": "CNY", "name": "Chinese Yuan", "symbol": "¥"},
    {"code": "HKD", "name": "Hong Kong Dollar", "symbol": "HK$"},
    {"code": "TWD", "name": "Taiwan Dollar", "symbol": "NT$"},
    {"code": "THB", "name": "Thai Baht", "symbol": "฿"},
    {"code": "IDR", "name": "Indonesian Rupiah", "symbol": "Rp"},
    {"code": "AUD", "name": "Australian Dollar", "symbol": "A$"},
    {"code": "KRW", "name": "South Korean Won", "symbol": "₩"},
    {"code": "INR", "name": "Indian Rupee", "symbol": "₹"},
    {"code": "PHP", "name": "Philippine Peso", "symbol": "₱"},
    {"code": "VND", "name": "Vietnamese Dong", "symbol": "₫"},
]

CODES: list[str] = [c["code"] for c in CURRENCIES]
SUPPORTED: set[str] = set(CODES)

# Static fallback rates (to_sgd), used until the first live fetch succeeds.
# MYR kept at 0.30 to match the seed / app_config so the acceptance figures hold.
FALLBACK_RATES: dict[str, Decimal] = {
    "SGD": Decimal("1"),
    "MYR": Decimal("0.30"),
    "USD": Decimal("1.35"),
    "EUR": Decimal("1.45"),
    "GBP": Decimal("1.70"),
    "JPY": Decimal("0.0090"),
    "CNY": Decimal("0.187"),
    "HKD": Decimal("0.173"),
    "TWD": Decimal("0.042"),
    "THB": Decimal("0.038"),
    "IDR": Decimal("0.000083"),
    "AUD": Decimal("0.89"),
    "KRW": Decimal("0.00098"),
    "INR": Decimal("0.0162"),
    "PHP": Decimal("0.0235"),
    "VND": Decimal("0.000053"),
}

FX_SOURCE_URL = "https://open.er-api.com/v6/latest/SGD"


def fetch_rates(timeout: float = 12.0) -> dict[str, Decimal]:
    """Fetch live to_sgd rates for every supported currency. Raises on failure."""
    resp = httpx.get(FX_SOURCE_URL, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("result") != "success":
        raise ValueError(f"FX source returned: {data.get('result')!r}")
    per_sgd = data["rates"]  # X units per 1 SGD
    out: dict[str, Decimal] = {"SGD": Decimal("1.000000")}
    for code in CODES:
        if code == "SGD":
            continue
        rate = per_sgd.get(code)
        if not rate or float(rate) <= 0:
            continue  # keep fallback for anything the source omits
        to_sgd = (Decimal("1") / Decimal(str(rate))).quantize(Decimal("0.000001"))
        out[code] = to_sgd
    return out


# ── NL currency detection (symbols, codes, words) ───────────────────────────
# Ordered: multi-char/symbol-prefixed forms before bare symbols. First match wins.
_CURRENCY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(usd|us\$|us dollars?|greenback)\b", re.I), "USD"),
    (re.compile(r"\b(sgd|s\$|sing dollars?)\b", re.I), "SGD"),
    (re.compile(r"\b(myr|rm|ringgit)\b", re.I), "MYR"),
    (re.compile(r"\b(hkd|hk\$)\b", re.I), "HKD"),
    (re.compile(r"\b(twd|ntd|nt\$|taiwan)\b", re.I), "TWD"),
    (re.compile(r"\b(aud|au\$|a\$|aussie)\b", re.I), "AUD"),
    (re.compile(r"\b(eur|euros?)\b|€", re.I), "EUR"),
    (re.compile(r"\b(gbp|pounds?|quid|sterling)\b|£", re.I), "GBP"),
    (re.compile(r"\b(cny|rmb|yuan|renminbi)\b", re.I), "CNY"),
    (re.compile(r"\b(jpy|yen)\b|¥", re.I), "JPY"),
    (re.compile(r"\b(thb|baht)\b|฿", re.I), "THB"),
    (re.compile(r"\b(idr|rupiah|rp)\b", re.I), "IDR"),
    (re.compile(r"\b(krw|won)\b|₩", re.I), "KRW"),
    (re.compile(r"\b(inr|rupees?)\b|₹", re.I), "INR"),
    (re.compile(r"\b(php|peso)\b|₱", re.I), "PHP"),
    (re.compile(r"\b(vnd|dong)\b|₫", re.I), "VND"),
]


def detect_currency(text: str) -> str | None:
    """Return a supported currency code if the text clearly names one, else None.
    Bare ``$`` is intentionally ignored (ambiguous) so it falls back to default."""
    if not text:
        return None
    for pat, code in _CURRENCY_PATTERNS:
        if pat.search(text):
            return code
    return None
