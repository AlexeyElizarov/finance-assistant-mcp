"""
FIN-280 cand acceptance T1–T5: MCP modules against permanent finance.db via TestClient.

Live uvicorn on :8001 currently returns HTTP 500 for personal-fund-carryover;
this script uses in-process TestClient (same approach as FIN-279) plus MCP compute
entry points.

Run from mcp-servers/finance-assistant:
  python tests/_fin280_cand_acceptance.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

_MCP_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _MCP_ROOT / "scripts"
_REPO = Path(r"C:\Users\haake\PycharmProjects\FinancePlanningProject")
for path in (_SCRIPTS, _REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

_PROVIDER = "ACCEPT-FIN280"
_PROFILE = "cand"
_BUDGET_VERSION_ID = "d008ce16-03b1-434a-839a-26a51b72e204"

MAPPING = {
    "schema_version": 1,
    "profile": "cand",
    "partners": [
        {"id": "aleksei", "display_name": "Алексей"},
        {"id": "nikolay", "display_name": "Николай"},
    ],
    "legacy_irr_sanity": [{"article_match": "Кафе и рестораны"}],
    "personal_subscriptions_sanity": [{"article_match": "ChatGPT"}],
    "account_attribution": {
        "default_partner_by_provider": {"c24": "aleksei"},
        "description_overrides": [],
    },
}


class TestClientApi:
    """Minimal ApiClient-compatible wrapper over FastAPI TestClient."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.base = "http://testserver"

    def get_json(self, path: str) -> dict[str, Any]:
        resp = self._client.get(path)
        body = resp.json()
        if resp.status_code >= 400:
            raise RuntimeError(f"GET {path} -> HTTP {resp.status_code}: {body}")
        if not isinstance(body, dict):
            raise RuntimeError(f"GET {path}: expected object")
        return body

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        method_u = method.upper()
        if method_u == "GET":
            resp = self._client.get(path)
        elif method_u == "PUT":
            resp = self._client.put(path, json=data or {})
        elif method_u == "POST":
            resp = self._client.post(path, json=data or {})
        else:
            raise AssertionError(f"unsupported method {method}")
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
        return resp.status_code, body


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_MCP_ROOT),
            text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _step(steps: dict[str, list], tid: str, step: int, actual: str, result: str) -> None:
    steps[tid].append({"step": step, "actual": actual, "result": result})


def _pass(steps: dict[str, list], tid: str, step: int, actual: str) -> None:
    _step(steps, tid, step, actual, "PASSED")


def _fail(steps: dict[str, list], tid: str, step: int, actual: str) -> None:
    _step(steps, tid, step, actual, "FAILED")


