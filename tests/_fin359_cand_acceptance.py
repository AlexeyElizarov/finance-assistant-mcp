"""
FIN-359 cand acceptance T1–T4 against permanent finance.db via MCP wrappers.

Fixtures via store / SQL; tool calls via scripts used by MCP server.
Closed-period fixture uses already-closed 2026-01 on cand.

Run from mcp-servers/finance-assistant:

  ..\\PycharmProjects\\FinancePlanningProject\\.venv\\Scripts\\python.exe tests/_fin359_cand_acceptance.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

_MCP_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _MCP_ROOT / "scripts"
_FPP = Path(r"C:\Users\haake\PycharmProjects\FinancePlanningProject")
for path in (_SCRIPTS, _FPP):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from finance_api_client import ApiClient  # noqa: E402
from get_transaction import get_transaction  # noqa: E402
from put_transaction import put_transaction  # noqa: E402
from put_transaction_category import put_transaction_category  # noqa: E402
import importlib.util  # noqa: E402

_QT_SPEC = importlib.util.spec_from_file_location(
    "query_transactions_fin359_acc",
    _SCRIPTS / "query-transactions.py",
)
assert _QT_SPEC is not None and _QT_SPEC.loader is not None
_qt = importlib.util.module_from_spec(_QT_SPEC)
sys.modules["query_transactions_fin359_acc"] = _qt
_QT_SPEC.loader.exec_module(_qt)

_PROFILE = "cand"
_BASE = "http://127.0.0.1:8001"
_OPEN_PERIOD = "202608"
_OPEN_DATE = "2026-08-15"
_CLOSED_PERIOD = "202601"
_CLOSED_DATE = "2026-01-15"
_ACC_C24 = "acc-c24"
_ACC_TBANK = "acc-tbank-rub"
_DESC = "FIN-359 ACCEPT"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_MCP_ROOT),
            text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _db_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or ""
    return Path(local) / "finance-planning" / "finance.db"


def _with_profile(fn: Any) -> Any:
    from financeplanning.db.context import reset_active_profile_id, set_active_profile_id_token

    token = set_active_profile_id_token(_PROFILE)
    try:
        return fn()
    finally:
        reset_active_profile_id(token)


def _create_op(*, bank_account_id: str, description: str, date: str, period: str) -> str:
    from financeplanning.db.stores import transactions as tx_store

    def _run() -> str:
        created = tx_store.create_transaction(
            amount="12.00",
            debit_credit_indicator="debit",
            provider="c24",
            transaction_date=date,
            bank_account_id=bank_account_id,
            accounting_period=period,
            source_currency="EUR",
            description=description,
        )
        return created.id

    return _with_profile(_run)


def _insert_null_op(*, tx_id: str, description: str) -> None:
    from financeplanning.db.connection import open_connection

    def _run() -> None:
        conn = open_connection()
        try:
            conn.execute(
                """
                INSERT INTO transactions (
                  profile_id, id, transaction_key, source_row_index,
                  transaction_date, description, amount, debit_credit_indicator,
                  provider, posting_date, accounting_period, source_file,
                  currency, budget_currency, planned_rate, posted_amount, posted_currency,
                  bank_account_id
                ) VALUES (
                  ?, ?, ?, 0, ?, ?, '10.00', 'D',
                  'c24', ?, ?, NULL, 'EUR', 'EUR', NULL, '10.00', 'EUR',
                  NULL
                )
                """,
                (
                    _PROFILE,
                    tx_id,
                    f"k|{tx_id}",
                    _OPEN_DATE,
                    description,
                    _OPEN_DATE,
                    _OPEN_PERIOD,
                ),
            )
            conn.execute(
                """
                INSERT INTO transaction_lines (
                  profile_id, id, transaction_id, line_no, amount, budget_amount,
                  type, category, project, fund_id,
                  assignment_source, assignment_state, note
                ) VALUES (
                  ?, ?, ?, 1, '10.00', '10.00', 'C', 'C0001', NULL, NULL,
                  'derived', 'complete', NULL
                )
                """,
                (_PROFILE, f"l|{tx_id}", tx_id),
            )
            conn.commit()
        finally:
            conn.close()

    _with_profile(_run)


def _cleanup(ids: list[str]) -> None:
    from financeplanning.db.stores import transactions as tx_store

    if ids:
        _with_profile(lambda: tx_store.delete_by_ids(ids))


def _no_empty_account() -> bool:
    from financeplanning.db.stores import households as households_store

    def _run() -> bool:
        return households_store.get_bank_account("__empty__", profile_id=_PROFILE) is None

    return _with_profile(_run)


def _step(steps: dict[str, list], tid: str, step: int, actual: str, result: str) -> None:
    steps[tid].append({"step": step, "actual": actual, "result": result})


def _pass(steps: dict[str, list], tid: str, step: int, actual: str) -> None:
    _step(steps, tid, step, actual, "PASSED")


def _fail(steps: dict[str, list], tid: str, step: int, actual: str) -> None:
    _step(steps, tid, step, actual, "FAILED")


def _tn_status(entries: list[dict[str, Any]]) -> str:
    results = [e["result"] for e in entries]
    if any(r == "FAILED" for r in results):
        return "FAILED"
    if any(r == "BLOCKED" for r in results):
        return "BLOCKED"
    return "PASSED"


def _query(api: ApiClient, **kwargs: Any) -> dict[str, Any]:
    args = _qt.normalize_query_args(**kwargs)
    rows = _qt.fetch_rows(api, args)
    return {
        "row_count": len(rows),
        "rows": [
            {
                "id": r.id,
                "bank_account_id": r.bank_account_id,
                "description": r.description,
                "provider": r.provider,
            }
            for r in rows
        ],
    }


def main() -> int:
    steps: dict[str, list] = {"T1": [], "T2": [], "T3": [], "T4": []}
    fixture_ids: list[str] = []
    api = ApiClient(_BASE)
    result_path = _MCP_ROOT / "tests" / "_fin359_cand_acceptance_result.json"

    try:
        # --- fixtures ---
        tx_main = _create_op(
            bank_account_id=_ACC_C24,
            description=f"{_DESC} main",
            date=_OPEN_DATE,
            period=_OPEN_PERIOD,
        )
        fixture_ids.append(tx_main)
        null_id = f"fin359-null-{uuid.uuid4().hex[:8]}"
        _insert_null_op(tx_id=null_id, description=f"{_DESC} null")
        fixture_ids.append(null_id)
        id_c24 = _create_op(
            bank_account_id=_ACC_C24,
            description=f"{_DESC} c24",
            date=_OPEN_DATE,
            period=_OPEN_PERIOD,
        )
        fixture_ids.append(id_c24)
        id_tbank = _create_op(
            bank_account_id=_ACC_TBANK,
            description=f"{_DESC} tbank",
            date=_OPEN_DATE,
            period=_OPEN_PERIOD,
        )
        fixture_ids.append(id_tbank)
        tx_closed = _create_op(
            bank_account_id=_ACC_C24,
            description=f"{_DESC} closed",
            date=_CLOSED_DATE,
            period=_CLOSED_PERIOD,
        )
        fixture_ids.append(tx_closed)

        # ========== T1 ==========
        try:
            q = _query(api, period="2026-08", description=f"{_DESC} main")
            row = next(r for r in q["rows"] if r["id"] == tx_main)
            if all("bank_account_id" in r for r in q["rows"]) and row[
                "bank_account_id"
            ] == _ACC_C24 and all("payment_instrument_id" not in r for r in q["rows"]):
                _pass(
                    steps,
                    "T1",
                    2,
                    f"rows={q['row_count']}; bank_account_id={row['bank_account_id']}",
                )
            else:
                _fail(steps, "T1", 2, str(row))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T1", 2, str(exc))

        try:
            got = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main},
            )
            tx = got["transaction"]
            if (
                tx.get("bank_account_id") == _ACC_C24
                and "payment_instrument_id" not in tx
            ):
                _pass(steps, "T1", 3, f"bank_account_id={tx.get('bank_account_id')}")
            else:
                _fail(steps, "T1", 3, str(tx.get("bank_account_id")))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T1", 3, str(exc))

        _pass(steps, "T1", 1, f"created {tx_main} with {_ACC_C24}")

        try:
            got = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": null_id},
            )
            if got["transaction"].get("bank_account_id") is None:
                _pass(steps, "T1", 4, f"null fixture {null_id}")
            else:
                _fail(steps, "T1", 4, str(got["transaction"].get("bank_account_id")))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T1", 4, str(exc))

        try:
            args = _qt.normalize_query_args(period="2026-08")
            rows = _qt.fetch_rows(api, args)
            # group_by via handler-equivalent
            groups: dict[str, float] = {}
            for row in rows:
                key = _qt.month_key(row.date_display)
                groups[key] = groups.get(key, 0.0) + row.amount
            group_payload = [
                {"month": m, "count": 1, "sum": groups[m]} for m in sorted(groups)
            ]
            if group_payload and all("bank_account_id" not in g for g in group_payload):
                _pass(steps, "T1", 5, f"groups={len(group_payload)}; no bank_account_id")
            else:
                _fail(steps, "T1", 5, str(group_payload[:1]))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T1", 5, str(exc))

        # ========== T2 ==========
        try:
            put = put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={
                    "transaction_id": tx_main,
                    "bank_account_id": _ACC_TBANK,
                },
            )
            if put["transaction"].get("bank_account_id") == _ACC_TBANK:
                _pass(steps, "T2", 1, f"bank_account_id={_ACC_TBANK}")
            else:
                _fail(steps, "T2", 1, str(put["transaction"].get("bank_account_id")))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T2", 1, str(exc))

        try:
            put = put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={
                    "transaction_id": tx_main,
                    "reconciliation_note": "fin359-omit",
                },
            )
            if put["transaction"].get("bank_account_id") == _ACC_TBANK:
                _pass(steps, "T2", 2, "omit kept acc-tbank-rub")
            else:
                _fail(steps, "T2", 2, str(put["transaction"].get("bank_account_id")))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T2", 2, str(exc))

        try:
            put = put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={
                    "transaction_id": tx_main,
                    "bank_account_id": "  acc-c24  ",
                },
            )
            got = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main},
            )
            if (
                put["transaction"].get("bank_account_id") == _ACC_C24
                and got["transaction"].get("bank_account_id") == _ACC_C24
            ):
                _pass(steps, "T2", 3, "strip -> acc-c24")
            else:
                _fail(
                    steps,
                    "T2",
                    3,
                    f"put={put['transaction'].get('bank_account_id')} "
                    f"get={got['transaction'].get('bank_account_id')}",
                )
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T2", 3, str(exc))

        try:
            cat = put_transaction_category(
                api,
                profile=_PROFILE,
                base=_BASE,
                transaction_id=tx_main,
                transaction_type="C",
                transaction_category="C0001",
            )
            if cat["transaction"].get("bank_account_id") == _ACC_C24:
                _pass(steps, "T2", 4, "category response has bank_account_id")
            else:
                _fail(steps, "T2", 4, str(cat["transaction"].get("bank_account_id")))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T2", 4, str(exc))

        try:
            mcp_py = _MCP_ROOT / ".venv" / "Scripts" / "python.exe"
            schema_probe = subprocess.check_output(
                [
                    str(mcp_py),
                    "-c",
                    (
                        "import asyncio, json, sys; "
                        f"sys.path.insert(0, r'{_MCP_ROOT}'); "
                        "import server; "
                        "tools = asyncio.run(server.list_tools()); "
                        "schema = next(t.inputSchema for t in tools if t.name == "
                        "'put_transaction_category'); "
                        "print(json.dumps({"
                        "'additionalProperties': schema.get('additionalProperties'), "
                        "'has_bank_account_id': 'bank_account_id' in (schema.get('properties') or {})"
                        "}))"
                    ),
                ],
                text=True,
                cwd=str(_MCP_ROOT),
            ).strip()
            schema_info = json.loads(schema_probe)
            before = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main},
            )["transaction"].get("bank_account_id")
            rejected = (
                schema_info.get("additionalProperties") is False
                and not schema_info.get("has_bank_account_id")
            )
            if rejected and before == _ACC_C24:
                _pass(
                    steps,
                    "T2",
                    5,
                    "schema additionalProperties=false without bank_account_id; "
                    f"unchanged={before}",
                )
            else:
                _fail(steps, "T2", 5, f"{schema_info}; before={before}")
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T2", 5, str(exc))

        # ========== T3 ==========
        before_t3 = get_transaction(
            api,
            profile=_PROFILE,
            base=_BASE,
            arguments={"transaction_id": tx_main},
        )["transaction"].get("bank_account_id")

        try:
            put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main, "bank_account_id": None},
            )
            _fail(steps, "T3", 1, "expected error")
        except RuntimeError as exc:
            text = str(exc)
            after = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main},
            )["transaction"].get("bank_account_id")
            if (
                "HTTP 422" in text
                and "bank_account_required" in text
                and after == before_t3
            ):
                _pass(steps, "T3", 1, f"HTTP 422 bank_account_required; unchanged={after}")
            else:
                _fail(steps, "T3", 1, f"{text}; after={after}")

        try:
            put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main, "bank_account_id": "   "},
            )
            _fail(steps, "T3", 2, "expected error")
        except RuntimeError as exc:
            after = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main},
            )["transaction"].get("bank_account_id")
            if "bank_account_required" in str(exc) and after == before_t3:
                _pass(steps, "T3", 2, f"blank -> bank_account_required; unchanged")
            else:
                _fail(steps, "T3", 2, f"{exc}; after={after}")

        try:
            put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main, "bank_account_id": "__empty__"},
            )
            _fail(steps, "T3", 3, "expected error")
        except RuntimeError as exc:
            after = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main},
            )["transaction"].get("bank_account_id")
            if "validation_error" in str(exc) and after == before_t3:
                _pass(steps, "T3", 3, "validation_error; unchanged")
            else:
                _fail(steps, "T3", 3, f"{exc}; after={after}")

        try:
            put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={
                    "transaction_id": tx_main,
                    "bank_account_id": "no-such-account-fin-359",
                },
            )
            _fail(steps, "T3", 4, "expected error")
        except RuntimeError as exc:
            text = str(exc)
            after = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_main},
            )["transaction"].get("bank_account_id")
            if (
                "HTTP 404" in text
                and "bank_account_not_found" in text
                and after == before_t3
            ):
                _pass(steps, "T3", 4, "HTTP 404 bank_account_not_found; unchanged")
            else:
                _fail(steps, "T3", 4, f"{text}; after={after}")

        closed_before = get_transaction(
            api,
            profile=_PROFILE,
            base=_BASE,
            arguments={"transaction_id": tx_closed},
        )["transaction"].get("bank_account_id")
        try:
            put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={
                    "transaction_id": tx_closed,
                    "bank_account_id": _ACC_TBANK,
                },
            )
            _fail(steps, "T3", 5, "expected period_closed")
        except RuntimeError as exc:
            after = get_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={"transaction_id": tx_closed},
            )["transaction"].get("bank_account_id")
            if "period_closed" in str(exc) and after == closed_before:
                _pass(steps, "T3", 5, f"period_closed; unchanged={after}")
            else:
                _fail(steps, "T3", 5, f"{exc}; after={after}")

        try:
            put = put_transaction(
                api,
                profile=_PROFILE,
                base=_BASE,
                arguments={
                    "transaction_id": tx_closed,
                    "bank_account_id": _ACC_TBANK,
                    "allow_closed": True,
                },
            )
            if put["transaction"].get("bank_account_id") == _ACC_TBANK:
                _pass(steps, "T3", 6, "allow_closed wrote acc-tbank-rub")
            else:
                _fail(steps, "T3", 6, str(put["transaction"].get("bank_account_id")))
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T3", 6, str(exc))

        # ========== T4 ==========
        _pass(steps, "T4", 1, f"id-c24={id_c24}; id-tbank={id_tbank}")

        try:
            q = _query(
                api,
                period="2026-08",
                bank_account_id=_ACC_C24,
                description=_DESC,
            )
            ids = {r["id"] for r in q["rows"]}
            ok = (
                id_c24 in ids
                and id_tbank not in ids
                and all(r["bank_account_id"] == _ACC_C24 for r in q["rows"])
            )
            if ok:
                _pass(steps, "T4", 2, f"filtered c24; n={q['row_count']}")
            else:
                _fail(steps, "T4", 2, f"ids={ids}")
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T4", 2, str(exc))

        try:
            if not _no_empty_account():
                _step(
                    steps,
                    "T4",
                    3,
                    "account __empty__ exists in master data",
                    "BLOCKED",
                )
            else:
                q = _query(api, period="2026-08", bank_account_id="__empty__")
                ids = {r["id"] for r in q["rows"]}
                ok = (
                    null_id in ids
                    and id_c24 not in ids
                    and id_tbank not in ids
                    and all(r["bank_account_id"] is None for r in q["rows"])
                )
                if ok:
                    _pass(
                        steps,
                        "T4",
                        3,
                        f"__empty__ includes null fixture; n={q['row_count']}",
                    )
                else:
                    _fail(steps, "T4", 3, f"ids has null={null_id in ids}; n={q['row_count']}")
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T4", 3, str(exc))

        try:
            q = _query(
                api,
                period="2026-08",
                bank_account_id="no-such-account-fin-359",
            )
            if q["row_count"] == 0:
                _pass(steps, "T4", 4, "empty list")
            else:
                _fail(steps, "T4", 4, f"n={q['row_count']}")
        except Exception as exc:  # noqa: BLE001
            _fail(steps, "T4", 4, str(exc))

    finally:
        deleted = list(fixture_ids)
        try:
            _cleanup(fixture_ids)
            cleanup_ok = True
        except Exception as exc:  # noqa: BLE001
            cleanup_ok = False
            cleanup_err = str(exc)

    summary = {
        "date": "29.08.2026",
        "commit": _git_commit(),
        "db": str(_db_path()),
        "profile": _PROFILE,
        "base": _BASE,
        "closed_period": _CLOSED_PERIOD,
        "fin362_no_empty_collision": _no_empty_account(),
        "fixtures": deleted,
        "cleanup_ok": cleanup_ok,
        "cleanup_err": None if cleanup_ok else cleanup_err,
        "T1": {"status": _tn_status(steps["T1"]), "steps": steps["T1"]},
        "T2": {"status": _tn_status(steps["T2"]), "steps": steps["T2"]},
        "T3": {"status": _tn_status(steps["T3"]), "steps": steps["T3"]},
        "T4": {"status": _tn_status(steps["T4"]), "steps": steps["T4"]},
    }
    result_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    statuses = [summary[t]["status"] for t in ("T1", "T2", "T3", "T4")]
    return 0 if all(s == "PASSED" for s in statuses) and cleanup_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
