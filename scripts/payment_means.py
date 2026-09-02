"""Payment instruments and payment-means fund assignments MCP helpers (FIN-286, FIN-313)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

INSTRUMENTS_PATH = "/api/v1/payment-instruments"
ASSIGNMENTS_PATH = "/api/v1/payment-means-fund-assignments"

_INSTRUMENT_PATCH_KEYS = (
    "display_name",
    "payment_network",
    "settlement_class",
    "pan_last4",
    "holder_id",
    "valid_from",
    "valid_to",
    "issuer_expiry",
)
_INSTRUMENT_NULLABLE_KEYS = (
    "payment_network",
    "settlement_class",
    "pan_last4",
    "holder_id",
    "valid_from",
    "valid_to",
    "issuer_expiry",
)
_INSTRUMENT_PATCH_KEYS_TEXT = (
    "display_name, payment_network, settlement_class, pan_last4, "
    "holder_id, valid_from, valid_to, issuer_expiry"
)
_ASSIGNMENT_PATCH_KEYS = ("valid_from", "valid_to")


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

    Empty string is sent as stripped empty string (not converted to ``null``).

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


def _put_str_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
    key: str,
) -> None:
    """
    Copy optional non-nullable string into body when present.

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


def _put_non_empty_str_if_present(
    body: dict[str, Any],
    arguments: dict[str, Any],
    key: str,
) -> None:
    """
    Copy a present string that must stay non-empty after strip.

    :param body: Request body under construction
    :param arguments: Raw MCP arguments
    :param key: Argument name
    """
    if key not in arguments:
        return
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} is required")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{key} is required")
    body[key] = text


def _require_instrument_patch_keys(
    arguments: dict[str, Any],
    *,
    index: int | None = None,
) -> None:
    """
    Require at least one mutable instrument field before HTTP.

    :param arguments: MCP args or batch element
    :param index: Batch element index for the error prefix
    """
    if any(key in arguments for key in _INSTRUMENT_PATCH_KEYS):
        return
    prefix = f"payment_instruments[{index}]: " if index is not None else ""
    raise ValueError(
        f"{prefix}at least one of {_INSTRUMENT_PATCH_KEYS_TEXT} is required"
    )


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


def _instrument_create_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Build create body for one payment instrument.

    :param arguments: Raw MCP arguments or batch element
    :return: JSON body
    """
    body: dict[str, Any] = {
        "bank_account_id": _require_str(arguments, "bank_account_id"),
        "display_name": _require_str(arguments, "display_name"),
        "instrument_type": _require_str(arguments, "instrument_type"),
    }
    for key in _INSTRUMENT_NULLABLE_KEYS:
        _put_nullable_str_if_present(body, arguments, key)
    return body


def _instrument_patch_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Build patch body fields for one payment instrument (no id).

    :param arguments: Raw MCP arguments or batch element
    :return: JSON body fragment
    """
    body: dict[str, Any] = {}
    _put_non_empty_str_if_present(body, arguments, "display_name")
    for key in _INSTRUMENT_NULLABLE_KEYS:
        _put_nullable_str_if_present(body, arguments, key)
    return body


def _assignment_create_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Build create body for one payment-means fund assignment.

    :param arguments: Raw MCP arguments or batch element
    :return: JSON body
    """
    body: dict[str, Any] = {
        "means_type": _require_str(arguments, "means_type"),
        "means_id": _require_str(arguments, "means_id"),
        "fund_id": _require_str(arguments, "fund_id"),
        "valid_from": _require_str(arguments, "valid_from"),
    }
    _put_nullable_str_if_present(body, arguments, "valid_to")
    return body


def _assignment_patch_fields(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Build patch body fields for one assignment (no id).

    :param arguments: Raw MCP arguments or batch element
    :return: JSON body fragment
    """
    body: dict[str, Any] = {}
    _put_str_if_present(body, arguments, "valid_from")
    _put_nullable_str_if_present(body, arguments, "valid_to")
    return body


