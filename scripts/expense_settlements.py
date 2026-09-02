"""MCP helpers: expense settlement thin wrappers (FIN-271 / FIN-273)."""

from __future__ import annotations

import urllib.parse
from typing import Any

from finance_api_client import ApiClient
from put_transaction import format_api_error


def _require_stripped(arguments: dict[str, Any], key: str) -> str:
    """
    Require a non-empty stripped string argument.

    :param arguments: Raw MCP arguments
    :param key: Argument name
    :return: Stripped value
    :raises ValueError: When missing or empty after strip
    """
    if key not in arguments:
        raise ValueError(f"{key} is required")
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{key} is required")
    return text


def _allow_closed_query(arguments: dict[str, Any]) -> str:
    """
    Build ``allow_closed`` query string.

    :param arguments: Raw MCP arguments
    :return: Encoded query fragment without leading ``?``
    """
    allow_closed = bool(arguments.get("allow_closed", False))
    return urllib.parse.urlencode(
        {"allow_closed": "true" if allow_closed else "false"}
    )


def _raise_api_error(
    status: int,
    resp: Any,
    *,
    method: str,
    path: str,
) -> None:
    """
    Raise ``RuntimeError`` from a non-success API response.

    :param status: HTTP status
    :param resp: Parsed body
    :param method: HTTP method
    :param path: Request path
    :raises RuntimeError: Always
    """
    raise RuntimeError(format_api_error(status, resp, method=method, path=path))


def create_expense_settlement(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create expense settlement via FIN-273 ``POST …/expense-settlements``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``settlement``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-201 API response
    """
    compensating_line_id = _require_stripped(arguments, "compensating_line_id")
    expense_line_id = _require_stripped(arguments, "expense_line_id")
    amount = _require_stripped(arguments, "amount")
    query = _allow_closed_query(arguments)
    path = f"/api/v1/expense-settlements?{query}"
    body = {
        "compensating_line_id": compensating_line_id,
        "expense_line_id": expense_line_id,
        "amount": amount,
    }
    status, resp = api.request("POST", path, data=body)
    if status != 201 or not isinstance(resp, dict):
        _raise_api_error(status, resp, method="POST", path=path)
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "settlement": resp,
    }


def get_expense_settlement(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Read expense settlement via FIN-273 ``GET …/expense-settlements/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``settlement``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    settlement_id = _require_stripped(arguments, "settlement_id")
    path = (
        f"/api/v1/expense-settlements/"
        f"{urllib.parse.quote(settlement_id, safe='')}"
    )
    status, resp = api.request("GET", path)
    if status != 200 or not isinstance(resp, dict):
        _raise_api_error(status, resp, method="GET", path=path)
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "settlement": resp,
    }


def patch_expense_settlement(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch settlement amount via FIN-273 ``PATCH …/expense-settlements/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``settlement``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    settlement_id = _require_stripped(arguments, "settlement_id")
    amount = _require_stripped(arguments, "amount")
    query = _allow_closed_query(arguments)
    path = (
        f"/api/v1/expense-settlements/"
        f"{urllib.parse.quote(settlement_id, safe='')}?{query}"
    )
    body = {"amount": amount}
    status, resp = api.request("PATCH", path, data=body)
    if status != 200 or not isinstance(resp, dict):
        _raise_api_error(status, resp, method="PATCH", path=path)
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "settlement": resp,
    }


def delete_expense_settlement(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete expense settlement via FIN-273 ``DELETE …/expense-settlements/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``deleted: true``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-204 API response
    """
    settlement_id = _require_stripped(arguments, "settlement_id")
    query = _allow_closed_query(arguments)
    path = (
        f"/api/v1/expense-settlements/"
        f"{urllib.parse.quote(settlement_id, safe='')}?{query}"
    )
    status, resp = api.request("DELETE", path)
    if status != 204:
        _raise_api_error(status, resp, method="DELETE", path=path)
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "deleted": True,
    }


def list_expense_settlements(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List settlements for a line via FIN-273 ``GET …/expense-settlements``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``settlements``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    line_id = _require_stripped(arguments, "line_id")
    query = urllib.parse.urlencode({"line_id": line_id})
    path = f"/api/v1/expense-settlements?{query}"
    status, resp = api.request("GET", path)
    if status != 200 or not isinstance(resp, dict):
        _raise_api_error(status, resp, method="GET", path=path)
    settlements = resp.get("settlements", [])
    if not isinstance(settlements, list):
        settlements = []
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "settlements": settlements,
    }


def get_line_settlement_state(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Read line settlement state via FIN-273 settlement-state path.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments
    :return: Tool success payload with ``settlement_state``
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    line_id = _require_stripped(arguments, "line_id")
    path = (
        f"/api/v1/transaction-lines/"
        f"{urllib.parse.quote(line_id, safe='')}/settlement-state"
    )
    status, resp = api.request("GET", path)
    if status != 200 or not isinstance(resp, dict):
        _raise_api_error(status, resp, method="GET", path=path)
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "settlement_state": resp,
    }
