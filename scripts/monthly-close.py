"""Monthly close pipeline via FinancePlanningProject REST API.

Resolves statement paths from kontoauszuege naming conventions (no manifest).
Close only with ``--close``; keywords only with ``--apply-keywords`` (see policies).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from monthly_close_lib import (
    CLOSE_PHASES,
    REPORT_SUBDIRS,
    REPORTS_ROOT,
    WORKING,
    apply_keywords_file,
    c9999_rows,
    close_period,
    connect_api,
    generate_reports,
    mc_affected_periods,
    parse_period,
    print_c9999_proposal,
    reopen_closed_periods,
    reopen_period,
    resolve_budget_version_id,
    run_derive,
    run_imports,
    verify_period,
    print_verify_report,
)

def run_pipeline(args: argparse.Namespace) -> int:
    """
    Execute monthly close pipeline.

    :param args: Parsed CLI arguments
    :return: Exit code
    """
    period = parse_period(args.period)
    profile = args.profile
    api, base = connect_api(args.base, profile)
    vid = resolve_budget_version_id(api, period)

    report_subdir = REPORT_SUBDIRS.get(profile)
    if report_subdir is None:
        raise ValueError(f"unknown profile {profile!r}")
    out_dir = REPORTS_ROOT / report_subdir / period.yyyy_mm
    log_path = WORKING / f"{profile}-{period.yyyy_mm}-close-log.json"

    log: dict = {
        "profile": profile,
        "period": period.ymmm,
        "base": base,
        "budget_version_id": vid,
        "imports": [],
        "steps": {},
    }

    print(f"=== {profile} {period.yyyy_mm} ({period.ymmm}) @ {base} ===")
    meta = api.get_json("/api/v1/meta")
    print("meta profile:", meta.get("data_profile"))
    if meta.get("data_profile") != profile:
        print(
            f"WARNING: server profile {meta.get('data_profile')!r} != {profile!r}",
            file=sys.stderr,
        )
    log["steps"]["meta"] = {"data_profile": meta.get("data_profile")}

    if args.reopen_neighbors:
        affected = mc_affected_periods(period)
        print(f"reopen-neighbors: {[p.yyyy_mm for p in affected]}")
        log["steps"]["reopen_neighbors"] = reopen_closed_periods(api, vid, affected)

    if args.reopen:
        status, body = reopen_period(api, vid, period)
        print("reopen:", status, body)
        log["steps"]["reopen"] = {"status": status, "body": body}

    if args.apply_keywords:
        added = apply_keywords_file(api, args.apply_keywords)
        print(f"keywords added: {len(added)}")
        for item in added:
            print(f"  {item['category']}: {item['keyword']}")
        log["steps"]["keywords_added"] = added

    if not args.skip_import:
        log["imports"] = run_imports(api, period)
        failed = [i for i in log["imports"] if i["status"] != 200]
        if failed:
            log["steps"]["import_blocked"] = failed
            log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
            print("STOP: import failed — см. blocked_accounting_periods в логе", file=sys.stderr)
            print("log:", log_path)
            return 1

    log["steps"]["derive"] = run_derive(api, period)

    verify = verify_period(api, period, vid)
    print_verify_report(verify, period)
    log["steps"]["verify"] = verify
    log["steps"]["classification_summary"] = verify["classification_summary"]

    c9999_count = int(verify["classification_summary"].get("expense_c9999_count") or 0)
    rows: list[dict] = []
    if c9999_count > 0:
        rows = c9999_rows(api, period)
    print(f"C9999 rows: {len(rows)}")
    log["steps"]["c9999_count"] = len(rows)
    log["steps"]["c9999_samples"] = [
        {"amount": r.get("amount"), "description": r.get("description")} for r in rows[:20]
    ]
    if rows:
        print_c9999_proposal(rows)

    readiness = verify["readiness"]
    log["steps"]["readiness"] = readiness

    generate_reports(api, period, out_dir, log)

    if rows and not args.apply_keywords:
        print("STOP: C9999 > 0 — apply keywords or fix manually before --close")
        log["steps"]["close"] = {"status": "skipped", "reason": "c9999_pending"}
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print("log:", log_path)
        return 1

    if not args.close:
        print("close: SKIPPED (no --close; see close-policy.md)")
        log["steps"]["close"] = {"status": "skipped", "reason": "no --close flag"}
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print("log:", log_path)
        return 0 if readiness.get("ready") else 1

    if not readiness.get("ready"):
        print("close: BLOCKED (readiness not ready)", file=sys.stderr)
        log["steps"]["close"] = {"status": "blocked", "reason": "readiness false"}
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        return 1

    close_status, close_body = close_period(
        api, vid, period, close_phase=args.close_phase
    )
    print(f"close ({args.close_phase}):", close_status, close_body)
    log["steps"]["close"] = {
        "status": close_status,
        "close_phase": args.close_phase,
        "body": close_body if isinstance(close_body, dict) else str(close_body),
    }
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print("log:", log_path)
    return 0 if close_status == 200 else 1


def build_parser() -> argparse.ArgumentParser:
    """
    Build CLI parser.

    :return: Argument parser
    """
    parser = argparse.ArgumentParser(
        description="Monthly close via API (import -> derive -> reports -> optional close)",
    )
    parser.add_argument(
        "--profile",
        required=True,
        choices=sorted(REPORT_SUBDIRS),
        help="Data profile (server must use FINANCE_DATA_PROFILE=<profile>)",
    )
    parser.add_argument(
        "--period",
        required=True,
        help="Calendar month YYYY-MM or YYYYMM",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="API base URL (default: scan ports 8000-8010 via discover_api_base)",
    )
    parser.add_argument(
        "--close",
        action="store_true",
        help="POST reconciliation/close (only after explicit user command)",
    )
    parser.add_argument(
        "--close-phase",
        choices=CLOSE_PHASES,
        default="final",
        help="Close phase: preliminary (phase 1) or final (phase 2, default)",
    )
    parser.add_argument(
        "--reopen",
        action="store_true",
        help="Reopen target period before pipeline",
    )
    parser.add_argument(
        "--reopen-neighbors",
        action="store_true",
        help="Reopen closed M-1/M/M+1 before MC import (tail-import collateral)",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Skip statement import (derive/readiness/close only)",
    )
    parser.add_argument(
        "--apply-keywords",
        type=Path,
        metavar="FILE",
        help="JSON {category_id: [keywords]} - only after user approved C9999 proposal",
    )
    return parser


def main() -> int:
    """
    CLI entry point.

    :return: Exit code
    """
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_pipeline(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
