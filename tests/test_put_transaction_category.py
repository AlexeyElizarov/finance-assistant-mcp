"""Unit tests for FIN-211 put_transaction_category and query_transactions lookup fields."""

from __future__ import annotations

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

from put_transaction_category import put_transaction_category  # noqa: E402

import server  # noqa: E402

TX_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"


def _load_query_transactions():
    path = _SCRIPTS / "query-transactions.py"
    spec = importlib.util.spec_from_file_location("query_transactions_fin211", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_qt = _load_query_transactions()


class _PatchMockApi:
    """API stub for PATCH …/category."""

    def __init__(self) -> None:
        self.last_method: str | None = None
        self.last_path: str | None = None
        self.last_body: dict[str, Any] | None = None
        self.status = 200
        self.body: dict[str, Any] | Any = {
            "id": TX_ID,
            "transaction_type": "P",
            "transaction_category": "P0002",
            "category_source": "manual",
            "classification_status": "classified",
            "reconciliation_note": "",
        }
        self.call_count = 0

    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
    ) -> tuple[int, Any]:
        self.call_count += 1
        self.last_method = method
        self.last_path = path
        self.last_body = data
        return self.status, self.body


class PutTransactionCategoryTests(unittest.TestCase):
    """FIN-211 T1–T10."""

    def test_t1_happy_path_d09(self) -> None:
        api = _PatchMockApi()
        result = put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            transaction_type="P",
            transaction_category="P0002",
        )
        self.assertTrue(result["ok"])
        tx = result["transaction"]
        self.assertEqual(tx["transaction_type"], "P")
        self.assertEqual(tx["transaction_category"], "P0002")
        self.assertEqual(tx["category_source"], "manual")
        self.assertEqual(tx["classification_status"], "classified")
        self.assertEqual(api.last_method, "PATCH")
        assert api.last_body is not None
        self.assertEqual(
            api.last_body,
            {"transaction_type": "P", "transaction_category": "P0002"},
        )
        self.assertNotIn("category_source", api.last_body)
        self.assertIn("allow_closed=false", api.last_path or "")

    def test_t2_lowercase_type(self) -> None:
        api = _PatchMockApi()
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            transaction_type="p",
            transaction_category="P0002",
        )
        assert api.last_body is not None
        self.assertEqual(api.last_body["transaction_type"], "p")

    def test_t3_mismatch_422(self) -> None:
        api = _PatchMockApi()
        api.status = 422
        api.body = {
            "error": {
                "code": "validation_error",
                "message": "Категория C0001 типа C несовместима с типом транзакции P.",
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                transaction_type="P",
                transaction_category="C0001",
            )
        self.assertIn("422", str(ctx.exception))
        self.assertIn("validation_error", str(ctx.exception))
        self.assertEqual(api.call_count, 1)

    def test_t4_empty_category_before_http(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                transaction_type="P",
                transaction_category="   ",
            )
        self.assertIn("transaction_category", str(ctx.exception))
        self.assertEqual(api.call_count, 0)

    def test_t5_missing_type_before_http(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                transaction_type=None,
                transaction_category="P0002",
            )
        self.assertIn("transaction_type", str(ctx.exception))
        self.assertEqual(api.call_count, 0)

    def test_t6_category_source_forbidden(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                transaction_type="P",
                transaction_category="P0002",
                category_source="manual",
            )
        self.assertIn("category_source", str(ctx.exception))
        self.assertEqual(api.call_count, 0)

    def test_t7_period_closed(self) -> None:
        api = _PatchMockApi()
        api.status = 422
        api.body = {
            "error": {
                "code": "period_closed",
                "message": "Period is closed",
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                transaction_type="P",
                transaction_category="P0002",
                allow_closed=False,
            )
        self.assertIn("period_closed", str(ctx.exception))

    def test_t8_allow_closed_true(self) -> None:
        api = _PatchMockApi()
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            transaction_type="P",
            transaction_category="P0002",
            allow_closed=True,
        )
        self.assertIn("allow_closed=true", api.last_path or "")

    def test_t9_note_atomic(self) -> None:
        api = _PatchMockApi()
        api.body = {
            "id": TX_ID,
            "transaction_type": "P",
            "transaction_category": "P0002",
            "category_source": "manual",
            "classification_status": "classified",
            "reconciliation_note": "insurance refund",
        }
        result = put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            transaction_type="P",
            transaction_category="P0002",
            reconciliation_note="insurance refund",
        )
        assert api.last_body is not None
        self.assertEqual(
            api.last_body,
            {
                "transaction_type": "P",
                "transaction_category": "P0002",
                "reconciliation_note": "insurance refund",
            },
        )
        self.assertEqual(result["transaction"]["reconciliation_note"], "insurance refund")

    def test_t10_handler_schema_defaults(self) -> None:
        with patch.object(server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")):
            with patch.object(
                server,
                "put_transaction_category",
                return_value={
                    "ok": True,
                    "profile": "cand",
                    "base": "http://127.0.0.1:8000",
                    "transaction": {
                        "id": TX_ID,
                        "transaction_type": "P",
                        "transaction_category": "P0002",
                        "category_source": "manual",
                        "classification_status": "classified",
                        "reconciliation_note": "",
                    },
                },
            ) as mock_put:
                out = server._handle_put_transaction_category(
                    {
                        "profile": "cand",
                        "transaction_id": TX_ID,
                        "transaction_type": "P",
                        "transaction_category": "P0002",
                    },
                )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        mock_put.assert_called_once()
        kwargs = mock_put.call_args.kwargs
        self.assertFalse(kwargs["allow_closed"])
        self.assertNotIn("category_source", kwargs)
        self.assertNotIn("reconciliation_note", kwargs)

        import asyncio

        tools_list = asyncio.run(server.list_tools())
        tool = next(t for t in tools_list if t.name == "put_transaction_category")
        required = tool.inputSchema.get("required") or []
        self.assertEqual(
            set(required),
            {"transaction_id", "transaction_type", "transaction_category"},
        )


class QueryTransactionsLookupTests(unittest.TestCase):
    """FIN-211 T11 — additive id / transaction_type."""

    def test_t11_row_has_id_and_type_no_key(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [
                {
                    "id": TX_ID,
                    "date_display": "01.02.2026",
                    "amount": "10,00",
                    "debit_credit_indicator": "D",
                    "description": "Erstattung",
                    "transaction_category": "",
                    "transaction_type": "I",
                    "transaction_key": "should-not-surface",
                    "provider": "sparkasse_sepa",
                }
            ],
            "meta": {},
        }
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-02"))
        self.assertEqual(rows[0].id, TX_ID)
        self.assertEqual(rows[0].transaction_type, "I")
        self.assertFalse(hasattr(rows[0], "transaction_key"))

        mock_row = rows[0]
        with patch.object(server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")):
            with patch.object(server, "fetch_rows", return_value=[mock_row]):
                result = server._handle_query_transactions({"period": "2026-02"})
        payload = json.loads(result[0].text)
        row = payload["rows"][0]
        self.assertEqual(row["id"], TX_ID)
        self.assertEqual(row["transaction_type"], "I")
        self.assertNotIn("transaction_key", row)
        self.assertEqual(row["category"], "")
        self.assertEqual(row["provider"], "sparkasse_sepa")

    def test_t11_group_by_month_unchanged(self) -> None:
        mock_row = MagicMock()
        mock_row.date_display = "01.02.2026"
        mock_row.amount = 10.0
        mock_row.indicator = "D"
        mock_row.category = "C9999"
        mock_row.provider = "sparkasse_sepa"
        mock_row.description = "x"
        mock_row.id = TX_ID
        mock_row.transaction_type = "C"

        with patch.object(server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")):
            with patch.object(server, "fetch_rows", return_value=[mock_row]):
                result = server._handle_query_transactions(
                    {"period": "2026-02", "group_by": "month"},
                )
        payload = json.loads(result[0].text)
        self.assertIn("groups", payload)
        self.assertNotIn("rows", payload)
        self.assertEqual(payload["groups"][0]["month"], "2026-02")


if __name__ == "__main__":
    unittest.main()
