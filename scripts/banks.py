"""Banks catalogue MCP helpers (FIN-293)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

BANKS_PATH = "/api/v1/banks"


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


def _put_nullable_str_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
    key: str,
) -> None:
    """
    Copy optional nullable string into body when present.

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


def _bank_write_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Build create/patch field body from MCP arguments (presence-preserving).

    :param arguments: Raw MCP arguments
    :return: JSON body fragment
    """
    body: dict[str, Any] = {}
    if "display_name" in arguments:
        value = arguments["display_name"]
        if value is None:
            body["display_name"] = None
        else:
            body["display_name"] = str(value).strip()
    _put_nullable_str_if_present(body, arguments, "bic")
    return body


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


def list_banks(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    List banks via ``GET /api/v1/banks``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Unused; accepted for handler uniformity
    :return: Wrapped MCP payload
    """
    del arguments
    path = BANKS_PATH
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    banks = body.get("banks")
    if not isinstance(banks, list):
        raise RuntimeError("GET /banks: banks is not a list")
    return _wrap_ok(profile, base, "banks", banks)


def get_bank(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one bank via ``GET /api/v1/banks/{bank_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``bank_id``)
    :return: Wrapped MCP payload
    """
    bank_id = _require_str(arguments, "bank_id")
    path = f"{BANKS_PATH}/{_path_segment(bank_id)}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    return _wrap_ok(profile, base, "bank", body)


def create_bank(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one bank via ``POST /api/v1/banks``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``display_name``)
    :return: Wrapped MCP payload
    """
    display_name = _require_str(arguments, "display_name")
    body: dict[str, Any] = {"display_name": display_name}
    _put_nullable_str_if_present(body, arguments, "bic")
    path = BANKS_PATH
    status, response = api.request("POST", path, data=body)
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    return _wrap_ok(profile, base, "bank", response)


def create_banks(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-create banks via ``POST /api/v1/banks/batch``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``banks``)
    :return: Wrapped MCP payload
    """
    raw_banks = _require_non_empty_list(arguments, "banks")
    banks_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_banks):
        if not isinstance(raw, dict):
            raise ValueError(f"banks[{index}] must be an object")
        banks_body.append(_bank_write_fields(raw))
    path = f"{BANKS_PATH}/batch"
    status, response = api.request("POST", path, data={"banks": banks_body})
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    banks = response.get("banks")
    if not isinstance(banks, list):
        raise RuntimeError("POST /banks/batch: banks is not a list")
    return _wrap_ok(profile, base, "banks", banks)


def patch_bank(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch one bank via ``PATCH /api/v1/banks/{bank_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``bank_id`` and at least one field)
    :return: Wrapped MCP payload
    """
    bank_id = _require_str(arguments, "bank_id")
    if "display_name" not in arguments and "bic" not in arguments:
        raise ValueError("at least one of display_name or bic is required")
    body = _bank_write_fields(arguments)
    path = f"{BANKS_PATH}/{_path_segment(bank_id)}"
    status, response = api.request("PATCH", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    return _wrap_ok(profile, base, "bank", response)


def patch_banks(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-patch banks via ``PATCH /api/v1/banks``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``banks`` with ``id``)
    :return: Wrapped MCP payload
    """
    raw_banks = _require_non_empty_list(arguments, "banks")
    banks_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_banks):
        if not isinstance(raw, dict):
            raise ValueError(f"banks[{index}] must be an object")
        item_id = raw.get("id")
        if item_id is None or not str(item_id).strip():
            raise ValueError(f"banks[{index}].id is required")
        item = {"id": str(item_id).strip()}
        item.update(_bank_write_fields(raw))
        banks_body.append(item)
    path = BANKS_PATH
    status, response = api.request("PATCH", path, data={"banks": banks_body})
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    banks = response.get("banks")
    if not isinstance(banks, list):
        raise RuntimeError("PATCH /banks: banks is not a list")
    return _wrap_ok(profile, base, "banks", banks)


def delete_bank(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete one bank via ``DELETE /api/v1/banks/{bank_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``bank_id``)
    :return: Wrapped MCP payload without entity key
    """
    bank_id = _require_str(arguments, "bank_id")
    path = f"{BANKS_PATH}/{_path_segment(bank_id)}"
    status, body = api.request("DELETE", path)
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)


def delete_banks(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-delete banks via ``DELETE /api/v1/banks``.

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
    path = BANKS_PATH
    status, body = api.request("DELETE", path, data={"ids": ids})
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)
