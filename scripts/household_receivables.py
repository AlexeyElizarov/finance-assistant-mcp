"""Household third-party receivables ledger (FIN-116)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from household_advances import (
    load_partner_ids,
    normalize_period,
    round_money,
    save_ledger,
    utc_now_iso,
    validate_amount,
)
from monthly_close_lib import ASSISTANT_ROOT

SUPPORTED_SCHEMA_VERSION = 1
CURRENCY = "EUR"
VALID_SOURCES = frozenset({"personal", "shared"})
VALID_STATUSES = frozenset({"open", "repaid", "written_off", "gift"})
TERMINAL_STATUSES = frozenset({"repaid", "written_off", "gift"})
VALID_ACTIONS = frozenset(
    {"register", "record_repayment", "list", "extend", "write_off", "mark_gift"}
)
_BALANCE_TOLERANCE = 0.011


def default_ledger_path(profile: str) -> Path:
    """
    Default receivables ledger file for a data profile.

    :param profile: ``test`` / ``cand`` / ``prod``
    :return: Path under ``{ASSISTANT_ROOT}/working/household/``
    """
    return ASSISTANT_ROOT / "working" / "household" / f"household-receivables.{profile}.json"


def current_calendar_month_utc() -> str:
    """
    Current calendar month in UTC as ``YYYY-MM``.

    :return: Month key
    """
    return datetime.now(UTC).strftime("%Y-%m")


def empty_ledger(profile: str) -> dict[str, Any]:
    """
    Build an empty receivables ledger document.

    :param profile: Data profile name
    :return: Ledger dict with empty ``entries``
    """
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "profile": profile,
        "entries": [],
    }


def _sum_repayments(entry: dict[str, Any]) -> float:
    repayments = entry.get("repayments")
    if not isinstance(repayments, list):
        return 0.0
    total = 0.0
    for row in repayments:
        if isinstance(row, dict):
            total += float(row.get("amount", 0.0))
    return round_money(total)


def _validate_entry_invariants(entry: dict[str, Any]) -> None:
    entry_id = entry.get("id", "?")
    status = entry.get("status")
    if status not in VALID_STATUSES:
        raise RuntimeError(f"Invalid entry status: {entry_id!r}: {status!r}")
    balance = round_money(float(entry.get("balance", 0.0)))
    principal = float(entry.get("principal", 0.0))
    if entry.get("currency") != CURRENCY:
        raise RuntimeError(f"Invalid currency for entry {entry_id!r}")
    if status == "open":
        rep_sum = _sum_repayments(entry)
        expected = round_money(principal - rep_sum)
        if abs(balance - expected) > _BALANCE_TOLERANCE:
            raise RuntimeError(f"balance desync for open entry {entry_id!r}")
        if balance <= 0 or balance > principal + _BALANCE_TOLERANCE:
            raise RuntimeError(f"invalid open balance for entry {entry_id!r}")
    elif status in TERMINAL_STATUSES:
        if balance != 0.0:
            raise RuntimeError(f"terminal entry must have balance 0: {entry_id!r}")
        if not entry.get("closed_at"):
            raise RuntimeError(f"terminal entry missing closed_at: {entry_id!r}")


def load_ledger(profile: str, *, ledger_path: Path | None = None) -> dict[str, Any]:
    """
    Load ledger JSON; missing file → empty entries.

    :param profile: Data profile
    :param ledger_path: Optional override for tests
    :return: Parsed ledger dict
    :raises RuntimeError: When JSON is corrupt or invariants fail
    """
    path = ledger_path or default_ledger_path(profile)
    if not path.is_file():
        return empty_ledger(profile)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid ledger JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid ledger JSON: {path}: expected object")
    entries = data.get("entries")
    if entries is None:
        data["entries"] = []
    elif not isinstance(entries, list):
        raise RuntimeError(f"Invalid ledger JSON: {path}: entries must be array")
    data.setdefault("schema_version", SUPPORTED_SCHEMA_VERSION)
    data.setdefault("profile", profile)
    for entry in data["entries"]:
        if isinstance(entry, dict):
            _validate_entry_invariants(entry)
    return data


def _entry_id_prefix(issue_period: str, lender_id: str) -> str:
    compact = issue_period.replace("-", "")
    return f"recv-{compact}-{lender_id}-"


def generate_entry_id(ledger: dict[str, Any], issue_period: str, lender_id: str) -> str:
    """
    Allocate the next entry id for lender and issue month.

    :param ledger: Current ledger
    :param issue_period: Normalized issue month
    :param lender_id: Lender partner id
    :return: Unique entry id
    """
    prefix = _entry_id_prefix(issue_period, lender_id)
    count = sum(
        1
        for entry in ledger.get("entries", [])
        if isinstance(entry, dict) and str(entry.get("id", "")).startswith(prefix)
    )
    return f"{prefix}{count + 1:03d}"


def compute_is_overdue(entry: dict[str, Any], as_of_period: str) -> bool:
    """
    Whether an entry is overdue relative to ``as_of_period``.

    :param entry: Ledger entry
    :param as_of_period: Normalized ``YYYY-MM``
    :return: True when open with balance and due before as_of
    """
    if entry.get("status") != "open":
        return False
    balance = float(entry.get("balance", 0.0))
    if balance <= 0:
        return False
    due_period = str(entry.get("due_period", ""))
    return due_period < as_of_period


def sum_outstanding_by_lender(
    ledger: dict[str, Any],
    *,
    lender_id: str | None = None,
) -> dict[str, float]:
    """
    Sum open personal-source balances by lender.

    :param ledger: Ledger document
    :param lender_id: Optional single-lender filter
    :return: ``lender_id → EUR`` totals
    """
    totals: dict[str, float] = {}
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        if entry.get("source") != "personal":
            continue
        lid = str(entry.get("lender_id", ""))
        if lender_id is not None and lid != lender_id:
            continue
        totals[lid] = round_money(totals.get(lid, 0.0) + float(entry.get("balance", 0.0)))
    return totals


def sum_outstanding_shared(ledger: dict[str, Any]) -> float:
    """
    Sum open shared-source balances.

    :param ledger: Ledger document
    :return: Total EUR outstanding from shared fund loans
    """
    total = 0.0
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        if entry.get("source") != "shared":
            continue
        total = round_money(total + float(entry.get("balance", 0.0)))
    return total


def totals_by_due_period(ledger: dict[str, Any]) -> dict[str, float]:
    """
    Aggregate open balances by due period.

    :param ledger: Ledger document
    :return: ``due_period → EUR`` totals
    """
    totals: dict[str, float] = {}
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        period = str(entry.get("due_period", ""))
        totals[period] = round_money(totals.get(period, 0.0) + float(entry.get("balance", 0.0)))
    return totals


def list_overdue_entries(
    ledger: dict[str, Any],
    *,
    as_of_period: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return open entries with ``due_period`` before ``as_of_period``.

    :param ledger: Ledger document
    :param as_of_period: Normalized month; default current UTC month
    :return: Entry dict copies with ``is_overdue`` flag
    """
    ref = normalize_period(as_of_period) if as_of_period else current_calendar_month_utc()
    rows: list[dict[str, Any]] = []
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        row["is_overdue"] = compute_is_overdue(entry, ref)
        if row["is_overdue"]:
            rows.append(row)
    return rows


