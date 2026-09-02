"""Personal fund carryover after FINAL close (FIN-105)."""

from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.parse
from decimal import Decimal
from pathlib import Path
from typing import Any

from finance_api_client import ApiClient
from household_advances import (
    _action_mark_deducted,
    default_ledger_path,
    default_mapping_path,
    load_ledger,
    load_partner_ids,
    normalize_period,
    round_money,
    save_ledger,
    sum_open_for_issue_period,
)
from household_base_share import (
    compute_household_base_share,
    load_mapping_file,
    period_start,
    round_money as base_round_money,
)
from monthly_close_lib import fetch_reconciliation, parse_period

SUPPORTED_LOG_SCHEMA_VERSION = 1
HOUSEHOLD_CARRYOVER_API_PATH = "/api/v1/household/personal-fund-carryover"
HOUSEHOLD_CARRYOVER_RUNS_PATH = "/api/v1/household/personal-fund-carryover/runs"
LEGACY_JUNE_PERIOD = "2026-06"
OVERRUN_DISCUSSION_THRESHOLD = 50.0
CANONICAL_FORMULA = (
    "carryover = starting_fund - actual_spend; "
    "available_personal_fund = base_share(target) + carryover - advance_deduction"
)


def period_to_yyyymm(yyyy_mm: str) -> str:
    """
    Convert ``YYYY-MM`` to compact ``YYYYMM`` for fund financing blocks.

    :param yyyy_mm: Normalized calendar month
    :return: ``YYYYMM`` string
    """
    return yyyy_mm.replace("-", "")


def empty_fund_financing_block(accounting_period: str = "") -> dict[str, Any]:
    """
    Build the empty fund financing block (FIN-280 D-05).

    :param accounting_period: Compact ``YYYYMM`` or empty string for money-check fallback
    :return: Empty financing block dict
    """
    return {
        "accounting_period": accounting_period,
        "projections": [],
        "outgoing_by_fund": {},
        "incoming_by_fund": {},
        "outgoing_by_member": {},
        "outgoing_by_analytics": [],
        "warnings": [],
    }


def resolve_fund_financing_from_api(
    api_body: dict[str, Any] | None,
    *,
    closed_period: str,
) -> dict[str, Any]:
    """
    Take ``fund_financing`` from HTTP body or return the empty block.

    :param api_body: Carryover API response or ``None``
    :param closed_period: Closed month ``YYYY-MM``
    :return: Financing block for MCP response
    """
    if isinstance(api_body, dict):
        block = api_body.get("fund_financing")
        if isinstance(block, dict):
            return dict(block)
    return empty_fund_financing_block(period_to_yyyymm(closed_period))


def default_carryover_log_path(profile: str) -> Path:
    """
    Default carryover log path for a data profile.

    :param profile: Data profile name
    :return: Path under ``working/household/``
    """
    from monthly_close_lib import ASSISTANT_ROOT

    return ASSISTANT_ROOT / "working" / "household" / f"personal-fund-carryover.{profile}.json"


def prev_calendar_month(yyyy_mm: str) -> str:
    """
    Return the calendar month before ``yyyy_mm``.

    :param yyyy_mm: Normalized ``YYYY-MM``
    :return: Previous month ``YYYY-MM``
    """
    year, month = (int(part) for part in yyyy_mm.split("-", 1))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year:04d}-{month - 1:02d}"


def empty_carryover_log(profile: str) -> dict[str, Any]:
    """
    Build an empty carryover log document.

    :param profile: Data profile name
    :return: Log dict with empty ``runs``
    """
    return {
        "schema_version": SUPPORTED_LOG_SCHEMA_VERSION,
        "profile": profile,
        "runs": [],
    }


def validate_carryover_log_runs(runs: list[Any]) -> None:
    """
    Reject duplicate ``closed_period`` entries on load.

    :param runs: ``runs`` array from log
    :raises RuntimeError: When duplicates exist
    """
    seen: set[str] = set()
    for run in runs:
        if not isinstance(run, dict):
            continue
        period = run.get("closed_period")
        if not period:
            continue
        if period in seen:
            raise RuntimeError(
                f"corrupt carryover log: duplicate closed_period {period!r}"
            )
        seen.add(str(period))


