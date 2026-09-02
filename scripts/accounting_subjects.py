"""Accounting subjects catalogue MCP helpers (FIN-366)."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from typing import Any

from finance_api_client import ApiClient

ACCOUNTING_SUBJECTS_PATH = "/api/v1/accounting-subjects"


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


def resolve_profile(arguments: dict[str, Any]) -> str:
    """
    Return effective data profile after handler checks.

    :param arguments: Raw MCP arguments
    :return: Profile name; omitted key defaults to ``prod``
    """
    if "profile" not in arguments:
        return "prod"
    value = arguments["profile"]
    if value is None:
        raise ValueError("profile is required")
    text = str(value).strip()
    if not text:
        raise ValueError("profile is required")
    return text


def resolve_base(arguments: dict[str, Any]) -> str | None:
    """
    Return URL override after handler checks, or ``None`` for session default.

    :param arguments: Raw MCP arguments
    :return: Stripped URL, or ``None`` when omitted
    """
    if "base" not in arguments:
        return None
    value = arguments["base"]
    if value is None:
        raise ValueError("base is required")
    text = str(value).strip()
    if not text:
        raise ValueError("base is required")
    return text


def prepare_request(
    arguments: dict[str, Any],
    validate: Callable[[dict[str, Any]], None],
) -> tuple[str, str | None]:
    """
    Resolve profile and base, then run handler validation.

    :param arguments: Raw MCP arguments
    :param validate: Callable taking ``arguments``
    :return: Effective profile and optional base override
    """
    profile = resolve_profile(arguments)
    base = resolve_base(arguments)
    validate(arguments)
    return profile, base


def _raise_api_error(status: int, body: Any, *, method: str, path: str) -> None:
    raise RuntimeError(format_api_error(status, body, method=method, path=path))


def _path_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _with_query(path: str, params: dict[str, str]) -> str:
    if not params:
        return path
    return f"{path}?{urllib.parse.urlencode(params)}"


def _wrap_ok(
    profile: str,
    base: str,
    key: str | None = None,
    value: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "profile": profile, "base": base}
    if key is not None:
        payload[key] = value
    return payload


def _require_key(arguments: dict[str, Any], key: str) -> Any:
    if key not in arguments:
        raise ValueError(f"{key} is required")
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _require_path_id(arguments: dict[str, Any], key: str) -> str:
    value = _require_key(arguments, key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    if value == "":
        raise ValueError(f"{key} is required")
    return value


def _require_body_str(arguments: dict[str, Any], key: str) -> str:
    value = _require_key(arguments, key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _require_non_empty_list(arguments: dict[str, Any], key: str) -> list[Any]:
    if key not in arguments:
        raise ValueError(f"{key} is required")
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} is required")
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    if not value:
        raise ValueError(f"{key} must be a non-empty list")
    return value


def _create_item_body(raw: dict[str, Any], *, prefix: str = "") -> dict[str, Any]:
    for field in ("subject_type", "display_name"):
        label = f"{prefix}{field}"
        if field not in raw:
            raise ValueError(f"{label} is required")
        if raw[field] is None:
            raise ValueError(f"{label} is required")
        if not isinstance(raw[field], str):
            raise ValueError(f"{label} must be a string")
    body: dict[str, Any] = {
        "subject_type": raw["subject_type"],
        "display_name": raw["display_name"],
    }
    if "household_id" in raw:
        if raw["household_id"] is None:
            raise ValueError(f"{prefix}household_id is required")
        if not isinstance(raw["household_id"], str):
            raise ValueError(f"{prefix}household_id must be a string")
        body["household_id"] = raw["household_id"]
    return body


def _patch_item_body(raw: dict[str, Any], *, prefix: str = "") -> dict[str, Any]:
    for field in ("id", "display_name"):
        label = f"{prefix}{field}"
        if field not in raw:
            raise ValueError(f"{label} is required")
        if raw[field] is None:
            raise ValueError(f"{label} is required")
        if not isinstance(raw[field], str):
            raise ValueError(f"{label} must be a string")
    return {"id": raw["id"], "display_name": raw["display_name"]}


def validate_list_arguments(arguments: dict[str, Any]) -> None:
    """Reject explicit null filter."""
    if "subject_type" in arguments and arguments["subject_type"] is None:
        raise ValueError("subject_type is required")


def validate_noop(_arguments: dict[str, Any]) -> None:
    """Skip extra handler checks."""


def validate_create(arguments: dict[str, Any]) -> None:
    _require_body_str(arguments, "subject_type")
    _require_body_str(arguments, "display_name")
    if "household_id" in arguments and arguments["household_id"] is None:
        raise ValueError("household_id is required")


def validate_patch(arguments: dict[str, Any]) -> None:
    _require_path_id(arguments, "subject_id")
    _require_body_str(arguments, "display_name")


def validate_get(arguments: dict[str, Any]) -> None:
    _require_path_id(arguments, "subject_id")


def validate_delete(arguments: dict[str, Any]) -> None:
    _require_path_id(arguments, "subject_id")


def validate_batch_create(arguments: dict[str, Any]) -> None:
    raw_items = _require_non_empty_list(arguments, "accounting_subjects")
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"accounting_subjects[{index}] must be an object")
        _create_item_body(raw, prefix=f"accounting_subjects[{index}].")


def validate_batch_patch(arguments: dict[str, Any]) -> None:
    raw_items = _require_non_empty_list(arguments, "accounting_subjects")
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"accounting_subjects[{index}] must be an object")
        _patch_item_body(raw, prefix=f"accounting_subjects[{index}].")


def validate_batch_delete(arguments: dict[str, Any]) -> None:
    raw_ids = _require_non_empty_list(arguments, "ids")
    for index, raw in enumerate(raw_ids):
        if raw is None:
            raise ValueError(f"ids[{index}] is required")
        if not isinstance(raw, str):
            raise ValueError(f"ids[{index}] must be a string")
        if raw == "":
            raise ValueError(f"ids[{index}] is required")


def list_accounting_subjects(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List accounting subjects via ``GET /api/v1/accounting-subjects``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (optional ``subject_type`` filter)
    :return: Wrapped MCP payload
    """
    params: dict[str, str] = {}
    if "subject_type" in arguments:
        value = arguments["subject_type"]
        if not isinstance(value, str):
            raise ValueError("subject_type must be a string")
        params["subject_type"] = value
    path = _with_query(ACCOUNTING_SUBJECTS_PATH, params)
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    subjects = body.get("accounting_subjects")
    if not isinstance(subjects, list):
        raise RuntimeError("GET /accounting-subjects: accounting_subjects is not a list")
    return _wrap_ok(profile, base, "accounting_subjects", subjects)


