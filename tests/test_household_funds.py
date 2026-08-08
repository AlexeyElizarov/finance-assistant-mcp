"""Unit tests for FIN-256 household funds MCP tools and query_transactions fund_id."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from household_funds import (  # noqa: E402
    create_household_fund,
    get_household_fund,
    list_household_funds,
    patch_household_fund,
)

import server  # noqa: E402

TX_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"


def _load_query_transactions():
    path = _SCRIPTS / "query-transactions.py"
    spec = importlib.util.spec_from_file_location("query_transactions_fin256", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_qt = _load_query_transactions()


class _FundsMockApi:
    """Stub ApiClient capturing GET/PUT/PATCH for funds API."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.raise_on_request = raise_on_request
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if self.raise_on_request is not None:
            raise self.raise_on_request
        self.calls.append((method, path, dict(data) if data is not None else None))
        return self.status, self.body

    @property
    def last_body(self) -> dict[str, Any] | None:
        if not self.calls:
            return None
        return self.calls[-1][2]


def _sample_fund(**overrides: Any) -> dict[str, Any]:
    fund = {
        "id": "personal-aleksey",
        "household_id": "hh1",
        "name": "Personal Aleksey",
        "allocation_rule": "equal_share",
        "member_id": "aleksey",
        "valid_from": "2026-01-01",
        "valid_to": None,
        "created_at": "t",
        "updated_at": "t",
    }
    fund.update(overrides)
    return fund


class HouseholdFundsLibTests(unittest.TestCase):
    """Lib helpers: happy path, domain errors, ValueError before HTTP."""

    def test_create_201(self) -> None:
        fund = _sample_fund()
        api = _FundsMockApi(status=201, body=fund)
        result = create_household_fund(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": "hh1",
                "fund_id": "personal-aleksey",
                "name": "Personal Aleksey",
                "allocation_rule": "equal_share",
                "valid_from": "2026-01-01",
                "member_id": "aleksey",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["fund"]["id"], "personal-aleksey")
        self.assertEqual(api.calls[0][0], "PUT")
        self.assertEqual(
            api.calls[0][1],
            "/api/v1/households/hh1/funds/personal-aleksey",
        )
        self.assertEqual(api.last_body["member_id"], "aleksey")
        self.assertNotIn("valid_to", api.last_body)

    def test_create_409(self) -> None:
        err = {
            "error": {
                "code": "fund_already_exists",
                "message": "fund id taken",
            }
        }
        api = _FundsMockApi(status=409, body=err)
        with self.assertRaises(RuntimeError) as ctx:
            create_household_fund(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": "hh1",
                    "fund_id": "personal-aleksey",
                    "name": "Personal Aleksey",
                    "allocation_rule": "equal_share",
                    "valid_from": "2026-01-01",
                },
            )
        msg = str(ctx.exception)
        self.assertIn("409", msg)
        self.assertIn("fund_already_exists", msg)

    def test_list_and_applicable_on(self) -> None:
        fund = _sample_fund()
        api = _FundsMockApi(body={"funds": [fund]})
        listed = list_household_funds(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_id": "hh1"},
        )
        self.assertEqual(listed["funds"][0]["id"], "personal-aleksey")
        self.assertEqual(api.calls[0][:2], ("GET", "/api/v1/households/hh1/funds"))

        api = _FundsMockApi(body={"funds": [fund]})
        list_household_funds(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_id": "hh1", "applicable_on": "2026-07-15"},
        )
        self.assertIn("applicable_on=2026-07-15", api.calls[0][1])

    def test_get_ok_and_404(self) -> None:
        fund = _sample_fund()
        api = _FundsMockApi(body=fund)
        got = get_household_fund(
            api,
            profile="cand",
            base="http://test",
            arguments={"household_id": "hh1", "fund_id": "personal-aleksey"},
        )
        self.assertEqual(got["fund"]["id"], "personal-aleksey")

        err = {"error": {"code": "unknown_fund", "message": "missing"}}
        api = _FundsMockApi(status=404, body=err)
        with self.assertRaises(RuntimeError) as ctx:
            get_household_fund(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": "hh1", "fund_id": "missing"},
            )
        self.assertIn("unknown_fund", str(ctx.exception))

    def test_patch_rename(self) -> None:
        fund = _sample_fund(name="Renamed")
        api = _FundsMockApi(body=fund)
        result = patch_household_fund(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": "hh1",
                "fund_id": "personal-aleksey",
                "name": "Renamed",
            },
        )
        self.assertEqual(result["fund"]["name"], "Renamed")
        self.assertEqual(api.last_body, {"name": "Renamed"})

    def test_patch_extension_forbidden(self) -> None:
        err = {
            "error": {
                "code": "fund_validity_extension_forbidden",
                "message": "cannot reopen",
            }
        }
        api = _FundsMockApi(status=422, body=err)
        with self.assertRaises(RuntimeError) as ctx:
            patch_household_fund(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": "hh1",
                    "fund_id": "personal-aleksey",
                    "valid_to": None,
                },
            )
        self.assertIn("fund_validity_extension_forbidden", str(ctx.exception))

    def test_network_error_not_wrapped(self) -> None:
        api = _FundsMockApi(raise_on_request=ConnectionError("down"))
        with self.assertRaises(ConnectionError):
            list_household_funds(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": "hh1"},
            )

    def test_value_error_before_http(self) -> None:
        api = _FundsMockApi()
        with self.assertRaises(ValueError):
            create_household_fund(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "household_id": "  ",
                    "fund_id": "f1",
                    "name": "N",
                    "allocation_rule": "equal_share",
                    "valid_from": "2026-01-01",
                },
            )
        self.assertEqual(api.calls, [])

        with self.assertRaises(ValueError):
            patch_household_fund(
                api,
                profile="cand",
                base="http://test",
                arguments={"household_id": "hh1", "fund_id": "f1"},
            )
        self.assertEqual(api.calls, [])

    def test_omit_vs_null_vs_empty_string(self) -> None:
        fund = _sample_fund()
        api = _FundsMockApi(status=201, body=fund)
        create_household_fund(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": "hh1",
                "fund_id": "personal-aleksey",
                "name": "Personal Aleksey",
                "allocation_rule": "equal_share",
                "valid_from": "2026-01-01",
            },
        )
        self.assertNotIn("member_id", api.last_body)
        self.assertNotIn("valid_to", api.last_body)

        api = _FundsMockApi(status=201, body=fund)
        create_household_fund(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": "hh1",
                "fund_id": "personal-aleksey",
                "name": "Personal Aleksey",
                "allocation_rule": "equal_share",
                "valid_from": "2026-01-01",
                "member_id": None,
                "valid_to": None,
            },
        )
        self.assertIn("member_id", api.last_body)
        self.assertIsNone(api.last_body["member_id"])
        self.assertIn("valid_to", api.last_body)
        self.assertIsNone(api.last_body["valid_to"])

        api = _FundsMockApi(status=201, body=fund)
        create_household_fund(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "household_id": "hh1",
                "fund_id": "personal-aleksey",
                "name": "Personal Aleksey",
                "allocation_rule": "equal_share",
                "valid_from": "2026-01-01",
                "member_id": "",
            },
        )
        self.assertEqual(api.last_body["member_id"], "")


