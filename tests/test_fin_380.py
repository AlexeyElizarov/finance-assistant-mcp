"""Unit tests for FIN-380 retirement of specialized match MCP tools."""

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
    "list_internal_transfer_matches",
    "get_internal_transfer_match",
    "create_internal_transfer_match",
    "create_internal_transfer_matches",
    "delete_internal_transfer_match",
    "delete_internal_transfer_matches",
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
_UNREGISTERED_PROBE = "fin_380_never_registered_tool"
_SET_A_ITEMS = [
    {"debit_credit_indicator": "debit", "line_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
    {"debit_credit_indicator": "credit", "line_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
    {"debit_credit_indicator": "credit", "line_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"},
    {"debit_credit_indicator": "credit", "line_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"},
]
_RETIRED_CALLS = (
    ("list_internal_transfer_matches", {"profile": "cand"}),
    ("get_internal_transfer_match", {"match_id": _UUID}),
    ("create_internal_transfer_match", {"items": _SET_A_ITEMS}),
    ("create_internal_transfer_matches", {"items": [_SET_A_ITEMS]}),
    ("delete_internal_transfer_match", {"match_id": _UUID}),
    ("delete_internal_transfer_matches", {"ids": [_UUID]}),
)


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


class Fin380RegistrationTests(unittest.TestCase):
    """T1.1: retired names absent, FIN-355 names present."""

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


class Fin380UnknownToolTests(unittest.TestCase):
    """T1.2–T1.7: retired-name call matches unregistered tool (D-02)."""

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


class Fin380GenericSurfaceTests(unittest.TestCase):
    """T1.8: FIN-355 list remains callable."""

    def test_list_clearing_documents_internal_transfer(self) -> None:
        api = _MockApi(body={"clearing_documents": []})
        with patch.object(server, "get_session", return_value=(api, "http://test")):
            result = asyncio.run(
                server.call_tool(
                    "list_clearing_documents",
                    {"profile": "cand", "document_type": "internal_transfer"},
                )
            )
        payload = _payload(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["clearing_documents"], [])
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.calls[0][0], "GET")
        self.assertIn("/api/v1/clearing-documents", api.calls[0][1])
        self.assertIn("document_type=internal_transfer", api.calls[0][1])
        self.assertNotIn("/api/v1/internal-transfer-matches", api.calls[0][1])


if __name__ == "__main__":
    unittest.main()
