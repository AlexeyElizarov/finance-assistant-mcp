"""MCP helpers for internal-transfer match tools (FIN-351)."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from typing import Any

from finance_api_client import ApiClient

MATCHES_PATH = "/api/v1/internal-transfer-matches"


def resolve_profile(arguments: dict[str, Any]) -> str:
    """
    Return effective data profile after schema checks.

    :param arguments: Raw MCP arguments
    :return: Profile name; missing or blank after strip is ``prod``
    """
    if "profile" not in arguments or arguments["profile"] is None:
        return "prod"
    text = str(arguments["profile"]).strip()
    if not text:
        return "prod"
    return text


def resolve_base(arguments: dict[str, Any]) -> str | None:
    """
    Return URL override after schema checks, or ``None`` for profile default.

    :param arguments: Raw MCP arguments
    :return: Stripped URL, or ``None`` when omitted or blank
    """
    if "base" not in arguments or arguments["base"] is None:
        return None
    text = str(arguments["base"]).strip()
    if not text:
        return None
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


def validate_noop(_arguments: dict[str, Any]) -> None:
    """Skip extra handler checks (create one record)."""


def validate_match_id(arguments: dict[str, Any]) -> None:
    """
    Require ``match_id`` nonempty after strip; HTTP still uses the raw string.

    :param arguments: Raw MCP arguments
    """
    if "match_id" not in arguments or arguments["match_id"] is None:
        raise ValueError("match_id is required")
    if not str(arguments["match_id"]).strip():
        raise ValueError("match_id is required")


def validate_list_arguments(arguments: dict[str, Any]) -> None:
    """
    Reject present ``line_id`` that is null, empty, or whitespace-only.

    :param arguments: Raw MCP arguments
    """
    if "line_id" not in arguments:
        return
    value = arguments["line_id"]
    if value is None or not str(value).strip():
        raise ValueError("line_id is required")


def validate_batch_create(arguments: dict[str, Any]) -> None:
    """
    Require a non-empty ``internal_transfer_matches`` list.

    :param arguments: Raw MCP arguments
    """
    matches = arguments.get("internal_transfer_matches")
    if not isinstance(matches, list) or not matches:
        raise ValueError("internal_transfer_matches must be a non-empty list")


def validate_batch_delete(arguments: dict[str, Any]) -> None:
    """
    Require a non-empty ``ids`` list whose items are nonempty after strip.

    :param arguments: Raw MCP arguments
    """
    ids = arguments.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError("ids must be a non-empty list")
    for item in ids:
        if not str(item).strip():
            raise ValueError("ids items must be non-empty")


def allow_closed_query(arguments: dict[str, Any]) -> dict[str, str]:
    """
    Build ``allow_closed`` query params only when the argument is present.

    :param arguments: Raw MCP arguments
    :return: Query mapping, possibly empty
    """
    if "allow_closed" not in arguments:
        return {}
    flag = arguments["allow_closed"]
    return {"allow_closed": "true" if flag else "false"}


def _with_query(path: str, params: dict[str, str]) -> str:
    if not params:
        return path
    return f"{path}?{urllib.parse.urlencode(params)}"


def _http_error_text(body: Any) -> str:
    if isinstance(body, (bytes, bytearray)):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False)


def _raise_unexpected(body: Any) -> None:
    raise RuntimeError(_http_error_text(body))


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


def list_internal_transfer_matches(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    List matches via ``GET /api/v1/internal-transfer-matches``.

    :param api: Authenticated API client
    :param profile: Effective profile
    :param base: Resolved API base URL
    :param arguments: Raw MCP arguments
    :return: Success envelope with ``internal_transfer_matches``
    """
    params: dict[str, str] = {}
    if "line_id" in arguments:
        params["line_id"] = str(arguments["line_id"]).strip()
    path = _with_query(MATCHES_PATH, params)
    status, resp = api.request("GET", path)
    if status != 200:
        _raise_unexpected(resp)
    matches = (
        resp["internal_transfer_matches"]
        if isinstance(resp, dict) and "internal_transfer_matches" in resp
        else resp
    )
    return _wrap_ok(profile, base, "internal_transfer_matches", matches)


def get_internal_transfer_match(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Read one match via ``GET /api/v1/internal-transfer-matches/{match_id}``.

    :param api: Authenticated API client
    :param profile: Effective profile
    :param base: Resolved API base URL
    :param arguments: Raw MCP arguments
    :return: Success envelope with ``internal_transfer_match``
    """
    match_id = str(arguments["match_id"])
    path = f"{MATCHES_PATH}/{urllib.parse.quote(match_id, safe='')}"
    status, resp = api.request("GET", path)
    if status != 200:
        _raise_unexpected(resp)
    return _wrap_ok(profile, base, "internal_transfer_match", resp)


def create_internal_transfer_match(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create one match via ``POST /api/v1/internal-transfer-matches``.

    :param api: Authenticated API client
    :param profile: Effective profile
    :param base: Resolved API base URL
    :param arguments: Raw MCP arguments
    :return: Success envelope with ``internal_transfer_match``
    """
    path = _with_query(MATCHES_PATH, allow_closed_query(arguments))
    body = {
        "debit_line_ids": arguments["debit_line_ids"],
        "credit_line_ids": arguments["credit_line_ids"],
    }
    status, resp = api.request("POST", path, data=body)
    if status != 201:
        _raise_unexpected(resp)
    return _wrap_ok(profile, base, "internal_transfer_match", resp)


def create_internal_transfer_matches(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-create matches via ``POST /api/v1/internal-transfer-matches/batch``.

    :param api: Authenticated API client
    :param profile: Effective profile
    :param base: Resolved API base URL
    :param arguments: Raw MCP arguments
    :return: Success envelope with ``internal_transfer_matches``
    """
    path = _with_query(f"{MATCHES_PATH}/batch", allow_closed_query(arguments))
    body = {"internal_transfer_matches": arguments["internal_transfer_matches"]}
    status, resp = api.request("POST", path, data=body)
    if status != 201:
        _raise_unexpected(resp)
    matches = (
        resp["internal_transfer_matches"]
        if isinstance(resp, dict) and "internal_transfer_matches" in resp
        else resp
    )
    return _wrap_ok(profile, base, "internal_transfer_matches", matches)


def delete_internal_transfer_match(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Delete one match via ``DELETE /api/v1/internal-transfer-matches/{match_id}``.

    :param api: Authenticated API client
    :param profile: Effective profile
    :param base: Resolved API base URL
    :param arguments: Raw MCP arguments
    :return: Success envelope without an entity key
    """
    match_id = str(arguments["match_id"])
    path = _with_query(
        f"{MATCHES_PATH}/{urllib.parse.quote(match_id, safe='')}",
        allow_closed_query(arguments),
    )
    status, resp = api.request("DELETE", path)
    if status != 204:
        _raise_unexpected(resp)
    return _wrap_ok(profile, base)


def delete_internal_transfer_matches(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Batch-delete matches via ``DELETE /api/v1/internal-transfer-matches``.

    :param api: Authenticated API client
    :param profile: Effective profile
    :param base: Resolved API base URL
    :param arguments: Raw MCP arguments
    :return: Success envelope without a collection key
    """
    path = _with_query(MATCHES_PATH, allow_closed_query(arguments))
    body = {"ids": list(arguments["ids"])}
    status, resp = api.request("DELETE", path, data=body)
    if status != 204:
        _raise_unexpected(resp)
    return _wrap_ok(profile, base)
