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

from finance_api_client import ApiClient

PROFILE_CHOICES = ("test", "cand", "prod")


@dataclass(frozen=True)
class Row:
    """One transaction row from API."""

    date_display: str
    amount: float
    indicator: str
    description: str
    category: str
    provider: str


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
    return Row(
        date_display=raw["date_display"],
        amount=parse_amount(raw["amount"]),
        indicator=raw["debit_credit_indicator"],
        description=raw["description"],
        category=raw["transaction_category"],
        provider=raw["provider"],
    )


def build_query_path(args: argparse.Namespace) -> str:
    """
    Build ``GET /api/v1/transactions`` query string.

    :param args: Parsed CLI arguments
    :return: Path with query string
    """
    params: dict[str, str] = {}
    if args.date_from:
        params["date_from"] = args.date_from
    if args.date_to:
        params["date_to"] = args.date_to
    if args.indicator:
        params["debit_credit_indicator"] = args.indicator
    if args.category:
        params["transaction_category"] = args.category
    if args.provider:
        params["provider"] = args.provider
    if args.description and len(args.contains) <= 1:
        params["description"] = args.description or (args.contains[0] if args.contains else "")
    if not params:
        raise ValueError("Укажите хотя бы один фильтр (даты, категория, --contains, …)")
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


def fetch_rows(api: ApiClient, args: argparse.Namespace) -> list[Row]:
    """
    Load and post-filter transaction rows.

    :param api: API client
    :param args: CLI arguments
    :return: Matching rows
    """
    path = build_query_path(args)
    body = api.get_json(path)
    meta = body.get("meta", {})
    if meta.get("filter_error"):
        raise RuntimeError(f"Ошибка фильтра API: {meta['filter_error']}")
    rows = [row_from_api(r) for r in body.get("rows", [])]
    needles = list(args.contains)
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
            "--from 2025-01-01 --to 2025-12-31 --indicator D "
            "--contains vodafone --contains netcologne --group-by month --split-internet"
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
    parser.add_argument(
        "--indicator",
        choices=("D", "C"),
        help="debit_credit_indicator",
    )
    parser.add_argument("--category", help="transaction_category, напр. C0010")
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
    args = parser.parse_args()
    try:
        api = ApiClient(args.base)
        active = verify_profile(api, args.profile)
        rows = fetch_rows(api, args)
        if args.format == "json":
            payload = {
                "data_profile": active or None,
                "row_count": len(rows),
                "rows": [
                {
                    "date": r.date_display,
                    "amount": r.amount,
                    "indicator": r.indicator,
                    "category": r.category,
                    "provider": r.provider,
                    "description": r.description,
                }
                for r in rows
                ],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.group_by == "month":
            if active:
                print(f"# data_profile: {active}")
            print_group_month(rows, args.split_internet)
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
