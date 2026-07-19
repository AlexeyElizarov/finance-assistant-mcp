"""Unit tests for FIN-27 query_transactions period/category filters."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load_query_transactions():
    path = _SCRIPTS / "query-transactions.py"
    spec = importlib.util.spec_from_file_location("query_transactions_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_qt = _load_query_transactions()
build_query_path = _qt.build_query_path
fetch_rows = _qt.fetch_rows
normalize_query_args = _qt.normalize_query_args

import server  # noqa: E402

_FILTER_REQUIRED_MSG = "Укажите хотя бы один фильтр"


class BuildQueryPathTests(unittest.TestCase):
    """T01–T07, T11–T18: build_query_path and validation."""

    def test_t01_period_only(self) -> None:
        path = build_query_path(normalize_query_args(period="2026-02"))
        self.assertIn("accounting_period=202602", path)

    def test_t02_period_and_category(self) -> None:
        path = build_query_path(
            normalize_query_args(period="2026-02", category="C9999"),
        )
        self.assertIn("accounting_period=202602", path)
        self.assertIn("transaction_category=C9999", path)

    def test_t03_transaction_category_alias(self) -> None:
        path = build_query_path(
            normalize_query_args(transaction_category="C9999", period="2026-02"),
        )
        self.assertIn("transaction_category=C9999", path)

    def test_t04_category_conflict(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_query_path(
                normalize_query_args(
                    period="2026-02",
                    category="C9999",
                    transaction_category="FOOD",
                ),
            )
        self.assertIn("conflict", str(ctx.exception))

    def test_t05_period_mutually_exclusive(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_query_path(
                normalize_query_args(period="2026-02", accounting_period="202602"),
            )
        self.assertIn("mutually exclusive", str(ctx.exception))

    def test_t06_invalid_month(self) -> None:
        with self.assertRaises(ValueError):
            build_query_path(normalize_query_args(period="2026-13"))

    def test_t07_backward_compat_date_range(self) -> None:
        path = build_query_path(
            normalize_query_args(date_from="2025-01-01", date_to="2025-12-31"),
        )
        self.assertIn("date_from=2025-01-01", path)
        self.assertIn("date_to=2025-12-31", path)
        self.assertNotIn("accounting_period=", path)

    def test_t11_period_yyyymm_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_query_path(normalize_query_args(period="202602"))
        self.assertIn("period must be YYYY-MM", str(ctx.exception))

    def test_t12_period_trim(self) -> None:
        path = build_query_path(normalize_query_args(period=" 2026-02 "))
        self.assertIn("accounting_period=202602", path)

    def test_t13_empty_period_no_filters(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_query_path(normalize_query_args(period=""))
        self.assertIn(_FILTER_REQUIRED_MSG, str(ctx.exception))

    def test_t14_category_trim(self) -> None:
        path = build_query_path(
            normalize_query_args(period="2026-02", category=" C9999 "),
        )
        self.assertIn("transaction_category=C9999", path)

    def test_t15_empty_period_with_accounting_period(self) -> None:
        path = build_query_path(
            normalize_query_args(period="", accounting_period="202602"),
        )
        self.assertIn("accounting_period=202602", path)

    def test_t16_empty_contains_no_filters(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            build_query_path(normalize_query_args(contains=["", ""]))
        self.assertIn(_FILTER_REQUIRED_MSG, str(ctx.exception))

    def test_t17_accounting_period_yyyymm(self) -> None:
        path = build_query_path(normalize_query_args(accounting_period="202602"))
        self.assertIn("accounting_period=202602", path)

    def test_t18_accounting_period_yyyy_mm(self) -> None:
        path = build_query_path(normalize_query_args(accounting_period="2026-02"))
        self.assertIn("accounting_period=202602", path)


class FetchRowsTests(unittest.TestCase):
    """T08–T09: fetch_rows and API errors."""

    def test_t08_fetch_rows_shape(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [
                {
                    "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
                    "date_display": "01.02.2026",
                    "amount": "10,00",
                    "debit_credit_indicator": "D",
                    "description": "OTTO",
                    "transaction_category": "C9999",
                    "transaction_type": "C",
                    "provider": "sparkasse_sepa",
                }
            ],
            "meta": {},
        }
        rows = fetch_rows(api, normalize_query_args(period="2026-02"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].category, "C9999")
        self.assertEqual(rows[0].amount, 10.0)
        self.assertEqual(rows[0].id, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1")
        self.assertEqual(rows[0].transaction_type, "C")

    def test_t09_filter_error_raises(self) -> None:
        api = MagicMock()
        api.get_json.return_value = {
            "rows": [],
            "meta": {"filter_error": "invalid_range"},
        }
        with self.assertRaises(RuntimeError) as ctx:
            fetch_rows(api, normalize_query_args(period="2026-02"))
        self.assertIn("invalid_range", str(ctx.exception))


class CliParityTests(unittest.TestCase):
    """T10: CLI uses same build_query_path as MCP normalization."""

    def test_t10_cli_period_same_path(self) -> None:
        cli_args = normalize_query_args(
            period="2026-02",
            category="C9999",
            indicator="D",
        )
        mcp_args = normalize_query_args(
            period="2026-02",
            category="C9999",
            indicator="D",
        )
        self.assertEqual(build_query_path(cli_args), build_query_path(mcp_args))


class HandlerTests(unittest.TestCase):
    """MCP handler smoke."""

    def test_handler_returns_row_count(self) -> None:
        mock_row = MagicMock()
        mock_row.date_display = "01.02.2026"
        mock_row.amount = 42.0
        mock_row.indicator = "D"
        mock_row.category = "C9999"
        mock_row.provider = "sparkasse_sepa"
        mock_row.description = "TEST"
        mock_row.id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
        mock_row.transaction_type = "C"

        with patch.object(server, "get_session", return_value=(MagicMock(), "http://127.0.0.1:8000")):
            with patch.object(server, "fetch_rows", return_value=[mock_row]):
                result = server._handle_query_transactions(
                    {"period": "2026-02", "category": "C9999"},
                )
        payload = json.loads(result[0].text)
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["category"], "C9999")
        self.assertEqual(payload["rows"][0]["id"], "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1")
        self.assertEqual(payload["rows"][0]["transaction_type"], "C")
        self.assertNotIn("transaction_key", payload["rows"][0])


if __name__ == "__main__":
    unittest.main()