def list_payment_instruments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    List payment instruments via ``GET /api/v1/payment-instruments``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Optional filters ``bank_account_id`` / ``household_id``
    :return: Wrapped MCP payload
    """
    args = arguments or {}
    query: dict[str, str] = {}
    bank_account_id = _optional_filter_str(args, "bank_account_id")
    if bank_account_id is not None:
        query["bank_account_id"] = bank_account_id
    household_id = _optional_filter_str(args, "household_id")
    if household_id is not None:
        query["household_id"] = household_id
    path = INSTRUMENTS_PATH
    if query:
        path = f"{path}?{urllib.parse.urlencode(query)}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    items = body.get("payment_instruments")
    if not isinstance(items, list):
        raise RuntimeError("GET /payment-instruments: payment_instruments is not a list")
    return _wrap_ok(profile, base, "payment_instruments", items)


def get_payment_instrument(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one payment instrument via ``GET /api/v1/payment-instruments/{instrument_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``instrument_id``)
    :return: Wrapped MCP payload
    """
    instrument_id = _require_str(arguments, "instrument_id")
    path = f"{INSTRUMENTS_PATH}/{_path_segment(instrument_id)}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    return _wrap_ok(profile, base, "payment_instrument", body)


def create_payment_instrument(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one payment instrument via ``POST /api/v1/payment-instruments``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP create fields
    :return: Wrapped MCP payload
    """
    body = _instrument_create_fields(arguments)
    path = INSTRUMENTS_PATH
    status, response = api.request("POST", path, data=body)
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    return _wrap_ok(profile, base, "payment_instrument", response)


def create_payment_instruments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-create payment instruments via ``POST /api/v1/payment-instruments/batch``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``payment_instruments``)
    :return: Wrapped MCP payload
    """
    raw_items = _require_non_empty_list(arguments, "payment_instruments")
    items_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"payment_instruments[{index}] must be an object")
        items_body.append(_instrument_create_fields(raw))
    path = f"{INSTRUMENTS_PATH}/batch"
    status, response = api.request("POST", path, data={"payment_instruments": items_body})
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    items = response.get("payment_instruments")
    if not isinstance(items, list):
        raise RuntimeError("POST /payment-instruments/batch: payment_instruments is not a list")
    return _wrap_ok(profile, base, "payment_instruments", items)


def patch_payment_instrument(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch one payment instrument via ``PATCH /api/v1/payment-instruments/{instrument_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``instrument_id`` and at least one field)
    :return: Wrapped MCP payload
    """
    instrument_id = _require_str(arguments, "instrument_id")
    _require_instrument_patch_keys(arguments)
    body = _instrument_patch_fields(arguments)
    path = f"{INSTRUMENTS_PATH}/{_path_segment(instrument_id)}"
    status, response = api.request("PATCH", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    return _wrap_ok(profile, base, "payment_instrument", response)


def patch_payment_instruments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-patch payment instruments via ``PATCH /api/v1/payment-instruments``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``payment_instruments`` with ``id``)
    :return: Wrapped MCP payload
    """
    raw_items = _require_non_empty_list(arguments, "payment_instruments")
    items_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"payment_instruments[{index}] must be an object")
        item_id = raw.get("id")
        if item_id is None or not str(item_id).strip():
            raise ValueError(f"payment_instruments[{index}].id is required")
        _require_instrument_patch_keys(raw, index=index)
        item = {"id": str(item_id).strip()}
        item.update(_instrument_patch_fields(raw))
        items_body.append(item)
    path = INSTRUMENTS_PATH
    status, response = api.request("PATCH", path, data={"payment_instruments": items_body})
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    items = response.get("payment_instruments")
    if not isinstance(items, list):
        raise RuntimeError("PATCH /payment-instruments: payment_instruments is not a list")
    return _wrap_ok(profile, base, "payment_instruments", items)


