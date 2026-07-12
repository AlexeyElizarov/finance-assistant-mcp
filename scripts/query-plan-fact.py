"""Query plan-fact for budget articles via FinancePlanningProject REST API.

Ad-hoc сверка план/факт по статье бюджета без UI.
Сервер: см. ``working/monthly-close-api/index.md`` (bootstrap prod).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from finance_api_client import ApiClient, discover_api_base

PROFILE_CHOICES = ("test", "cand", "prod")
_CANDIDATE_LIMIT = 5
_CATEGORY_ID_RE = re.compile(r"^[PCSI]\d{3,5}$")
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


def normalize_match_text(text: str) -> str:
    """
    Normalize article or budget item name for case-insensitive match (FIN-122 D-08).

    :param text: Raw label
    :return: Stripped, whitespace-collapsed, casefolded string
    """
    return " ".join(text.strip().split()).casefold()


def _is_category_id_shaped(value: str) -> bool:
    """
    Return whether ``value`` looks like an operation category id (FIN-122 D-04).

    :param value: Candidate category id or article token
    :return: True when value matches ``^[PCSI]\\d{3,5}$``
    """
    return bool(_CATEGORY_ID_RE.match(value.strip()))


def _levenshtein_at_most_one(left: str, right: str) -> bool:
    """
    Return whether standard Levenshtein distance is at most one.

    :param left: First string
    :param right: Second string
    :return: True for distance 0 or 1 (insert/delete/replace only)
    """
    if left == right:
        return True
    left_len = len(left)
    right_len = len(right)
    if abs(left_len - right_len) > 1:
        return False
    if left_len == right_len:
        return sum(1 for a, b in zip(left, right, strict=True) if a != b) == 1
    if left_len > right_len:
        left, right = right, left
        left_len, right_len = right_len, left_len
    index_left = 0
    index_right = 0
    skipped = False
    while index_left < left_len and index_right < right_len:
        if left[index_left] == right[index_right]:
            index_left += 1
            index_right += 1
        elif skipped:
            return False
        else:
            skipped = True
            index_right += 1
    return True


def _active_budget_items(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Filter ACT budget items from catalog response rows.

    :param catalog: Raw ``budget_items`` list from API
    :return: Items with ``status == \"ACT\"``
    """
    return [row for row in catalog if str(row.get("status", "")).strip().upper() == "ACT"]


def _item_row_key(item: dict[str, Any]) -> tuple[str, str]:
    """
    Sort key for ambiguous rows (FIN-122 D-09).

    :param item: Budget item dict
    :return: Tuple of casefolded name and id
    """
    return (str(item.get("name", "")).casefold(), str(item["id"]))


