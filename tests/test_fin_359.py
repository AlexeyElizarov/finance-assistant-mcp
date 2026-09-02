"""Unit tests for FIN-359 bank account on operations MCP surfaces."""

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
from put_transaction import put_transaction  # noqa: E402
from put_transaction_category import put_transaction_category  # noqa: E402

import server  # noqa: E402

_QT_SPEC = importlib.util.spec_from_file_location(
    "query_transactions_fin359",
    _SCRIPTS / "query-transactions.py",
)
assert _QT_SPEC is not None and _QT_SPEC.loader is not None
_qt = importlib.util.module_from_spec(_QT_SPEC)
sys.modules["query_transactions_fin359"] = _qt
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


class QueryTransactionsBankAccountTests(unittest.TestCase):
    """D-04 — bank_account_id on rows and filter."""

    def test_missing_key_is_null(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {"rows": [_api_tx_row()], "meta": {}}
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertIsNone(rows[0].bank_account_id)

        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=rows):
                result = server._handle_query_transactions({"period": "2026-07"})
        payload = json.loads(result[0].text)
        self.assertIsNone(payload["rows"][0]["bank_account_id"])
        self.assertIn("bank_account_id", payload["rows"][0])

    def test_stored_value_passthrough(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [_api_tx_row(bank_account_id="acc-c24")],
            "meta": {},
        }
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertEqual(rows[0].bank_account_id, "acc-c24")

    def test_filter_strip_and_empty_and_sentinel(self) -> None:
        path_stripped = _qt.build_query_path(
            _qt.normalize_query_args(
                period="2026-07",
                bank_account_id="  acc-c24  ",
            )
        )
        self.assertIn("bank_account_id=acc-c24", path_stripped)
        self.assertNotIn("%20", path_stripped.split("bank_account_id=")[-1])

        path_empty = _qt.build_query_path(
            _qt.normalize_query_args(period="2026-07", bank_account_id="   ")
        )
        self.assertNotIn("bank_account_id", path_empty)

        path_sentinel = _qt.build_query_path(
            _qt.normalize_query_args(
                period="2026-07",
                bank_account_id="__empty__",
            )
        )
        self.assertIn("bank_account_id=__empty__", path_sentinel)

    def test_bank_account_id_alone_is_active_filter(self) -> None:
        args = _qt.normalize_query_args(bank_account_id="acc-c24")
        self.assertTrue(_qt._has_active_filter(args))
        path = _qt.build_query_path(args)
        self.assertIn("bank_account_id=acc-c24", path)

    def test_group_by_month_omits_bank_account_id(self) -> None:
        row = _qt.Row(
            date_display="15.07.2026",
            amount=10.0,
            indicator="D",
            description="x",
            category="C0001",
            provider="tbank",
            id=TX_ID,
            bank_account_id="acc-c24",
        )
        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=[row]):
                result = server._handle_query_transactions(
                    {"period": "2026-07", "group_by": "month"}
                )
        payload = json.loads(result[0].text)
        self.assertNotIn("bank_account_id", payload["groups"][0])


class GetTransactionBankAccountTests(unittest.TestCase):
    """D-05 — get_transaction always exposes bank_account_id."""

    def test_keeps_value(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {"id": TX_ID, "bank_account_id": "acc-tbank-rub"},
        )
        result = get_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID},
        )
        self.assertEqual(result["transaction"]["bank_account_id"], "acc-tbank-rub")

    def test_missing_key_is_null(self) -> None:
        api = MagicMock()
        api.request.return_value = (200, {"id": TX_ID})
        result = get_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID},
        )
        self.assertIsNone(result["transaction"]["bank_account_id"])


