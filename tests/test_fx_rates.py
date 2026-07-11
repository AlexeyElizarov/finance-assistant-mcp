"""Unit tests for FIN-114 fx_rates MCP tools."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from fx_rates import list_fx_rates, upsert_fx_rate


class _FxMockApi:
    """API stub for /fx-rates."""

    def __init__(self) -> None:
        self.last_method: str | None = None
        self.last_path: str | None = None
        self.last_body: dict[str, Any] | None = None
        self.get_status = 200
        self.get_body: dict[str, Any] = {"fx_rates": []}
        self.put_status = 200
        self.put_body: dict[str, Any] = {}

    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
    ) -> tuple[int, Any]:
        self.last_method = method
        self.last_path = path
        self.last_body = data
        if method == "GET":
            return self.get_status, self.get_body
        if method == "PUT":
            return self.put_status, self.put_body
        raise AssertionError(f"unexpected method: {method}")


class FxRatesTest(unittest.TestCase):
    """FIN-114 list/upsert FX rates."""

    def test_t1_upsert_happy_path(self) -> None:
        """T1: PUT wraps canonical rate."""
        api = _FxMockApi()
        api.put_body = {
            "period": "2026-07-01",
            "from_currency": "RUB",
            "to_currency": "EUR",
            "rate": "89.8",
            "updated_at": "2026-07-11T05:00:00",
        }
        result = upsert_fx_rate(
            api,
            profile="prod",
            base="http://127.0.0.1:8000",
            period="2026-07",
            rate="89.80",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["fx_rate"]["rate"], "89.8")
        self.assertEqual(api.last_method, "PUT")

    def test_t2_list_by_period(self) -> None:
        """T2: GET wraps fx_rates list."""
        api = _FxMockApi()
        api.get_body = {
            "fx_rates": [
                {
                    "period": "2026-07-01",
                    "from_currency": "RUB",
                    "to_currency": "EUR",
                    "rate": "89.8",
                    "updated_at": "2026-07-03T10:15:00",
                }
            ]
        }
        result = list_fx_rates(
            api,
            profile="prod",
            base="http://127.0.0.1:8000",
            period="2026-07",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["fx_rates"]), 1)
        self.assertIn("period=2026-07", api.last_path or "")

    def test_t3_list_all(self) -> None:
        """T3: GET without period returns all rates."""
        api = _FxMockApi()
        result = list_fx_rates(api, profile="prod", base="http://127.0.0.1:8000")
        self.assertEqual(result["fx_rates"], [])
        self.assertEqual(api.last_path, "/api/v1/fx-rates")

    def test_t4_period_conflict_422(self) -> None:
        """T4: validation_error from API propagates."""
        api = _FxMockApi()
        api.get_status = 422
        api.get_body = {
            "error": {
                "code": "validation_error",
                "message": "Укажите либо period, либо пару period_from и period_to.",
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            list_fx_rates(
                api,
                profile="prod",
                base="http://127.0.0.1:8000",
                period="2026-07",
                period_from="2026-01",
            )
        self.assertIn("validation_error", str(ctx.exception))

    def test_t5_zero_rate_422(self) -> None:
        """T5: invalid rate returns tool error."""
        api = _FxMockApi()
        api.put_status = 422
        api.put_body = {
            "error": {"code": "validation_error", "message": "rate must be > 0"}
        }
        with self.assertRaises(RuntimeError):
            upsert_fx_rate(
                api,
                profile="prod",
                base="http://127.0.0.1:8000",
                period="2026-07",
                rate="0",
            )

    def test_t6_unsupported_pair(self) -> None:
        """T6: fx_pair_not_supported propagates."""
        api = _FxMockApi()
        api.put_status = 422
        api.put_body = {
            "error": {
                "code": "fx_pair_not_supported",
                "message": "Валютная пара не поддерживается.",
            }
        }
        with self.assertRaises(RuntimeError) as ctx:
            upsert_fx_rate(
                api,
                profile="prod",
                base="http://127.0.0.1:8000",
                period="2026-07",
                rate="1.1",
                from_currency="USD",
                to_currency="EUR",
            )
        self.assertIn("fx_pair_not_supported", str(ctx.exception))

    def test_missing_period(self) -> None:
        """Empty period fails before HTTP."""
        api = MagicMock()
        with self.assertRaises(RuntimeError):
            upsert_fx_rate(
                api,
                profile="prod",
                base="http://127.0.0.1:8000",
                period="  ",
                rate="89.8",
            )
        api.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