def get_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one accounting subject via ``GET /api/v1/accounting-subjects/{subject_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``subject_id``)
    :return: Wrapped MCP payload
    """
    subject_id = _require_path_id(arguments, "subject_id")
    path = f"{ACCOUNTING_SUBJECTS_PATH}/{_path_segment(subject_id)}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    return _wrap_ok(profile, base, "accounting_subject", body)


def create_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one accounting subject via ``POST /api/v1/accounting-subjects``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``subject_type`` and ``display_name``)
    :return: Wrapped MCP payload
    """
    body = _create_item_body(arguments)
    path = ACCOUNTING_SUBJECTS_PATH
    status, response = api.request("POST", path, data=body)
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    return _wrap_ok(profile, base, "accounting_subject", response)


def create_accounting_subjects(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-create accounting subjects via ``POST /api/v1/accounting-subjects/batch``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``accounting_subjects``)
    :return: Wrapped MCP payload
    """
    raw_items = _require_non_empty_list(arguments, "accounting_subjects")
    items_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"accounting_subjects[{index}] must be an object")
        items_body.append(_create_item_body(raw, prefix=f"accounting_subjects[{index}]."))
    path = f"{ACCOUNTING_SUBJECTS_PATH}/batch"
    status, response = api.request("POST", path, data={"accounting_subjects": items_body})
    if status != 201 or not isinstance(response, dict):
        _raise_api_error(status, response, method="POST", path=path)
    subjects = response.get("accounting_subjects")
    if not isinstance(subjects, list):
        raise RuntimeError("POST /accounting-subjects/batch: accounting_subjects is not a list")
    return _wrap_ok(profile, base, "accounting_subjects", subjects)


def patch_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch one accounting subject via ``PATCH /api/v1/accounting-subjects/{subject_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``subject_id`` and ``display_name``)
    :return: Wrapped MCP payload
    """
    subject_id = _require_path_id(arguments, "subject_id")
    display_name = _require_body_str(arguments, "display_name")
    path = f"{ACCOUNTING_SUBJECTS_PATH}/{_path_segment(subject_id)}"
    status, response = api.request("PATCH", path, data={"display_name": display_name})
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    return _wrap_ok(profile, base, "accounting_subject", response)


def patch_accounting_subjects(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-patch accounting subjects via ``PATCH /api/v1/accounting-subjects``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``accounting_subjects``)
    :return: Wrapped MCP payload
    """
    raw_items = _require_non_empty_list(arguments, "accounting_subjects")
    items_body: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"accounting_subjects[{index}] must be an object")
        items_body.append(_patch_item_body(raw, prefix=f"accounting_subjects[{index}]."))
    path = ACCOUNTING_SUBJECTS_PATH
    status, response = api.request("PATCH", path, data={"accounting_subjects": items_body})
    if status != 200 or not isinstance(response, dict):
        _raise_api_error(status, response, method="PATCH", path=path)
    subjects = response.get("accounting_subjects")
    if not isinstance(subjects, list):
        raise RuntimeError("PATCH /accounting-subjects: accounting_subjects is not a list")
    return _wrap_ok(profile, base, "accounting_subjects", subjects)


def delete_accounting_subject(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete one accounting subject via ``DELETE /api/v1/accounting-subjects/{subject_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires ``subject_id``)
    :return: Wrapped MCP payload without entity key
    """
    subject_id = _require_path_id(arguments, "subject_id")
    path = f"{ACCOUNTING_SUBJECTS_PATH}/{_path_segment(subject_id)}"
    status, body = api.request("DELETE", path)
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)


def delete_accounting_subjects(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-delete accounting subjects via ``DELETE /api/v1/accounting-subjects``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP args (requires non-empty ``ids``)
    :return: Wrapped MCP payload without entity key
    """
    raw_ids = _require_non_empty_list(arguments, "ids")
    ids: list[str] = []
    for index, raw in enumerate(raw_ids):
        if raw is None:
            raise ValueError(f"ids[{index}] is required")
        if not isinstance(raw, str):
            raise ValueError(f"ids[{index}] must be a string")
        if raw == "":
            raise ValueError(f"ids[{index}] is required")
        ids.append(raw)
    path = ACCOUNTING_SUBJECTS_PATH
    status, body = api.request("DELETE", path, data={"ids": ids})
    if status != 204:
        _raise_api_error(status, body, method="DELETE", path=path)
    return _wrap_ok(profile, base)
