"""
FIN-341 acceptance: T1–T4 on cand permanent DB; T5 on isolated tempfile DB.

Uses MCP helpers (list_bank_accounts / upsert_bank_account) over TestClient.

Run from mcp-servers/finance-assistant with FinancePlanning venv:

  C:\\Users\\haake\\PycharmProjects\\FinancePlanningProject\\.venv\\Scripts\\python.exe tests/_fin341_acceptance.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

_MCP_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _MCP_ROOT / "scripts"
_REPO = Path(r"C:\Users\haake\PycharmProjects\FinancePlanningProject")
for path in (_SCRIPTS, _MCP_ROOT, _REPO):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from households import list_bank_accounts, upsert_bank_account  # noqa: E402

_PROFILE = "cand"
_HH = "default"
_ACC = "acc-accept-fin341"
_BANK_NAME = "ACCEPT-FIN341 bank"
_PROVIDER = "accept_fin341"


class TestClientApi:
    """ApiClient-compatible wrapper over FastAPI TestClient."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.base = "http://testserver"

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
        elif method_u == "DELETE":
            resp = self._client.delete(path)
        else:
            raise ValueError(f"unsupported method {method}")
        try:
            body: Any = resp.json()
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


def _db_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or ""
    return Path(local) / "finance-planning" / "finance.db"


def _err_code(exc: BaseException) -> str:
    text = str(exc)
    for code in (
        "validation_error",
        "bank_account_currency_immutable",
    ):
        if code in text:
            return code
    return ""


def _http_status(exc: BaseException) -> str:
    text = str(exc)
    for token in text.split():
        if token.isdigit() and len(token) == 3:
            return token
    return ""


def _upsert_args(account_id: str, bank_id: str, **extra: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "household_id": _HH,
        "account_id": account_id,
        "provider": _PROVIDER,
        "display_name": "ACCEPT-FIN341",
        "valid_from": "2026-08",
        "bank_id": bank_id,
    }
    arguments.update(extra)
    return arguments


def _cleanup(*, profile: str = _PROFILE) -> None:
    from financeplanning.db.stores._helpers import open_store_connection

    conn = open_store_connection()
    try:
        conn.execute(
            """
            DELETE FROM bank_account_identifiers
            WHERE profile_id = ?
              AND bank_account_id LIKE 'acc-accept-fin341%'
            """,
            (profile,),
        )
        conn.execute(
            """
            DELETE FROM bank_accounts
            WHERE profile_id = ?
              AND (id LIKE 'acc-accept-fin341%' OR provider = ?)
            """,
            (profile, _PROVIDER),
        )
        conn.execute(
            """
            DELETE FROM banks
            WHERE profile_id = ?
              AND display_name = ?
            """,
            (profile, _BANK_NAME),
        )
        conn.commit()
    finally:
        conn.close()


def _create_bank(api: TestClientApi) -> str:
    status, body = api.request(
        "POST",
        "/api/v1/banks",
        data={"display_name": _BANK_NAME, "bic": None},
    )
    if status != 201 or not isinstance(body, dict):
        raise RuntimeError(f"create bank -> HTTP {status}: {body}")
    return str(body["id"])


