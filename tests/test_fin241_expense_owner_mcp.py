"""Unit tests for FIN-241 expense_owner on put_transaction_category / query_transactions."""

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

TX_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"


def _load_query_transactions():
    path = _SCRIPTS / "query-transactions.py"
    spec = importlib.util.spec_from_file_location("query_transactions_fin241", path)
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
            "transaction_type": "C",
            "transaction_category": "C0003",
            "category_source": "manual",
            "classification_status": "classified",
            "reconciliation_note": "",
            "expense_owner": "nikolai",
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


class Fin241PutExpenseOwnerTests(unittest.TestCase):
    """FIN-241 T1–T11, T13–T15 for put_transaction_category."""

    def test_t1_owner_only_set(self) -> None:
        api = _PatchMockApi()
        result = put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            expense_owner="nikolai",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["transaction"]["expense_owner"], "nikolai")
        assert api.last_body is not None
        self.assertEqual(api.last_body, {"expense_owner": "nikolai"})
        self.assertNotIn("transaction_type", api.last_body)

    def test_t2_clear_null(self) -> None:
        api = _PatchMockApi()
        api.body = {**api.body, "expense_owner": None}
        result = put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            expense_owner=None,
        )
        assert api.last_body is not None
        self.assertEqual(api.last_body, {"expense_owner": None})
        self.assertIsNone(result["transaction"]["expense_owner"])

    def test_t3_clear_empty_string(self) -> None:
        api = _PatchMockApi()
        api.body = {**api.body, "expense_owner": None}
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            expense_owner="",
        )
        assert api.last_body is not None
        self.assertEqual(api.last_body["expense_owner"], "")

    def test_t3a_whitespace_pass_through(self) -> None:
        api = _PatchMockApi()
        api.body = {**api.body, "expense_owner": None}
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            expense_owner="   ",
        )
        assert api.last_body is not None
        self.assertEqual(api.last_body["expense_owner"], "   ")

    def test_t4_type_category_without_owner(self) -> None:
        api = _PatchMockApi()
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            transaction_type="P",
            transaction_category="P0002",
        )
        assert api.last_body is not None
        self.assertEqual(
            api.last_body,
            {"transaction_type": "P", "transaction_category": "P0002"},
        )
        self.assertNotIn("expense_owner", api.last_body)

    def test_t5_type_category_with_owner(self) -> None:
        api = _PatchMockApi()
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            transaction_type="C",
            transaction_category="C0003",
            expense_owner="nikolai",
        )
        assert api.last_body is not None
        self.assertEqual(
            api.last_body,
            {
                "transaction_type": "C",
                "transaction_category": "C0003",
                "expense_owner": "nikolai",
            },
        )

    def test_t6_member_422_codes(self) -> None:
        for code in ("unknown_member", "inactive_member", "no_active_household"):
            api = _PatchMockApi()
            api.status = 422
            api.body = {
                "error": {"code": code, "message": f"fail {code}"},
            }
            with self.assertRaises(RuntimeError) as ctx:
                put_transaction_category(
                    api,
                    profile="cand",
                    base="http://127.0.0.1:8000",
                    transaction_id=TX_ID,
                    expense_owner="no-such-member",
                )
            self.assertIn(code, str(ctx.exception))
            self.assertEqual(api.call_count, 1)

    def test_t7_type_without_category(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                transaction_type="P",
            )
        self.assertIn("together", str(ctx.exception))
        self.assertEqual(api.call_count, 0)

    def test_t8_neither_type_nor_owner(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
            )
        self.assertIn("expense_owner", str(ctx.exception))
        self.assertEqual(api.call_count, 0)

    def test_t9_category_source_forbidden(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                expense_owner="nikolai",
                category_source="manual",
            )
        self.assertIn("category_source", str(ctx.exception))
        self.assertEqual(api.call_count, 0)

    def test_t10_period_closed(self) -> None:
        api = _PatchMockApi()
        api.status = 422
        api.body = {"error": {"code": "period_closed", "message": "closed"}}
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                expense_owner="nikolai",
                allow_closed=False,
            )
        self.assertIn("period_closed", str(ctx.exception))

    def test_t11_schema_required_and_owner_property(self) -> None:
        import asyncio

        tools_list = asyncio.run(server.list_tools())
        tool = next(t for t in tools_list if t.name == "put_transaction_category")
        self.assertEqual(tool.inputSchema.get("required"), ["transaction_id"])
        owner_schema = tool.inputSchema["properties"]["expense_owner"]
        self.assertEqual(owner_schema["type"], ["string", "null"])

    def test_t13_owner_only_with_note(self) -> None:
        api = _PatchMockApi()
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            expense_owner="nikolai",
            reconciliation_note="partner spend",
        )
        assert api.last_body is not None
        self.assertEqual(
            api.last_body,
            {
                "expense_owner": "nikolai",
                "reconciliation_note": "partner spend",
            },
        )

    def test_t13a_null_owner_with_note(self) -> None:
        api = _PatchMockApi()
        api.body = {**api.body, "expense_owner": None}
        put_transaction_category(
            api,
            profile="cand",
            base="http://127.0.0.1:8000",
            transaction_id=TX_ID,
            expense_owner=None,
            reconciliation_note="owner cleared after reconciliation",
        )
        assert api.last_body is not None
        self.assertEqual(
            api.last_body,
            {
                "expense_owner": None,
                "reconciliation_note": "owner cleared after reconciliation",
            },
        )

    def test_t14_note_only_rejected(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError):
            put_transaction_category(
                api,
                profile="cand",
                base="http://127.0.0.1:8000",
                transaction_id=TX_ID,
                reconciliation_note="checked",
            )
        self.assertEqual(api.call_count, 0)

    def test_t15_type_category_note_without_owner(self) -> None:
        api = _PatchMockApi()
        put_transaction_category(
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
        self.assertNotIn("expense_owner", api.last_body)

    def test_handler_passes_expense_owner_key(self) -> None:
        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(
                server,
                "put_transaction_category",
                return_value={
                    "ok": True,
                    "profile": "cand",
                    "base": "http://127.0.0.1:8000",
                    "transaction": {"id": TX_ID, "expense_owner": "nikolai"},
                },
            ) as mock_put:
                server._handle_put_transaction_category(
                    {
                        "profile": "cand",
                        "transaction_id": TX_ID,
                        "expense_owner": "nikolai",
                    },
                )
        kwargs = mock_put.call_args.kwargs
        self.assertEqual(kwargs["expense_owner"], "nikolai")
        self.assertNotIn("reconciliation_note", kwargs)


class Fin241QueryExpenseOwnerTests(unittest.TestCase):
    """FIN-241 T12 — query_transactions expense_owner."""

    def test_t12_row_has_expense_owner(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [
                {
                    "id": TX_ID,
                    "date_display": "01.07.2026",
                    "amount": "10,00",
                    "debit_credit_indicator": "D",
                    "description": "Partner",
                    "transaction_category": "C0003",
                    "transaction_type": "C",
                    "expense_owner": "nikolai",
                    "provider": "sparkasse_sepa",
                },
                {
                    "id": "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
                    "date_display": "02.07.2026",
                    "amount": "5,00",
                    "debit_credit_indicator": "D",
                    "description": "No owner key",
                    "transaction_category": "C0001",
                    "transaction_type": "C",
                    "provider": "sparkasse_sepa",
                },
            ],
            "meta": {},
        }
        rows = _qt.fetch_rows(api, _qt.normalize_query_args(period="2026-07"))
        self.assertEqual(rows[0].expense_owner, "nikolai")
        self.assertIsNone(rows[1].expense_owner)

        with patch.object(
            server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")
        ):
            with patch.object(server, "fetch_rows", return_value=rows):
                result = server._handle_query_transactions({"period": "2026-07"})
        payload = json.loads(result[0].text)
        self.assertEqual(payload["rows"][0]["expense_owner"], "nikolai")
        self.assertIsNone(payload["rows"][1]["expense_owner"])
        self.assertIn("expense_owner", payload["rows"][1])
        self.assertEqual(payload["rows"][0]["id"], TX_ID)
        self.assertEqual(payload["rows"][0]["transaction_type"], "C")

    def test_t12_group_by_month_unchanged(self) -> None:
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