def _cleanup() -> None:
    from financeplanning.db.stores._helpers import open_store_connection

    conn = open_store_connection()
    try:
        conn.execute(
            "DELETE FROM transactions WHERE profile_id = ? AND provider = ?",
            (_PROFILE, _PROVIDER),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_op(
    *,
    debit_credit: str,
    amount: str,
    accounting_period: str,
    fund_id: str | None,
    category: str = "C0001",
    line_type: str = "C",
    posting_date: str = "2026-03-15",
) -> tuple[str, str]:
    from financeplanning.db.stores._helpers import open_store_connection

    tx_id = str(uuid.uuid4())
    line_id = str(uuid.uuid4())
    key = f"fin280-accept|{tx_id}"
    conn = open_store_connection()
    try:
        conn.execute(
            """
            INSERT INTO transactions (
              profile_id, id, transaction_key, source_row_index,
              transaction_date, description, amount, debit_credit_indicator,
              provider, posting_date, accounting_period, source_file
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                _PROFILE,
                tx_id,
                key,
                posting_date,
                "fin-280-accept",
                amount,
                debit_credit,
                _PROVIDER,
                posting_date,
                accounting_period,
            ),
        )
        conn.execute(
            """
            INSERT INTO transaction_lines (
              profile_id, id, transaction_id, line_no, amount,
              type, category, project, fund_id,
              assignment_source, assignment_state, note
            ) VALUES (?, ?, ?, 1, ?, ?, ?, NULL, ?, 'manual', 'complete', NULL)
            """,
            (
                _PROFILE,
                line_id,
                tx_id,
                amount,
                line_type,
                category,
                fund_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return tx_id, line_id


def _ensure_funds() -> tuple[str, str, str]:
    from financeplanning.db.stores import funds as funds_store

    shared_id = "shared"
    personal_a = "personal-elizarov"
    personal_b = "personal-dubrovskii"
    for fund_id in (shared_id, personal_a, personal_b):
        if funds_store.get_fund(fund_id, profile_id=_PROFILE) is None:
            raise RuntimeError(f"cand fund missing: {fund_id}")
    return shared_id, personal_a, personal_b


def _get_http_carryover(client: Any, *, closed_period: str) -> tuple[int, dict[str, Any]]:
    resp = client.get(
        "/api/v1/household/personal-fund-carryover",
        params={
            "closed_period": closed_period,
            "allow_non_final": "true",
            "incoming_carryover": json.dumps({"aleksei": 0, "nikolay": 0}),
        },
    )
    return resp.status_code, resp.json()


def _mcp_carryover(
    api: TestClientApi,
    *,
    closed_period: str,
    mapping_path: str,
    target_period: str | None = None,
) -> dict[str, Any]:
    from personal_fund_carryover import compute_personal_fund_carryover

    return compute_personal_fund_carryover(
        api,
        profile=_PROFILE,
        base=api.base,
        closed_period=closed_period,
        budget_version_id=_BUDGET_VERSION_ID,
        target_period=target_period,
        mapping_path=mapping_path,
        dry_run=True,
        mark_advances_deducted=False,
        allow_non_final=True,
        incoming_carryover_override={"aleksei": 0.0, "nikolay": 0.0},
    )


def _mcp_money_check(
    api: TestClientApi,
    *,
    check_period: str,
    prior_period: str,
    mapping_path: str,
    carryover_log_path: Path,
) -> dict[str, Any]:
    from money_check_report import compute_money_check_report

    return compute_money_check_report(
        api,
        profile=_PROFILE,
        base=api.base,
        budget_version_id=_BUDGET_VERSION_ID,
        check_period=check_period,
        prior_period=prior_period,
        mapping_path=mapping_path,
        carryover_log_path=carryover_log_path,
    )


def main() -> int:
    from fastapi.testclient import TestClient

    from financeplanning.db.connection import resolve_db_path
    from financeplanning.db.context import set_active_profile_id_token
    from financeplanning.db.migrate import migrate
    from financeplanning.db.stores import expense_settlements as settlements_store
    from web.app import app

    commit = _git_commit()
    db_path = resolve_db_path()
    print(f"DB: {db_path}")
    print(f"MCP commit: {commit}")
    migrate()

    steps: dict[str, list] = {
        "T1": [],
        "T2": [],
        "T3": [],
        "T4": [],
        "T5": [],
    }
    env = {"FINANCE_DATA_PROFILE": _PROFILE}
    token = set_active_profile_id_token(_PROFILE)
    tmp = tempfile.TemporaryDirectory()
    mapping_path = Path(tmp.name) / "household-contour-mapping.cand.json"
    mapping_path.write_text(json.dumps(MAPPING), encoding="utf-8")
    empty_log = Path(tmp.name) / "personal-fund-carryover.cand.json"
    empty_log.write_text(
        json.dumps({"schema_version": 1, "profile": "cand", "runs": []}),
        encoding="utf-8",
    )
    log_with_run = Path(tmp.name) / "personal-fund-carryover-log.cand.json"
    log_with_run.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": "cand",
                "runs": [
                    {
                        "closed_period": "2026-01",
                        "target_period": "2026-02",
                        "computed_at": "2026-02-01T00:00:00Z",
                        "partners": {
                            "aleksei": {
                                "carryover": 10.0,
                                "advance_deduction": 0.0,
                                "overrun_amount": 0.0,
                            },
                            "nikolay": {
                                "carryover": 0.0,
                                "advance_deduction": 0.0,
                                "overrun_amount": 0.0,
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        with mock.patch.dict(os.environ, env, clear=False):
            client = TestClient(app)
            api = TestClientApi(client)
            _cleanup()
            shared, personal_a, personal_b = _ensure_funds()

            # --- T1 different funds ---
            period_ym = "2026-03"
            period = "202603"
            try:
                _, groceries = _insert_op(
                    debit_credit="debit",
                    amount="100.00",
                    accounting_period=period,
                    fund_id=shared,
                    category="C0001",
                    posting_date="2026-03-15",
                )
                _, chemicals = _insert_op(
                    debit_credit="debit",
                    amount="40.00",
                    accounting_period=period,
                    fund_id=shared,
                    category="C0006",
                    posting_date="2026-03-15",
                )
                _, credit = _insert_op(
                    debit_credit="credit",
                    amount="140.00",
                    accounting_period=period,
                    fund_id=personal_a,
                    category="I0001",
                    line_type="I",
                    posting_date="2026-03-15",
                )
                s1 = settlements_store.create_expense_settlement(
                    credit, groceries, "100.00"
                )
                s2 = settlements_store.create_expense_settlement(
                    credit, chemicals, "40.00"
                )
                _pass(
                    steps,
                    "T1",
                    1,
                    f"settlements {s1.id},{s2.id}; period {period}",
                )
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T1", 1, f"error: {exc}")
                s1 = s2 = None  # type: ignore[assignment]

            mcp_body: dict[str, Any] = {}
            http_body: dict[str, Any] = {}
            try:
                mcp_body = _mcp_carryover(
                    api, closed_period=period_ym, mapping_path=str(mapping_path)
                )
                partner = next(
                    row for row in mcp_body.get("partners", []) if row.get("id") == "aleksei"
                )
                ff = mcp_body.get("fund_financing") or {}
                ok = (
                    mcp_body.get("ok") is True
                    and partner.get("outgoing_financing") == 140.0
                    and len(ff.get("projections") or []) >= 2
                    and ff.get("outgoing_by_member", {}).get("aleksei") == 140.0
                )
                actual = (
                    f"ok={mcp_body.get('ok')}; outgoing={partner.get('outgoing_financing')}; "
                    f"n_proj={len(ff.get('projections') or [])}; "
                    f"out_member={ff.get('outgoing_by_member')}"
                )
                (_pass if ok else _fail)(steps, "T1", 2, actual)
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T1", 2, f"error: {exc}")

            try:
                status, http_body = _get_http_carryover(client, closed_period=period_ym)
                http_ff = http_body.get("fund_financing") or {}
                http_partner = next(
                    row for row in http_body.get("partners", []) if row.get("id") == "aleksei"
                )
                mcp_partner = next(
                    row for row in mcp_body.get("partners", []) if row.get("id") == "aleksei"
                )
                mcp_ff = mcp_body.get("fund_financing") or {}
                ok = (
                    status == 200
                    and mcp_partner.get("outgoing_financing")
                    == http_partner.get("outgoing_financing")
                    and len(mcp_ff.get("projections") or [])
                    == len(http_ff.get("projections") or [])
                    and mcp_ff.get("outgoing_by_member") == http_ff.get("outgoing_by_member")
                )
                actual = (
                    f"HTTP {status}; mcp_out={mcp_partner.get('outgoing_financing')}; "
                    f"http_out={http_partner.get('outgoing_financing')}; "
                    f"mcp_n={len(mcp_ff.get('projections') or [])}; "
                    f"http_n={len(http_ff.get('projections') or [])}"
                )
                (_pass if ok else _fail)(steps, "T1", 3, actual)
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T1", 3, f"error: {exc}")

            # --- T2 money_check dry_run (same seeded month as prior) ---
            try:
                from personal_fund_carryover import (
                    compute_personal_fund_carryover as _cpc,
                )

                def _dry_carryover(*_a: Any, **kw: Any) -> dict[str, Any]:
                    kw = dict(kw)
                    kw["allow_non_final"] = True
                    kw["incoming_carryover_override"] = {"aleksei": 0.0, "nikolay": 0.0}
                    kw["mapping_path"] = str(mapping_path)
                    return _cpc(*_a, **kw)

                with mock.patch(
                    "money_check_report.build_methodology_block",
                    side_effect=lambda recon, period: {
                        "period": period,
                        "methodology_status": "final_closed",
                        "is_final": True,
                        "is_preliminary": False,
                        "label": "final",
                    },
                ), mock.patch(
                    "money_check_report.compute_personal_fund_carryover",
                    side_effect=_dry_carryover,
                ):
                    mc = _mcp_money_check(
                        api,
                        check_period="2026-04",
                        prior_period=period_ym,
                        mapping_path=str(mapping_path),
                        carryover_log_path=empty_log,
                    )
                partner = next(
                    row for row in mc.get("partners", []) if row.get("id") == "aleksei"
                )
                ff = mc.get("fund_financing") or {}
                ok = (
                    mc.get("ok") is True
                    and mc.get("carryover", {}).get("source") == "dry_run"
                    and partner.get("outgoing_financing") == 140.0
                    and ff.get("outgoing_by_member", {}).get("aleksei") == 140.0
                )
                actual = (
                    f"source={mc.get('carryover', {}).get('source')}; "
                    f"outgoing={partner.get('outgoing_financing')}; "
                    f"out_member={ff.get('outgoing_by_member')}"
                )
                (_pass if ok else _fail)(steps, "T2", 1, actual)
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T2", 1, f"error: {exc}")

            _cleanup()

            # --- T3 log / none ---
            try:
                mc_log = _mcp_money_check(
                    api,
                    check_period="2026-02",
                    prior_period="2026-01",
                    mapping_path=str(mapping_path),
                    carryover_log_path=log_with_run,
                )
                outs = [row.get("outgoing_financing") for row in mc_log.get("partners", [])]
                ff = mc_log.get("fund_financing") or {}
                ok = (
                    mc_log.get("carryover", {}).get("source") == "log"
                    and outs == [0.0, 0.0]
                    and ff.get("projections") == []
                    and ff.get("accounting_period") == ""
                    and not any("financing" in str(w) and "log" in str(w) for w in mc_log.get("warnings", []))
                )
                actual = (
                    f"source={mc_log.get('carryover', {}).get('source')}; "
                    f"outs={outs}; accounting_period={ff.get('accounting_period')!r}; "
                    f"projections={len(ff.get('projections') or [])}"
                )
                (_pass if ok else _fail)(steps, "T3", 1, actual)
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T3", 1, f"error: {exc}")

            try:
                with mock.patch(
                    "money_check_report.build_methodology_block",
                    side_effect=lambda recon, period: {
                        "period": period,
                        "methodology_status": "open",
                        "is_final": False,
                        "is_preliminary": False,
                        "label": "open",
                    },
                ):
                    mc_none = _mcp_money_check(
                        api,
                        check_period="2026-04",
                        prior_period="2026-03",
                        mapping_path=str(mapping_path),
                        carryover_log_path=empty_log,
                    )
                outs = [row.get("outgoing_financing") for row in mc_none.get("partners", [])]
                ff = mc_none.get("fund_financing") or {}
                ok = (
                    mc_none.get("carryover", {}).get("source") == "none"
                    and all(v == 0.0 for v in outs)
                    and ff.get("projections") == []
                    and ff.get("accounting_period") == ""
                )
                actual = (
                    f"source={mc_none.get('carryover', {}).get('source')}; "
                    f"outs={outs}; accounting_period={ff.get('accounting_period')!r}"
                )
                (_pass if ok else _fail)(steps, "T3", 2, actual)
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T3", 2, f"error: {exc}")

            # --- T4 same fund ---
            period_ym = "2026-04"
            period = "202604"
            try:
                baseline = _mcp_carryover(
                    api, closed_period=period_ym, mapping_path=str(mapping_path)
                )
                baseline_out = next(
                    row for row in baseline.get("partners", []) if row.get("id") == "aleksei"
                ).get("outgoing_financing")
                _, expense = _insert_op(
                    debit_credit="debit",
                    amount="25.00",
                    accounting_period=period,
                    fund_id=personal_a,
                    category="C0001",
                    posting_date="2026-04-15",
                )
                _, credit = _insert_op(
                    debit_credit="credit",
                    amount="25.00",
                    accounting_period=period,
                    fund_id=personal_a,
                    category="I0001",
                    line_type="I",
                    posting_date="2026-04-15",
                )
                settlement = settlements_store.create_expense_settlement(
                    credit, expense, "25.00"
                )
                _pass(
                    steps,
                    "T4",
                    1,
                    f"settlement {settlement.id}; baseline_out={baseline_out}",
                )
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T4", 1, f"error: {exc}")
                settlement = None  # type: ignore[assignment]
                baseline_out = None

            try:
                after = _mcp_carryover(
                    api, closed_period=period_ym, mapping_path=str(mapping_path)
                )
                partner = next(
                    row for row in after.get("partners", []) if row.get("id") == "aleksei"
                )
                proj_ids = {
                    row.get("settlement_id")
                    for row in (after.get("fund_financing") or {}).get("projections") or []
                }
                ok = (
                    partner.get("outgoing_financing") == baseline_out
                    and (settlement is None or settlement.id not in proj_ids)
                )
                actual = (
                    f"outgoing={partner.get('outgoing_financing')}; "
                    f"baseline={baseline_out}; "
                    f"settlement_in_proj={settlement.id in proj_ids if settlement else None}"
                )
                (_pass if ok else _fail)(steps, "T4", 2, actual)
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T4", 2, f"error: {exc}")

            _cleanup()

            # --- T5 empty month ---
            try:
                empty = _mcp_carryover(
                    api, closed_period="2026-05", mapping_path=str(mapping_path)
                )
                partners = empty.get("partners") or []
                ff = empty.get("fund_financing") or {}
                ok = (
                    empty.get("ok") is True
                    and all("outgoing_financing" in row for row in partners)
                    and all(row.get("outgoing_financing") == 0.0 for row in partners)
                    and "fund_financing" in empty
                    and ff.get("projections") == []
                )
                actual = (
                    f"partners_out={[row.get('outgoing_financing') for row in partners]}; "
                    f"projections={len(ff.get('projections') or [])}"
                )
                (_pass if ok else _fail)(steps, "T5", 1, actual)
            except Exception as exc:  # noqa: BLE001
                _fail(steps, "T5", 1, f"error: {exc}")

            _cleanup()
    finally:
        from financeplanning.db.context import reset_active_profile_id

        reset_active_profile_id(token)
        tmp.cleanup()

    failed = [
        f"{tid}.{item['step']}"
        for tid, items in steps.items()
        for item in items
        if item["result"] != "PASSED"
    ]
    result = {
        "commit": commit,
        "db": str(db_path),
        "profile": _PROFILE,
        "transport": "TestClient+MCP modules (live :8001 carryover HTTP 500)",
        "steps": steps,
        "failed": failed,
    }
    out_path = _MCP_ROOT / "tests" / "fin-280-cand-acceptance-result.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print(f"wrote {out_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