def delete_payment_instrument(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete one payment instrument via ``DELETE /api/v1/payment-instruments/{instrument_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``instrument_id``)
    :return: Wrapped MCP payload without entity key
    """
    instrument_id = _require_str(arguments, "instrument_id")
    path = f"{INSTRUMENTS_PATH}/{_path_segment(instrument_id)}"
    status, body = api.request("DELETE", path)
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)


def delete_payment_instruments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-delete payment instruments via ``DELETE /api/v1/payment-instruments``.

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
    path = INSTRUMENTS_PATH
    status, body = api.request("DELETE", path, data={"ids": ids})
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)


def list_payment_means_fund_assignments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    List assignments via ``GET /api/v1/payment-means-fund-assignments``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Optional filters ``means_type`` / ``means_id`` / ``fund_id``
    :return: Wrapped MCP payload
    """
    args = arguments or {}
    query: dict[str, str] = {}
    for key in ("means_type", "means_id", "fund_id"):
        value = _optional_filter_str(args, key)
        if value is not None:
            query[key] = value
    path = ASSIGNMENTS_PATH
    if query:
        path = f"{path}?{urllib.parse.urlencode(query)}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    items = body.get("payment_means_fund_assignments")
    if not isinstance(items, list):
        raise RuntimeError(
            "GET /payment-means-fund-assignments: "
            "payment_means_fund_assignments is not a list"
        )
    return _wrap_ok(profile, base, "payment_means_fund_assignments", items)


def get_payment_means_fund_assignment(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one assignment via ``GET /api/v1/payment-means-fund-assignments/{assignment_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``assignment_id``)
    :return: Wrapped MCP payload
    """
    assignment_id = _require_str(arguments, "assignment_id")
    path = f"{ASSIGNMENTS_PATH}/{_path_segment(assignment_id)}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    return _wrap_ok(profile, base, "payment_means_fund_assignment", body)


def create_payment_means_fund_assignment(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one assignment via ``POST /api/v1/payment-means-fund-assignments``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP create fields
    :return: Wrapped MCP payload
    """
    body = _assignment_create_fields(arguments)
    path = ASSIGNMENTS_PATH
    status, response = api.request("POST", path, data=body)
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    return _wrap_ok(profile, base, "payment_means_fund_assignment", response)


def create_payment_means_fund_assignments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-create assignments via ``POST /api/v1/payment-means-fund-assignments/batch``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``payment_means_fund_assignments``)
    :return: Wrapped MCP payload
    """
    raw_items = _require_non_empty_list(arguments, "payment_means_fund_assignments")
    items_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"payment_means_fund_assignments[{index}] must be an object")
        items_body.append(_assignment_create_fields(raw))
    path = f"{ASSIGNMENTS_PATH}/batch"
    status, response = api.request(
        "POST",
        path,
        data={"payment_means_fund_assignments": items_body},
    )
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    items = response.get("payment_means_fund_assignments")
    if not isinstance(items, list):
        raise RuntimeError(
            "POST /payment-means-fund-assignments/batch: "
            "payment_means_fund_assignments is not a list"
        )
    return _wrap_ok(profile, base, "payment_means_fund_assignments", items)


def patch_payment_means_fund_assignment(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch one assignment via ``PATCH /api/v1/payment-means-fund-assignments/{assignment_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``assignment_id`` and at least one field)
    :return: Wrapped MCP payload
    """
    assignment_id = _require_str(arguments, "assignment_id")
    if not any(key in arguments for key in _ASSIGNMENT_PATCH_KEYS):
        raise ValueError("at least one of valid_from or valid_to is required")
    body = _assignment_patch_fields(arguments)
    path = f"{ASSIGNMENTS_PATH}/{_path_segment(assignment_id)}"
    status, response = api.request("PATCH", path, data=body)
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    return _wrap_ok(profile, base, "payment_means_fund_assignment", response)


def patch_payment_means_fund_assignments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-patch assignments via ``PATCH /api/v1/payment-means-fund-assignments``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty list with ``id``)
    :return: Wrapped MCP payload
    """
    raw_items = _require_non_empty_list(arguments, "payment_means_fund_assignments")
    items_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"payment_means_fund_assignments[{index}] must be an object")
        item_id = raw.get("id")
        if item_id is None or not str(item_id).strip():
            raise ValueError(f"payment_means_fund_assignments[{index}].id is required")
        item = {"id": str(item_id).strip()}
        item.update(_assignment_patch_fields(raw))
        items_body.append(item)
    path = ASSIGNMENTS_PATH
    status, response = api.request(
        "PATCH",
        path,
        data={"payment_means_fund_assignments": items_body},
    )
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    items = response.get("payment_means_fund_assignments")
    if not isinstance(items, list):
        raise RuntimeError(
            "PATCH /payment-means-fund-assignments: "
            "payment_means_fund_assignments is not a list"
        )
    return _wrap_ok(profile, base, "payment_means_fund_assignments", items)


def delete_payment_means_fund_assignment(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete one assignment via ``DELETE /api/v1/payment-means-fund-assignments/{assignment_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``assignment_id``)
    :return: Wrapped MCP payload without entity key
    """
    assignment_id = _require_str(arguments, "assignment_id")
    path = f"{ASSIGNMENTS_PATH}/{_path_segment(assignment_id)}"
    status, body = api.request("DELETE", path)
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)


def delete_payment_means_fund_assignments(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-delete assignments via ``DELETE /api/v1/payment-means-fund-assignments``.

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
    path = ASSIGNMENTS_PATH
    status, body = api.request("DELETE", path, data={"ids": ids})
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)