def filter_entries(ledger: dict[str, Any], filters: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Filter ledger entries by optional list filters.

    :param ledger: Ledger document
    :param filters: Optional list filter fields
    :return: Matching entry dicts (copies)
    """
    rows = [dict(entry) for entry in ledger.get("entries", []) if isinstance(entry, dict)]
    lender_id = filters.get("lender_id")
    if lender_id is not None:
        rows = [row for row in rows if row.get("lender_id") == lender_id]
    borrower_label = filters.get("borrower_label")
    if borrower_label is not None:
        needle = str(borrower_label).casefold()
        rows = [
            row
            for row in rows
            if needle in str(row.get("borrower_label", "")).casefold()
        ]
    issue_period = filters.get("issue_period")
    if issue_period is not None:
        rows = [row for row in rows if row.get("issue_period") == issue_period]
    due_period = filters.get("due_period")
    if due_period is not None:
        rows = [row for row in rows if row.get("due_period") == due_period]
    source = filters.get("source")
    if source is not None:
        rows = [row for row in rows if row.get("source") == source]
    status = filters.get("status")
    if status is not None:
        rows = [row for row in rows if row.get("status") == status]
    return rows


def _find_entry_by_id(ledger: dict[str, Any], entry_id: str) -> dict[str, Any] | None:
    matches = [
        entry
        for entry in ledger.get("entries", [])
        if isinstance(entry, dict) and entry.get("id") == entry_id
    ]
    if len(matches) > 1:
        raise ValueError(f"ambiguous receivable id: {entry_id}")
    return matches[0] if matches else None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _require_open(entry: dict[str, Any]) -> None:
    if entry.get("status") != "open":
        raise ValueError("not_open")


def _action_register(
    ledger: dict[str, Any],
    partners: frozenset[str],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    lender_id = str(arguments["lender_id"])
    if lender_id not in partners:
        raise ValueError(f"unknown lender_id: {lender_id}")
    borrower_label = str(arguments["borrower_label"]).strip()
    if not borrower_label:
        raise ValueError("borrower_label must be non-empty")
    source = str(arguments["source"])
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source}")
    issue_period = normalize_period(str(arguments["issue_period"]))
    due_period = normalize_period(str(arguments["due_period"]))
    if due_period < issue_period:
        raise ValueError("due_period must be >= issue_period")
    amount = validate_amount(arguments["amount"])
    entry = {
        "id": generate_entry_id(ledger, issue_period, lender_id),
        "lender_id": lender_id,
        "borrower_label": borrower_label,
        "principal": amount,
        "balance": amount,
        "currency": CURRENCY,
        "source": source,
        "issue_period": issue_period,
        "due_period": due_period,
        "note": _optional_str(arguments.get("note")),
        "transaction_key": _optional_str(arguments.get("transaction_key")),
        "status": "open",
        "repayments": [],
        "extensions": [],
        "registered_at": utc_now_iso(),
        "closed_at": None,
        "close_reason": None,
    }
    ledger.setdefault("entries", []).append(entry)
    return {"ok": True, "action": "register", "entry": dict(entry)}


def _action_record_repayment(ledger: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    entry_id = str(arguments["id"])
    entry = _find_entry_by_id(ledger, entry_id)
    if entry is None:
        raise ValueError(f"receivable not found: {entry_id}")
    _require_open(entry)
    amount = validate_amount(arguments["amount"])
    balance = round_money(float(entry.get("balance", 0.0)))
    if amount > balance + _BALANCE_TOLERANCE:
        raise ValueError("repayment amount exceeds balance")
    receipt_period = normalize_period(str(arguments["receipt_period"]))
    repayment = {
        "amount": amount,
        "receipt_period": receipt_period,
        "recorded_at": utc_now_iso(),
        "note": _optional_str(arguments.get("note")),
        "transaction_key": _optional_str(arguments.get("transaction_key")),
    }
    entry.setdefault("repayments", []).append(repayment)
    new_balance = round_money(balance - amount)
    entry["balance"] = new_balance
    if new_balance == 0.0:
        entry["status"] = "repaid"
        entry["closed_at"] = utc_now_iso()
    return {
        "ok": True,
        "action": "record_repayment",
        "entry": dict(entry),
        "repayment": repayment,
    }


def _action_extend(ledger: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    entry_id = str(arguments["id"])
    entry = _find_entry_by_id(ledger, entry_id)
    if entry is None:
        raise ValueError(f"receivable not found: {entry_id}")
    _require_open(entry)
    if float(entry.get("balance", 0.0)) <= 0:
        raise ValueError("not_open")
    old_due = str(entry.get("due_period", ""))
    new_due = normalize_period(str(arguments["new_due_period"]))
    if new_due <= old_due:
        raise ValueError("new_due_period must be after current due_period")
    extension = {
        "from_due_period": old_due,
        "to_due_period": new_due,
        "extended_at": utc_now_iso(),
        "note": _optional_str(arguments.get("note")),
    }
    entry.setdefault("extensions", []).append(extension)
    entry["due_period"] = new_due
    return {"ok": True, "action": "extend", "entry": dict(entry)}


def _close_entry(entry: dict[str, Any], *, status: str, close_reason: str) -> None:
    if float(entry.get("balance", 0.0)) <= 0:
        raise ValueError("not_open")
    entry["status"] = status
    entry["balance"] = 0.0
    entry["closed_at"] = utc_now_iso()
    entry["close_reason"] = close_reason


def _action_write_off(ledger: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    entry_id = str(arguments["id"])
    entry = _find_entry_by_id(ledger, entry_id)
    if entry is None:
        raise ValueError(f"receivable not found: {entry_id}")
    _require_open(entry)
    reason = arguments.get("reason")
    close_reason = str(reason) if reason is not None else "written_off"
    _close_entry(entry, status="written_off", close_reason=close_reason)
    return {"ok": True, "action": "write_off", "entry": dict(entry)}


def _action_mark_gift(ledger: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    entry_id = str(arguments["id"])
    entry = _find_entry_by_id(ledger, entry_id)
    if entry is None:
        raise ValueError(f"receivable not found: {entry_id}")
    _require_open(entry)
    reason = arguments.get("reason")
    close_reason = str(reason) if reason is not None else "gift"
    _close_entry(entry, status="gift", close_reason=close_reason)
    return {"ok": True, "action": "mark_gift", "entry": dict(entry)}


def _action_list(ledger: dict[str, Any], profile: str, arguments: dict[str, Any]) -> dict[str, Any]:
    as_of_raw = arguments.get("as_of_period")
    as_of_period = (
        normalize_period(str(as_of_raw)) if as_of_raw is not None else current_calendar_month_utc()
    )
    filters: dict[str, Any] = {}
    for key in ("lender_id", "borrower_label", "issue_period", "due_period", "source", "status"):
        if key in arguments and arguments[key] is not None:
            value = str(arguments[key])
            if key in ("issue_period", "due_period"):
                value = normalize_period(value)
            if key == "source" and value not in VALID_SOURCES:
                raise ValueError(f"invalid source: {value}")
            if key == "status" and value not in VALID_STATUSES:
                raise ValueError(f"invalid status: {value}")
            filters[key] = value if key != "borrower_label" else arguments[key]
    entries = filter_entries(ledger, filters)
    for row in entries:
        row["is_overdue"] = compute_is_overdue(row, as_of_period)
    overdue_count = sum(
        1
        for entry in ledger.get("entries", [])
        if isinstance(entry, dict) and compute_is_overdue(entry, as_of_period)
    )
    return {
        "ok": True,
        "action": "list",
        "profile": profile,
        "as_of_period": as_of_period,
        "filters": filters,
        "entries": entries,
        "totals_by_lender": sum_outstanding_by_lender(ledger),
        "totals_shared": sum_outstanding_shared(ledger),
        "totals_by_due_period": totals_by_due_period(ledger),
        "overdue_count": overdue_count,
    }


def run_household_receivables(
    profile: str,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Dispatch ``household_receivables`` MCP action.

    :param profile: Data profile
    :param action: register | record_repayment | list | extend | write_off | mark_gift
    :param arguments: Action-specific fields
    :return: Tool response payload
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown action: {action}")
    ledger_path = arguments.get("_ledger_path")
    mapping_path = arguments.get("_mapping_path")
    path = Path(ledger_path) if ledger_path else default_ledger_path(profile)
    partners = load_partner_ids(profile, mapping_path=Path(mapping_path) if mapping_path else None)
    ledger = load_ledger(profile, ledger_path=path)
    ledger["profile"] = profile

    mutating = action in {"register", "record_repayment", "extend", "write_off", "mark_gift"}

    if action == "register":
        for field in ("lender_id", "borrower_label", "amount", "source", "issue_period", "due_period"):
            if field not in arguments:
                raise ValueError(f"missing required field: {field}")
        result = _action_register(ledger, partners, arguments)
    elif action == "list":
        return _action_list(ledger, profile, arguments)
    elif action == "record_repayment":
        for field in ("id", "amount", "receipt_period"):
            if field not in arguments:
                raise ValueError(f"missing required field: {field}")
        result = _action_record_repayment(ledger, arguments)
    elif action == "extend":
        for field in ("id", "new_due_period"):
            if field not in arguments:
                raise ValueError(f"missing required field: {field}")
        result = _action_extend(ledger, arguments)
    elif action == "write_off":
        if "id" not in arguments:
            raise ValueError("missing required field: id")
        result = _action_write_off(ledger, arguments)
    elif action == "mark_gift":
        if "id" not in arguments:
            raise ValueError("missing required field: id")
        result = _action_mark_gift(ledger, arguments)
    else:
        raise ValueError(f"unknown action: {action}")

    if mutating:
        save_ledger(path, ledger)
    result["profile"] = profile
    return result
