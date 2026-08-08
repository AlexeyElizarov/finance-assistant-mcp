"""Household basic-needs advance ledger (FIN-115)."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from household_base_share import load_mapping_file
from monthly_close_lib import ASSISTANT_ROOT

SUPPORTED_SCHEMA_VERSION = 1
CURRENCY = "EUR"
VALID_STATUSES = frozenset({"open", "deducted", "void"})
VALID_ACTIONS = frozenset({"register", "list", "void", "mark_deducted"})

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def default_mapping_path(profile: str) -> Path:
    """
    Contour mapping path for partner validation.

    :param profile: Data profile name
    :return: Path under ``ASSISTANT_ROOT/ops/``
    """
    return ASSISTANT_ROOT / "ops" / f"household-contour-mapping.{profile}.json"


def default_ledger_path(profile: str) -> Path:
    """
    Default advance ledger file for a data profile.

    :param profile: ``test`` / ``cand`` / ``prod``
    :return: Path under ``{ASSISTANT_ROOT}/working/household/``
    """
    return ASSISTANT_ROOT / "working" / "household" / f"household-advances.{profile}.json"


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


def next_calendar_month(yyyy_mm: str) -> str:
    """
    Return the calendar month after ``yyyy_mm``.

    :param yyyy_mm: Normalized ``YYYY-MM``
    :return: Next month ``YYYY-MM``
    """
    year, month = (int(part) for part in yyyy_mm.split("-", 1))
    if month == 12:
        return f"{year + 1}-01"
    return f"{year:04d}-{month + 1:02d}"


def utc_now_iso() -> str:
    """
    Current UTC timestamp for audit fields.

    :return: ISO8601 string with ``Z`` suffix
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def round_money(value: float | Decimal) -> float:
    """
    Round to cents (half-up).

    :param value: Amount in EUR
    :return: Rounded amount
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_amount(raw: Any) -> float:
    """
    Validate register amount: positive, max 2 decimal places.

    :param raw: Request amount
    :return: Rounded EUR amount
    :raises ValueError: When amount is invalid
    """
    try:
        amount = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"Invalid amount: {raw!r}") from exc
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount != quantized:
        raise ValueError("amount must have at most 2 decimal places")
    return float(quantized)


def load_partner_ids(profile: str, *, mapping_path: Path | None = None) -> frozenset[str]:
    """
    Load partner ids from contour mapping.

    :param profile: Data profile
    :param mapping_path: Optional override for tests
    :return: Set of partner ids
    :raises RuntimeError: When mapping missing or invalid
    """
    path = mapping_path or default_mapping_path(profile)
    mapping = load_mapping_file(path)
    partners = mapping.get("partners")
    if not isinstance(partners, list) or not partners:
        raise RuntimeError(f"Invalid partners in mapping: {path}")
    ids: list[str] = []
    for row in partners:
        if not isinstance(row, dict) or not row.get("id"):
            raise RuntimeError(f"Invalid partners in mapping: {path}")
        ids.append(str(row["id"]))
    return frozenset(ids)


def empty_ledger(profile: str) -> dict[str, Any]:
    """
    Build an empty ledger document.

    :param profile: Data profile name
    :return: Ledger dict with empty ``entries``
    """
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "profile": profile,
        "entries": [],
    }


def load_ledger(profile: str, *, ledger_path: Path | None = None) -> dict[str, Any]:
    """
    Load ledger JSON; missing file → empty entries.

    :param profile: Data profile
    :param ledger_path: Optional override for tests
    :return: Parsed ledger dict
    :raises RuntimeError: When JSON is corrupt
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
    return data


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    """
    Atomically persist ledger JSON.

    :param path: Target file path
    :param ledger: Ledger document
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ledger, indent=2, ensure_ascii=False) + "\n"
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


def _entry_id_prefix(issue_period: str, partner_id: str) -> str:
    compact = issue_period.replace("-", "")
    return f"adv-{compact}-{partner_id}-"


def generate_entry_id(ledger: dict[str, Any], issue_period: str, partner_id: str) -> str:
    """
    Allocate the next entry id for partner and issue month.

    :param ledger: Current ledger
    :param issue_period: Normalized issue month
    :param partner_id: Partner id
    :return: Unique entry id
    """
    prefix = _entry_id_prefix(issue_period, partner_id)
    count = sum(
        1
        for entry in ledger.get("entries", [])
        if isinstance(entry, dict) and str(entry.get("id", "")).startswith(prefix)
    )
    return f"{prefix}{count + 1:03d}"


def sum_open_by_partner(
    ledger: dict[str, Any],
    *,
    partner_id: str | None = None,
) -> dict[str, float]:
    """
    Sum open advances per partner.

    :param ledger: Ledger document
    :param partner_id: Optional single-partner filter
    :return: ``partner_id → EUR`` totals for ``open`` entries
    """
    totals: dict[str, float] = {}
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        pid = str(entry.get("partner_id", ""))
        if partner_id is not None and pid != partner_id:
            continue
        totals[pid] = round_money(totals.get(pid, 0.0) + float(entry.get("amount", 0.0)))
    return totals


def sum_open_for_issue_period(
    ledger: dict[str, Any],
    issue_period: str,
    *,
    partner_id: str | None = None,
) -> dict[str, float]:
    """
    Sum open advances for an issue month per partner.

    :param ledger: Ledger document
    :param issue_period: Normalized ``YYYY-MM``
    :param partner_id: Optional single-partner filter
    :return: ``partner_id → EUR`` totals
    """
    totals: dict[str, float] = {}
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        if entry.get("issue_period") != issue_period:
            continue
        pid = str(entry.get("partner_id", ""))
        if partner_id is not None and pid != partner_id:
            continue
        totals[pid] = round_money(totals.get(pid, 0.0) + float(entry.get("amount", 0.0)))
    return totals


def totals_by_issue_period(ledger: dict[str, Any]) -> dict[str, float]:
    """
    Aggregate open advance amounts by issue period across all partners.

    :param ledger: Ledger document
    :return: ``issue_period → EUR`` totals
    """
    totals: dict[str, float] = {}
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        period = str(entry.get("issue_period", ""))
        totals[period] = round_money(totals.get(period, 0.0) + float(entry.get("amount", 0.0)))
    return totals


def filter_entries(ledger: dict[str, Any], filters: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Filter ledger entries by optional list filters.

    :param ledger: Ledger document
    :param filters: Optional ``partner_id``, ``issue_period``, ``deduct_in_period``, ``status``
    :return: Matching entry dicts (copies)
    """
    rows = [
        dict(entry)
        for entry in ledger.get("entries", [])
        if isinstance(entry, dict)
    ]
    partner_id = filters.get("partner_id")
    if partner_id is not None:
        rows = [row for row in rows if row.get("partner_id") == partner_id]
    issue_period = filters.get("issue_period")
    if issue_period is not None:
        rows = [row for row in rows if row.get("issue_period") == issue_period]
    deduct_in_period = filters.get("deduct_in_period")
    if deduct_in_period is not None:
        rows = [row for row in rows if row.get("deduct_in_period") == deduct_in_period]
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
        raise ValueError(f"ambiguous advance id: {entry_id}")
    return matches[0] if matches else None


