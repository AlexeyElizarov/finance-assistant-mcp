"""MCP helper: batch canonical PATCH for operation fields (FIN-265)."""

from __future__ import annotations

from typing import Any

from finance_api_client import ApiClient
from put_transaction import put_transaction


def validate_batch_arguments(arguments: dict[str, Any]) -> list[Any]:
    """
    Validate batch-level arguments before session resolve (D-05).

    :param arguments: Raw MCP arguments
    :return: Non-empty ``items`` list
    :raises ValueError: When ``items`` is missing, not a list, or empty
    """
    if "items" not in arguments:
        raise ValueError("items must be a non-empty list")
    items = arguments["items"]
    if not isinstance(items, list):
        raise ValueError("items must be a non-empty list")
    if len(items) == 0:
        raise ValueError("items must be a non-empty list")
    return items


def _item_transaction_id(item: dict[str, Any]) -> str:
    """Best-effort transaction_id for error results (missing → empty string)."""
    if "transaction_id" not in item:
        return ""
    value = item["transaction_id"]
    if value is None:
        return ""
    return str(value).strip()


def put_transactions(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Apply canonical merge-patch to many operations (FIN-265).

    Orchestrates sequential ``put_transaction`` calls. Item failures become
    ``results[i].ok = false``; the tool envelope stays ``ok: true``.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param arguments: Raw MCP arguments (presence-preserving)
    :return: Tool success payload with ``summary`` and ``results``
    :raises ValueError: Empty or missing ``items`` (batch-level)
    """
    items = validate_batch_arguments(arguments)
    allow_closed = bool(arguments.get("allow_closed", False))
    results: list[dict[str, Any]] = []

    for raw_item in items:
        if not isinstance(raw_item, dict):
            results.append(
                {
                    "ok": False,
                    "transaction_id": "",
                    "error": "item must be an object",
                }
            )
            continue

        transaction_id = _item_transaction_id(raw_item)
        item_arguments = dict(raw_item)
        item_arguments["allow_closed"] = allow_closed
        try:
            one = put_transaction(
                api,
                profile=profile,
                base=base,
                arguments=item_arguments,
            )
            transaction = one["transaction"]
            results.append(
                {
                    "ok": True,
                    "transaction_id": str(transaction.get("id") or transaction_id),
                    "transaction": transaction,
                }
            )
        except ValueError as exc:
            results.append(
                {
                    "ok": False,
                    "transaction_id": transaction_id,
                    "error": str(exc),
                }
            )
        except RuntimeError as exc:
            results.append(
                {
                    "ok": False,
                    "transaction_id": transaction_id,
                    "error": str(exc),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "transaction_id": transaction_id,
                    "error": str(exc),
                }
            )

    succeeded = sum(1 for row in results if row.get("ok") is True)
    failed = len(results) - succeeded
    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "summary": {
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
        },
        "results": results,
    }