def load_carryover_log(profile: str, *, log_path: Path | None = None) -> dict[str, Any]:
    """
    Load carryover log JSON; missing file → empty ``runs``.

    :param profile: Data profile
    :param log_path: Optional override for tests
    :return: Parsed log dict
    :raises RuntimeError: When JSON is corrupt or duplicate periods exist
    """
    path = log_path or default_carryover_log_path(profile)
    if not path.is_file():
        return empty_carryover_log(profile)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid carryover log JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid carryover log JSON: {path}: expected object")
    runs = data.get("runs")
    if runs is None:
        data["runs"] = []
    elif not isinstance(runs, list):
        raise RuntimeError(f"Invalid carryover log JSON: {path}: runs must be array")
    else:
        validate_carryover_log_runs(runs)
    data.setdefault("schema_version", SUPPORTED_LOG_SCHEMA_VERSION)
    data.setdefault("profile", profile)
    return data


def save_carryover_log(path: Path, log: dict[str, Any]) -> None:
    """
    Atomically persist carryover log JSON.

    :param path: Target file path
    :param log: Log document
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(log, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _find_run(log: dict[str, Any], closed_period: str) -> dict[str, Any] | None:
    matches = [
        run
        for run in log.get("runs", [])
        if isinstance(run, dict) and run.get("closed_period") == closed_period
    ]
    return matches[0] if matches else None


def _incoming_from_run(
    run: dict[str, Any] | None,
    partner_ids: frozenset[str],
) -> dict[str, float]:
    """
    Build incoming map from one history run.

    :param run: History run or None
    :param partner_ids: Known partners
    :return: ``partner_id → EUR``
    """
    incoming = {pid: 0.0 for pid in partner_ids}
    if run is None:
        return incoming
    partners_block = run.get("partners")
    if not isinstance(partners_block, dict):
        return incoming
    for pid in partner_ids:
        row = partners_block.get(pid)
        if isinstance(row, dict):
            incoming[pid] = round_money(float(row.get("carryover", 0.0)))
    return incoming


def resolve_incoming_carryover(
    log: dict[str, Any],
    closed_period: str,
    partner_ids: frozenset[str],
    *,
    override: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Resolve per-partner incoming carryover for month M from JSON log.

    :param log: Carryover log
    :param closed_period: Closed month ``YYYY-MM``
    :param partner_ids: Known partners
    :param override: Optional map replacing log lookup
    :return: ``partner_id → EUR``
    """
    if override is not None:
        return {pid: round_money(float(override.get(pid, 0.0))) for pid in partner_ids}
    prior = prev_calendar_month(closed_period)
    return _incoming_from_run(_find_run(log, prior), partner_ids)


