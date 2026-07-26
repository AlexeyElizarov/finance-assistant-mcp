"""Shared monthly close helpers for Finance Assistant MCP (``process_month`` tool)."""

from __future__ import annotations

import calendar
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from decimal import Decimal
from typing import Any

from finance_api_client import ApiClient, resolve_api_base

_DEFAULT_ASSISTANT_ROOT = Path(r"C:\Users\haake\assistant\35-finance-assistant")
ASSISTANT_ROOT = Path(os.environ.get("FINANCE_ASSISTANT_ROOT", str(_DEFAULT_ASSISTANT_ROOT)))
REPORTS_ROOT = ASSISTANT_ROOT.parent / "33-financial-reports"
KANON = REPORTS_ROOT / "kontoauszuege"
WORKING = ASSISTANT_ROOT / "working" / "monthly-close-api"

REPORT_SUBDIRS: dict[str, str] = {
    "test": "test-reports",
    "cand": "cand-reports",
    "prod": "prod-reports",
}

FALLBACK_BUDGET_VERSION_ID = "d008ce16-03b1-434a-839a-26a51b72e204"

IMPORT_ORDER: tuple[tuple[str, str], ...] = (
    ("sparkasse_mastercard", "Mastercard"),
    ("sparkasse_sepa", "SEPA Giro"),
    ("c24", "C24"),
)

CLOSE_PHASES = ("preliminary", "final")

PRESET_MONTHLY_CLOSE_PREPARE = "monthly_close_prepare"

PROCESS_MONTH_PRESETS: dict[str, dict[str, Any]] = {
    PRESET_MONTHLY_CLOSE_PREPARE: {
        "reopen_neighbors": True,
        "reopen": True,
        "reports": True,
        "close": False,
        "skip_import": False,
        "verify_only": False,
    },
}

OVERRIDABLE_PROCESS_MONTH_KEYS = frozenset(
    {
        "reopen_neighbors",
        "reopen",
        "skip_import",
        "reports",
        "close",
        "verify_only",
        "close_phase",
        "apply_keywords",
        "c9999_acknowledged",
    }
)


def validate_process_month_close_phase(arguments: dict[str, Any], close_flag: bool) -> None:
    """
    Reject ``close_phase`` without ``close=true`` (FIN-31 D-09).

    :param arguments: Raw MCP tool arguments
    :param close_flag: Effective close flag after preset merge
    :raises ValueError: When ``close_phase`` is present but close is false
    """
    if "close_phase" in arguments and not close_flag:
        raise ValueError("close_phase requires close=true")


def validate_process_month_c9999_acknowledged(
    arguments: dict[str, Any],
    close_flag: bool,
    close_phase: str,
) -> None:
    """
    Reject invalid ``c9999_acknowledged`` combinations (FIN-2 D-05, D-06).

    :param arguments: Raw MCP tool arguments
    :param close_flag: Effective close flag after preset merge
    :param close_phase: Effective close phase
    :raises ValueError: When ack is set without close or with final close
    """
    if not bool(arguments.get("c9999_acknowledged")):
        return
    if not close_flag:
        raise ValueError("c9999_acknowledged requires close=true")
    if close_phase == "final":
        raise ValueError("c9999_acknowledged is not allowed with close_phase=final")


UNIFIED_KEYWORDS_SECTIONS = frozenset({"categories", "budget_items", "projects"})


class ApplyKeywordsError(Exception):
    """Base error for keyword apply flows (FIN-16)."""


class ApplyKeywordsValidationError(ApplyKeywordsError):
    """Invalid payload or unknown entity reference."""