def sort_ambiguous_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sort ambiguous matches for stable error output (FIN-122 D-09).

    :param items: Matching budget items
    :return: Sorted copy
    """
    return sorted(items, key=_item_row_key)


def _candidate_row_key(item_score: tuple[dict[str, Any], float]) -> tuple[float, str, str]:
    item, score = item_score
    return (-score, str(item.get("name", "")).casefold(), str(item["id"]))


def sort_candidate_rows(
    items_with_scores: list[tuple[dict[str, Any], float]],
) -> list[tuple[dict[str, Any], float]]:
    """
    Sort not-found candidates for stable error output (FIN-122 D-09).

    :param items_with_scores: Budget items with ranking score
    :return: Sorted copy
    """
    return sorted(items_with_scores, key=_candidate_row_key)


def _format_item_line(item: dict[str, Any]) -> str:
    """
    Format one budget item line for enriched tool errors.

    :param item: Budget item dict
    :return: Single bullet line
    """
    item_id = str(item["id"])
    name = str(item.get("name", item_id))
    category_id = str(item.get("operation_category_id", ""))
    return f"  - {item_id} | {name} | категория {category_id}"


def _longest_shared_prefix_example(items: list[dict[str, Any]]) -> str:
    """
    Build substring hint from ambiguous match names (FIN-122).

    :param items: Ambiguous budget items
    :return: Shared prefix example or truncated longest name
    """
    normalized_names = [normalize_match_text(str(item.get("name", ""))) for item in items]
    if not normalized_names:
        return ""
    prefix = normalized_names[0]
    for name in normalized_names[1:]:
        limit = min(len(prefix), len(name))
        shared = 0
        while shared < limit and prefix[shared] == name[shared]:
            shared += 1
        prefix = prefix[:shared]
    if len(prefix) >= 4:
        return prefix
    longest = max(normalized_names, key=len)
    if len(longest) <= 20:
        return longest
    return f"{longest[:20]}…"


def format_ambiguous_article_error(article: str, matches: list[dict[str, Any]]) -> str:
    """
    Build enriched ambiguous article tool error (FIN-122).

    :param article: Original article argument
    :param matches: Ambiguous budget items (pre-sorted)
    :return: Multi-line error message
    """
    lines = [
        f"Неоднозначно article {article!r} — найдено {len(matches)} статей:",
        "",
    ]
    lines.extend(_format_item_line(item) for item in matches)
    prefix_example = _longest_shared_prefix_example(matches)
    lines.append("")
    lines.append(
        "Уточните: budget_item_id=<uuid> одного из вариантов "
        f"или более длинная подстрока (напр. {prefix_example}).",
    )
    return "\n".join(lines)


def format_not_found_article_error(
    article: str,
    candidates: list[dict[str, Any]],
) -> str:
    """
    Build enriched not-found article tool error (FIN-122).

    :param article: Original article argument
    :param candidates: Ranked candidate items (pre-sorted, max 5)
    :return: Multi-line error message
    """
    lines = [f"Статья бюджета не найдена по article {article!r}."]
    if candidates:
        lines.extend(["", "Возможные статьи (до 5):"])
        lines.extend(_format_item_line(item) for item in candidates)
    lines.append("")
    lines.append("Уточните: budget_item_id=<uuid> или более точная подстрока имени.")
    return "\n".join(lines)


def _score_article_candidate(
    article: str,
    needle: str,
    item: dict[str, Any],
) -> float:
    """
    Compute ranking score for one not-found candidate (FIN-122).

    :param article: Original article argument
    :param needle: Normalized article text
    :param item: Budget item dict
    :return: Score; 0 when item is not a candidate
    """
    category_id = str(item.get("operation_category_id", "")).strip()
    article_stripped = article.strip()
    name_normalized = normalize_match_text(str(item.get("name", "")))
    scores: list[float] = []

    if category_id == article_stripped:
        scores.append(100.0)

    if (
        _is_category_id_shaped(article_stripped)
        and _is_category_id_shaped(category_id)
        and _levenshtein_at_most_one(category_id, article_stripped)
    ):
        scores.append(90.0)

    if name_normalized == needle:
        scores.append(80.0)
    if needle and needle in name_normalized:
        scores.append(70.0)

    if len(needle) >= 3:
        for token in name_normalized.split():
            if token.startswith(needle):
                scores.append(60.0)
                break

    ratio = SequenceMatcher(None, needle, name_normalized).ratio()
    if ratio >= 0.6:
        scores.append(50.0 + ratio * 10.0)

    return max(scores) if scores else 0.0


def rank_article_candidates(
    article: str,
    active_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Rank and trim not-found candidate budget items (FIN-122 D-01).

    :param article: Original article argument
    :param active_catalog: ACT budget items
    :return: Up to five candidate items
    """
    needle = normalize_match_text(article)
    scored: list[tuple[dict[str, Any], float]] = []
    seen_ids: set[str] = set()
    for item in active_catalog:
        item_id = str(item["id"])
        if item_id in seen_ids:
            continue
        score = _score_article_candidate(article, needle, item)
        if score <= 0:
            continue
        seen_ids.add(item_id)
        scored.append((item, score))
    sorted_rows = sort_candidate_rows(scored)
    return [item for item, _score in sorted_rows[:_CANDIDATE_LIMIT]]


def resolve_budget_item_id(api: ApiClient, article: str | None, budget_item_id: str | None) -> tuple[str, str]:
    """
    Resolve article label to budget item id.

    :param api: API client
    :param article: Substring of article name
    :param budget_item_id: Explicit UUID
    :return: Tuple of item id and display name
    :raises RuntimeError: When article is not found or ambiguous
    :raises ValueError: When neither article nor budget_item_id is provided
    """
    if budget_item_id:
        item = api.get_json(f"/api/v1/budget/items/{budget_item_id}")
        return budget_item_id, str(item.get("name", budget_item_id))

    if not article:
        raise ValueError("Укажите article или budget_item_id")

    catalog = api.get_json("/api/v1/budget/items").get("budget_items", [])
    active_catalog = _active_budget_items(catalog)
    needle = normalize_match_text(article)

    exact_matches = [
        item
        for item in active_catalog
        if normalize_match_text(str(item.get("name", ""))) == needle
    ]
    if len(exact_matches) == 1:
        item = exact_matches[0]
        return str(item["id"]), str(item["name"])
    if len(exact_matches) > 1:
        raise RuntimeError(
            format_ambiguous_article_error(article, sort_ambiguous_rows(exact_matches)),
        )

    substring_matches = [
        item
        for item in active_catalog
        if needle in normalize_match_text(str(item.get("name", "")))
    ]
    if len(substring_matches) == 1:
        item = substring_matches[0]
        return str(item["id"]), str(item["name"])
    if len(substring_matches) > 1:
        raise RuntimeError(
            format_ambiguous_article_error(article, sort_ambiguous_rows(substring_matches)),
        )

    candidates = rank_article_candidates(article, active_catalog)
    raise RuntimeError(format_not_found_article_error(article, candidates))


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
