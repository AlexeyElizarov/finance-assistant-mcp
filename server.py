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
    CreateBudgetItemPlanItemError,
    CreateBudgetItemRecalculateError,
    CreatePlanItemRecalculateError,
    UpdatePlanItemRecalculateError,
    act_horizon_periods,
    apply_keywords_file,
    apply_keywords_payload,
    ApplyKeywordsError,
    ApplyKeywordsPartialError,
    ApplyKeywordsValidationError,
    close_period,
    connect_api,
    c9999_close_guard_error,
    empty_keywords_changes,
    filter_horizon_periods,
    generate_reports,
    keywords_file_effective,
    keywords_payload_effective,
    mc_reopen_neighbor_periods,
    parse_period,
    prepare_process_month_orchestrator_flags,
    period_status_report,
    PRESET_MONTHLY_CLOSE_PREPARE,
    put_transaction_overrides,
    reopen_closed_periods,
    reopen_period,
    resolve_budget_version_id,
    run_derive,
    run_imports,
    create_budget_item,
    create_category,
    create_plan_item,
    update_plan_item,
    upsert_expense_project,
    verify_period,
    list_c9999_payload,
)

_query_plan_fact = _load_script_module("query_plan_fact", "query-plan-fact.py")
_query_transactions = _load_script_module("query_transactions", "query-transactions.py")
_delete_by_filter = _load_script_module("delete_by_filter", "delete-by-filter.py")
_household_base_share = _load_script_module("household_base_share", "household_base_share.py")
_fx_rates = _load_script_module("fx_rates", "fx_rates.py")
_household_advances = _load_script_module("household_advances", "household_advances.py")
_household_receivables = _load_script_module("household_receivables", "household_receivables.py")
_personal_fund_carryover = _load_script_module("personal_fund_carryover", "personal_fund_carryover.py")
_money_check_report = _load_script_module("money_check_report", "money_check_report.py")
_put_transaction_category = _load_script_module(
    "put_transaction_category",
    "put_transaction_category.py",
)

active_budget_version_id = _query_plan_fact.active_budget_version_id
fetch_month_row = _query_plan_fact.fetch_month_row
fetch_plan_fact_transactions = _query_plan_fact.fetch_transactions
iter_months = _query_plan_fact.iter_months
resolve_budget_item_id = _query_plan_fact.resolve_budget_item_id
fetch_rows = _query_transactions.fetch_rows
month_key = _query_transactions.month_key
normalize_query_args = _query_transactions.normalize_query_args
build_delete_by_filter_payload = _delete_by_filter.build_payload
run_delete_by_filter = _delete_by_filter.run_delete_by_filter
compute_household_base_share = _household_base_share.compute_household_base_share
list_fx_rates = _fx_rates.list_fx_rates
upsert_fx_rate = _fx_rates.upsert_fx_rate
run_household_advances = _household_advances.run_household_advances
run_household_receivables = _household_receivables.run_household_receivables
compute_personal_fund_carryover = _personal_fund_carryover.compute_personal_fund_carryover
compute_money_check_report = _money_check_report.compute_money_check_report
put_transaction_category = _put_transaction_category.put_transaction_category

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


