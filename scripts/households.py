"""Household master data MCP helpers (FIN-240)."""

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


def _put_bool_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
    key: str,
) -> None:
    """
    Copy optional bool into body when present (D-09).

    :param body: PUT body under construction
    :param arguments: Raw MCP arguments
    :param key: Argument name
    """
    if key not in arguments:
        return
    value = arguments[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a bool")
    body[key] = value


def _put_nullable_str_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
    key: str,
) -> None:
    """
    Copy optional nullable string into body when present (D-09).

    Explicit ``null`` becomes JSON null; omit leaves the key out.

    :param body: PUT body under construction
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


def list_households(
    api: ApiClient,
    *,
    profile: str,
    base: str,
) -> dict[str, Any]:
    """
    List households via ``GET /api/v1/households``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :return: Wrapped MCP payload
    """
    path = HOUSEHOLDS_PATH
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    households = body.get("households")
    if not isinstance(households, list):
        raise RuntimeError("GET /households: households is not a list")
    return _wrap_ok(profile, base, "households", households)


def upsert_household(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Upsert one household via ``PUT /api/v1/households/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (presence-preserving for optional fields)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "id")
    body: dict[str, Any] = {"name": _require_str(arguments, "name")}
    _put_bool_if_present(body, arguments, "is_active")
    path = f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}"
    status, response = api.request("PUT", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PUT", path=path)
    return _wrap_ok(profile, base, "household", response)


def list_household_members(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List members via ``GET /api/v1/households/{id}/members``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_id``)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    path = f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}/members"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    members = body.get("members")
    if not isinstance(members, list):
        raise RuntimeError("GET /households/.../members: members is not a list")
    return _wrap_ok(profile, base, "members", members)


def upsert_household_member(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Upsert one member via ``PUT .../members/{member_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (presence-preserving for optional fields)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    member_id = _require_str(arguments, "member_id")
    body: dict[str, Any] = {"display_name": _require_str(arguments, "display_name")}
    _put_bool_if_present(body, arguments, "is_active")
    path = (
        f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}"
        f"/members/{_path_segment(member_id)}"
    )
    status, response = api.request("PUT", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PUT", path=path)
    return _wrap_ok(profile, base, "member", response)


def list_bank_accounts(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List bank accounts via ``GET .../bank-accounts``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_id``)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    path = f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}/bank-accounts"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    accounts = body.get("bank_accounts")
    if not isinstance(accounts, list):
        raise RuntimeError(
            "GET /households/.../bank-accounts: bank_accounts is not a list"
        )
    return _wrap_ok(profile, base, "bank_accounts", accounts)


def upsert_bank_account(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Upsert one bank account via ``PUT .../bank-accounts/{account_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (presence-preserving for optional fields)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    account_id = _require_str(arguments, "account_id")
    body: dict[str, Any] = {
        "provider": _require_str(arguments, "provider"),
        "display_name": _require_str(arguments, "display_name"),
        "valid_from": _require_str(arguments, "valid_from"),
    }
    _put_nullable_str_if_present(body, arguments, "holder_member_id")
    _put_bool_if_present(body, arguments, "statement_expected")
    _put_bool_if_present(body, arguments, "final_close_only")
    _put_nullable_str_if_present(body, arguments, "valid_to")
    path = (
        f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}"
        f"/bank-accounts/{_path_segment(account_id)}"
    )
    status, response = api.request("PUT", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PUT", path=path)
    return _wrap_ok(profile, base, "bank_account", response)
