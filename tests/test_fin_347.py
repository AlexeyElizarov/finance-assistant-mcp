"""Unit tests for FIN-347 operation posted amount/currency MCP surfaces."""

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
from put_transaction_category import put_transaction_category  # noqa: E402

import server  # noqa: E402

_QT_SPEC = importlib.util.spec_from_file_location(
    "query_transactions_fin347",
    _SCRIPTS / "query-transactions.py",
)
assert _QT_SPEC is not None and _QT_SPEC.loader is not None
_qt = importlib.util.module_from_spec(_QT_SPEC)
sys.modules["query_transactions_fin347"] = _qt
_QT_SPEC.loader.exec_module(_qt)

TX_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _api_tx_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": TX_ID,
        "date_display": "15.07.2026",
        "amount": "10.00",
        "debit_credit_indicator": "D",
        "description": "Card purchase",
        "transaction_category": "C0001",
        "transaction_type": "C",
        "provider": "tbank",
    }
    row.update(overrides)
    return row


def _tool_schema(name: str) -> dict[str, Any]:
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == name)
    return tool.inputSchema


class QueryTransactionsPostedTests(unittest.TestCase):
    """Posted pair on non-aggregated query_transactions rows."""

    def test_missing_keys_are_null_without_amount_fallback(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [_api_tx_row(amount="10.00", currency="EUR")],
            "meta": {},
        }
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertIsNone(rows[0].posted_amount)
        self.assertIsNone(rows[0].posted_currency)

        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=rows):
                result = server._handle_query_transactions({"period": "2026-07"})
        payload = json.loads(result[0].text)
        row = payload["rows"][0]
        self.assertIsNone(row["posted_amount"])
        self.assertIsNone(row["posted_currency"])
        self.assertEqual(row["amount"], 10.0)
        self.assertNotEqual(row["posted_amount"], row["amount"])

    def test_stored_posted_pair_passthrough(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [
                _api_tx_row(
                    currency="EUR",
                    posted_amount="950.00",
                    posted_currency="RUB",
                )
            ],
            "meta": {},
        }
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertEqual(rows[0].posted_amount, "950.00")
        self.assertEqual(rows[0].posted_currency, "RUB")
        self.assertEqual(rows[0].amount, 10.0)

    def test_group_by_month_omits_posted_keys(self) -> None:
        row = _qt.Row(
            date_display="15.07.2026",
            amount=10.0,
            indicator="D",
            description="x",
            category="C0001",
            provider="tbank",
            id=TX_ID,
            posted_amount="950.00",
            posted_currency="RUB",
        )
        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=[row]):
                result = server._handle_query_transactions(
                    {"period": "2026-07", "group_by": "month"}
                )
        payload = json.loads(result[0].text)
        group = payload["groups"][0]
        self.assertNotIn("posted_amount", group)
        self.assertNotIn("posted_currency", group)


class GetTransactionPostedTests(unittest.TestCase):
    """D-05 — get_transaction always exposes posted keys."""

    def test_keeps_posted_pair(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {
                "id": TX_ID,
                "amount": "10.00",
                "currency": "EUR",
                "posted_amount": "950.00",
                "posted_currency": "RUB",
            },
        )
        result = get_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID},
        )
        self.assertEqual(result["transaction"]["posted_amount"], "950.00")
        self.assertEqual(result["transaction"]["posted_currency"], "RUB")

    def test_missing_keys_are_null(self) -> None:
        api = MagicMock()
        api.request.return_value = (200, {"id": TX_ID, "amount": "10.00"})
        result = get_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID},
        )
        self.assertIsNone(result["transaction"]["posted_amount"])
        self.assertIsNone(result["transaction"]["posted_currency"])