def _handle_list_c9999(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    period = parse_period(str(arguments["period"]))
    api, base = get_session(profile, arguments.get("base"))
    payload = list_c9999_payload(api, period)
    return _json_text({"ok": True, "profile": profile, "base": base, **payload})


def _handle_apply_keywords(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    period = parse_period(str(arguments["period"]))
    keywords_file = arguments.get("keywords_file")
    payload = arguments.get("payload")
    if bool(keywords_file) == bool(payload):
        raise ValueError("exactly one of keywords_file or payload is required")
    derive_flag = bool(arguments.get("derive", True))

    api, base = get_session(profile, arguments.get("base"))
    if keywords_file:
        raw = json.loads(Path(str(keywords_file)).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ApplyKeywordsValidationError("payload root must be an object")
        effective = keywords_payload_effective(raw)
        try:
            changes = apply_keywords_file(api, Path(str(keywords_file)))
        except ApplyKeywordsPartialError as exc:
            return _json_text(
                {
                    "ok": False,
                    "profile": profile,
                    "base": base,
                    "period": period.yyyy_mm,
                    "effective": effective,
                    "changes": exc.partial_changes,
                    "error": str(exc),
                }
            )
    else:
        if not isinstance(payload, dict):
            raise ApplyKeywordsValidationError("payload must be an object")
        effective = keywords_payload_effective(payload)
        try:
            changes = apply_keywords_payload(api, payload)
        except ApplyKeywordsPartialError as exc:
            return _json_text(
                {
                    "ok": False,
                    "profile": profile,
                    "base": base,
                    "period": period.yyyy_mm,
                    "effective": effective,
                    "changes": exc.partial_changes,
                    "error": str(exc),
                }
            )

    body: dict[str, Any] = {
        "ok": True,
        "profile": profile,
        "base": base,
        "period": period.yyyy_mm,
        "effective": effective,
        "changes": changes,
    }
    if derive_flag and effective:
        body["derive"] = run_derive(api, period)
    return _json_text(body)


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
    flags = prepare_process_month_orchestrator_flags(arguments)
    verify_only = flags["verify_only"]
    reopen_flag = flags["reopen"]
    reopen_neighbors = flags["reopen_neighbors"]
    skip_import = flags["skip_import"]
    close_flag = flags["close"]
    close_phase = flags["close_phase"]
    reports = flags["reports"]
    apply_keywords = flags["apply_keywords"]
    c9999_acknowledged = flags["c9999_acknowledged"]

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

    keywords_effective = False
    if apply_keywords:
        kw_path = Path(str(apply_keywords))
        raw = json.loads(kw_path.read_text(encoding="utf-8"))
        keywords_effective = (
            keywords_payload_effective(raw) if isinstance(raw, dict) else False
        )
        try:
            changes = apply_keywords_file(api, kw_path)
        except ApplyKeywordsPartialError as exc:
            log["steps"]["keywords_effective"] = keywords_effective
            log["steps"]["keywords_changes"] = exc.partial_changes
            log["steps"]["keywords_added"] = exc.partial_changes["categories_added"]
            return _json_text({"ok": False, "error": str(exc), "log": log})
        except ApplyKeywordsError as exc:
            return _json_text({"ok": False, "error": str(exc), "log": log})
        log["steps"]["keywords_effective"] = keywords_effective
        log["steps"]["keywords_changes"] = changes
        log["steps"]["keywords_added"] = changes["categories_added"]

    log["steps"]["derive"] = run_derive(api, period)
    verify = verify_period(api, period, vid)
    log["steps"]["verify"] = verify

    c9999_count = int(verify["classification_summary"].get("expense_c9999_count") or 0)
    if close_flag:
        guard_error = c9999_close_guard_error(
            expense_c9999_count=c9999_count,
            close_phase=close_phase,
            keywords_effective=keywords_effective,
            c9999_acknowledged=c9999_acknowledged,
            readiness=verify.get("readiness") or {},
        )
        if guard_error:
            return _json_text(
                {
                    "ok": False,
                    "error": guard_error,
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

    if c9999_acknowledged and c9999_count > 0 and close_phase == "preliminary":
        log["steps"]["c9999_acknowledged"] = True
        log["steps"]["c9999_count"] = c9999_count

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


def _handle_household_base_share(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    period = str(arguments["period"])
    budget_version_id = str(
        arguments.get("budget_version_id") or active_budget_version_id(api)
    )
    payload = compute_household_base_share(
        api,
        profile=profile,
        base=base,
        period=period,
        budget_version_id=budget_version_id,
        mapping_path=arguments.get("mapping_path"),
        income_mode=arguments.get("income_mode"),
        include_income_matches=arguments.get("include_income_matches"),
        exclude_income_matches=arguments.get("exclude_income_matches"),
        convert_plans_to_eur=bool(arguments.get("convert_plans_to_eur", True)),
    )
    return _json_text(payload)


def _handle_list_fx_rates(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    payload = list_fx_rates(
        api,
        profile=profile,
        base=base,
        period=arguments.get("period"),
        period_from=arguments.get("period_from"),
        period_to=arguments.get("period_to"),
        from_currency=arguments.get("from_currency"),
        to_currency=arguments.get("to_currency"),
    )
    return _json_text(payload)


def _handle_upsert_fx_rate(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    payload = upsert_fx_rate(
        api,
        profile=profile,
        base=base,
        period=str(arguments["period"]),
        rate=str(arguments["rate"]),
        from_currency=arguments.get("from_currency"),
        to_currency=arguments.get("to_currency"),
    )
    return _json_text(payload)


def _handle_household_advances(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    action = str(arguments["action"])
    payload = run_household_advances(profile, action, dict(arguments))
    return _json_text(payload)


def _handle_household_receivables(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    action = str(arguments["action"])
    payload = run_household_receivables(profile, action, dict(arguments))
    return _json_text(payload)


def _handle_personal_fund_carryover(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    closed_period = str(arguments["closed_period"])
    budget_version_id = str(
        arguments.get("budget_version_id") or active_budget_version_id(api)
    )
    payload = compute_personal_fund_carryover(
        api,
        profile=profile,
        base=base,
        closed_period=closed_period,
        budget_version_id=budget_version_id,
        target_period=arguments.get("target_period"),
        mapping_path=arguments.get("mapping_path"),
        dry_run=bool(arguments.get("dry_run", False)),
        mark_advances_deducted=bool(arguments.get("mark_advances_deducted", True)),
        allow_non_final=bool(arguments.get("allow_non_final", False)),
        incoming_carryover_override=arguments.get("incoming_carryover_override"),
    )
    return _json_text(payload)


def _handle_money_check_report(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    budget_version_id = str(
        arguments.get("budget_version_id") or active_budget_version_id(api)
    )
    payload = compute_money_check_report(
        api,
        profile=profile,
        base=base,
        budget_version_id=budget_version_id,
        check_period=arguments.get("check_period"),
        prior_period=arguments.get("prior_period"),
        as_of_period=arguments.get("as_of_period"),
        mapping_path=arguments.get("mapping_path"),
        include_advance_breakdown=bool(arguments.get("include_advance_breakdown", True)),
    )
    return _json_text(payload)


def _handle_put_transaction_overrides(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    period = parse_period(str(arguments["period"]))
    vid = resolve_budget_version_id(api, period)
    raw_overrides = arguments.get("overrides") or {}
    if not isinstance(raw_overrides, dict) or not raw_overrides:
        raise ValueError("overrides must be a non-empty object {transaction_key: budget_item_id}")
    overrides = {str(k): str(v) for k, v in raw_overrides.items()}
    merge = bool(arguments.get("merge", True))
    body = put_transaction_overrides(api, vid, period, overrides, merge=merge)
    payload: dict[str, Any] = {
        "ok": True,
        "profile": profile,
        "base": base,
        "period": period.yyyy_mm,
        "budget_version_id": vid,
        "overrides_applied": overrides,
        "merge": merge,
        "reconciliation": body,
    }
    if arguments.get("derive", True):
        payload["derive"] = run_derive(api, period)
    return _json_text(payload)


def _handle_put_transaction_category(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    kwargs: dict[str, Any] = {
        "transaction_id": arguments.get("transaction_id"),
        "transaction_type": arguments.get("transaction_type"),
        "transaction_category": arguments.get("transaction_category"),
        "allow_closed": bool(arguments.get("allow_closed", False)),
    }
    if "reconciliation_note" in arguments:
        kwargs["reconciliation_note"] = arguments.get("reconciliation_note")
    if "category_source" in arguments:
        kwargs["category_source"] = arguments.get("category_source")
    payload = put_transaction_category(api, profile=profile, base=base, **kwargs)
    return _json_text(payload)


def _handle_upsert_expense_project(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    project = arguments.get("project")
    if not isinstance(project, dict):
        raise ValueError("project must be an object")
    result = upsert_expense_project(api, project)
    return _json_text({"ok": True, "profile": profile, "base": base, **result})


def _handle_create_category(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    required = ("id", "type", "description")
    missing = [field for field in required if field not in arguments]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    kwargs: dict[str, Any] = {
        "id": str(arguments["id"]),
        "type": str(arguments["type"]),
        "description": str(arguments["description"]),
    }
    if "keywords" in arguments:
        kwargs["keywords"] = arguments["keywords"]
    if "default" in arguments:
        kwargs["default"] = arguments["default"]
    category = create_category(api, **kwargs)
    return _json_text({"ok": True, "profile": profile, "base": base, "category": category})


def _handle_create_budget_item(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    required = ("name", "flow_type", "operation_category_id", "amount", "start_period")
    missing = [field for field in required if field not in arguments]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    end_raw = arguments.get("end_period")
    end_period = parse_period(str(end_raw)) if end_raw else None
    try:
        result = create_budget_item(
            api,
            name=str(arguments["name"]),
            flow_type=str(arguments["flow_type"]),
            operation_category_id=str(arguments["operation_category_id"]),
            amount=arguments["amount"],
            start_period=parse_period(str(arguments["start_period"])),
            planning_type=str(arguments.get("planning_type") or "REG"),
            keywords=list(arguments.get("keywords") or []),
            item_status=str(arguments.get("item_status") or "ACT"),
            currency=str(arguments.get("currency") or "EUR"),
            periodicity=str(arguments.get("periodicity") or "M"),
            end_period=end_period,
            recalculate=bool(arguments.get("recalculate", True)),
        )
    except CreateBudgetItemPlanItemError as exc:
        return _json_text(
            {
                "ok": False,
                "error": str(exc),
                "profile": profile,
                "base": base,
                **exc.context,
            },
        )
    except CreateBudgetItemRecalculateError as exc:
        return _json_text(
            {
                "ok": False,
                "error": str(exc),
                "profile": profile,
                "base": base,
                **exc.context,
            },
        )
    return _json_text({"ok": True, "profile": profile, "base": base, **result})


def _handle_update_plan_item(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    if "amount" not in arguments:
        raise ValueError("amount is required")
    plan_item_id = arguments.get("plan_item_id")
    period_raw = arguments.get("period")
    period = parse_period(str(period_raw)) if period_raw else None
    start_raw = arguments.get("start_period")
    start_period = parse_period(str(start_raw)) if start_raw else None
    end_raw = arguments.get("end_period")
    end_period = parse_period(str(end_raw)) if end_raw else None
    try:
        result = update_plan_item(
            api,
            arguments["amount"],
            plan_item_id=str(plan_item_id) if plan_item_id else None,
            period=period,
            article=arguments.get("article"),
            budget_item_id=arguments.get("budget_item_id"),
            start_period=start_period,
            end_period=end_period,
            recalculate=bool(arguments.get("recalculate", True)),
        )
    except UpdatePlanItemRecalculateError as exc:
        return _json_text(
            {
                "ok": False,
                "error": str(exc),
                "profile": profile,
                "base": base,
                **exc.context,
            },
        )
    return _json_text({"ok": True, "profile": profile, "base": base, **result})


def _handle_create_plan_item(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    if "amount" not in arguments:
        raise ValueError("missing required fields: amount")
    if not arguments.get("article") and not arguments.get("budget_item_id"):
        raise ValueError("article or budget_item_id is required")
    provided = frozenset(
        key
        for key in ("start_period", "end_period", "periodicity", "forecast_method", "planning_type")
        if key in arguments
    )
    end_raw = arguments.get("end_period")
    end_period = parse_period(str(end_raw)) if end_raw else None
    start_raw = arguments.get("start_period")
    start_period = parse_period(str(start_raw)) if start_raw else None
    explicit_planning_type = (
        str(arguments["planning_type"]) if "planning_type" in arguments else None
    )
    try:
        result = create_plan_item(
            api,
            arguments["amount"],
            start_period,
            article=str(arguments["article"]) if arguments.get("article") else None,
            budget_item_id=str(arguments["budget_item_id"])
            if arguments.get("budget_item_id")
            else None,
            planning_type=explicit_planning_type,
            forecast_method=str(arguments.get("forecast_method") or "MAN"),
            currency=str(arguments.get("currency") or "EUR"),
            periodicity=str(arguments.get("periodicity") or "M"),
            end_period=end_period,
            recalculate=bool(arguments.get("recalculate", True)),
            provided_fields=provided,
        )
    except CreatePlanItemRecalculateError as exc:
        return _json_text(
            {
                "ok": False,
                "error": str(exc),
                "profile": profile,
                "base": base,
                **exc.context,
            },
        )
    return _json_text({"ok": True, "profile": profile, "base": base, **result})


def _handle_query_transactions(arguments: dict[str, Any]) -> list[types.TextContent]:
    profile = str(arguments.get("profile") or DEFAULT_PROFILE)
    api, base = get_session(profile, arguments.get("base"))
    args = normalize_query_args(
        date_from=arguments.get("date_from"),
        date_to=arguments.get("date_to"),
        indicator=arguments.get("indicator"),
        period=arguments.get("period"),
        accounting_period=arguments.get("accounting_period"),
        category=arguments.get("category"),
        transaction_category=arguments.get("transaction_category"),
        provider=arguments.get("provider"),
        description=arguments.get("description"),
        contains=arguments.get("contains"),
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
                    "id": r.id,
                    "date": r.date_display,
                    "amount": r.amount,
                    "indicator": r.indicator,
                    "category": r.category,
                    "transaction_type": r.transaction_type,
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
            name="list_c9999",
            description=(
                "Список expense C9999 за месяц для c9999-proposal-policy "
                "(drill-down; не verify и не override flow)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {"type": "string", "description": "YYYY-MM"},
                },
                "required": ["period"],
            },
        ),
        types.Tool(
            name="apply_keywords",
            description=(
                "Применить keywords к категориям, статьям бюджета и проектам "
                "(unified или legacy JSON; FIN-16)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {"type": "string", "description": "YYYY-MM для derive"},
                    "keywords_file": {
                        "type": "string",
                        "description": "Путь к JSON keywords",
                    },
                    "payload": {
                        "type": "object",
                        "description": "Inline keywords JSON (unified или legacy)",
                    },
                    "derive": {
                        "type": "boolean",
                        "description": "POST derive после apply, если effective=true",
                    },
                },
                "required": ["period"],
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
                "preset monthly_close_prepare — рекомендуемый prepare-workflow с PDF (FIN-31). "
                "close=true только по явной команде пользователя."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {"type": "string", "description": "YYYY-MM или YYYYMM"},
                    "preset": {
                        "type": "string",
                        "enum": [PRESET_MONTHLY_CLOSE_PREPARE],
                        "description": (
                            "UX preset: reopen_neighbors + reopen + reports; "
                            "explicit flags override preset defaults"
                        ),
                    },
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
                        "description": "Путь к unified/legacy JSON keywords",
                    },
                    "close": {
                        "type": "boolean",
                        "description": "Закрыть период — только по явной команде пользователя",
                    },
                    "close_phase": {
                        "type": "string",
                        "enum": list(CLOSE_PHASES),
                    },
                    "c9999_acknowledged": {
                        "type": "boolean",
                        "description": (
                            "Operator acknowledged retained C9999 misc expenses; "
                            "only with close=true and close_phase=preliminary"
                        ),
                    },
                    "reports": {"type": "boolean", "description": "Генерировать PDF отчёты"},
                },
                "required": ["period"],
            },
        ),
        types.Tool(
            name="query_plan_fact",
            description=(
                "План/факт по статье бюджета (GET /budget/plan-actual). "
                "При not-found/ambiguous article — enriched error с кандидатами "
                "(budget_item_id, категория) и подсказками уточнения (FIN-122)."
            ),
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
            name="household_base_share",
            description=(
                "Базовая доля личных фондов (FIN-103, FIN-121, FIN-114): контуры household income, "
                "professional, shared fund, savings → free_remainder и base_share "
                "на партнёра. Interim mapping JSON; income_mode / overrides; "
                "convert_plans_to_eur (default true) через API FX."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {
                        "type": "string",
                        "description": "YYYY-MM — месяц базовой доли",
                    },
                    "budget_version_id": {"type": "string"},
                    "mapping_path": {
                        "type": "string",
                        "description": "Override пути к household-contour-mapping JSON",
                    },
                    "convert_plans_to_eur": {
                        "type": "boolean",
                        "description": (
                            "EUR plan amounts via plan-actual convert_to_eur (default true); "
                            "false = legacy grouped fetch"
                        ),
                    },
                    "income_mode": {
                        "type": "string",
                        "description": (
                            "Preset состава доходов: mapping_default, salary_only, "
                            "salary_plus_partner_contribution"
                        ),
                    },
                    "include_income_matches": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Доп. article_match для включения в household income",
                    },
                    "exclude_income_matches": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "article_match для исключения из household income",
                    },
                },
                "required": ["period"],
            },
        ),
        types.Tool(
            name="list_fx_rates",
            description=(
                "Плановые курсы RUB→EUR (FIN-114): GET /api/v1/fx-rates — список по period "
                "или period_from/period_to."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {"type": "string", "description": "YYYY-MM или YYYY-MM-DD"},
                    "period_from": {"type": "string", "description": "YYYY-MM — начало диапазона"},
                    "period_to": {"type": "string", "description": "YYYY-MM — конец диапазона"},
                    "from_currency": {"type": "string", "description": "Default RUB"},
                    "to_currency": {"type": "string", "description": "Default EUR"},
                },
            },
        ),
        types.Tool(
            name="upsert_fx_rate",
            description=(
                "Сохранить плановый курс RUB→EUR на месяц (FIN-114): PUT /api/v1/fx-rates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {
                        "type": "string",
                        "description": "YYYY-MM или YYYY-MM-DD",
                    },
                    "rate": {"type": "string", "description": "Плановый курс (> 0)"},
                    "from_currency": {"type": "string", "description": "Default RUB"},
                    "to_currency": {"type": "string", "description": "Default EUR"},
                },
                "required": ["period", "rate"],
            },
        ),
        types.Tool(
            name="household_advances",
            description=(
                "Журнал авансов на базовые потребности (FIN-115): register, list, void, "
                "mark_deducted. Interim JSON ledger per profile; не проверяет остаток "
                "личного фонда и статус закрытия месяца."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "action": {
                        "type": "string",
                        "enum": ["register", "list", "void", "mark_deducted"],
                    },
                    "partner_id": {"type": "string"},
                    "issue_period": {"type": "string", "description": "YYYY-MM"},
                    "deduct_in_period": {
                        "type": "string",
                        "description": "YYYY-MM — фильтр list",
                    },
                    "amount": {"type": "number", "description": "EUR, register only"},
                    "note": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["open", "deducted", "void"],
                        "description": "Фильтр list",
                    },
                    "id": {"type": "string", "description": "Entry id для void"},
                    "reason": {"type": "string", "description": "Причина void"},
                },
                "required": ["action"],
            },
        ),
        types.Tool(
            name="personal_fund_carryover",
            description=(
                "Перенос остатков/перерасхода личного фонда после FINAL close (FIN-105): "
                "carryover, advance_deduction, available_personal_fund при target_period; "
                "persist carryover log и mark_deducted для open advances issue_period=closed_period."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "closed_period": {
                        "type": "string",
                        "description": "YYYY-MM — закрытый учётный месяц",
                    },
                    "target_period": {
                        "type": "string",
                        "description": (
                            "YYYY-MM — месяц для available_personal_fund; "
                            "omit → target_period: null в ответе"
                        ),
                    },
                    "budget_version_id": {"type": "string"},
                    "mapping_path": {
                        "type": "string",
                        "description": "Override пути к household-contour-mapping JSON",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Только расчёт; без persist log и mark_deducted",
                    },
                    "mark_advances_deducted": {
                        "type": "boolean",
                        "description": "После persist log — mark_deducted (default true)",
                    },
                    "allow_non_final": {
                        "type": "boolean",
                        "description": "Разрешить non-final methodology_status с warning",
                    },
                    "incoming_carryover_override": {
                        "type": "object",
                        "description": (
                            "partner_id → EUR; обязателен для closed_period=2026-06 без prior log"
                        ),
                        "additionalProperties": {"type": "number"},
                    },
                },
                "required": ["closed_period"],
            },
        ),
        types.Tool(
            name="money_check_report",
            description=(
                "Еженедельный household money check (FIN-104): остатки личных фондов, "
                "methodology prior/check month, C9999/? counts, advances, receivables, "
                "carryover (log или dry_run FIN-105). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "check_period": {
                        "type": "string",
                        "description": "YYYY-MM — месяц лимита и факта трат (default UTC month)",
                    },
                    "prior_period": {
                        "type": "string",
                        "description": "YYYY-MM — prior month для methodology/carryover",
                    },
                    "as_of_period": {
                        "type": "string",
                        "description": "YYYY-MM — ref month для stale advances / overdue",
                    },
                    "budget_version_id": {"type": "string"},
                    "mapping_path": {
                        "type": "string",
                        "description": "Override пути к household-contour-mapping JSON",
                    },
                    "include_advance_breakdown": {
                        "type": "boolean",
                        "description": "Включить advances.totals_by_issue_period (default true)",
                    },
                },
            },
        ),
        types.Tool(
            name="household_receivables",
            description=(
                "Журнал займов третьим лицам / дебиторка (FIN-116): register, record_repayment, "
                "list, extend, write_off, mark_gift. Interim JSON ledger per profile."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "action": {
                        "type": "string",
                        "enum": [
                            "register",
                            "record_repayment",
                            "list",
                            "extend",
                            "write_off",
                            "mark_gift",
                        ],
                    },
                    "lender_id": {"type": "string"},
                    "borrower_label": {"type": "string"},
                    "amount": {"type": "number", "description": "EUR, register only"},
                    "source": {
                        "type": "string",
                        "enum": ["personal", "shared"],
                    },
                    "issue_period": {"type": "string", "description": "YYYY-MM"},
                    "due_period": {"type": "string", "description": "YYYY-MM"},
                    "new_due_period": {
                        "type": "string",
                        "description": "YYYY-MM, extend only",
                    },
                    "receipt_period": {
                        "type": "string",
                        "description": "YYYY-MM, record_repayment only",
                    },
                    "as_of_period": {
                        "type": "string",
                        "description": "YYYY-MM для is_overdue на list",
                    },
                    "note": {"type": "string"},
                    "transaction_key": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["open", "repaid", "written_off", "gift"],
                        "description": "Фильтр list",
                    },
                    "id": {"type": "string", "description": "Entry id"},
                    "reason": {"type": "string", "description": "write_off / mark_gift audit"},
                },
                "required": ["action"],
            },
        ),
        types.Tool(
            name="query_transactions",
            description=(
                "Выборка транзакций (GET /api/v1/transactions) с фильтрами учётного периода, "
                "категории и group-by month. Неагрегированные rows включают id и transaction_type "
                "(для put_transaction_category)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {
                        "type": "string",
                        "description": "Учётный месяц YYYY-MM (mutually exclusive с accounting_period)",
                    },
                    "accounting_period": {
                        "type": "string",
                        "description": "Учётный месяц YYYY-MM или YYYYMM (REST-aligned alias)",
                    },
                    "date_from": {"type": "string"},
                    "date_to": {"type": "string"},
                    "indicator": {"type": "string", "enum": ["D", "C"]},
                    "category": {
                        "type": "string",
                        "description": "transaction_category, напр. C9999",
                    },
                    "transaction_category": {
                        "type": "string",
                        "description": "Alias для category",
                    },
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
            name="put_transaction_overrides",
            description=(
                "PUT reconciliation overrides: transaction_key → budget_item_id для месяца. "
                "При 422 (budget_item без plan-item в ACT) — подсказка create_plan_item. "
                "Опционально derive после записи (default true)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "period": {"type": "string", "description": "YYYY-MM"},
                    "overrides": {
                        "type": "object",
                        "description": "transaction_key → budget_item_id",
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "Merge with existing overrides (default true). false = destructive full replace of persisted map",
                    },
                    "derive": {
                        "type": "boolean",
                        "description": "Run derive after PUT (default true)",
                    },
                },
                "required": ["period", "overrides"],
            },
        ),
        types.Tool(
            name="put_transaction_category",
            description=(
                "Коррекция transaction_type вместе с совместимой непустой "
                "transaction_category (PATCH …/category, FIN-202). "
                "category_source не передавать (implicit manual). "
                "Опционально reconciliation_note и allow_closed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "transaction_id": {
                        "type": "string",
                        "description": "UUID строки (query_transactions → id)",
                    },
                    "transaction_type": {
                        "type": "string",
                        "description": "C / P / S / I (strip; регистр нормализует API)",
                    },
                    "transaction_category": {
                        "type": "string",
                        "description": "Непустой id категории, совместимый с типом",
                    },
                    "reconciliation_note": {
                        "type": ["string", "null"],
                        "description": "Опционально; атомарно с type+category",
                    },
                    "allow_closed": {
                        "type": "boolean",
                        "description": "Bypass closed-period guard (default false)",
                    },
                },
                "required": [
                    "transaction_id",
                    "transaction_type",
                    "transaction_category",
                ],
            },
        ),
        types.Tool(
            name="upsert_expense_project",
            description="Создать или полностью заменить проект расходов (POST/PUT /api/v1/projects; full replace, no partial update).",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "project": {
                        "type": "object",
                        "description": "id, description, keywords, valid_from, valid_to",
                    },
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="create_category",
            description=(
                "Создать категорию транзакций в справочнике профиля "
                "(POST /api/v1/categories). Domain-валидация id/type/default — на API."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "id": {
                        "type": "string",
                        "description": "Id категории (напр. P0004)",
                    },
                    "type": {
                        "type": "string",
                        "description": "Тип: C, P, S или I",
                    },
                    "description": {
                        "type": "string",
                        "description": "Человекочитаемое имя",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Начальные keywords (default [])",
                    },
                    "default": {
                        "type": "boolean",
                        "description": (
                            "Флаг default-категории (default false; "
                            "true отклоняется API 422)"
                        ),
                    },
                },
                "required": ["id", "type", "description"],
            },
        ),
        types.Tool(
            name="create_budget_item",
            description=(
                "Создать статью бюджета и REG plan-item в ACT-версии "
                "(POST /budget/items + POST /budget/plan-items). "
                "Для RUB: задайте currency=RUB и номинал в ₽, затем upsert_fx_rate на месяц. "
                "Default recalculate=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "name": {"type": "string", "description": "Имя статьи"},
                    "flow_type": {"type": "string", "description": "EXP или INC"},
                    "operation_category_id": {
                        "type": "string",
                        "description": "Код категории (напр. C0006)",
                    },
                    "amount": {"type": ["string", "number"], "description": "Сумма REG plan (>= 0)"},
                    "start_period": {"type": "string", "description": "YYYY-MM — начало REG"},
                    "end_period": {"type": "string", "description": "YYYY-MM — конец REG (опц.)"},
                    "planning_type": {"type": "string", "description": "Только REG (default REG)"},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords статьи (пустой список допустим)",
                    },
                    "item_status": {
                        "type": "string",
                        "description": "budget_item.status (default ACT)",
                    },
                    "currency": {"type": "string", "description": "Валюта (default EUR)"},
                    "periodicity": {"type": "string", "description": "REG periodicity (default M)"},
                    "recalculate": {
                        "type": "boolean",
                        "description": "POST projections/recalculate (default true)",
                    },
                },
                "required": [
                    "name",
                    "flow_type",
                    "operation_category_id",
                    "amount",
                    "start_period",
                ],
            },
        ),
        types.Tool(
            name="create_plan_item",
            description=(
                "POST REG or IRR plan-item на существующую статью в ACT-версии. "
                "REG: start_period (+ optional end_period). "
                "IRR: forecast_method MAN/AVG (default MAN). "
                "planning_type infer из статьи. Default recalculate=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "article": {"type": "string", "description": "Подстрока имени статьи"},
                    "budget_item_id": {"type": "string", "description": "UUID статьи"},
                    "amount": {"type": ["string", "number"], "description": "Сумма plan-item (>= 0)"},
                    "start_period": {
                        "type": "string",
                        "description": "YYYY-MM — начало REG (обяз. для REG)",
                    },
                    "end_period": {
                        "type": "string",
                        "description": "YYYY-MM — конец REG (ops: start=end для one-off)",
                    },
                    "planning_type": {
                        "type": "string",
                        "description": "REG или IRR; default — из статьи",
                    },
                    "forecast_method": {
                        "type": "string",
                        "description": "IRR: MAN или AVG (default MAN)",
                    },
                    "currency": {"type": "string", "description": "Валюта (default EUR)"},
                    "periodicity": {"type": "string", "description": "REG periodicity (default M)"},
                    "recalculate": {
                        "type": "boolean",
                        "description": "POST projections/recalculate (default true)",
                    },
                },
                "required": ["amount"],
            },
        ),
        types.Tool(
            name="update_plan_item",
            description=(
                "Изменить plan-item: сумма и/или bounded horizon "
                "(PUT /budget/plan-items). Resolve по plan_item_id или "
                "article/budget_item_id + period. Default recalculate=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": PROFILE_SCHEMA,
                    "base": BASE_SCHEMA,
                    "plan_item_id": {"type": "string", "description": "UUID plan-item (приоритет над resolve)"},
                    "article": {"type": "string", "description": "Подстрока имени статьи"},
                    "budget_item_id": {"type": "string", "description": "UUID статьи"},
                    "period": {"type": "string", "description": "YYYY-MM для resolve по article"},
                    "amount": {"type": ["string", "number"], "description": "Новая сумма (>= 0)"},
                    "start_period": {"type": "string", "description": "YYYY-MM — новый start_date (опц.)"},
                    "end_period": {"type": "string", "description": "YYYY-MM — новый end_date (опц.)"},
                    "recalculate": {
                        "type": "boolean",
                        "description": "POST projections/recalculate после PUT (default true)",
                    },
                },
                "required": ["amount"],
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
        "list_c9999": _handle_list_c9999,
        "apply_keywords": _handle_apply_keywords,
        "verify_month": _handle_verify_month,
        "process_month": _handle_process_month,
        "fix_month": _handle_process_month,  # deprecated alias
        "query_plan_fact": _handle_query_plan_fact,
        "household_base_share": _handle_household_base_share,
        "list_fx_rates": _handle_list_fx_rates,
        "upsert_fx_rate": _handle_upsert_fx_rate,
        "household_advances": _handle_household_advances,
        "personal_fund_carryover": _handle_personal_fund_carryover,
        "money_check_report": _handle_money_check_report,
        "household_receivables": _handle_household_receivables,
        "put_transaction_overrides": _handle_put_transaction_overrides,
        "put_transaction_category": _handle_put_transaction_category,
        "upsert_expense_project": _handle_upsert_expense_project,
        "create_category": _handle_create_category,
        "create_budget_item": _handle_create_budget_item,
        "create_plan_item": _handle_create_plan_item,
        "update_plan_item": _handle_update_plan_item,
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
                server_version="1.5.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(run())
