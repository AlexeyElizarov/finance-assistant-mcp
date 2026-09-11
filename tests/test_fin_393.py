"""Unit tests for FIN-393 claim-movement type pass-through on MCP tools."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_query_transactions():
    path = _SCRIPTS / "query-transactions.py"
    spec = importlib.util.spec_from_file_location("query_transactions_fin393", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_qt = _load_query_transactions()
build_query_path = _qt.build_query_path
fetch_rows = _qt.fetch_rows
normalize_query_args = _qt.normalize_query_args

from put_transaction import (  # noqa: E402
    BODY_FIELD_SCHEMA_PROPERTIES,
    put_transaction,
)
from put_transaction_category import put_transaction_category  # noqa: E402
from put_transaction_lines import put_transaction_lines  # noqa: E402
from put_transactions import put_transactions  # noqa: E402

import server  # noqa: E402

TX_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
_FILTER_REQUIRED_MSG = "Укажите хотя бы один фильтр"
_D01_TOOLS = (
    "put_transaction_lines",
    "get_transaction_lines",
    "get_transaction",
    "put_transaction",
    "put_transactions",
    "query_transactions",
)


class _CaptureApi:
    """Stub ApiClient capturing request and get_json calls."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: Any | None = None,
        get_json_body: Any | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.get_json_body = get_json_body if get_json_body is not None else {
            "rows": [],
            "meta": {},
        }
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.get_json_paths: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        self.calls.append((method, path, dict(data) if data is not None else None))
        return self.status, self.body

    def get_json(self, path: str) -> Any:
        self.get_json_paths.append(path)
        return self.get_json_body

    @property
    def last_body(self) -> dict[str, Any] | None:
        return self.calls[-1][2] if self.calls else None


def _tool(name: str) -> Any:
    tools = asyncio.run(server.list_tools())
    return next(tool for tool in tools if tool.name == name)


def _schema_has_no_enum(schema: dict[str, Any]) -> bool:
    return "enum" not in schema


