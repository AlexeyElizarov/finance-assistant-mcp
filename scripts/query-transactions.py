"""Query transactions via FinancePlanningProject REST API.

Ad-hoc выборки для сверок и налоговой подготовки без прямого доступа к SQLite.
Сервер должен быть запущен с нужным ``FINANCE_DATA_PROFILE`` (см. monthly-close-api/index.md).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from finance_api_client import ApiClient
from monthly_close_lib import parse_period

PROFILE_CHOICES = ("test", "cand", "prod")
_FILTER_REQUIRED_MSG = "Укажите хотя бы один фильтр (даты, period, категория, --contains, …)"


@dataclass(frozen=True)
class Row:
    """One transaction row from API."""

    date_display: str
    amount: float
    indicator: str
    description: str
    category: str
    provider: str
    id: str = ""
    transaction_type: str = ""
    expense_owner: str | None = None
    fund_id: str | None = None


@dataclass(frozen=True)
class QueryArgs:
    """Normalized query filters for ``GET /api/v1/transactions`` (FIN-27)."""

    date_from: str | None = None
    date_to: str | None = None
    indicator: str | None = None
    period: str | None = None
    accounting_period: str | None = None
    category: str | None = None
    transaction_category: str | None = None
    provider: str | None = None
    description: str | None = None
    contains: list[str] | None = None


def _normalize_optional_string(value: Any) -> str | None:
    """
    Trim a string filter; empty after trim is unset.

    :param value: Raw filter value
    :return: Active string or ``None``
    """
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_contains(value: Any) -> list[str] | None:
    """
    Normalize ``contains`` list; drop empty elements after trim.

    :param value: Raw contains value (list or single item)
    :return: Non-empty needle list or ``None`` when unset
    """
    if value is None:
        return None
    items = value if isinstance(value, list) else [value]
    needles = [
        stripped
        for item in items
        if (stripped := _normalize_optional_string(item)) is not None
    ]
    return needles if needles else None


def normalize_query_args(
    *,
    date_from: Any = None,
    date_to: Any = None,
    indicator: Any = None,
    period: Any = None,
    accounting_period: Any = None,
    category: Any = None,
    transaction_category: Any = None,
    provider: Any = None,
    description: Any = None,
    contains: Any = None,
) -> QueryArgs:
    """
    Normalize MCP/CLI transaction query arguments (FIN-27).

    :return: :class:`QueryArgs` with active or unset filters
    """
    return QueryArgs(
        date_from=_normalize_optional_string(date_from),
        date_to=_normalize_optional_string(date_to),
        indicator=_normalize_optional_string(indicator),
        period=_normalize_optional_string(period),
        accounting_period=_normalize_optional_string(accounting_period),
        category=_normalize_optional_string(category),
        transaction_category=_normalize_optional_string(transaction_category),
        provider=_normalize_optional_string(provider),
        description=_normalize_optional_string(description),
        contains=_normalize_contains(contains),
    )


def _has_active_filter(args: QueryArgs) -> bool:
    """
    Return whether at least one filter is active after normalization.

    :param args: Normalized query args
    :return: True when a filter applies
    """
    return any(
        (
            args.date_from,
            args.date_to,
            args.indicator,
            args.period,
            args.accounting_period,
            args.category,
            args.transaction_category,
            args.provider,
            args.description,
            args.contains,
        )
    )


def _resolve_accounting_period_ymmm(args: QueryArgs) -> str | None:
    """
    Resolve MCP period filters to API ``accounting_period=YYYYMM``.

    :param args: Normalized query args
    :return: ``YYYYMM`` or ``None`` when period filter unset
    :raises ValueError: On mutual exclusion or invalid ``period`` format
    """
    period_value = args.period
    accounting_period_value = args.accounting_period
    if period_value and accounting_period_value:
        raise ValueError("period and accounting_period are mutually exclusive")
    if period_value:
        if len(period_value) < 7 or period_value[4] != "-":
            raise ValueError("period must be YYYY-MM")
        return parse_period(period_value).ymmm
    if accounting_period_value:
        return parse_period(accounting_period_value).ymmm
    return None


def _resolve_transaction_category(args: QueryArgs) -> str | None:
    """
    Resolve category aliases to a single API ``transaction_category`` value.

    :param args: Normalized query args
    :return: Category id or ``None`` when unset
    :raises ValueError: When both aliases are active and differ
    """
    category_value = args.category
    transaction_category_value = args.transaction_category
    if (
        category_value
        and transaction_category_value
        and category_value != transaction_category_value
    ):
        raise ValueError("category and transaction_category conflict")
    return category_value or transaction_category_value


def parse_amount(raw: str) -> float:
    """
    Parse amount string from API.

    :param raw: Amount as returned by API
    :return: Absolute numeric value
    """
    normalized = raw.strip().replace(",", ".")
    return abs(float(normalized))


def month_key(date_display: str) -> str:
    """
    Normalize display date to ``YYYY-MM``.

    :param date_display: ``DD.MM.YYYY`` or ``YYYY-MM-DD``
    :return: Year-month key
    """
    if len(date_display) >= 4 and date_display[4] == "-":
        return date_display[:7]
    parts = date_display.split(".")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}"
    raise ValueError(f"Неизвестный формат даты: {date_display!r}")


def row_from_api(raw: dict) -> Row:
    """
    Build :class:`Row` from API dict.

    :param raw: One element of ``GET /transactions`` ``rows``
    :return: Parsed row
    """
    raw_owner = raw.get("expense_owner")
    expense_owner: str | None
    if raw_owner is None:
        expense_owner = None
    else:
        expense_owner = str(raw_owner)
    raw_fund = raw.get("fund_id")
    fund_id: str | None
    if raw_fund is None:
        fund_id = None
    else:
        fund_id = str(raw_fund)
    return Row(
        date_display=raw["date_display"],
        amount=parse_amount(raw["amount"]),
        indicator=raw["debit_credit_indicator"],
        description=raw["description"],
        category=raw["transaction_category"],
        provider=raw["provider"],
        id=str(raw.get("id") or ""),
        transaction_type=str(raw.get("transaction_type") or ""),
        expense_owner=expense_owner,
        fund_id=fund_id,
    )


def build_query_path(args: QueryArgs) -> str:
    """
    Build ``GET /api/v1/transactions`` query string.

    :param args: Normalized query args
    :return: Path with query string
    :raises ValueError: When no active filter or validation fails
    """
    if not _has_active_filter(args):
        raise ValueError(_FILTER_REQUIRED_MSG)

    params: dict[str, str] = {}
    period_ymmm = _resolve_accounting_period_ymmm(args)
    if period_ymmm:
        params["accounting_period"] = period_ymmm

    category_value = _resolve_transaction_category(args)
    if category_value:
        params["transaction_category"] = category_value

    if args.date_from:
        params["date_from"] = args.date_from
    if args.date_to:
        params["date_to"] = args.date_to
    if args.indicator:
        params["debit_credit_indicator"] = args.indicator
    if args.provider:
        params["provider"] = args.provider

    contains = args.contains or []
    if args.description and len(contains) <= 1:
        params["description"] = args.description or (contains[0] if contains else "")

    if not params:
        raise ValueError(_FILTER_REQUIRED_MSG)
    return "/api/v1/transactions?" + urllib.parse.urlencode(params)


def matches_contains(row: Row, needles: list[str]) -> bool:
    """
    Return whether row description matches any needle (case-insensitive).

    :param row: Transaction row
    :param needles: Substrings; empty list matches all
    :return: True if matched
    """
    if not needles:
        return True
    hay = row.description.lower()
    return any(n.lower() in hay for n in needles)


def fetch_rows(api: ApiClient, args: QueryArgs) -> list[Row]:
    """
    Load and post-filter transaction rows.

    :param api: API client
    :param args: Normalized query args
    :return: Matching rows
    """
    path = build_query_path(args)
    body = api.get_json(path)
    meta = body.get("meta", {})
    if meta.get("filter_error"):
        raise RuntimeError(f"Ошибка фильтра API: {meta['filter_error']}")
    rows = [row_from_api(r) for r in body.get("rows", [])]
    needles = list(args.contains or [])
    if args.description:
        needles.append(args.description)
    if len(needles) > 1 or (len(needles) == 1 and "description" not in path):
        rows = [r for r in rows if matches_contains(r, needles)]
    return rows


def verify_profile(api: ApiClient, expected: str | None) -> str:
    """
    Read active profile from ``GET /api/v1/meta``.

    :param api: API client
    :param expected: Expected profile id or None to skip check
    :return: Active profile id
    """
    meta = api.get_json("/api/v1/meta")
    active = meta.get("data_profile") or ""
    if expected and active and active != expected:
        print(
            f"WARNING: сервер data_profile={active!r}, ожидался --profile {expected!r}",
            file=sys.stderr,
        )
    return active


def print_table(rows: list[Row]) -> None:
    """
    Print rows as a fixed-width table.

    :param rows: Rows to print
    """
    print("date\tamount\tD/C\tcategory\tprovider\tdescription")
    for r in rows:
        desc = r.description.replace("\t", " ")[:120]
        print(
            f"{r.date_display}\t{r.amount:.2f}\t{r.indicator}\t{r.category}\t{r.provider}\t{desc}"
        )


def print_group_month(rows: list[Row], split_provider: bool) -> None:
    """
    Print monthly totals.

    :param rows: Rows to aggregate
    :param split_provider: Split totals by provider substring keys
    """
    if not split_provider:
        by_month: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for r in rows:
            key = month_key(r.date_display)
            by_month[key] += r.amount
            counts[key] += 1
        print("month\tcount\tsum")
        total = 0.0
        for m in sorted(by_month):
            total += by_month[m]
            print(f"{m}\t{counts[m]}\t{by_month[m]:.2f}")
        print(f"TOTAL\t{len(rows)}\t{total:.2f}")
        return

    buckets = ("vodafone", "netcologne", "other")
    by_month = defaultdict(lambda: {b: 0.0 for b in buckets})
    for r in rows:
        m = month_key(r.date_display)
        desc = r.description.lower()
        if "vodafone" in desc:
            slot = "vodafone"
        elif "netcologne" in desc:
            slot = "netcologne"
        else:
            slot = "other"
        by_month[m][slot] += r.amount

    print("month\tvodafone\tnetcologne\tother\ttotal")
    totals = {b: 0.0 for b in buckets}
    grand = 0.0
    for m in sorted(by_month):
        v = by_month[m]["vodafone"]
        n = by_month[m]["netcologne"]
        o = by_month[m]["other"]
        t = v + n + o
        totals["vodafone"] += v
        totals["netcologne"] += n
        totals["other"] += o
        grand += t
        print(f"{m}\t{v:.2f}\t{n:.2f}\t{o:.2f}\t{t:.2f}")
    print(
        f"TOTAL\t{totals['vodafone']:.2f}\t{totals['netcologne']:.2f}\t"
        f"{totals['other']:.2f}\t{grand:.2f}"
    )


def build_parser() -> argparse.ArgumentParser:
    """
    Build CLI parser.

    :return: Argument parser
    """
    parser = argparse.ArgumentParser(
        description="Выборка транзакций через GET /api/v1/transactions",
        epilog=(
            "Пример: query-transactions.py --base http://127.0.0.1:8001 --profile cand "
            "--period 2026-02 --category C9999 --indicator D"
        ),
    )
    parser.add_argument(
        "--base",
        default="http://127.0.0.1:8000",
        help="URL API (порт должен соответствовать запущенному серверу)",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_CHOICES,
        help="Ожидаемый FINANCE_DATA_PROFILE на сервере (проверка через /meta)",
    )
    parser.add_argument("--from", dest="date_from", metavar="DATE", help="date_from (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", metavar="DATE", help="date_to (YYYY-MM-DD)")
    period_group = parser.add_mutually_exclusive_group()
    period_group.add_argument(
        "--period",
        metavar="YYYY-MM",
        help="Учётный месяц (YYYY-MM)",
    )
    period_group.add_argument(
        "--accounting-period",
        dest="accounting_period",
        metavar="PERIOD",
        help="Учётный месяц (YYYY-MM или YYYYMM)",
    )
    parser.add_argument(
        "--indicator",
        choices=("D", "C"),
        help="debit_credit_indicator",
    )
    parser.add_argument("--category", help="transaction_category, напр. C0010")
    parser.add_argument(
        "--transaction-category",
        dest="transaction_category",
        help="Alias для --category",
    )
    parser.add_argument("--provider", help="provider, напр. sparkasse_sepa")
    parser.add_argument(
        "--description",
        help="Подстрока в описании (одна; передаётся в API)",
    )
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        metavar="TEXT",
        help="Подстрока в описании (можно несколько, OR; при >1 — фильтр на клиенте)",
    )
    parser.add_argument(
        "--group-by",
        choices=("month",),
        help="Агрегация по месяцам",
    )
    parser.add_argument(
        "--split-internet",
        action="store_true",
        help="С --group-by month: колонки vodafone / netcologne / other",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Формат вывода (по умолчанию table)",
    )
    return parser


def main() -> int:
    """
    CLI entry point.

    :return: Exit code
    """
    parser = build_parser()
    raw = parser.parse_args()
    query_args = normalize_query_args(
        date_from=raw.date_from,
        date_to=raw.date_to,
        indicator=raw.indicator,
        period=raw.period,
        accounting_period=raw.accounting_period,
        category=raw.category,
        transaction_category=raw.transaction_category,
        provider=raw.provider,
        description=raw.description,
        contains=raw.contains,
    )
    try:
        api = ApiClient(raw.base)
        active = verify_profile(api, raw.profile)
        rows = fetch_rows(api, query_args)
        if raw.format == "json":
            payload = {
                "data_profile": active or None,
                "row_count": len(rows),
                "rows": [
                    {
                        "id": r.id,
                        "date": r.date_display,
                        "amount": r.amount,
                        "indicator": r.indicator,
                        "category": r.category,
                        "transaction_type": r.transaction_type,
                        "expense_owner": r.expense_owner,
                        "fund_id": r.fund_id,
                        "provider": r.provider,
                        "description": r.description,
                    }
                    for r in rows
                ],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif raw.group_by == "month":
            if active:
                print(f"# data_profile: {active}")
            print_group_month(rows, raw.split_internet)
        else:
            if active:
                print(f"# data_profile: {active}")
            print_table(rows)
            print(f"# rows: {len(rows)}")
        return 0
    except (RuntimeError, ValueError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
