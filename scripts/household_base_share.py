"""Household base personal-fund share computation (FIN-103)."""

from __future__ import annotations

import json
import re
import urllib.parse
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from finance_api_client import ApiClient
from monthly_close_lib import ASSISTANT_ROOT

SUPPORTED_SCHEMA_VERSION = 1
HOUSEHOLD_API_PATH = "/api/v1/household/base-share"
FORMULA = (
    "free_remainder = household_income - professional - shared_fund - savings; "
    "base_share = round(free_remainder / partner_count, 2)"
)
SANITY_NOTE = (
    "Legacy IRR-подлимиты не равны Σ base_share — ожидаемо при новой модели; "
    "операционный контроль — остаток личного фонда, не строки IRR."
)

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


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


def default_mapping_path(profile: str) -> Path:
    """
    Default contour mapping file for a data profile.

    :param profile: ``test`` / ``cand`` / ``prod``
    :return: Path under ``FINANCE_ASSISTANT_ROOT/methodology/``
    """
    return ASSISTANT_ROOT / "methodology" / f"household-contour-mapping.{profile}.json"


def normalize_period(period: str) -> str:
    """
    Validate and normalize ``YYYY-MM``.

    :param period: Month key
    :return: ``YYYY-MM``
    :raises ValueError: When format is invalid
    """
    text = period.strip()
    if len(text) == 6 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}"
    if not _PERIOD_RE.match(text):
        raise ValueError(f"Ожидается period YYYY-MM, получено: {period!r}")
    return text


def period_start(yyyy_mm: str) -> str:
    """
    Convert ``YYYY-MM`` to plan-actual period start.

    :param yyyy_mm: Month key
    :return: ``YYYY-MM-01``
    """
    return f"{yyyy_mm}-01"


def round_money(value: float) -> float:
    """
    Round to cents (half-up).

    :param value: Amount in EUR
    :return: Rounded amount
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def load_mapping_file(path: Path) -> dict[str, Any]:
    """
    Load and parse contour mapping JSON.

    :param path: Mapping file path
    :return: Parsed mapping dict
    :raises RuntimeError: When file missing or JSON invalid
    """
    if not path.is_file():
        raise RuntimeError(f"Mapping file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid mapping JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid mapping JSON: {path}: expected object")
    return data


def validate_mapping_structure(mapping: dict[str, Any], profile: str) -> None:
    """
    Validate mapping schema before article resolution.

    :param mapping: Parsed mapping
    :param profile: Request data profile
    :raises RuntimeError: On invalid mapping
    """
    schema_version = mapping.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported mapping schema_version: {schema_version!r} "
            f"(expected {SUPPORTED_SCHEMA_VERSION})"
        )
    if mapping.get("profile") != profile:
        raise RuntimeError(
            f"Mapping profile {mapping.get('profile')!r} != request profile {profile!r}"
        )
    partners = mapping.get("partners")
    if not isinstance(partners, list) or len(partners) < 1:
        raise RuntimeError("invalid mapping: empty partners")


def load_budget_items(api: ApiClient) -> list[dict[str, Any]]:
    """
    Load budget item catalog.

    :param api: API client
    :return: Budget item dicts
    """
    data = api.get_json("/api/v1/budget/items")
    items = data.get("budget_items", [])
    if not isinstance(items, list):
        raise RuntimeError("GET /budget/items: budget_items is not a list")
    return items


def resolve_article_match(
    article_match: str,
    budget_items: list[dict[str, Any]],
    *,
    required: bool = True,
) -> tuple[str, str] | None:
    """
    Resolve substring match to a single budget item.

    :param article_match: Case-insensitive substring
    :param budget_items: Catalog from API
    :param required: When ``False``, return ``None`` if no match
    :return: Tuple of item id and display name, or ``None``
    :raises RuntimeError: When ambiguous or missing required match
    """
    needle = article_match.casefold()
    exact = [
        item
        for item in budget_items
        if str(item.get("name", "")).casefold() == needle
    ]
    if len(exact) == 1:
        item = exact[0]
        return str(item["id"]), str(item.get("name", item["id"]))
    matches = [
        item
        for item in budget_items
        if needle in str(item.get("name", "")).casefold()
    ]
    if not matches:
        if required:
            raise RuntimeError(f"Статья бюджета не найдена по article_match {article_match!r}")
        return None
    if len(matches) > 1:
        names = ", ".join(str(m.get("name")) for m in matches)
        raise RuntimeError(f"Неоднозначно article_match {article_match!r}: {names}")
    item = matches[0]
    return str(item["id"]), str(item.get("name", item["id"]))


def fetch_period_plans(
    api: ApiClient,
    budget_version_id: str,
    yyyy_mm: str,
) -> dict[str, float]:
    """
    Load plan amounts for all budget items in one grouped plan-actual call.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param yyyy_mm: Month ``YYYY-MM``
    :return: Map budget_item_id → plan amount
    """
    query = urllib.parse.urlencode(
        {
            "budget_version_id": budget_version_id,
            "period": period_start(yyyy_mm),
            "view": "grouped",
        }
    )
    data = api.get_json(f"/api/v1/budget/plan-actual?{query}")
    plans: dict[str, float] = {}
    for node in data.get("grid_nodes", []):
        if node.get("kind") != "row":
            continue
        item_id = node.get("budget_item_id")
        if not item_id:
            continue
        plans[str(item_id)] = round_money(parse_amount(node.get("plan_amount")))
    return plans


def probe_household_api(api: ApiClient, yyyy_mm: str) -> tuple[str, dict[str, Any] | None]:
    """
    Probe FIN-102 household base-share endpoint.

    :param api: API client
    :param yyyy_mm: Month ``YYYY-MM``
    :return: ``("api", body)`` or ``("mapping", None)``
    :raises RuntimeError: On 5xx or unexpected errors
    """
    query = urllib.parse.urlencode({"period": period_start(yyyy_mm)})
    status, body = api.request("GET", f"{HOUSEHOLD_API_PATH}?{query}")
    if status == 200 and isinstance(body, dict):
        return "api", body
    if status == 404:
        return "mapping", None
    if status >= 500:
        raise RuntimeError(f"GET {HOUSEHOLD_API_PATH} -> HTTP {status}: {body}")
    raise RuntimeError(f"GET {HOUSEHOLD_API_PATH} -> HTTP {status}: {body}")


def _line_entry(
    article_match: str,
    item_id: str,
    article: str,
    plan: float,
) -> dict[str, Any]:
    return {
        "article_match": article_match,
        "budget_item_id": item_id,
        "article": article,
        "plan": plan,
    }


def _resolve_contour_lines(
    entries: list[dict[str, Any]],
    budget_items: list[dict[str, Any]],
    plans: dict[str, float],
    *,
    required: bool,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Resolve mapping entries to plan lines.

    :return: Lines and resolved ``budget_item_id`` values
    """
    lines: list[dict[str, Any]] = []
    item_ids: list[str] = []
    for entry in entries:
        match = str(entry.get("article_match", ""))
        resolved = resolve_article_match(match, budget_items, required=required)
        if resolved is None:
            warnings.append(f"mapping_sanity_miss:{match}")
            continue
        item_id, article = resolved
        plan = plans.get(item_id, 0.0)
        lines.append(_line_entry(match, item_id, article, plan))
        item_ids.append(item_id)
    return lines, item_ids


