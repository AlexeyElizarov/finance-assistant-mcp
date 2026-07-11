"""Weekly household money check report orchestration (FIN-104)."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finance_api_client import ApiClient
from household_advances import (
    default_mapping_path,
    load_ledger as load_advances_ledger,
    load_partner_ids,
    normalize_period,
    round_money,
    sum_open_by_partner,
    totals_by_issue_period,
)
from household_base_share import compute_household_base_share, load_mapping_file
from household_receivables import (
    current_calendar_month_utc,
    list_overdue_entries,
    load_ledger as load_receivables_ledger,
    sum_outstanding_by_lender,
    sum_outstanding_shared,
)
from monthly_close_lib import fetch_reconciliation, parse_period
from personal_fund_carryover import (
    _parse_attribution_block,
    compute_personal_fund_carryover,
    compute_personal_spend,
    detect_late_advance_register_conflict,
    load_carryover_log,
    prev_calendar_month,
)

PURCHASES_HINT = (
    "Откройте 00-todo/lists/purchases.md — три ближайшие позиции для money check (вне MCP)."
)
ILLIQUID_NOTE = "Informational; does not mutate plan/fact"


def utc_now_iso() -> str:
    """
    Return current UTC timestamp in ISO8601 with ``Z`` suffix.

    :return: Timestamp string
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def methodology_label(methodology_status: str | None) -> str:
    """
    Map API methodology status to ops display label.

    :param methodology_status: Reconciliation methodology status
    :return: ``open``, ``preliminary``, or ``final``
    """
    if methodology_status == "preliminary_closed":
        return "preliminary"
    if methodology_status == "final_closed":
        return "final"
    return "open"


def build_methodology_block(
    reconciliation: dict[str, Any],
    *,
    period: str,
) -> dict[str, Any]:
    """
    Build methodology subsection for one calendar month.

    :param reconciliation: Payload from :func:`fetch_reconciliation`
    :param period: Normalized ``YYYY-MM``
    :return: Methodology block for tool response
    """
    methodology_status = str(reconciliation.get("methodology_status") or "open")
    return {
        "period": period,
        "reconciliation_status": str(reconciliation.get("status") or ""),
        "methodology_status": methodology_status,
        "close_phase": reconciliation.get("close_phase"),
        "label": methodology_label(methodology_status),
        "is_preliminary": methodology_status == "preliminary_closed",
        "is_final": methodology_status == "final_closed",
    }


def find_carryover_run(
    log: dict[str, Any],
    *,
    closed_period: str,
    target_period: str,
) -> dict[str, Any] | None:
    """
    Find a carryover log run matching both closed and target periods (D-13).

    :param log: Carryover log document
    :param closed_period: Prior closed month ``YYYY-MM``
    :param target_period: Check month ``YYYY-MM``
    :return: Matching run or ``None``
    """
    matches = [
        run
        for run in log.get("runs", [])
        if isinstance(run, dict)
        and run.get("closed_period") == closed_period
        and run.get("target_period") == target_period
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"corrupt carryover log: duplicate run for "
            f"({closed_period!r}, {target_period!r})"
        )
    return matches[0] if matches else None