def fetch_carryover_run_api(
    api: ApiClient,
    closed_period: str,
) -> tuple[str, dict[str, Any] | None]:
    """
    Fetch one history run from backend (FIN-163).

    :param api: API client
    :param closed_period: Canonical ``YYYY-MM``
    :return: ``("ok", run)`` | ``("not_found", None)`` | ``("unavailable", None)``
    """
    path = f"{HOUSEHOLD_CARRYOVER_RUNS_PATH}/{closed_period}"
    try:
        status, body = api.request("GET", path)
    except (OSError, TimeoutError, urllib.error.URLError):
        return "unavailable", None
    if status == 200 and isinstance(body, dict):
        return "ok", body
    if status == 404:
        err = body.get("error") if isinstance(body, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        if code == "not_found" or code is None:
            return "not_found", None
        return "unavailable", None
    if status >= 500:
        return "unavailable", None
    return "unavailable", None


def put_carryover_run_api(
    api: ApiClient,
    *,
    closed_period: str,
    target_period: str | None,
    source: str,
    partners: list[dict[str, Any]],
    advances_marked: bool,
    computed_at: str,
) -> tuple[str, dict[str, Any] | None]:
    """
    Upsert history run via backend API.

    :param api: API client
    :param closed_period: Closed month
    :param target_period: Optional target month
    :param source: Run source enum
    :param partners: Partner result rows
    :param advances_marked: Advances marked flag
    :param computed_at: UTC ISO timestamp
    :return: ``("ok", body)`` | ``("unavailable", None)`` | raises on 4xx validation
    """
    compact_partners: dict[str, dict[str, float]] = {}
    for row in partners:
        pid = str(row["id"])
        compact_partners[pid] = {
            "carryover": round_money(float(row["carryover"])),
            "advance_deduction": round_money(float(row.get("advance_deduction", 0.0))),
            "overrun_amount": round_money(float(row.get("overrun_amount", 0.0))),
        }
    computed = str(computed_at).strip()
    if computed and "T" in computed and not (
        computed.endswith("Z") or "+" in computed[10:] or computed.count("-") > 2
    ):
        computed = f"{computed}Z"
    payload = {
        "closed_period": closed_period,
        "target_period": target_period,
        "source": source if source in {"api", "mapping", "manual_runbook", "migrated"} else "api",
        "partners": compact_partners,
        "advances_marked": advances_marked,
        "computed_at": computed,
    }
    try:
        status, body = api.request("PUT", HOUSEHOLD_CARRYOVER_RUNS_PATH, data=payload)
    except (OSError, TimeoutError, urllib.error.URLError):
        return "unavailable", None
    if status == 200 and isinstance(body, dict):
        return "ok", body
    if status == 404:
        return "unavailable", None
    if status >= 500:
        return "unavailable", None
    raise RuntimeError(
        f"PUT {HOUSEHOLD_CARRYOVER_RUNS_PATH} -> HTTP {status}: {body}"
    )


def resolve_incoming_carryover_cutover(
    api: ApiClient,
    log: dict[str, Any],
    closed_period: str,
    partner_ids: frozenset[str],
    *,
    override: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Resolve incoming with API-first history + JSON fallback (FIN-163 D-10/D-20).

    :param api: API client
    :param log: Local JSON carryover log
    :param closed_period: Closed month M
    :param partner_ids: Known partners
    :param override: Optional explicit override
    :return: ``partner_id → EUR``
    """
    if override is not None:
        return {pid: round_money(float(override.get(pid, 0.0))) for pid in partner_ids}
    prior = prev_calendar_month(closed_period)
    status, run = fetch_carryover_run_api(api, prior)
    if status == "ok":
        return _incoming_from_run(run, partner_ids)
    # not_found or unavailable → JSON fallback; 0 only if absent in both
    return resolve_incoming_carryover(log, closed_period, partner_ids, override=None)


def _parse_attribution_block(mapping: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    block = mapping.get("account_attribution")
    if not isinstance(block, dict):
        return {}, []
    defaults_raw = block.get("default_partner_by_provider")
    defaults: dict[str, str] = {}
    if isinstance(defaults_raw, dict):
        defaults = {str(k): str(v) for k, v in defaults_raw.items()}
    overrides_raw = block.get("description_overrides")
    overrides: list[dict[str, Any]] = [
        row for row in overrides_raw if isinstance(row, dict)
    ] if isinstance(overrides_raw, list) else []
    return defaults, overrides


def _normalize_spend_category(raw: Any) -> str | None:
    """
    Normalize a line category for spend-line JSON.

    :param raw: Stored category value
    :return: Stripped category or ``None`` when empty
    """
    if raw is None:
        return None
    text = str(raw).strip()
    return text if text else None


def _line_amount(raw: Any) -> Decimal:
    """
    Parse a line amount as a non-negative Decimal.

    :param raw: Stored amount
    :return: Absolute Decimal amount
    """
    return abs(Decimal(str(raw if raw is not None else "0").replace(",", ".")))


def _load_funds_by_id(api: ApiClient) -> dict[str, dict[str, Any]]:
    """
    Load the household funds catalogue keyed by fund id.

    :param api: API client
    :return: ``fund_id → fund row``
    """
    status, body = api.request("GET", "/api/v1/households")
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"GET /api/v1/households -> HTTP {status}: {body}")
    households = body.get("households")
    if not isinstance(households, list):
        raise RuntimeError("GET /api/v1/households: households is not a list")
    catalog: dict[str, dict[str, Any]] = {}
    for row in households:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        household_id = str(row["id"])
        path = f"/api/v1/households/{urllib.parse.quote(household_id, safe='')}/funds"
        fund_status, fund_body = api.request("GET", path)
        if fund_status != 200 or not isinstance(fund_body, dict):
            raise RuntimeError(f"GET {path} -> HTTP {fund_status}: {fund_body}")
        funds = fund_body.get("funds")
        if not isinstance(funds, list):
            raise RuntimeError(f"GET {path}: funds is not a list")
        for fund in funds:
            if not isinstance(fund, dict):
                continue
            fund_id = str(fund.get("id") or "").strip()
            if fund_id:
                catalog[fund_id] = fund
    return catalog


def _fetch_operation_lines(api: ApiClient, transaction_id: str) -> list[dict[str, Any]]:
    """
    Load expense-capable lines for one operation.

    :param api: API client
    :param transaction_id: Operation id
    :return: Line dicts
    """
    path = (
        f"/api/v1/transactions/{urllib.parse.quote(transaction_id, safe='')}/lines"
    )
    status, body = api.request("GET", path)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"GET {path} -> HTTP {status}: {body}")
    lines = body.get("lines")
    if not isinstance(lines, list):
        return []
    return [row for row in lines if isinstance(row, dict)]


def compute_personal_spend(
    api: ApiClient,
    *,
    budget_version_id: str,
    closed_period: str,
    mapping: dict[str, Any],
    partner_ids: frozenset[str],
    partners_meta: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]], list[str]]:
    """
    Sum personal-fund expense lines per household member (FIN-324).

    :param api: API client
    :param budget_version_id: Unused; kept for caller compatibility
    :param closed_period: Month ``YYYY-MM``
    :param mapping: Unused for spend; members come from ``partner_ids``
    :param partner_ids: Member id set
    :param partners_meta: Unused; display names stay in the mapping path
    :return: Spend totals, spend line breakdown, warnings
    """
    del budget_version_id, mapping, partners_meta
    funds = _load_funds_by_id(api)
    ymmm = parse_period(closed_period).ymmm
    query = urllib.parse.urlencode({"accounting_period": ymmm})
    listing = api.get_json(f"/api/v1/transactions?{query}")
    rows = listing.get("rows", []) if isinstance(listing, dict) else []
    spend_acc: dict[str, Decimal] = {pid: Decimal("0.00") for pid in partner_ids}
    lines_out: dict[str, list[dict[str, Any]]] = {pid: [] for pid in partner_ids}
    warnings: list[str] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        transaction_id = str(raw.get("id") or "").strip()
        if not transaction_id:
            continue
        for line in _fetch_operation_lines(api, transaction_id):
            assignment = line.get("assignment")
            if not isinstance(assignment, dict):
                assignment = {}
            line_type = str(assignment.get("type") or "").strip().upper()
            if line_type != "C":
                continue
            line_id = str(line.get("id") or "").strip()
            if not line_id:
                continue
            amount = _line_amount(line.get("amount")).quantize(Decimal("0.01"))
            fund_raw = assignment.get("fund_id")
            fund_id = str(fund_raw).strip() if fund_raw is not None else ""
            if not fund_id:
                warnings.append(f"unattributed_spend:{line_id}")
                continue
            fund = funds.get(fund_id)
            if fund is None:
                warnings.append(f"unattributed_spend:{line_id}")
                continue
            rule = str(fund.get("allocation_rule") or "")
            member_raw = fund.get("member_id")
            member_id = str(member_raw).strip() if member_raw is not None else ""
            if rule != "equal_share" or not member_id:
                continue
            if member_id not in partner_ids:
                warnings.append(f"unattributed_spend:{line_id}")
                continue
            spend_acc[member_id] += amount
            lines_out[member_id].append(
                {
                    "line_id": line_id,
                    "amount": round_money(float(amount)),
                    "fund_id": fund_id,
                    "category": _normalize_spend_category(assignment.get("category")),
                }
            )
    for pid in partner_ids:
        lines_out[pid].sort(key=lambda row: str(row["line_id"]))
    spend = {
        pid: round_money(float(value.quantize(Decimal("0.01"))))
        for pid, value in spend_acc.items()
    }
    return spend, lines_out, warnings


def resolve_period_personal_spend(
    api: ApiClient,
    *,
    period: str,
    partner_ids: frozenset[str],
    partners_meta: list[dict[str, Any]],
    mapping: dict[str, Any],
    budget_version_id: str,
    allow_non_final: bool,
) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]], list[str]]:
    """
    Resolve personal spend via HTTP 200 body or local fund fallback (FIN-324).

    :param api: API client
    :param period: Spend month ``YYYY-MM``
    :param partner_ids: Member id set
    :param partners_meta: Mapping partner rows
    :param mapping: Contour mapping
    :param budget_version_id: Budget version UUID
    :param allow_non_final: Forward non-final period bypass to HTTP
    :return: Spend totals, spend lines, warnings
    """
    source, api_body = probe_household_carryover_api(
        api,
        period,
        None,
        allow_non_final=allow_non_final,
    )
    if source == "api" and api_body is not None:
        spend = {pid: 0.0 for pid in partner_ids}
        lines: dict[str, list[dict[str, Any]]] = {pid: [] for pid in partner_ids}
        raw_partners = api_body.get("partners")
        if isinstance(raw_partners, list):
            for row in raw_partners:
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                pid = str(row["id"])
                if pid not in partner_ids:
                    continue
                spend[pid] = round_money(float(row.get("actual_spend", 0.0)))
        warnings: list[str] = []
        raw_warnings = api_body.get("warnings")
        if isinstance(raw_warnings, list):
            for item in raw_warnings:
                text = str(item)
                if text.startswith("unattributed_spend:"):
                    warnings.append(text)
        return spend, lines, warnings
    return compute_personal_spend(
        api,
        budget_version_id=budget_version_id,
        closed_period=period,
        mapping=mapping,
        partner_ids=partner_ids,
        partners_meta=partners_meta,
    )


def probe_household_carryover_api(
    api: ApiClient,
    closed_period: str,
    target_period: str | None,
    *,
    incoming_carryover: dict[str, float] | None = None,
    allow_non_final: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """
    Probe FIN-102 household carryover endpoint.

    :param api: API client
    :param closed_period: Closed month
    :param target_period: Optional target month
    :param incoming_carryover: Optional map passed as query (FIN-105 semantics)
    :param allow_non_final: Forward non-final period bypass to HTTP
    :return: ``("api", body)`` or ``("mapping", None)``
    :raises RuntimeError: On 5xx or unexpected errors
    """
    params: dict[str, str] = {"closed_period": period_start(closed_period)}
    if target_period:
        params["target_period"] = period_start(target_period)
    if allow_non_final:
        params["allow_non_final"] = "true"
    if incoming_carryover is not None:
        params["incoming_carryover"] = json.dumps(
            {pid: float(amount) for pid, amount in incoming_carryover.items()},
            separators=(",", ":"),
        )
    query = urllib.parse.urlencode(params)
    status, body = api.request("GET", f"{HOUSEHOLD_CARRYOVER_API_PATH}?{query}")
    if status == 200 and isinstance(body, dict):
        return "api", body
    if status == 404:
        return "mapping", None
    if status >= 500:
        raise RuntimeError(f"GET {HOUSEHOLD_CARRYOVER_API_PATH} -> HTTP {status}: {body}")
    raise RuntimeError(f"GET {HOUSEHOLD_CARRYOVER_API_PATH} -> HTTP {status}: {body}")


def normalize_api_partners(
    api_body: dict[str, Any],
    *,
    partner_ids: frozenset[str],
    partners_meta: list[dict[str, Any]],
    include_target: bool,
) -> list[dict[str, Any]]:
    """
    Normalize FIN-102 carryover API payload to MCP partner rows.

    :param api_body: API response body
    :param partner_ids: Expected partner ids
    :param partners_meta: Mapping partner metadata
    :param include_target: Whether target-period fields are included
    :return: Partner row dicts
    """
    display_names = {
        str(row.get("id")): str(row.get("display_name", row.get("id")))
        for row in partners_meta
        if isinstance(row, dict) and row.get("id")
    }
    raw_partners = api_body.get("partners")
    if not isinstance(raw_partners, list):
        raise RuntimeError("Invalid household carryover API: partners must be array")
    by_id: dict[str, dict[str, Any]] = {}
    for row in raw_partners:
        if isinstance(row, dict) and row.get("id"):
            by_id[str(row["id"])] = row
    normalized: list[dict[str, Any]] = []
    for pid in sorted(partner_ids):
        src = by_id.get(pid, {})
        carryover = round_money(float(src.get("carryover", src.get("balance", 0.0))))
        balance = round_money(float(src.get("balance", carryover)))
        overrun_amount = round_money(
            float(src.get("overrun_amount", max(0.0, -balance)))
        )
        out: dict[str, Any] = {
            "id": pid,
            "display_name": display_names.get(pid, pid),
            "base_share_closed": round_money(float(src.get("base_share_closed", 0.0))),
            "incoming_carryover": round_money(float(src.get("incoming_carryover", 0.0))),
            "starting_fund": round_money(float(src.get("starting_fund", 0.0))),
            "actual_spend": round_money(float(src.get("actual_spend", 0.0))),
            "outgoing_financing": round_money(float(src.get("outgoing_financing", 0.0))),
            "balance": balance,
            "carryover": carryover,
            "overrun_amount": overrun_amount,
            "overrun_requires_discussion": bool(
                src.get("overrun_requires_discussion", overrun_amount > OVERRUN_DISCUSSION_THRESHOLD)
            ),
        }
        if include_target:
            out["base_share_target"] = round_money(float(src.get("base_share_target", 0.0)))
            out["available_personal_fund"] = round_money(
                float(src.get("available_personal_fund", 0.0))
            )
        if isinstance(src.get("spend_lines"), list):
            out["spend_lines"] = src["spend_lines"]
        normalized.append(out)
    return normalized


def _partner_display_names(mapping: dict[str, Any]) -> dict[str, str]:
    partners = mapping.get("partners")
    if not isinstance(partners, list):
        return {}
    return {
        str(row["id"]): str(row.get("display_name", row["id"]))
        for row in partners
        if isinstance(row, dict) and row.get("id")
    }


def build_partner_rows_mapping(
    *,
    mapping: dict[str, Any],
    partner_ids: frozenset[str],
    base_share_closed: dict[str, float],
    base_share_target: dict[str, float] | None,
    incoming: dict[str, float],
    prior_advance: dict[str, float],
    actual_spend: dict[str, float],
    spend_lines: dict[str, list[dict[str, Any]]],
    include_target: bool,
) -> list[dict[str, Any]]:
    """
    Build partner result rows from mapping-path computation.

    :return: Partner dicts for tool response
    """
    names = _partner_display_names(mapping)
    rows: list[dict[str, Any]] = []
    for pid in sorted(partner_ids):
        starting = round_money(
            base_share_closed.get(pid, 0.0)
            + incoming.get(pid, 0.0)
            - prior_advance.get(pid, 0.0)
        )
        spend = round_money(actual_spend.get(pid, 0.0))
        balance = round_money(starting - spend)
        overrun_amount = round_money(max(0.0, -balance))
        row: dict[str, Any] = {
            "id": pid,
            "display_name": names.get(pid, pid),
            "base_share_closed": round_money(base_share_closed.get(pid, 0.0)),
            "incoming_carryover": round_money(incoming.get(pid, 0.0)),
            "starting_fund": starting,
            "actual_spend": spend,
            "outgoing_financing": 0.0,
            "balance": balance,
            "carryover": balance,
            "overrun_amount": overrun_amount,
            "overrun_requires_discussion": overrun_amount > OVERRUN_DISCUSSION_THRESHOLD,
        }
        if spend_lines.get(pid):
            row["spend_lines"] = spend_lines[pid]
        if include_target and base_share_target is not None:
            row["base_share_target"] = round_money(base_share_target.get(pid, 0.0))
        rows.append(row)
    return rows


def merge_advance_and_warnings(
    partners: list[dict[str, Any]],
    advance_deduction: dict[str, float],
    *,
    include_target: bool,
) -> list[str]:
    """
    Attach advance deduction and overrun warnings to partner rows.

    :param partners: Mutable partner rows
    :param advance_deduction: Open advances for closed period
    :param include_target: Whether to set ``available_personal_fund``
    :return: Warning codes
    """
    warnings: list[str] = []
    for row in partners:
        pid = str(row["id"])
        deduction = round_money(advance_deduction.get(pid, 0.0))
        row["advance_deduction"] = deduction
        if row.get("overrun_requires_discussion"):
            warnings.append(f"overrun_discussion_required:{pid}")
        if include_target and "base_share_target" in row:
            row["available_personal_fund"] = round_money(
                float(row["base_share_target"])
                + float(row["carryover"])
                - deduction
            )
    return warnings


def detect_late_advance_register_conflict(
    log: dict[str, Any],
    ledger: dict[str, Any],
    closed_period: str,
) -> str | None:
    """
    Detect FIN-132 late-register conflict (warning only).

    :param log: Carryover log
    :param ledger: Advances ledger
    :param closed_period: Closed month
    :return: Warning code or ``None``
    """
    prior_run = _find_run(log, closed_period)
    if prior_run is None or not prior_run.get("advances_marked"):
        return None
    open_totals = sum_open_for_issue_period(ledger, closed_period)
    if any(amount > 0 for amount in open_totals.values()):
        return f"late_advance_register_conflict:{closed_period}"
    return None


def upsert_carryover_run(
    log: dict[str, Any],
    *,
    closed_period: str,
    target_period: str | None,
    source: str,
    partners: list[dict[str, Any]],
    advances_marked: bool,
    computed_at: str,
) -> None:
    """
    Replace or append a single run entry for ``closed_period``.

    :param log: Mutable carryover log
    :param closed_period: Closed month key
    :param target_period: Target month or ``None``
    :param source: ``api`` or ``mapping``
    :param partners: Partner result rows
    :param advances_marked: Whether advances were marked in this run
    :param computed_at: UTC ISO timestamp
    """
    compact_partners: dict[str, dict[str, float]] = {}
    for row in partners:
        pid = str(row["id"])
        compact_partners[pid] = {
            "carryover": round_money(float(row["carryover"])),
            "advance_deduction": round_money(float(row.get("advance_deduction", 0.0))),
            "overrun_amount": round_money(float(row.get("overrun_amount", 0.0))),
        }
    entry = {
        "closed_period": closed_period,
        "target_period": target_period,
        "computed_at": computed_at,
        "source": source,
        "partners": compact_partners,
        "advances_marked": advances_marked,
    }
    runs = [
        run
        for run in log.get("runs", [])
        if not (isinstance(run, dict) and run.get("closed_period") == closed_period)
    ]
    runs.append(entry)
    log["runs"] = runs


def _parse_override(raw: Any, partner_ids: frozenset[str]) -> dict[str, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("incoming_carryover_override must be an object")
    result: dict[str, float] = {}
    for key, value in raw.items():
        pid = str(key)
        if pid not in partner_ids:
            raise ValueError(f"unknown partner_id in incoming_carryover_override: {pid}")
        result[pid] = round_money(float(value))
    return result


def compute_personal_fund_carryover(
    api: ApiClient,
    *,
    profile: str,
    base: str,
    closed_period: str,
    budget_version_id: str,
    target_period: str | None = None,
    mapping_path: str | None = None,
    dry_run: bool = False,
    mark_advances_deducted: bool = True,
    allow_non_final: bool = False,
    incoming_carryover_override: dict[str, Any] | None = None,
    ledger_path: Path | None = None,
    carryover_log_path: Path | None = None,
) -> dict[str, Any]:
    """
    MCP entry point: compute and optionally persist personal fund carryover.

    :param api: Authenticated API client
    :param profile: Data profile
    :param base: API base URL
    :param closed_period: Closed accounting month
    :param budget_version_id: Active budget version UUID
    :param target_period: Optional month for ``available_personal_fund``
    :param mapping_path: Optional contour mapping override
    :param dry_run: Skip persist and mark
    :param mark_advances_deducted: Whether to mark advances after log save
    :param allow_non_final: Allow non-final methodology status with warning
    :param incoming_carryover_override: Optional incoming carryover map
    :param ledger_path: Optional advances ledger path (tests)
    :param carryover_log_path: Optional carryover log path (tests)
    :return: Tool response payload
    :raises ValueError: On invalid arguments
    :raises RuntimeError: On API or persist failures
    """
    closed_yyyy_mm = normalize_period(closed_period)
    target_yyyy_mm = normalize_period(target_period) if target_period else None
    include_target = target_yyyy_mm is not None

    if closed_yyyy_mm == LEGACY_JUNE_PERIOD and incoming_carryover_override is None:
        raise RuntimeError(
            f"closed_period {LEGACY_JUNE_PERIOD} requires incoming_carryover_override or manual runbook"
        )

    path = Path(mapping_path) if mapping_path else default_mapping_path(profile)
    mapping = load_mapping_file(path)
    partners_meta = mapping.get("partners")
    if not isinstance(partners_meta, list):
        raise RuntimeError(f"Invalid partners in mapping: {path}")
    partner_ids = load_partner_ids(profile, mapping_path=path)
    override_map = _parse_override(incoming_carryover_override, partner_ids)

    reconciliation = fetch_reconciliation(
        api, budget_version_id, parse_period(closed_yyyy_mm)
    )
    methodology_status = str(reconciliation.get("methodology_status") or "")
    warnings: list[str] = []
    if methodology_status != "final_closed":
        if not allow_non_final:
            raise RuntimeError(
                f"methodology_status must be final_closed, got {methodology_status!r}"
            )
        warnings.append("non_final_period")

    advances_path = ledger_path or default_ledger_path(profile)
    ledger = load_ledger(profile, ledger_path=advances_path)
    log_path = carryover_log_path or default_carryover_log_path(profile)
    carryover_log = load_carryover_log(profile, log_path=log_path)

    late_warning = detect_late_advance_register_conflict(
        carryover_log, ledger, closed_yyyy_mm
    )
    if late_warning:
        warnings.append(late_warning)

    # FIN-230: API compute owns history resolution; pass only explicit override.
    source, api_body = probe_household_carryover_api(
        api,
        closed_yyyy_mm,
        target_yyyy_mm,
        incoming_carryover=override_map,
        allow_non_final=allow_non_final,
    )
    spend_warnings: list[str] = []
    formula = CANONICAL_FORMULA
    fund_financing = empty_fund_financing_block(period_to_yyyymm(closed_yyyy_mm))
    if source == "api" and api_body is not None:
        partners_rows = normalize_api_partners(
            api_body,
            partner_ids=partner_ids,
            partners_meta=partners_meta,
            include_target=include_target,
        )
        api_formula = api_body.get("formula")
        if isinstance(api_formula, str) and api_formula.strip():
            formula = api_formula
        fund_financing = resolve_fund_financing_from_api(
            api_body, closed_period=closed_yyyy_mm
        )
        api_warnings = api_body.get("warnings")
        if isinstance(api_warnings, list):
            for item in api_warnings:
                text = str(item)
                if text and text not in warnings:
                    warnings.append(text)
    else:
        incoming = resolve_incoming_carryover_cutover(
            api,
            carryover_log,
            closed_yyyy_mm,
            partner_ids,
            override=override_map,
        )
        base_closed_payload = compute_household_base_share(
            api,
            profile=profile,
            base=base,
            period=closed_yyyy_mm,
            budget_version_id=budget_version_id,
            mapping_path=mapping_path,
        )
        base_share_closed = {
            str(row["id"]): base_round_money(float(row["base_share"]))
            for row in base_closed_payload.get("partners", [])
            if isinstance(row, dict) and row.get("id")
        }
        base_share_target: dict[str, float] | None = None
        if include_target and target_yyyy_mm:
            base_target_payload = compute_household_base_share(
                api,
                profile=profile,
                base=base,
                period=target_yyyy_mm,
                budget_version_id=budget_version_id,
                mapping_path=mapping_path,
            )
            base_share_target = {
                str(row["id"]): base_round_money(float(row["base_share"]))
                for row in base_target_payload.get("partners", [])
                if isinstance(row, dict) and row.get("id")
            }
        prior_advance = sum_open_for_issue_period(
            ledger, prev_calendar_month(closed_yyyy_mm)
        )
        actual_spend, spend_lines, spend_warnings = compute_personal_spend(
            api,
            budget_version_id=budget_version_id,
            closed_period=closed_yyyy_mm,
            mapping=mapping,
            partner_ids=partner_ids,
            partners_meta=partners_meta,
        )
        partners_rows = build_partner_rows_mapping(
            mapping=mapping,
            partner_ids=partner_ids,
            base_share_closed=base_share_closed,
            base_share_target=base_share_target,
            incoming=incoming,
            prior_advance=prior_advance,
            actual_spend=actual_spend,
            spend_lines=spend_lines,
            include_target=include_target,
        )

    advance_deduction = sum_open_for_issue_period(ledger, closed_yyyy_mm)
    warnings.extend(spend_warnings)
    warnings.extend(
        merge_advance_and_warnings(
            partners_rows,
            advance_deduction,
            include_target=include_target,
        )
    )

    effective_dry_run = bool(dry_run)
    effective_mark = bool(mark_advances_deducted) and not effective_dry_run
    log_persisted = False
    advances_marked = False
    marked_advances: dict[str, Any] | None = None
    persist_target = "json"

    if not effective_dry_run:
        from household_advances import utc_now_iso

        computed_at = utc_now_iso()
        api_status, _ = put_carryover_run_api(
            api,
            closed_period=closed_yyyy_mm,
            target_period=target_yyyy_mm,
            source=source,
            partners=partners_rows,
            advances_marked=False,
            computed_at=computed_at,
        )
        if api_status == "ok":
            persist_target = "api"
            log_persisted = True
        else:
            upsert_carryover_run(
                carryover_log,
                closed_period=closed_yyyy_mm,
                target_period=target_yyyy_mm,
                source=source,
                partners=partners_rows,
                advances_marked=False,
                computed_at=computed_at,
            )
            save_carryover_log(log_path, carryover_log)
            persist_target = "json"
            log_persisted = True

        if effective_mark and any(amount > 0 for amount in advance_deduction.values()):
            try:
                marked_advances = _action_mark_deducted(
                    ledger, {"issue_period": closed_yyyy_mm}
                )
                save_ledger(advances_path, ledger)
                advances_marked = True
                if persist_target == "api":
                    put_status, _ = put_carryover_run_api(
                        api,
                        closed_period=closed_yyyy_mm,
                        target_period=target_yyyy_mm,
                        source=source,
                        partners=partners_rows,
                        advances_marked=True,
                        computed_at=computed_at,
                    )
                    if put_status != "ok":
                        raise RuntimeError(
                            "mark_deducted succeeded but history PUT advances_marked update failed"
                        )
                else:
                    for run in carryover_log.get("runs", []):
                        if isinstance(run, dict) and run.get("closed_period") == closed_yyyy_mm:
                            run["advances_marked"] = True
                    save_carryover_log(log_path, carryover_log)
            except Exception as exc:
                raise RuntimeError(
                    f"mark_deducted failed after carryover log persisted: {exc}"
                ) from exc

    return {
        "ok": True,
        "profile": profile,
        "base": base,
        "closed_period": closed_yyyy_mm,
        "target_period": target_yyyy_mm,
        "source": source,
        "methodology_status": methodology_status,
        "dry_run": effective_dry_run,
        "advances_marked": advances_marked,
        "log_persisted": log_persisted,
        "persist_target": persist_target if not effective_dry_run else None,
        "formula": formula,
        "partners": partners_rows,
        "fund_financing": fund_financing,
        "warnings": warnings,
        "marked_advances": marked_advances,
    }