def _validate_contour_assignments(
    tagged: list[tuple[str, str]],
    *,
    include_ids: set[str],
    exclude_ids: set[str],
) -> None:
    overlap = include_ids & exclude_ids
    if overlap:
        raise RuntimeError("mapping validation: include/exclude overlap")
    seen: dict[str, str] = {}
    for item_id, contour in tagged:
        if item_id in seen:
            if seen[item_id] != contour:
                raise RuntimeError("mapping validation: duplicate contour assignment")
        else:
            seen[item_id] = contour


def _collect_warnings_unmapped_inc(
    budget_items: list[dict[str, Any]],
    plans: dict[str, float],
    mapped_inc_ids: set[str],
) -> list[str]:
    warnings: list[str] = []
    for item in budget_items:
        if item.get("flow_type") != "INC":
            continue
        item_id = str(item["id"])
        if item_id in mapped_inc_ids:
            continue
        plan = plans.get(item_id, 0.0)
        if plan > 0:
            name = str(item.get("name", item_id))
            warnings.append(f"unmapped_income:{name}")
    return warnings


def _build_sanity_check(
    legacy_lines: list[dict[str, Any]],
    subscription_lines: list[dict[str, Any]],
    free_remainder: float,
    base_share: float,
    partner_count: int,
) -> dict[str, Any]:
    legacy_total = round_money(sum(line["plan"] for line in legacy_lines))
    subscriptions_total = round_money(sum(line["plan"] for line in subscription_lines))
    combined = round_money(legacy_total + subscriptions_total)
    two_base_shares = round_money(base_share * partner_count)
    return {
        "legacy_irr_total": legacy_total,
        "personal_subscriptions_total": subscriptions_total,
        "combined_legacy_personal": combined,
        "two_base_shares": two_base_shares,
        "rounding_delta": round_money(two_base_shares - free_remainder),
        "delta_vs_two_base_shares": round_money(combined - two_base_shares),
        "note": SANITY_NOTE,
    }


