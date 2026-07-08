"""Unit tests for FIN-17 list_c9999 MCP tool."""

from __future__ import annotations

import asyncio
import json
import unittest
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from monthly_close_lib import (
    Period,
    list_c9999_payload,
    normalize_c9999_rows,
    parse_period,
    parse_transaction_date_sort_key,
)


def _raw_row(
    *,
    row_id: str = "1",
    date_display: str = "05.02.2026",
    amount: str = "10.00",
    description: str = "REWE",
    provider: str = "sparkasse",
    project: str = "",
) -> dict[str, Any]:
    return {
        "id": row_id,
        "date_display": date_display,
        "amount": amount,
        "description": description,
        "provider": provider,
        "project": project,
    }


class ListC9999NormalizeTest(unittest.TestCase):
    """Normalization, sorting, and aggregation (T1–T3, T6–T7, T9–T10)."""

    def test_t1_two_rows_normalized(self) -> None:
        """T1: two rows → row_count=2, fields normalized."""
        rows, warnings, total = normalize_c9999_rows(
            [
                _raw_row(row_id="1", date_display="01.02.2026", amount="52.90", description="REWE"),
                _raw_row(row_id="2", date_display="02.02.2026", amount="34.50", description="DM"),
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(warnings, [])
        self.assertEqual(rows[0]["id"], "1")
        self.assertEqual(rows[0]["provider"], "sparkasse")
        self.assertEqual(rows[0]["suggestions"], [])
        self.assertEqual(rows[1]["suggestions"], [])
        self.assertAlmostEqual(float(total), 87.40)

    def test_t2_empty_input(self) -> None:
        """T2: empty raw rows."""
        rows, warnings, total = normalize_c9999_rows([])
        self.assertEqual(rows, [])
        self.assertEqual(warnings, [])
        self.assertEqual(total, Decimal("0"))

    def test_t3_negative_and_decimal_total(self) -> None:
        """T3: abs(amount); Decimal sum without float artifacts."""
        rows, warnings, _total = normalize_c9999_rows(
            [
                _raw_row(row_id="1", amount="-0.10"),
                _raw_row(row_id="2", amount="-0.20"),
            ]
        )
        self.assertEqual(warnings, [])
        self.assertEqual(rows[0]["amount"], 0.10)
        self.assertEqual(rows[1]["amount"], 0.20)
        with patch(
            "monthly_close_lib.c9999_rows",
            return_value=[_raw_row(amount="-0.10"), _raw_row(row_id="2", amount="-0.20")],
        ):
            result = list_c9999_payload(MagicMock(), parse_period("2026-02"))
        self.assertEqual(result["total_amount_eur"], 0.30)

    def test_t6_sort_by_parsed_date(self) -> None:
        """T6: January before February despite DD.MM.YYYY display."""
        rows, _, _ = normalize_c9999_rows(
            [
                _raw_row(row_id="feb", date_display="05.02.2026", description="B"),
                _raw_row(row_id="jan", date_display="15.01.2026", description="A"),
            ]
        )
        self.assertEqual([row["id"] for row in rows], ["jan", "feb"])

    def test_t7_suggestions_always_empty(self) -> None:
        """T7: suggestions == [] on every row."""
        rows, _, _ = normalize_c9999_rows([_raw_row(), _raw_row(row_id="2")])
        self.assertTrue(all(row["suggestions"] == [] for row in rows))

    def test_t9_unparseable_date_regression(self) -> None:
        """T9: bad date → warning, row kept, sorted last."""
        rows, warnings, _ = normalize_c9999_rows(
            [
                _raw_row(row_id="bad", date_display="not-a-date", description="X"),
                _raw_row(row_id="good", date_display="01.01.2026", description="Y"),
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(warnings), 1)
        self.assertIn("unparseable_date:id=bad", warnings[0])
        self.assertEqual(rows[-1]["id"], "bad")

    def test_t10_warnings_key_on_success(self) -> None:
        """T10: successful payload includes warnings array."""
        api = MagicMock()
        with patch(
            "monthly_close_lib.c9999_rows",
            return_value=[_raw_row()],
        ):
            result = list_c9999_payload(api, parse_period("2026-02"))
        self.assertIn("warnings", result)
        self.assertEqual(result["warnings"], [])

    def test_parse_transaction_date_sort_key_iso(self) -> None:
        """ISO date_display parses for sorting."""
        key, ok = parse_transaction_date_sort_key("2026-02-05")
        self.assertTrue(ok)
        self.assertEqual(key, "2026-02-05")


class ListC9999HandlerTest(unittest.TestCase):
    """MCP handler wiring (T4, T5, T8)."""

    def test_t4_invalid_period_no_http(self) -> None:
        """T4: invalid period returns error without API."""
        import server

        api = MagicMock()
        with patch("server.get_session", return_value=(api, "http://127.0.0.1:8000")):
            out = asyncio.run(server.call_tool("list_c9999", {"period": "2026-13"}))
        payload = json.loads(out[0].text)
        self.assertFalse(payload["ok"])
        self.assertIn("invalid month", payload["error"])
        api.get_json.assert_not_called()

    @patch("server.list_c9999_payload")
    @patch("server.get_session")
    def test_t8_calls_list_c9999_payload_with_parsed_period(
        self,
        get_session: MagicMock,
        list_payload: MagicMock,
    ) -> None:
        """T8: handler uses Period from YYYY-MM input."""
        import server

        api = MagicMock()
        get_session.return_value = (api, "http://127.0.0.1:8000")
        list_payload.return_value = {
            "period": "2026-02",
            "row_count": 0,
            "total_amount_eur": 0.0,
            "warnings": [],
            "rows": [],
        }
        server._handle_list_c9999({"period": "2026-02", "profile": "prod"})
        list_payload.assert_called_once()
        called_api, called_period = list_payload.call_args[0]
        self.assertIs(called_api, api)
        self.assertEqual(called_period, Period(year=2026, month=2))

    def test_t5_tool_registered(self) -> None:
        """T5: list_c9999 appears in MCP tool list."""
        import server

        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        self.assertIn("list_c9999", names)


if __name__ == "__main__":
    unittest.main()
