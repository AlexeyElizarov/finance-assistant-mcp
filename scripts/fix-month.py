"""Orchestrated month fix: reopen → import → verify → derive → optional close.

Idempotent ops flow replacing ad-hoc ``python -c`` chains.
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
    print_verify_report,
    reopen_closed_periods,
    reopen_period,
    resolve_budget_version_id,
    run_derive,
    run_imports,
    verify_period,
)

def run_fix(args: argparse.Namespace) -> int:
    """
    Execute fix-month orchestration.

    :param args: Parsed CLI arguments
    :return: Exit code
    """
    period = parse_period(args.period)
    profile = args.profile
    api, base = connect_api(args.base, profile)
    vid = resolve_budget_version_id(api, period)

    report_subdir = REPORT_SUBDIRS[profile]
    out_dir = REPORTS_ROOT / report_subdir / period.yyyy_mm
    log_path = WORKING / f"{profile}-{period.yyyy_mm}-fix-log.json"

    log: dict = {
        "profile": profile,
        "period": period.ymmm,
        "base": base,
        "budget_version_id": vid,
        "mode": "verify-only" if args.verify_only else "fix",
        "imports": [],
        "steps": {},
    }

    print(f"=== fix-month {profile} {period.yyyy_mm} @ {base} ===")

    if args.verify_only:
        verify = verify_period(api, period, vid)
        print_verify_report(verify, period)
        log["steps"]["verify"] = verify
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print("log:", log_path)
        return 0 if verify["ok"] else 1

    if args.reopen_neighbors:
        affected = mc_affected_periods(period)
        print(f"reopen-neighbors: {[p.yyyy_mm for p in affected]}")
        log["steps"]["reopen_neighbors"] = reopen_closed_periods(api, vid, affected)

    if args.reopen:
        status, body = reopen_period(api, vid, period)
        print(f"reopen {period.yyyy_mm}: {status}")
        log["steps"]["reopen"] = {"status": status, "body": body}

    if not args.skip_import:
        log["imports"] = run_imports(api, period)
        failed = [i for i in log["imports"] if i["status"] != 200]
        if failed:
            log["steps"]["import_blocked"] = failed
            log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
            print(
                "STOP: import 422 — reopen blocked_accounting_periods из лога, затем повтор",
                file=sys.stderr,
            )
            for entry in failed:
                blocked = entry.get("blocked_accounting_periods")
                if blocked:
                    print(f"  {entry['provider']}: blocked={blocked}", file=sys.stderr)
            print("log:", log_path)
            return 1

    if args.apply_keywords:
        added = apply_keywords_file(api, args.apply_keywords)
        print(f"keywords added: {len(added)}")
        log["steps"]["keywords_added"] = added

    log["steps"]["derive"] = run_derive(api, period)

    verify = verify_period(api, period, vid)
    print_verify_report(verify, period)
    log["steps"]["verify"] = verify
    log["steps"]["classification_summary"] = verify["classification_summary"]
    log["steps"]["readiness"] = verify["readiness"]

    c9999_count = int(verify["classification_summary"].get("expense_c9999_count") or 0)
    rows: list[dict] = []
    if c9999_count > 0:
        rows = c9999_rows(api, period)
    if rows:
        print_c9999_proposal(rows)
        log["steps"]["c9999_count"] = len(rows)
        if not args.apply_keywords and args.close:
            print("STOP: C9999 > 0 — keywords + --skip-import перед close", file=sys.stderr)
            log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
            return 1

    if args.reports:
        generate_reports(api, period, out_dir, log)

    if not args.close:
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print("log:", log_path)
        return 0 if verify["ok"] else 1

    if not verify["readiness"].get("ready"):
        print("close: BLOCKED (readiness not ready)", file=sys.stderr)
        log["steps"]["close"] = {"status": "blocked", "reason": "readiness false"}
        log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        return 1

    close_status, close_body = close_period(
        api, vid, period, close_phase=args.close_phase
    )
    print(f"close ({args.close_phase}): {close_status}")
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
        description=(
            "Fix month: reopen-neighbors -> import -> derive -> verify -> optional close. "
            "Single derive after all imports."
        ),
    )
    parser.add_argument("--profile", required=True, choices=sorted(REPORT_SUBDIRS))
    parser.add_argument("--period", required=True, help="YYYY-MM or YYYYMM")
    parser.add_argument(
        "--base",
        default=None,
        help="API base (default: discover_api_base(profile=...))",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify only: MC from_17th, classification-summary, readiness",
    )
    parser.add_argument(
        "--reopen-neighbors",
        action="store_true",
        help="Reopen closed M-1, M, M+1 before import",
    )
    parser.add_argument("--reopen", action="store_true", help="Reopen target month M")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--apply-keywords", type=Path, metavar="FILE")
    parser.add_argument(
        "--close",
        action="store_true",
        help="Close after verify (explicit user command only)",
    )
    parser.add_argument(
        "--close-phase",
        choices=CLOSE_PHASES,
        default="final",
        help="preliminary or final (default: final)",
    )
    parser.add_argument(
        "--reports",
        action="store_true",
        help="Generate report PDFs",
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
        return run_fix(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
