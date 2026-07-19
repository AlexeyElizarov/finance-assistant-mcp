"""MCP helper: PATCH transaction type + category (FIN-211 / FIN-202)."""

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
)


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


def put_transaction_category(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    transaction_id: Any,
    transaction_type: Any,
    transaction_category: Any,
    allow_closed: bool = False,
    reconciliation_note: Any = ...,
    category_source: Any = ...,
) -> dict[str, Any]:
    """
    Set ``transaction_type`` with a compatible non-empty category (FIN-211).

    Thin wrap of ``PATCH /api/v1/transactions/{id}/category`` (FIN-202).

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param transaction_id: Row UUID
    :param transaction_type: ``C``/``P``/``S``/``I`` (strip; enum via API)
    :param transaction_category: Non-empty category id
    :param allow_closed: Closed-period bypass query flag
    :param reconciliation_note: Include in body when not sentinel (FIN-202 D-10)
    :param category_source: Forbidden in v1 when not sentinel (D-04)
    :return: Tool success payload with ``transaction`` subset
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: Non-200 API response
    """
    if category_source is not ...:
        raise ValueError(
            "category_source is not accepted in put_transaction_category v1 "
            "(omit the key; backend applies implicit manual)"
        )

    tx_id = _require_non_empty("transaction_id", transaction_id)
    tx_type = _require_non_empty("transaction_type", transaction_type)
    tx_category = _require_non_empty("transaction_category", transaction_category)

    body: dict[str, Any] = {
        "transaction_type": tx_type,
        "transaction_category": tx_category,
    }
    if reconciliation_note is not ...:
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
