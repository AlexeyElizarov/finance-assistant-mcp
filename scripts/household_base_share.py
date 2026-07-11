"""Household base personal-fund share computation (FIN-103, FIN-121, FIN-114)."""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from finance_api_client import ApiClient
from fx_rates import FX_RATES_PATH, format_api_error
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

INCOME_MODE_SALARY_ONLY = "salary_only"
INCOME_MODE_SALARY_PLUS_PARTNER = "salary_plus_partner_contribution"
INCOME_MODE_MAPPING_DEFAULT = "mapping_default"
_VALID_INCOME_MODES = frozenset(
    {
        INCOME_MODE_MAPPING_DEFAULT,
        INCOME_MODE_SALARY_ONLY,
        INCOME_MODE_SALARY_PLUS_PARTNER,
    }
)
_PRESET_SALARY_NEEDLE = "заработная плата"
_PRESET_PARTNER_NEEDLE = "взнос николая"

REASON_MAPPING_EXCLUDE = "mapping:exclude"
REASON_OVERRIDE_EXCLUDE = "override:exclude"
REASON_INCOME_MODE_SALARY_ONLY = "income_mode:salary_only"
REASON_INCOME_MODE_SALARY_PLUS_PARTNER = (
    "income_mode:salary_plus_partner_contribution"
)


@dataclass(frozen=True)
class IncomeFilterParams:
    """Runtime household income composition (FIN-121)."""

    income_mode: str | None
    include_income_matches: tuple[str, ...]
    exclude_income_matches: tuple[str, ...]


def strip_income_match_list(matches: list[str] | None) -> list[str]:
    """
  Strip and drop empty override match strings.

  :param matches: Raw match list from MCP/CLI
  :return: Non-empty trimmed strings
  """
    if not matches:
        return []
    return [text.strip() for text in matches if text and text.strip()]


def normalize_income_mode(raw: str | None) -> str | None:
    """
  Normalize ``income_mode`` preset.

  :param raw: Request value or ``None``
  :return: Active preset name or ``None`` for default
  :raises RuntimeError: When value is unknown
  """
    if raw is None:
        return None
    text = raw.strip()
    if not text or text == INCOME_MODE_MAPPING_DEFAULT:
        return None
    if text in (INCOME_MODE_SALARY_ONLY, INCOME_MODE_SALARY_PLUS_PARTNER):
        return text
    raise RuntimeError(f"Unknown income_mode: {raw!r}")


def parse_income_filter_params(
    *,
    income_mode: str | None = None,
    include_income_matches: list[str] | None = None,
    exclude_income_matches: list[str] | None = None,
) -> IncomeFilterParams:
    """
  Build normalized income filter parameters.

  :param income_mode: Optional preset
  :param include_income_matches: Optional include overrides
  :param exclude_income_matches: Optional exclude overrides
  :return: Normalized filter params
  """
    return IncomeFilterParams(
        income_mode=normalize_income_mode(income_mode),
        include_income_matches=tuple(strip_income_match_list(include_income_matches)),
        exclude_income_matches=tuple(strip_income_match_list(exclude_income_matches)),
    )


def income_filter_is_active(params: IncomeFilterParams) -> bool:
    """
  Whether any FIN-121 income filter is active.

  :param params: Normalized filter params
  :return: ``True`` when preset or overrides are set
  """
    return (
        params.income_mode is not None
        or bool(params.include_income_matches)
        or bool(params.exclude_income_matches)
    )


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


def _flow_type_for_item(budget_items: list[dict[str, Any]], item_id: str) -> str | None:
    for item in budget_items:
        if str(item.get("id")) == item_id:
            return str(item.get("flow_type")) if item.get("flow_type") is not None else None
    return None


def _resolve_income_override_ids(
    matches: tuple[str, ...],
    budget_items: list[dict[str, Any]],
) -> set[str]:
    """
  Resolve include/exclude override matches to INC budget item ids.

  :param matches: ``article_match`` strings
  :param budget_items: Budget catalog
  :return: Unique resolved item ids
  :raises RuntimeError: On missing, ambiguous, or non-INC match
  """
    ids: set[str] = set()
    for match in matches:
        resolved = resolve_article_match(match, budget_items, required=True)
        assert resolved is not None
        item_id, article = resolved
        if _flow_type_for_item(budget_items, item_id) != "INC":
            raise RuntimeError(f"income match not inc: {article}")
        ids.add(item_id)
    return ids