def run_t1_t4(api: TestClientApi) -> dict[str, list[dict[str, Any]]]:
    steps: dict[str, list[dict[str, Any]]] = {f"T{i}": [] for i in range(1, 5)}

    listed = list_bank_accounts(
        api, profile=_PROFILE, base=api.base, arguments={"household_id": _HH}
    )
    rows = listed["bank_accounts"]
    has_key = bool(rows) and all("currency" in row for row in rows)
    steps["T1"].append(
        {
            "step": 1,
            "actual": f"ok={listed['ok']}; n={len(rows)}; currency_key={has_key}",
            "result": "PASSED" if listed["ok"] and has_key else "FAILED",
        }
    )
    unfilled = [
        row
        for row in rows
        if not str(row.get("id", "")).startswith("acc-accept-fin341")
        and row.get("currency") is None
    ]
    if unfilled:
        sample = unfilled[0]
        steps["T1"].append(
            {
                "step": 2,
                "actual": f"id={sample['id']}; currency={sample.get('currency')!r}",
                "result": "PASSED",
            }
        )
    else:
        steps["T1"].append(
            {
                "step": 2,
                "actual": "no pre-existing unfilled bank account on cand",
                "result": "BLOCKED",
            }
        )

    bank_id = _create_bank(api)
    created = upsert_bank_account(
        api,
        profile=_PROFILE,
        base=api.base,
        arguments=_upsert_args(_ACC, bank_id, currency="eur"),
    )
    ok_t2_1 = created["ok"] and created["bank_account"]["currency"] == "EUR"
    steps["T2"].append(
        {
            "step": 1,
            "actual": (
                f"ok={created['ok']}; "
                f"currency={created['bank_account'].get('currency')}"
            ),
            "result": "PASSED" if ok_t2_1 else "FAILED",
        }
    )
    listed2 = list_bank_accounts(
        api, profile=_PROFILE, base=api.base, arguments={"household_id": _HH}
    )
    match = [r for r in listed2["bank_accounts"] if r["id"] == _ACC]
    ok_t2_2 = match and match[0].get("currency") == "EUR"
    steps["T2"].append(
        {
            "step": 2,
            "actual": f"listed_currency={match[0].get('currency') if match else None}",
            "result": "PASSED" if ok_t2_2 else "FAILED",
        }
    )
    again = upsert_bank_account(
        api,
        profile=_PROFILE,
        base=api.base,
        arguments=_upsert_args(_ACC, bank_id, currency=" eur "),
    )
    ok_t2_3 = again["ok"] and again["bank_account"]["currency"] == "EUR"
    steps["T2"].append(
        {
            "step": 3,
            "actual": f"ok={again['ok']}; currency={again['bank_account'].get('currency')}",
            "result": "PASSED" if ok_t2_3 else "FAILED",
        }
    )

    t3_cases = [
        (1, f"{_ACC}-omit", {}),
        (2, f"{_ACC}-empty", {"currency": ""}),
        (3, f"{_ACC}-ws", {"currency": "   "}),
        (4, f"{_ACC}-eu", {"currency": "eu"}),
    ]
    listed_before = {
        r["id"]
        for r in list_bank_accounts(
            api, profile=_PROFILE, base=api.base, arguments={"household_id": _HH}
        )["bank_accounts"]
    }
    for step, account_id, extra in t3_cases:
        try:
            upsert_bank_account(
                api,
                profile=_PROFILE,
                base=api.base,
                arguments=_upsert_args(account_id, bank_id, **extra),
            )
            steps["T3"].append(
                {
                    "step": step,
                    "actual": "unexpected success",
                    "result": "FAILED",
                }
            )
        except RuntimeError as exc:
            created_ids = {
                r["id"]
                for r in list_bank_accounts(
                    api,
                    profile=_PROFILE,
                    base=api.base,
                    arguments={"household_id": _HH},
                )["bank_accounts"]
            }
            not_created = account_id not in (created_ids - listed_before)
            ok = (
                _http_status(exc) == "422"
                and _err_code(exc) == "validation_error"
                and not_created
            )
            steps["T3"].append(
                {
                    "step": step,
                    "actual": (
                        f"HTTP {_http_status(exc)}; "
                        f"code={_err_code(exc)}; created={not not_created}"
                    ),
                    "result": "PASSED" if ok else "FAILED",
                }
            )

    try:
        upsert_bank_account(
            api,
            profile=_PROFILE,
            base=api.base,
            arguments=_upsert_args(f"{_ACC}-num", bank_id, currency=123),
        )
        steps["T3"].append(
            {"step": 5, "actual": "unexpected success", "result": "FAILED"}
        )
    except ValueError as exc:
        steps["T3"].append(
            {
                "step": 5,
                "actual": f"ValueError before HTTP: {exc}",
                "result": "PASSED" if "currency" in str(exc) else "FAILED",
            }
        )

    renamed = upsert_bank_account(
        api,
        profile=_PROFILE,
        base=api.base,
        arguments=_upsert_args(_ACC, bank_id, display_name="ACCEPT-FIN341 T4 step 1"),
    )
    ok_t4_1 = (
        renamed["ok"]
        and renamed["bank_account"]["display_name"] == "ACCEPT-FIN341 T4 step 1"
        and renamed["bank_account"]["currency"] == "EUR"
    )
    steps["T4"].append(
        {
            "step": 1,
            "actual": (
                f"name={renamed['bank_account']['display_name']}; "
                f"currency={renamed['bank_account']['currency']}"
            ),
            "result": "PASSED" if ok_t4_1 else "FAILED",
        }
    )
    try:
        upsert_bank_account(
            api,
            profile=_PROFILE,
            base=api.base,
            arguments=_upsert_args(
                _ACC,
                bank_id,
                display_name="Must not apply",
                currency="RUB",
            ),
        )
        steps["T4"].append(
            {"step": 2, "actual": "unexpected success", "result": "FAILED"}
        )
    except RuntimeError as exc:
        after = list_bank_accounts(
            api, profile=_PROFILE, base=api.base, arguments={"household_id": _HH}
        )
        row = next(r for r in after["bank_accounts"] if r["id"] == _ACC)
        ok = (
            _http_status(exc) == "409"
            and _err_code(exc) == "bank_account_currency_immutable"
            and row["currency"] == "EUR"
            and row["display_name"] == "ACCEPT-FIN341 T4 step 1"
        )
        steps["T4"].append(
            {
                "step": 2,
                "actual": (
                    f"HTTP {_http_status(exc)}; code={_err_code(exc)}; "
                    f"name={row['display_name']}; currency={row['currency']}"
                ),
                "result": "PASSED" if ok else "FAILED",
            }
        )
    try:
        upsert_bank_account(
            api,
            profile=_PROFILE,
            base=api.base,
            arguments=_upsert_args(_ACC, bank_id, currency=None),
        )
        steps["T4"].append(
            {"step": 3, "actual": "unexpected success", "result": "FAILED"}
        )
    except ValueError as exc:
        after = list_bank_accounts(
            api, profile=_PROFILE, base=api.base, arguments={"household_id": _HH}
        )
        row = next(r for r in after["bank_accounts"] if r["id"] == _ACC)
        ok = "currency" in str(exc) and row["currency"] == "EUR"
        steps["T4"].append(
            {
                "step": 3,
                "actual": (
                    f"ValueError before HTTP: {exc}; currency={row['currency']}"
                ),
                "result": "PASSED" if ok else "FAILED",
            }
        )
    return steps


