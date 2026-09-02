"""Unit tests for FIN-265 put_transactions (batch merge-patch MCP)."""

from __future__ import annotations

import asyncio
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

from put_transaction import (  # noqa: E402
    BODY_FIELD_SCHEMA_PROPERTIES,
    DECLARED_BODY_FIELDS,
)
from put_transactions import (  # noqa: E402
    put_transactions,
    validate_batch_arguments,
)

import server  # noqa: E402

TX_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
TX_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"


class _SequentialPatchApi:
    """Stub ApiClient with per-call responses for sequential PATCH."""

    def __init__(self, responses: list[tuple[int, Any] | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        self.calls.append((method, path, dict(data) if data is not None else None))
        if not self._responses:
            raise AssertionError("unexpected extra HTTP call")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _ok_body(tx_id: str, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": tx_id,
        "transaction_type": "C",
        "transaction_category": "C0003",
        "category_source": "manual",
        "reconciliation_note": "",
        "expense_owner": None,
        "project": None,
        "project_source": None,
        "fund_id": None,
        "bank_account_id": "acc-1",
    }
    body.update(fields)
    return body


class PutTransactionsLibTests(unittest.TestCase):
    """FIN-265 T7 unit coverage."""

    def test_mixed_success_two_items(self) -> None:
        api = _SequentialPatchApi(
            [
                (200, _ok_body(TX_A, fund_id="fund.a")),
                (
                    404,
                    {
                        "error": {
                            "code": "unknown_fund",
                            "message": "not found",
                        }
                    },
                ),
            ]
        )
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "items": [
                    {"transaction_id": TX_A, "fund_id": "fund.a"},
                    {"transaction_id": TX_B, "fund_id": "missing"},
                ]
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"], {"total": 2, "succeeded": 1, "failed": 1})
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(result["results"][0]["ok"])
        self.assertEqual(result["results"][0]["transaction"]["fund_id"], "fund.a")
        self.assertFalse(result["results"][1]["ok"])
        self.assertIn("unknown_fund", result["results"][1]["error"])
        self.assertEqual(len(api.calls), 2)

    def test_empty_items_before_http(self) -> None:
        api = _SequentialPatchApi([])
        with self.assertRaises(ValueError):
            put_transactions(
                api,
                profile="cand",
                base="http://test",
                arguments={"items": []},
            )
        self.assertEqual(api.calls, [])

    def test_validate_batch_before_session(self) -> None:
        with self.assertRaises(ValueError):
            validate_batch_arguments({"items": []})
        with self.assertRaises(ValueError):
            validate_batch_arguments({})

    def test_assign_and_clear_fund_id(self) -> None:
        api = _SequentialPatchApi(
            [
                (200, _ok_body(TX_A, fund_id="fund.a")),
                (200, _ok_body(TX_B, fund_id=None)),
            ]
        )
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "items": [
                    {"transaction_id": TX_A, "fund_id": "fund.a"},
                    {"transaction_id": TX_B, "fund_id": None},
                ]
            },
        )
        self.assertEqual(api.calls[0][2], {"fund_id": "fund.a"})
        self.assertEqual(api.calls[1][2], {"fund_id": None})
        self.assertTrue(result["results"][0]["ok"])
        self.assertTrue(result["results"][1]["ok"])
        self.assertIsNone(result["results"][1]["transaction"]["fund_id"])

    def test_item_validation_before_http_t6(self) -> None:
        api = _SequentialPatchApi(
            [
                (200, _ok_body(TX_A, fund_id="fund.a")),
                (200, _ok_body(TX_B, fund_id="fund.b")),
            ]
        )
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "items": [
                    {"transaction_id": TX_A, "fund_id": "fund.a"},
                    {"fund_id": "fund.x"},
                    {"transaction_id": "   ", "fund_id": "fund.y"},
                    {"transaction_id": TX_B},
                    {"transaction_id": TX_B, "fund_id": "fund.b"},
                ]
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["total"], 5)
        self.assertEqual(result["summary"]["succeeded"], 2)
        self.assertEqual(result["summary"]["failed"], 3)
        self.assertTrue(result["results"][0]["ok"])
        self.assertFalse(result["results"][1]["ok"])
        self.assertEqual(result["results"][1]["transaction_id"], "")
        self.assertFalse(result["results"][2]["ok"])
        self.assertFalse(result["results"][3]["ok"])
        self.assertTrue(result["results"][4]["ok"])
        self.assertEqual(len(api.calls), 2)

    def test_duplicate_transaction_id_ordered_patches(self) -> None:
        api = _SequentialPatchApi(
            [
                (200, _ok_body(TX_A, fund_id="fund.x")),
                (200, _ok_body(TX_A, fund_id="fund.y")),
            ]
        )
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "items": [
                    {"transaction_id": TX_A, "fund_id": "fund.x"},
                    {"transaction_id": TX_A, "fund_id": "fund.y"},
                ]
            },
        )
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(api.calls[0][2], {"fund_id": "fund.x"})
        self.assertEqual(api.calls[1][2], {"fund_id": "fund.y"})
        self.assertIn(TX_A, api.calls[0][1])
        self.assertIn(TX_A, api.calls[1][1])
        self.assertTrue(result["results"][0]["ok"])
        self.assertTrue(result["results"][1]["ok"])

    def test_network_error_continues(self) -> None:
        api = _SequentialPatchApi(
            [
                ConnectionError("connection reset"),
                (200, _ok_body(TX_B, fund_id="fund.b")),
            ]
        )
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "items": [
                    {"transaction_id": TX_A, "fund_id": "fund.a"},
                    {"transaction_id": TX_B, "fund_id": "fund.b"},
                ]
            },
        )
        self.assertEqual(len(result["results"]), 2)
        self.assertFalse(result["results"][0]["ok"])
        self.assertIn("connection reset", result["results"][0]["error"])
        self.assertTrue(result["results"][1]["ok"])
        self.assertEqual(len(api.calls), 2)

    def test_shared_allow_closed_on_two_items(self) -> None:
        api = _SequentialPatchApi(
            [
                (200, _ok_body(TX_A, fund_id="fund.a")),
                (200, _ok_body(TX_B, fund_id="fund.b")),
            ]
        )
        put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "allow_closed": True,
                "items": [
                    {"transaction_id": TX_A, "fund_id": "fund.a"},
                    {"transaction_id": TX_B, "fund_id": "fund.b"},
                ],
            },
        )
        self.assertEqual(len(api.calls), 2)
        for _method, path, _body in api.calls:
            self.assertIn("allow_closed=true", path)

    def test_summary_arithmetic(self) -> None:
        api = _SequentialPatchApi(
            [
                (200, _ok_body(TX_A, fund_id="fund.a")),
                (
                    500,
                    {"error": {"code": "internal_error", "message": "boom"}},
                ),
                (200, _ok_body(TX_B, fund_id="fund.b")),
            ]
        )
        items = [
            {"transaction_id": TX_A, "fund_id": "fund.a"},
            {"transaction_id": TX_A, "fund_id": "bad"},
            {"transaction_id": TX_B, "fund_id": "fund.b"},
        ]
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={"items": items},
        )
        summary = result["summary"]
        self.assertEqual(summary["total"], len(items))
        self.assertEqual(summary["total"], summary["succeeded"] + summary["failed"])
        self.assertEqual(
            summary["succeeded"],
            sum(1 for row in result["results"] if row["ok"] is True),
        )
        self.assertEqual(
            summary["failed"],
            sum(1 for row in result["results"] if row["ok"] is False),
        )

    def test_http_5xx_is_item_error(self) -> None:
        api = _SequentialPatchApi(
            [
                (503, {"error": {"code": "unavailable", "message": "down"}}),
                (200, _ok_body(TX_B, fund_id="fund.b")),
            ]
        )
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "items": [
                    {"transaction_id": TX_A, "fund_id": "fund.a"},
                    {"transaction_id": TX_B, "fund_id": "fund.b"},
                ]
            },
        )
        self.assertFalse(result["results"][0]["ok"])
        self.assertIn("503", result["results"][0]["error"])
        self.assertTrue(result["results"][1]["ok"])