def _action_register(
    ledger: dict[str, Any],
    partners: frozenset[str],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    partner_id = str(arguments["partner_id"])
    if partner_id not in partners:
        raise ValueError(f"unknown partner_id: {partner_id}")
    issue_period = normalize_period(str(arguments["issue_period"]))
    amount = validate_amount(arguments["amount"])
    note = arguments.get("note")
    entry = {
        "id": generate_entry_id(ledger, issue_period, partner_id),
        "partner_id": partner_id,
        "issue_period": issue_period,
        "deduct_in_period": next_calendar_month(issue_period),
        "amount": amount,
        "currency": CURRENCY,
        "note": str(note) if note is not None else None,
        "status": "open",
        "registered_at": utc_now_iso(),
        "voided_at": None,
        "void_reason": None,
        "deducted_at": None,
    }
    ledger.setdefault("entries", []).append(entry)
    return {"ok": True, "action": "register", "entry": entry}


def _action_list(ledger: dict[str, Any], profile: str, arguments: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for key in ("partner_id", "issue_period", "deduct_in_period", "status"):
        if key in arguments and arguments[key] is not None:
            value = str(arguments[key]) if key != "status" else str(arguments[key])
            if key in ("issue_period", "deduct_in_period"):
                value = normalize_period(value)
            if key == "status" and value not in VALID_STATUSES:
                raise ValueError(f"invalid status: {value}")
            filters[key] = value
    entries = filter_entries(ledger, filters)
    return {
        "ok": True,
        "action": "list",
        "profile": profile,
        "filters": filters,
        "entries": entries,
        "totals_by_partner": sum_open_by_partner(ledger),
        "totals_by_issue_period": totals_by_issue_period(ledger),
    }


def _action_void(ledger: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    entry_id = str(arguments["id"])
    entry = _find_entry_by_id(ledger, entry_id)
    if entry is None:
        raise ValueError(f"advance not found: {entry_id}")
    status = entry.get("status")
    if status == "void":
        raise ValueError("already_void")
    if status == "deducted":
        raise ValueError("already_deducted")
    entry["status"] = "void"
    entry["voided_at"] = utc_now_iso()
    reason = arguments.get("reason")
    entry["void_reason"] = str(reason) if reason is not None else None
    return {"ok": True, "action": "void", "entry": dict(entry)}


def _action_mark_deducted(ledger: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    issue_period = normalize_period(str(arguments["issue_period"]))
    partner_filter = arguments.get("partner_id")
    if partner_filter is not None:
        partner_filter = str(partner_filter)
    marked: list[dict[str, Any]] = []
    marked_total = 0.0
    for entry in ledger.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        if entry.get("issue_period") != issue_period:
            continue
        if partner_filter is not None and entry.get("partner_id") != partner_filter:
            continue
        entry["status"] = "deducted"
        entry["deducted_at"] = utc_now_iso()
        marked.append(dict(entry))
        marked_total = round_money(marked_total + float(entry.get("amount", 0.0)))
    return {
        "ok": True,
        "action": "mark_deducted",
        "issue_period": issue_period,
        "marked": marked,
        "marked_total": marked_total,
    }


def run_household_advances(profile: str, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Dispatch ``household_advances`` MCP action.

    :param profile: Data profile
    :param action: ``register`` | ``list`` | ``void`` | ``mark_deducted``
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

    if action == "register":
        for field in ("partner_id", "issue_period", "amount"):
            if field not in arguments:
                raise ValueError(f"missing required field: {field}")
        result = _action_register(ledger, partners, arguments)
        save_ledger(path, ledger)
        result["profile"] = profile
        return result
    if action == "list":
        return _action_list(ledger, profile, arguments)
    if action == "void":
        if "id" not in arguments:
            raise ValueError("missing required field: id")
        result = _action_void(ledger, arguments)
        save_ledger(path, ledger)
        result["profile"] = profile
        return result
    if action == "mark_deducted":
        if "issue_period" not in arguments:
            raise ValueError("missing required field: issue_period")
        result = _action_mark_deducted(ledger, arguments)
        save_ledger(path, ledger)
        result["profile"] = profile
        return result
    raise ValueError(f"unknown action: {action}")
