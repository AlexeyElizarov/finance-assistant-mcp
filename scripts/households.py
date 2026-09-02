"""Household master data MCP helpers (FIN-240)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

HOUSEHOLDS_PATH = "/api/v1/households"
HOUSEHOLD_MEMBERS_PATH = "/api/v1/household-members"


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


_U0020 = "\u0020"
_ASSIGNMENT_KEYS = ("accounting_subject", "accounting_subject_id")


def strip_u0020(value: str) -> str:
    """
    Remove leading and trailing U+0020 SPACE characters only.

    :param value: Raw string
    :return: String without edge U+0020 SPACE
    """
    return value.strip(_U0020)


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


def _require_u0020_str(arguments: dict[str, Any], key: str) -> str:
    """
    Require a non-empty string after removing edge U+0020 SPACE.

    :param arguments: Raw MCP arguments (presence-preserving)
    :param key: Argument name
    :return: Value without edge U+0020 SPACE
    """
    if key not in arguments:
        raise ValueError(f"{key} is required")
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} is required")
    text = strip_u0020(str(value))
    if not text:
        raise ValueError(f"{key} is required")
    return text


def resolve_profile(arguments: dict[str, Any], session_profile: str | None) -> str:
    """
    Return effective profile after U+0020 trim (FIN-369 D-02).

    :param arguments: Raw MCP arguments
    :param session_profile: Session profile when the argument key is absent
    :return: Effective profile
    """
    if "profile" in arguments:
        selected = arguments["profile"]
    elif session_profile is not None:
        selected = session_profile
    else:
        return "prod"
    if selected is None:
        raise ValueError("profile is required")
    text = strip_u0020(str(selected))
    if not text:
        raise ValueError("profile is required")
    return text


def resolve_base(arguments: dict[str, Any], session_base: str | None) -> str:
    """
    Return effective API base after U+0020 trim (FIN-369 D-02).

    :param arguments: Raw MCP arguments
    :param session_base: Session base when the argument key is absent
    :return: Effective base URL
    """
    if "base" in arguments:
        selected = arguments["base"]
    elif session_base is not None:
        selected = session_base
    else:
        raise RuntimeError("API base is not configured")
    if selected is None:
        raise ValueError("base is required")
    text = strip_u0020(str(selected))
    if not text:
        raise ValueError("base is required")
    return text


def _reject_assignment_keys(arguments: dict[str, Any]) -> None:
    """
    Reject accounting-subject assignment keys before HTTP (FIN-369 D-06).

    :param arguments: Raw MCP arguments
    """
    for key in _ASSIGNMENT_KEYS:
        if key in arguments:
            raise ValueError(f"{key} is not allowed")


def _put_display_name_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    """
    Copy optional member display_name after U+0020 trim (FIN-369 D-05).

    Omit leaves the key out. JSON ``null`` or empty after trim is rejected.

    :param body: PUT body under construction
    :param arguments: Raw MCP arguments
    """
    if "display_name" not in arguments:
        return
    value = arguments["display_name"]
    if value is None:
        raise ValueError("display_name is required")
    if not isinstance(value, str):
        raise ValueError("display_name must be a string")
    text = strip_u0020(value)
    if not text:
        raise ValueError("display_name is required")
    body["display_name"] = text


def _collection_from_body(
    status: int,
    body: Any,
    *,
    method: str,
    path: str,
    key: str,
) -> list[Any]:
    """
    Return the list collection from a successful HTTP list body.

    :param status: HTTP status code
    :param body: Parsed response body
    :param method: HTTP method
    :param path: Request path
    :param key: Collection key
    :return: Collection array
    """
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method=method, path=path)
    collection = body.get(key)
    if not isinstance(collection, list):
        _raise_api_error(status, body, method=method, path=path)
    return collection


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


def _wrap_ok_envelope(profile: str, base: str) -> dict[str, Any]:
    return {"ok": True, "profile": profile, "base": base}


def _put_currency_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
) -> None:
    """
    Copy optional account currency into the HTTP body as-is (FIN-341).

    Omit leaves the key out. JSON ``null`` is rejected. The string is not
    stripped or case-folded.

    :param body: PUT body under construction
    :param arguments: Raw MCP arguments
    """
    if "currency" not in arguments:
        return
    value = arguments["currency"]
    if value is None:
        raise ValueError("currency cannot be null")
    if not isinstance(value, str):
        raise ValueError("currency must be a string")
    body["currency"] = value


def _with_account_row_defaults(account: dict[str, Any]) -> dict[str, Any]:
    """
    Ensure ``bank_id``, ``identifiers`` and ``currency`` keys on a row.

    :param account: Account object from HTTP
    :return: Shallow copy with defaults
    """
    row = dict(account)
    if "bank_id" not in row:
        row["bank_id"] = None
    if not isinstance(row.get("identifiers"), list):
        row["identifiers"] = []
    if "currency" not in row:
        row["currency"] = None
    return row


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
    households = _collection_from_body(
        status,
        body,
        method="GET",
        path=path,
        key="households",
    )
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
    name = _require_u0020_str(arguments, "name")
    _reject_assignment_keys(arguments)
    body: dict[str, Any] = {"name": name}
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
    members = _collection_from_body(
        status,
        body,
        method="GET",
        path=path,
        key="members",
    )
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
    _reject_assignment_keys(arguments)
    body: dict[str, Any] = {}
    _put_display_name_if_present(body, arguments)
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
    normalized: list[Any] = []
    for account in accounts:
        if not isinstance(account, dict):
            normalized.append(account)
            continue
        normalized.append(_with_account_row_defaults(account))
    return _wrap_ok(profile, base, "bank_accounts", normalized)


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
        "bank_id": _require_str(arguments, "bank_id"),
    }
    _put_nullable_str_if_present(body, arguments, "holder_member_id")
    _put_bool_if_present(body, arguments, "statement_expected")
    _put_bool_if_present(body, arguments, "final_close_only")
    _put_nullable_str_if_present(body, arguments, "valid_to")
    _put_currency_if_present(body, arguments)
    path = (
        f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}"
        f"/bank-accounts/{_path_segment(account_id)}"
    )
    status, response = api.request("PUT", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PUT", path=path)
    return _wrap_ok(profile, base, "bank_account", _with_account_row_defaults(response))


def get_household_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Read group accounting subject via ``GET .../households/{id}/accounting-subject``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_id``)
    :return: Wrapped MCP payload
    """
    household_id = _require_str(arguments, "household_id")
    path = f"{HOUSEHOLDS_PATH}/{_path_segment(household_id)}/accounting-subject"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    return _wrap_ok(profile, base, "accounting_subject", body)