class PutTransactionBankAccountTests(unittest.TestCase):
    """D-06 / D-08 — put_transaction bank_account_id states and errors."""

    def test_four_input_states(self) -> None:
        api = MagicMock()
        api.request.return_value = (200, {"id": TX_ID, "bank_account_id": "acc-c24"})

        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "reconciliation_note": "n"},
        )
        self.assertNotIn("bank_account_id", api.request.call_args.kwargs["data"])

        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "bank_account_id": None},
        )
        self.assertEqual(api.request.call_args.kwargs["data"], {"bank_account_id": None})

        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "bank_account_id": "   "},
        )
        self.assertEqual(api.request.call_args.kwargs["data"], {"bank_account_id": ""})

        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "bank_account_id": "  acc-c24  "},
        )
        self.assertEqual(
            api.request.call_args.kwargs["data"],
            {"bank_account_id": "acc-c24"},
        )

    def test_response_whitelist(self) -> None:
        api = MagicMock()
        api.request.return_value = (200, {"id": TX_ID, "bank_account_id": "acc-c24"})
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "bank_account_id": "acc-c24"},
        )
        self.assertEqual(result["transaction"]["bank_account_id"], "acc-c24")
        self.assertNotIn("payment_instrument_id", result["transaction"])

        api.request.return_value = (200, {"id": TX_ID})
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "reconciliation_note": "n"},
        )
        self.assertIsNone(result["transaction"]["bank_account_id"])

    def test_error_preserves_http_code_and_body(self) -> None:
        api = MagicMock()
        error_body = {
            "error": {
                "code": "bank_account_required",
                "message": "Bank account is required.",
                "details": {},
            }
        }
        api.request.return_value = (422, error_body)
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "bank_account_id": None},
            )
        text = str(ctx.exception)
        self.assertIn("HTTP 422", text)
        self.assertIn("bank_account_required", text)
        self.assertIn("Bank account is required.", text)

    def test_domain_error_codes(self) -> None:
        cases = (
            (404, "bank_account_not_found", "acc-missing"),
            (422, "period_closed", "acc-c24"),
            (422, "validation_error", "__empty__"),
        )
        for status, code, value in cases:
            api = MagicMock()
            api.request.return_value = (
                status,
                {"error": {"code": code, "message": code, "details": {}}},
            )
            with self.assertRaises(RuntimeError) as ctx:
                put_transaction(
                    api,
                    profile="cand",
                    base="http://test",
                    arguments={"transaction_id": TX_ID, "bank_account_id": value},
                )
            self.assertIn(code, str(ctx.exception))
            self.assertIn(f"HTTP {status}", str(ctx.exception))

    def test_schema_accepts_bank_account_id(self) -> None:
        schema = _tool_schema("put_transaction")
        props = schema.get("properties") or {}
        self.assertIn("bank_account_id", props)
        jsonschema.validate(
            {"transaction_id": TX_ID, "bank_account_id": "acc-c24"},
            schema,
        )
        jsonschema.validate(
            {"transaction_id": TX_ID, "bank_account_id": None},
            schema,
        )


class PutTransactionCategoryBankAccountTests(unittest.TestCase):
    """D-07 — category facade response and schema reject."""

    def test_echoes_bank_account_id(self) -> None:
        api = MagicMock()
        api.request.return_value = (
            200,
            {
                "id": TX_ID,
                "transaction_type": "C",
                "transaction_category": "C0001",
                "bank_account_id": "acc-c24",
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
        self.assertEqual(result["transaction"]["bank_account_id"], "acc-c24")

    def test_missing_key_is_null(self) -> None:
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
        self.assertIsNone(result["transaction"]["bank_account_id"])

    def test_schema_rejects_bank_account_id_before_http(self) -> None:
        schema = _tool_schema("put_transaction_category")
        self.assertIs(schema.get("additionalProperties"), False)
        props = schema.get("properties") or {}
        self.assertNotIn("bank_account_id", props)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "transaction_id": TX_ID,
                    "transaction_type": "C",
                    "transaction_category": "C0001",
                    "bank_account_id": "acc-c24",
                },
                schema,
            )


if __name__ == "__main__":
    unittest.main()