def _validate_api_contour_uniqueness(payload: dict[str, Any]) -> None:
    """Ensure API payload does not assign one budget item to multiple calculation contours."""
    seen: dict[str, str] = {}

    def scan_lines(lines: list[dict[str, Any]], contour: str) -> None:
        for line in lines:
            item_id = str(line.get("budget_item_id", ""))
            if not item_id:
                continue
            if item_id in seen and seen[item_id] != contour:
                raise RuntimeError("mapping validation: duplicate contour assignment")
            seen[item_id] = contour

    income = payload.get("household_income") or {}
    scan_lines(list(income.get("lines") or []), "household_income.include")
    for row in list(income.get("excluded_income") or []):
        item_id = str(row.get("budget_item_id", ""))
        if item_id:
            if item_id in seen and seen[item_id] != "household_income.exclude":
                raise RuntimeError("mapping validation: duplicate contour assignment")
            seen[item_id] = "household_income.exclude"
    prof = payload.get("professional") or {}
    for partner_id, block in (prof.get("by_partner") or {}).items():
        scan_lines(list(block.get("lines") or []), f"professional.{partner_id}")
    scan_lines(list((payload.get("shared_fund") or {}).get("lines") or []), "shared_fund")
    scan_lines(list((payload.get("savings") or {}).get("lines") or []), "savings")


def finalize_api_payload(
    api_body: dict[str, Any],
    *,
    profile: str,
    base: str,
    period: str,
    budget_version_id: str | None,
) -> dict[str, Any]:
    """
    Normalize FIN-102 API response to MCP contract.

    :param api_body: Raw API JSON
    :param profile: Data profile
    :param base: API base URL
    :param period: ``YYYY-MM``
    :param budget_version_id: Optional version id from API body
    :return: Tool response dict
    :raises RuntimeError: On invalid API payload
    """
    _validate_api_contour_uniqueness(api_body)
    partners_raw = list(api_body.get("partners") or [])
    if not partners_raw:
        raise RuntimeError("invalid mapping: empty partners")
    partner_count = len(partners_raw)
    payload = dict(api_body)
    payload.update(
        {
            "ok": True,
            "profile": profile,
            "base": base,
            "period": period,
            "budget_version_id": budget_version_id or api_body.get("budget_version_id"),
            "mapping_path": None,
            "mapping_schema_version": None,
            "source": "api",
            "formula": FORMULA,
            "partner_count": partner_count,
            "warnings": list(api_body.get("warnings") or []),
        }
    )
    if "sanity_check" not in payload:
        base_share = float(partners_raw[0].get("base_share", 0.0))
        free_remainder = float(payload.get("free_remainder", 0.0))
        payload["sanity_check"] = _build_sanity_check(
            [], [], free_remainder, base_share, partner_count
        )
    return payload


