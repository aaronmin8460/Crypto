from __future__ import annotations

from collections.abc import Iterable


def normalize_symbol(symbol: str | None, quote_currency: str | None = None) -> str | None:
    """Normalize broker and scanner symbols into a canonical BASE/QUOTE format."""
    if not isinstance(symbol, str):
        return None

    cleaned = symbol.strip().upper()
    if not cleaned:
        return None

    normalized_quote = quote_currency.upper() if isinstance(quote_currency, str) and quote_currency else None
    for delimiter in (" ", "-", "_"):
        cleaned = cleaned.replace(delimiter, "/")
    if "//" in cleaned or cleaned.count("/") > 1:
        return None

    if "/" in cleaned:
        parts = [part for part in cleaned.split("/") if part]
        if len(parts) != 2:
            return None
        base, quote = parts
    else:
        if normalized_quote is None or not cleaned.endswith(normalized_quote):
            return None
        base = cleaned[: -len(normalized_quote)]
        quote = normalized_quote

    if not base or not quote or base == quote:
        return None
    if not base.isalnum() or not quote.isalnum():
        return None
    if normalized_quote is not None and quote != normalized_quote:
        return None

    return f"{base}/{quote}"


def unique_symbols(symbols: Iterable[str], quote_currency: str | None = None) -> list[str]:
    seen: set[str] = set()
    normalized_symbols: list[str] = []
    for raw_symbol in symbols:
        normalized = normalize_symbol(raw_symbol, quote_currency=quote_currency)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        normalized_symbols.append(normalized)
    return normalized_symbols