def run_t5() -> list[dict[str, Any]]:
    from financeplanning.db.stores._helpers import open_store_connection
    from financeplanning.household_master_data import utc_now_z
    from web.test_client_profile import test_client_with_profile

    steps: list[dict[str, Any]] = []
    with test_client_with_profile("test") as client:
        api = TestClientApi(client)
        status, bank_body = api.request(
            "POST",
            "/api/v1/banks",
            data={"display_name": _BANK_NAME, "bic": None},
        )
        if status != 201 or not isinstance(bank_body, dict):
            return [
                {
                    "step": 1,
                    "actual": f"create bank HTTP {status}: {bank_body}",
                    "result": "FAILED",
                }
            ]
        bank_id = str(bank_body["id"])
        account_id = "acc-accept-fin341-t5"
        stamp = utc_now_z()
        conn = open_store_connection()
        try:
            conn.execute(
                """
                INSERT INTO bank_accounts (
                  profile_id, household_id, id, provider, display_name,
                  holder_member_id, statement_expected, final_close_only,
                  valid_from, valid_to, created_at, updated_at, bank_id
                ) VALUES ('test', 'default', ?, ?, ?, NULL, 1, 0,
                          '2026-08', NULL, ?, ?, ?)
                """,
                (
                    account_id,
                    "accept_fin341_t5",
                    f"Unfilled {account_id}",
                    stamp,
                    stamp,
                    bank_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        listed = list_bank_accounts(
            api,
            profile="test",
            base=api.base,
            arguments={"household_id": _HH},
        )
        row = next(r for r in listed["bank_accounts"] if r["id"] == account_id)
        steps.append(
            {
                "step": 1,
                "actual": f"ok={listed['ok']}; currency={row.get('currency')!r}",
                "result": "PASSED"
                if listed["ok"] and row.get("currency") is None
                else "FAILED",
            }
        )
        filled = upsert_bank_account(
            api,
            profile="test",
            base=api.base,
            arguments=_upsert_args(
                account_id,
                bank_id,
                provider="accept_fin341_t5",
                display_name=f"Unfilled {account_id}",
                currency="rub",
            ),
        )
        steps.append(
            {
                "step": 2,
                "actual": (
                    f"ok={filled['ok']}; "
                    f"currency={filled['bank_account'].get('currency')}"
                ),
                "result": "PASSED"
                if filled["ok"] and filled["bank_account"]["currency"] == "RUB"
                else "FAILED",
            }
        )
        same = upsert_bank_account(
            api,
            profile="test",
            base=api.base,
            arguments=_upsert_args(
                account_id,
                bank_id,
                provider="accept_fin341_t5",
                display_name=f"Unfilled {account_id}",
                currency="RUB",
            ),
        )
        steps.append(
            {
                "step": 3,
                "actual": (
                    f"ok={same['ok']}; "
                    f"currency={same['bank_account'].get('currency')}"
                ),
                "result": "PASSED"
                if same["ok"] and same["bank_account"]["currency"] == "RUB"
                else "FAILED",
            }
        )
        try:
            upsert_bank_account(
                api,
                profile="test",
                base=api.base,
                arguments=_upsert_args(
                    account_id,
                    bank_id,
                    provider="accept_fin341_t5",
                    display_name=f"Unfilled {account_id}",
                    currency="EUR",
                ),
            )
            steps.append(
                {"step": 4, "actual": "unexpected success", "result": "FAILED"}
            )
        except RuntimeError as exc:
            after = list_bank_accounts(
                api,
                profile="test",
                base=api.base,
                arguments={"household_id": _HH},
            )
            row = next(r for r in after["bank_accounts"] if r["id"] == account_id)
            ok = (
                _http_status(exc) == "409"
                and _err_code(exc) == "bank_account_currency_immutable"
                and row["currency"] == "RUB"
            )
            steps.append(
                {
                    "step": 4,
                    "actual": (
                        f"HTTP {_http_status(exc)}; code={_err_code(exc)}; "
                        f"currency={row['currency']}"
                    ),
                    "result": "PASSED" if ok else "FAILED",
                }
            )
    return steps


def main() -> int:
    from financeplanning.db.context import (
        reset_active_profile_id,
        set_active_profile_id_token,
    )
    from financeplanning.db.migrate import migrate
    from web.app import app
    from fastapi.testclient import TestClient

    db = _db_path()
    if not db.is_file():
        print(f"FAIL: permanent DB not found: {db}")
        return 1

    commit = _git_commit()
    print(f"DB: {db}")
    print(f"commit: {commit}")

    env = {
        "FINANCE_DATA_PROFILE": _PROFILE,
        "FINANCE_DB_PATH": str(db),
    }
    with mock.patch.dict(os.environ, env, clear=False):
        migrate()
        token = set_active_profile_id_token(_PROFILE)
        try:
            _cleanup()
            client = TestClient(app)
            api = TestClientApi(client)
            t1_t4 = run_t1_t4(api)
        finally:
            _cleanup()
            reset_active_profile_id(token)

    t5 = run_t5()
    payload = {
        "commit": commit,
        "db": str(db),
        "profile": _PROFILE,
        "T1": t1_t4["T1"],
        "T2": t1_t4["T2"],
        "T3": t1_t4["T3"],
        "T4": t1_t4["T4"],
        "T5": t5,
    }
    out = _MCP_ROOT / "tests" / "_fin341_acceptance_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    failed = [
        (tid, step)
        for tid, rows in payload.items()
        if tid.startswith("T")
        for step in (rows if isinstance(rows, list) else [])
        if step.get("result") != "PASSED"
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