def _apply_income_mode_preset(
    income_mode: str | None,
    mapping_include_lines: dict[str, dict[str, Any]],
) -> tuple[set[str], str | None]:
    """
  Filter mapping-include ids by preset substring rules.

  :param income_mode: Active preset or ``None``
  :param mapping_include_lines: Resolved mapping include lines by id
  :return: Preset id set and mode suffix for excluded reasons
  """
    all_ids = set(mapping_include_lines.keys())
    if income_mode is None:
        return all_ids, None
    if income_mode == INCOME_MODE_SALARY_ONLY:
        preset_ids = {
            item_id
            for item_id, line in mapping_include_lines.items()
            if _PRESET_SALARY_NEEDLE in str(line.get("article", "")).casefold()
        }
        return preset_ids, INCOME_MODE_SALARY_ONLY
    if income_mode == INCOME_MODE_SALARY_PLUS_PARTNER:
        preset_ids = {
            item_id
            for item_id, line in mapping_include_lines.items()
            if _PRESET_SALARY_NEEDLE in str(line.get("article", "")).casefold()
            or _PRESET_PARTNER_NEEDLE in str(line.get("article", "")).casefold()
        }
        return preset_ids, INCOME_MODE_SALARY_PLUS_PARTNER
    raise RuntimeError(f"Unknown income_mode: {income_mode!r}")


def _excluded_reason_for_item(
    item_id: str,
    *,
    exclude_override_ids: set[str],
    mapping_exclude_ids: set[str],
    mapping_include_ids: set[str],
    preset_ids: set[str],
    income_mode_suffix: str | None,
) -> str:
    if item_id in exclude_override_ids:
        return REASON_OVERRIDE_EXCLUDE
    if item_id in mapping_exclude_ids:
        return REASON_MAPPING_EXCLUDE
    if (
        item_id in mapping_include_ids
        and item_id not in preset_ids
        and income_mode_suffix is not None
    ):
        return f"income_mode:{income_mode_suffix}"
    return REASON_MAPPING_EXCLUDE


def _build_income_resolution(
    params: IncomeFilterParams,
    *,
    mapping_include_count: int,
    effective_include_count: int,
) -> dict[str, Any]:
    return {
        "income_mode": params.income_mode,
        "include_income_matches": list(params.include_income_matches),
        "exclude_income_matches": list(params.exclude_income_matches),
        "mapping_include_count": mapping_include_count,
        "effective_include_count": effective_include_count,
    }


