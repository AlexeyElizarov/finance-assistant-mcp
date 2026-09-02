"""Unit tests for FIN-271 expense settlement MCP tools."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from expense_settlements import (  # noqa: E402
    create_expense_settlement,
    delete_expense_settlement,
    get_expense_settlement,
    get_line_settlement_state,
    list_expense_settlements,
    patch_expense_settlement,
)

import server  # noqa: E402

SETTLEMENT_ID = "settlement-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
LINE_EXPENSE = "line-expense-55"
LINE_FUNDING = "line-funding-1"

_SAMPLE_SETTLEMENT = {
    "id": SETTLEMENT_ID,
    "compensating_line_id": LINE_FUNDING,
    "expense_line_id": LINE_EXPENSE,
    "amount": "55.00",
}

_SAMPLE_STATE = {
    "line_id": LINE_EXPENSE,
    "line_amount": "55.00",
    "settled_amount": "55.00",
    "unsettled_amount": "0.00",
    "settlement_status": "full",
}


class _MockApi:
    """Stub ApiClient for FIN-273 paths."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else dict(_SAMPLE_SETTLEMENT)
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
    def last_method(self) -> str | None:
        return self.calls[-1][0] if self.calls else None

    @property
    def last_path(self) -> str | None:
        return self.calls[-1][1] if self.calls else None

    @property
    def last_body(self) -> dict[str, Any] | None:
        return self.calls[-1][2] if self.calls else None


class CreateExpenseSettlementTests(unittest.TestCase):
    """FIN-271 T5 create coverage."""

    def test_create_path_and_body(self) -> None:
        api = _MockApi(status=201, body=dict(_SAMPLE_SETTLEMENT))
        result = create_expense_settlement(
            api,
            profile="cand",
            base="http://127.0.0.1:8004",
            arguments={
                "compensating_line_id": LINE_FUNDING,
                "expense_line_id": LINE_EXPENSE,
                "amount": "55.00",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "cand")
        self.assertEqual(result["settlement"]["id"], SETTLEMENT_ID)
        self.assertEqual(api.last_method, "POST")
        self.assertEqual(
            api.last_body,
            {
                "compensating_line_id": LINE_FUNDING,
                "expense_line_id": LINE_EXPENSE,
                "amount": "55.00",
            },
        )
        path = api.last_path or ""
        self.assertTrue(path.startswith("/api/v1/expense-settlements?"))
        self.assertIn("allow_closed=false", path)

    def test_allow_closed_query(self) -> None:
        api = _MockApi(status=201, body=dict(_SAMPLE_SETTLEMENT))
        create_expense_settlement(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "compensating_line_id": LINE_FUNDING,
                "expense_line_id": LINE_EXPENSE,
                "amount": "55.00",
                "allow_closed": True,
            },
        )
        self.assertIn("allow_closed=true", api.last_path or "")

    def test_amount_exceeded_422(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "settlement_amount_exceeded",
                    "message": "Settlement amount exceeds remaining settleable amount on a side.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            create_expense_settlement(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "compensating_line_id": LINE_FUNDING,
                    "expense_line_id": LINE_EXPENSE,
                    "amount": "999.00",
                },
            )
        text = str(ctx.exception)
        self.assertIn("422", text)
        self.assertIn("settlement_amount_exceeded", text)

    def test_value_error_empty_amount(self) -> None:
        api = _MockApi(status=201)
        with self.assertRaises(ValueError):
            create_expense_settlement(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "compensating_line_id": LINE_FUNDING,
                    "expense_line_id": LINE_EXPENSE,
                    "amount": "  ",
                },
            )
        self.assertEqual(api.calls, [])


