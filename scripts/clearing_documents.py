"""Clearing-documents MCP helpers (FIN-355)."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from finance_api_client import ApiClient

DOCUMENTS_PATH = "/api/v1/clearing-documents"

_LIST_FILTERS = (
    "line_id",
    "document_type",
    "creditor_subject_id",
    "debtor_subject_id",
    "clearing_currency",
    "status",
)
_ITEM_KEYS = (
    "debit_credit_indicator",
    "clearing_amount",
    "clearing_date",
    "line_id",
)
_CREATE_BODY_KEYS = (
    "document_type",
    "creditor_subject_id",
    "debtor_subject_id",
    "clearing_currency",
    "items",
)
_PATCH_BODY_KEYS = ("status", "status_date", "comment")
_COMMON_KEYS = ("profile", "base")

_LIST_KEYS = _COMMON_KEYS + _LIST_FILTERS
_GET_KEYS = _COMMON_KEYS + ("document_id",)
_CREATE_KEYS = _COMMON_KEYS + (
    "allow_closed",
    "document_type",
    "creditor_subject_id",
    "debtor_subject_id",
    "clearing_currency",
    "items",
)
_BATCH_CREATE_KEYS = _COMMON_KEYS + ("allow_closed", "clearing_documents")
_PATCH_KEYS = _COMMON_KEYS + (
    "document_id",
    "allow_closed",
    "status",
    "status_date",
    "comment",
)
_DELETE_ONE_KEYS = _COMMON_KEYS + ("document_id", "allow_closed")
_DELETE_BATCH_KEYS = _COMMON_KEYS + ("allow_closed", "ids")
_CREATE_ITEM_KEYS = _COMMON_KEYS + (
    "document_id",
    "allow_closed",
    "debit_credit_indicator",
    "clearing_amount",
    "clearing_date",
    "line_id",
)
_LIST_ITEMS_KEYS = _COMMON_KEYS + ("document_id",)
_GET_ITEM_KEYS = _COMMON_KEYS + ("document_id", "item_id")
_PATCH_ITEM_KEYS = _COMMON_KEYS + (
    "document_id",
    "item_id",
    "allow_closed",
    "line_id",
)
_DELETE_ITEM_KEYS = _COMMON_KEYS + ("document_id", "item_id", "allow_closed")


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


def _unexpected(status: int, body: Any, *, method: str, path: str) -> None:
    raise RuntimeError(f"{method} {path} -> HTTP {status}: {body}")


def _has_error_code(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and isinstance(body.get("error"), dict)
        and "code" in body["error"]
    )


def _raise_http_outcome(
    status: int,
    body: Any,
    *,
    method: str,
    path: str,
    expected_status: int,
    body_ok: bool,
) -> None:
    """
    Classify HTTP outcome (FIN-355 D-08) and raise on non-success.

    :param status: HTTP status
    :param body: Response body
    :param method: HTTP method
    :param path: Request path including query
    :param expected_status: Success status for this operation
    :param body_ok: Whether the body matches the success shape
    """
    if status == expected_status and body_ok:
        return
    if status == 422 and _has_error_code(body):
        _raise_api_error(status, body, method=method, path=path)
    if status >= 400 and status != 422 and _has_error_code(body):
        _raise_api_error(status, body, method=method, path=path)
    _unexpected(status, body, method=method, path=path)


def _path_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _with_query(path: str, params: list[tuple[str, str]]) -> str:
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


def _reject_unknown_keys(arguments: dict[str, Any], allowed: tuple[str, ...]) -> None:
    extra = [key for key in arguments if key not in allowed]
    if extra:
        raise ValueError(f"unknown argument: {extra[0]}")


def _require_present(arguments: dict[str, Any], key: str) -> Any:
    if key not in arguments:
        raise ValueError(f"{key} is required")
    value = arguments[key]
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = _require_present(arguments, key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _require_path_id(arguments: dict[str, Any], key: str) -> str:
    value = _require_str(arguments, key)
    if value == "":
        raise ValueError(f"{key} is required")
    return value


def _require_list(arguments: dict[str, Any], key: str) -> list[Any]:
    value = _require_present(arguments, key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _require_non_empty_list(arguments: dict[str, Any], key: str) -> list[Any]:
    value = _require_list(arguments, key)
    if not value:
        raise ValueError(f"{key} must be a non-empty list")
    return value


def _copy_present_str(source: dict[str, Any], dest: dict[str, Any], key: str) -> None:
    if key not in source:
        return
    value = source[key]
    if value is None:
        raise ValueError(f"{key} is required")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    dest[key] = value


def _reject_unknown_nested(obj: dict[str, Any], allowed: tuple[str, ...]) -> None:
    extra = [key for key in obj if key not in allowed]
    if extra:
        raise ValueError(f"unknown argument: {extra[0]}")


def _build_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        raise ValueError("items must be a list")
    items: list[dict[str, Any]] = []
    for element in raw_items:
        if not isinstance(element, dict):
            raise ValueError("items element must be an object")
        _reject_unknown_nested(element, _ITEM_KEYS)
        item: dict[str, Any] = {}
        for key in _ITEM_KEYS:
            _copy_present_str(element, item, key)
        items.append(item)
    return items


def _build_create_body(
    source: dict[str, Any],
    *,
    require_type_and_items: bool,
    reject_unknown: bool,
) -> dict[str, Any]:
    if reject_unknown:
        _reject_unknown_nested(source, _CREATE_BODY_KEYS)
    if require_type_and_items:
        _require_str(source, "document_type")
        _require_present(source, "items")
    body: dict[str, Any] = {}
    _copy_present_str(source, body, "document_type")
    _copy_present_str(source, body, "creditor_subject_id")
    _copy_present_str(source, body, "debtor_subject_id")
    _copy_present_str(source, body, "clearing_currency")
    if "items" in source:
        if source["items"] is None:
            raise ValueError("items is required")
        body["items"] = _build_items(source["items"])
    return body


def _allow_closed_params(arguments: dict[str, Any]) -> list[tuple[str, str]]:
    if "allow_closed" not in arguments:
        return []
    value = arguments["allow_closed"]
    if not isinstance(value, bool):
        raise ValueError("allow_closed must be a boolean")
    return [("allow_closed", "true" if value else "false")]


def _list_query(arguments: dict[str, Any]) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    for key in _LIST_FILTERS:
        if key not in arguments:
            continue
        value = arguments[key]
        if value is None:
            raise ValueError(f"{key} is required")
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        params.append((key, value))
    return params


def _ids_body(arguments: dict[str, Any]) -> list[str]:
    raw_ids = _require_non_empty_list(arguments, "ids")
    ids: list[str] = []
    for element in raw_ids:
        if not isinstance(element, str) or element == "":
            raise ValueError("ids element must be a non-empty string")
        ids.append(element)
    return ids


def _request(
    api: ApiClient,
    method: str,
    path: str,
    *,
    data: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    if data is None:
        return api.request(method, path)
    return api.request(method, path, data=data)


def list_clearing_documents(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List clearing documents via ``GET /api/v1/clearing-documents``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _LIST_KEYS)
    path = _with_query(DOCUMENTS_PATH, _list_query(arguments))
    status, body = _request(api, "GET", path)
    body_ok = isinstance(body, dict) and isinstance(body.get("clearing_documents"), list)
    _raise_http_outcome(
        status,
        body,
        method="GET",
        path=path,
        expected_status=200,
        body_ok=body_ok,
    )
    documents = body["clearing_documents"] if isinstance(body, dict) else []
    return _wrap_ok(profile, base, "clearing_documents", documents)


def get_clearing_document(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one clearing document via ``GET /api/v1/clearing-documents/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _GET_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    path = f"{DOCUMENTS_PATH}/{_path_segment(document_id)}"
    status, body = _request(api, "GET", path)
    _raise_http_outcome(
        status,
        body,
        method="GET",
        path=path,
        expected_status=200,
        body_ok=isinstance(body, dict),
    )
    return _wrap_ok(profile, base, "clearing_document", body)


def create_clearing_document(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one clearing document via ``POST /api/v1/clearing-documents``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _CREATE_KEYS)
    body = _build_create_body(
        arguments, require_type_and_items=True, reject_unknown=False
    )
    path = _with_query(DOCUMENTS_PATH, _allow_closed_params(arguments))
    status, response = _request(api, "POST", path, data=body)
    _raise_http_outcome(
        status,
        response,
        method="POST",
        path=path,
        expected_status=201,
        body_ok=isinstance(response, dict),
    )
    return _wrap_ok(profile, base, "clearing_document", response)


def create_clearing_documents(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-create clearing documents via ``POST /api/v1/clearing-documents/batch``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _BATCH_CREATE_KEYS)
    raw_documents = _require_non_empty_list(arguments, "clearing_documents")
    documents_body: list[dict[str, Any]] = []
    for element in raw_documents:
        if not isinstance(element, dict):
            raise ValueError("clearing_documents element must be an object")
        documents_body.append(
            _build_create_body(
                element, require_type_and_items=True, reject_unknown=True
            )
        )
    path = _with_query(f"{DOCUMENTS_PATH}/batch", _allow_closed_params(arguments))
    status, response = _request(
        api, "POST", path, data={"clearing_documents": documents_body}
    )
    body_ok = (
        isinstance(response, dict) and isinstance(response.get("clearing_documents"), list)
    )
    _raise_http_outcome(
        status,
        response,
        method="POST",
        path=path,
        expected_status=201,
        body_ok=body_ok,
    )
    documents = response["clearing_documents"] if isinstance(response, dict) else []
    return _wrap_ok(profile, base, "clearing_documents", documents)


def patch_clearing_document(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch a clearing document via ``PATCH /api/v1/clearing-documents/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _PATCH_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    _require_str(arguments, "status")
    body: dict[str, Any] = {}
    for key in _PATCH_BODY_KEYS:
        _copy_present_str(arguments, body, key)
    path = _with_query(
        f"{DOCUMENTS_PATH}/{_path_segment(document_id)}",
        _allow_closed_params(arguments),
    )
    status, response = _request(api, "PATCH", path, data=body)
    _raise_http_outcome(
        status,
        response,
        method="PATCH",
        path=path,
        expected_status=200,
        body_ok=isinstance(response, dict),
    )
    return _wrap_ok(profile, base, "clearing_document", response)


def delete_clearing_document(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete one clearing document via ``DELETE /api/v1/clearing-documents/{id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload without entity key
    """
    _reject_unknown_keys(arguments, _DELETE_ONE_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    path = _with_query(
        f"{DOCUMENTS_PATH}/{_path_segment(document_id)}",
        _allow_closed_params(arguments),
    )
    status, body = _request(api, "DELETE", path)
    _raise_http_outcome(
        status,
        body,
        method="DELETE",
        path=path,
        expected_status=204,
        body_ok=True,
    )
    return _wrap_ok(profile, base)


def delete_clearing_documents(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-delete clearing documents via ``DELETE /api/v1/clearing-documents``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload without entity key
    """
    _reject_unknown_keys(arguments, _DELETE_BATCH_KEYS)
    ids = _ids_body(arguments)
    path = _with_query(DOCUMENTS_PATH, _allow_closed_params(arguments))
    status, body = _request(api, "DELETE", path, data={"ids": ids})
    _raise_http_outcome(
        status,
        body,
        method="DELETE",
        path=path,
        expected_status=204,
        body_ok=True,
    )
    return _wrap_ok(profile, base)


def create_clearing_document_item(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create an item via ``POST /api/v1/clearing-documents/{id}/items``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _CREATE_ITEM_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    body: dict[str, Any] = {}
    for key in ("debit_credit_indicator", "clearing_amount", "clearing_date"):
        _require_str(arguments, key)
        _copy_present_str(arguments, body, key)
    _copy_present_str(arguments, body, "line_id")
    path = _with_query(
        f"{DOCUMENTS_PATH}/{_path_segment(document_id)}/items",
        _allow_closed_params(arguments),
    )
    status, response = _request(api, "POST", path, data=body)
    _raise_http_outcome(
        status,
        response,
        method="POST",
        path=path,
        expected_status=201,
        body_ok=isinstance(response, dict),
    )
    return _wrap_ok(profile, base, "clearing_document_item", response)


def list_clearing_document_items(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List items via ``GET /api/v1/clearing-documents/{id}/items``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _LIST_ITEMS_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    path = f"{DOCUMENTS_PATH}/{_path_segment(document_id)}/items"
    status, body = _request(api, "GET", path)
    body_ok = isinstance(body, dict) and isinstance(body.get("items"), list)
    _raise_http_outcome(
        status,
        body,
        method="GET",
        path=path,
        expected_status=200,
        body_ok=body_ok,
    )
    items = body["items"] if isinstance(body, dict) else []
    return _wrap_ok(profile, base, "items", items)


def get_clearing_document_item(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Get one item via ``GET /api/v1/clearing-documents/{id}/items/{item_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _GET_ITEM_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    item_id = _require_path_id(arguments, "item_id")
    path = (
        f"{DOCUMENTS_PATH}/{_path_segment(document_id)}/items/{_path_segment(item_id)}"
    )
    status, body = _request(api, "GET", path)
    _raise_http_outcome(
        status,
        body,
        method="GET",
        path=path,
        expected_status=200,
        body_ok=isinstance(body, dict),
    )
    return _wrap_ok(profile, base, "clearing_document_item", body)


def patch_clearing_document_item(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Patch an item via ``PATCH /api/v1/clearing-documents/{id}/items/{item_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload
    """
    _reject_unknown_keys(arguments, _PATCH_ITEM_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    item_id = _require_path_id(arguments, "item_id")
    _require_str(arguments, "line_id")
    body: dict[str, Any] = {}
    _copy_present_str(arguments, body, "line_id")
    path = _with_query(
        f"{DOCUMENTS_PATH}/{_path_segment(document_id)}/items/{_path_segment(item_id)}",
        _allow_closed_params(arguments),
    )
    status, response = _request(api, "PATCH", path, data=body)
    _raise_http_outcome(
        status,
        response,
        method="PATCH",
        path=path,
        expected_status=200,
        body_ok=isinstance(response, dict),
    )
    return _wrap_ok(profile, base, "clearing_document_item", response)


def delete_clearing_document_item(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete an item via ``DELETE /api/v1/clearing-documents/{id}/items/{item_id}``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: MCP arguments
    :return: Wrapped MCP payload without entity key
    """
    _reject_unknown_keys(arguments, _DELETE_ITEM_KEYS)
    document_id = _require_path_id(arguments, "document_id")
    item_id = _require_path_id(arguments, "item_id")
    path = _with_query(
        f"{DOCUMENTS_PATH}/{_path_segment(document_id)}/items/{_path_segment(item_id)}",
        _allow_closed_params(arguments),
    )
    status, body = _request(api, "DELETE", path)
    _raise_http_outcome(
        status,
        body,
        method="DELETE",
        path=path,
        expected_status=204,
        body_ok=True,
    )
    return _wrap_ok(profile, base)
