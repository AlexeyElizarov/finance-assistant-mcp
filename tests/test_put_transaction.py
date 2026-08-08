"""Unit tests for FIN-260 put_transaction (canonical merge-patch MCP)."""

from __future__ import annotations

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

from put_transaction import (  # noqa: E402
    DECLARED_BODY_FIELDS,
    put_transaction,
)

import server  # noqa: E402

TX_ID = "cccccccc-cccc-4ccc-8ccc-ccccccccccc1"


class _PatchMockApi:
    """Stub ApiClient for PATCH /api/v1/transactions/{id}."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else {
            "id": TX_ID,
            "transaction_type": "C",
            "transaction_category": "C0003",
            "category_source": "manual",
            "reconciliation_note": "",
            "expense_owner": None,
            "project": None,
            "project_source": None,
            "fund_id": "personal.elizarov.2026-08",
        }
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


class PutTransactionLibTests(unittest.TestCase):
    """FIN-260 T9 unit coverage."""

    def test_assign_fund_happy_path(self) -> None:
        api = _PatchMockApi()
        result = put_transaction(
            api,
            profile="cand",
            base="http://127.0.0.1:8001",
            arguments={
                "transaction_id": TX_ID,
                "fund_id": "personal.elizarov.2026-08",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "cand")
        self.assertEqual(result["transaction"]["fund_id"], "personal.elizarov.2026-08")
        self.assertEqual(api.last_method, "PATCH")
        self.assertEqual(
            api.last_body,
            {"fund_id": "personal.elizarov.2026-08"},
        )
        path = api.last_path or ""
        self.assertIn(f"/api/v1/transactions/{TX_ID}?", path)
        self.assertNotIn("/category", path)
        self.assertNotIn("/project", path)
        self.assertIn("allow_closed=false", path)

    def test_clear_fund_null(self) -> None:
        api = _PatchMockApi(
            body={
                "id": TX_ID,
                "fund_id": None,
                "transaction_type": "C",
                "transaction_category": "C0003",
                "category_source": "manual",
                "reconciliation_note": "",
                "expense_owner": None,
                "project": None,
                "project_source": None,
            }
        )
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "fund_id": None},
        )
        self.assertIsNone(result["transaction"]["fund_id"])
        self.assertEqual(api.last_body, {"fund_id": None})

    def test_clear_fund_empty_string(self) -> None:
        api = _PatchMockApi(body={"id": TX_ID, "fund_id": None})
        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "fund_id": ""},
        )
        self.assertEqual(api.last_body, {"fund_id": ""})

    def test_omit_vs_null_vs_empty_string(self) -> None:
        api = _PatchMockApi()
        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "transaction_id": TX_ID,
                "fund_id": "f1",
                "expense_owner": None,
                "project": "",
            },
        )
        body = api.last_body or {}
        self.assertEqual(body["fund_id"], "f1")
        self.assertIsNone(body["expense_owner"])
        self.assertEqual(body["project"], "")
        self.assertNotIn("transaction_category", body)
        self.assertNotIn("project_source", body)

    def test_mixed_body(self) -> None:
        api = _PatchMockApi()
        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "transaction_id": TX_ID,
                "transaction_category": "C0003",
                "category_source": "manual",
                "project": "PR005",
                "project_source": "manual",
                "fund_id": "shared.main.2026-08",
            },
        )
        self.assertEqual(
            api.last_body,
            {
                "transaction_category": "C0003",
                "category_source": "manual",
                "project": "PR005",
                "project_source": "manual",
                "fund_id": "shared.main.2026-08",
            },
        )

    def test_response_subset_fills_missing_declared_keys_with_null(self) -> None:
        api = _PatchMockApi(body={"id": TX_ID, "fund_id": "f1"})
        result = put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "fund_id": "f1"},
        )
        tx = result["transaction"]
        self.assertEqual(tx["id"], TX_ID)
        self.assertEqual(tx["fund_id"], "f1")
        for key in DECLARED_BODY_FIELDS:
            self.assertIn(key, tx)
        self.assertIsNone(tx["project"])
        self.assertIsNone(tx["expense_owner"])

    def test_value_error_empty_transaction_id(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError):
            put_transaction(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": "   ", "fund_id": "f1"},
            )
        self.assertEqual(api.calls, [])

    def test_value_error_no_declared_fields(self) -> None:
        api = _PatchMockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID},
            )
        self.assertIn("declared set", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_unknown_fund_404(self) -> None:
        api = _PatchMockApi(
            status=404,
            body={
                "error": {
                    "code": "unknown_fund",
                    "message": "Фонд не найден.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "fund_id": "missing"},
            )
        text = str(ctx.exception)
        self.assertIn("404", text)
        self.assertIn("unknown_fund", text)

    def test_period_closed_422(self) -> None:
        api = _PatchMockApi(
            status=422,
            body={
                "error": {
                    "code": "period_closed",
                    "message": "Period closed",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "fund_id": "f1"},
            )
        self.assertIn("period_closed", str(ctx.exception))

    def test_allow_closed_query(self) -> None:
        api = _PatchMockApi()
        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "transaction_id": TX_ID,
                "fund_id": "f1",
                "allow_closed": True,
            },
        )
        self.assertIn("allow_closed=true", api.last_path or "")

    def test_network_error_not_wrapped(self) -> None:
        api = _PatchMockApi(raise_on_request=ConnectionError("down"))
        with self.assertRaises(ConnectionError):
            put_transaction(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "fund_id": "f1"},
            )


class PutTransactionHandlerTests(unittest.TestCase):
    """server._handle_put_transaction presence and schema registration."""

    def _patch_session(self, api: _PatchMockApi) -> Any:
        return patch("server.get_session", return_value=(api, "http://test"))

    def test_handler_passes_null_fund_id(self) -> None:
        api = _PatchMockApi(body={"id": TX_ID, "fund_id": None})
        with self._patch_session(api):
            out = server._handle_put_transaction(
                {
                    "profile": "cand",
                    "transaction_id": TX_ID,
                    "fund_id": None,
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(api.last_body, {"fund_id": None})

    def test_handler_omits_absent_keys(self) -> None:
        api = _PatchMockApi()
        with self._patch_session(api):
            server._handle_put_transaction(
                {
                    "transaction_id": TX_ID,
                    "expense_owner": "nikolai",
                }
            )
        self.assertEqual(api.last_body, {"expense_owner": "nikolai"})

    def test_tool_registered(self) -> None:
        import asyncio

        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        self.assertIn("put_transaction", names)
        tool = next(t for t in tools if t.name == "put_transaction")
        self.assertEqual(tool.inputSchema.get("required"), ["transaction_id"])
        props = tool.inputSchema.get("properties") or {}
        for key in DECLARED_BODY_FIELDS:
            self.assertIn(key, props)


if __name__ == "__main__":
    unittest.main()
