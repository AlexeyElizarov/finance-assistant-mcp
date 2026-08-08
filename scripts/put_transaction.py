"""MCP helper: canonical PATCH for operation fields (FIN-260 / FIN-258)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

DECLARED_BODY_FIELDS: tuple[str, ...] = (
    "transaction_category",
    "category_source",
    "reconciliation_note",
    "transaction_type",
    "expense_owner",
    "project",
    "project_source",
    "fund_id",
)

_RESPONSE_FIELDS: tuple[str, ...] = ("id",) + DECLARED_BODY_FIELDS


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


def _build_body(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Build PATCH body from present declared keys (omit vs null preserved).

    :param arguments: Raw MCP arguments
    :return: Request body
    :raises ValueError: When no declared field is present
    """
    body: dict[str, Any] = {}
    for key in DECLARED_BODY_FIELDS:
        if key not in arguments:
            continue
        value = arguments[key]
        if value is None:
            body[key] = None
        else:
            body[key] = str(value).strip()
    if not body:
        raise ValueError(
            "at least one field from the declared set is required "
            f"({', '.join(DECLARED_BODY_FIELDS)})"
        )
    return body


def _transaction_subset(resp: dict[str, Any], transaction_id: str) -> dict[str, Any]:
    """
    Map API row to MCP ``transaction`` subset (D-08).

    :param resp: Successful API response body
    :param transaction_id: Path id used as fallback for ``id``
    :return: Subset with ``id`` and all declared body fields
    """
    transaction: dict[str, Any] = {}
    for key in _RESPONSE_FIELDS:
        if key in resp:
            transaction[key] = resp[key]
        elif key == "id":
            transaction[key] = transaction_id
        else:
            transaction[key] = None
    return transaction


def put_transaction(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    PATCH operation fields via the FIN-258 canonical path (FIN-260).

    Thin wrap of ``PATCH /api/v1/transactions/{id}``. Body contains only
    declared fields that are present in ``arguments`` (omit ≠ null).

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments (presence-preserving)
    :return: Tool success payload with ``transaction`` subset
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    tx_id = _require_transaction_id(arguments)
    body = _build_body(arguments)
    allow_closed = bool(arguments.get("allow_closed", False))
    query = urllib.parse.urlencode(
        {"allow_closed": "true" if allow_closed else "false"}
    )
    path = (
        f"/api/v1/transactions/{urllib.parse.quote(tx_id, safe='')}?{query}"
    )
    status, resp = api.request("PATCH", path, data=body)
    if status != 200 or not isinstance(resp, dict):
        raise RuntimeError(
            format_api_error(status, resp, method="PATCH", path=path)
        )
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "transaction": _transaction_subset(resp, tx_id),
    }
