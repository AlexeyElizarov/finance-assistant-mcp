"""Unit tests for FIN-377 retirement of expense-settlement MCP tools."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import server  # noqa: E402

_RETIRED_NAMES = (
    "create_expense_settlement",
    "get_expense_settlement",
    "patch_expense_settlement",
    "delete_expense_settlement",
    "list_expense_settlements",
    "get_line_settlement_state",
)
_FIN355_NAMES = (
    "list_clearing_documents",
    "get_clearing_document",
    "create_clearing_document",
    "create_clearing_documents",
    "patch_clearing_document",
    "delete_clearing_document",
    "delete_clearing_documents",
    "create_clearing_document_item",
    "list_clearing_document_items",
    "get_clearing_document_item",
    "patch_clearing_document_item",
    "delete_clearing_document_item",
)
_UUID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_LINE_EXPENSE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_LINE_FUNDING = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_UNREGISTERED_PROBE = "fin_377_never_registered_tool"
_EXPENSE_SETTLEMENT_ITEMS = [
    {"debit_credit_indicator": "debit", "line_id": _LINE_EXPENSE},
    {"debit_credit_indicator": "credit", "line_id": _LINE_FUNDING},
]
_CREATE_ARGS = {
    "profile": "cand",
    "base": "http://test",
    "document_type": "expense_settlement",
    "items": _EXPENSE_SETTLEMENT_ITEMS,
}
_RETIRED_CALLS = (
    (
        "create_expense_settlement",
        {
            "compensating_line_id": _LINE_FUNDING,
            "expense_line_id": _LINE_EXPENSE,
            "amount": "50.00",
        },
    ),
    ("get_expense_settlement", {"settlement_id": _UUID}),
    ("patch_expense_settlement", {"settlement_id": _UUID, "amount": "20.00"}),
    ("delete_expense_settlement", {"settlement_id": _UUID}),
    ("list_expense_settlements", {"line_id": _UUID}),
    ("get_line_settlement_state", {"line_id": _UUID}),
)
_SAMPLE_DOCUMENT = {
    "id": _UUID,
    "profile_id": "cand",
    "document_type": "expense_settlement",
    "status": "open",
    "items": [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "debit_credit_indicator": "debit",
            "line_id": _LINE_EXPENSE,
            "clearing_amount": "50.00",
        },
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "debit_credit_indicator": "credit",
            "line_id": _LINE_FUNDING,
            "clearing_amount": "50.00",
        },
    ],
}


class _MockApi:
    """Stub ApiClient capturing HTTP calls."""

    def __init__(self, *, status: int = 200, body: Any | None = None) -> None:
        self.status = status
        self.body: Any = {} if body is None else body
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        self.calls.append((method, path, dict(data) if data is not None else None))
        return self.status, self.body


def _payload(result: list[Any]) -> dict[str, Any]:
    return json.loads(result[0].text)


def _tool_names() -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


class Fin377RegistrationTests(unittest.TestCase):
    """Retired names absent, FIN-355 names present, mcp-gaps without retired names."""

    def test_retired_names_not_registered(self) -> None:
        names = _tool_names()
        for name in _RETIRED_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(name, names)

    def test_fin355_names_remain_registered(self) -> None:
        names = _tool_names()
        for name in _FIN355_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, names)

    def test_mcp_gaps_omits_retired_names(self) -> None:
        text = (_ROOT / "mcp-gaps.md").read_text(encoding="utf-8")
        for name in _RETIRED_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(f"`{name}`", text)

    def test_mcp_gaps_keeps_fin355_names(self) -> None:
        text = (_ROOT / "mcp-gaps.md").read_text(encoding="utf-8")
        for name in _FIN355_NAMES:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", text)


class Fin377UnknownToolTests(unittest.TestCase):
    """D-02: retired-name call matches unregistered tool."""

    def test_retired_call_matches_unregistered_without_session_or_http(self) -> None:
        api = _MockApi()
        for name, arguments in _RETIRED_CALLS:
            with self.subTest(name=name):
                with patch.object(server, "get_session") as get_session:
                    with self.assertRaises(ValueError) as retired:
                        asyncio.run(server.call_tool(name, arguments))
                    with self.assertRaises(ValueError) as probe:
                        asyncio.run(server.call_tool(_UNREGISTERED_PROBE, arguments))
                self.assertEqual(str(retired.exception), f"Unknown tool: {name}")
                self.assertEqual(
                    str(probe.exception),
                    f"Unknown tool: {_UNREGISTERED_PROBE}",
                )
                get_session.assert_not_called()
                self.assertEqual(api.calls, [])

    def test_empty_arguments_same_as_unregistered(self) -> None:
        api = _MockApi()
        with patch.object(server, "get_session") as get_session:
            for name in _RETIRED_NAMES:
                with self.subTest(name=name):
                    with self.assertRaises(ValueError) as ctx:
                        asyncio.run(server.call_tool(name, {}))
                    self.assertEqual(str(ctx.exception), f"Unknown tool: {name}")
        get_session.assert_not_called()
        self.assertEqual(api.calls, [])


class Fin377GenericSurfaceTests(unittest.TestCase):
    """D-05: expense_settlement goes through FIN-355 HTTP paths."""

    def test_create_clearing_document_expense_settlement_posts_clearing_documents(
        self,
    ) -> None:
        api = _MockApi(status=201, body=_SAMPLE_DOCUMENT)
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            result = asyncio.run(
                server.call_tool("create_clearing_document", _CREATE_ARGS)
            )
        payload = _payload(result)
        self.assertTrue(payload["ok"])
        document = payload["clearing_document"]
        self.assertEqual(document["document_type"], "expense_settlement")
        self.assertNotIn("amount", document)
        self.assertNotIn("compensating_line_id", document)
        self.assertNotIn("expense_line_id", document)
        self.assertEqual(len(api.calls), 1)
        method, path, body = api.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/v1/clearing-documents")
        self.assertNotIn("/api/v1/expense-settlements", path)
        self.assertEqual(
            body,
            {
                "document_type": "expense_settlement",
                "items": _EXPENSE_SETTLEMENT_ITEMS,
            },
        )
        self.assertNotIn("amount", body)
        self.assertNotIn("compensating_line_id", body)
        self.assertNotIn("expense_line_id", body)

    def test_list_clearing_documents_expense_settlement_uses_clearing_path(
        self,
    ) -> None:
        api = _MockApi(body={"clearing_documents": []})
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            result = asyncio.run(
                server.call_tool(
                    "list_clearing_documents",
                    {
                        "profile": "cand",
                        "line_id": _LINE_EXPENSE,
                        "document_type": "expense_settlement",
                    },
                )
            )
        payload = _payload(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["clearing_documents"], [])
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][0], "GET")
        self.assertIn("/api/v1/clearing-documents", api.calls[0][1])
        self.assertIn("document_type=expense_settlement", api.calls[0][1])
        self.assertIn(f"line_id={_LINE_EXPENSE}", api.calls[0][1])
        self.assertNotIn("/api/v1/expense-settlements", api.calls[0][1])

    def test_patch_preserves_invalid_clearing_document_operation(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "invalid_clearing_document_operation",
                    "message": "header patch is not allowed",
                }
            },
        )
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            result = asyncio.run(
                server.call_tool(
                    "patch_clearing_document",
                    {
                        "profile": "cand",
                        "base": "http://test",
                        "document_id": _UUID,
                        "status": "closed",
                    },
                )
            )
        payload = _payload(result)
        self.assertFalse(payload["ok"])
        self.assertIn("invalid_clearing_document_operation", payload["error"])
        self.assertNotIn("expense_settlement_not_found", payload["error"])

    def test_create_preserves_line_in_expense_settlement(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "line_in_expense_settlement",
                    "message": "line already in expense settlement",
                }
            },
        )
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            result = asyncio.run(
                server.call_tool("create_clearing_document", _CREATE_ARGS)
            )
        payload = _payload(result)
        self.assertFalse(payload["ok"])
        self.assertIn("line_in_expense_settlement", payload["error"])
        self.assertNotIn("expense_settlement_not_found", payload["error"])
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][0], "POST")
        self.assertEqual(api.calls[0][1], "/api/v1/clearing-documents")


if __name__ == "__main__":
    unittest.main()