def resolve_household_income(
    mapping_include_lines: dict[str, dict[str, Any]],
    mapping_exclude_lines: dict[str, dict[str, Any]],
    params: IncomeFilterParams,
    budget_items: list[dict[str, Any]],
    plans: dict[str, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """
  Resolve effective household income lines and exclusions (FIN-121).

  :param mapping_include_lines: Mapping include lines keyed by budget item id
  :param mapping_exclude_lines: Mapping exclude lines keyed by budget item id
  :param params: Income filter parameters
  :param budget_items: Budget catalog
  :param plans: Plan amounts for the target month
  :return: Income lines, excluded income rows, income_resolution block
  """
    mapping_include_ids = set(mapping_include_lines.keys())
    mapping_exclude_ids = set(mapping_exclude_lines.keys())

    preset_ids, income_mode_suffix = _apply_income_mode_preset(
        params.income_mode, mapping_include_lines
    )
    include_override_ids = _resolve_income_override_ids(
        params.include_income_matches, budget_items
    )
    exclude_override_ids = _resolve_income_override_ids(
        params.exclude_income_matches, budget_items
    )

    if include_override_ids & exclude_override_ids:
        raise RuntimeError("income override conflict")

    effective_include_ids = (preset_ids | include_override_ids) - exclude_override_ids

    known_lines = dict(mapping_include_lines)
    known_lines.update(mapping_exclude_lines)
    for match in params.include_income_matches:
        resolved = resolve_article_match(match, budget_items, required=True)
        assert resolved is not None
        item_id, article = resolved
        if item_id not in known_lines:
            known_lines[item_id] = _line_entry(
                match, item_id, article, plans.get(item_id, 0.0)
            )

    line_order: list[str] = []
    for item_id in mapping_include_lines:
        if item_id in effective_include_ids and item_id not in line_order:
            line_order.append(item_id)
    for item_id in include_override_ids:
        if item_id in effective_include_ids and item_id not in line_order:
            line_order.append(item_id)

    income_lines = [known_lines[item_id] for item_id in line_order]

    considered_ids = mapping_include_ids | mapping_exclude_ids | include_override_ids
    excluded_ids = sorted(considered_ids - effective_include_ids)
    excluded_income: list[dict[str, Any]] = []
    for item_id in excluded_ids:
        line = known_lines[item_id]
        excluded_income.append(
            {
                "article_match": line["article_match"],
                "budget_item_id": item_id,
                "article": line["article"],
                "plan": line["plan"],
                "reason": _excluded_reason_for_item(
                    item_id,
                    exclude_override_ids=exclude_override_ids,
                    mapping_exclude_ids=mapping_exclude_ids,
                    mapping_include_ids=mapping_include_ids,
                    preset_ids=preset_ids,
                    income_mode_suffix=income_mode_suffix,
                ),
            }
        )

    resolution = _build_income_resolution(
        params,
        mapping_include_count=len(mapping_include_ids),
        effective_include_count=len(income_lines),
    )
    return income_lines, excluded_income, resolution


def _recalculate_free_remainder_and_shares(payload: dict[str, Any]) -> None:
    household_total = float((payload.get("household_income") or {}).get("total", 0.0))
    professional_total = float((payload.get("professional") or {}).get("total", 0.0))
    shared_total = float((payload.get("shared_fund") or {}).get("total", 0.0))
    savings_total = float((payload.get("savings") or {}).get("total", 0.0))
    free_remainder = round_money(
        household_total - professional_total - shared_total - savings_total
    )
    payload["free_remainder"] = free_remainder
    partners = list(payload.get("partners") or [])
    partner_count = len(partners)
    base_share = round_money(free_remainder / partner_count) if partner_count else 0.0
    for partner in partners:
        partner["base_share"] = base_share
    warnings = list(payload.get("warnings") or [])
    if free_remainder < 0 and "negative_free_remainder" not in warnings:
        warnings.append("negative_free_remainder")
    payload["warnings"] = warnings
    sanity = payload.get("sanity_check")
    if isinstance(sanity, dict):
        sanity["two_base_shares"] = round_money(base_share * partner_count)
        sanity["rounding_delta"] = round_money(
            sanity["two_base_shares"] - free_remainder
        )
        combined = float(sanity.get("combined_legacy_personal", 0.0))
        sanity["delta_vs_two_base_shares"] = round_money(
            combined - sanity["two_base_shares"]
        )


def apply_income_filter_to_payload(
    payload: dict[str, Any],
    params: IncomeFilterParams,
    *,
    budget_items: list[dict[str, Any]],
    plans: dict[str, float],
) -> None:
    """
  Post-filter normalized API household income (FIN-121).

  :param payload: Normalized tool payload with ``household_income``
  :param params: Income filter parameters
  :param budget_items: Budget catalog for override resolution
  :param plans: Plan amounts for override additions
  :raises RuntimeError: When income block is missing for filtering
  """
    income = payload.get("household_income")
    if not isinstance(income, dict):
        raise RuntimeError("household income data missing for income filter")
    raw_lines = income.get("lines")
    if raw_lines is None:
        raise RuntimeError("household income data missing for income filter")

    include_lines: dict[str, dict[str, Any]] = {}
    for row in list(raw_lines or []):
        item_id = str(row.get("budget_item_id", ""))
        if not item_id:
            continue
        include_lines[item_id] = {
            "article_match": str(row.get("article_match", row.get("article", ""))),
            "budget_item_id": item_id,
            "article": str(row.get("article", item_id)),
            "plan": round_money(parse_amount(row.get("plan"))),
        }
    exclude_lines: dict[str, dict[str, Any]] = {}
    for row in list(income.get("excluded_income") or []):
        item_id = str(row.get("budget_item_id", ""))
        if not item_id:
            continue
        exclude_lines[item_id] = {
            "article_match": str(row.get("article_match", row.get("article", ""))),
            "budget_item_id": item_id,
            "article": str(row.get("article", item_id)),
            "plan": round_money(parse_amount(row.get("plan"))),
        }

    lines, excluded, resolution = resolve_household_income(
        include_lines,
        exclude_lines,
        params,
        budget_items,
        plans,
    )
    income["lines"] = lines
    income["excluded_income"] = excluded
    income["total"] = round_money(sum(line["plan"] for line in lines))
    income["income_resolution"] = resolution
    payload["household_income"] = income
    _recalculate_free_remainder_and_shares(payload)


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
    path = f"/api/v1/budget/plan-actual?{query}"
    data = api.get_json(path)
    plans: dict[str, float] = {}
    for node in data.get("grid_nodes", []):
        if node.get("kind") != "row":
            continue
        item_id = node.get("budget_item_id")
        if not item_id:
            continue
        plans[str(item_id)] = round_money(parse_amount(node.get("plan_amount")))
    return plans


def _raise_api_error(status: int, body: Any, *, method: str, path: str) -> None:
    raise RuntimeError(format_api_error(status, body, method=method, path=path))


def _lookup_fx_rate(api: ApiClient, period: str) -> str:
    """
    Load canonical RUB→EUR rate for one month.

    :param api: API client
    :param period: Month start ``YYYY-MM-DD``
    :return: Canonical rate string
    """
    query = urllib.parse.urlencode({"period": period})
    path = f"{FX_RATES_PATH}?{query}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    rates = body.get("fx_rates", [])
    if not isinstance(rates, list) or not rates:
        raise RuntimeError(
            f"fx_rate_missing: no planned rate for period {period!r}"
        )
    return str(rates[0].get("rate", ""))


def fetch_period_plans_eur(
    api: ApiClient,
    budget_version_id: str,
    yyyy_mm: str,
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """
    Load EUR plan amounts via flat plan-actual with ``convert_to_eur=true``.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param yyyy_mm: Month ``YYYY-MM``
    :return: Plan map and optional ``amount_detail`` blocks for non-EUR lines
    """
    month_start = period_start(yyyy_mm)
    query = urllib.parse.urlencode(
        {
            "budget_version_id": budget_version_id,
            "period": month_start,
            "convert_to_eur": "true",
        }
    )
    path = f"/api/v1/budget/plan-actual?{query}"
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        _raise_api_error(status, body, method="GET", path=path)
    rows = body.get("plan_actual_month_rows", [])
    if not isinstance(rows, list):
        raise RuntimeError("GET /budget/plan-actual: plan_actual_month_rows is not a list")

    fx_rate: str | None = None
    plans: dict[str, float] = {}
    plan_details: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        item_id = row.get("budget_item_id")
        if not item_id:
            continue
        item_key = str(item_id)
        currency = str(row.get("currency", "EUR")).upper()
        plan_eur = round_money(parse_amount(row.get("plan_amount_eur")))
        plans[item_key] = plan_eur
        if currency == "EUR":
            continue
        if fx_rate is None:
            fx_rate = _lookup_fx_rate(api, month_start)
        plan_details[item_key] = {
            "native_amount": round_money(parse_amount(row.get("plan_amount"))),
            "native_currency": currency,
            "fx_rate": fx_rate,
            "fx_period": str(row.get("period", month_start)),
        }
    return plans, plan_details


def fetch_period_plan_data(
    api: ApiClient,
    budget_version_id: str,
    yyyy_mm: str,
    *,
    convert_plans_to_eur: bool = True,
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """
    Load plan amounts for household contour computation.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param yyyy_mm: Month ``YYYY-MM``
    :param convert_plans_to_eur: Use FIN-114 EUR conversion pipeline
    :return: Plan map and optional amount detail blocks
    """
    if convert_plans_to_eur:
        return fetch_period_plans_eur(api, budget_version_id, yyyy_mm)
    return fetch_period_plans(api, budget_version_id, yyyy_mm), {}


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
    *,
    amount_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "article_match": article_match,
        "budget_item_id": item_id,
        "article": article,
        "plan": plan,
    }
    if amount_detail is not None:
        line["amount_detail"] = amount_detail
    return line


def _resolve_contour_lines(
    entries: list[dict[str, Any]],
    budget_items: list[dict[str, Any]],
    plans: dict[str, float],
    *,
    required: bool,
    warnings: list[str],
    plan_details: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Resolve mapping entries to plan lines.

    :return: Lines and resolved ``budget_item_id`` values
    """
    details = plan_details or {}
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
        lines.append(
            _line_entry(
                match,
                item_id,
                article,
                plan,
                amount_detail=details.get(item_id),
            )
        )
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
    income_filter: IncomeFilterParams | None = None,
    convert_plans_to_eur: bool = True,
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
    :param income_filter: Optional FIN-121 income composition filter
    :param convert_plans_to_eur: Use EUR conversion pipeline (FIN-114)
    :return: Tool response dict
    """
    filter_params = income_filter or parse_income_filter_params()
    validate_mapping_structure(mapping, profile)
    budget_items = load_budget_items(api)
    plans, plan_details = fetch_period_plan_data(
        api,
        budget_version_id,
        yyyy_mm,
        convert_plans_to_eur=convert_plans_to_eur,
    )
    warnings: list[str] = []

    income_cfg = mapping.get("household_income") or {}
    include_entries = list(income_cfg.get("include") or [])
    exclude_entries = list(income_cfg.get("exclude") or [])

    mapping_include_lines: dict[str, dict[str, Any]] = {}
    include_ids_list: list[str] = []
    for entry in include_entries:
        match = str(entry.get("article_match", ""))
        resolved = resolve_article_match(match, budget_items, required=True)
        assert resolved is not None
        item_id, article = resolved
        mapping_include_lines[item_id] = _line_entry(
            match,
            item_id,
            article,
            plans.get(item_id, 0.0),
            amount_detail=plan_details.get(item_id),
        )
        include_ids_list.append(item_id)

    tagged: list[tuple[str, str]] = [
        (item_id, "household_income.include") for item_id in include_ids_list
    ]
    include_ids = set(include_ids_list)

    mapping_exclude_lines: dict[str, dict[str, Any]] = {}
    exclude_ids: set[str] = set()
    for entry in exclude_entries:
        match = str(entry.get("article_match", ""))
        resolved = resolve_article_match(match, budget_items, required=True)
        assert resolved is not None
        item_id, article = resolved
        exclude_ids.add(item_id)
        mapping_exclude_lines[item_id] = _line_entry(
            match,
            item_id,
            article,
            plans.get(item_id, 0.0),
            amount_detail=plan_details.get(item_id),
        )
        tagged.append((item_id, "household_income.exclude"))

    if include_ids & exclude_ids:
        raise RuntimeError("mapping validation: include/exclude overlap")

    income_lines, excluded_income, income_resolution = resolve_household_income(
        mapping_include_lines,
        mapping_exclude_lines,
        filter_params,
        budget_items,
        plans,
    )
    household_income_total = round_money(sum(line["plan"] for line in income_lines))

    professional_cfg = mapping.get("professional") or {}
    partner_defs = list(mapping.get("partners") or [])
    by_partner: dict[str, dict[str, Any]] = {}
    professional_total = 0.0
    for partner in partner_defs:
        partner_id = str(partner["id"])
        entries = list(professional_cfg.get(partner_id) or [])
        lines, prof_ids = _resolve_contour_lines(
            entries,
            budget_items,
            plans,
            required=True,
            warnings=warnings,
            plan_details=plan_details,
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
        plan_details=plan_details,
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
        plan_details=plan_details,
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
        plan_details=plan_details,
    )
    subscription_lines, _ = _resolve_contour_lines(
        list(mapping.get("personal_subscriptions_sanity") or []),
        budget_items,
        plans,
        required=False,
        warnings=warnings,
        plan_details=plan_details,
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
            "income_resolution": income_resolution,
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
    income_mode: str | None = None,
    include_income_matches: list[str] | None = None,
    exclude_income_matches: list[str] | None = None,
    convert_plans_to_eur: bool = True,
) -> dict[str, Any]:
    """
    MCP entry point: probe API or compute from mapping.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param period: Target month ``YYYY-MM`` or ``YYYYMM``
    :param budget_version_id: Active budget version UUID
    :param mapping_path: Optional mapping file override
    :param income_mode: Optional FIN-121 income preset
    :param include_income_matches: Optional include overrides
    :param exclude_income_matches: Optional exclude overrides
    :param convert_plans_to_eur: Use EUR conversion pipeline (FIN-114)
    :return: Tool response payload
    """
    yyyy_mm = normalize_period(period)
    income_filter = parse_income_filter_params(
        income_mode=income_mode,
        include_income_matches=include_income_matches,
        exclude_income_matches=exclude_income_matches,
    )
    source, api_body = probe_household_api(api, yyyy_mm)
    if source == "api" and api_body is not None:
        payload = finalize_api_payload(
            api_body,
            profile=profile,
            base=base,
            period=yyyy_mm,
            budget_version_id=budget_version_id,
        )
        if income_filter_is_active(income_filter):
            budget_items = load_budget_items(api)
            plans, _ = fetch_period_plan_data(
                api,
                budget_version_id,
                yyyy_mm,
                convert_plans_to_eur=convert_plans_to_eur,
            )
            apply_income_filter_to_payload(
                payload,
                income_filter,
                budget_items=budget_items,
                plans=plans,
            )
        return payload

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
        income_filter=income_filter,
        convert_plans_to_eur=convert_plans_to_eur,
    )