class PutTransactionsSchemaTests(unittest.TestCase):
    """Schema registration and body-field parity (D-04 / T7.7)."""

    def test_tool_registered(self) -> None:
        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        self.assertIn("put_transactions", names)
        tool = next(t for t in tools if t.name == "put_transactions")
        self.assertEqual(tool.inputSchema.get("required"), ["items"])
        props = tool.inputSchema.get("properties") or {}
        self.assertIn("items", props)
        self.assertIn("allow_closed", props)
        item_schema = props["items"]["items"]
        self.assertNotIn("transaction_id", item_schema.get("required") or [])
        self.assertEqual(item_schema.get("properties", {}).get("transaction_id", {}).get("type"), "string")
        for key in DECLARED_BODY_FIELDS:
            self.assertIn(key, item_schema["properties"])

    def test_body_field_schema_parity(self) -> None:
        tools = asyncio.run(server.list_tools())
        by_name = {t.name: t for t in tools}
        single_props = by_name["put_transaction"].inputSchema["properties"]
        batch_item_props = by_name["put_transactions"].inputSchema["properties"]["items"][
            "items"
        ]["properties"]
        single_body = {
            key: single_props[key]
            for key in DECLARED_BODY_FIELDS
            if key in single_props
        }
        batch_body = {
            key: batch_item_props[key]
            for key in DECLARED_BODY_FIELDS
            if key in batch_item_props
        }
        self.assertEqual(single_body, batch_body)
        self.assertEqual(single_body, BODY_FIELD_SCHEMA_PROPERTIES)


class PutTransactionsHandlerTests(unittest.TestCase):
    """Handler: empty batch before session; happy path."""

    def test_empty_items_does_not_resolve_session(self) -> None:
        with patch("server.get_session") as get_session:
            with self.assertRaises(ValueError):
                server._handle_put_transactions({"items": []})
            get_session.assert_not_called()

    def test_handler_mixed_payload(self) -> None:
        api = _SequentialPatchApi(
            [
                (200, _ok_body(TX_A, fund_id="fund.a")),
                (
                    404,
                    {"error": {"code": "unknown_fund", "message": "missing"}},
                ),
            ]
        )
        with patch("server.get_session", return_value=(api, "http://test")):
            out = server._handle_put_transactions(
                {
                    "profile": "cand",
                    "items": [
                        {"transaction_id": TX_A, "fund_id": "fund.a"},
                        {"transaction_id": TX_B, "fund_id": "missing"},
                    ],
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["succeeded"], 1)
        self.assertEqual(payload["summary"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
