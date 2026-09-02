"""Unit tests for FIN-270 operation split MCP tools."""

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

from get_transaction import get_transaction  # noqa: E402
from get_transaction_lines import get_transaction_lines  # noqa: E402
from put_transaction_lines import put_transaction_lines  # noqa: E402

import server  # noqa: E402

TX_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"

_SAMPLE_LINES = [
    {
        "id": "line-keep-1",
        "line_no": 1,
        "amount": "55.00",
        "assignment": {
            "type": "expense",
            "category": "C0003",
            "project": None,
            "fund_id": "personal.elizarov.2026-07",
            "source": "manual",
            "state": "complete",
            "note": None,
        },
    },
    {
        "line_no": 2,
        "amount": "45.00",
        "assignment": {
            "type": "expense",
            "category": "C0003",
            "project": None,
            "fund_id": "personal.dubrovskii.2026-07",
            "source": "manual",
            "state": "complete",
            "note": None,
        },
    },
]


class _MockApi:
    """Stub ApiClient for FIN-272 paths."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else {"lines": _SAMPLE_LINES}
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


class PutTransactionLinesTests(unittest.TestCase):
    """FIN-270 T4 put coverage."""

    def test_put_path_and_body(self) -> None:
        api = _MockApi()
        result = put_transaction_lines(
            api,
            profile="cand",
            base="http://127.0.0.1:8003",
            arguments={"transaction_id": TX_ID, "lines": _SAMPLE_LINES},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "cand")
        self.assertEqual(len(result["lines"]), 2)
        self.assertEqual(api.last_method, "PUT")
        self.assertEqual(api.last_body, {"lines": _SAMPLE_LINES})
        path = api.last_path or ""
        self.assertIn(f"/api/v1/transactions/{TX_ID}/lines?", path)
        self.assertIn("allow_closed=false", path)

    def test_allow_closed_query(self) -> None:
        api = _MockApi()
        put_transaction_lines(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "transaction_id": TX_ID,
                "lines": _SAMPLE_LINES,
                "allow_closed": True,
            },
        )
        self.assertIn("allow_closed=true", api.last_path or "")

    def test_line_amount_mismatch_422(self) -> None:
        api = _MockApi(
            status=422,
            body={
                "error": {
                    "code": "line_amount_mismatch",
                    "message": "Sum of line amounts must equal the operation amount.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "lines": _SAMPLE_LINES},
            )
        text = str(ctx.exception)
        self.assertIn("422", text)
        self.assertIn("line_amount_mismatch", text)

    def test_period_closed_422(self) -> None:
        api = _MockApi(
            status=422,
            body={"error": {"code": "period_closed", "message": "Period closed"}},
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "lines": _SAMPLE_LINES},
            )
        self.assertIn("period_closed", str(ctx.exception))

    def test_value_error_empty_transaction_id(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": "  ", "lines": _SAMPLE_LINES},
            )
        self.assertEqual(api.calls, [])

    def test_value_error_empty_lines(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "lines": []},
            )
        self.assertIn("lines", str(ctx.exception).lower())
        self.assertEqual(api.calls, [])

    def test_value_error_missing_lines(self) -> None:
        api = _MockApi()
        with self.assertRaises(ValueError):
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID},
            )
        self.assertEqual(api.calls, [])

    def test_network_error_not_wrapped(self) -> None:
        api = _MockApi(raise_on_request=ConnectionError("down"))
        with self.assertRaises(ConnectionError):
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "lines": _SAMPLE_LINES},
            )


class GetTransactionLinesTests(unittest.TestCase):
    """FIN-270 T4 get lines coverage."""

    def test_get_lines_happy(self) -> None:
        api = _MockApi()
        result = get_transaction_lines(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.last_method, "GET")
        self.assertEqual(
            api.last_path,
            f"/api/v1/transactions/{TX_ID}/lines",
        )
        self.assertIsNone(api.last_body)
        self.assertEqual(len(result["lines"]), 2)

    def test_not_found_404(self) -> None:
        api = _MockApi(
            status=404,
            body={
                "error": {
                    "code": "transaction_not_found",
                    "message": "Transaction not found.",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            get_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID},
            )
        self.assertIn("transaction_not_found", str(ctx.exception))


class GetTransactionTests(unittest.TestCase):
    """FIN-270 T4 get transaction coverage."""

    def test_get_transaction_happy(self) -> None:
        body = {
            "id": TX_ID,
            "amount": "100.00",
            "debit_credit_indicator": "debit",
            "lines": _SAMPLE_LINES,
        }
        api = _MockApi(body=body)
        result = get_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.last_method, "GET")
        self.assertEqual(api.last_path, f"/api/v1/transactions/{TX_ID}")
        self.assertEqual(result["transaction"]["id"], TX_ID)
        self.assertEqual(result["transaction"]["amount"], "100.00")
        self.assertEqual(len(result["transaction"]["lines"]), 2)


class HandlerAndRegistrationTests(unittest.TestCase):
    """server handlers and tool list."""

    def _patch_session(self, api: _MockApi) -> Any:
        return patch("server.get_session", return_value=(api, "http://test"))

    def test_put_handler(self) -> None:
        api = _MockApi()
        with self._patch_session(api):
            out = server._handle_put_transaction_lines(
                {
                    "profile": "cand",
                    "transaction_id": TX_ID,
                    "lines": _SAMPLE_LINES,
                }
            )
        payload = json.loads(out[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(api.last_body, {"lines": _SAMPLE_LINES})

    def test_tools_registered(self) -> None:
        import asyncio

        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        self.assertIn("put_transaction_lines", names)
        self.assertIn("get_transaction_lines", names)
        self.assertIn("get_transaction", names)
        put_tool = next(t for t in tools if t.name == "put_transaction_lines")
        self.assertEqual(
            put_tool.inputSchema.get("required"),
            ["transaction_id", "lines"],
        )


if __name__ == "__main__":
    unittest.main()
