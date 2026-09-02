"""MCP helper: read operation with lines (FIN-270 / FIN-272)."""

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


def get_transaction(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Read operation with lines via FIN-272 ``GET /transactions/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``transaction``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    tx_id = _require_transaction_id(arguments)
    path = f"/api/v1/transactions/{urllib.parse.quote(tx_id, safe='')}"
    status, resp = api.request("GET", path)
    if status != 200 or not isinstance(resp, dict):
        raise RuntimeError(
            format_api_error(status, resp, method="GET", path=path)
        )
    transaction = dict(resp)
    if "posted_amount" not in transaction:
        transaction["posted_amount"] = None
    if "posted_currency" not in transaction:
        transaction["posted_currency"] = None
    if "bank_account_id" not in transaction:
        transaction["bank_account_id"] = None
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "transaction": transaction,
    }
