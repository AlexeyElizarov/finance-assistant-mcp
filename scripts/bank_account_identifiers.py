"""Bank-account identifier collection MCP helpers (FIN-321)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

IDENTIFIERS_PATH = "/api/v1/bank-account-identifiers"


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


def _path_segment(value: str) -> str:
    """
    Encode one URL path segment.

    :param value: Raw id
    :return: Encoded segment
    """
    return urllib.parse.quote(value, safe="")


def _wrap_ok(profile: str, base: str, key: str | None = None, value: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "profile": profile, "base": base}
    if key is not None:
        payload[key] = value
    return payload


def _require_non_empty_list(arguments: dict[str, Any], key: str) -> list[Any]:
    """
    Require a non-empty list argument.

    :param arguments: Raw MCP arguments
    :param key: Argument name
    :return: List value
    """
    if key not in arguments:
        raise ValueError(f"{key} is required")
    value = arguments[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    if not value:
        raise ValueError(f"{key} must be a non-empty list")
    return value


def _optional_filter_str(arguments: dict[str, Any], key: str) -> str | None:
    """
    Return a non-empty stripped filter value when the key is present.

    :param arguments: Raw MCP arguments
    :param key: Filter name
    :return: Stripped value or ``None`` when key absent
    """
    if key not in arguments:
        return None
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} must be a non-empty string")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{key} must be a non-empty string")
    return text


def list_bank_account_identifiers(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    List identifiers via ``GET /api/v1/bank-account-identifiers``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Optional filter ``bank_account_id``
    :return: Wrapped MCP payload
    """
    args = arguments or {}
    path = IDENTIFIERS_PATH
    bank_account_id = _optional_filter_str(args, "bank_account_id")
    if bank_account_id is not None:
        path = f"{path}?{urllib.parse.urlencode({'bank_account_id': bank_account_id})}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    items = body.get("bank_account_identifiers")
    if not isinstance(items, list):
        raise RuntimeError(
            "GET /bank-account-identifiers: bank_account_identifiers is not a list"
        )
    return _wrap_ok(profile, base, "bank_account_identifiers", items)


def get_bank_account_identifier(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one identifier via ``GET /api/v1/bank-account-identifiers/{identifier_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``identifier_id``)
    :return: Wrapped MCP payload
    """
    identifier_id = _require_str(arguments, "identifier_id")
    path = f"{IDENTIFIERS_PATH}/{_path_segment(identifier_id)}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    return _wrap_ok(profile, base, "bank_account_identifier", body)


def create_bank_account_identifier(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one identifier via ``POST /api/v1/bank-account-identifiers``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires account, type, value)
    :return: Wrapped MCP payload
    """
    body = {
        "bank_account_id": _require_str(arguments, "bank_account_id"),
        "identifier_type": _require_str(arguments, "identifier_type"),
        "value": _require_str(arguments, "value"),
    }
    path = IDENTIFIERS_PATH
    status, response = api.request("POST", path, data=body)
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    return _wrap_ok(profile, base, "bank_account_identifier", response)


def create_bank_account_identifiers(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-create identifiers via ``POST /api/v1/bank-account-identifiers/batch``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``bank_account_identifiers``)
    :return: Wrapped MCP payload
    """
    items = _require_non_empty_list(arguments, "bank_account_identifiers")
    path = f"{IDENTIFIERS_PATH}/batch"
    status, response = api.request(
        "POST",
        path,
        data={"bank_account_identifiers": items},
    )
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    created = response.get("bank_account_identifiers")
    if not isinstance(created, list):
        raise RuntimeError(
            "POST /bank-account-identifiers/batch: bank_account_identifiers is not a list"
        )
    return _wrap_ok(profile, base, "bank_account_identifiers", created)


def patch_bank_account_identifier(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch identifier value via ``PATCH /api/v1/bank-account-identifiers/{identifier_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``identifier_id`` and ``value``)
    :return: Wrapped MCP payload
    """
    identifier_id = _require_str(arguments, "identifier_id")
    value = _require_str(arguments, "value")
    path = f"{IDENTIFIERS_PATH}/{_path_segment(identifier_id)}"
    status, response = api.request("PATCH", path, data={"value": value})
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    return _wrap_ok(profile, base, "bank_account_identifier", response)


def patch_bank_account_identifiers(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-patch identifier values via ``PATCH /api/v1/bank-account-identifiers``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``bank_account_identifiers``)
    :return: Wrapped MCP payload
    """
    raw_items = _require_non_empty_list(arguments, "bank_account_identifiers")
    items: list[dict[str, str]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"bank_account_identifiers[{index}] must be an object")
        item_id = raw.get("id")
        if item_id is None or not str(item_id).strip():
            raise ValueError(f"bank_account_identifiers[{index}].id is required")
        value = raw.get("value")
        if value is None or not str(value).strip():
            raise ValueError(f"bank_account_identifiers[{index}].value is required")
        items.append({"id": str(item_id).strip(), "value": str(value).strip()})
    path = IDENTIFIERS_PATH
    status, response = api.request(
        "PATCH",
        path,
        data={"bank_account_identifiers": items},
    )
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    patched = response.get("bank_account_identifiers")
    if not isinstance(patched, list):
        raise RuntimeError(
            "PATCH /bank-account-identifiers: bank_account_identifiers is not a list"
        )
    return _wrap_ok(profile, base, "bank_account_identifiers", patched)


def delete_bank_account_identifier(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete one identifier via ``DELETE /api/v1/bank-account-identifiers/{identifier_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``identifier_id``)
    :return: Wrapped MCP payload without entity key
    """
    identifier_id = _require_str(arguments, "identifier_id")
    path = f"{IDENTIFIERS_PATH}/{_path_segment(identifier_id)}"
    status, body = api.request("DELETE", path)
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)


def delete_bank_account_identifiers(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-delete identifiers via ``DELETE /api/v1/bank-account-identifiers``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``ids``)
    :return: Wrapped MCP payload without entity key
    """
    raw_ids = _require_non_empty_list(arguments, "ids")
    ids: list[str] = []
    for index, raw in enumerate(raw_ids):
        if raw is None or not str(raw).strip():
            raise ValueError(f"ids[{index}] is required")
        ids.append(str(raw).strip())
    path = IDENTIFIERS_PATH
    status, body = api.request("DELETE", path, data={"ids": ids})
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)
