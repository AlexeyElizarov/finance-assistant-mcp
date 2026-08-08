"""Household funds catalogue MCP helpers (FIN-256)."""

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


def _require_str(arguments: dict[str, Any], key: str) -> str:
    """
    Require a non-empty stripped string from MCP arguments.

    :param arguments: Raw MCP arguments (presence-preserving)
    :param key: Argument name
    :return: Stripped value
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


def _put_nullable_str_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
    key: str,
) -> None:
    """
    Copy optional nullable string into body when present.

    Explicit ``null`` becomes JSON null; omit leaves the key out.
    Empty string stays empty string after strip (not coerced to null).

    :param body: Request body under construction
    :param arguments: Raw MCP arguments
    :param key: Argument name
    """
    if key not in arguments:
        return
    value = arguments[key]
    if value is None:
        body[key] = None
        return
    body[key] = str(value).strip()


def _path_segment(value: str) -> str:
    """
    Encode one URL path segment.

    :param value: Raw id
    :return: Encoded segment
    """
    return urllib.parse.quote(value, safe="")


def _wrap_ok(profile: str, base: str, key: str, value: Any) -> dict[str, Any]:
    return {"ok": True, "profile": profile, "base": base, key: value}


def _funds_collection_path(household_id: str) -> str:
    return f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}/funds"


def _fund_item_path(household_id: str, fund_id: str) -> str:
    return (
        f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}"
        f"/funds/{_path_segment(fund_id)}"
    )


def list_household_funds(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List household funds via ``GET …/funds``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (optional ``applicable_on``)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    path = _funds_collection_path(household_id)
    if "applicable_on" in arguments and arguments["applicable_on"] is not None:
        applicable = str(arguments["applicable_on"]).strip()
        if applicable:
            path = f"{path}?{urllib.parse.urlencode({'applicable_on': applicable})}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    funds = body.get("funds")
    if not isinstance(funds, list):
        raise RuntimeError(f"GET {path}: funds is not a list")
    return _wrap_ok(profile, base, "funds", funds)


def get_household_fund(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one fund via ``GET …/funds/{fund_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    fund_id = _require_str(arguments, "fund_id")
    path = _fund_item_path(household_id, fund_id)
    status, response = api.request("GET", path)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="GET", path=path)
    return _wrap_ok(profile, base, "fund", response)


def create_household_fund(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create a fund via ``PUT …/funds/{fund_id}`` (create-only, HTTP 201).

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (presence-preserving for optional fields)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    fund_id = _require_str(arguments, "fund_id")
    body: dict[str, Any] = {
        "name": _require_str(arguments, "name"),
        "allocation_rule": _require_str(arguments, "allocation_rule"),
        "valid_from": _require_str(arguments, "valid_from"),
    }
    _put_nullable_str_if_present(body, arguments, "member_id")
    _put_nullable_str_if_present(body, arguments, "valid_to")
    path = _fund_item_path(household_id, fund_id)
    status, response = api.request("PUT", path, data=body)
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PUT", path=path)
    return _wrap_ok(profile, base, "fund", response)


def patch_household_fund(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch a fund via ``PATCH …/funds/{fund_id}`` (rename and/or delimit).

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (at least one of ``name`` / ``valid_to``)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    fund_id = _require_str(arguments, "fund_id")
    has_name = "name" in arguments
    has_valid_to = "valid_to" in arguments
    if not has_name and not has_valid_to:
        raise ValueError("at least one of name or valid_to is required")
    body: dict[str, Any] = {}
    if has_name:
        if arguments["name"] is None:
            raise ValueError("name must be a non-empty string")
        text = str(arguments["name"]).strip()
        if not text:
            raise ValueError("name must be a non-empty string")
        body["name"] = text
    _put_nullable_str_if_present(body, arguments, "valid_to")
    path = _fund_item_path(household_id, fund_id)
    status, response = api.request("PATCH", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    return _wrap_ok(profile, base, "fund", response)
