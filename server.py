"""Finance Assistant MCP — ops prod/cand/test через FinancePlanningProject API."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions

_MCP_ROOT = Path(__file__).resolve().parent
_SCRIPTS = Path(os.environ.get("FINANCE_ASSISTANT_SCRIPTS", str(_MCP_ROOT / "scripts")))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load_script_module(module_name: str, filename: str) -> ModuleType:
    """
    Import a script file whose name contains hyphens.

    :param module_name: Synthetic module name
    :param filename: File name under ``FINANCE_ASSISTANT_SCRIPTS``
    :return: Loaded module
    """
    path = _SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


from finance_api_client import ApiClient  # noqa: E402
from monthly_close_lib import (  # noqa: E402
    CLOSE_PHASES,
    REPORT_SUBDIRS,
    REPORTS_ROOT,
    WORKING,
    act_horizon_periods,
    apply_keywords_file,
    close_period,
    connect_api,
    filter_horizon_periods,
    generate_reports,
    mc_reopen_neighbor_periods,
    parse_period,
    period_status_report,
    reopen_closed_periods,
    reopen_period,
    resolve_budget_version_id,
    run_derive,
    run_imports,
    verify_period,
)

_query_plan_fact = _load_script_module("query_plan_fact", "query-plan-fact.py")
_query_transactions = _load_script_module("query_transactions", "query-transactions.py")
_delete_by_filter = _load_script_module("delete_by_filter", "delete-by-filter.py")

active_budget_version_id = _query_plan_fact.active_budget_version_id
fetch_month_row = _query_plan_fact.fetch_month_row
fetch_plan_fact_transactions = _query_plan_fact.fetch_transactions
iter_months = _query_plan_fact.iter_months
resolve_budget_item_id = _query_plan_fact.resolve_budget_item_id
fetch_rows = _query_transactions.fetch_rows
month_key = _query_transactions.month_key
build_delete_by_filter_payload = _delete_by_filter.build_payload
run_delete_by_filter = _delete_by_filter.run_delete_by_filter

DEFAULT_PROFILE = os.environ.get("FINANCE_DATA_PROFILE", "prod")
DEFAULT_BASE = os.environ.get("FINANCE_API_BASE") or None

_sessions: dict[str, tuple[ApiClient, str]] = {}

server = Server("finance-assistant")


def _json_text(payload: Any) -> list[types.TextContent]:
    return [
        types.TextContent(
            type="text",
            text=json.dumps(payload, ensure_ascii=False, indent=2),
        )
    ]


def _session_key(profile: str, base: str | None) -> str:
    effective = base or DEFAULT_BASE or "auto"
    return f"{profile}:{effective}"


def get_session(profile: str, base: str | None = None) -> tuple[ApiClient, str]:
    """
    Return cached authenticated API client for profile.

    :param profile: ``test`` / ``cand`` / ``prod``
    :param base: Optional API base URL
    :return: Client and resolved base URL
    """
    key = _session_key(profile, base)
    if key in _sessions:
        return _sessions[key]
    effective_base = base or DEFAULT_BASE
    api, resolved = connect_api(effective_base, profile)
    _sessions[key] = (api, resolved)
    return api, resolved


def _handle_connect(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    base_arg = arguments.get("base") or DEFAULT_BASE
    api, base = get_session(profile, base_arg)
    meta = api.get_json("/api/v1/meta")
    versions = api.get_json("/api/v1/budget/versions")
    act = [
        v
        for v in (versions.get("budget_versions") or versions.get("versions") or [])
        if v.get("status") == "ACT"
    ]
    return _json_text(
        {
            "ok": True,
            "base": base,
            "data_profile": meta.get("data_profile"),
            "expected_profile": profile,
            "profile_match": meta.get("data_profile") == profile,
            "act_budget_version": act[0] if act else None,
        }
    )


def _handle_list_period_statuses(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    anchor = parse_period(str(arguments.get("anchor_period") or "2026-06"))
    vid = resolve_budget_version_id(api, anchor)
    from monthly_close_lib import fetch_reconciliation

    periods = act_horizon_periods(api)
    statuses = []
    for p in periods:
        rec = fetch_reconciliation(api, vid, p)
        statuses.append(
            {
                "period": p.yyyy_mm,
                "status": rec["status"],
                "methodology_status": rec.get("methodology_status"),
                "close_phase": rec.get("close_phase"),
            }
        )
    closed = [s["period"] for s in statuses if s["status"] == "closed"]
    return _json_text(
        {
            "base": base,
            "profile": profile,
            "budget_version_id": vid,
            "periods": statuses,
            "closed_count": len(closed),
            "closed_periods": closed,
            "hint": "Для полного отчёта (ready, C9999, блокеры) используй period_status_report.",
        }
    )


def _resolve_report_periods(
    api: ApiClient,
    arguments: dict[str, Any],
) -> tuple[Any, list[Any]]:
    """
    Resolve budget version and filtered horizon months for status reports.

    :param api: Authenticated API client
    :param arguments: Tool arguments
    :return: Budget version id and period list
    """
    anchor = parse_period(str(arguments.get("anchor_period") or "2026-06"))
    vid = resolve_budget_version_id(api, anchor)
    horizon = act_horizon_periods(api)
    year_arg = arguments.get("year")
    year = int(year_arg) if year_arg is not None else None
    if year is None and not arguments.get("period_from") and not arguments.get("period_to"):
        year = anchor.year
    periods = filter_horizon_periods(
        horizon,
        year=year,
        period_from=arguments.get("period_from"),
        period_to=arguments.get("period_to"),
    )
    return vid, periods


def _handle_period_status_report(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    vid, periods = _resolve_report_periods(api, arguments)
    detail = str(arguments.get("detail") or "summary")
    skip_empty = arguments.get("skip_empty", True)
    if not isinstance(skip_empty, bool):
        skip_empty = bool(skip_empty)
    report = period_status_report(
        api,
        vid,
        periods,
        detail=detail,
        skip_empty=skip_empty,
    )
    return _json_text(
        {
            "base": base,
            "profile": profile,
            "budget_version_id": vid,
            **report,
        }
    )


def _handle_reopen_periods(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    all_closed = bool(arguments.get("all_closed"))
    raw_periods = arguments.get("periods") or []

    anchor = parse_period(
        str(raw_periods[0] if raw_periods else arguments.get("anchor_period") or "2026-06")
    )
    vid = resolve_budget_version_id(api, anchor)

    if all_closed:
        from monthly_close_lib import reconciliation_status

        targets = [
            p
            for p in act_horizon_periods(api)
            if reconciliation_status(api, vid, p) == "closed"
        ]
    else:
        if not raw_periods:
            raise ValueError("Укажите periods (YYYY-MM) или all_closed=true")
        targets = [parse_period(str(p)) for p in raw_periods]

    log = reopen_closed_periods(api, vid, targets)
    return _json_text(
        {
            "base": base,
            "profile": profile,
            "targets": [p.yyyy_mm for p in targets],
            "results": log,
        }
    )


def _handle_verify_month(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    period = parse_period(str(arguments["period"]))
    api, base = get_session(profile, arguments.get("base"))
    vid = resolve_budget_version_id(api, period)
    verify = verify_period(api, period, vid)
    return _json_text(
        {
            "base": base,
            "profile": profile,
            "period": period.yyyy_mm,
            "verify": verify,
        }
    )


def _handle_process_month(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    period = parse_period(str(arguments["period"]))
    verify_only = bool(arguments.get("verify_only"))
    reopen_flag = bool(arguments.get("reopen"))
    reopen_neighbors = bool(arguments.get("reopen_neighbors"))
    skip_import = bool(arguments.get("skip_import"))
    close_flag = bool(arguments.get("close"))
    close_phase = str(arguments.get("close_phase") or "final")
    reports = bool(arguments.get("reports"))
    apply_keywords = arguments.get("apply_keywords")

    if close_flag and close_phase not in CLOSE_PHASES:
        raise ValueError(f"close_phase must be one of {CLOSE_PHASES}")

    api, base = get_session(profile, arguments.get("base"))
    vid = resolve_budget_version_id(api, period)
    log: dict[str, Any] = {
        "profile": profile,
        "period": period.yyyy_mm,
        "base": base,
        "budget_version_id": vid,
        "steps": {},
        "imports": [],
    }

    if verify_only:
        verify = verify_period(api, period, vid)
        log["steps"]["verify"] = verify
        return _json_text({"ok": verify["ok"], "log": log})

    if reopen_neighbors:
        affected, skipped = mc_reopen_neighbor_periods(period, api)
        log["steps"]["reopen_neighbors"] = {
            "targets": [p.yyyy_mm for p in affected],
            "skipped_outside_horizon": skipped,
            "results": reopen_closed_periods(api, vid, affected),
        }

    if reopen_flag:
        status, body = reopen_period(api, vid, period)
        log["steps"]["reopen"] = {"status": status, "body": body}

    if not skip_import:
        log["imports"] = run_imports(api, period)
        failed = [i for i in log["imports"] if i.get("status") != 200]
        if failed:
            log["steps"]["import_blocked"] = failed
            return _json_text({"ok": False, "log": log})

    if apply_keywords:
        added = apply_keywords_file(api, Path(str(apply_keywords)))
        log["steps"]["keywords_added"] = added

    log["steps"]["derive"] = run_derive(api, period)
    verify = verify_period(api, period, vid)
    log["steps"]["verify"] = verify

    c9999_count = int(verify["classification_summary"].get("expense_c9999_count") or 0)
    if c9999_count > 0 and close_flag and not apply_keywords:
        return _json_text(
            {
                "ok": False,
                "error": "C9999 > 0 — apply_keywords перед close",
                "log": log,
            }
        )

    if reports:
        out_dir = REPORTS_ROOT / REPORT_SUBDIRS[profile] / period.yyyy_mm
        generate_reports(api, period, out_dir, log)

    if not close_flag:
        log_path = WORKING / f"{profile}-{period.yyyy_mm}-process-log.json"
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        return _json_text({"ok": verify["ok"], "log": log, "log_path": str(log_path)})

    if not verify["readiness"].get("ready"):
        log["steps"]["close"] = {"status": "blocked", "reason": "readiness false"}
        return _json_text({"ok": False, "log": log})

    close_status, close_body = close_period(api, vid, period, close_phase=close_phase)
    log["steps"]["close"] = {
        "status": close_status,
        "close_phase": close_phase,
        "body": close_body if isinstance(close_body, dict) else str(close_body),
    }
    log_path = WORKING / f"{profile}-{period.yyyy_mm}-process-log.json"
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    return _json_text(
        {
            "ok": close_status == 200,
            "log": log,
            "log_path": str(log_path),
        }
    )


def _handle_query_plan_fact(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    date_from = str(arguments["date_from"])
    date_to = str(arguments["date_to"])
    include_tx = bool(arguments.get("transactions"))
    budget_version_id = arguments.get("budget_version_id") or active_budget_version_id(api)
    item_id, article_name = resolve_budget_item_id(
        api,
        arguments.get("article"),
        arguments.get("budget_item_id"),
    )
    months = iter_months(date_from, date_to)
    rows = [
        fetch_month_row(api, budget_version_id, period, item_id, article_name)
        for period in months
    ]
    payload: dict[str, Any] = {
        "base": base,
        "profile": profile,
        "budget_version_id": budget_version_id,
        "budget_item_id": item_id,
        "article": article_name,
        "months": [
            {
                "period": row.period,
                "plan": row.plan,
                "fact": row.fact,
                "variance": row.variance,
            }
            for row in rows
        ],
    }
    if include_tx:
        for period in months:
            txs = fetch_plan_fact_transactions(api, budget_version_id, period, item_id)
            if txs:
                for month_entry in payload["months"]:
                    if month_entry["period"] == period:
                        month_entry["transactions"] = txs
    return _json_text(payload)


def _handle_query_transactions(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    args = SimpleNamespace(
        date_from=arguments.get("date_from"),
        date_to=arguments.get("date_to"),
        indicator=arguments.get("indicator"),
        category=arguments.get("category"),
        provider=arguments.get("provider"),
        description=arguments.get("description"),
        contains=list(arguments.get("contains") or []),
    )
    rows = fetch_rows(api, args)
    group_by = arguments.get("group_by")
    if group_by == "month":
        split = bool(arguments.get("split_internet"))
        if not split:
            by_month: dict[str, float] = {}
            counts: dict[str, int] = {}
            for row in rows:
                key = month_key(row.date_display)
                by_month[key] = by_month.get(key, 0.0) + row.amount
                counts[key] = counts.get(key, 0) + 1
            groups = [
                {"month": m, "count": counts[m], "sum": round(by_month[m], 2)}
                for m in sorted(by_month)
            ]
        else:
            groups = []
            buckets: dict[str, dict[str, float]] = {}
            for row in rows:
                m = month_key(row.date_display)
                desc = row.description.lower()
                if "vodafone" in desc:
                    slot = "vodafone"
                elif "netcologne" in desc:
                    slot = "netcologne"
                else:
                    slot = "other"
                buckets.setdefault(m, {"vodafone": 0.0, "netcologne": 0.0, "other": 0.0})
                buckets[m][slot] += row.amount
            for m in sorted(buckets):
                v = buckets[m]
                groups.append(
                    {
                        "month": m,
                        **{k: round(v[k], 2) for k in v},
                        "total": round(sum(v.values()), 2),
                    }
                )
        return _json_text({"base": base, "profile": profile, "groups": groups})

    return _json_text(
        {
            "base": base,
            "profile": profile,
            "row_count": len(rows),
            "rows": [
                {
                    "date": r.date_display,
                    "amount": r.amount,
                    "indicator": r.indicator,
                    "category": r.category,
                    "provider": r.provider,
                    "description": r.description,
                }
                for r in rows
            ],
        }
    )


def _handle_delete_transactions_by_filter(
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))

    raw_filter = arguments.get("filter")
    if not isinstance(raw_filter, dict) or not raw_filter:
        raise ValueError("filter обязателен — объект с ≥1 активным условием")

    dry_run = arguments.get("dry_run", True)
    if not isinstance(dry_run, bool):
        dry_run = bool(dry_run)
    confirm = bool(arguments.get("confirm"))
    allow_closed = bool(arguments.get("allow_closed"))
    confirm_count = arguments.get("confirm_count")
    if confirm_count is not None:
        confirm_count = int(confirm_count)

    if confirm and dry_run:
        raise ValueError("Для удаления передай dry_run=false и confirm=true")

    payload = build_delete_by_filter_payload(
        dry_run=dry_run,
        confirm=confirm,
        allow_closed=allow_closed,
        confirm_count=confirm_count,
        filter_data=raw_filter,
    )
    status, body = run_delete_by_filter(api, payload)
    result: dict[str, Any] = {
        "ok": status == 200,
        "base": base,
        "profile": profile,
        "status": status,
        "request": payload,
    }
    if isinstance(body, dict):
        result["body"] = body
    else:
        result["error"] = body
    return _json_text(result)


PROFILE_SCHEMA = {
    "type": "string",
    "enum": ["test", "cand", "prod"],
    "description": "FINANCE_DATA_PROFILE (по умолчанию prod)",
}

BASE_SCHEMA = {
    "type": "string",
    "description": "URL API (по умолчанию FINANCE_API_BASE или скан 8000–8010)",
}

DELETE_FILTER_SCHEMA = {
    "type": "object",
    "description": "Фильтр как у list API + source_file (__empty__ для orphan)",
    "properties": {
        "date_from": {"type": "string"},
        "date_to": {"type": "string"},
        "posting_date_from": {"type": "string"},
        "posting_date_to": {"type": "string"},
        "description": {"type": "string"},
        "amount": {"type": "string"},
        "debit_credit_indicator": {"type": "string", "enum": ["D", "C"]},
        "provider": {"type": "string"},
        "accounting_period": {
            "type": "string",
            "description": "YYYYMM (не использовать вместе с _from/_to)",
        },
        "accounting_period_from": {"type": "string", "description": "YYYYMM inclusive"},
        "accounting_period_to": {"type": "string", "description": "YYYYMM inclusive"},
        "budget_period": {"type": "string"},
        "transaction_type": {"type": "string"},
        "transaction_category": {"type": "string"},
        "project": {"type": "string"},
        "source_file": {
            "type": "string",
            "description": "Exact filename или __empty__ (orphan без source_file)",
        },
    },
}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="finance_api_connect",
            description=(
                "Подключиться к FinancePlanning API: login, проверить data_profile и ACT-версию бюджета. "
                "Сессия кэшируется в процессе MCP."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                },
            },
        ),
        types.Tool(
            name="list_period_statuses",
            description=(
                "Статусы reconciliation (open/closed/draft) и methodology_status "
                "(preliminary_closed/final_closed) для горизонта ACT. "
                "Полный отчёт (ready, C9999, агрегаты) — period_status_report."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "anchor_period": {
                        "type": "string",
                        "description": "YYYY-MM для resolve budget_version_id (default 2026-06)",
                    },
                },
            },
        ),
        types.Tool(
            name="period_status_report",
            description=(
                "Отчёт по статусу периодов за год или диапазон: reconciliation, "
                "methodology_status, close_phase, ready, C9999, MC tail, блокеры readiness. "
                "Один вызов вместо list_period_statuses + N× verify_month."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "year": {
                        "type": "integer",
                        "description": "Календарный год (default: год anchor_period)",
                    },
                    "period_from": {
                        "type": "string",
                        "description": "YYYY-MM — начало диапазона (вместо year)",
                    },
                    "period_to": {
                        "type": "string",
                        "description": "YYYY-MM — конец диапазона",
                    },
                    "anchor_period": {
                        "type": "string",
                        "description": "YYYY-MM для budget_version_id и default year (default 2026-06)",
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["status_only", "summary", "full"],
                        "description": "status_only | summary (default) | full (+ verify payload)",
                    },
                    "skip_empty": {
                        "type": "boolean",
                        "description": "Не вызывать full verify для месяцев без строк (default true)",
                    },
                },
            },
        ),
        types.Tool(
            name="reopen_periods",
            description=(
                "Переоткрыть закрытые периоды reconciliation. "
                "all_closed=true — все closed в горизонте ACT; иначе список periods."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "all_closed": {
                        "type": "boolean",
                        "description": "Reopen все closed месяцы горизонта ACT",
                    },
                    "periods": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Список YYYY-MM для reopen",
                    },
                    "anchor_period": {
                        "type": "string",
                        "description": "YYYY-MM для budget_version_id при all_closed",
                    },
                },
            },
        ),
        types.Tool(
            name="verify_month",
            description=(
                "Verify месяца: MC from_17th, classification-summary, readiness (без import/close)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {"type": "string", "description": "YYYY-MM или YYYYMM"},
                },
                "required": ["period"],
            },
        ),
        types.Tool(
            name="process_month",
            description=(
                "Ops-оркестратор периода: reopen → import → derive → verify → optional close/PDF. "
                "close=true только по явной команде пользователя."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {"type": "string", "description": "YYYY-MM или YYYYMM"},
                    "verify_only": {"type": "boolean"},
                    "reopen": {"type": "boolean"},
                    "reopen_neighbors": {
                        "type": "boolean",
                        "description": (
                            "Reopen closed M-1, M, M+1 (MC tail); "
                            "месяцы вне горизонта ACT пропускаются"
                        ),
                    },
                    "skip_import": {"type": "boolean"},
                    "apply_keywords": {
                        "type": "string",
                        "description": "Путь к JSON keywords (C9999)",
                    },
                    "close": {
                        "type": "boolean",
                        "description": "Закрыть период — только по явной команде пользователя",
                    },
                    "close_phase": {
                        "type": "string",
                        "enum": list(CLOSE_PHASES),
                    },
                    "reports": {"type": "boolean", "description": "Генерировать PDF отчёты"},
                },
                "required": ["period"],
            },
        ),
        types.Tool(
            name="query_plan_fact",
            description="План/факт по статье бюджета (GET /budget/plan-actual).",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "article": {"type": "string", "description": "Подстрока имени статьи"},
                    "budget_item_id": {"type": "string"},
                    "budget_version_id": {"type": "string"},
                    "date_from": {"type": "string", "description": "YYYY-MM"},
                    "date_to": {"type": "string", "description": "YYYY-MM"},
                    "transactions": {
                        "type": "boolean",
                        "description": "Drill-down транзакций по месяцам",
                    },
                },
                "required": ["date_from", "date_to"],
            },
        ),
        types.Tool(
            name="query_transactions",
            description="Выборка транзакций (GET /transactions) с фильтрами и group-by month.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "date_from": {"type": "string"},
                    "date_to": {"type": "string"},
                    "indicator": {"type": "string", "enum": ["D", "C"]},
                    "category": {"type": "string"},
                    "provider": {"type": "string"},
                    "description": {"type": "string"},
                    "contains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Подстроки в описании (OR)",
                    },
                    "group_by": {"type": "string", "enum": ["month"]},
                    "split_internet": {
                        "type": "boolean",
                        "description": "С group_by month: vodafone/netcologne/other",
                    },
                },
            },
        ),
        types.Tool(
            name="delete_transactions_by_filter",
            description=(
                "Maintenance: подсчёт или удаление транзакций по фильтру (BLG-084). "
                "dry_run=true по умолчанию. Orphan cleanup: "
                'filter={"source_file":"__empty__"}. '
                "Удаление: dry_run=false, confirm=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview (default true)",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "true для фактического удаления (с dry_run=false)",
                    },
                    "allow_closed": {
                        "type": "boolean",
                        "description": "Bypass guard BLG-032 для closed периодов",
                    },
                    "confirm_count": {
                        "type": "integer",
                        "description": "Опц.: MUST = deletable_count из свежего dry_run",
                    },
                    "filter": DELETE_FILTER_SCHEMA,
                },
                "required": ["filter"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    handlers = {
        "finance_api_connect": _handle_connect,
        "list_period_statuses": _handle_list_period_statuses,
        "period_status_report": _handle_period_status_report,
        "reopen_periods": _handle_reopen_periods,
        "verify_month": _handle_verify_month,
        "process_month": _handle_process_month,
        "fix_month": _handle_process_month,  # deprecated alias
        "query_plan_fact": _handle_query_plan_fact,
        "query_transactions": _handle_query_transactions,
        "delete_transactions_by_filter": _handle_delete_transactions_by_filter,
    }
    handler = handlers.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    try:
        return handler(arguments or {})
    except Exception as exc:
        return _json_text({"ok": False, "error": str(exc)})


async def run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="finance-assistant",
                server_version="1.4.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(run())
