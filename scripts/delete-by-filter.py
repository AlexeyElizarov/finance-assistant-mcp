"""Delete transactions by filter via FinancePlanningProject REST API (BLG-084)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from finance_api_client import ApiClient, resolve_api_base

PROFILE_CHOICES = ("test", "cand", "prod")

FILTER_FIELDS = (
    "date_from",
    "date_to",
    "posting_date_from",
    "posting_date_to",
    "description",
    "amount",
    "debit_credit_indicator",
    "provider",
    "accounting_period",
    "accounting_period_from",
    "accounting_period_to",
    "budget_period",
    "transaction_type",
    "transaction_category",
    "project",
    "source_file",
)


def build_payload(
    *,
    dry_run: bool = True,
    confirm: bool = False,
    allow_closed: bool = False,
    confirm_count: int | None = None,
    filter_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build request body for ``POST /api/v1/transactions/delete-by-filter``.

    :param dry_run: Preview only when ``True``
    :param confirm: Must be ``True`` to delete
    :param allow_closed: Bypass BLG-032 closed-period guard
    :param confirm_count: Optional match against ``deletable_count``
    :param filter_data: Filter object (canonical field names)
    :return: JSON-serializable body
    """
    payload: dict[str, Any] = {
        "dry_run": dry_run,
        "confirm": confirm,
        "allow_closed": allow_closed,
        "filter": dict(filter_data or {}),
    }
    if confirm_count is not None:
        payload["confirm_count"] = confirm_count
    return payload


def run_delete_by_filter(api: ApiClient, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | str]:
    """
    Call delete-by-filter endpoint.

    :param api: Authenticated API client
    :param payload: Request body
    :return: HTTP status and parsed JSON or error text
    """
    status, body = api.request(
        "POST",
        "/api/v1/transactions/delete-by-filter",
        data=payload,
    )
    if isinstance(body, dict):
        return status, body
    return status, str(body)


def parse_filter_args(args: argparse.Namespace) -> dict[str, Any]:
    """
    Collect filter fields from CLI namespace.

    :param args: Parsed CLI arguments
    :return: Filter object with only set fields
    """
    result: dict[str, Any] = {}
    for key in FILTER_FIELDS:
        value = getattr(args, key.replace("-", "_"), None)
        if value is not None and value != "":
            result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point.

    :param argv: Command-line arguments
    :return: Exit code
    """
    parser = argparse.ArgumentParser(description="Delete transactions by filter (BLG-084)")
    parser.add_argument("--profile", choices=PROFILE_CHOICES, default="prod")
    parser.add_argument("--base", default=None)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--allow-closed", action="store_true")
    parser.add_argument("--confirm-count", type=int, default=None)
    parser.add_argument("--filter-json", help="Full filter object as JSON")
    for field in FILTER_FIELDS:
        parser.add_argument(f"--{field.replace('_', '-')}", dest=field)
    args = parser.parse_args(argv)

    if args.filter_json:
        filter_data = json.loads(args.filter_json)
    else:
        filter_data = parse_filter_args(args)
    if not filter_data:
        print("Укажите --filter-json или хотя бы одно поле filter", file=sys.stderr)
        return 2

    base = resolve_api_base(args.base, args.profile)
    api = ApiClient(base)
    api.login(data_profile=args.profile)
    payload = build_payload(
        dry_run=args.dry_run,
        confirm=args.confirm,
        allow_closed=args.allow_closed,
        confirm_count=args.confirm_count,
        filter_data=filter_data,
    )
    status, body = run_delete_by_filter(api, payload)
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