def get_household_member_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Read accounting subject by member via ``GET .../accounting-subject``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_member_id``)
    :return: Wrapped MCP payload
    """
    household_member_id = _require_str(arguments, "household_member_id")
    path = (
        f"{HOUSEHOLD_MEMBERS_PATH}/{_path_segment(household_member_id)}"
        "/accounting-subject"
    )
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    return _wrap_ok(profile, base, "accounting_subject", body)


def link_household_member_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Link member to person subject via ``POST .../accounting-subject-link``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_member_id`` and ``subject_id``)
    :return: Wrapped MCP payload without entity key
    """
    household_member_id = _require_str(arguments, "household_member_id")
    if "subject_id" not in arguments:
        raise ValueError("subject_id is required")
    subject_id = arguments["subject_id"]
    if subject_id is None:
        raise ValueError("subject_id is required")
    if not isinstance(subject_id, str):
        raise ValueError("subject_id must be a string")
    path = (
        f"{HOUSEHOLD_MEMBERS_PATH}/{_path_segment(household_member_id)}"
        "/accounting-subject-link"
    )
    status, body = api.request("POST", path, data={"subject_id": subject_id})
    if status != 204:
        _raise_api_error(status, body, method="POST", path=path)
    return _wrap_ok_envelope(profile, base)


def unlink_household_member_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Unlink member accounting subject via ``DELETE .../accounting-subject-link``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``household_member_id``)
    :return: Wrapped MCP payload without entity key
    """
    household_member_id = _require_str(arguments, "household_member_id")
    path = (
        f"{HOUSEHOLD_MEMBERS_PATH}/{_path_segment(household_member_id)}"
        "/accounting-subject-link"
    )
    status, body = api.request("DELETE", path)
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok_envelope(profile, base)


def validate_household_id_argument(arguments: dict[str, Any]) -> None:
    """Require non-empty ``household_id`` path segment."""
    _require_str(arguments, "household_id")


def validate_household_member_id_argument(arguments: dict[str, Any]) -> None:
    """Require non-empty ``household_member_id`` path segment."""
    _require_str(arguments, "household_member_id")


def validate_link_household_member_accounting_subject(arguments: dict[str, Any]) -> None:
    """Require member path id and body ``subject_id`` key."""
    _require_str(arguments, "household_member_id")
    if "subject_id" not in arguments:
        raise ValueError("subject_id is required")
    if arguments["subject_id"] is None:
        raise ValueError("subject_id is required")


def validate_household_id_argument(arguments: dict[str, Any]) -> None:
    """Require non-empty ``household_id`` path segment."""
    _require_str(arguments, "household_id")


def validate_household_member_id_argument(arguments: dict[str, Any]) -> None:
    """Require non-empty ``household_member_id`` path segment."""
    _require_str(arguments, "household_member_id")


def validate_link_household_member_accounting_subject(arguments: dict[str, Any]) -> None:
    """Require member path id and body ``subject_id`` key."""
    _require_str(arguments, "household_member_id")
    if "subject_id" not in arguments:
        raise ValueError("subject_id is required")
    if arguments["subject_id"] is None:
        raise ValueError("subject_id is required")