def compute_from_mapping(
    api: ApiClient,
    mapping: dict[str, Any],
    *,
    profile: str,
    base: str,
    yyyy_mm: str,
    budget_version_id: str,
    mapping_path: Path,
) -> dict[str, Any]:
    """
    Compute base share from contour mapping and plan amounts.

    :param api: API client
    :param mapping: Parsed mapping JSON
    :param profile: Data profile
    :param base: API base URL
    :param yyyy_mm: Month ``YYYY-MM``
    :param budget_version_id: Budget version UUID
    :param mapping_path: Path to mapping file used
    :return: Tool response dict
    """
    validate_mapping_structure(mapping, profile)
    budget_items = load_budget_items(api)
    plans = fetch_period_plans(api, budget_version_id, yyyy_mm)
    warnings: list[str] = []

    income_cfg = mapping.get("household_income") or {}
    include_entries = list(income_cfg.get("include") or [])
    exclude_entries = list(income_cfg.get("exclude") or [])

    income_lines, include_ids_list = _resolve_contour_lines(
        include_entries, budget_items, plans, required=True, warnings=warnings
    )
    tagged: list[tuple[str, str]] = [
        (item_id, "household_income.include") for item_id in include_ids_list
    ]
    include_ids = set(include_ids_list)

    excluded_income: list[dict[str, Any]] = []
    exclude_ids: set[str] = set()
    for entry in exclude_entries:
        match = str(entry.get("article_match", ""))
        resolved = resolve_article_match(match, budget_items, required=True)
        assert resolved is not None
        item_id, article = resolved
        exclude_ids.add(item_id)
        tagged.append((item_id, "household_income.exclude"))
        excluded_income.append(
            {
                "article_match": match,
                "budget_item_id": item_id,
                "article": article,
                "plan": plans.get(item_id, 0.0),
                "reason": entry.get("reason"),
            }
        )

    if include_ids & exclude_ids:
        raise RuntimeError("mapping validation: include/exclude overlap")

    household_income_total = round_money(sum(line["plan"] for line in income_lines))

    professional_cfg = mapping.get("professional") or {}
    partner_defs = list(mapping.get("partners") or [])
    by_partner: dict[str, dict[str, Any]] = {}
    professional_total = 0.0
    for partner in partner_defs:
        partner_id = str(partner["id"])
        entries = list(professional_cfg.get(partner_id) or [])
        lines, prof_ids = _resolve_contour_lines(
            entries, budget_items, plans, required=True, warnings=warnings
        )
        for item_id in prof_ids:
            tagged.append((item_id, f"professional.{partner_id}"))
        total = round_money(sum(line["plan"] for line in lines))
        professional_total += total
        by_partner[partner_id] = {"total": total, "lines": lines}

    shared_lines, shared_ids = _resolve_contour_lines(
        list(mapping.get("shared_fund") or []),
        budget_items,
        plans,
        required=True,
        warnings=warnings,
    )
    for item_id in shared_ids:
        tagged.append((item_id, "shared_fund"))
    shared_total = round_money(sum(line["plan"] for line in shared_lines))

    savings_lines, savings_ids = _resolve_contour_lines(
        list(mapping.get("savings") or []),
        budget_items,
        plans,
        required=True,
        warnings=warnings,
    )
    for item_id in savings_ids:
        tagged.append((item_id, "savings"))
    savings_total = round_money(sum(line["plan"] for line in savings_lines))

    _validate_contour_assignments(
        tagged, include_ids=include_ids, exclude_ids=exclude_ids
    )

    mapped_inc_ids = include_ids | exclude_ids
    warnings.extend(
        _collect_warnings_unmapped_inc(budget_items, plans, mapped_inc_ids)
    )

    legacy_lines, _ = _resolve_contour_lines(
        list(mapping.get("legacy_irr_sanity") or []),
        budget_items,
        plans,
        required=False,
        warnings=warnings,
    )
    subscription_lines, _ = _resolve_contour_lines(
        list(mapping.get("personal_subscriptions_sanity") or []),
        budget_items,
        plans,
        required=False,
        warnings=warnings,
    )

    free_remainder = round_money(
        household_income_total - professional_total - shared_total - savings_total
    )
    partner_count = len(partner_defs)
    base_share = round_money(free_remainder / partner_count)

    if free_remainder < 0:
        warnings.append("negative_free_remainder")

    partners_out = [
        {
            "id": str(partner["id"]),
            "display_name": str(partner.get("display_name", partner["id"])),
            "base_share": base_share,
        }
        for partner in partner_defs
    ]

    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "period": yyyy_mm,
        "budget_version_id": budget_version_id,
        "mapping_path": str(mapping_path),
        "mapping_schema_version": mapping.get("schema_version"),
        "source": "mapping",
        "formula": FORMULA,
        "household_income": {
            "total": household_income_total,
            "lines": income_lines,
            "excluded_income": excluded_income,
        },
        "professional": {
            "total": round_money(professional_total),
            "by_partner": by_partner,
        },
        "shared_fund": {"total": shared_total, "lines": shared_lines},
        "savings": {"total": savings_total, "lines": savings_lines},
        "free_remainder": free_remainder,
        "partner_count": partner_count,
        "partners": partners_out,
        "sanity_check": _build_sanity_check(
            legacy_lines,
            subscription_lines,
            free_remainder,
            base_share,
            partner_count,
        ),
        "warnings": warnings,
    }


def compute_household_base_share(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    period: str,
    budget_version_id: str,
    mapping_path: str | None = None,
) -> dict[str, Any]:
    """
    MCP entry point: probe API or compute from mapping.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param period: Target month ``YYYY-MM`` or ``YYYYMM``
    :param budget_version_id: Active budget version UUID
    :param mapping_path: Optional mapping file override
    :return: Tool response payload
    """
    yyyy_mm = normalize_period(period)
    source, api_body = probe_household_api(api, yyyy_mm)
    if source == "api" and api_body is not None:
        return finalize_api_payload(
            api_body,
            profile=profile,
            base=base,
            period=yyyy_mm,
            budget_version_id=budget_version_id,
        )

    path = Path(mapping_path) if mapping_path else default_mapping_path(profile)
    mapping = load_mapping_file(path)
    return compute_from_mapping(
        api,
        mapping,
        profile=profile,
        base=base,
        yyyy_mm=yyyy_mm,
        budget_version_id=budget_version_id,
        mapping_path=path,
    )
