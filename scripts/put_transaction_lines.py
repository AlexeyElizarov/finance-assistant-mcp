"""MCP helper: replace operation lines (FIN-270 / FIN-272)."""

from __future__ import annotations

import urllib.parse
from typing import Any

from finance_api_client import ApiClient
from put_transaction import format_api_error


def _require_transaction_id(arguments: dict[str, Any]) -> str:
    """
    Require a non-empty stripped ``transaction_id``.

    :param arguments: Raw MCP arguments
    :return: Stripped transaction id
    :raises ValueError: When missing or empty after strip
    """
    if "transaction_id" not in arguments:
        raise ValueError("transaction_id is required")
    value = arguments["transaction_id"]
    if value is None:
        raise ValueError("transaction_id is required")
    text = str(value).strip()
    if not text:
        raise ValueError("transaction_id is required")
    return text


def _require_lines(arguments: dict[str, Any]) -> list[Any]:
    """
    Require a non-empty ``lines`` array without input ``budget_amount``.

    :param arguments: Raw MCP arguments
    :return: Lines list
    :raises ValueError: When missing, empty, or a line has ``budget_amount``
    """
    if "lines" not in arguments:
        raise ValueError("lines is required")
    lines = arguments["lines"]
    if not isinstance(lines, list) or len(lines) == 0:
        raise ValueError("lines must be a non-empty array")
    for line in lines:
        if isinstance(line, dict) and "budget_amount" in line:
            raise ValueError(
                "budget_amount is not an allowed input field on lines"
            )
    return lines


def put_transaction_lines(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Replace operation lines via FIN-272 ``PUT …/lines``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``lines``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    tx_id = _require_transaction_id(arguments)
    lines = _require_lines(arguments)
    allow_closed = bool(arguments.get("allow_closed", False))
    query = urllib.parse.urlencode(
        {"allow_closed": "true" if allow_closed else "false"}
    )
    path = (
        f"/api/v1/transactions/{urllib.parse.quote(tx_id, safe='')}/lines?{query}"
    )
    body = {"lines": lines}
    status, resp = api.request("PUT", path, data=body)
    if status != 200 or not isinstance(resp, dict):
        raise RuntimeError(
            format_api_error(status, resp, method="PUT", path=path)
        )
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "lines": resp.get("lines", []),
    }
