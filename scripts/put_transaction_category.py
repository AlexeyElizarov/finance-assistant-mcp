"""MCP helper: PATCH transaction type/category and expense_owner (FIN-211 / FIN-241)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

_TRANSACTION_FIELDS = (
    "id",
    "transaction_type",
    "transaction_category",
    "category_source",
    "classification_status",
    "reconciliation_note",
    "expense_owner",
)

_MISSING = object()


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


def _require_non_empty(name: str, value: Any) -> str:
    """
    Strip and require a non-empty string argument.

    :param name: Argument name for error messages
    :param value: Raw argument value
    :return: Stripped value
    :raises ValueError: When missing or empty after strip
    """
    if value is None:
        raise ValueError(f"{name} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _non_empty_stripped(value: Any) -> str | None:
    """
    Return stripped non-empty string, or ``None`` when unset/blank.

    :param value: Raw argument value
    :return: Stripped value or ``None``
    """
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def put_transaction_category(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    transaction_id: Any,
    transaction_type: Any = None,
    transaction_category: Any = None,
    allow_closed: bool = False,
    reconciliation_note: Any = _MISSING,
    category_source: Any = _MISSING,
    expense_owner: Any = _MISSING,
) -> dict[str, Any]:
    """
    PATCH transaction type/category and/or ``expense_owner`` (FIN-211 / FIN-241).

    Thin wrap of ``PATCH /api/v1/transactions/{id}/category``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param transaction_id: Row UUID
    :param transaction_type: ``C``/``P``/``S``/``I`` when correcting type (with category)
    :param transaction_category: Non-empty category id when correcting type
    :param allow_closed: Closed-period bypass query flag
    :param reconciliation_note: Include in body when not sentinel
    :param category_source: Forbidden when not sentinel (FIN-211 D-04)
    :param expense_owner: Include in body when not sentinel (no MCP normalize)
    :return: Tool success payload with ``transaction`` subset
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    if category_source is not _MISSING:
        raise ValueError(
            "category_source is not accepted in put_transaction_category v1 "
            "(omit the key; backend applies implicit manual)"
        )

    tx_id = _require_non_empty("transaction_id", transaction_id)
    type_stripped = _non_empty_stripped(transaction_type)
    category_stripped = _non_empty_stripped(transaction_category)
    has_type = type_stripped is not None
    has_category = category_stripped is not None
    has_owner = expense_owner is not _MISSING

    if has_type ^ has_category:
        raise ValueError(
            "transaction_type and transaction_category must be provided together"
        )
    if not (has_type and has_category) and not has_owner:
        raise ValueError(
            "provide transaction_type+transaction_category and/or expense_owner"
        )

    body: dict[str, Any] = {}
    if has_type:
        body["transaction_type"] = type_stripped
        body["transaction_category"] = category_stripped
    if has_owner:
        body["expense_owner"] = expense_owner
    if reconciliation_note is not _MISSING:
        body["reconciliation_note"] = reconciliation_note

    query = urllib.parse.urlencode(
        {"allow_closed": "true" if allow_closed else "false"}
    )
    path = f"/api/v1/transactions/{urllib.parse.quote(tx_id, safe='')}/category?{query}"
    status, resp = api.request("PATCH", path, data=body)
    if status != 200 or not isinstance(resp, dict):
        raise RuntimeError(
            format_api_error(status, resp, method="PATCH", path=path)
        )

    transaction: dict[str, Any] = {}
    for key in _TRANSACTION_FIELDS:
        if key in resp:
            transaction[key] = resp[key]
        elif key == "reconciliation_note":
            transaction[key] = ""
        elif key == "id":
            transaction[key] = tx_id
        else:
            transaction[key] = None
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "transaction": transaction,
    }
