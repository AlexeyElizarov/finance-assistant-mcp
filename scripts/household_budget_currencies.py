"""Household budget currency history MCP helpers (FIN-332)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

HOUSEHOLDS_PATH = "/api/v1/households"


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


def _require_present_nonblank(arguments: dict[str, Any], key: str) -> str:
    """
    Require a present string that is non-blank after strip (D-04).

    ``strip()`` is used only for the emptiness check. The returned value is the
    original argument string (not the stripped form).

    :param arguments: Raw MCP arguments (presence-preserving)
    :param key: Argument name
    :return: Original string value
    """
    if key not in arguments:
        raise ValueError(f"{key} is required")
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} is required")
    text = str(value)
    if not text.strip():
        raise ValueError(f"{key} is required")
    return text


def _path_segment(value: str) -> str:
    """
    Encode one URL path segment.

    :param value: Raw id
    :return: Encoded segment
    """
    return urllib.parse.quote(value, safe="")


def _wrap_ok(profile: str, base: str, key: str, value: Any) -> dict[str, Any]:
    return {"ok": True, "profile": profile, "base": base, key: value}


def _collection_path(household_id: str) -> str:
    return f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}/budget-currencies"


def list_household_budget_currencies(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List budget currency history via ``GET .../budget-currencies``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_id``)
    :return: Wrapped MCP payload
    """
    household_id = _require_present_nonblank(arguments, "household_id")
    path = _collection_path(household_id)
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    rows = body.get("budget_currencies")
    if not isinstance(rows, list):
        raise RuntimeError(
            f"GET {path}: budget_currencies is not a list"
        )
    return _wrap_ok(profile, base, "budget_currencies", rows)


def create_household_budget_currency(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one budget currency history row via ``POST .../budget-currencies``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_id``, ``valid_from``, ``currency``)
    :return: Wrapped MCP payload
    """
    household_id = _require_present_nonblank(arguments, "household_id")
    valid_from = _require_present_nonblank(arguments, "valid_from")
    currency = _require_present_nonblank(arguments, "currency")
    path = _collection_path(household_id)
    request_body = {"valid_from": valid_from, "currency": currency}
    status, response = api.request("POST", path, data=request_body)
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    return _wrap_ok(profile, base, "budget_currency", response)