class ApplyKeywordsPartialError(ApplyKeywordsError):
    """HTTP failure after partial apply (FIN-16 D-07)."""

    def __init__(self, message: str, *, partial_changes: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__(message)
        self.partial_changes = partial_changes


def empty_keywords_changes() -> dict[str, list[dict[str, Any]]]:
    """
    Return an empty keyword change journal (FIN-16).

    :return: All journal lists empty
    """
    return {
        "categories_added": [],
        "categories_removed": [],
        "budget_items_added": [],
        "budget_items_removed": [],
        "projects_added": [],
        "projects_removed": [],
    }


def _is_category_id(key: str) -> bool:
    return len(key) >= 2 and key[0] in "CPSI"


def _validate_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ApplyKeywordsValidationError(f"{label} must be a string array")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ApplyKeywordsValidationError(f"{label}[{index}] must be a string")
        out.append(item)
    return out


def _normalize_keyword_ops(adds: list[str], removes: list[str]) -> tuple[list[str], list[str]]:
    def dedup(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for kw in items:
            if not kw.strip():
                continue
            if kw in seen:
                continue
            seen.add(kw)
            out.append(kw)
        return out

    return dedup(adds), dedup(removes)


def _parse_entity_keyword_ops(entry: Any, label: str) -> tuple[list[str], list[str]]:
    if isinstance(entry, list):
        adds = _validate_string_list(entry, label)
        return _normalize_keyword_ops(adds, [])
    if isinstance(entry, dict):
        extra = set(entry) - {"add", "remove"}
        if extra:
            raise ApplyKeywordsValidationError(
                f"{label}: extra fields not allowed: {sorted(extra)}"
            )
        raw_add = entry.get("add", [])
        raw_remove = entry.get("remove", [])
        if raw_add is None or raw_remove is None:
            raise ApplyKeywordsValidationError(f"{label}: add/remove must not be null")
        adds = _validate_string_list(raw_add, f"{label}.add")
        removes = _validate_string_list(raw_remove, f"{label}.remove")
        return _normalize_keyword_ops(adds, removes)
    raise ApplyKeywordsValidationError(f"{label} must be a string array or add/remove object")


@dataclass
class _ParsedKeywordsPayload:
    categories: list[tuple[str, list[str], list[str]]]
    budget_items: list[tuple[str, list[str], list[str]]]
    projects: list[tuple[str, list[str], list[str]]]


def parse_keywords_payload(payload: Any) -> _ParsedKeywordsPayload:
    """
    Validate and normalize a keywords JSON payload (FIN-16).

    :param payload: Raw JSON object
    :return: Parsed sections with normalized add/remove lists
    :raises ApplyKeywordsValidationError: Invalid structure
    """
    if not isinstance(payload, dict):
        raise ApplyKeywordsValidationError("payload root must be an object")
    if not payload:
        return _ParsedKeywordsPayload([], [], [])

    keys = list(payload.keys())
    has_unified = any(key in UNIFIED_KEYWORDS_SECTIONS for key in keys)
    has_legacy = any(_is_category_id(key) for key in keys)
    invalid_root = [
        key for key in keys if key not in UNIFIED_KEYWORDS_SECTIONS and not _is_category_id(key)
    ]
    if has_unified and has_legacy:
        raise ApplyKeywordsValidationError("mixed unified sections and legacy category keys")
    if invalid_root:
        raise ApplyKeywordsValidationError(f"invalid root keys: {invalid_root}")

    if has_unified:
        if any(key not in UNIFIED_KEYWORDS_SECTIONS for key in keys):
            bad = [key for key in keys if key not in UNIFIED_KEYWORDS_SECTIONS]
            raise ApplyKeywordsValidationError(f"invalid root keys: {bad}")
        categories_sec = payload.get("categories", {})
        budget_sec = payload.get("budget_items", {})
        projects_sec = payload.get("projects", {})
        for name, section in (
            ("categories", categories_sec),
            ("budget_items", budget_sec),
            ("projects", projects_sec),
        ):
            if section is None or not isinstance(section, dict):
                raise ApplyKeywordsValidationError(f"{name} section must be an object")
        categories = [
            (cat_id, *_parse_entity_keyword_ops(entry, f"categories.{cat_id}"))
            for cat_id, entry in categories_sec.items()
        ]
        budget_items = [
            (item_key, *_parse_entity_keyword_ops(entry, f"budget_items.{item_key}"))
            for item_key, entry in budget_sec.items()
        ]
        projects = [
            (proj_id, *_parse_entity_keyword_ops(entry, f"projects.{proj_id}"))
            for proj_id, entry in projects_sec.items()
        ]
        return _ParsedKeywordsPayload(categories, budget_items, projects)

    if any(not _is_category_id(key) for key in keys):
        raise ApplyKeywordsValidationError("legacy payload keys must be category ids")
    categories = []
    for cat_id, entry in payload.items():
        adds = _validate_string_list(entry, cat_id)
        norm_adds, norm_removes = _normalize_keyword_ops(adds, [])
        categories.append((cat_id, norm_adds, norm_removes))
    return _ParsedKeywordsPayload(categories, [], [])


def keywords_payload_effective(payload: dict[str, Any]) -> bool:
    """
    Return whether payload contains at least one non-blank add after normalization.

    :param payload: Keywords JSON object (legacy or unified)
    :return: True when at least one add keyword remains after normalization
    """
    try:
        parsed = parse_keywords_payload(payload)
    except ApplyKeywordsValidationError:
        return False
    for _key, adds, _removes in (
        parsed.categories + parsed.budget_items + parsed.projects
    ):
        if adds:
            return True
    return False


def keywords_file_effective(path: Path) -> bool:
    """
    Return whether a keywords JSON file is effective for C9999 guard (FIN-2 D-03).

    :param path: Path to keywords JSON file
    :return: True when payload contains at least one non-blank add
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return False
    return keywords_payload_effective(raw)


def _readiness_check_by_id(
    readiness: dict[str, Any],
    check_id: str,
) -> dict[str, Any] | None:
    """
    Return a readiness check item by id.

    :param readiness: Readiness payload from API
    :param check_id: Check identifier
    :return: Matching check or ``None``
    """
    for check in readiness.get("checks") or []:
        if isinstance(check, dict) and check.get("id") == check_id:
            return check
    return None


def c9999_close_guard_error(
    *,
    expense_c9999_count: int,
    close_phase: str,
    keywords_effective: bool,
    c9999_acknowledged: bool,
    readiness: dict[str, Any] | None = None,
) -> str | None:
    """
    Return close-blocking classification error, or ``None`` when close may proceed.

    Final (FIN-69): requires readiness checks ``unclassified_pending`` and
    ``other_without_note``; ``expense_c9999_count`` alone does not block.
    Preliminary (FIN-2): C9999 requires ack or effective keywords; new checks optional.

    :param expense_c9999_count: Count from latest verify classification summary
    :param close_phase: ``preliminary`` or ``final``
    :param keywords_effective: Whether apply_keywords payload was effective
    :param c9999_acknowledged: Operator acknowledged retained C9999 (preliminary only)
    :param readiness: Backend readiness payload (required fields for final)
    :return: English error message when blocked, else ``None``
    """
    if close_phase == "final":
        payload = readiness if isinstance(readiness, dict) else {}
        pending_check = _readiness_check_by_id(payload, "unclassified_pending")
        note_check = _readiness_check_by_id(payload, "other_without_note")
        if pending_check is None or note_check is None:
            return (
                "API readiness missing required classification checks "
                "(unclassified_pending, other_without_note) for final close"
            )
        pending_count = int(
            (pending_check.get("details") or {}).get("unclassified_pending_count") or 0
        )
        note_count = int((note_check.get("details") or {}).get("count") or 0)
        if pending_count > 0:
            return "unclassified pending > 0 — resolve pending before final close"
        if note_count > 0:
            return (
                "intentional Other without reconciliation_note — "
                "add notes before final close"
            )
        return None
    if expense_c9999_count <= 0:
        return None
    if close_phase == "preliminary":
        if keywords_effective or c9999_acknowledged:
            return None
        return (
            "C9999 > 0 — apply_keywords or c9999_acknowledged before preliminary close"
        )
    return None


def resolve_process_month_arguments(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """
    Expand ``preset`` into orchestrator defaults merged with explicit overrides.

    :param arguments: Raw MCP tool arguments
    :return: Effective orchestrator flags, or ``None`` when no preset
    :raises ValueError: Unknown preset name
    """
    if "preset" not in arguments:
        return None
    preset_name = str(arguments["preset"])
    base = PROCESS_MONTH_PRESETS.get(preset_name)
    if base is None:
        raise ValueError(f"unknown preset {preset_name!r}")
    explicit = {
        k: arguments[k]
        for k in OVERRIDABLE_PROCESS_MONTH_KEYS
        if k in arguments
    }
    return {**base, **explicit}


def prepare_process_month_orchestrator_flags(arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Resolve preset merge and return normalized orchestrator flags for the handler.

    :param arguments: Raw MCP tool arguments
    :return: Flag dict consumed by ``_handle_process_month``
    :raises ValueError: Invalid preset or ``close_phase`` without close
    """
    effective = resolve_process_month_arguments(arguments)
    if effective is None:
        close_flag = bool(arguments.get("close"))
        close_phase = str(arguments.get("close_phase") or "final")
        validate_process_month_close_phase(arguments, close_flag)
        validate_process_month_c9999_acknowledged(arguments, close_flag, close_phase)
        return {
            "verify_only": bool(arguments.get("verify_only")),
            "reopen": bool(arguments.get("reopen")),
            "reopen_neighbors": bool(arguments.get("reopen_neighbors")),
            "skip_import": bool(arguments.get("skip_import")),
            "close": close_flag,
            "close_phase": close_phase,
            "reports": bool(arguments.get("reports")),
            "apply_keywords": arguments.get("apply_keywords"),
            "c9999_acknowledged": bool(arguments.get("c9999_acknowledged")),
        }
    close_flag = bool(effective.get("close"))
    close_phase = str(effective.get("close_phase") or "final")
    validate_process_month_close_phase(arguments, close_flag)
    validate_process_month_c9999_acknowledged(arguments, close_flag, close_phase)
    return {
        "verify_only": bool(effective.get("verify_only")),
        "reopen": bool(effective.get("reopen")),
        "reopen_neighbors": bool(effective.get("reopen_neighbors")),
        "skip_import": bool(effective.get("skip_import")),
        "close": close_flag,
        "close_phase": close_phase,
        "reports": bool(effective.get("reports")),
        "apply_keywords": effective.get("apply_keywords"),
        "c9999_acknowledged": bool(arguments.get("c9999_acknowledged")),
    }


@dataclass(frozen=True)
class Period:
    """Calendar month for close pipeline."""

    year: int
    month: int

    @property
    def ymmm(self) -> str:
        return f"{self.year:04d}{self.month:02d}"

    @property
    def yyyy_mm(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def month_start(self) -> str:
        return f"{self.yyyy_mm}-01"


def parse_period(raw: str) -> Period:
    """
    Parse ``YYYY-MM`` or ``YYYYMM`` into :class:`Period`.

    :param raw: Period string
    :return: Parsed period
    :raises ValueError: if format invalid
    """
    cleaned = raw.strip().replace("-", "")
    if len(cleaned) != 6 or not cleaned.isdigit():
        raise ValueError(f"period must be YYYY-MM or YYYYMM, got {raw!r}")
    year = int(cleaned[:4])
    month = int(cleaned[4:6])
    if month < 1 or month > 12:
        raise ValueError(f"invalid month in period {raw!r}")
    return Period(year=year, month=month)


def period_last_day(period: Period) -> str:
    """
    Return ISO date of the last calendar day in a budget month (FIN-109 D-05).

    :param period: Target month
    :return: ``YYYY-MM-DD``
    """
    last = calendar.monthrange(period.year, period.month)[1]
    return f"{period.yyyy_mm}-{last:02d}"


def assert_period_range(start: Period, end: Period) -> None:
    """
    Reject when ``end`` month is strictly before ``start`` (FIN-109 D-05).

    :param start: REG start month
    :param end: REG end month
    :raises ValueError: When end precedes start
    """
    if (end.year, end.month) < (start.year, start.month):
        raise ValueError(
            f"end_period {end.yyyy_mm} must not be before start_period {start.yyyy_mm}",
        )


def shift_period(period: Period, months: int) -> Period:
    """
    Shift calendar month by ``months`` (negative = earlier).

    :param period: Base month
    :param months: Delta in months
    :return: Shifted period
    """
    total = period.year * 12 + (period.month - 1) + months
    return Period(year=total // 12, month=total % 12 + 1)


def mc_affected_periods(period: Period) -> list[Period]:
    """
    Accounting periods a Mastercard head+tail batch may touch.

    Tail PDF (16.(M+1)) writes ops from 17.M; head PDF (16.M) may touch M-1 tail.

    :param period: Target close month M
    :return: Neighbour months M-1, M, M+1
    """
    return [shift_period(period, -1), period, shift_period(period, 1)]


def act_horizon_periods(api: ApiClient) -> list[Period]:
    """
    List calendar months covered by the ACT budget version.

    :param api: Authenticated API client
    :return: Months from version ``start_date`` through ``end_date`` inclusive
    :raises RuntimeError: When no ACT version exists
    """
    body = api.get_json("/api/v1/budget/versions")
    versions = body.get("budget_versions") or body.get("versions") or []
    act = [v for v in versions if v.get("status") == "ACT"]
    if not act:
        raise RuntimeError("ACT budget version not found")
    version = act[0]
    start = date.fromisoformat(str(version["start_date"])[:10])
    end = date.fromisoformat(str(version["end_date"])[:10])
    periods: list[Period] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        periods.append(parse_period(f"{year:04d}-{month:02d}"))
        month += 1
        if month > 12:
            year += 1
            month = 1
    return periods


def filter_periods_to_horizon(
    periods: list[Period],
    horizon: list[Period],
) -> list[Period]:
    """
    Keep only periods that fall within the ACT budget horizon.

    :param periods: Candidate months (e.g. MC neighbours M-1, M, M+1)
    :param horizon: ACT version month list
    :return: Subset of ``periods`` inside ``horizon``
    """
    horizon_keys = {(p.year, p.month) for p in horizon}
    return [p for p in periods if (p.year, p.month) in horizon_keys]


def mc_reopen_neighbor_periods(
    period: Period,
    api: ApiClient,
) -> tuple[list[Period], list[str]]:
    """
    MC-affected months for ``reopen_neighbors``, restricted to ACT horizon.

    Months outside the ACT budget (e.g. 2025-12 when closing 2026-01) are skipped
    because reconciliation reopen returns 422.

    :param period: Target close month M
    :param api: Authenticated API client
    :return: Periods to reopen and ``YYYY-MM`` list skipped as out of horizon
    """
    affected = mc_affected_periods(period)
    horizon = act_horizon_periods(api)
    filtered = filter_periods_to_horizon(affected, horizon)
    filtered_keys = {(p.year, p.month) for p in filtered}
    skipped = [p.yyyy_mm for p in affected if (p.year, p.month) not in filtered_keys]
    return filtered, skipped


def connect_api(base: str | None, profile: str) -> tuple[ApiClient, str]:
    """
    Resolve base URL, login, then return authenticated client.

    Login runs before any authenticated GET (versions, meta).

    :param base: Explicit ``--base`` or None for port scan
    :param profile: Data profile
    :return: Client and resolved base URL
    """
    resolved = resolve_api_base(base, profile)
    api = ApiClient(resolved)
    api.login(data_profile=profile)
    return api, resolved


def resolve_mastercard_statements(period: Period, mc_dir: Path) -> list[Path]:
    """
    Resolve Mastercard Abrechnung PDFs for a calendar month.

    :param period: Target month
    :param mc_dir: Mastercard statements directory
    :return: One or two PDF paths to import
    :raises FileNotFoundError: if no matching file is found or match is ambiguous
    """
    needle = f"{period.year:04d}-{period.month:02d}"
    pdfs = [
        p
        for p in mc_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf" and "Abrechnung" in p.name
    ]
    new_style = [p for p in pdfs if needle in p.name]
    if len(new_style) == 1:
        head = new_style
        if period.month == 12:
            tail_token = f"17_01_{period.year + 1}"
            tail_iso = f"{period.year + 1}-01-"
        else:
            tail_token = f"17_{period.month + 1:02d}_{period.year}"
            tail_iso = f"{period.year:04d}-{period.month + 1:02d}-"
        tail = [
            p
            for p in pdfs
            if p not in head and (tail_token in p.name or tail_iso in p.name)
        ]
        if len(tail) > 1:
            names = [p.name for p in tail]
            raise FileNotFoundError(
                f"Mastercard: ambiguous tail PDF for {period.yyyy_mm}: {names}"
            )
        return tail + head
    if len(new_style) > 1:
        names = [p.name for p in new_style]
        raise FileNotFoundError(
            f"Mastercard: ambiguous Abrechnung PDF containing {needle!r}: {names}"
        )

    head_token = f"16_{period.month:02d}_{period.year}"
    if period.month == 12:
        tail_token = f"17_01_{period.year + 1}"
        tail_iso = f"{period.year + 1}-01-"
    else:
        tail_token = f"17_{period.month + 1:02d}_{period.year}"
        tail_iso = f"{period.year:04d}-{period.month + 1:02d}-"

    head = [p for p in pdfs if head_token in p.name]
    if not head:
        alt_head_token = f"17_{period.month:02d}_{period.year}"
        head = [p for p in pdfs if alt_head_token in p.name]
    tail = [p for p in pdfs if tail_token in p.name or tail_iso in p.name]
    if len(head) > 1 or len(tail) > 1:
        raise FileNotFoundError(
            f"Mastercard: ambiguous legacy match head={len(head)} tail={len(tail)} "
            f"for {period.yyyy_mm}"
        )
    resolved = tail + head
    if not resolved:
        raise FileNotFoundError(
            f"Mastercard: no Abrechnung PDF for {period.yyyy_mm} "
            f"(tried {needle!r}, {head_token!r}, {tail_token!r})"
        )
    return resolved


def resolve_statements(period: Period, kanon: Path = KANON) -> dict[str, Path | list[Path]]:
    """
    Resolve import files for the month from kontoauszuege conventions.

    :param period: Target month
    :param kanon: Statements root
    :return: provider id -> file path or list of paths (Mastercard)
    :raises FileNotFoundError: if a required file is missing or ambiguous
    """
    found: dict[str, Path | list[Path]] = {}

    c24 = kanon / "c24" / f"{period.yyyy_mm}-c24-transaktionen.csv"
    if not c24.is_file():
        raise FileNotFoundError(f"C24: expected {c24}")
    found["c24"] = c24

    sepa_glob = list(
        kanon.glob(
            f"sparkasse-giro/Konto_1019180243-Auszug_{period.year}_{period.month:04d}.*"
        )
    )
    sepa_pdfs = [p for p in sepa_glob if p.suffix.lower() == ".pdf"]
    if len(sepa_pdfs) != 1:
        raise FileNotFoundError(
            f"SEPA: expected one PDF Konto_…_{period.year}_{period.month:04d}.*, "
            f"found {len(sepa_pdfs)}"
        )
    found["sparkasse_sepa"] = sepa_pdfs[0]
    found["sparkasse_mastercard"] = resolve_mastercard_statements(
        period, kanon / "sparkasse-mastercard"
    )
    return found


def resolve_budget_version_id(api: ApiClient, period: Period) -> str:
    """
    Return budget version id whose horizon covers the close period.

    :param api: API client (authenticated)
    :param period: Target calendar month
    :return: Version UUID
    """
    body = api.get_json("/api/v1/budget/versions")
    versions = body.get("budget_versions") or body.get("versions") or []
    month_start = period.month_start
    covering = [
        v
        for v in versions
        if str(v.get("start_date", "")) <= month_start <= str(v.get("end_date", ""))
    ]
    if len(covering) == 1:
        return str(covering[0]["id"])
    act = [v for v in versions if v.get("status") == "ACT"]
    if len(act) == 1:
        return str(act[0]["id"])
    fallback = [v for v in versions if v.get("id") == FALLBACK_BUDGET_VERSION_ID]
    if len(fallback) == 1:
        return FALLBACK_BUDGET_VERSION_ID
    raise RuntimeError(
        f"cannot resolve budget version for {period.yyyy_mm}: "
        f"covering={len(covering)}, ACT count={len(act)}, "
        f"fallback present={bool(fallback)}"
    )


def fetch_reconciliation(
    api: ApiClient,
    budget_version_id: str,
    period: Period,
) -> dict[str, Any]:
    """
    Return reconciliation payload fields for a calendar month (passthrough from API).

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Target month
    :return: ``status``, ``methodology_status``, ``close_phase`` from API
    """
    body = fetch_reconciliation_full(api, budget_version_id, period)
    return {
        "status": str(body.get("status") or "open"),
        "methodology_status": body.get("methodology_status"),
        "close_phase": body.get("close_phase"),
    }


def fetch_reconciliation_full(
    api: ApiClient,
    budget_version_id: str,
    period: Period,
) -> dict[str, Any]:
    """
    Return full reconciliation payload for a calendar month.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Target month
    :return: Full API reconciliation body
    """
    return api.get_json(
        f"/api/v1/budget/reconciliation?budget_version_id={budget_version_id}"
        f"&period={period.month_start}"
    )


_BUDGET_ITEM_VALIDATION_MESSAGE = "Укажите существующую статью бюджета."


@dataclass(frozen=True)
class _OverrideDiagnosisFinding:
    """One diagnosable budget-item problem for override enrichment (FIN-120)."""

    priority_rank: int
    candidate_index: int
    kind: str
    budget_item_id: str
    name: str = ""
    status: str = ""
    planning_type: str = ""


def _normalize_api_error_fields(body: Any) -> tuple[str, str]:
    """
    Extract ``code`` and ``message`` from an API error body.

    :param body: Raw response body from ``api.request``
    :return: Code and message strings (may be empty)
    """
    if not isinstance(body, dict):
        return "", str(body)
    nested = body.get("error")
    if isinstance(nested, dict):
        return str(nested.get("code", "")), str(nested.get("message", ""))
    return str(body.get("code", "")), str(body.get("message", ""))


def is_budget_item_validation_failure(body: Any) -> bool:
    """
    Return whether a PUT reconciliation 422 is budget item validation (FIN-120 D-05).

    :param body: PUT response body
    :return: True when enrichment may apply
    """
    code, message = _normalize_api_error_fields(body)
    if code == "budget_item_not_in_version":
        return True
    return code == "validation_error" and message == _BUDGET_ITEM_VALIDATION_MESSAGE


def _unique_preserve_order(values: Any) -> list[str]:
    """
    Deduplicate string values preserving first-seen order.

    :param values: Iterable of values to stringify
    :return: Unique id list
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _override_budget_item_candidate_ids(
    overrides_arg: dict[str, str],
    current_map: dict[str, str],
) -> tuple[list[str], list[str]]:
    """
    Split override map values into arg-first and merged-extra candidate ids (FIN-120 D-03).

    :param overrides_arg: Map from the tool argument
    :param current_map: Full map sent to PUT
    :return: Arg ids and remaining merged ids
    """
    arg_ids = _unique_preserve_order(overrides_arg.values())
    arg_set = set(arg_ids)
    merged_extra = [
        item_id
        for item_id in _unique_preserve_order(current_map.values())
        if item_id not in arg_set
    ]
    return arg_ids, merged_extra


def _fetch_plan_item_budget_ids(
    api: ApiClient,
    budget_version_id: str,
) -> set[str] | None:
    """
    Load budget item ids linked to a version via plan-items (FIN-120 D-08).

    :param api: API client
    :param budget_version_id: ACT version UUID
    :return: Set of ids, or ``None`` on diagnostic API failure
    """
    status, body = api.request(
        "GET",
        f"/api/v1/budget/plan-items?budget_version_id={budget_version_id}",
    )
    if status != 200 or not isinstance(body, dict):
        return None
    rows = body.get("budget_plan_items", [])
    if not isinstance(rows, list):
        return None
    ids: set[str] = set()
    for row in rows:
        if isinstance(row, dict) and row.get("budget_item_id") is not None:
            ids.add(str(row["budget_item_id"]))
    return ids


def _lookup_budget_item_for_diagnosis(
    api: ApiClient,
    item_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """
    GET one budget item for override diagnosis.

    :param api: API client
    :param item_id: Budget item UUID
    :return: ``(\"ok\", item)``, ``(\"unknown\", None)``, or ``(\"failed\", None)``
    """
    status, body = api.request("GET", f"/api/v1/budget/items/{item_id}")
    if status == 200 and isinstance(body, dict):
        return "ok", body
    if status == 404:
        return "unknown", None
    return "failed", None


def _diagnose_override_candidates(
    api: ApiClient,
    version_item_ids: set[str],
    candidate_ids: list[str],
) -> list[_OverrideDiagnosisFinding] | None:
    """
    Classify candidate budget items for override 422 enrichment.

    :param api: API client
    :param version_item_ids: Budget item ids with plan-items in the version
    :param candidate_ids: Ordered candidate budget item ids
    :return: Findings, empty list, or ``None`` on diagnostic API failure
    """
    findings: list[_OverrideDiagnosisFinding] = []
    for index, item_id in enumerate(candidate_ids):
        lookup_kind, item = _lookup_budget_item_for_diagnosis(api, item_id)
        if lookup_kind == "failed":
            return None
        if lookup_kind == "unknown":
            findings.append(
                _OverrideDiagnosisFinding(
                    1,
                    index,
                    "unknown_budget_item",
                    item_id,
                ),
            )
        elif item is not None:
            item_status = str(item.get("status", "")).strip()
            name = str(item.get("name", item_id))
            if item_status != "ACT":
                findings.append(
                    _OverrideDiagnosisFinding(
                        2,
                        index,
                        "inactive_budget_item",
                        item_id,
                        name=name,
                        status=item_status,
                    ),
                )
            elif item_id not in version_item_ids:
                findings.append(
                    _OverrideDiagnosisFinding(
                        3,
                        index,
                        "missing_plan_item_in_version",
                        item_id,
                        name=name,
                        planning_type=_budget_item_planning_type(item),
                    ),
                )
        if findings:
            best_rank = min(finding.priority_rank for finding in findings)
            if best_rank == 1:
                break
    return findings


def _format_override_diagnosis_error(
    finding: _OverrideDiagnosisFinding,
    *,
    budget_version_id: str,
    period: Period,
) -> str:
    """
    Build Russian tool error text for one override diagnosis finding.

    :param finding: Selected finding
    :param budget_version_id: ACT version UUID
    :param period: Override month
    :return: Enriched error message
    """
    if finding.kind == "unknown_budget_item":
        return f"budget_item не найден: budget_item_id={finding.budget_item_id}"
    if finding.kind == "inactive_budget_item":
        return (
            f"budget_item «{finding.name}» не ACTIVE "
            f"(budget_item_id={finding.budget_item_id}, status={finding.status})"
        )
    lines = [
        (
            f"Статья «{finding.name}» "
            f"(budget_item_id={finding.budget_item_id}, "
            f"planning_type={finding.planning_type}) "
            f"не имеет plan-item в ACT-версии {budget_version_id}."
        ),
        "",
        "Создайте plan-item через create_plan_item и повторите override.",
    ]
    if finding.planning_type == "IRR":
        lines.extend(
            [
                "",
                "Пример (IRR):",
                (
                    f'  create_plan_item(budget_item_id="{finding.budget_item_id}", '
                    f'amount=0, planning_type="IRR", forecast_method="MAN")'
                ),
            ],
        )
    elif finding.planning_type == "REG":
        lines.extend(
            [
                "",
                "Пример (REG):",
                (
                    f'  create_plan_item(budget_item_id="{finding.budget_item_id}", '
                    f'amount=0, start_period="{period.yyyy_mm}")'
                ),
            ],
        )
    return "\n".join(lines)


def diagnose_put_reconciliation_budget_item_error(
    api: ApiClient,
    budget_version_id: str,
    period: Period,
    overrides_arg: dict[str, str],
    current_map: dict[str, str],
) -> str | None:
    """
    Diagnose budget-item override 422 and return an enriched ops message (FIN-120).

    :param api: API client
    :param budget_version_id: ACT version UUID
    :param period: Target month
    :param overrides_arg: Map from tool argument
    :param current_map: Full map sent to PUT
    :return: Enriched message, or ``None`` to fall back to raw PUT error
    """
    version_item_ids = _fetch_plan_item_budget_ids(api, budget_version_id)
    if version_item_ids is None:
        return None

    arg_ids, merged_extra = _override_budget_item_candidate_ids(overrides_arg, current_map)
    for candidate_ids in (arg_ids, merged_extra):
        if not candidate_ids:
            continue
        findings = _diagnose_override_candidates(api, version_item_ids, candidate_ids)
        if findings is None:
            return None
        if findings:
            best = min(findings, key=lambda row: (row.priority_rank, row.candidate_index))
            return _format_override_diagnosis_error(
                best,
                budget_version_id=budget_version_id,
                period=period,
            )
    return None


def put_transaction_overrides(
    api: ApiClient,
    budget_version_id: str,
    period: Period,
    overrides: dict[str, str],
    *,
    merge: bool = True,
) -> dict[str, Any]:
    """
    PUT reconciliation transaction overrides for one month.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Target month
    :param overrides: ``transaction_key`` → ``budget_item_id``
    :param merge: Merge with existing overrides when true
    :return: Updated reconciliation body
    """
    existing = fetch_reconciliation_full(api, budget_version_id, period)
    current = dict(existing.get("transaction_overrides") or {})
    if merge:
        current.update(overrides)
    else:
        current = dict(overrides)
    status, body = api.request(
        "PUT",
        "/api/v1/budget/reconciliation",
        data={
            "budget_version_id": budget_version_id,
            "period": period.month_start,
            "transaction_overrides": current,
        },
    )
    if status != 200:
        if status == 422 and is_budget_item_validation_failure(body):
            enriched = diagnose_put_reconciliation_budget_item_error(
                api,
                budget_version_id,
                period,
                overrides,
                current,
            )
            if enriched is not None:
                raise RuntimeError(enriched)
        raise RuntimeError(f"PUT reconciliation -> {status}: {body}")
    if not isinstance(body, dict):
        raise RuntimeError(f"PUT reconciliation unexpected body: {body!r}")
    return body


def upsert_expense_project(api: ApiClient, project: dict[str, Any]) -> dict[str, Any]:
    """
    Create or replace one expense project (``projects.json`` via API).

    :param api: API client
    :param project: Project body with ``id``, ``description``, ``keywords``, dates
    :return: Project record from API
    """
    project_id = str(project["id"])
    existing = {p["id"] for p in api.get_json("/api/v1/projects").get("projects", [])}
    if project_id in existing:
        status, body = api.request("PUT", f"/api/v1/projects/{project_id}", data=project)
        action = "updated"
    else:
        status, body = api.request("POST", "/api/v1/projects", data=project)
        action = "created"
    if status not in (200, 201):
        raise RuntimeError(f"upsert project {project_id} -> {status}: {body}")
    if not isinstance(body, dict):
        raise RuntimeError(f"upsert project unexpected body: {body!r}")
    return {"action": action, "project": body}


def reconciliation_status(api: ApiClient, budget_version_id: str, period: Period) -> str:
    """
    Return reconciliation status for a calendar month.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Target month
    :return: ``open``, ``closed``, ``draft``, etc.
    """
    return fetch_reconciliation(api, budget_version_id, period)["status"]


def _methodology_row_fields(reconciliation: dict[str, Any]) -> dict[str, Any]:
    """
    Extract methodology fields from a reconciliation payload (passthrough).

    :param reconciliation: Result from :func:`fetch_reconciliation`
    :return: ``methodology_status`` and ``close_phase`` keys
    """
    return {
        "methodology_status": reconciliation.get("methodology_status"),
        "close_phase": reconciliation.get("close_phase"),
    }


def reopen_period(
    api: ApiClient,
    budget_version_id: str,
    period: Period,
) -> tuple[int, dict | str | bytes]:
    """
    POST reconciliation reopen for one month.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Month to reopen
    :return: HTTP status and response body
    """
    return api.request(
        "POST",
        "/api/v1/budget/reconciliation/reopen",
        data={"budget_version_id": budget_version_id, "period": period.month_start},
    )


def reopen_closed_periods(
    api: ApiClient,
    budget_version_id: str,
    periods: list[Period],
) -> list[dict[str, Any]]:
    """
    Reopen each period that is currently ``closed``.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param periods: Months to consider
    :return: Log entries per reopen attempt
    """
    log: list[dict[str, Any]] = []
    for p in periods:
        status_before = reconciliation_status(api, budget_version_id, p)
        if status_before != "closed":
            log.append(
                {
                    "period": p.yyyy_mm,
                    "action": "skipped",
                    "status_before": status_before,
                }
            )
            continue
        http_status, body = reopen_period(api, budget_version_id, p)
        print(f"reopen {p.yyyy_mm}: {http_status}")
        log.append(
            {
                "period": p.yyyy_mm,
                "action": "reopened",
                "status_before": status_before,
                "http_status": http_status,
                "body": body if isinstance(body, dict) else str(body),
            }
        )
    return log


def import_log_entry(
    provider: str,
    status: int,
    body: dict | bytes | str,
    files: list[Path],
) -> dict[str, Any]:
    """
    Build import log record; on 422 persist full error body.

    :param provider: Provider id
    :param status: HTTP status
    :param body: Response body
    :param files: Uploaded files
    :return: Log dict for ``imports[]``
    """
    entry: dict[str, Any] = {
        "provider": provider,
        "status": status,
        "files": [str(fp) for fp in files],
    }
    if isinstance(body, dict):
        entry["body"] = body
        if status == 422:
            error = body.get("error") or {}
            details = error.get("details") or {}
            if isinstance(details, dict) and "blocked_accounting_periods" in details:
                entry["blocked_accounting_periods"] = details["blocked_accounting_periods"]
        brief = {
            k: body[k]
            for k in (
                "success",
                "rows_written",
                "derivation",
                "partial",
                "warnings",
                "stale",
            )
            if k in body
        }
        if brief:
            entry["brief"] = brief
    else:
        entry["body"] = str(body)
    return entry


def run_imports(
    api: ApiClient,
    period: Period,
    *,
    kanon: Path = KANON,
) -> list[dict[str, Any]]:
    """
    Import MC (one multipart), SEPA, C24 for the month.

    :param api: API client
    :param period: Target month
    :param kanon: Statements root
    :return: Import log entries
    """
    statements = resolve_statements(period, kanon)
    log: list[dict[str, Any]] = []
    for provider, _label in IMPORT_ORDER:
        raw = statements[provider]
        fps = [raw] if isinstance(raw, Path) else list(raw)
        file_providers = [(fp, provider) for fp in fps]
        status, body = api.request("POST", "/api/v1/import", files=file_providers)
        entry = import_log_entry(provider, status, body, fps)
        names = ", ".join(fp.name for fp in fps)
        print(f"import {provider}: {status} {entry.get('brief', entry.get('body'))} ({names})")
        if status == 422 and entry.get("blocked_accounting_periods"):
            print(
                f"  blocked_accounting_periods: {entry['blocked_accounting_periods']}",
                file=sys.stderr,
            )
        log.append(entry)
    return log


def day_of_month(date_display: str) -> int:
    """
    Parse day from API ``date_display``.

    :param date_display: ``DD.MM.YYYY`` or ``YYYY-MM-DD``
    :return: Day of month
    """
    if len(date_display) >= 10 and date_display[4] == "-":
        return int(date_display[8:10])
    parts = date_display.split(".")
    if len(parts) == 3:
        return int(parts[0])
    raise ValueError(f"unknown date format: {date_display!r}")


def mc_verify(api: ApiClient, period: Period) -> dict[str, Any]:
    """
    Quick MC checks: total count and ops from 17th (tail slice).

    :param api: API client
    :param period: Target month
    :return: MC verification metrics
    """
    body = api.get_json(
        f"/api/v1/transactions?period={period.ymmm}&provider=sparkasse_mastercard"
    )
    rows = body.get("rows") if isinstance(body.get("rows"), list) else []
    from_17 = [r for r in rows if day_of_month(str(r.get("date_display", "01.01.2000"))) >= 17]
    return {
        "mc_total": len(rows),
        "mc_from_17th": len(from_17),
        "from_17th_samples": [
            {"amount": r.get("amount"), "description": (r.get("description") or "")[:80]}
            for r in from_17[:5]
        ],
    }


def verify_period(
    api: ApiClient,
    period: Period,
    budget_version_id: str,
) -> dict[str, Any]:
    """
    Pre-close verification: MC tail, classification summary, readiness gates.

    :param api: API client
    :param period: Target month
    :param budget_version_id: Budget version UUID
    :return: Verification result with ``ok``, ``issues``, and ``warnings``
    """
    mc = mc_verify(api, period)
    summary = api.get_json(
        f"/api/v1/transactions/classification-summary?period={period.ymmm}"
    )
    readiness = api.get_json(
        f"/api/v1/budget/reconciliation/readiness?budget_version_id={budget_version_id}"
        f"&period={period.month_start}"
    )
    checks = {c["id"]: c for c in readiness.get("checks", [])}
    balances = checks.get("account_balances_reconciliation", {})
    t13 = checks.get("t13_income_expense", {})

    issues: list[str] = []
    warnings: list[str] = []
    if mc["mc_from_17th"] == 0:
        issues.append(
            "MC: нет операций с 17-го — проверь tail PDF в одном batch с head"
        )
    c9999_count = int(summary.get("expense_c9999_count") or 0)
    if c9999_count > 0:
        warnings.append(f"C9999: {c9999_count} расходов")
    pending_check = checks.get("unclassified_pending") or {}
    pending_count = int(
        (pending_check.get("details") or {}).get("unclassified_pending_count")
        or summary.get("unclassified_pending_count")
        or 0
    )
    if pending_check.get("status") == "warn" or pending_count > 0:
        warnings.append(f"pending: {pending_count} неклассифицировано")
    note_check = checks.get("other_without_note") or {}
    note_count = int(
        (note_check.get("details") or {}).get("count")
        or summary.get("other_without_note_count")
        or 0
    )
    if note_check.get("status") == "warn" or note_count > 0:
        warnings.append(f"Other без note: {note_count}")
    if balances.get("status") == "incomplete":
        issues.append("balances: incomplete — повтори import SEPA/MC пока period open")
    elif balances.get("status") != "pass":
        issues.append(f"balances: {balances.get('status')} — {balances.get('message', '')}")
    if t13.get("status") != "pass":
        issues.append(f"T13: {t13.get('status')} — {t13.get('message', '')}")
    if not readiness.get("ready"):
        blocking = [
            c["id"]
            for c in readiness.get("checks", [])
            if c.get("blocking") and c.get("status") != "pass"
        ]
        if blocking:
            issues.append(f"readiness blocking: {', '.join(blocking)}")

    result: dict[str, Any] = {
        "ok": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "mc": mc,
        "classification_summary": summary,
        "readiness": readiness,
    }
    return result


def filter_horizon_periods(
    periods: list[Period],
    *,
    year: int | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> list[Period]:
    """
    Restrict ACT horizon months to a calendar year or inclusive range.

    :param periods: Full horizon list (sorted)
    :param year: Filter to one calendar year
    :param period_from: Range start ``YYYY-MM`` or ``YYYYMM``
    :param period_to: Range end ``YYYY-MM`` or ``YYYYMM``
    :return: Filtered periods
    """
    if year is not None:
        return [p for p in periods if p.year == year]
    if period_from or period_to:
        start = parse_period(period_from) if period_from else periods[0]
        end = parse_period(period_to) if period_to else periods[-1]
        start_key = (start.year, start.month)
        end_key = (end.year, end.month)
        return [
            p
            for p in periods
            if start_key <= (p.year, p.month) <= end_key
        ]
    return periods


def _blocking_check_ids(readiness: dict[str, Any]) -> list[str]:
    """
    Return ids of blocking readiness checks that did not pass.

    :param readiness: Readiness payload from API
    :return: Check id list
    """
    return [
        str(c["id"])
        for c in readiness.get("checks", [])
        if c.get("blocking") and c.get("status") != "pass"
    ]


def compact_period_summary(
    period: Period,
    *,
    reconciliation: dict[str, Any],
    verify: dict[str, Any] | None = None,
    row_count: int | None = None,
) -> dict[str, Any]:
    """
    Build a compact month row for period status reports.

    :param period: Calendar month
    :param reconciliation: Reconciliation payload from :func:`fetch_reconciliation`
    :param verify: Optional full verify payload
    :param row_count: Row count when verify was skipped
    :return: Summary dict
    """
    base = {
        "period": period.yyyy_mm,
        "reconciliation_status": reconciliation["status"],
        **_methodology_row_fields(reconciliation),
    }
    if verify is None:
        count = int(row_count or 0)
        return {
            **base,
            "has_data": count > 0,
            "row_count": count,
        }

    summary = verify["classification_summary"]
    readiness = verify["readiness"]
    mc = verify["mc"]
    count = int(summary.get("row_count") or 0)
    return {
        **base,
        "has_data": count > 0 or int(mc.get("mc_total") or 0) > 0,
        "row_count": count,
        "ready": readiness.get("ready"),
        "verify_ok": verify.get("ok"),
        "c9999_count": int(summary.get("expense_c9999_count") or 0),
        "mc_total": int(mc.get("mc_total") or 0),
        "mc_from_17th": int(mc.get("mc_from_17th") or 0),
        "issues": list(verify.get("issues") or []),
        "blocking_checks": _blocking_check_ids(readiness),
    }


def period_status_report(
    api: ApiClient,
    budget_version_id: str,
    periods: list[Period],
    *,
    detail: str = "summary",
    skip_empty: bool = True,
) -> dict[str, Any]:
    """
    Build multi-month close status report (reconciliation + optional verify).

    :param api: Authenticated API client
    :param budget_version_id: Budget version UUID
    :param periods: Months to include (typically filtered ACT horizon)
    :param detail: ``status_only``, ``summary``, or ``full``
    :param skip_empty: Skip full verify when ``row_count`` is 0
    :return: Report payload with per-period rows and aggregates
    """
    if detail not in ("status_only", "summary", "full"):
        raise ValueError(
            f"detail must be status_only, summary, or full, got {detail!r}"
        )

    rows: list[dict[str, Any]] = []
    for period in periods:
        rec = fetch_reconciliation(api, budget_version_id, period)
        if detail == "status_only":
            rows.append(compact_period_summary(period, reconciliation=rec, row_count=0))
            continue

        summary_body = api.get_json(
            f"/api/v1/transactions/classification-summary?period={period.ymmm}"
        )
        row_count = int(summary_body.get("row_count") or 0)
        if skip_empty and row_count == 0:
            rows.append(
                compact_period_summary(
                    period,
                    reconciliation=rec,
                    row_count=0,
                )
            )
            continue

        verify = verify_period(api, period, budget_version_id)
        entry = compact_period_summary(
            period,
            reconciliation=rec,
            verify=verify,
        )
        if detail == "full":
            entry["verify"] = verify
        rows.append(entry)

    closed = [r["period"] for r in rows if r.get("reconciliation_status") == "closed"]
    preliminary_closed = [
        r["period"]
        for r in rows
        if r.get("methodology_status") == "preliminary_closed"
    ]
    final_closed = [
        r["period"]
        for r in rows
        if r.get("methodology_status") == "final_closed"
    ]
    with_data = [r for r in rows if r.get("has_data")]
    ready = [r["period"] for r in rows if r.get("ready") is True]
    verify_ok = [r["period"] for r in rows if r.get("verify_ok") is True]
    blocked = [
        r["period"]
        for r in rows
        if r.get("has_data") and r.get("ready") is False
    ]
    needs_attention = [
        r["period"]
        for r in rows
        if r.get("has_data") and not r.get("verify_ok", True)
    ]

    return {
        "detail": detail,
        "skip_empty": skip_empty,
        "period_count": len(rows),
        "closed_count": len(closed),
        "closed_periods": closed,
        "preliminary_closed_count": len(preliminary_closed),
        "preliminary_closed_periods": preliminary_closed,
        "final_closed_count": len(final_closed),
        "final_closed_periods": final_closed,
        "periods_with_data": len(with_data),
        "ready_count": len(ready),
        "verify_ok_count": len(verify_ok),
        "blocked_periods": blocked,
        "needs_attention": needs_attention,
        "periods": rows,
    }


def print_verify_report(verify: dict[str, Any], period: Period) -> None:
    """
    Print human-readable verification summary.

    :param verify: Result from :func:`verify_period`
    :param period: Target month
    """
    mc = verify["mc"]
    summary = verify["classification_summary"]
    readiness = verify["readiness"]
    print(f"\n--- verify {period.yyyy_mm} ---")
    print(f"MC: total={mc['mc_total']}, from_17th={mc['mc_from_17th']}")
    print(
        f"classification: row_count={summary.get('row_count')}, "
        f"C9999={summary.get('expense_c9999_count')}"
    )
    print(f"readiness ready: {readiness.get('ready')}")
    for check in readiness.get("checks", []):
        print(f"  {check['id']}: {check['status']}")
    if verify["issues"]:
        print("issues:")
        for issue in verify["issues"]:
            print(f"  - {issue}")
    warnings = verify.get("warnings") or []
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    if verify["ok"]:
        print("verify: OK")
    elif not verify["issues"]:
        print("verify: issues empty but ok=false")


def c9999_rows(api: ApiClient, period: Period) -> list[dict]:
    """
    List expense C9999 rows for the month.

    :param api: API client
    :param period: Target month
    :return: Transaction rows
    """
    body = api.get_json(
        f"/api/v1/transactions?period={period.ymmm}"
        "&transaction_category=C9999&transaction_type=C"
    )
    rows = body.get("rows")
    return rows if isinstance(rows, list) else []


UNPARSEABLE_DATE_SORT_KEY = "9999-12-31"


def parse_transaction_date_sort_key(date_display: str) -> tuple[str, bool]:
    """
    Parse display date to ISO sort key ``YYYY-MM-DD``.

    :param date_display: ``DD.MM.YYYY`` or ``YYYY-MM-DD``
    :return: Sort key and whether parsing succeeded
    """
    text = (date_display or "").strip()
    if not text:
        return UNPARSEABLE_DATE_SORT_KEY, False
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10], True
    parts = text.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        day, month, year = parts
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}", True
    return UNPARSEABLE_DATE_SORT_KEY, False


def parse_c9999_amount(raw: Any) -> Decimal:
    """
    Parse transaction amount as absolute ``Decimal``.

    :param raw: Amount from API row
    :return: Non-negative decimal
    :raises ValueError: When value is not numeric
    """
    from decimal import InvalidOperation

    if raw is None:
        return Decimal("0")
    text = str(raw).strip().replace(",", ".")
    if not text:
        return Decimal("0")
    try:
        return abs(Decimal(text))
    except InvalidOperation as exc:
        raise ValueError(f"amount must be numeric, got {raw!r}") from exc


def decimal_amount_to_json_number(amount: Decimal) -> float:
    """
    Serialize money for MCP JSON responses (two fractional digits).

    :param amount: Decimal amount
    :return: JSON number
    """
    return float(amount.quantize(Decimal("0.01")))


def normalize_c9999_rows(raw_rows: list[dict]) -> tuple[list[dict[str, Any]], list[str], Decimal]:
    """
    Normalize, sort, and aggregate C9999 list rows (FIN-17).

    :param raw_rows: Rows from :func:`c9999_rows`
    :return: Normalized rows, warnings, total amount
    """
    warnings: list[str] = []
    enriched: list[tuple[str, str, dict[str, Any], Decimal]] = []

    for raw in raw_rows:
        row_id = str(raw.get("id") or "")
        date_display = str(raw.get("date_display") or "")
        sort_key, date_ok = parse_transaction_date_sort_key(date_display)
        if not date_ok:
            warnings.append(f"unparseable_date:id={row_id}:date_display={date_display}")
        try:
            amount_dec = parse_c9999_amount(raw.get("amount"))
        except ValueError:
            amount_dec = Decimal("0")
            warnings.append(f"unparseable_amount:id={row_id}:amount={raw.get('amount')!r}")
        enriched.append(
            (
                sort_key,
                str(raw.get("description") or "").casefold(),
                {
                    "id": row_id,
                    "date": date_display,
                    "amount": decimal_amount_to_json_number(amount_dec),
                    "description": str(raw.get("description") or ""),
                    "provider": str(raw.get("provider") or ""),
                    "project": str(raw.get("project") or ""),
                    "suggestions": [],
                },
                amount_dec,
            )
        )

    enriched.sort(key=lambda item: (item[0], item[1]))
    rows = [item[2] for item in enriched]
    total = sum((item[3] for item in enriched), Decimal("0"))
    return rows, warnings, total


def list_c9999_payload(api: ApiClient, period: Period) -> dict[str, Any]:
    """
    Build ``list_c9999`` tool payload for one accounting month.

    :param api: API client
    :param period: Target month
    :return: Rows, counts, warnings (without profile/base/ok)
    """
    raw_rows = c9999_rows(api, period)
    rows, warnings, total = normalize_c9999_rows(raw_rows)
    return {
        "period": period.yyyy_mm,
        "row_count": len(rows),
        "total_amount_eur": decimal_amount_to_json_number(total),
        "warnings": warnings,
        "rows": rows,
    }


def print_c9999_proposal(rows: list[dict]) -> None:
    """
    Print C9999 table for chat review (c9999-proposal-policy).

    :param rows: C9999 transaction rows
    """
    print("\n--- C9999: предложение по разнесению ---")
    print("| EUR | Описание |")
    print("| --- | --- |")
    total = 0.0
    for row in rows:
        amount = row.get("amount") or 0
        try:
            total += float(amount)
        except (TypeError, ValueError):
            pass
        desc = (row.get("description") or "")[:90]
        print(f"| {amount} | {desc} |")
    print(f"\nИтого: {len(rows)} строк, ~{total:.2f} EUR")
    print("Подтверди категории/keywords, затем --apply-keywords <file.json>")


def _merge_keyword_list(
    existing: list[str],
    adds: list[str],
    removes: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Apply add then remove ops with exact keyword matching (FIN-16).

    :param existing: Current keyword list from API
    :param adds: Keywords to add
    :param removes: Keywords to remove
    :return: Tuple of new list, added journal, removed journal
    """
    current = list(existing)
    present = set(current)
    added_journal: list[str] = []
    removed_journal: list[str] = []
    for kw in adds:
        if kw not in present:
            current.append(kw)
            present.add(kw)
            added_journal.append(kw)
    for kw in removes:
        if kw in present:
            current.remove(kw)
            present.discard(kw)
            removed_journal.append(kw)
    return current, added_journal, removed_journal


def _resolve_budget_item(
    key: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id = {str(item["id"]): item for item in items}
    if key in by_id:
        return by_id[key]
    normalized = key.strip().casefold()
    matches = [
        item
        for item in items
        if str(item.get("name", "")).strip().casefold() == normalized
    ]
    if not matches:
        raise ApplyKeywordsValidationError(f"unknown budget item {key!r}")
    if len(matches) > 1:
        names = [str(m.get("name", "")) for m in matches]
        raise ApplyKeywordsValidationError(
            f"ambiguous budget item {key!r}: candidates={names}"
        )
    return matches[0]


def _apply_category_keywords(
    api: ApiClient,
    parsed: _ParsedKeywordsPayload,
    changes: dict[str, list[dict[str, Any]]],
) -> None:
    if not parsed.categories:
        return
    categories = api.get_json("/api/v1/categories")["categories"]
    by_id = {str(c["id"]): c for c in categories}
    categories_changed = False
    for cat_id, adds, removes in parsed.categories:
        if cat_id not in by_id:
            raise ApplyKeywordsValidationError(f"unknown category {cat_id!r}")
        existing = list(by_id[cat_id].get("keywords") or [])
        new_list, added, removed = _merge_keyword_list(existing, adds, removes)
        if added or removed:
            by_id[cat_id]["keywords"] = new_list
            categories_changed = True
            for kw in added:
                changes["categories_added"].append({"category": cat_id, "keyword": kw})
            for kw in removed:
                changes["categories_removed"].append({"category": cat_id, "keyword": kw})
    if not categories_changed:
        return
    status, body = api.request(
        "PUT",
        "/api/v1/categories",
        data={"categories": categories},
    )
    if status != 200:
        raise ApplyKeywordsPartialError(
            f"PUT categories -> {status}: {body}",
            partial_changes=changes,
        )


def _apply_budget_item_keywords(
    api: ApiClient,
    parsed: _ParsedKeywordsPayload,
    changes: dict[str, list[dict[str, Any]]],
) -> None:
    if not parsed.budget_items:
        return
    catalog = api.get_json("/api/v1/budget/items").get("budget_items", [])
    merged: dict[str, tuple[dict[str, Any], list[str], list[str]]] = {}
    for key, adds, removes in parsed.budget_items:
        item = _resolve_budget_item(key, catalog)
        item_id = str(item["id"])
        if item_id not in merged:
            merged[item_id] = (item, [], [])
        _item, acc_adds, acc_removes = merged[item_id]
        acc_adds.extend(adds)
        acc_removes.extend(removes)
        merged[item_id] = (_item, acc_adds, acc_removes)

    for item_id, (item, adds, removes) in merged.items():
        existing = list(item.get("keywords") or [])
        norm_adds, norm_removes = _normalize_keyword_ops(adds, removes)
        new_list, added, removed = _merge_keyword_list(existing, norm_adds, norm_removes)
        if not added and not removed:
            continue
        body = dict(item)
        body["keywords"] = new_list
        status, resp = api.request("PUT", f"/api/v1/budget/items/{item_id}", data=body)
        if status != 200:
            raise ApplyKeywordsPartialError(
                f"PUT budget/items/{item_id} -> {status}: {resp}",
                partial_changes=changes,
            )
        name = str(item.get("name", ""))
        for kw in added:
            changes["budget_items_added"].append(
                {"budget_item_id": item_id, "name": name, "keyword": kw}
            )
        for kw in removed:
            changes["budget_items_removed"].append(
                {"budget_item_id": item_id, "name": name, "keyword": kw}
            )


def _apply_project_keywords(
    api: ApiClient,
    parsed: _ParsedKeywordsPayload,
    changes: dict[str, list[dict[str, Any]]],
) -> None:
    if not parsed.projects:
        return
    projects = api.get_json("/api/v1/projects").get("projects", [])
    by_id = {str(p["id"]): p for p in projects}
    merged: dict[str, tuple[dict[str, Any], list[str], list[str]]] = {}
    for proj_id, adds, removes in parsed.projects:
        if proj_id not in by_id:
            raise ApplyKeywordsValidationError(f"unknown project {proj_id!r}")
        if proj_id not in merged:
            merged[proj_id] = (by_id[proj_id], [], [])
        proj, acc_adds, acc_removes = merged[proj_id]
        acc_adds.extend(adds)
        acc_removes.extend(removes)
        merged[proj_id] = (proj, acc_adds, acc_removes)

    for proj_id, (project, adds, removes) in merged.items():
        existing = list(project.get("keywords") or [])
        norm_adds, norm_removes = _normalize_keyword_ops(adds, removes)
        new_list, added, removed = _merge_keyword_list(existing, norm_adds, norm_removes)
        if not added and not removed:
            continue
        body = dict(project)
        body["keywords"] = new_list
        status, resp = api.request("PUT", f"/api/v1/projects/{proj_id}", data=body)
        if status != 200:
            raise ApplyKeywordsPartialError(
                f"PUT projects/{proj_id} -> {status}: {resp}",
                partial_changes=changes,
            )
        for kw in added:
            changes["projects_added"].append({"project": proj_id, "keyword": kw})
        for kw in removed:
            changes["projects_removed"].append({"project": proj_id, "keyword": kw})


def apply_keywords_payload(api: ApiClient, payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """
    Apply keywords from unified or legacy JSON payload (FIN-16).

    :param api: API client
    :param payload: Parsed keywords object
    :return: Change journal with real persisted deltas
    :raises ApplyKeywordsValidationError: Invalid payload or unknown entity
    :raises ApplyKeywordsPartialError: HTTP failure after partial apply
    """
    parsed = parse_keywords_payload(payload)
    changes = empty_keywords_changes()
    _apply_category_keywords(api, parsed, changes)
    _apply_budget_item_keywords(api, parsed, changes)
    _apply_project_keywords(api, parsed, changes)
    return changes


def apply_keywords_file(api: ApiClient, path: Path) -> dict[str, list[dict[str, Any]]]:
    """
    Load keywords JSON from disk and apply via :func:`apply_keywords_payload`.

    :param api: API client
    :param path: Path to keywords JSON file
    :return: Change journal
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ApplyKeywordsValidationError("payload root must be an object")
    return apply_keywords_payload(api, raw)


def run_derive(api: ApiClient, period: Period) -> dict | str | bytes:
    """
    POST period-scope derive (fast path, BLG-031).

    :param api: API client
    :param period: Target calendar month
    :return: Derive response body
    """
    _, derive = api.request(
        "POST",
        "/api/v1/transactions/derive",
        data={"scope": "period", "accounting_period": period.ymmm},
    )
    print("derive:", derive)
    return derive


def generate_reports(
    api: ApiClient,
    period: Period,
    out_dir: Path,
    log: dict,
) -> None:
    """
    Generate all report PDFs into ``out_dir``.

    :param api: API client
    :param period: Target month
    :param out_dir: Output directory
    :param log: Mutable log dict
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    reports = api.get_json("/api/v1/reports")["reports"]
    projects = api.get_json("/api/v1/projects").get("projects", [])
    active = [p for p in projects if p.get("status") != "inactive"]
    proj_id = active[0]["id"] if active else None
    log["reports"] = {}
    for rep in reports:
        slug = rep["name"]
        body: dict = {"report_name": slug, "period": period.ymmm}
        if slug == "project_expense" and proj_id:
            body["parameters"] = {"project_id": proj_id}
        pdf_status, pdf = api.request(
            "POST",
            "/api/v1/reports/generate?disposition=attachment",
            data=body,
        )
        pdf_path = out_dir / f"{slug}.pdf"
        if pdf_status == 200 and isinstance(pdf, bytes):
            pdf_path.write_bytes(pdf)
            print(f"pdf {slug} OK")
        else:
            print(f"pdf {slug} FAIL {pdf_status}")
        log["reports"][slug] = str(pdf_path)


def close_period(
    api: ApiClient,
    budget_version_id: str,
    period: Period,
    *,
    close_phase: str = "final",
) -> tuple[int, dict | str | bytes]:
    """
    POST reconciliation close with explicit phase.

    :param api: API client
    :param budget_version_id: Budget version UUID
    :param period: Target month
    :param close_phase: ``preliminary`` or ``final``
    :return: HTTP status and body
    """
    if close_phase not in CLOSE_PHASES:
        raise ValueError(f"close_phase must be one of {CLOSE_PHASES}, got {close_phase!r}")
    return api.request(
        "POST",
        "/api/v1/budget/reconciliation/close",
        data={
            "budget_version_id": budget_version_id,
            "period": period.month_start,
            "close_phase": close_phase,
        },
    )


_PLAN_ITEM_PUT_KEYS = frozenset(
    {
        "id",
        "budget_version_id",
        "budget_item_id",
        "planning_type",
        "amount",
        "currency",
        "status",
        "periodicity",
        "start_date",
        "end_date",
        "forecast_method",
    },
)


def period_from_start_date(start_date: str) -> Period:
    """
    Derive budget month from plan-item ``start_date`` (ISO date).

    :param start_date: ``YYYY-MM-DD`` or longer ISO prefix
    :return: Parsed period
    :raises ValueError: When date prefix is invalid
    """
    return parse_period(start_date[:7])


def plan_item_put_body(
    plan_item: dict[str, Any],
    amount: str,
    *,
    start_period: Period | None = None,
    end_period: Period | None = None,
) -> dict[str, Any]:
    """
    Build PUT body from a plan item, dropping projection-page enrichments.

    :param plan_item: Source row (GET plan-item or projection-period-page)
    :param amount: Normalized amount string
    :param start_period: Optional new REG start month (FIN-110)
    :param end_period: Optional new REG end month (FIN-110)
    :return: Body accepted by ``PUT /budget/plan-items/{id}``
    """
    body = {k: plan_item[k] for k in _PLAN_ITEM_PUT_KEYS if k in plan_item}
    body["amount"] = amount
    body["id"] = str(plan_item["id"])
    if start_period is not None:
        body["start_date"] = start_period.month_start
    if end_period is not None:
        body["end_date"] = period_last_day(end_period)
    return body


def validate_plan_item_period_update(
    plan_item: dict[str, Any],
    *,
    start_period: Period | None,
    end_period: Period | None,
) -> None:
    """
    Validate start/end month changes before PUT (FIN-110 D-10).

    :param plan_item: Resolved plan-item body (GET or projection-page)
    :param start_period: Optional new start month
    :param end_period: Optional new end month
    :raises ValueError: When end month precedes effective start month
    """
    if start_period is None and end_period is None:
        return
    if start_period is not None and end_period is not None:
        assert_period_range(start_period, end_period)
        return
    if end_period is not None:
        existing_start = str(plan_item.get("start_date", ""))
        if not existing_start:
            raise ValueError("plan item has no start_date for end_period validation")
        effective_start = period_from_start_date(existing_start)
        assert_period_range(effective_start, end_period)


class UpdatePlanItemRecalculateError(RuntimeError):
    """
    Recalculate failed after successful plan-item PUT (FIN-108 D-13).

    :param message: Error text
    :param context: Successful PUT fields for ops retry
    """

    def __init__(self, message: str, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


def normalize_plan_amount(raw: Any) -> str:
    """
    Normalize plan amount to a non-negative decimal string.

    :param raw: Amount from tool args
    :return: Decimal string (two fractional digits)
    :raises ValueError: When missing, invalid, or negative
    """
    if raw is None:
        raise ValueError("amount is required")
    from decimal import Decimal, InvalidOperation

    try:
        if isinstance(raw, str):
            amt = Decimal(raw.strip())
        else:
            amt = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"amount must be a decimal number, got {raw!r}") from exc
    if amt < 0:
        raise ValueError(f"amount must be non-negative, got {amt}")
    return format(amt.quantize(Decimal("0.01")), "f")


def resolve_act_version_id(api: ApiClient) -> str:
    """
    Return the single ACT budget version id.

    :param api: API client
    :return: Version UUID
    :raises RuntimeError: When ACT count is not exactly one
    """
    data = api.get_json("/api/v1/budget/versions")
    act = [v for v in data.get("budget_versions", []) if v.get("status") == "ACT"]
    if len(act) != 1:
        raise RuntimeError(f"expected exactly one ACT budget version, found {len(act)}")
    return str(act[0]["id"])


def fetch_budget_version(api: ApiClient, version_id: str) -> dict[str, Any]:
    """
    Load one budget version.

    :param api: API client
    :param version_id: Version UUID
    :return: Version body
    """
    return api.get_json(f"/api/v1/budget/versions/{version_id}")


def assert_version_mutable(
    *,
    version: dict[str, Any] | None = None,
    can_mutate: bool | None = None,
) -> None:
    """
    Reject mutation when version is ARC or ``can_mutate`` is false (FIN-108 D-09b).

    Either signal is sufficient; they are checked independently.

    :param version: Optional version dict from GET
    :param can_mutate: Optional flag from projection-period-page
    :raises RuntimeError: When version cannot be mutated
    """
    if version is not None and version.get("status") == "ARC":
        raise RuntimeError(
            f"budget version {version.get('id')} has status ARC (immutable)",
        )
    if can_mutate is False:
        raise RuntimeError("budget version is not mutable (can_mutate=false)")


def _budget_item_planning_type(item: dict[str, Any]) -> str:
    """
    Read ``planning_type`` from a budget item API row.

    :param item: Budget item dict
    :return: ``REG`` or ``IRR``
    """
    return str(item.get("planning_type", "REG")).strip().upper()


def resolve_budget_item_id_for_plan(
    api: ApiClient,
    article: str | None,
    budget_item_id: str | None,
) -> tuple[str, str, str]:
    """
    Resolve article label to budget item id (same rules as ``query_plan_fact``).

    :param api: API client
    :param article: Substring of article name
    :param budget_item_id: Explicit UUID
    :return: Tuple of item id, display name, and ``planning_type``
    :raises RuntimeError: When ``article`` and ``budget_item_id`` resolve to different ids
    """
    if not article and not budget_item_id:
        raise ValueError("article or budget_item_id required for resolve")

    if budget_item_id and article:
        item_by_id = api.get_json(f"/api/v1/budget/items/{budget_item_id}")
        data = api.get_json("/api/v1/budget/items")
        needle = article.casefold()
        matches = [
            row
            for row in data.get("budget_items", [])
            if needle in str(row.get("name", "")).casefold()
        ]
        if not matches:
            raise RuntimeError(f"budget item not found for article {article!r}")
        if len(matches) > 1:
            names = ", ".join(str(m.get("name")) for m in matches)
            raise RuntimeError(f"ambiguous article {article!r}: {names}")
        id_from_article = str(matches[0]["id"])
        if budget_item_id != id_from_article:
            raise RuntimeError(
                f"article {article!r} and budget_item_id {budget_item_id!r} "
                "resolve to different budget items",
            )
        return (
            budget_item_id,
            str(item_by_id.get("name", budget_item_id)),
            _budget_item_planning_type(item_by_id),
        )

    if budget_item_id:
        item = api.get_json(f"/api/v1/budget/items/{budget_item_id}")
        return (
            budget_item_id,
            str(item.get("name", budget_item_id)),
            _budget_item_planning_type(item),
        )

    assert article is not None
    data = api.get_json("/api/v1/budget/items")
    needle = article.casefold()
    matches = [
        item
        for item in data.get("budget_items", [])
        if needle in str(item.get("name", "")).casefold()
    ]
    if not matches:
        raise RuntimeError(f"budget item not found for article {article!r}")
    if len(matches) > 1:
        names = ", ".join(str(m.get("name")) for m in matches)
        raise RuntimeError(f"ambiguous article {article!r}: {names}")
    item = matches[0]
    return str(item["id"]), str(item["name"]), _budget_item_planning_type(item)


def resolve_plan_item_for_update(
    api: ApiClient,
    *,
    plan_item_id: str | None,
    period: Period | None,
    article: str | None,
    budget_item_id: str | None,
) -> tuple[dict[str, Any], str]:
    """
    Resolve one plan item and article name for update (FIN-108 D-11).

    :param api: API client
    :param plan_item_id: Direct plan-item UUID (takes precedence)
    :param period: Month for article resolve
    :param article: Article substring
    :param budget_item_id: Article UUID
    :return: Plan item dict and article display name
    """
    if plan_item_id:
        plan_item = api.get_json(f"/api/v1/budget/plan-items/{plan_item_id}")
        item_id = str(plan_item["budget_item_id"])
        item = api.get_json(f"/api/v1/budget/items/{item_id}")
        return plan_item, str(item.get("name", item_id))

    if period is None or (not article and not budget_item_id):
        raise ValueError(
            "provide plan_item_id or (period and article or budget_item_id)",
        )

    item_id, article_name, _ = resolve_budget_item_id_for_plan(api, article, budget_item_id)
    act_vid = resolve_act_version_id(api)
    query = (
        f"/api/v1/budget/projection-period-page"
        f"?budget_version_id={act_vid}&period={period.month_start}"
    )
    page = api.get_json(query)
    assert_version_mutable(can_mutate=page.get("can_mutate"))
    matched = [
        row
        for row in page.get("plan_items", [])
        if str(row.get("budget_item_id")) == item_id
    ]
    if not matched:
        raise RuntimeError(
            f"no plan item for article {article_name!r} in period {period.yyyy_mm}",
        )
    if len(matched) > 1:
        ids = ", ".join(str(row.get("id")) for row in matched)
        raise RuntimeError(
            f"ambiguous plan items for {article_name!r} in {period.yyyy_mm}: {ids}",
        )
    return matched[0], article_name


def projection_rows_count(body: dict[str, Any]) -> int:
    """
    Extract recalculated projection row count from API response (FIN-108 D-15).

    :param body: POST ``/budget/projections/recalculate`` response
    :return: Row count
    """
    if "updated_count" in body:
        return int(body["updated_count"])
    rows = body.get("budget_projections")
    if rows is None:
        rows = body.get("projections")
    if not isinstance(rows, list):
        return 0
    return len(rows)


def recalculate_budget_projections(api: ApiClient, budget_version_id: str) -> dict[str, Any]:
    """
    Rebuild projections for one budget version.

    :param api: API client
    :param budget_version_id: Version UUID
    :return: Full recalculate response body
    """
    status, body = api.request(
        "POST",
        "/api/v1/budget/projections/recalculate",
        data={"budget_version_id": budget_version_id},
    )
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"POST projections/recalculate -> {status}: {body}")
    return body


def update_plan_item(
    api: ApiClient,
    amount: Any,
    *,
    plan_item_id: str | None = None,
    period: Period | None = None,
    article: str | None = None,
    budget_item_id: str | None = None,
    start_period: Period | None = None,
    end_period: Period | None = None,
    recalculate: bool = True,
) -> dict[str, Any]:
    """
    Update one plan item amount and/or bounded horizon; optional recalculate (FIN-108, FIN-110).

    :param api: API client
    :param amount: New plan amount (non-negative; zero allowed)
    :param plan_item_id: Plan-item UUID (precedence over resolve fields)
    :param period: Month for article resolve
    :param article: Article substring
    :param budget_item_id: Article UUID
    :param start_period: Optional new REG start month
    :param end_period: Optional new REG end month
    :param recalculate: Run projection recalculate after PUT
    :return: Tool result fields
    :raises UpdatePlanItemRecalculateError: PUT succeeded, recalculate failed
    """
    amount_after = normalize_plan_amount(amount)
    plan_item, article_name = resolve_plan_item_for_update(
        api,
        plan_item_id=plan_item_id,
        period=period,
        article=article,
        budget_item_id=budget_item_id,
    )
    validate_plan_item_period_update(
        plan_item,
        start_period=start_period,
        end_period=end_period,
    )
    version_id = str(plan_item["budget_version_id"])
    version = fetch_budget_version(api, version_id)
    assert_version_mutable(version=version)

    plan_id = str(plan_item["id"])
    amount_before = str(plan_item.get("amount", ""))
    put_body = plan_item_put_body(
        plan_item,
        amount_after,
        start_period=start_period,
        end_period=end_period,
    )

    status, updated = api.request(
        "PUT",
        f"/api/v1/budget/plan-items/{plan_id}",
        data=put_body,
    )
    if status != 200 or not isinstance(updated, dict):
        raise RuntimeError(f"PUT plan-items/{plan_id} -> {status}: {updated}")

    base_result: dict[str, Any] = {
        "plan_item_id": plan_id,
        "budget_version_id": version_id,
        "budget_item_id": str(plan_item["budget_item_id"]),
        "article": article_name,
        "amount_before": amount_before,
        "amount_after": amount_after,
        "plan_item": updated,
    }
    if start_period is not None:
        base_result["start_period"] = start_period.yyyy_mm
    if end_period is not None:
        base_result["end_period"] = end_period.yyyy_mm

    if not recalculate:
        return base_result

    try:
        recalc_body = recalculate_budget_projections(api, version_id)
    except RuntimeError as exc:
        raise UpdatePlanItemRecalculateError(str(exc), base_result) from exc

    base_result["recalculate"] = {
        "budget_version_id": version_id,
        "projection_rows": projection_rows_count(recalc_body),
    }
    return base_result


class CreateBudgetItemPlanItemError(RuntimeError):
    """
    Plan-item POST failed after successful budget item POST (FIN-109 D-04).

    :param message: Error text
    :param context: Successful items POST fields for ops recovery
    """

    def __init__(self, message: str, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


class CreateBudgetItemRecalculateError(RuntimeError):
    """
    Recalculate failed after successful budget item + plan-item POST (FIN-109 D-13).

    :param message: Error text
    :param context: Successful create fields for ops retry
    """

    def __init__(self, message: str, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


def assert_budget_item_name_available(
    api: ApiClient,
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    """
    Reject create/rename when an article with the same name already exists.

    Comparison uses ``strip()`` + ``casefold()`` on both sides; NFC/NFD not normalized.
    FIN-109 D-02 (create); FIN-227 (rename, with ``exclude_id`` = self).

    :param api: API client
    :param name: Proposed article name (already trimmed by caller)
    :param exclude_id: Budget item id to ignore (self on rename)
    :raises RuntimeError: When a case-insensitive exact match exists
    """
    needle = name.strip().casefold()
    data = api.get_json("/api/v1/budget/items")
    for item in data.get("budget_items", []):
        item_id = str(item.get("id", ""))
        if exclude_id is not None and item_id == exclude_id:
            continue
        if str(item.get("name", "")).strip().casefold() == needle:
            raise RuntimeError(f"budget item already exists: {item.get('name')!r}")


def build_reg_plan_item_body(
    *,
    budget_version_id: str,
    budget_item_id: str,
    amount: str,
    currency: str,
    start_period: Period,
    end_period: Period | None,
    periodicity: str,
) -> dict[str, Any]:
    """
    Build POST body for a REG plan item (FIN-109).

    :param budget_version_id: ACT version UUID
    :param budget_item_id: New article UUID
    :param amount: Normalized amount string
    :param currency: Currency code
    :param start_period: REG start month
    :param end_period: Optional REG end month (last day of month in ``end_date``)
    :param periodicity: REG periodicity code
    :return: Body for ``POST /budget/plan-items``
    """
    body: dict[str, Any] = {
        "budget_version_id": budget_version_id,
        "budget_item_id": budget_item_id,
        "planning_type": "REG",
        "amount": amount,
        "currency": currency,
        "status": "ACTIVE",
        "periodicity": periodicity,
        "start_date": start_period.month_start,
        "end_date": period_last_day(end_period) if end_period else None,
        "forecast_method": None,
    }
    return body


def build_irr_plan_item_body(
    *,
    budget_version_id: str,
    budget_item_id: str,
    amount: str,
    currency: str,
    forecast_method: str,
) -> dict[str, Any]:
    """
    Build POST body for an IRR plan item (FIN-119).

    :param budget_version_id: ACT version UUID
    :param budget_item_id: Article UUID
    :param amount: Normalized amount string
    :param currency: Currency code
    :param forecast_method: ``MAN`` or ``AVG``
    :return: Body for ``POST /budget/plan-items``
    """
    return {
        "budget_version_id": budget_version_id,
        "budget_item_id": budget_item_id,
        "planning_type": "IRR",
        "amount": amount,
        "currency": currency,
        "status": "ACTIVE",
        "periodicity": None,
        "start_date": None,
        "end_date": None,
        "forecast_method": forecast_method.strip().upper(),
    }


def create_budget_item(
    api: ApiClient,
    *,
    name: str,
    flow_type: str,
    operation_category_id: str,
    amount: Any,
    start_period: Period,
    planning_type: str = "REG",
    keywords: list[str] | None = None,
    item_status: str = "ACT",
    currency: str = "EUR",
    periodicity: str = "M",
    end_period: Period | None = None,
    recalculate: bool = True,
) -> dict[str, Any]:
    """
    Create budget item and REG plan-item in ACT version, optionally recalculate (FIN-109).

    :param api: API client
    :param name: Article name
    :param flow_type: ``EXP`` or ``INC``
    :param operation_category_id: Operation category code
    :param amount: REG plan amount (non-negative)
    :param start_period: First active month
    :param planning_type: Must be ``REG`` in v1
    :param keywords: Optional article keywords (empty list allowed)
    :param item_status: ``budget_item.status`` (default ACT)
    :param currency: Plan currency
    :param periodicity: REG periodicity (backend validates)
    :param end_period: Optional last active month
    :param recalculate: Run projection recalculate after POST plan-item
    :return: Tool result fields
    :raises CreateBudgetItemPlanItemError: Items POST OK, plan-items POST failed
    :raises CreateBudgetItemRecalculateError: POSTs succeeded, recalculate failed
    """
    if planning_type != "REG":
        raise ValueError(f"planning_type must be REG, got {planning_type!r}")

    trimmed_name = name.strip()
    if not trimmed_name:
        raise ValueError("name is required")
    if not flow_type.strip():
        raise ValueError("flow_type is required")
    if not operation_category_id.strip():
        raise ValueError("operation_category_id is required")
    if end_period is not None:
        assert_period_range(start_period, end_period)

    amount_norm = normalize_plan_amount(amount)
    version_id = resolve_act_version_id(api)
    version = fetch_budget_version(api, version_id)
    assert_version_mutable(version=version)
    assert_budget_item_name_available(api, trimmed_name)

    item_body: dict[str, Any] = {
        "name": trimmed_name,
        "flow_type": flow_type.strip(),
        "operation_category_id": operation_category_id.strip(),
        "planning_type": planning_type,
        "keywords": list(keywords or []),
        "status": item_status,
    }
    status_code, created_item = api.request("POST", "/api/v1/budget/items", data=item_body)
    if status_code != 201 or not isinstance(created_item, dict):
        raise RuntimeError(f"POST budget/items -> {status_code}: {created_item}")

    budget_item_id = str(created_item["id"])
    items_context: dict[str, Any] = {
        "budget_version_id": version_id,
        "budget_item_id": budget_item_id,
        "name": trimmed_name,
        "amount": amount_norm,
        "budget_item": created_item,
    }

    plan_body = build_reg_plan_item_body(
        budget_version_id=version_id,
        budget_item_id=budget_item_id,
        amount=amount_norm,
        currency=currency.strip().upper(),
        start_period=start_period,
        end_period=end_period,
        periodicity=periodicity,
    )
    plan_status, created_plan = api.request(
        "POST",
        "/api/v1/budget/plan-items",
        data=plan_body,
    )
    if plan_status != 201 or not isinstance(created_plan, dict):
        raise CreateBudgetItemPlanItemError(
            f"POST budget/plan-items -> {plan_status}: {created_plan}",
            items_context,
        )

    base_result: dict[str, Any] = {
        **items_context,
        "plan_item_id": str(created_plan["id"]),
        "start_period": start_period.yyyy_mm,
        "plan_item": created_plan,
    }
    if end_period is not None:
        base_result["end_period"] = end_period.yyyy_mm

    if not recalculate:
        return base_result

    try:
        recalc_body = recalculate_budget_projections(api, version_id)
    except RuntimeError as exc:
        raise CreateBudgetItemRecalculateError(str(exc), base_result) from exc

    base_result["recalculate"] = {
        "budget_version_id": version_id,
        "projection_rows": projection_rows_count(recalc_body),
    }
    return base_result


class CreatePlanItemRecalculateError(RuntimeError):
    """
    Recalculate failed after successful plan-item POST (FIN-110 D-07b).

    :param message: Error text
    :param context: Successful create fields for ops retry
    """

    def __init__(self, message: str, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


def create_plan_item(
    api: ApiClient,
    amount: Any,
    start_period: Period | None = None,
    *,
    article: str | None = None,
    budget_item_id: str | None = None,
    planning_type: str | None = None,
    forecast_method: str = "MAN",
    currency: str = "EUR",
    periodicity: str = "M",
    end_period: Period | None = None,
    recalculate: bool = True,
    provided_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """
    POST REG or IRR plan-item on an existing budget article in ACT (FIN-110, FIN-119).

    :param api: API client
    :param amount: Plan amount (non-negative; zero allowed)
    :param start_period: First active month (REG only)
    :param article: Article substring
    :param budget_item_id: Article UUID
    :param planning_type: Explicit ``REG`` or ``IRR``; omitted → infer from article
    :param forecast_method: IRR forecast method (default ``MAN``)
    :param currency: Plan currency
    :param periodicity: REG periodicity (backend validates)
    :param end_period: Optional last active month (REG only)
    :param recalculate: Run projection recalculate after POST plan-item
    :param provided_fields: Argument keys explicitly passed by caller
    :return: Tool result fields
    :raises CreatePlanItemRecalculateError: POST succeeded, recalculate failed
    """
    if not article and not budget_item_id:
        raise ValueError("article or budget_item_id is required")

    amount_norm = normalize_plan_amount(amount)
    item_id, article_name, article_planning_type = resolve_budget_item_id_for_plan(
        api,
        article,
        budget_item_id,
    )

    explicit_planning_type = (
        str(planning_type).strip().upper() if "planning_type" in provided_fields else None
    )
    if explicit_planning_type is not None and explicit_planning_type != article_planning_type:
        raise ValueError(
            f"planning_type {explicit_planning_type!r} does not match article "
            f"planning_type {article_planning_type!r}",
        )
    effective_planning_type = (
        explicit_planning_type if explicit_planning_type is not None else article_planning_type
    )

    if effective_planning_type == "REG":
        if "forecast_method" in provided_fields:
            raise ValueError("forecast_method is not allowed for REG plan-items")
        if start_period is None:
            raise ValueError("start_period is required for REG plan-items")
        if end_period is not None:
            assert_period_range(start_period, end_period)
    elif effective_planning_type == "IRR":
        if "start_period" in provided_fields:
            raise ValueError("start_period is not allowed for IRR plan-items")
        if "end_period" in provided_fields:
            raise ValueError("end_period is not allowed for IRR plan-items")
        if "periodicity" in provided_fields:
            raise ValueError("periodicity is not allowed for IRR plan-items")
        fm = forecast_method.strip().upper() if "forecast_method" in provided_fields else "MAN"
        if fm not in {"MAN", "AVG"}:
            raise ValueError(f"forecast_method must be MAN or AVG, got {fm!r}")
    else:
        raise ValueError(
            f"unsupported planning_type {effective_planning_type!r}; expected REG or IRR",
        )

    version_id = resolve_act_version_id(api)
    version = fetch_budget_version(api, version_id)
    assert_version_mutable(version=version)

    if effective_planning_type == "REG":
        assert start_period is not None
        plan_body = build_reg_plan_item_body(
            budget_version_id=version_id,
            budget_item_id=item_id,
            amount=amount_norm,
            currency=currency.strip().upper(),
            start_period=start_period,
            end_period=end_period,
            periodicity=periodicity,
        )
    else:
        plan_body = build_irr_plan_item_body(
            budget_version_id=version_id,
            budget_item_id=item_id,
            amount=amount_norm,
            currency=currency.strip().upper(),
            forecast_method=fm,
        )

    plan_status, created_plan = api.request(
        "POST",
        "/api/v1/budget/plan-items",
        data=plan_body,
    )
    if plan_status != 201 or not isinstance(created_plan, dict):
        raise RuntimeError(f"POST budget/plan-items -> {plan_status}: {created_plan}")

    base_result: dict[str, Any] = {
        "plan_item_id": str(created_plan["id"]),
        "budget_item_id": item_id,
        "budget_version_id": version_id,
        "article": article_name,
        "amount": amount_norm,
        "planning_type": effective_planning_type,
        "plan_item": created_plan,
    }
    if effective_planning_type == "REG":
        assert start_period is not None
        base_result["start_period"] = start_period.yyyy_mm
        if end_period is not None:
            base_result["end_period"] = end_period.yyyy_mm
    else:
        base_result["forecast_method"] = plan_body["forecast_method"]

    if not recalculate:
        return base_result

    try:
        recalc_body = recalculate_budget_projections(api, version_id)
    except RuntimeError as exc:
        raise CreatePlanItemRecalculateError(str(exc), base_result) from exc

    base_result["recalculate"] = {
        "budget_version_id": version_id,
        "projection_rows": projection_rows_count(recalc_body),
    }
    return base_result


_UNSET: Any = object()


def create_category(
    api: ApiClient,
    *,
    id: str,
    type: str,
    description: str,
    keywords: Any = _UNSET,
    default: Any = _UNSET,
) -> dict[str, Any]:
    """
    Create a transaction category via ``POST /api/v1/categories`` (FIN-217).

    Pre-HTTP validation is limited to presence/strip and ``list`` / ``bool`` types.
    Domain rules (id pattern, type↔id, ``default:true``, keyword elements) stay on
    the backend (FIN-214).

    :param api: API client
    :param id: Category id (e.g. ``P0004``)
    :param type: Category type letter ``C`` / ``P`` / ``S`` / ``I``
    :param description: Human-readable name
    :param keywords: Initial keywords; omit for ``[]``
    :param default: Default-category flag; omit for ``false``
    :return: Created category body (201)
    :raises ValueError: Pre-HTTP validation failure
    :raises RuntimeError: HTTP status is not 201 or body is not a dict
    """
    cat_id = id.strip()
    cat_type = type.strip()
    cat_description = description.strip()
    if not cat_id:
        raise ValueError("id is required")
    if not cat_type:
        raise ValueError("type is required")
    if not cat_description:
        raise ValueError("description is required")

    if keywords is _UNSET:
        keywords_body: list[Any] = []
    elif not isinstance(keywords, list):
        raise ValueError("keywords must be a list")
    else:
        keywords_body = keywords

    if default is _UNSET:
        default_body = False
    elif not isinstance(default, bool):
        raise ValueError("default must be a bool")
    else:
        default_body = default

    body = {
        "id": cat_id,
        "type": cat_type,
        "description": cat_description,
        "keywords": keywords_body,
        "default": default_body,
    }
    status, created = api.request("POST", "/api/v1/categories", data=body)
    if status != 201 or not isinstance(created, dict):
        raise RuntimeError(f"POST /api/v1/categories -> {status}: {created}")
    return created


_MASTER_PATCH_FIELDS = frozenset(
    {
        "planning_type",
        "name",
        "flow_type",
        "operation_category_id",
        "keywords",
        "item_status",
    },
)


class UpdateBudgetItemConvertError(RuntimeError):
    """
    Convert plan-item PUT failed; article rolled back (FIN-227 D-14).

    :param message: Error text
    :param context: Rollback / convert context for ops
    """

    def __init__(self, message: str, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


class UpdateBudgetItemCriticalError(RuntimeError):
    """
    Convert plan-item PUT failed and article rollback also failed (FIN-227 D-14).

    :param message: Error text
    :param context: Mismatch contexts for ops remediation
    """

    def __init__(self, message: str, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


class UpdateBudgetItemRecalculateError(RuntimeError):
    """
    Recalculate failed after successful article (+ convert) mutation (FIN-227).

    :param message: Error text
    :param context: Successful mutation fields for ops retry
    """

    def __init__(self, message: str, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context


def budget_item_put_body(item: dict[str, Any]) -> dict[str, Any]:
    """
    Build PUT body from a budget item GET/PUT response (FIN-227 D-01).

    :param item: Source item dict
    :return: Body accepted by ``PUT /budget/items/{id}``
    """
    return {
        "id": str(item["id"]),
        "name": str(item["name"]),
        "flow_type": str(item["flow_type"]),
        "operation_category_id": str(item["operation_category_id"]),
        "planning_type": str(item["planning_type"]),
        "keywords": list(item.get("keywords") or []),
        "status": str(item["status"]),
    }


def _list_act_plan_items_for_budget_item(
    api: ApiClient,
    *,
    budget_version_id: str,
    budget_item_id: str,
) -> list[dict[str, Any]]:
    """
    List ACT-version plan-items for one budget item (FIN-227 D-13).

    :param api: API client
    :param budget_version_id: ACT version UUID
    :param budget_item_id: Article UUID
    :return: Matching plan-item dicts
    """
    data = api.get_json(
        f"/api/v1/budget/plan-items?budget_version_id={budget_version_id}",
    )
    rows = data.get("budget_plan_items", [])
    if not isinstance(rows, list):
        raise RuntimeError("GET plan-items: budget_plan_items is not a list")
    return [row for row in rows if str(row.get("budget_item_id")) == budget_item_id]


def update_budget_item(
    api: ApiClient,
    *,
    article: str | None = None,
    budget_item_id: str | None = None,
    planning_type: str | None = None,
    name: str | None = None,
    flow_type: str | None = None,
    operation_category_id: str | None = None,
    keywords: list[str] | None = None,
    item_status: str | None = None,
    convert_plan_item: bool = False,
    amount: Any = None,
    start_period: Period | None = None,
    end_period: Period | None = None,
    periodicity: str = "M",
    forecast_method: str = "MAN",
    currency: str | None = None,
    recalculate: bool | None = None,
    provided_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """
    Update budget article master fields; optional ACT plan-item convert (FIN-227).

    :param api: API client
    :param article: Article substring for resolve
    :param budget_item_id: Article UUID for resolve
    :param planning_type: Optional new ``REG`` / ``IRR``
    :param name: Optional new name
    :param flow_type: Optional new flow type
    :param operation_category_id: Optional new category code (non-empty)
    :param keywords: Optional full keyword list replacement
    :param item_status: Optional article ``status``
    :param convert_plan_item: Convert exactly one ACT plan-item when type changes
    :param amount: Required for convert→REG; optional for convert→IRR
    :param start_period: Required for convert→REG
    :param end_period: Optional REG end month for convert
    :param periodicity: REG periodicity for convert→REG
    :param forecast_method: IRR forecast method for convert→IRR
    :param currency: Optional currency override for convert
    :param recalculate: Explicit recalculate flag; ``None`` → default per D-05
    :param provided_fields: Argument keys explicitly passed by caller
    :return: Tool result fields
    :raises UpdateBudgetItemConvertError: Convert failed; article rolled back
    :raises UpdateBudgetItemCriticalError: Convert and rollback both failed
    :raises UpdateBudgetItemRecalculateError: Mutations OK; recalculate failed
    """
    patch_keys = provided_fields & _MASTER_PATCH_FIELDS
    if not patch_keys:
        raise ValueError(
            "at least one master field required: "
            "planning_type, name, flow_type, operation_category_id, "
            "keywords, item_status",
        )

    item_id, _resolved_name, _ = resolve_budget_item_id_for_plan(
        api,
        article,
        budget_item_id,
    )
    current_item = api.get_json(f"/api/v1/budget/items/{item_id}")
    if not isinstance(current_item, dict):
        raise RuntimeError(f"GET budget/items/{item_id}: expected object")

    planning_type_before = str(current_item.get("planning_type", "")).strip().upper()
    new_planning_type = planning_type_before
    if "planning_type" in provided_fields:
        if planning_type is None or not str(planning_type).strip():
            raise ValueError("planning_type must be REG or IRR")
        new_planning_type = str(planning_type).strip().upper()
        if new_planning_type not in {"REG", "IRR"}:
            raise ValueError(f"planning_type must be REG or IRR, got {new_planning_type!r}")

    type_changed = new_planning_type != planning_type_before
    if convert_plan_item and not type_changed:
        raise ValueError(
            "convert_plan_item requires an actual planning_type change",
        )

    if "name" in provided_fields:
        if name is None or not str(name).strip():
            raise ValueError("name is required")
    if "operation_category_id" in provided_fields:
        if operation_category_id is None or not str(operation_category_id).strip():
            raise ValueError("operation_category_id cannot be empty")

    version_id = resolve_act_version_id(api)
    version = fetch_budget_version(api, version_id)
    assert_version_mutable(version=version)

    convert_path = False
    current_plan: dict[str, Any] | None = None
    if type_changed:
        candidates = _list_act_plan_items_for_budget_item(
            api,
            budget_version_id=version_id,
            budget_item_id=item_id,
        )
        if candidates and not convert_plan_item:
            ids = [str(row.get("id")) for row in candidates]
            raise RuntimeError(
                "planning_type change blocked: ACT plan-items exist; "
                f"pass convert_plan_item=true or resolve conflicts. "
                f"conflicting_plan_item_ids={ids}",
            )
        if convert_plan_item and len(candidates) > 1:
            ids = [str(row.get("id")) for row in candidates]
            raise RuntimeError(
                f"ambiguous ACT plan-items for convert: {ids}",
            )
        if convert_plan_item and len(candidates) == 1:
            convert_path = True
            plan_id = str(candidates[0]["id"])
            current_plan = api.get_json(f"/api/v1/budget/plan-items/{plan_id}")
            if not isinstance(current_plan, dict):
                raise RuntimeError(f"GET plan-items/{plan_id}: expected object")

    if "name" in provided_fields:
        trimmed = str(name).strip()
        assert_budget_item_name_available(api, trimmed, exclude_id=item_id)

    put_article = budget_item_put_body(current_item)
    if "planning_type" in provided_fields:
        put_article["planning_type"] = new_planning_type
    if "name" in provided_fields:
        put_article["name"] = str(name).strip()
    if "flow_type" in provided_fields:
        put_article["flow_type"] = str(flow_type).strip()
    if "operation_category_id" in provided_fields:
        put_article["operation_category_id"] = str(operation_category_id).strip()
    if "keywords" in provided_fields:
        if keywords is None:
            raise ValueError("keywords must be a list")
        if not isinstance(keywords, list):
            raise ValueError("keywords must be a list")
        put_article["keywords"] = list(keywords)
    if "item_status" in provided_fields:
        put_article["status"] = str(item_status)

    rollback_body = budget_item_put_body(current_item)

    if convert_path:
        assert current_plan is not None
        plan_put = _build_convert_plan_item_put_body(
            current_plan=current_plan,
            new_planning_type=new_planning_type,
            amount=amount,
            start_period=start_period,
            end_period=end_period,
            periodicity=periodicity,
            forecast_method=forecast_method,
            currency=currency,
            provided_fields=provided_fields,
        )

    status, updated_item = api.request(
        "PUT",
        f"/api/v1/budget/items/{item_id}",
        data=put_article,
    )
    if status != 200 or not isinstance(updated_item, dict):
        raise RuntimeError(f"PUT budget/items/{item_id} -> {status}: {updated_item}")

    converted = False
    updated_plan: dict[str, Any] | None = None
    if convert_path:
        assert current_plan is not None
        plan_id = str(current_plan["id"])
        plan_status, updated_plan = api.request(
            "PUT",
            f"/api/v1/budget/plan-items/{plan_id}",
            data=plan_put,
        )
        if plan_status != 200 or not isinstance(updated_plan, dict):
            plan_err = f"PUT plan-items/{plan_id} -> {plan_status}: {updated_plan}"
            rb_status, rb_body = api.request(
                "PUT",
                f"/api/v1/budget/items/{item_id}",
                data=rollback_body,
            )
            ctx: dict[str, Any] = {
                "budget_item_id": item_id,
                "budget_version_id": version_id,
                "article_before": rollback_body,
                "attempted_article_after": updated_item,
                "plan_item_error": plan_err,
                "conflicting_plan_item_ids": [plan_id],
            }
            if rb_status == 200 and isinstance(rb_body, dict):
                raise UpdateBudgetItemConvertError(
                    f"conversion failed, changes rolled back: {plan_err}",
                    ctx,
                )
            ctx["article_after"] = updated_item
            ctx["rollback_error"] = f"PUT budget/items/{item_id} -> {rb_status}: {rb_body}"
            raise UpdateBudgetItemCriticalError(
                f"conversion failed and rollback failed: {plan_err}; "
                f"{ctx['rollback_error']}",
                ctx,
            )
        converted = True

    base_result: dict[str, Any] = {
        "budget_item_id": item_id,
        "budget_version_id": version_id,
        "article": str(updated_item.get("name", "")),
        "planning_type_before": planning_type_before,
        "planning_type_after": str(updated_item.get("planning_type", "")),
        "budget_item": updated_item,
        "converted": converted,
    }
    if converted and updated_plan is not None:
        base_result["plan_item_id"] = str(updated_plan["id"])
        base_result["plan_item"] = updated_plan

    if recalculate is None:
        effective_recalculate = converted
    else:
        effective_recalculate = recalculate

    if not effective_recalculate:
        return base_result

    try:
        recalc_body = recalculate_budget_projections(api, version_id)
    except RuntimeError as exc:
        raise UpdateBudgetItemRecalculateError(str(exc), base_result) from exc

    base_result["recalculate"] = {
        "budget_version_id": version_id,
        "projection_rows": projection_rows_count(recalc_body),
    }
    return base_result


def _build_convert_plan_item_put_body(
    *,
    current_plan: dict[str, Any],
    new_planning_type: str,
    amount: Any,
    start_period: Period | None,
    end_period: Period | None,
    periodicity: str,
    forecast_method: str,
    currency: str | None,
    provided_fields: frozenset[str],
) -> dict[str, Any]:
    """
    Build plan-item PUT body for planning_type convert (FIN-227 D-12 / D-16).

    :param current_plan: Existing ACT plan-item
    :param new_planning_type: Target ``REG`` or ``IRR``
    :param amount: Optional amount override
    :param start_period: REG start (required for REG)
    :param end_period: Optional REG end
    :param periodicity: REG periodicity
    :param forecast_method: IRR forecast method
    :param currency: Optional currency override
    :param provided_fields: Explicit caller keys
    :return: PUT body including ``id``
    """
    plan_id = str(current_plan["id"])
    version_id = str(current_plan["budget_version_id"])
    item_id = str(current_plan["budget_item_id"])
    status = str(current_plan.get("status") or "ACTIVE")
    cur = (
        currency.strip().upper()
        if currency is not None and str(currency).strip()
        else str(current_plan.get("currency") or "EUR").strip().upper()
    )

    if new_planning_type == "REG":
        if amount is None:
            raise ValueError("amount is required for convert to REG")
        if start_period is None:
            raise ValueError("start_period is required for convert to REG")
        if end_period is not None:
            assert_period_range(start_period, end_period)
        amount_norm = normalize_plan_amount(amount)
        body = build_reg_plan_item_body(
            budget_version_id=version_id,
            budget_item_id=item_id,
            amount=amount_norm,
            currency=cur,
            start_period=start_period,
            end_period=end_period,
            periodicity=periodicity,
        )
        body["id"] = plan_id
        body["status"] = status
        return body

    if "start_period" in provided_fields:
        raise ValueError("start_period is not allowed for convert to IRR")
    if "end_period" in provided_fields:
        raise ValueError("end_period is not allowed for convert to IRR")
    if "periodicity" in provided_fields:
        raise ValueError("periodicity is not allowed for convert to IRR")
    if amount is None:
        amount_norm = normalize_plan_amount(current_plan.get("amount", "0"))
    else:
        amount_norm = normalize_plan_amount(amount)
    fm = (
        forecast_method.strip().upper()
        if "forecast_method" in provided_fields
        else "MAN"
    )
    if fm not in {"MAN", "AVG"}:
        raise ValueError(f"forecast_method must be MAN or AVG, got {fm!r}")
    body = build_irr_plan_item_body(
        budget_version_id=version_id,
        budget_item_id=item_id,
        amount=amount_norm,
        currency=cur,
        forecast_method=fm,
    )
    body["id"] = plan_id
    body["status"] = status
    return body