class GetPatchDeleteTests(unittest.TestCase):
    """FIN-271 get / patch / delete coverage."""

    def test_get_happy(self) -> None:
        api = _MockApi(status=200, body=dict(_SAMPLE_SETTLEMENT))
        result = get_expense_settlement(
            api,
            profile="cand",
            base="http://test",
            arguments={"settlement_id": SETTLEMENT_ID},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.last_method, "GET")
        self.assertEqual(
            api.last_path,
            f"/api/v1/expense-settlements/{SETTLEMENT_ID}",
        )
        self.assertEqual(result["settlement"]["amount"], "55.00")

    def test_get_not_found(self) -> None:
        api = _MockApi(
            status=404,
            body={
                "error": {
                    "code": "expense_settlement_not_found",
                    "message": "Expense settlement not found.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_expense_settlement(
                api,
                profile="cand",
                base="http://test",
                arguments={"settlement_id": SETTLEMENT_ID},
            )
        self.assertIn("expense_settlement_not_found", str(ctx.exception))

    def test_patch_body_only_amount(self) -> None:
        patched = dict(_SAMPLE_SETTLEMENT)
        patched["amount"] = "20.00"
        api = _MockApi(status=200, body=patched)
        result = patch_expense_settlement(
            api,
            profile="cand",
            base="http://test",
            arguments={"settlement_id": SETTLEMENT_ID, "amount": "20.00"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.last_method, "PATCH")
        self.assertEqual(api.last_body, {"amount": "20.00"})
        self.assertIn("allow_closed=false", api.last_path or "")
        self.assertEqual(result["settlement"]["amount"], "20.00")

    def test_delete_204(self) -> None:
        api = _MockApi(status=204, body=b"")
        result = delete_expense_settlement(
            api,
            profile="cand",
            base="http://test",
            arguments={"settlement_id": SETTLEMENT_ID},
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted"])
        self.assertNotIn("settlement_id", result)
        self.assertEqual(api.last_method, "DELETE")
        self.assertIn(
            f"/api/v1/expense-settlements/{SETTLEMENT_ID}?",
            api.last_path or "",
        )

    def test_delete_not_found(self) -> None:
        api = _MockApi(
            status=404,
            body={
                "error": {
                    "code": "expense_settlement_not_found",
                    "message": "Expense settlement not found.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            delete_expense_settlement(
                api,
                profile="cand",
                base="http://test",
                arguments={"settlement_id": SETTLEMENT_ID},
            )
        self.assertIn("expense_settlement_not_found", str(ctx.exception))


class ListAndStateTests(unittest.TestCase):
    """FIN-271 list / settlement-state coverage."""

    def test_list_happy(self) -> None:
        api = _MockApi(
            status=200,
            body={"settlements": [dict(_SAMPLE_SETTLEMENT)]},
        )
        result = list_expense_settlements(
            api,
            profile="cand",
            base="http://test",
            arguments={"line_id": LINE_EXPENSE},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.last_method, "GET")
        self.assertEqual(
            api.last_path,
            f"/api/v1/expense-settlements?line_id={LINE_EXPENSE}",
        )
        self.assertEqual(len(result["settlements"]), 1)

    def test_list_empty(self) -> None:
        api = _MockApi(status=200, body={"settlements": []})
        result = list_expense_settlements(
            api,
            profile="cand",
            base="http://test",
            arguments={"line_id": LINE_EXPENSE},
        )
        self.assertEqual(result["settlements"], [])

    def test_list_empty_line_id(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            list_expense_settlements(
                api,
                profile="cand",
                base="http://test",
                arguments={"line_id": "  "},
            )
        self.assertEqual(api.calls, [])

    def test_state_happy(self) -> None:
        api = _MockApi(status=200, body=dict(_SAMPLE_STATE))
        result = get_line_settlement_state(
            api,
            profile="cand",
            base="http://test",
            arguments={"line_id": LINE_EXPENSE},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.last_method, "GET")
        self.assertEqual(
            api.last_path,
            f"/api/v1/transaction-lines/{LINE_EXPENSE}/settlement-state",
        )
        self.assertEqual(
            result["settlement_state"]["settlement_status"],
            "full",
        )

    def test_state_line_not_found(self) -> None:
        api = _MockApi(
            status=404,
            body={
                "error": {
                    "code": "line_not_found",
                    "message": "Operation line not found.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_line_settlement_state(
                api,
                profile="cand",
                base="http://test",
                arguments={"line_id": LINE_EXPENSE},
            )
        self.assertIn("line_not_found", str(ctx.exception))


class HandlerAndRegistrationTests(unittest.TestCase):
    """server handlers and tool list."""

    def _patch_session(self, api: _MockApi) -> Any:
        return patch("server.get_session", return_value=(api, "http://test"))

    def test_create_handler(self) -> None:
        api = _MockApi(status=201, body=dict(_SAMPLE_SETTLEMENT))
        with self._patch_session(api):
            out = server._handle_create_expense_settlement(
                {
                    "profile": "cand",
                    "compensating_line_id": LINE_FUNDING,
                    "expense_line_id": LINE_EXPENSE,
                    "amount": "55.00",
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["settlement"]["id"], SETTLEMENT_ID)

    def test_delete_handler(self) -> None:
        api = _MockApi(status=204, body=b"")
        with self._patch_session(api):
            out = server._handle_delete_expense_settlement(
                {"profile": "cand", "settlement_id": SETTLEMENT_ID}
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["deleted"])

    def test_tools_registered(self) -> None:
        import asyncio

        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        expected = {
            "create_expense_settlement",
            "get_expense_settlement",
            "patch_expense_settlement",
            "delete_expense_settlement",
            "list_expense_settlements",
            "get_line_settlement_state",
        }
        self.assertTrue(expected.issubset(names))
        create_tool = next(
            t for t in tools if t.name == "create_expense_settlement"
        )
        self.assertEqual(
            create_tool.inputSchema.get("required"),
            ["compensating_line_id", "expense_line_id", "amount"],
        )
        # Regression: settlement fields not added to query_transactions schema
        qt = next(t for t in tools if t.name == "query_transactions")
        qt_props = qt.inputSchema.get("properties") or {}
        self.assertNotIn("settlement_status", qt_props)
        self.assertNotIn("settled_amount", qt_props)


if __name__ == "__main__":
    unittest.main()
