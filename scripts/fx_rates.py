"""Planned FX rates MCP helpers (FIN-114)."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

FX_RATES_PATH = "/api/v1/fx-rates"
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def format_api_error(
    status: int,
    body: Any,
    *,
    method: str,
    path: str,
) -> str:
    """
    Build a tool error message from an API error response.

    :param status: HTTP status code
    :param body: Parsed or raw response body
    :param method: HTTP method
    :param path: Request path
    :return: Error message string
    """
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        code = str(err.get("code", ""))
        message = str(err.get("message", body))
        details = err.get("details")
        text = f"{method} {path} -> HTTP {status}"
        if code:
            text += f" {code}"
        text += f": {message}"
        if details is not None:
            text += f" details={json.dumps(details, ensure_ascii=False)}"
        return text
    return f"{method} {path} -> HTTP {status}: {body}"


def _raise_api_error(status: int, body: Any, *, method: str, path: str) -> None:
    raise RuntimeError(format_api_error(status, body, method=method, path=path))


def _validate_fx_period(period: str) -> None:
    """
    Validate month argument for FX upsert.

    :param period: ``YYYY-MM`` or ``YYYY-MM-DD``
    :raises RuntimeError: When format is invalid
    """
    text = period.strip()
    if len(text) >= 7:
        text = text[:7]
    elif len(text) == 6 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}"
    if not _PERIOD_RE.match(text):
        raise RuntimeError(f"Ожидается period YYYY-MM, получено: {period!r}")


def _optional_query_params(**kwargs: str | None) -> str:
    parts = [(key, value) for key, value in kwargs.items() if value is not None and str(value).strip()]
    if not parts:
        return ""
    return urllib.parse.urlencode(parts)


def list_fx_rates(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    period: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    from_currency: str | None = None,
    to_currency: str | None = None,
) -> dict[str, Any]:
    """
    List planned FX rates via ``GET /api/v1/fx-rates``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param period: Single month filter
    :param period_from: Range start
    :param period_to: Range end
    :param from_currency: Source currency code
    :param to_currency: Target currency code
    :return: Wrapped MCP payload
    """
    query = _optional_query_params(
        period=period,
        period_from=period_from,
        period_to=period_to,
        from_currency=from_currency,
        to_currency=to_currency,
    )
    path = f"{FX_RATES_PATH}?{query}" if query else FX_RATES_PATH
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    rates = body.get("fx_rates", [])
    if not isinstance(rates, list):
        raise RuntimeError("GET /fx-rates: fx_rates is not a list")
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "fx_rates": rates,
    }


def upsert_fx_rate(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    period: str,
    rate: str,
    from_currency: str | None = None,
    to_currency: str | None = None,
) -> dict[str, Any]:
    """
    Upsert one planned FX rate via ``PUT /api/v1/fx-rates``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param period: Month ``YYYY-MM`` or ``YYYY-MM-DD``
    :param rate: Planned rate string
    :param from_currency: Source currency code
    :param to_currency: Target currency code
    :return: Wrapped MCP payload
    """
    period_text = str(period).strip()
    if not period_text:
        raise RuntimeError("period is required")
    rate_text = str(rate).strip()
    if not rate_text:
        raise RuntimeError("rate is required")
    _validate_fx_period(period_text)
    payload: dict[str, str] = {"period": period_text, "rate": rate_text}
    if from_currency is not None and str(from_currency).strip():
        payload["from_currency"] = str(from_currency).strip()
    if to_currency is not None and str(to_currency).strip():
        payload["to_currency"] = str(to_currency).strip()
    status, body = api.request("PUT", FX_RATES_PATH, data=payload)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="PUT", path=FX_RATES_PATH)
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "fx_rate": body,
    }
