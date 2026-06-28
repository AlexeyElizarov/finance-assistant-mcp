"""Query plan-fact for budget articles via FinancePlanningProject REST API.

Ad-hoc сверка план/факт по статье бюджета без UI.
Сервер: см. ``working/monthly-close-api/index.md`` (bootstrap prod).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
from dataclasses import dataclass

from finance_api_client import ApiClient, discover_api_base

PROFILE_CHOICES = ("test", "cand", "prod")
BOOTSTRAP_HINT = (
    "Запустите сервер: $env:FINANCE_DATA_PROFILE = '{profile}'; "
    "$env:FINANCE_WEB_PORT = '{port}'; "
    "cd C:\\Users\\haake\\PycharmProjects\\FinancePlanningProject; "
    ".\\.venv\\Scripts\\python.exe -m web"
)


@dataclass(frozen=True)
class MonthRow:
    """Plan-fact row for one calendar month."""

    period: str
    article: str
    budget_item_id: str
    plan: float
    fact: float
    variance: float


def parse_amount(raw: str | float | int | None) -> float:
    """
    Parse API amount.

    :param raw: Amount from API
    :return: Numeric value
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    return float(str(raw).strip().replace(",", "."))


def month_period(year_month: str) -> str:
    """
    Normalize ``YYYY-MM`` to ``YYYY-MM-01``.

    :param year_month: Year-month key
    :return: First day of month for API
    """
    if len(year_month) == 10 and year_month.endswith("-01"):
        return year_month
    if len(year_month) == 7:
        return f"{year_month}-01"
    raise ValueError(f"Ожидается YYYY-MM или YYYY-MM-01, получено: {year_month!r}")


def iter_months(date_from: str, date_to: str) -> list[str]:
    """
    List month starts between two ``YYYY-MM`` bounds inclusive.

    :param date_from: Start month ``YYYY-MM``
    :param date_to: End month ``YYYY-MM``
    :return: ``YYYY-MM-01`` values
    """
    start_y, start_m = map(int, date_from.split("-"))
    end_y, end_m = map(int, date_to.split("-"))
    months: list[str] = []
    y, m = start_y, start_m
    while (y, m) <= (end_y, end_m):
        months.append(f"{y:04d}-{m:02d}-01")
        m += 1
        if m > 12:
            y += 1
            m = 1
    return months


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


def ensure_api(api: ApiClient, profile: str | None) -> str:
    """
    Verify API responds and return active profile.

    :param api: API client
    :param profile: Expected profile or None
    :return: Active profile id
    :raises RuntimeError: When ``GET /meta`` fails
    """
    try:
        return verify_profile(api, profile)
    except (urllib.error.URLError, RuntimeError) as exc:
        hint = BOOTSTRAP_HINT.format(profile=profile or "prod", port="8000")
        raise RuntimeError(f"API недоступен ({api.base}): {exc}\n{hint}") from exc


def resolve_base_url(explicit_base: str | None, profile: str) -> str:
    """
    Resolve API base URL: explicit ``--base`` or scan ports 8000–8010.

    :param explicit_base: CLI ``--base`` or None
    :param profile: Expected data profile
    :return: Base URL
    :raises RuntimeError: When no server responds in the scan range
    """
    if explicit_base:
        return explicit_base
    found = discover_api_base(profile=profile)
    if found:
        return found
    hint = BOOTSTRAP_HINT.format(profile=profile, port="8000")
    raise RuntimeError(
        f"API не найден на портах 8000–8010 для profile={profile!r}.\n{hint}"
    )


def active_budget_version_id(api: ApiClient) -> str:
    """
    Return active budget version id.

    :param api: API client
    :return: Version UUID with status ACT, else first DRA for current year
    """
    data = api.get_json("/api/v1/budget/versions")
    versions = data.get("budget_versions", [])
    for status in ("ACT", "DRA"):
        for version in versions:
            if version.get("status") == status:
                return str(version["id"])
    raise RuntimeError("Не найдена версия бюджета (ACT/DRA)")


def resolve_budget_item_id(api: ApiClient, article: str | None, budget_item_id: str | None) -> tuple[str, str]:
    """
    Resolve article label to budget item id.

    :param api: API client
    :param article: Substring of article name
    :param budget_item_id: Explicit UUID
    :return: Tuple of item id and display name
    """
    if budget_item_id:
        item = api.get_json(f"/api/v1/budget/items/{budget_item_id}")
        return budget_item_id, str(item.get("name", budget_item_id))

    if not article:
        raise ValueError("Укажите --article или --budget-item-id")

    data = api.get_json("/api/v1/budget/items")
    needle = article.casefold()
    matches = [
        item
        for item in data.get("budget_items", [])
        if needle in str(item.get("name", "")).casefold()
    ]
    if not matches:
        raise RuntimeError(f"Статья бюджета не найдена по --article {article!r}")
    if len(matches) > 1:
        names = ", ".join(str(m.get("name")) for m in matches)
        raise RuntimeError(f"Неоднозначно --article {article!r}: {names}")
    item = matches[0]
    return str(item["id"]), str(item["name"])