class Fin393FacadeTests(unittest.TestCase):
    """D-02 / D-07: legacy facade unchanged; canonical C without category hits HTTP."""

    def test_put_transaction_category_r_without_category_before_http(self) -> None:
        api = _CaptureApi()
        with self.assertRaises(ValueError) as ctx:
            put_transaction_category(
                api,
                profile="cand",
                base="http://test",
                transaction_id=TX_ID,
                transaction_type="R",
            )
        self.assertIn("transaction_type", str(ctx.exception))
        self.assertNotIn("HTTP", str(ctx.exception))
        self.assertEqual(api.calls, [])

    def test_put_transaction_category_p_with_category_same_body(self) -> None:
        api = _CaptureApi(
            body={
                "id": TX_ID,
                "transaction_type": "P",
                "transaction_category": "P0002",
                "category_source": "manual",
                "classification_status": "classified",
                "reconciliation_note": "",
            }
        )
        result = put_transaction_category(
            api,
            profile="cand",
            base="http://test",
            transaction_id=TX_ID,
            transaction_type="P",
            transaction_category="P0002",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            api.last_body,
            {"transaction_type": "P", "transaction_category": "P0002"},
        )

    def test_put_transaction_c_without_category_calls_http(self) -> None:
        api = _CaptureApi(
            status=422,
            body={
                "error": {
                    "code": "validation_error",
                    "message": "category required",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction(
                api,
                profile="cand",
                base="http://test",
                arguments={"transaction_id": TX_ID, "transaction_type": "C"},
            )
        self.assertIn("validation_error", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.last_body, {"transaction_type": "C"})


class Fin393WritePassThroughTests(unittest.TestCase):
    """D-03: universal write paths forward type without a local dictionary."""

    def test_put_transaction_lines_forwards_r_without_category(self) -> None:
        lines = [
            {
                "id": "line-1",
                "line_no": 1,
                "amount": "100.00",
                "assignment": {
                    "type": "R",
                    "fund_id": "personal.elizarov",
                },
            }
        ]
        api = _CaptureApi(body={"lines": lines})
        result = put_transaction_lines(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "lines": lines},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(api.calls[0][0], "PUT")
        sent = api.last_body or {}
        assignment = sent["lines"][0]["assignment"]
        self.assertEqual(assignment["type"], "R")
        self.assertNotIn("category", assignment)

    def test_put_transaction_type_only_body(self) -> None:
        api = _CaptureApi(
            body={
                "id": TX_ID,
                "transaction_type": "R",
                "transaction_category": None,
            }
        )
        put_transaction(
            api,
            profile="cand",
            base="http://test",
            arguments={"transaction_id": TX_ID, "transaction_type": "R"},
        )
        self.assertEqual(api.last_body, {"transaction_type": "R"})

    def test_put_transactions_single_item_same_body(self) -> None:
        api = _CaptureApi(
            body={
                "id": TX_ID,
                "transaction_type": "R",
                "transaction_category": None,
            }
        )
        result = put_transactions(
            api,
            profile="cand",
            base="http://test",
            arguments={
                "items": [{"transaction_id": TX_ID, "transaction_type": "R"}],
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(api.last_body, {"transaction_type": "R"})


class Fin393QueryFilterTests(unittest.TestCase):
    """D-05: transaction_type is a declared string filter and a standalone active filter."""

    def test_query_includes_transaction_type_parameter(self) -> None:
        path = build_query_path(
            normalize_query_args(period="2026-02", transaction_type="R"),
        )
        self.assertIn("transaction_type=R", path)
        self.assertIn("accounting_period=202602", path)

    def test_omitted_transaction_type_not_in_query(self) -> None:
        path = build_query_path(normalize_query_args(period="2026-02"))
        self.assertNotIn("transaction_type=", path)

    def test_blank_transaction_type_after_trim_is_unset(self) -> None:
        path = build_query_path(
            normalize_query_args(period="2026-02", transaction_type="   "),
        )
        self.assertNotIn("transaction_type=", path)

    def test_transaction_type_only_is_active_filter_and_reaches_http(self) -> None:
        args = normalize_query_args(transaction_type="R")
        path = build_query_path(args)
        self.assertIn("transaction_type=R", path)
        self.assertNotIn("accounting_period=", path)
        api = _CaptureApi()
        fetch_rows(api, args)
        self.assertEqual(api.get_json_paths, [path])

    def test_blank_transaction_type_only_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_query_path(normalize_query_args(transaction_type="   "))
        self.assertIn(_FILTER_REQUIRED_MSG, str(ctx.exception))

    def test_schema_transaction_type_is_string_without_enum(self) -> None:
        props = _tool("query_transactions").inputSchema.get("properties") or {}
        schema = props["transaction_type"]
        self.assertEqual(schema.get("type"), "string")
        self.assertTrue(_schema_has_no_enum(schema))
        required = _tool("query_transactions").inputSchema.get("required") or []
        self.assertNotIn("transaction_type", required)


class Fin393HttpErrorTests(unittest.TestCase):
    """D-06 / D-08 / D-09: HTTP validation is forwarded; 201 is not success."""

    def test_lines_validation_error_no_second_write(self) -> None:
        api = _CaptureApi(
            status=422,
            body={
                "error": {
                    "code": "validation_error",
                    "message": "R cannot have category",
                }
            },
        )
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "transaction_id": TX_ID,
                    "lines": [
                        {
                            "line_no": 1,
                            "amount": "100.00",
                            "assignment": {
                                "type": "R",
                                "category": "C0003",
                                "fund_id": "personal.elizarov",
                            },
                        }
                    ],
                },
            )
        self.assertIn("422", str(ctx.exception))
        self.assertIn("validation_error", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)

    def test_lines_http_201_is_not_success(self) -> None:
        api = _CaptureApi(status=201, body={"lines": []})
        with self.assertRaises(RuntimeError) as ctx:
            put_transaction_lines(
                api,
                profile="cand",
                base="http://test",
                arguments={
                    "transaction_id": TX_ID,
                    "lines": [
                        {
                            "line_no": 1,
                            "amount": "100.00",
                            "assignment": {"type": "R", "fund_id": "f1"},
                        }
                    ],
                },
            )
        self.assertIn("201", str(ctx.exception))
        self.assertEqual(len(api.calls), 1)


class Fin393SchemaAndCatalogTests(unittest.TestCase):
    """D-01 / D-09: no MCP type enum; catalog mentions FIN-393 on D-01 tools."""

    def test_put_transaction_type_schema_has_no_enum(self) -> None:
        schema = BODY_FIELD_SCHEMA_PROPERTIES["transaction_type"]
        self.assertEqual(schema.get("type"), ["string", "null"])
        self.assertTrue(_schema_has_no_enum(schema))
        props = _tool("put_transaction").inputSchema.get("properties") or {}
        self.assertTrue(_schema_has_no_enum(props["transaction_type"]))

    def test_put_transaction_lines_assignment_type_has_no_enum(self) -> None:
        props = _tool("put_transaction_lines").inputSchema.get("properties") or {}
        assignment = props["lines"]["items"]["properties"]["assignment"]["properties"]
        schema = assignment["type"]
        self.assertEqual(schema.get("type"), ["string", "null"])
        self.assertTrue(_schema_has_no_enum(schema))

    def test_mcp_gaps_marks_d01_fin393(self) -> None:
        text = (_ROOT / "mcp-gaps.md").read_text(encoding="utf-8")
        for name in _D01_TOOLS:
            self.assertIn(f"`{name}`", text)
        self.assertIn("FIN-393", text)
        catalog = text.split("## Открытые пробелы")[0]
        for name in _D01_TOOLS:
            line = next(
                row for row in catalog.splitlines() if row.startswith(f"| `{name}` ")
            )
            self.assertIn("FIN-393", line)


if __name__ == "__main__":
    unittest.main()