def _base_share_map(base_payload: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in base_payload.get("partners") or []:
        if isinstance(row, dict) and row.get("id") is not None:
            result[str(row["id"])] = round_money(float(row.get("base_share", 0.0)))
    return result


def _display_names(base_payload: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in base_payload.get("partners") or []:
        if isinstance(row, dict) and row.get("id"):
            pid = str(row["id"])
            names[pid] = str(row.get("display_name", pid))
    return names


def materialize_carryover_from_log(
    run: dict[str, Any],
    *,
    base_share_by_partner: dict[str, float],
) -> dict[str, Any]:
    """
    Build carryover partner fields from a log run without FIN-105 recompute.

    :param run: Log run with ``partners`` compact block
    :param base_share_by_partner: Base share on check period per partner
    :return: Internal carryover block for partner row merge
    """
    block = run.get("partners")
    if not isinstance(block, dict):
        block = {}
    partners: dict[str, dict[str, Any]] = {}
    overrun_flags: dict[str, bool] = {}
    for pid, base_share in base_share_by_partner.items():
        row = block.get(pid)
        if not isinstance(row, dict):
            row = {}
        carryover = round_money(float(row.get("carryover", 0.0)))
        advance_deduction = round_money(float(row.get("advance_deduction", 0.0)))
        overrun_amount = round_money(float(row.get("overrun_amount", 0.0)))
        starting_fund = round_money(base_share + carryover - advance_deduction)
        partners[pid] = {
            "incoming_carryover": carryover,
            "advance_deduction": advance_deduction,
            "starting_fund": starting_fund,
            "available_personal_fund": starting_fund,
        }
        overrun_flags[pid] = overrun_amount > 50.0
    return {
        "partners": partners,
        "log_computed_at": run.get("computed_at"),
        "overrun_flags": overrun_flags,
    }


def materialize_carryover_from_dry_run(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Extract carryover partner fields from ``personal_fund_carryover`` dry_run.

    :param payload: FIN-105 tool response
    :return: Internal carryover block
    """
    partners: dict[str, dict[str, Any]] = {}
    overrun_flags: dict[str, bool] = {}
    for row in payload.get("partners") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        pid = str(row["id"])
        incoming = round_money(float(row.get("incoming_carryover", 0.0)))
        advance_deduction = round_money(float(row.get("advance_deduction", 0.0)))
        available = row.get("available_personal_fund")
        if available is not None:
            starting_fund = round_money(float(available))
        else:
            starting_fund = round_money(
                float(row.get("starting_fund", 0.0)) - advance_deduction
            )
        partners[pid] = {
            "incoming_carryover": incoming,
            "advance_deduction": advance_deduction,
            "starting_fund": starting_fund,
            "available_personal_fund": starting_fund,
        }
        overrun_flags[pid] = bool(row.get("overrun_requires_discussion"))
    return {
        "partners": partners,
        "log_computed_at": None,
        "overrun_flags": overrun_flags,
    }


def sum_open_by_deduct_period(
    ledger: dict[str, Any],
    deduct_period: str,
) -> dict[str, float]:
    """
    Sum open advances due for deduction in ``deduct_period`` per partner.

    :param ledger: Advances ledger
    :param deduct_period: Target month ``YYYY-MM``
    :return: ``partner_id → EUR``
    """
    totals: dict[str, float] = {}
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        if entry.get("deduct_in_period") != deduct_period:
            continue
        pid = str(entry.get("partner_id", ""))
        totals[pid] = round_money(totals.get(pid, 0.0) + float(entry.get("amount", 0.0)))
    return totals


def filter_stale_open_advances(
    ledger: dict[str, Any],
    as_of_period: str,
) -> list[dict[str, Any]]:
    """
    Return open advances with ``deduct_in_period`` before ``as_of_period``.

    :param ledger: Advances ledger
    :param as_of_period: Reference month ``YYYY-MM``
    :return: Entry copies
    """
    rows: list[dict[str, Any]] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        deduct = str(entry.get("deduct_in_period", ""))
        if deduct and deduct < as_of_period:
            rows.append(dict(entry))
    return rows


def fetch_classification_summary(api: ApiClient, yyyy_mm: str) -> dict[str, Any]:
    """
    Load classification summary for one accounting month.

    :param api: API client
    :param yyyy_mm: Calendar month ``YYYY-MM``
    :return: API response body
    """
    period = parse_period(yyyy_mm)
    query = urllib.parse.urlencode({"accounting_period": period.ymmm})
    return api.get_json(f"/api/v1/transactions/classification-summary?{query}")


def count_unresolved_expenses(api: ApiClient, yyyy_mm: str) -> int:
    """
    Count expense transactions with empty category in ``yyyy_mm`` (D-03).

    :param api: API client
    :param yyyy_mm: Calendar month ``YYYY-MM``
    :return: Unresolved expense count
    """
    period = parse_period(yyyy_mm)
    query = urllib.parse.urlencode({"accounting_period": period.ymmm})
    body = api.get_json(f"/api/v1/transactions?{query}")
    count = 0
    for row in body.get("rows", []):
        if not isinstance(row, dict):
            continue
        if str(row.get("transaction_type", "")) != "C":
            continue
        category = row.get("transaction_category")
        if category is None or str(category).strip() == "":
            count += 1
    return count


def build_c24_attribution_notes(
    mapping: dict[str, Any],
    spend_warnings: list[str],
) -> dict[str, Any]:
    """
    Build C24 attribution notes and unmapped spend refs (D-05).

    :param mapping: Contour mapping document
    :param spend_warnings: Warnings from ``compute_personal_spend``
    :return: ``notes`` and ``unattributed_spend_refs``
    """
    notes: list[str] = []
    defaults, _overrides = _parse_attribution_block(mapping)
    for provider_key, partner_id in defaults.items():
        if provider_key.lower() in {"c24", "c24-de", "comdirect"} and partner_id == "nikolay":
            notes.append("C24 spends attributed to Nikolay personal fund")
            break
    if not defaults and not mapping.get("account_attribution"):
        pass
    unattributed = [
        warning.split(":", 1)[1]
        for warning in spend_warnings
        if warning.startswith("unattributed_spend:")
    ]
    return {"notes": notes, "unattributed_spend_refs": unattributed}


def _compare_periods(left: str, right: str) -> int:
    ly, lm = (int(part) for part in left.split("-", 1))
    ry, rm = (int(part) for part in right.split("-", 1))
    if (ly, lm) < (ry, rm):
        return -1
    if (ly, lm) > (ry, rm):
        return 1
    return 0


def compute_money_check_report(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    budget_version_id: str,
    check_period: str | None = None,
    prior_period: str | None = None,
    as_of_period: str | None = None,
    mapping_path: str | None = None,
    include_advance_breakdown: bool = True,
    carryover_log_path: Any = None,
    advances_ledger_path: Any = None,
    receivables_ledger_path: Any = None,
) -> dict[str, Any]:
    """
    MCP entry point: assemble weekly money check payload (FIN-104).

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param budget_version_id: Active budget version UUID
    :param check_period: Limit/spend month ``YYYY-MM``
    :param prior_period: Prior month for methodology/carryover
    :param as_of_period: Reference month for stale/overdue
    :param mapping_path: Optional contour mapping override
    :param include_advance_breakdown: Include ``totals_by_issue_period``
    :param carryover_log_path: Optional carryover log override (tests)
    :param advances_ledger_path: Optional advances ledger override (tests)
    :param receivables_ledger_path: Optional receivables ledger override (tests)
    :return: Tool response payload
    :raises ValueError: On invalid arguments
    :raises RuntimeError: On API or ledger failures
    """
    check_yyyy_mm = normalize_period(check_period or current_calendar_month_utc())
    prior_yyyy_mm = normalize_period(prior_period or prev_calendar_month(check_yyyy_mm))
    as_of_yyyy_mm = normalize_period(as_of_period or check_yyyy_mm)

    if _compare_periods(prior_yyyy_mm, check_yyyy_mm) >= 0:
        raise ValueError("prior_period must be before check_period")

    path = Path(mapping_path) if mapping_path else default_mapping_path(profile)
    mapping = load_mapping_file(path)
    partner_ids = load_partner_ids(profile, mapping_path=path)
    if not partner_ids:
        raise RuntimeError("invalid mapping: empty partners")

    partners_meta = mapping.get("partners")
    if not isinstance(partners_meta, list):
        partners_meta = []

    reconciliation_prior = fetch_reconciliation(
        api, budget_version_id, parse_period(prior_yyyy_mm)
    )
    reconciliation_check = fetch_reconciliation(
        api, budget_version_id, parse_period(check_yyyy_mm)
    )
    methodology = build_methodology_block(reconciliation_prior, period=prior_yyyy_mm)
    check_period_methodology = build_methodology_block(
        reconciliation_check, period=check_yyyy_mm
    )

    base_payload = compute_household_base_share(
        api,
        profile=profile,
        base=base,
        period=check_yyyy_mm,
        budget_version_id=budget_version_id,
        mapping_path=str(path),
    )
    base_share_map = _base_share_map(base_payload)
    display_names = _display_names(base_payload)

    class_summary = fetch_classification_summary(api, check_yyyy_mm)
    unresolved_count = count_unresolved_expenses(api, check_yyyy_mm)

    log_kwargs: dict[str, Any] = {}
    if carryover_log_path is not None:
        log_kwargs["log_path"] = carryover_log_path
    carryover_log = load_carryover_log(profile, **log_kwargs)
    matched_run = find_carryover_run(
        carryover_log,
        closed_period=prior_yyyy_mm,
        target_period=check_yyyy_mm,
    )

    carryover_source = "none"
    log_computed_at: str | None = None
    carryover_partners: dict[str, dict[str, Any]] = {}
    overrun_flags: dict[str, bool] = {}

    if matched_run is not None:
        block = materialize_carryover_from_log(
            matched_run, base_share_by_partner=base_share_map
        )
        carryover_source = "log"
        log_computed_at = block.get("log_computed_at")
        carryover_partners = block["partners"]
        overrun_flags = block["overrun_flags"]
    elif methodology["is_final"]:
        dry_payload = compute_personal_fund_carryover(
            api,
            profile=profile,
            base=base,
            closed_period=prior_yyyy_mm,
            budget_version_id=budget_version_id,
            target_period=check_yyyy_mm,
            mapping_path=str(path),
            dry_run=True,
            mark_advances_deducted=False,
            carryover_log_path=carryover_log_path,
            ledger_path=advances_ledger_path,
        )
        block = materialize_carryover_from_dry_run(dry_payload)
        carryover_source = "dry_run"
        carryover_partners = block["partners"]
        overrun_flags = block["overrun_flags"]
    else:
        adv_ledger_preview = load_advances_ledger(
            profile, ledger_path=advances_ledger_path
        )
        deduct_map = sum_open_by_deduct_period(adv_ledger_preview, check_yyyy_mm)
        for pid, base_share in base_share_map.items():
            advance_deduction = round_money(deduct_map.get(pid, 0.0))
            starting_fund = round_money(base_share - advance_deduction)
            carryover_partners[pid] = {
                "incoming_carryover": 0.0,
                "advance_deduction": advance_deduction,
                "starting_fund": starting_fund,
                "available_personal_fund": starting_fund,
            }

    actual_spend, _spend_lines, spend_warnings = compute_personal_spend(
        api,
        budget_version_id=budget_version_id,
        closed_period=check_yyyy_mm,
        mapping=mapping,
        partner_ids=partner_ids,
        partners_meta=partners_meta,
    )

    figures_preliminary = methodology["is_preliminary"]
    figures_incomplete = (
        carryover_source == "none" and methodology["methodology_status"] == "open"
    )

    partners_out: list[dict[str, Any]] = []
    for pid in sorted(partner_ids):
        carry = carryover_partners.get(pid, {})
        starting_fund = round_money(float(carry.get("starting_fund", base_share_map.get(pid, 0.0))))
        spend_mtd = round_money(actual_spend.get(pid, 0.0))
        partners_out.append(
            {
                "id": pid,
                "display_name": display_names.get(pid, pid),
                "base_share": round_money(base_share_map.get(pid, 0.0)),
                "incoming_carryover": round_money(float(carry.get("incoming_carryover", 0.0))),
                "advance_deduction": round_money(float(carry.get("advance_deduction", 0.0))),
                "starting_fund": starting_fund,
                "actual_spend_mtd": spend_mtd,
                "remaining_balance": round_money(starting_fund - spend_mtd),
                "figures_preliminary": figures_preliminary,
                "figures_incomplete": figures_incomplete,
            }
        )

    advances_ledger = load_advances_ledger(profile, ledger_path=advances_ledger_path)
    open_by_partner = sum_open_by_partner(advances_ledger)
    stale_entries = filter_stale_open_advances(advances_ledger, as_of_yyyy_mm)

    advances_block: dict[str, Any] = {
        "open_advances_by_partner": open_by_partner,
        "stale_open_advances": stale_entries,
    }
    if include_advance_breakdown:
        advances_block["totals_by_issue_period"] = totals_by_issue_period(advances_ledger)
    else:
        advances_block["totals_by_issue_period"] = {}

    receivables_ledger = load_receivables_ledger(
        profile, ledger_path=receivables_ledger_path
    )
    outstanding_by_lender = sum_outstanding_by_lender(receivables_ledger)
    outstanding_shared = sum_outstanding_shared(receivables_ledger)
    overdue_entries = list_overdue_entries(
        receivables_ledger, as_of_period=as_of_yyyy_mm
    )
    total_outstanding = round_money(
        sum(outstanding_by_lender.values()) + outstanding_shared
    )

    warnings: list[str] = []
    if figures_incomplete:
        warnings.append(f"prior_period_not_closed:{prior_yyyy_mm}")
    if figures_preliminary:
        warnings.append("figures_preliminary")
    if stale_entries:
        warnings.append("stale_open_advances")
    if overdue_entries:
        warnings.append("overdue_receivables")

    late_conflict = detect_late_advance_register_conflict(
        carryover_log, advances_ledger, prior_yyyy_mm
    )
    if late_conflict:
        warnings.append(late_conflict)

    if "missing_account_attribution" in spend_warnings:
        warnings.append("missing_account_attribution")
    for warning in spend_warnings:
        if warning.startswith("unattributed_spend:"):
            warnings.append(warning)

    c9999_count = int(class_summary.get("expense_c9999_count") or 0)
    if c9999_count > 0:
        warnings.append(f"expense_c9999_open:{c9999_count}")
    if unresolved_count > 0:
        warnings.append(f"unresolved_expenses:{unresolved_count}")

    for pid, flagged in overrun_flags.items():
        if flagged:
            warnings.append(f"overrun_discussion_required:{pid}")

    c24_block = build_c24_attribution_notes(mapping, spend_warnings)

    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "check_period": check_yyyy_mm,
        "prior_period": prior_yyyy_mm,
        "as_of_period": as_of_yyyy_mm,
        "computed_at": utc_now_iso(),
        "methodology": methodology,
        "check_period_methodology": check_period_methodology,
        "partners": partners_out,
        "classification": {
            "period": check_yyyy_mm,
            "expense_c9999_count": c9999_count,
            "expense_c9999_amount_eur": str(
                class_summary.get("expense_c9999_amount_eur", "0.00")
            ),
            "unresolved_expense_count": unresolved_count,
        },
        "advances": advances_block,
        "receivables": {
            "outstanding_by_lender": outstanding_by_lender,
            "outstanding_shared_total": outstanding_shared,
            "overdue_entries": overdue_entries,
            "overdue_count": len(overdue_entries),
        },
        "illiquid_hint": {
            "total_outstanding_eur": total_outstanding,
            "note": ILLIQUID_NOTE,
        },
        "carryover": {
            "source": carryover_source,
            "closed_period": prior_yyyy_mm,
            "target_period": check_yyyy_mm,
            "log_computed_at": log_computed_at,
        },
        "c24_attribution": c24_block,
        "purchases_hint": PURCHASES_HINT,
        "warnings": warnings,
    }