class PutTransactionPostedTests(unittest.TestCase):
    """D-06 — response whitelist and schema reject for put_transaction."""

    def test_echoes_posted_pair(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {
                "id": TX_ID,
                "posted_amount": "950.00",
                "posted_currency": "RUB",
            },
        )
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "reconciliation_note": "n"},
        )
        self.assertEqual(result["transaction"]["posted_amount"], "950.00")
        self.assertEqual(result["transaction"]["posted_currency"], "RUB")
        body = api.request.call_args.kwargs.get("data") or {}
        self.assertNotIn("posted_amount", body)
        self.assertNotIn("posted_currency", body)

    def test_missing_posted_keys_are_null(self) -> None:
        api = MagicMock()
        api.request.return_value = (200, {"id": TX_ID})
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "reconciliation_note": "n"},
        )
        self.assertIsNone(result["transaction"]["posted_amount"])
        self.assertIsNone(result["transaction"]["posted_currency"])

    def test_schema_rejects_posted_keys(self) -> None:
        schema = _tool_schema("put_transaction")
        self.assertIs(schema.get("additionalProperties"), False)
        props = schema.get("properties") or {}
        self.assertNotIn("posted_amount", props)
        self.assertNotIn("posted_currency", props)
        for key in ("posted_amount", "posted_currency"):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(
                    {
                        "transaction_id": TX_ID,
                        "reconciliation_note": "n",
                        key: "950.00",
                    },
                    schema,
                )


class PutTransactionCategoryPostedTests(unittest.TestCase):
    """D-06 — response whitelist and schema reject for category facade."""

    def test_echoes_posted_pair(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {
                "id": TX_ID,
                "transaction_type": "C",
                "transaction_category": "C0001",
                "posted_amount": "950.00",
                "posted_currency": "RUB",
            },
        )
        result = put_transaction_category(
            api,
            profile="cand",
            base="http://test",
            transaction_id=TX_ID,
            transaction_type="C",
            transaction_category="C0001",
        )
        self.assertEqual(result["transaction"]["posted_amount"], "950.00")
        self.assertEqual(result["transaction"]["posted_currency"], "RUB")

    def test_missing_posted_keys_are_null(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {
                "id": TX_ID,
                "transaction_type": "C",
                "transaction_category": "C0001",
            },
        )
        result = put_transaction_category(
            api,
            profile="cand",
            base="http://test",
            transaction_id=TX_ID,
            transaction_type="C",
            transaction_category="C0001",
        )
        self.assertIsNone(result["transaction"]["posted_amount"])
        self.assertIsNone(result["transaction"]["posted_currency"])

    def test_schema_rejects_posted_keys(self) -> None:
        schema = _tool_schema("put_transaction_category")
        self.assertIs(schema.get("additionalProperties"), False)
        props = schema.get("properties") or {}
        self.assertNotIn("posted_amount", props)
        self.assertNotIn("posted_currency", props)
        for key in ("posted_amount", "posted_currency"):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(
                    {
                        "transaction_id": TX_ID,
                        "transaction_type": "C",
                        "transaction_category": "C0001",
                        key: "950.00",
                    },
                    schema,
                )


class ImportLogPostedTests(unittest.TestCase):
    """Universal error_code / error_details passthrough for posted-pair codes."""

    def _entry(self, code: str, details: Any) -> dict[str, Any]:
        body = {
            "error": {
                "code": code,
                "message": "x",
                "details": details,
            }
        }
        return import_log_entry("tbank", 422, body, [Path("stmt.pdf")])

    def test_posted_amount_required_empty_details(self) -> None:
        entry = self._entry("posted_amount_required", {})
        self.assertEqual(entry["error_code"], "posted_amount_required")
        self.assertEqual(entry["error_details"], {})

    def test_posted_currency_mismatch_opaque_details(self) -> None:
        details = {"posted_currency": "USD", "source_currency": "RUB"}
        entry = self._entry("posted_currency_mismatch", details)
        self.assertEqual(entry["error_code"], "posted_currency_mismatch")
        self.assertEqual(entry["error_details"], details)

    def test_process_month_stops_before_derive(self) -> None:
        entry = self._entry("posted_amount_required", {})
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
        self.assertEqual(
            payload["log"]["imports"][0]["error_code"], "posted_amount_required"
        )
        derive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
