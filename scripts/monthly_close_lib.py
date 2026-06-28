"""Shared monthly close helpers for Finance Assistant MCP (``process_month`` tool)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
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
    :return: Verification result with ``ok`` and ``issues``
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
    if mc["mc_from_17th"] == 0:
        issues.append(
            "MC: нет операций с 17-го — проверь tail PDF в одном batch с head"
        )
    if int(summary.get("expense_c9999_count") or 0) > 0:
        issues.append(f"C9999: {summary['expense_c9999_count']} расходов")
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
    else:
        print("verify: OK")


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


def apply_keywords_file(api: ApiClient, path: Path) -> list[dict]:
    """
    Merge keywords from JSON and PUT categories.

    :param api: API client
    :param path: JSON ``{category_id: [keywords]}``
    :return: List of added keyword records
    """
    additions: dict[str, list[str]] = json.loads(path.read_text(encoding="utf-8"))
    categories = api.get_json("/api/v1/categories")["categories"]
    by_id = {c["id"]: c for c in categories}
    added: list[dict] = []
    for cat_id, keywords in additions.items():
        if cat_id not in by_id:
            raise KeyError(f"unknown category {cat_id!r}")
        existing = set(by_id[cat_id].get("keywords") or [])
        for kw in keywords:
            if kw not in existing:
                by_id[cat_id].setdefault("keywords", []).append(kw)
                existing.add(kw)
                added.append({"category": cat_id, "keyword": kw})
    status, body = api.request(
        "PUT", "/api/v1/categories", data={"categories": categories}
    )
    if status != 200:
        raise RuntimeError(f"PUT categories -> {status}: {body}")
    return added


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


def plan_item_put_body(plan_item: dict[str, Any], amount: str) -> dict[str, Any]:
    """
    Build PUT body from a plan item, dropping projection-page enrichments.

    :param plan_item: Source row (GET plan-item or projection-period-page)
    :param amount: Normalized amount string
    :return: Body accepted by ``PUT /budget/plan-items/{id}``
    """
    body = {k: plan_item[k] for k in _PLAN_ITEM_PUT_KEYS if k in plan_item}
    body["amount"] = amount
    body["id"] = str(plan_item["id"])
    return body


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


def resolve_budget_item_id_for_plan(
    api: ApiClient,
    article: str | None,
    budget_item_id: str | None,
) -> tuple[str, str]:
    """
    Resolve article label to budget item id (same rules as ``query_plan_fact``).

    :param api: API client
    :param article: Substring of article name
    :param budget_item_id: Explicit UUID
    :return: Tuple of item id and display name
    """
    if budget_item_id:
        item = api.get_json(f"/api/v1/budget/items/{budget_item_id}")
        return budget_item_id, str(item.get("name", budget_item_id))
    if not article:
        raise ValueError("article or budget_item_id required for resolve")
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
    return str(item["id"]), str(item["name"])


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

    item_id, article_name = resolve_budget_item_id_for_plan(api, article, budget_item_id)
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
    recalculate: bool = True,
) -> dict[str, Any]:
    """
    Update one plan item amount and optionally recalculate projections (FIN-108).

    :param api: API client
    :param amount: New plan amount (non-negative; zero allowed)
    :param plan_item_id: Plan-item UUID (precedence over resolve fields)
    :param period: Month for article resolve
    :param article: Article substring
    :param budget_item_id: Article UUID
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
    version_id = str(plan_item["budget_version_id"])
    version = fetch_budget_version(api, version_id)
    assert_version_mutable(version=version)

    plan_id = str(plan_item["id"])
    amount_before = str(plan_item.get("amount", ""))
    put_body = plan_item_put_body(plan_item, amount_after)

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