class HouseholdFundsHandlerPresenceTests(unittest.TestCase):
    """omit vs null via MCP handler path."""

    def _patch_session(self, api: _FundsMockApi) -> Any:
        return patch("server.get_session", return_value=(api, "http://test"))

    def test_handler_omit_valid_to_create(self) -> None:
        api = _FundsMockApi(status=201, body=_sample_fund())
        with self._patch_session(api):
            out = server._handle_create_household_fund(
                {
                    "household_id": "hh1",
                    "fund_id": "personal-aleksey",
                    "name": "Personal Aleksey",
                    "allocation_rule": "equal_share",
                    "valid_from": "2026-01-01",
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertNotIn("valid_to", api.last_body)

    def test_handler_explicit_null_valid_to_create(self) -> None:
        api = _FundsMockApi(status=201, body=_sample_fund())
        with self._patch_session(api):
            out = server._handle_create_household_fund(
                {
                    "household_id": "hh1",
                    "fund_id": "personal-aleksey",
                    "name": "Personal Aleksey",
                    "allocation_rule": "equal_share",
                    "valid_from": "2026-01-01",
                    "valid_to": None,
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertIn("valid_to", api.last_body)
        self.assertIsNone(api.last_body["valid_to"])

    def test_tools_registered(self) -> None:
        tools_list = asyncio.run(server.list_tools())
        names = {t.name for t in tools_list}
        for name in (
            "list_household_funds",
            "get_household_fund",
            "create_household_fund",
            "patch_household_fund",
        ):
            self.assertIn(name, names)
        create = next(t for t in tools_list if t.name == "create_household_fund")
        props = create.inputSchema["properties"]
        self.assertEqual(props["member_id"]["type"], ["string", "null"])
        self.assertEqual(props["valid_to"]["type"], ["string", "null"])


class Fin256QueryFundIdTests(unittest.TestCase):
    """FIN-256 T8 — query_transactions fund_id."""

    def test_row_has_fund_id(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [
                {
                    "id": TX_ID,
                    "date_display": "01.07.2026",
                    "amount": "10,00",
                    "debit_credit_indicator": "D",
                    "description": "With fund",
                    "transaction_category": "C0003",
                    "transaction_type": "C",
                    "expense_owner": "nikolai",
                    "fund_id": "personal-nikolai",
                    "provider": "sparkasse_sepa",
                },
                {
                    "id": "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
                    "date_display": "02.07.2026",
                    "amount": "5,00",
                    "debit_credit_indicator": "D",
                    "description": "No fund key",
                    "transaction_category": "C0001",
                    "transaction_type": "C",
                    "provider": "sparkasse_sepa",
                },
            ],
            "meta": {},
        }
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertEqual(rows[0].fund_id, "personal-nikolai")
        self.assertIsNone(rows[1].fund_id)

        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=rows):
                result = server._handle_query_transactions({"period": "2026-07"})
        payload = json.loads(result[0].text)
        self.assertEqual(payload["rows"][0]["fund_id"], "personal-nikolai")
        self.assertIsNone(payload["rows"][1]["fund_id"])
        self.assertIn("fund_id", payload["rows"][1])

    def test_group_by_month_unchanged(self) -> None:
        mock_row = MagicMock()
        mock_row.date_display = "01.07.2026"
        mock_row.amount = 10.0
        mock_row.indicator = "D"
        mock_row.category = "C0003"
        mock_row.provider = "sparkasse_sepa"
        mock_row.description = "x"
        mock_row.id = TX_ID
        mock_row.transaction_type = "C"
        mock_row.expense_owner = "nikolai"
        mock_row.fund_id = "personal-nikolai"

        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=[mock_row]):
                result = server._handle_query_transactions(
                    {"period": "2026-07", "group_by": "month"},
                )
        payload = json.loads(result[0].text)
        self.assertIn("groups", payload)
        self.assertNotIn("rows", payload)


if __name__ == "__main__":
    unittest.main()
