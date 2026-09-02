"""Unit tests for FIN-336 fact-side multi-currency MCP surfaces."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import jsonschema

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from get_transaction import get_transaction  # noqa: E402
from monthly_close_lib import import_log_entry  # noqa: E402
from put_transaction import put_transaction  # noqa: E402
from put_transaction_lines import put_transaction_lines  # noqa: E402

import server  # noqa: E402

_QT_SPEC = importlib.util.spec_from_file_location(
    "query_transactions_fin336",
    _SCRIPTS / "query-transactions.py",
)
assert _QT_SPEC is not None and _QT_SPEC.loader is not None
_qt = importlib.util.module_from_spec(_QT_SPEC)
sys.modules["query_transactions_fin336"] = _qt
_QT_SPEC.loader.exec_module(_qt)

_QPF_SPEC = importlib.util.spec_from_file_location(
    "query_plan_fact_fin336",
    _SCRIPTS / "query-plan-fact.py",
)
assert _QPF_SPEC is not None and _QPF_SPEC.loader is not None
_qpf = importlib.util.module_from_spec(_QPF_SPEC)
sys.modules["query_plan_fact_fin336"] = _qpf
_QPF_SPEC.loader.exec_module(_qpf)

TX_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ITEM_ID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
ASSIGNMENT = {
    "type": "expense",
    "category": "C0001",
    "project": None,
    "fund_id": None,
    "source": "manual",
    "state": "complete",
    "note": None,
}


def _api_tx_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": TX_ID,
        "date_display": "15.07.2026",
        "amount": "6606.70",
        "debit_credit_indicator": "D",
        "description": "Moscow corridor",
        "transaction_category": "C0001",
        "transaction_type": "C",
        "provider": "tbank",
    }
    row.update(overrides)
    return row


class _PlanFactApi:
    """Stub GET JSON for grouped plan-actual and drill-down."""

    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes = nodes
        self.paths: list[str] = []

    def get_json(self, path: str) -> dict[str, Any]:
        self.paths.append(path)
        if path.startswith("/api/v1/budget/plan-actual/transactions"):
            return {"transactions": [{"id": TX_ID, "amount": "6606.70"}]}
        if path.startswith("/api/v1/budget/plan-actual?"):
            return {"grid_nodes": self.nodes}
        raise AssertionError(f"unexpected path {path}")


class QueryTransactionsFxTests(unittest.TestCase):
    """T6.1 — FX header fields on query_transactions rows."""

    def test_missing_keys_are_null_without_eur_fallback(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {"rows": [_api_tx_row()], "meta": {}}
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertIsNone(rows[0].currency)
        self.assertIsNone(rows[0].budget_currency)
        self.assertIsNone(rows[0].planned_rate)

        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=rows):
                result = server._handle_query_transactions({"period": "2026-07"})
        payload = json.loads(result[0].text)
        row = payload["rows"][0]
        self.assertIsNone(row["currency"])
        self.assertIsNone(row["budget_currency"])
        self.assertIsNone(row["planned_rate"])
        self.assertNotEqual(row["currency"], "EUR")

    def test_stored_rub_header_passthrough(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [
                _api_tx_row(
                    currency="RUB",
                    budget_currency="EUR",
                    planned_rate="90.8776",
                )
            ],
            "meta": {},
        }
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertEqual(rows[0].currency, "RUB")
        self.assertEqual(rows[0].budget_currency, "EUR")
        self.assertEqual(rows[0].planned_rate, "90.8776")
        self.assertEqual(rows[0].amount, 6606.7)


class PutTransactionFxTests(unittest.TestCase):
    """D-06 response echo of stored header FX fields."""

    def test_echoes_header_fx_fields(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {
                "id": TX_ID,
                "fund_id": None,
                "currency": "RUB",
                "budget_currency": "EUR",
                "planned_rate": "90.8776",
            },
        )
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "transaction_id": TX_ID,
                "reconciliation_note": "fx",
            },
        )
        self.assertEqual(result["transaction"]["currency"], "RUB")
        self.assertEqual(result["transaction"]["planned_rate"], "90.8776")
        self.assertNotIn("currency", api.request.call_args.kwargs.get("data") or {})

    def test_missing_header_keys_are_null(self) -> None:
        api = MagicMock()
        api.request.return_value = (200, {"id": TX_ID})
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "reconciliation_note": "n"},
        )
        self.assertIsNone(result["transaction"]["currency"])
        self.assertIsNone(result["transaction"]["budget_currency"])
        self.assertIsNone(result["transaction"]["planned_rate"])


class PutTransactionLinesFxTests(unittest.TestCase):
    """T2.3 / T6.3 — budget_amount is output-only."""

    def test_handler_rejects_budget_amount_without_http(self) -> None:
        api = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "transaction_id": TX_ID,
                    "lines": [
                        {
                            "line_no": 1,
                            "amount": "6606.70",
                            "assignment": ASSIGNMENT,
                            "budget_amount": "72.70",
                        }
                    ],
                },
            )
        self.assertIn("budget_amount", str(ctx.exception))
        api.request.assert_not_called()

    def test_schema_rejects_budget_amount(self) -> None:
        tools = asyncio.run(server.list_tools())
        put_tool = next(t for t in tools if t.name == "put_transaction_lines")
        items = put_tool.inputSchema["properties"]["lines"]["items"]
        self.assertFalse(items.get("additionalProperties", True))
        self.assertNotIn("budget_amount", items.get("properties") or {})
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "transaction_id": TX_ID,
                    "lines": [
                        {
                            "line_no": 1,
                            "amount": "4000.00",
                            "assignment": ASSIGNMENT,
                            "budget_amount": "44.02",
                        }
                    ],
                },
                put_tool.inputSchema,
            )

    def test_put_transaction_schema_omits_fx_inputs(self) -> None:
        tools = asyncio.run(server.list_tools())
        tool = next(t for t in tools if t.name == "put_transaction")
        props = tool.inputSchema.get("properties") or {}
        self.assertNotIn("currency", props)
        self.assertNotIn("budget_currency", props)
        self.assertNotIn("planned_rate", props)


class QueryPlanFactFxTests(unittest.TestCase):
    """T6.2 — months grain is period + HTTP row currency."""

    def test_two_currencies_same_month(self) -> None:
        api = _PlanFactApi(
            [
                {
                    "kind": "row",
                    "budget_item_id": ITEM_ID,
                    "currency": "EUR",
                    "plan_amount": "0",
                    "actual_amount": "72.70",
                    "variance": "-72.70",
                },
                {
                    "kind": "row",
                    "budget_item_id": ITEM_ID,
                    "currency": "RUB",
                    "plan_amount": "1300.00",
                    "actual_amount": "0",
                    "variance": "1300.00",
                },
            ]
        )
        rows = _qpf.fetch_month_rows(
            api, "version-1", "2026-07-01", ITEM_ID, "Article"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].currency, "EUR")
        self.assertEqual(rows[0].fact, 72.7)
        self.assertEqual(rows[1].currency, "RUB")
        self.assertEqual(rows[1].plan, 1300.0)

        with patch.object(
            server, "get_session", return_value=(api, "http://127.0.0.1:8000")
        ):
            with patch.object(server, "active_budget_version_id", return_value="version-1"):
                with patch.object(
                    server,
                    "resolve_budget_item_id",
                    return_value=(ITEM_ID, "Article"),
                ):
                    result = server._handle_query_plan_fact(
                        {
                            "date_from": "2026-07",
                            "date_to": "2026-07",
                            "transactions": True,
                        }
                    )
        payload = json.loads(result[0].text)
        months = payload["months"]
        self.assertEqual(len(months), 2)
        self.assertEqual(months[0]["period"], months[1]["period"])
        self.assertEqual({m["currency"] for m in months}, {"EUR", "RUB"})
        drill_paths = [
            path
            for path in api.paths
            if "/plan-actual/transactions" in path
        ]
        self.assertEqual(len(drill_paths), 2)
        joined = "&".join(drill_paths)
        self.assertIn("currency=EUR", joined)
        self.assertIn("currency=RUB", joined)

    def test_empty_article_month_currency_null(self) -> None:
        api = _PlanFactApi([])
        rows = _qpf.fetch_month_rows(
            api, "version-1", "2026-07-01", ITEM_ID, "Article"
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].currency)
        self.assertEqual(rows[0].fact, 0.0)


class GetTransactionPassthroughTests(unittest.TestCase):
    """D-05 — HTTP body is not stripped of FX fields."""

    def test_get_transaction_keeps_budget_amount(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {
                "id": TX_ID,
                "currency": "RUB",
                "budget_currency": "EUR",
                "planned_rate": "90.8776",
                "lines": [{"line_no": 1, "amount": "6606.70", "budget_amount": "72.70"}],
            },
        )
        result = get_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID},
        )
        self.assertEqual(result["transaction"]["currency"], "RUB")
        self.assertEqual(result["transaction"]["lines"][0]["budget_amount"], "72.70")


class ImportLogFxTests(unittest.TestCase):
    """T5 — process_month copies error_code and error_details as-is."""

    def _entry(self, code: str, details: Any) -> dict[str, Any]:
        body = {
            "error": {
                "code": code,
                "message": "x",
                "details": details,
            }
        }
        return import_log_entry("tbank", 422, body, [Path("stmt.pdf")])

    def test_fx_rate_missing_copies_missing_rates(self) -> None:
        details = {
            "missing_rates": [
                {
                    "period": "2026-07-01",
                    "from_currency": "RUB",
                    "to_currency": "EUR",
                }
            ]
        }
        entry = self._entry("fx_rate_missing", details)
        self.assertEqual(entry["error_code"], "fx_rate_missing")
        self.assertEqual(entry["error_details"], details)

    def test_budget_currency_undefined_opaque_details(self) -> None:
        details = {"household_id": "default", "period": "2026-07-01"}
        entry = self._entry("budget_currency_undefined", details)
        self.assertEqual(entry["error_code"], "budget_currency_undefined")
        self.assertEqual(entry["error_details"], details)
        self.assertNotIn("missing_rates", entry["error_details"])

    def test_operation_currency_undefined_empty_details(self) -> None:
        entry = self._entry("operation_currency_undefined", {})
        self.assertEqual(entry["error_code"], "operation_currency_undefined")
        self.assertEqual(entry["error_details"], {})

    def test_process_month_stops_before_derive(self) -> None:
        entry = self._entry(
            "fx_rate_missing",
            {
                "missing_rates": [
                    {
                        "period": "2026-07-01",
                        "from_currency": "RUB",
                        "to_currency": "EUR",
                    }
                ]
            },
        )
        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(
                server, "resolve_budget_version_id", return_value="vid"
            ):
                with patch.object(server, "run_imports", return_value=[entry]):
                    with patch.object(server, "run_derive") as derive:
                        result = server._handle_process_month({"period": "2026-07"})
        payload = json.loads(result[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["log"]["imports"][0]["error_code"], "fx_rate_missing")
        derive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