def fetch_month_row(
    api: ApiClient,
    budget_version_id: str,
    period: str,
    budget_item_id: str,
    article: str,
) -> MonthRow:
    """
    Load plan-fact for one month from grouped plan-actual.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Month start ``YYYY-MM-01``
    :param budget_item_id: Article UUID
    :param article: Display name
    :return: Month row
    """
    query = urllib.parse.urlencode(
        {
            "budget_version_id": budget_version_id,
            "period": period,
            "view": "grouped",
        }
    )
    data = api.get_json(f"/api/v1/budget/plan-actual?{query}")
    node = next(
        (
            n
            for n in data.get("grid_nodes", [])
            if n.get("kind") == "row" and n.get("budget_item_id") == budget_item_id
        ),
        None,
    )
    if node is None:
        return MonthRow(period, article, budget_item_id, 0.0, 0.0, 0.0)
    plan = parse_amount(node.get("plan_amount"))
    fact = parse_amount(node.get("actual_amount"))
    variance = parse_amount(node.get("variance"))
    return MonthRow(period, article, budget_item_id, plan, fact, variance)


def fetch_transactions(
    api: ApiClient,
    budget_version_id: str,
    period: str,
    budget_item_id: str,
) -> list[dict]:
    """
    Load drill-down transactions for one month.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Month start
    :param budget_item_id: Article UUID
    :return: Transaction dicts
    """
    query = urllib.parse.urlencode(
        {
            "budget_version_id": budget_version_id,
            "period": period,
            "budget_item_id": budget_item_id,
            "currency": "EUR",
        }
    )
    data = api.get_json(f"/api/v1/budget/plan-actual/transactions?{query}")
    return list(data.get("transactions", []))


def print_table(rows: list[MonthRow]) -> None:
    """
    Print month rows as a table.

    :param rows: Plan-fact rows
    """
    print("period\tplan\tfact\tvariance")
    total_plan = 0.0
    total_fact = 0.0
    for row in rows:
        print(
            f"{row.period[:7]}\t{row.plan:.2f}\t{row.fact:.2f}\t{row.variance:.2f}"
        )
        total_plan += row.plan
        total_fact += row.fact
    if len(rows) > 1:
        print(f"TOTAL\t{total_plan:.2f}\t{total_fact:.2f}\t{total_fact - total_plan:.2f}")


def build_parser() -> argparse.ArgumentParser:
    """
    Build CLI parser.

    :return: Argument parser
    """
    parser = argparse.ArgumentParser(
        description="План/факт по статье бюджета (GET /api/v1/budget/plan-actual)",
        epilog=(
            "Пример: query-plan-fact.py --profile prod --article Cursor "
            "--from 2026-01 --to 2026-06 --transactions"
        ),
    )
    parser.add_argument(
        "--base",
        help="URL API (по умолчанию — сканирование портов 8000–8010)",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_CHOICES,
        default="prod",
        help="Ожидаемый FINANCE_DATA_PROFILE (по умолчанию prod)",
    )
    parser.add_argument(
        "--budget-version-id",
        help="UUID версии бюджета (по умолчанию — ACT/DRA из GET /budget/versions)",
    )
    parser.add_argument("--article", help="Подстрока имени статьи, напр. Cursor")
    parser.add_argument("--budget-item-id", help="UUID статьи бюджета")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM", required=True)
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM", required=True)
    parser.add_argument(
        "--transactions",
        action="store_true",
        help="Добавить drill-down транзакций по месяцам с ненулевым фактом",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Формат вывода",
    )
    return parser


def main() -> int:
    """
    CLI entry point.

    :return: Exit code
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    if not args.article and not args.budget_item_id:
        parser.error("нужен --article или --budget-item-id")

    base = resolve_base_url(args.base, args.profile)
    try:
        api = ApiClient(base)
        active = ensure_api(api, args.profile)
        budget_version_id = args.budget_version_id or active_budget_version_id(api)
        item_id, article_name = resolve_budget_item_id(api, args.article, args.budget_item_id)
        months = iter_months(args.date_from, args.date_to)
        rows = [
            fetch_month_row(api, budget_version_id, period, item_id, article_name)
            for period in months
        ]

        tx_by_month: dict[str, list[dict]] = {}
        if args.transactions:
            for period in months:
                txs = fetch_transactions(api, budget_version_id, period, item_id)
                if txs:
                    tx_by_month[period] = txs

        if args.format == "json":
            payload = {
                "data_profile": active or None,
                "base": base,
                "budget_version_id": budget_version_id,
                "budget_item_id": item_id,
                "article": article_name,
                "months": [
                    {
                        "period": row.period,
                        "plan": row.plan,
                        "fact": row.fact,
                        "variance": row.variance,
                        "transactions": tx_by_month.get(row.period, []),
                    }
                    for row in rows
                ],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if active:
                print(f"# data_profile: {active}")
            print(f"# article: {article_name}")
            print(f"# budget_item_id: {item_id}")
            print_table(rows)
            if args.transactions:
                for period, txs in tx_by_month.items():
                    print(f"\n# transactions {period[:7]}")
                    for tx in txs:
                        print(
                            f"{tx.get('posting_date', '')}\t"
                            f"{parse_amount(tx.get('amount')):.2f}\t"
                            f"{tx.get('description', '')}"
                        )
        return 0
    except (RuntimeError, ValueError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
